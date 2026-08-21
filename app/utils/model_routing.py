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


def _clean_model_id(value: Any) -> str:
    return str(value or "").strip()


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


def _default_model_id_for_tab(tab: Dict[str, Any]) -> str:
    return (
        _clean_model_id(tab.get("route_domain"))
        or _clean_model_id(get_preferred_route_domain(tab.get("current_domain") or ""))
        or _clean_model_id(tab.get("current_domain"))
    )


def _is_model_name_overridden(tab: Dict[str, Any]) -> bool:
    return bool(_clean_model_id(tab.get("model_name_override_source")))


def _get_exposed_model_id(tab: Dict[str, Any]) -> str:
    return _clean_model_id(tab.get("exposed_model_name")) or _default_model_id_for_tab(tab)


def collect_route_domain_models(tabs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build a deduplicated model list from active tabs.

    The exported model id is the tab's exposed model name. Tabs without a
    custom name keep the historical route-domain id and aliases.
    """
    model_map: Dict[str, Dict[str, Any]] = {}
    alias_map: Dict[str, str] = {}
    ambiguous_aliases = set()

    def _ensure_model(
        model_id: str,
        route_domain: str = "",
        *,
        allow_prefix: bool = True,
        is_route_alias: bool = False,
    ) -> None:
        normalized_model = _normalize_model_id(model_id)
        if not normalized_model:
            return
        if normalized_model not in model_map:
            model_map[normalized_model] = {
                "id": _clean_model_id(model_id),
                "display_name": _clean_model_id(model_id),
                "model_name": _clean_model_id(model_id),
                "route_type": "model_name",
                "route_domains": set(),
                "allow_prefix": bool(allow_prefix),
                "is_route_alias": bool(is_route_alias),
            }
        if route_domain:
            model_map[normalized_model]["route_domains"].add(route_domain)
        if not allow_prefix:
            model_map[normalized_model]["allow_prefix"] = False
        if not is_route_alias:
            model_map[normalized_model]["is_route_alias"] = False

    for tab in tabs or []:
        route_domain = _resolve_route_domain(tab)
        exposed_model_id = _get_exposed_model_id(tab)
        if not exposed_model_id:
            continue

        overridden = _is_model_name_overridden(tab)
        _ensure_model(
            exposed_model_id,
            route_domain,
            allow_prefix=not overridden,
            is_route_alias=not overridden,
        )

        if overridden or not route_domain:
            continue

        exposed_key = _normalize_model_id(exposed_model_id)
        for alias_id in _build_model_id_candidates(route_domain):
            alias_key = _normalize_model_id(alias_id)
            if not alias_key or alias_key == exposed_key:
                continue
            existing = alias_map.get(alias_key)
            if existing and existing != exposed_key:
                ambiguous_aliases.add(alias_key)
                continue
            alias_map[alias_key] = exposed_key

    for alias_key in sorted(alias_map):
        if alias_key in ambiguous_aliases or alias_key in model_map:
            continue
        target_key = alias_map[alias_key]
        target = model_map.get(target_key)
        if not target:
            continue
        model_map[alias_key] = {
            "id": alias_key,
            "display_name": alias_key,
            "model_name": target["model_name"],
            "route_type": "model_name",
            "route_domains": set(target.get("route_domains") or set()),
            "allow_prefix": True,
            "is_route_alias": True,
        }

    result: List[Dict[str, Any]] = []
    for model_key in sorted(model_map):
        item = model_map[model_key]
        route_domains = sorted(item.get("route_domains") or [])
        result.append({
            "id": item["id"],
            "display_name": item.get("display_name") or item["id"],
            "model_name": item.get("model_name") or item["id"],
            "route_type": item.get("route_type") or "model_name",
            "route_domain": route_domains[0] if len(route_domains) == 1 else "",
            "route_domains": route_domains,
            "allow_prefix": bool(item.get("allow_prefix", True)),
            "is_route_alias": bool(item.get("is_route_alias", False)),
        })

    return result


def inspect_model_route(
    model: Any,
    tabs: List[Dict[str, Any]],
    *,
    allow_prefix: bool = True,
    allow_generic: bool = True,
    models: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Return detailed model-route resolution information for logging/debugging.
    """
    normalized_model = _normalize_model_id(model)
    if models is None:
        models = collect_route_domain_models(tabs)
    available_model_ids = [str(item.get("id") or "") for item in models]

    info: Dict[str, Any] = {
        "normalized_model": normalized_model,
        "route_domain": "",
        "route_type": "",
        "model_name": "",
        "matched_id": "",
        "match_type": "none",
        "available_model_ids": available_model_ids,
    }

    # 优先级 1：先做标签页完全精确匹配并直接返回，避免暴露名恰为通用 id（如 gpt-4o）的
    # 标签页被 generic 判定抢先、永远无法被精确路由
    for item in models:
        if normalized_model == _normalize_model_id(item["id"]):
            info["route_domain"] = str(item.get("route_domain") or "")
            info["route_type"] = str(item.get("route_type") or "model_name")
            info["model_name"] = str(item.get("model_name") or item.get("id") or "")
            info["matched_id"] = str(item.get("id") or "")
            info["match_type"] = "exact"
            return info

    # 优先级 2：通用模型 id 落回 generic 默认分配流程（若启用 allow_generic）
    if allow_generic and normalized_model in _GENERIC_MODEL_IDS:
        info["match_type"] = "generic"
        return info

    if not allow_prefix:
        return info

    # 优先级 3：标签页前缀模糊匹配（按最长前缀优先匹配，避免短前缀遮蔽更具体的长前缀别名）
    prefix_matches = [
        item
        for item in models
        if item.get("allow_prefix") and _matches_model_alias(normalized_model, _normalize_model_id(item["id"]))
    ]
    if prefix_matches:
        best_match = max(prefix_matches, key=lambda x: len(str(x.get("id") or "")))
        info["route_domain"] = str(best_match.get("route_domain") or "")
        info["route_type"] = str(best_match.get("route_type") or "model_name")
        info["model_name"] = str(best_match.get("model_name") or best_match.get("id") or "")
        info["matched_id"] = str(best_match.get("id") or "")
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
