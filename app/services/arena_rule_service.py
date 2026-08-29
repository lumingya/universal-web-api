"""
app/services/arena_rule_service.py - Arena 规则匹配与候选记录业务服务

职责：
- 评估多规则匹配逻辑（必含/排除词/检测器评估）；
- 整合 Link Drawer 本地书签抽屉写入；
- 生成标准化候选命中记录并调用 command_result_store 进行持久化。
"""

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

def _get_store() -> Any:
    from app.services import command_result_store
    return command_result_store

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

_DRAWER_LOCK = threading.RLock()


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


def _detector_accepts(
    prompt: str,
    response_text: str,
    rule: Dict[str, Any],
    values: Dict[str, Any],
) -> tuple[bool, Dict[str, Any]]:
    req_module = getattr(_get_store(), "requests", requests)
    keyword = str(rule.get("detector_keyword") or "").strip()
    if not keyword:
        return True, {"skipped": True, "models": []}
    detector_url = str(values.get("detector_url") or "http://127.0.0.1:8765/api/judge").strip()
    if req_module is None:
        return True, {"unavailable": True, "error": "requests unavailable", "models": []}
    try:
        response = req_module.post(
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
    with _DRAWER_LOCK:
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
            _get_store()._atomic_write_json(path, payload)
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
    """
    匹配 Arena 规则并保存候选结果到通用持久化存储与 Link Drawer。
    """
    rules = values.get("rules") if isinstance(values.get("rules"), list) else []
    response_sides = info.get("response_sides") if isinstance(info, dict) else []
    if not isinstance(response_sides, (list, tuple)) or not response_sides:
        response_sides = [str((info or {}).get("visible_text") or "")]
    url = str((info or {}).get("url") or "").strip()
    if not url:
        return {"recorded": [], "matched": 0}

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

    store = _get_store()
    existing_results = store.list_command_results(command_id, limit=2000)
    existing_hits = {
        str(item.get("rule_id") or "")
        for item in existing_results
        if isinstance(item, dict) and item.get("url") == url
    }

    # Dispatch detector using command_result_store._detector_accepts so test patches take effect
    detector_fn = getattr(store, "_detector_accepts", _detector_accepts)
    matches_rule_fn = getattr(store, "_matches_rule", _matches_rule)

    for rule_index, rule in enumerate(rules):
        if not isinstance(rule, dict) or rule.get("enabled", True) is False:
            continue
        rule_id = str(rule.get("id") or f"rule-{rule_index + 1}")
        if rule_id in existing_hits:
            continue
        for side_index, response_text in enumerate(response_sides):
            text = str(response_text or "").strip()
            if not text:
                continue
            
            is_reasoning_side = False
            has_reasoning_sides = info.get("has_reasoning_sides") if isinstance(info, dict) else None
            if isinstance(has_reasoning_sides, list) and side_index < len(has_reasoning_sides):
                is_reasoning_side = bool(has_reasoning_sides[side_index])
                
            if rule.get("exclude_reasoning") and is_reasoning_side:
                continue
                
            matched, _ = matches_rule_fn(text, rule)
            if not matched:
                continue
            accepted, detector = detector_fn(prompt, text, rule, values)
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

    new_records: List[Dict[str, Any]] = []
    if not identity_unresolved and candidates:
        identity = _resolve_profile()
        current_records = store.get_all_records()
        for rule_id, rule, rule_index, side_index, text, detector in candidates:
            if any(
                item.get("command_id") == command_id
                and item.get("rule_id") == rule_id
                and item.get("url") == url
                for item in current_records
                if isinstance(item, dict)
            ):
                continue
            model = str(rule.get("model_name") or rule.get("name") or f"model-{rule_index + 1}").strip()
            profile = str(identity.get("name") or "").strip()
            title = _next_title(current_records, profile, model, str(rule.get("title_template") or ""))
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
            current_records.append(record)
            new_records.append(record)

        if new_records:
            # The final duplicate check and write must share one storage lock;
            # otherwise two concurrent bridge callbacks can persist the same hit.
            add_unique = getattr(store, "add_records_unique", None)
            if callable(add_unique):
                persisted_records = add_unique(
                    new_records,
                    ("command_id", "rule_id", "url"),
                )
            else:  # pragma: no cover - compatibility for lightweight test doubles
                persisted_records = store.add_records(new_records)
            new_records = persisted_records

    return {
        "recorded": new_records,
        "matched": len(new_records),
        "identity_unresolved": identity_unresolved,
    }


__all__ = [
    "_clean_title_component",
    "_controlled_browser_metadata",
    "_detector_accepts",
    "_matches_rule",
    "_next_title",
    "_resolve_drawer_file",
    "_split_terms",
    "_write_link_drawer",
    "record_arena_rule_candidates",
]
