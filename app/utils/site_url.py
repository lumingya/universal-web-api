"""
site_url.py - URL helpers for identifying real remote sites.
"""

from __future__ import annotations

import ipaddress
from typing import Optional
from urllib.parse import urlparse


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


__all__ = ["extract_remote_site_domain", "is_remote_site_url"]
