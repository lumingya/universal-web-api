"""Persistent command hits and optional Link Drawer integration."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover - requests is an application dependency
    requests = None


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_FILE = _PROJECT_ROOT / "config" / "command_results.local.json"
_LOCK = threading.RLock()
_MAX_RECORDS = 5000


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


def _load_results() -> Dict[str, Any]:
    if not RESULTS_FILE.exists():
        return {"version": 1, "records": []}
    try:
        with RESULTS_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {"version": 1, "records": []}
    records = payload.get("records") if isinstance(payload, dict) else []
    return {"version": 1, "records": records if isinstance(records, list) else []}


def list_command_results(command_id: str, limit: int = 500) -> List[Dict[str, Any]]:
    command_id = str(command_id or "").strip()
    with _LOCK:
        records = _load_results()["records"]
    filtered = [item for item in records if isinstance(item, dict) and item.get("command_id") == command_id]
    filtered.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    return filtered[: max(1, min(int(limit or 500), 2000))]


def clear_command_results(command_id: str, rule_id: str = "") -> int:
    command_id = str(command_id or "").strip()
    rule_id = str(rule_id or "").strip()
    with _LOCK:
        payload = _load_results()
        before = len(payload["records"])
        payload["records"] = [
            item
            for item in payload["records"]
            if item.get("command_id") != command_id
            or (rule_id and str(item.get("rule_id") or "") != rule_id)
        ]
        removed = before - len(payload["records"])
        if removed:
            _atomic_write_json(RESULTS_FILE, payload)
        return removed


def _split_terms(value: Any) -> List[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = re.split(r"[,，\n\r]+", str(value or ""))
    return [str(part).strip() for part in parts if str(part).strip()]


def _matches_rule(text: str, rule: Dict[str, Any]) -> tuple[bool, str]:
    folded = str(text or "").casefold()
    excluded = _split_terms(rule.get("excluded"))
    for token in excluded:
        if token.casefold() in folded:
            return False, f"excluded:{token}"

    required_all = _split_terms(rule.get("required_all"))
    missing = [token for token in required_all if token.casefold() not in folded]
    if missing:
        return False, f"missing:{','.join(missing)}"

    required_any = _split_terms(rule.get("required_any"))
    if required_any and not any(token.casefold() in folded for token in required_any):
        return False, "missing_any"
    return True, "matched"


def _detector_accepts(prompt: str, response_text: str, rule: Dict[str, Any], values: Dict[str, Any]) -> tuple[bool, Dict[str, Any]]:
    keyword = str(rule.get("detector_keyword") or "").strip()
    if not keyword:
        return True, {"skipped": True, "models": []}
    detector_url = str(values.get("detector_url") or "http://127.0.0.1:8765/api/judge").strip()
    if requests is None:
        return True, {"unavailable": True, "error": "requests unavailable", "models": []}
    try:
        response = requests.post(
            detector_url,
            json={"prompt": str(prompt or ""), "response": str(response_text or "")},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        predictions = list(payload.get("predictions") or [])[:5] if isinstance(payload, dict) else []
        models = [str(item.get("model") or "").strip() for item in predictions if isinstance(item, dict)]
        accepted = any(keyword.casefold() in model.casefold() for model in models)
        return accepted, {"models": models, "best_model": payload.get("best_model") if isinstance(payload, dict) else ""}
    except Exception as error:
        # Preserve a text-filter hit if the optional local detector is offline.
        return True, {"unavailable": True, "error": str(error), "models": []}


def _resolve_drawer_file(raw_path: Any) -> Path | None:
    text = os.path.expandvars(os.path.expanduser(str(raw_path or "").strip()))
    if not text:
        return None
    path = Path(text)
    return path / "drawer_data.json" if path.is_dir() or not path.suffix else path


def _write_link_drawer(
    path: Path | None,
    url: str,
    title: str,
    category: str,
    controlled_browser: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if path is None:
        return {"status": "disabled"}
    with _LOCK:
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            else:
                payload = {"categories": ["默认分类"], "links": [], "settings": {}}
            categories = payload.get("categories") if isinstance(payload.get("categories"), list) else []
            links = payload.get("links") if isinstance(payload.get("links"), list) else []
            category = str(category or "默认分类").strip() or "默认分类"
            if category not in categories:
                categories.append(category)
            normalized_url = str(url or "").strip().rstrip("/")
            for item in links:
                if str(item.get("url") or "").strip().rstrip("/") == normalized_url:
                    return {"status": "duplicate", "category": item.get("category", "")}
            link = {
                "id": uuid.uuid4().hex[:8],
                "url": str(url or "").strip(),
                "title": title,
                "category": category,
                "dateAdded": int(time.time() * 1000),
            }
            if isinstance(controlled_browser, dict) and controlled_browser:
                link["controlledBrowser"] = controlled_browser
            links.append(link)
            payload["categories"] = categories
            payload["links"] = links
            _atomic_write_json(path, payload)
            return {"status": "added", "category": category}
        except Exception as error:
            return {"status": "error", "error": str(error)}


def _clean_title_component(value: Any, fallback: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    return re.sub(r"\s+", " ", text) or fallback


def _next_title(
    records: Iterable[Dict[str, Any]],
    profile: str,
    model: str,
    title_template: str = "",
) -> str:
    raw_profile = str(profile or "").strip()
    raw_model = str(model or "").strip()
    profile = _clean_title_component(profile, "profile").replace("《", "〈").replace("》", "〉")
    model = _clean_title_component(model, "model")
    template = str(title_template or "").strip() or "《{profile}》-{model}-{index:03d}"
    matching_count = 0
    for item in records:
        if (
            str(item.get("browser_profile_name") or "").strip() != raw_profile
            or str(item.get("model_name") or "").strip() != raw_model
        ):
            continue
        matching_count += 1
    index = matching_count + 1
    try:
        title = template.format(profile=profile, model=model, index=index)
    except (KeyError, ValueError, IndexError):
        title = f"《{profile}》-{model}-{index:03d}"
    return _clean_title_component(title, f"《{profile}》-{model}-{index:03d}")


def _controlled_browser_metadata(values: Dict[str, Any], identity: Dict[str, Any]) -> Dict[str, Any]:
    profile = {
        key: str(identity.get(key) or "").strip()
        for key in (
            "name",
            "profile_directory",
            "profile_path",
            "user_data_dir",
            "browser_context_id",
            "source_tab_id",
        )
        if str(identity.get(key) or "").strip()
    }
    return {
        "version": 1,
        "apiUrl": str(
            values.get("controlled_browser_api_url")
            or "http://127.0.0.1:8199/api/browser/open-profile-url"
        ).strip(),
        "profile": profile,
    }


def record_arena_rule_candidates(
    command_id: str,
    values: Dict[str, Any],
    info: Dict[str, Any],
    prompt: str = "",
    source: str = "",
    profile_resolver: Optional[Callable[[], Any]] = None,
) -> Dict[str, Any]:
    rules = values.get("rules") if isinstance(values.get("rules"), list) else []
    response_sides = info.get("response_sides") if isinstance(info, dict) else []
    if not isinstance(response_sides, (list, tuple)) or not response_sides:
        response_sides = [str((info or {}).get("visible_text") or "")]
    url = str((info or {}).get("url") or "").strip()
    if not url:
        return {"recorded": [], "matched": 0}

    recorded: List[Dict[str, Any]] = []
    candidates: List[tuple[str, Dict[str, Any], int, int, str, Dict[str, Any]]] = []
    profile_identity: Optional[Dict[str, Any]] = None
    identity_unresolved = False

    def _resolve_profile() -> Dict[str, Any]:
        nonlocal profile_identity
        if profile_identity is not None:
            return profile_identity
        resolved: Any = {}
        if callable(profile_resolver):
            try:
                resolved = profile_resolver() or {}
            except Exception:
                resolved = {}
        if isinstance(resolved, str):
            resolved = {"name": resolved}
        profile_identity = resolved if isinstance(resolved, dict) else {}
        return profile_identity

    with _LOCK:
        existing_hits = {
            str(item.get("rule_id") or "")
            for item in _load_results()["records"]
            if isinstance(item, dict)
            and item.get("command_id") == command_id
            and item.get("url") == url
        }

    # Detector requests can take up to 20 seconds. Keep them outside the
    # process-wide persistence lock so reads, clears, and unrelated commands
    # remain responsive while a detector is slow or offline.
    for rule_index, rule in enumerate(rules):
        if not isinstance(rule, dict) or rule.get("enabled", True) is False:
            continue
        rule_id = str(rule.get("id") or f"rule-{rule_index + 1}")
        if rule_id in existing_hits:
            continue
        for side_index, response_text in enumerate(response_sides):
            text = str(response_text or "").strip()
            
            is_reasoning_side = False
            has_reasoning_sides = info.get("has_reasoning_sides") if isinstance(info, dict) else None
            if isinstance(has_reasoning_sides, list) and side_index < len(has_reasoning_sides):
                is_reasoning_side = bool(has_reasoning_sides[side_index])
                
            if rule.get("exclude_reasoning") and is_reasoning_side:
                continue
                
            matched, _ = _matches_rule(text, rule)
            if not matched:
                continue
            accepted, detector = _detector_accepts(prompt, text, rule, values)
            if accepted:
                candidates.append((rule_id, rule, rule_index, side_index, text, detector))
                break

    if candidates:
        identity = _resolve_profile()
        if not str(identity.get("name") or "").strip():
            identity_unresolved = True
            logger.warning(
                "[CMD_RESULT] browser profile name 为空，%d 条候选命中将被跳过。"
                "请确认 profile_resolver 已正确返回 identity。",
                len(candidates),
            )

    with _LOCK:
        payload = _load_results()
        records = payload["records"]
        if not identity_unresolved:
            for rule_id, rule, rule_index, side_index, text, detector in candidates:
                # Another worker may have persisted the same hit while the
                # detector was running, so deduplicate again under the lock.
                if any(
                    item.get("command_id") == command_id
                    and item.get("rule_id") == rule_id
                    and item.get("url") == url
                    for item in records
                    if isinstance(item, dict)
                ):
                    continue
                model = str(rule.get("model_name") or rule.get("name") or f"model-{rule_index + 1}").strip()
                profile = str(identity.get("name") or "").strip()
                title = _next_title(records, profile, model, str(rule.get("title_template") or ""))
                drawer = _write_link_drawer(
                    _resolve_drawer_file(values.get("link_drawer_path")),
                    url,
                    title,
                    str(rule.get("drawer_group") or model),
                    _controlled_browser_metadata(values, identity),
                )
                record = {
                    "id": uuid.uuid4().hex,
                    "command_id": command_id,
                    "rule_id": rule_id,
                    "rule_name": str(rule.get("name") or model),
                    "model_name": model,
                    "browser_profile_name": profile,
                    "browser_profile": identity,
                    "title": title,
                    "url": url,
                    "side": "A" if side_index == 0 else "B",
                    "source": str(source or ""),
                    "response_preview": text[:500],
                    "detector": detector,
                    "drawer": drawer,
                    "created_at": time.time(),
                }
                records.append(record)
                recorded.append(record)
        if recorded:
            payload["records"] = records[-_MAX_RECORDS:]
            _atomic_write_json(RESULTS_FILE, payload)
    return {
        "recorded": recorded,
        "matched": len(recorded),
        "identity_unresolved": identity_unresolved,
    }
