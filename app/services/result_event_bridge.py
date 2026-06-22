"""
Optional network-result sidecar event bridge.

The core project stays generic: workflow/network code only calls a generic
result handler. This module decides whether a parsed network result belongs to
Arena and, if so, appends a JSONL event for external tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.core.config import PROJECT_ROOT, get_logger
from app.core.parsers.lmarena_parser import _CP1252_TO_LATIN1


logger = get_logger("RESULT.EVENT.BRIDGE")
_WRITE_LOCK = threading.Lock()
_LAST_DIGEST_BY_KEY: Dict[str, str] = {}
_MODEL_MAP_LOCK = threading.Lock()
_MODEL_ID_MAP: Dict[str, str] = {}
_MODEL_MAP_PATH: Optional[Path] = None
_MODEL_MAP_MTIME: Optional[float] = None


def _env_flag(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_event_path() -> Path:
    raw_path = str(os.getenv("ARENA_EVENT_BRIDGE_PATH") or "").strip()
    if raw_path:
        return Path(raw_path).expanduser()
    return PROJECT_ROOT / "logs" / "arena_events.jsonl"


def _get_model_map_path() -> Optional[Path]:
    raw_path = str(os.getenv("ARENA_MODEL_MAP_PATH") or "").strip()
    candidates = []
    if raw_path:
        candidates.append(Path(raw_path).expanduser())
    candidates.append(
        PROJECT_ROOT.parent.parent
        / "竞技场"
        / "lmarena 自用"
        / "LMArenaBridge-main"
        / "available_models.json"
    )
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else None


def _load_model_id_map() -> Dict[str, str]:
    global _MODEL_ID_MAP, _MODEL_MAP_PATH, _MODEL_MAP_MTIME

    path = _get_model_map_path()
    if not path or not path.exists():
        return {}

    try:
        mtime = path.stat().st_mtime
    except Exception:
        return {}

    with _MODEL_MAP_LOCK:
        if _MODEL_MAP_PATH == path and _MODEL_MAP_MTIME == mtime:
            return dict(_MODEL_ID_MAP)

        try:
            with path.open("r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as exc:
            logger.debug(f"[RESULT_EVENT_BRIDGE] model map load failed: {path} ({exc})")
            return {}

        if isinstance(raw_data, dict):
            model_items = raw_data.get("models") or raw_data.get("data") or raw_data.get("items") or []
        else:
            model_items = raw_data

        id_map: Dict[str, str] = {}
        if isinstance(model_items, list):
            for item in model_items:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id") or "").strip()
                model_name = str(item.get("displayName") or item.get("publicName") or item.get("name") or "").strip()
                if model_id and model_name:
                    id_map[model_id] = model_name

        _MODEL_ID_MAP = id_map
        _MODEL_MAP_PATH = path
        _MODEL_MAP_MTIME = mtime
        logger.debug(f"[RESULT_EVENT_BRIDGE] loaded Arena model map: {len(id_map)} entries from {path}")
        return dict(_MODEL_ID_MAP)


def _resolve_model_id(raw_value: Any) -> str:
    model_id = str(raw_value or "").strip()
    if not model_id:
        return ""
    return _load_model_id_map().get(model_id, model_id)


def _fix_arena_mojibake(text: str) -> str:
    try:
        mapped = text.translate(_CP1252_TO_LATIN1)
        return mapped.encode("latin-1").decode("utf-8")
    except Exception:
        return text


def _parse_text_frame(payload: str) -> str:
    try:
        value = json.loads(payload)
        return value if isinstance(value, str) else ""
    except Exception:
        return ""


def _content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    return ""


def _extract_prompt_from_request_payload(raw_payload: Any) -> str:
    if raw_payload is None:
        return ""
    if isinstance(raw_payload, (bytes, bytearray)):
        text = raw_payload.decode("utf-8", errors="ignore")
    else:
        text = str(raw_payload or "")
    if not text:
        return ""

    try:
        data = json.loads(text)
    except Exception:
        return ""

    if isinstance(data, dict):
        user_message = data.get("userMessage")
        if isinstance(user_message, dict):
            content = _content_to_text(user_message.get("content"))
            if content:
                return content

        messages = data.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "").strip().lower()
                if role and role not in {"user", "human"}:
                    continue
                content = _content_to_text(message.get("content"))
                if content:
                    return content

        for key in ("prompt", "input", "query", "message"):
            content = _content_to_text(data.get(key))
            if content:
                return content

    return ""


def _parse_arena_sides(raw_body: Any) -> Dict[str, str]:
    text = raw_body.decode("utf-8", errors="ignore") if isinstance(raw_body, (bytes, bytearray)) else str(raw_body or "")
    if not text:
        return {"response_a": "", "response_b": ""}

    text = _fix_arena_mojibake(text)
    chunks = {"a": [], "b": []}

    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        prefix, payload = line.split(":", 1)
        if prefix == "a0":
            value = _parse_text_frame(payload)
            if value:
                chunks["a"].append(value)
        elif prefix == "b0":
            value = _parse_text_frame(payload)
            if value:
                chunks["b"].append(value)

    return {
        "response_a": "".join(chunks["a"]),
        "response_b": "".join(chunks["b"]),
    }


def _is_arena_result(payload: Dict[str, Any]) -> bool:
    parser_id = str(payload.get("parser_id") or "").strip().lower()
    if parser_id.startswith("lmarena"):
        return True

    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    url = str(event.get("url") or "").strip().lower()
    return any(token in url for token in ("arena.ai", "lmarena.ai", "lmsys.org", "nextjs-api/stream"))


def _side_from_payload(payload: Dict[str, Any]) -> str:
    parse_result = payload.get("parse_result") if isinstance(payload.get("parse_result"), dict) else {}
    selected = str(parse_result.get("selected_side") or "").strip().lower()
    if selected in {"left", "a", "modela"}:
        return "a"
    if selected in {"right", "b", "modelb"}:
        return "b"

    parser_id = str(payload.get("parser_id") or "").strip().lower()
    if "right" in parser_id:
        return "b"
    if "left" in parser_id:
        return "a"
    return ""


def _build_event(payload: Dict[str, Any], context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not _is_arena_result(payload):
        return None

    raw_body = payload.get("raw_body") or ""
    parse_result = payload.get("parse_result") if isinstance(payload.get("parse_result"), dict) else {}
    event_meta = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    sides = _parse_arena_sides(raw_body)

    content = str(parse_result.get("content") or "")
    selected_side = _side_from_payload(payload)
    if content:
        if selected_side == "b" and not sides["response_b"]:
            sides["response_b"] = content
        elif selected_side != "b" and not sides["response_a"]:
            sides["response_a"] = content

    if not sides["response_a"] and not sides["response_b"]:
        return None

    done = bool(parse_result.get("done", False))
    if not done and not _env_flag("ARENA_EVENT_BRIDGE_EMIT_PARTIAL", False):
        return None

    prompt = str(
        context.get("prompt")
        or payload.get("prompt")
        or _extract_prompt_from_request_payload(payload.get("request_post_data"))
        or ""
    ).strip()
    session_id = str(context.get("session_id") or payload.get("session_id") or "").strip()
    completion_id = str(context.get("completion_id") or payload.get("completion_id") or "").strip()
    parser_id = str(payload.get("parser_id") or "").strip()

    event = {
        "schema": 1,
        "event": "arena_response",
        "ts": time.time(),
        "source": "new_test_result_bridge",
        "site": "arena",
        "session_id": session_id,
        "completion_id": completion_id,
        "parser_id": parser_id,
        "url": str(event_meta.get("url") or ""),
        "method": str(event_meta.get("method") or ""),
        "status": event_meta.get("status", 0),
        "prompt": prompt,
        "response_a": sides["response_a"],
        "response_b": sides["response_b"],
        "selected_side": selected_side,
        "winner_side": str(parse_result.get("winner_side") or ""),
        "completion_side": str(parse_result.get("completion_side") or ""),
        "done": done,
        "raw_body_len": len(str(raw_body or "")),
    }

    digest_source = json.dumps(
        {
            "prompt": event["prompt"],
            "a": event["response_a"],
            "b": event["response_b"],
            "done": event["done"],
            "parser_id": event["parser_id"],
            "completion_id": event["completion_id"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    event["event_id"] = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:24]
    return event


def _append_event(event: Dict[str, Any]) -> None:
    path = _get_event_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")


def _event_digest(event: Dict[str, Any]) -> str:
    digest_source = json.dumps(
        {
            "prompt": event.get("prompt") or "",
            "a": event.get("response_a") or "",
            "b": event.get("response_b") or "",
            "model_a": event.get("model_a") or "",
            "model_b": event.get("model_b") or "",
            "model_id_a": event.get("model_id_a") or "",
            "model_id_b": event.get("model_id_b") or "",
            "done": bool(event.get("done", False)),
            "parser_id": event.get("parser_id") or "",
            "completion_id": event.get("completion_id") or "",
            "conversation_id": event.get("conversation_id") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:24]


def _dedupe_and_append(event: Dict[str, Any]) -> bool:
    digest = str(event.get("event_id") or "")
    dedupe_key = (
        f"{event.get('session_id')}:{event.get('completion_id')}:"
        f"{event.get('conversation_id')}:{event.get('parser_id')}"
    )
    if digest and _LAST_DIGEST_BY_KEY.get(dedupe_key) == digest:
        return False
    if digest:
        _LAST_DIGEST_BY_KEY[dedupe_key] = digest
    _append_event(event)
    return True


def emit_arena_snapshot_event(snapshot: Dict[str, Any]) -> bool:
    """Emit a full Arena response event from a live page store snapshot."""
    if not _env_flag("ARENA_EVENT_BRIDGE_ENABLED", True):
        return False
    if not isinstance(snapshot, dict):
        return False

    response_a = str(snapshot.get("response_a") or "")
    response_b = str(snapshot.get("response_b") or "")
    snapshot_model_a = str(snapshot.get("model_a") or "").strip()
    snapshot_model_b = str(snapshot.get("model_b") or "").strip()
    model_id_a = str(snapshot.get("model_id_a") or snapshot_model_a).strip()
    model_id_b = str(snapshot.get("model_id_b") or snapshot_model_b).strip()
    model_a = snapshot_model_a if snapshot_model_a and snapshot_model_a != model_id_a else _resolve_model_id(model_id_a)
    model_b = snapshot_model_b if snapshot_model_b and snapshot_model_b != model_id_b else _resolve_model_id(model_id_b)
    if not (response_a and response_b and model_id_a and model_id_b):
        return False

    event = {
        "schema": 1,
        "event": "arena_response",
        "ts": time.time(),
        "source": "arena_store_snapshot_bridge",
        "site": "arena",
        "session_id": str(snapshot.get("session_id") or ""),
        "completion_id": "",
        "conversation_id": str(snapshot.get("conversation_id") or ""),
        "parser_id": "arena_store_snapshot",
        "url": str(snapshot.get("url") or ""),
        "method": "",
        "status": 0,
        "prompt": str(snapshot.get("prompt") or ""),
        "response_a": response_a,
        "response_b": response_b,
        "model_a": model_a,
        "model_b": model_b,
        "model_id_a": model_id_a,
        "model_id_b": model_id_b,
        "message_id_a": str(snapshot.get("message_id_a") or ""),
        "message_id_b": str(snapshot.get("message_id_b") or ""),
        "selected_side": "",
        "winner_side": "",
        "completion_side": "",
        "done": True,
        "raw_body_len": 0,
    }
    event["event_id"] = _event_digest(event)
    emitted = _dedupe_and_append(event)
    if emitted:
        logger.debug(
            "[RESULT_EVENT_BRIDGE] Arena reveal snapshot emitted "
            f"(model_a={model_a}, model_b={model_b}, "
            f"model_id_a={model_id_a}, model_id_b={model_id_b}, "
            f"a={len(response_a)}, b={len(response_b)})"
        )
    return emitted


def create_result_event_handler(
    context_provider: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Optional[Callable[[Dict[str, Any]], bool]]:
    if not _env_flag("ARENA_EVENT_BRIDGE_ENABLED", True):
        logger.info("[RESULT_EVENT_BRIDGE] Arena event bridge disabled by environment")
        return None

    def handle_result(payload: Dict[str, Any]) -> bool:
        try:
            context = context_provider() if context_provider else {}
            if not isinstance(context, dict):
                context = {}

            event = _build_event(payload if isinstance(payload, dict) else {}, context)
            if not event:
                return False

            if event.get("event_id"):
                event["conversation_id"] = str(event.get("conversation_id") or "")
            if not _dedupe_and_append(event):
                return False
            logger.debug(
                "[RESULT_EVENT_BRIDGE] Arena event emitted "
                f"(parser={event.get('parser_id')}, a={len(event.get('response_a') or '')}, "
                f"b={len(event.get('response_b') or '')}, done={event.get('done')})"
            )
        except Exception as exc:
            logger.debug(f"[RESULT_EVENT_BRIDGE] emit failed: {exc}")
        return False

    return handle_result


__all__ = ["create_result_event_handler", "emit_arena_snapshot_event"]
