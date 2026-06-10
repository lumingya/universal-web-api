"""
app/core/request_transport.py - preset-level request transport configuration.

职责：
- 定义 request_transport 配置默认值
- 规范化 request_transport 配置
- 分发页面内 page_fetch profile
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from app.core.config import logger
from app.core.page_capture import (
    execute_page_request_transport,
    get_page_request_transport_profile,
    get_page_request_transport_profiles,
)


REQUEST_TRANSPORT_MODE_WORKFLOW = "workflow"
REQUEST_TRANSPORT_MODE_PAGE_FETCH = "page_fetch"


def get_default_request_transport_config() -> Dict[str, Any]:
    return {
        "mode": REQUEST_TRANSPORT_MODE_WORKFLOW,
        "profile": "",
        "options": {},
    }


def get_request_transport_profiles() -> List[Dict[str, Any]]:
    return get_page_request_transport_profiles()


def get_request_transport_profile(profile_id: Any) -> Optional[Dict[str, Any]]:
    return get_page_request_transport_profile(profile_id)


def _coerce_transport_mode(value: Any) -> str:
    mode = str(value or REQUEST_TRANSPORT_MODE_WORKFLOW).strip().lower()
    if mode in {REQUEST_TRANSPORT_MODE_WORKFLOW, REQUEST_TRANSPORT_MODE_PAGE_FETCH}:
        return mode
    return REQUEST_TRANSPORT_MODE_WORKFLOW


def normalize_request_transport_config(raw_config: Any) -> Dict[str, Any]:
    result = get_default_request_transport_config()
    if not isinstance(raw_config, dict):
        return result

    result["mode"] = _coerce_transport_mode(raw_config.get("mode"))
    result["profile"] = str(raw_config.get("profile") or "").strip()

    profile = get_request_transport_profile(result["profile"])
    raw_options = raw_config.get("options")
    normalized_options: Dict[str, Any] = {}
    if isinstance(raw_options, dict):
        normalized_options = copy.deepcopy(raw_options)

    if profile:
        option_defs = profile.get("options") or []
        next_options: Dict[str, Any] = {}
        for option_def in option_defs:
            key = str(option_def.get("key") or "").strip()
            if not key:
                continue
            default_value = copy.deepcopy(option_def.get("default"))
            option_type = str(option_def.get("type") or "").strip().lower()
            value = normalized_options.get(key, default_value)
            if option_type == "enum":
                allowed_values = {
                    str(choice.get("value") or "").strip()
                    for choice in (option_def.get("choices") or [])
                    if str(choice.get("value") or "").strip()
                }
                value_text = str(value or "").strip()
                if value_text not in allowed_values:
                    value = default_value
                else:
                    value = value_text
            elif option_type == "string":
                value = str(value or "").strip() or str(default_value or "").strip()
            next_options[key] = value
        result["options"] = next_options
    else:
        result["options"] = normalized_options

    return result


def get_request_transport_defaults_payload() -> Dict[str, Any]:
    return {
        "defaults": get_default_request_transport_config(),
        "mode_options": [
            REQUEST_TRANSPORT_MODE_WORKFLOW,
            REQUEST_TRANSPORT_MODE_PAGE_FETCH,
        ],
        "profiles": get_request_transport_profiles(),
    }


def execute_request_transport(
    tab: Any,
    transport_config: Dict[str, Any],
    *,
    prompt: str,
    consume_response: bool,
) -> Dict[str, Any]:
    normalized = normalize_request_transport_config(transport_config)
    mode = normalized.get("mode")
    profile_id = str(normalized.get("profile") or "").strip()
    options = normalized.get("options") or {}

    if mode != REQUEST_TRANSPORT_MODE_PAGE_FETCH or not profile_id:
        return {"ok": False, "error": "request_transport_not_enabled"}

    try:
        return execute_page_request_transport(
            tab,
            profile_id,
            options=options if isinstance(options, dict) else {},
            prompt=str(prompt or ""),
            consume_response=consume_response,
        )
    except Exception as e:
        logger.warning(f"[REQUEST_TRANSPORT] 页面直发执行异常: {e}")
        return {"ok": False, "error": str(e)}


__all__ = [
    "REQUEST_TRANSPORT_MODE_PAGE_FETCH",
    "REQUEST_TRANSPORT_MODE_WORKFLOW",
    "execute_request_transport",
    "get_default_request_transport_config",
    "get_request_transport_defaults_payload",
    "get_request_transport_profile",
    "get_request_transport_profiles",
    "normalize_request_transport_config",
]
