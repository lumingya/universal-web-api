"""Security helpers for fetching user-controlled remote resources."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests


ALLOWED_REMOTE_SCHEMES = frozenset({"http", "https"})
MAX_REMOTE_REDIRECTS = 4
_PROXY_FAKE_IP_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)
_PROXY_FAKE_IP_HTTPS_SUFFIXES = (
    "r2.cloudflarestorage.com",
    "contribution.usercontent.google.com",
    "raw.githubusercontent.com",
)


class UnsafeRemoteResourceError(ValueError):
    """Raised when a remote URL can reach a non-public network address."""


def normalize_remote_http_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except Exception:
        return ""
    scheme = str(parsed.scheme or "").lower()
    hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    if scheme not in ALLOWED_REMOTE_SCHEMES or not hostname:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host_for_netloc}:{port}" if port is not None else host_for_netloc
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def remote_url_origin(value: Any) -> str:
    normalized = normalize_remote_http_url(value)
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    default_port = 80 if parsed.scheme == "http" else 443
    port = parsed.port or default_port
    host = str(parsed.hostname or "").lower().rstrip(".")
    host_for_netloc = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{host_for_netloc}:{port}"


def is_same_remote_origin(left: Any, right: Any) -> bool:
    left_origin = remote_url_origin(left)
    return bool(left_origin and left_origin == remote_url_origin(right))


def _is_public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(address or "").split("%", 1)[0])
    except ValueError:
        return False
    return bool(ip.is_global)


def _resolve_addresses(hostname: str) -> tuple[str, ...]:
    try:
        results = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeRemoteResourceError(f"remote_dns_failed:{hostname}") from exc

    addresses = tuple(sorted({str(item[4][0]) for item in results if item and item[4]}))
    if not addresses:
        raise UnsafeRemoteResourceError(f"remote_dns_empty:{hostname}")
    return addresses


def _is_proxy_fake_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(address or "").split("%", 1)[0])
    except ValueError:
        return False
    return any(ip in network for network in _PROXY_FAKE_IP_NETWORKS)


def _allows_proxy_fake_ip(hostname: str, scheme: str, addresses: Iterable[str]) -> bool:
    host = str(hostname or "").strip().lower().rstrip(".")
    if scheme != "https" or not any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in _PROXY_FAKE_IP_HTTPS_SUFFIXES
    ):
        return False

    resolved = tuple(addresses)
    return bool(resolved) and all(
        _is_public_ip(address) or _is_proxy_fake_ip(address)
        for address in resolved
    )


def resolve_public_addresses(hostname: str) -> tuple[str, ...]:
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeRemoteResourceError("remote_host_missing")

    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise UnsafeRemoteResourceError(f"remote_address_not_public:{literal}")
        return (str(literal),)

    addresses = _resolve_addresses(host)
    for address in addresses:
        if not _is_public_ip(address):
            raise UnsafeRemoteResourceError(f"remote_address_not_public:{address}")
    return addresses


def validate_public_remote_url(value: Any) -> str:
    normalized = normalize_remote_http_url(value)
    if not normalized:
        raise UnsafeRemoteResourceError("remote_url_invalid")
    parsed = urlsplit(normalized)
    resolve_public_addresses(str(parsed.hostname or ""))
    return normalized


def _validate_fetch_remote_url(value: Any) -> str:
    """Validate a fetch target, accounting for HTTPS CDN names behind TUN fake DNS."""
    normalized = normalize_remote_http_url(value)
    if not normalized:
        raise UnsafeRemoteResourceError("remote_url_invalid")

    parsed = urlsplit(normalized)
    host = str(parsed.hostname or "")
    try:
        resolve_public_addresses(host)
        return normalized
    except UnsafeRemoteResourceError:
        addresses = _resolve_addresses(host)
        if _allows_proxy_fake_ip(host, parsed.scheme, addresses):
            return normalized
        blocked = next(
            (address for address in addresses if not _is_public_ip(address)),
            addresses[0],
        )
        raise UnsafeRemoteResourceError(f"remote_address_not_public:{blocked}")


def _headers_for_target(
    headers: Optional[Dict[str, str]],
    *,
    target_url: str,
    credential_origin: str,
) -> Dict[str, str]:
    result = dict(headers or {})
    if not credential_origin or remote_url_origin(target_url) != credential_origin:
        result.pop("Authorization", None)
        result.pop("authorization", None)
        result.pop("Cookie", None)
        result.pop("cookie", None)
        result.pop("Referer", None)
        result.pop("referer", None)
    return result


def get_public_remote_resource(
    url: Any,
    *,
    headers: Optional[Dict[str, str]] = None,
    cookies: Any = None,
    credential_origin_url: Any = None,
    timeout: Any = (8, 20),
    stream: bool = True,
    max_redirects: int = MAX_REMOTE_REDIRECTS,
):
    """GET a public URL, validating every redirect and scoping credentials."""
    current = _validate_fetch_remote_url(url)
    credential_origin = remote_url_origin(credential_origin_url)
    redirects_left = max(0, int(max_redirects))

    while True:
        same_origin = bool(credential_origin and remote_url_origin(current) == credential_origin)
        response = requests.get(
            current,
            headers=_headers_for_target(
                headers,
                target_url=current,
                credential_origin=credential_origin,
            ),
            cookies=cookies if same_origin else None,
            timeout=timeout,
            allow_redirects=False,
            stream=stream,
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response

        location = str(response.headers.get("Location") or "").strip()
        response.close()
        if not location:
            raise UnsafeRemoteResourceError("remote_redirect_missing_location")
        if redirects_left <= 0:
            raise UnsafeRemoteResourceError("remote_redirect_limit")
        redirects_left -= 1
        current = _validate_fetch_remote_url(urljoin(current, location))


__all__ = [
    "UnsafeRemoteResourceError",
    "get_public_remote_resource",
    "is_same_remote_origin",
    "normalize_remote_http_url",
    "remote_url_origin",
    "resolve_public_addresses",
    "validate_public_remote_url",
]
