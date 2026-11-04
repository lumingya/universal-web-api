import re
from typing import Any, Dict, List

from app.utils.site_url import (
    encode_tab_url_route_token,
    normalize_exact_tab_url,
    normalize_route_domain,
)

ROUTE_GROUP_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ROUTE_GROUP_ALLOCATION_MODES = {"first_idle", "round_robin", "random"}


def normalize_route_group_id(value: Any) -> str:
    group_id = str(value or "").strip().lower()
    return group_id if ROUTE_GROUP_ID_PATTERN.fullmatch(group_id) else ""


def normalize_route_group_member(value: Any) -> Dict[str, Any]:
    payload = {"url": value} if isinstance(value, str) else value
    if not isinstance(payload, dict):
        return {}

    url = normalize_exact_tab_url(str(payload.get("url") or "").strip())
    url_token = str(payload.get("url_token") or "").strip().lower()
    if url and not url_token:
        url_token = encode_tab_url_route_token(url)
    if not url and not url_token:
        return {}

    try:
        tab_index = int(payload.get("tab_index") or 0)
    except (TypeError, ValueError):
        tab_index = 0

    member = {
        "url": url,
        "url_token": url_token,
    }
    if tab_index > 0:
        member["tab_index"] = tab_index
    return member


def route_group_member_key(member: Dict[str, Any]) -> str:
    token = str(member.get("url_token") or "").strip().lower()
    url = normalize_exact_tab_url(str(member.get("url") or "").strip())
    try:
        tab_index = int(member.get("tab_index") or 0)
    except (TypeError, ValueError):
        tab_index = 0
    return f"{token or url}::#{tab_index if tab_index > 0 else '*'}"


def normalize_route_groups(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        source = []
        for key, item in value.items():
            payload = dict(item) if isinstance(item, dict) else {"members": item}
            payload.setdefault("id", key)
            source.append(payload)
    elif isinstance(value, list):
        source = value
    else:
        source = []

    normalized: List[Dict[str, Any]] = []
    seen_groups = set()
    for item in source:
        if not isinstance(item, dict):
            continue
        group_id = normalize_route_group_id(item.get("id") or item.get("group_id"))
        if not group_id or group_id in seen_groups:
            continue

        members: List[Dict[str, Any]] = []
        seen_members = set()
        for raw_member in item.get("members") or []:
            member = normalize_route_group_member(raw_member)
            member_key = route_group_member_key(member) if member else ""
            if not member_key or member_key in seen_members:
                continue
            seen_members.add(member_key)
            members.append(member)

        allocation_mode = str(item.get("allocation_mode") or "round_robin").strip().lower()
        if allocation_mode not in ROUTE_GROUP_ALLOCATION_MODES:
            allocation_mode = "round_robin"

        route_domain = normalize_route_domain(item.get("route_domain"))
        normalized.append({
            "id": group_id,
            "name": str(item.get("name") or group_id).strip()[:100] or group_id,
            "route_domain": route_domain,
            "preset_name": str(item.get("preset_name") or "").strip()[:100],
            "allocation_mode": allocation_mode,
            "members": members,
        })
        seen_groups.add(group_id)

    return normalized


def route_groups_by_id(value: Any) -> Dict[str, Dict[str, Any]]:
    return {item["id"]: item for item in normalize_route_groups(value)}
