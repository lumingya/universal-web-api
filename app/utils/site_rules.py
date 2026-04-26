"""
Site-level rule loading shared by routing, startup pages, and defaults.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SITE_RULES_FILE = Path(
    os.getenv("SITE_RULES_FILE", str(_PROJECT_ROOT / "config" / "site_rules.json"))
)
_SITES_LOCAL_FILE = Path(
    os.getenv("SITES_LOCAL_FILE", str(_PROJECT_ROOT / "config" / "sites.local.json"))
)

_IGNORED_SITE_ID_PARTS = {
    "",
    "www",
    "chat",
    "web",
    "app",
    "api",
    "beta",
    "console",
    "new",
}

_SITE_RULES_CACHE: Dict[str, Any] = {
    "signature": None,
    "rules": {},
}


def _normalize_domain(value: Any) -> str:
    return str(value or "").strip().lower().strip(".")


def _normalize_rule(rule: Any) -> Dict[str, Any]:
    if not isinstance(rule, dict):
        return {}

    normalized: Dict[str, Any] = {}

    display_name = str(rule.get("display_name") or "").strip()
    if display_name:
        normalized["display_name"] = display_name

    startup_url = str(rule.get("startup_url") or "").strip()
    if startup_url:
        normalized["startup_url"] = startup_url

    card_id = str(rule.get("card_id") or "").strip()
    if card_id:
        normalized["card_id"] = card_id

    guide_priority = rule.get("guide_priority", None)
    if isinstance(guide_priority, (int, float)) and not isinstance(guide_priority, bool):
        normalized["guide_priority"] = int(guide_priority)

    if "stealth_default" in rule:
        normalized["stealth_default"] = bool(rule.get("stealth_default"))

    route_aliases = rule.get("route_aliases", [])
    if isinstance(route_aliases, list):
        aliases: List[str] = []
        seen = set()
        for raw_alias in route_aliases:
            alias = _normalize_domain(raw_alias)
            if not alias or alias in seen:
                continue
            seen.add(alias)
            aliases.append(alias)
        normalized["route_aliases"] = aliases

    return normalized


def _read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _build_cache_signature() -> Tuple[Tuple[str, float], ...]:
    signature: List[Tuple[str, float]] = []
    for path in (_SITE_RULES_FILE, _SITES_LOCAL_FILE):
        try:
            mtime = path.stat().st_mtime if path.exists() else 0.0
        except Exception:
            mtime = 0.0
        signature.append((str(path), float(mtime)))
    return tuple(signature)


def _load_rules_from_files() -> Dict[str, Dict[str, Any]]:
    shipped = _read_json_file(_SITE_RULES_FILE).get("sites", {})
    local = _read_json_file(_SITES_LOCAL_FILE).get("site_overrides", {})

    merged: Dict[str, Dict[str, Any]] = {}
    for source in (shipped, local):
        if not isinstance(source, dict):
            continue
        for raw_domain, raw_rule in source.items():
            domain = _normalize_domain(raw_domain)
            if not domain:
                continue
            current = merged.setdefault(domain, {})
            current.update(_normalize_rule(raw_rule))

    return merged


def load_site_rules(force: bool = False) -> Dict[str, Dict[str, Any]]:
    signature = _build_cache_signature()
    if not force and _SITE_RULES_CACHE.get("signature") == signature:
        return copy.deepcopy(_SITE_RULES_CACHE.get("rules", {}))

    rules = _load_rules_from_files()
    _SITE_RULES_CACHE["signature"] = signature
    _SITE_RULES_CACHE["rules"] = rules
    return copy.deepcopy(rules)


def get_site_rule(domain: str) -> Dict[str, Any]:
    normalized = _normalize_domain(domain)
    if not normalized:
        return {}
    return load_site_rules().get(normalized, {})


def build_route_alias_groups() -> Tuple[Tuple[str, ...], ...]:
    groups: List[Tuple[str, ...]] = []
    for domain, rule in load_site_rules().items():
        ordered: List[str] = []
        seen = set()
        for item in list(rule.get("route_aliases", []) or []) + [domain]:
            alias = _normalize_domain(item)
            if not alias or alias in seen:
                continue
            seen.add(alias)
            ordered.append(alias)
        if len(ordered) >= 2:
            groups.append(tuple(ordered))
    return tuple(groups)


def derive_site_card_id(domain: str) -> str:
    normalized = _normalize_domain(domain)
    if not normalized:
        return ""

    rule = get_site_rule(normalized)
    explicit = str(rule.get("card_id") or "").strip()
    if explicit:
        return explicit

    parts = [part for part in normalized.split(".")[:-1] if part not in _IGNORED_SITE_ID_PARTS]
    if parts:
        return parts[0]

    return normalized.split(".", 1)[0]


__all__ = [
    "build_route_alias_groups",
    "derive_site_card_id",
    "get_site_rule",
    "load_site_rules",
]
