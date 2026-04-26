"""
site_url.py - URL helpers for identifying real remote sites and route domains.
"""

from __future__ import annotations

import ipaddress
from typing import Optional, List
from urllib.parse import urlparse

from app.utils.site_rules import build_route_alias_groups


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_LOCAL_SUFFIXES = (
    ".local",
    ".lan",
    ".internal",
    ".localhost",
    ".test",
    ".example",
    ".invalid",
    ".home.arpa",
)


def extract_remote_site_domain(url: str) -> Optional[str]:
    """Return the hostname for a real remote website, otherwise None."""
    raw = str(url or "").strip()
    if not raw:
        return None

    try:
        parsed = urlparse(raw)
    except Exception:
        return None

    if parsed.scheme not in {"http", "https"}:
        return None

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return None

    if hostname in _LOCAL_HOSTS or hostname.endswith(_LOCAL_SUFFIXES):
        return None

    try:
        ip = ipaddress.ip_address(hostname)
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_multicast
        ):
            return None
        return hostname
    except ValueError:
        pass

    if "." not in hostname:
        return None

    return hostname


def is_remote_site_url(url: str) -> bool:
    return extract_remote_site_domain(url) is not None


def normalize_route_domain(value: str) -> str:
    """Normalize a route-domain-like input into a lowercase hostname."""
    raw = str(value or "").strip().lower()
    if not raw:
        return ""

    candidate = raw
    if "://" not in candidate:
        candidate = f"https://{candidate.lstrip('/')}"

    try:
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        if hostname:
            return hostname
    except Exception:
        pass

    return raw.strip().strip("/").split("/", 1)[0].strip().strip(".")


def build_route_domain_aliases(value: str) -> List[str]:
    """Build compatible route-domain aliases for a hostname-like value."""
    normalized = normalize_route_domain(value)
    if not normalized:
        return []

    aliases: List[str] = []
    seen = set()

    def _add(item: str):
        host = normalize_route_domain(item)
        if host and host not in seen:
            seen.add(host)
            aliases.append(host)

    _add(normalized)

    if normalized.startswith("www."):
        _add(normalized[4:])
    else:
        _add(f"www.{normalized}")

    for group in build_route_alias_groups():
        if normalized in group:
            for alias in group:
                _add(alias)

    return aliases


def get_preferred_route_domain(value: str) -> str:
    """Return the preferred public route-domain alias for a site."""
    normalized = normalize_route_domain(value)
    if not normalized:
        return ""

    for group in build_route_alias_groups():
        if normalized in group:
            return group[0]

    if normalized.startswith("www."):
        return normalized[4:]
    return normalized


def route_domain_matches(target_domain: str, actual_domain: str) -> bool:
    """Whether the route-domain target can represent the actual page domain."""
    target_aliases = build_route_domain_aliases(target_domain)
    actual_aliases = build_route_domain_aliases(actual_domain)
    if not target_aliases or not actual_aliases:
        return False

    for target in target_aliases:
        for actual in actual_aliases:
            if actual == target:
                return True
            if actual.endswith(f".{target}") or target.endswith(f".{actual}"):
                return True
    return False


__all__ = [
    "build_route_domain_aliases",
    "extract_remote_site_domain",
    "get_preferred_route_domain",
    "is_remote_site_url",
    "normalize_route_domain",
    "route_domain_matches",
]
