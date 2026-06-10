"""
app/core/page_capture/request_transport.py - page-side request transport registry.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List


PageRequestTransportExecutor = Callable[..., Dict[str, Any]]

_PROFILES: Dict[str, Dict[str, Any]] = {}
_EXECUTORS: Dict[str, PageRequestTransportExecutor] = {}


def register_page_request_transport(
    profile: Dict[str, Any],
    executor: PageRequestTransportExecutor,
) -> None:
    profile_id = str((profile or {}).get("id") or "").strip()
    if not profile_id:
        raise ValueError("page request transport profile id is required")
    if not callable(executor):
        raise ValueError(f"page request transport executor is not callable: {profile_id}")

    _PROFILES[profile_id] = copy.deepcopy(profile)
    _EXECUTORS[profile_id] = executor


def get_page_request_transport_profiles() -> List[Dict[str, Any]]:
    return [copy.deepcopy(profile) for profile in _PROFILES.values()]


def get_page_request_transport_profile(profile_id: Any) -> Dict[str, Any] | None:
    pid = str(profile_id or "").strip()
    profile = _PROFILES.get(pid)
    return copy.deepcopy(profile) if profile else None


def execute_page_request_transport(
    tab: Any,
    profile_id: Any,
    *,
    options: Dict[str, Any],
    prompt: str,
    consume_response: bool,
) -> Dict[str, Any]:
    pid = str(profile_id or "").strip()
    executor = _EXECUTORS.get(pid)
    if executor is None:
        return {"ok": False, "error": f"unsupported_request_transport_profile:{pid}"}

    return executor(
        tab=tab,
        options=options if isinstance(options, dict) else {},
        prompt=str(prompt or ""),
        consume_response=consume_response,
    )


__all__ = [
    "execute_page_request_transport",
    "get_page_request_transport_profile",
    "get_page_request_transport_profiles",
    "register_page_request_transport",
]
