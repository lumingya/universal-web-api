"""
site_url.py - URL helpers for identifying real remote sites and route domains.
"""

from __future__ import annotations

import ipaddress
import hashlib
from typing import Optional, List
from urllib.parse import urlparse, urlunparse

from app.utils.site_rules import build_route_alias_groups, load_site_rules


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


def get_canonical_route_domain(value: str) -> str:
    """Return the configured site domain for aliases used in routing."""
    normalized = normalize_route_domain(value)
    if not normalized:
        return ""

    rules = load_site_rules()
    if normalized in rules:
        return normalized

    if normalized.startswith("www.") and normalized[4:] in rules:
        return normalized[4:]

    for site_domain, rule in rules.items():
        aliases = list(rule.get("route_aliases", []) or [])
        for alias in aliases:
            normalized_alias = normalize_route_domain(alias)
            if not normalized_alias:
                continue
            if normalized == normalized_alias:
                return site_domain
            if normalized.startswith("www.") and normalized[4:] == normalized_alias:
                return site_domain

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


def normalize_exact_tab_url(value: str) -> str:
    """Normalize a full tab URL for strict route matching."""
    raw = str(value or "").strip()
    if not raw:
        return ""

    try:
        parsed = urlparse(raw)
    except Exception:
        return raw

    scheme = str(parsed.scheme or "").strip().lower()
    hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    if not scheme or not hostname:
        return raw

    default_port = 80 if scheme == "http" else 443 if scheme == "https" else None
    port = parsed.port
    netloc = hostname
    if port and port != default_port:
        netloc = f"{hostname}:{port}"

    path = parsed.path or "/"
    return urlunparse((
        scheme,
        netloc,
        path,
        parsed.params,
        parsed.query,
        parsed.fragment,
    ))


def tab_url_matches(target_url: str, actual_url: str) -> bool:
    """Whether two tab URLs match after strict normalization."""
    target = normalize_exact_tab_url(target_url)
    actual = normalize_exact_tab_url(actual_url)
    return bool(target and actual and target == actual)


def encode_tab_url_route_token(value: str) -> str:
    """Encode a normalized tab URL into a short stable route token."""
    normalized = normalize_exact_tab_url(value)
    if not normalized:
        return ""

    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return digest[:12]


def decode_tab_url_route_token(token: str) -> str:
    """Short route tokens are one-way hashes; keep API shape for compatibility."""
    return str(token or "").strip().lower()


__all__ = [
    "build_route_domain_aliases",
    "decode_tab_url_route_token",
    "encode_tab_url_route_token",
    "extract_remote_site_domain",
    "get_canonical_route_domain",
    "get_preferred_route_domain",
    "is_remote_site_url",
    "normalize_route_domain",
    "normalize_exact_tab_url",
    "route_domain_matches",
    "tab_url_matches",
]
