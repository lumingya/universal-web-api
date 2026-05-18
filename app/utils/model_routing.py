"""
model_routing.py - Resolve OpenAI-style model ids to route-domain targets.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.utils.site_rules import derive_site_card_id
from app.utils.site_url import build_route_domain_aliases, get_preferred_route_domain


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
_MODEL_ALIAS_DELIMITERS = ("-", "_", "/", ":", ".")


def _normalize_model_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _build_model_id_candidates(route_domain: str) -> List[str]:
    """
    Build user-facing model ids for a routed site.

    Examples:
    - chat.deepseek.com -> chat.deepseek.com, deepseek
    - gemini.google.com -> gemini.google.com, gemini.com, gemini
    """
    normalized_route = _normalize_model_id(route_domain)
    if not normalized_route:
        return []

    result: List[str] = []
    seen = set()

    def _add(value: Any):
        candidate = _normalize_model_id(value)
        if candidate and candidate not in seen:
            seen.add(candidate)
            result.append(candidate)

    route_aliases = build_route_domain_aliases(normalized_route)

    _add(normalized_route)
    for alias in route_aliases:
        _add(alias)

    for alias in [normalized_route, *route_aliases]:
        _add(derive_site_card_id(alias))

    return result


def _matches_model_alias(model_id: str, alias_id: str) -> bool:
    if model_id == alias_id:
        return True
    if not alias_id or len(model_id) <= len(alias_id):
        return False
    if not model_id.startswith(alias_id):
        return False
    return model_id[len(alias_id)] in _MODEL_ALIAS_DELIMITERS


def _resolve_route_domain(tab: Dict[str, Any]) -> str:
    return str(
        tab.get("route_domain")
        or get_preferred_route_domain(tab.get("current_domain") or "")
        or ""
    ).strip().lower()


def collect_route_domain_models(tabs: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Build a deduplicated model list from active tabs.

    Exact route-domain ids are always exported. Friendly aliases such as
    "deepseek" are exported only when they map to a single routed site.
    """
    exact_route_domains = set()
    alias_map: Dict[str, str] = {}
    ambiguous_aliases = set()

    for tab in tabs or []:
        route_domain = _resolve_route_domain(tab)
        if not route_domain:
            continue

        exact_route_domains.add(route_domain)

        for alias_id in _build_model_id_candidates(route_domain):
            if alias_id == route_domain:
                continue
            existing = alias_map.get(alias_id)
            if existing and existing != route_domain:
                ambiguous_aliases.add(alias_id)
                continue
            alias_map[alias_id] = route_domain

    result: List[Dict[str, str]] = []
    for route_domain in sorted(exact_route_domains):
        result.append({
            "id": route_domain,
            "route_domain": route_domain,
        })

    for alias_id in sorted(alias_map):
        if alias_id in ambiguous_aliases or alias_id in exact_route_domains:
            continue
        result.append({
            "id": alias_id,
            "route_domain": alias_map[alias_id],
        })

    return result


def inspect_model_route(model: Any, tabs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Return detailed model-route resolution information for logging/debugging.
    """
    normalized_model = _normalize_model_id(model)
    models = collect_route_domain_models(tabs)
    available_model_ids = [str(item.get("id") or "") for item in models]

    info: Dict[str, Any] = {
        "normalized_model": normalized_model,
        "route_domain": "",
        "matched_id": "",
        "match_type": "none",
        "available_model_ids": available_model_ids,
    }

    if normalized_model in _GENERIC_MODEL_IDS:
        info["match_type"] = "generic"
        return info

    for item in models:
        if normalized_model == item["id"]:
            info["route_domain"] = item["route_domain"]
            info["matched_id"] = item["id"]
            info["match_type"] = "exact"
            return info

    for item in models:
        if _matches_model_alias(normalized_model, item["id"]):
            info["route_domain"] = item["route_domain"]
            info["matched_id"] = item["id"]
            info["match_type"] = "prefix"
            return info

    return info


def resolve_model_route_domain(model: Any, tabs: List[Dict[str, Any]]) -> str:
    """
    Resolve a client-supplied `model` to a route-domain target.

    Returns an empty string when the model should continue using the default
    generic tab allocation flow.
    """
    return str(inspect_model_route(model, tabs).get("route_domain") or "")


__all__ = [
    "collect_route_domain_models",
    "inspect_model_route",
    "resolve_model_route_domain",
]
