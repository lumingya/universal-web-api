"""
model_routing.py - Resolve OpenAI-style model ids to route-domain targets.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.utils.site_url import get_preferred_route_domain, normalize_route_domain


_GENERIC_MODEL_IDS = {
    "",
    "any",
    "auto",
    "default",
    "web-browser",
    "gpt-3.5-turbo",
    "gpt-4",
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o-mini",
}

_GENERIC_HOST_LABELS = {
    "www",
    "chat",
    "api",
    "app",
    "web",
    "console",
}


def _normalize_model_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _pick_short_alias(route_domain: str) -> str:
    labels = [part for part in str(route_domain or "").strip().lower().split(".") if part]
    if not labels:
        return ""

    for label in labels:
        if label not in _GENERIC_HOST_LABELS:
            return label
    return labels[0]


def _iter_route_model_ids(route_domain: str):
    normalized = normalize_route_domain(route_domain)
    if not normalized:
        return

    yield normalized

    short_alias = _pick_short_alias(normalized)
    if short_alias and short_alias != normalized:
        yield short_alias


def collect_route_domain_models(tabs: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Build a deduplicated model list from active tabs.

    Each active route domain contributes:
    - the exact route-domain id, e.g. `chat.qwen.ai`
    - one short alias when possible, e.g. `qwen`
    """
    result: List[Dict[str, str]] = []
    seen = set()

    for tab in tabs or []:
        route_domain = str(
            tab.get("route_domain")
            or get_preferred_route_domain(tab.get("current_domain") or "")
            or ""
        ).strip().lower()
        if not route_domain:
            continue

        for model_id in _iter_route_model_ids(route_domain):
            key = (model_id, route_domain)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "id": model_id,
                "route_domain": route_domain,
            })

    result.sort(key=lambda item: (item["route_domain"], item["id"]))
    return result


def resolve_model_route_domain(model: Any, tabs: List[Dict[str, Any]]) -> str:
    """
    Resolve a client-supplied `model` to a route-domain target.

    Returns an empty string when the model should continue using the default
    generic tab allocation flow.
    """
    normalized_model = _normalize_model_id(model)
    if normalized_model in _GENERIC_MODEL_IDS:
        return ""

    for item in collect_route_domain_models(tabs):
        if normalized_model == item["id"]:
            return item["route_domain"]

    return ""


__all__ = [
    "collect_route_domain_models",
    "resolve_model_route_domain",
]
