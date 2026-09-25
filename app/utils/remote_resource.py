"""Security helpers for fetching user-controlled remote resources."""

from __future__ import annotations

import contextlib
import ipaddress
import socket
import threading
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests


ALLOWED_REMOTE_SCHEMES = frozenset({"http", "https"})
MAX_REMOTE_REDIRECTS = 4
_PROXY_FAKE_IP_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)
_PROXY_FAKE_IP_HTTPS_SUFFIXES = (
    # Google & Gemini CDN / Media
    "googleusercontent.com",
    "usercontent.google.com",
    "gstatic.com",
    "googleapis.com",
    "google.com",
    # Cloudflare R2 & CDN
    "r2.cloudflarestorage.com",
    "cloudflare.com",
    "cloudflareinsights.com",
    # GitHub CDN
    "githubusercontent.com",
    "raw.githubusercontent.com",
    "github.com",
    # OpenAI CDN
    "oaistatic.com",
    "oaiusercontent.com",
    "openai.com",
    # Anthropic & Claude CDN
    "anthropic.com",
    "claude.ai",
    # Microsoft & Bing CDN
    "bing.com",
    "msn.com",
    "microsoft.com",
    "azureedge.net",
    "windows.net",
    # Other mainstream AI & media hosting CDNs
    "deepseek.com",
    "grok.com",
    "x.ai",
    "together.ai",
    "together.xyz",
    "replicate.delivery",
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


def _validate_fetch_target(value: Any) -> Tuple[str, Tuple[str, ...]]:
    """Validate a fetch target and return (normalized_url, checked_addresses)."""
    normalized = normalize_remote_http_url(value)
    if not normalized:
        raise UnsafeRemoteResourceError("remote_url_invalid")

    parsed = urlsplit(normalized)
    host = str(parsed.hostname or "")
    try:
        return normalized, resolve_public_addresses(host)
    except UnsafeRemoteResourceError:
        addresses = _resolve_addresses(host)
        if _allows_proxy_fake_ip(host, parsed.scheme, addresses):
            return normalized, tuple(addresses)
        blocked = next(
            (address for address in addresses if not _is_public_ip(address)),
            addresses[0],
        )
        raise UnsafeRemoteResourceError(f"remote_address_not_public:{blocked}")


def _validate_fetch_remote_url(value: Any) -> str:
    """Validate a fetch target, accounting for HTTPS CDN names behind TUN fake DNS."""
    return _validate_fetch_target(value)[0]


# ---------------------------------------------------------------------------
# S11：DNS 固定（pinning）
#
# 校验阶段解析出的地址必须就是实际连接的地址，否则攻击者控制的 DNS 可以在「校验」与
# 「连接」之间把答案换成内网地址（DNS rebinding）。做法：在 urllib3 建立 TCP 连接的
# 唯一入口 `urllib3.util.connection.create_connection` 外面包一层，只在
# get_public_remote_resource 调用期间、且只对本线程登记过的主机名生效——连接直接打到
# 已校验的 IP，而 HTTPConnection.host 保持原主机名，所以 TLS SNI / 证书校验 / Host 头都不变。
# 未登记的主机名（包括走 HTTP(S)_PROXY 时的代理主机）原样放行：代理场景由可信出网代理解析目标。
# ---------------------------------------------------------------------------

_pin_state = threading.local()
_pin_install_lock = threading.Lock()
_pin_installed = False


def _current_pins() -> Dict[str, Tuple[str, ...]]:
    pins = getattr(_pin_state, "pins", None)
    if pins is None:
        pins = {}
        _pin_state.pins = pins
    return pins


def _install_pinned_create_connection() -> None:
    global _pin_installed
    if _pin_installed:
        return
    with _pin_install_lock:
        if _pin_installed:
            return
        try:
            from urllib3.util import connection as urllib3_connection
        except Exception:  # pragma: no cover - urllib3 is a hard dependency of requests
            return
        original = urllib3_connection.create_connection

        def pinned_create_connection(address, *args, **kwargs):
            host, port = address[0], address[1]
            pinned = _current_pins().get(str(host or "").strip().lower().rstrip("."))
            if not pinned:
                return original(address, *args, **kwargs)
            last_error: Optional[BaseException] = None
            for ip in pinned:
                try:
                    return original((ip, port), *args, **kwargs)
                except OSError as exc:
                    last_error = exc
            raise last_error or OSError(f"remote_pinned_connect_failed:{host}")

        pinned_create_connection.__uwapi_pinned__ = True  # type: ignore[attr-defined]
        urllib3_connection.create_connection = pinned_create_connection
        _pin_installed = True


@contextlib.contextmanager
def _pinned_dns(url: str, addresses: Iterable[str]) -> Iterator[None]:
    host = str(urlsplit(url).hostname or "").strip().lower().rstrip(".")
    resolved = tuple(str(item) for item in addresses if str(item or "").strip())
    if not host or not resolved:
        yield
        return
    _install_pinned_create_connection()
    pins = _current_pins()
    previous = pins.get(host)
    pins[host] = resolved
    try:
        yield
    finally:
        if previous is None:
            pins.pop(host, None)
        else:
            pins[host] = previous


def _headers_for_target(
    headers: Optional[Dict[str, str]],
    *,
    target_url: str,
    credential_origin: str,
) -> Dict[str, str]:
    result = dict(headers or {})
    if not credential_origin or remote_url_origin(target_url) != credential_origin:
        sensitive = {"authorization", "cookie", "referer"}
        result = {k: v for k, v in result.items() if str(k).strip().lower() not in sensitive}
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
    current, addresses = _validate_fetch_target(url)
    credential_origin = remote_url_origin(credential_origin_url)
    redirects_left = max(0, int(max_redirects))

    while True:
        same_origin = bool(credential_origin and remote_url_origin(current) == credential_origin)
        # S11：连接固定到刚校验过的地址（连接在 requests.get 返回前已建立，stream 读取复用同一 socket）
        with _pinned_dns(current, addresses):
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
        current, addresses = _validate_fetch_target(urljoin(current, location))


__all__ = [
    "UnsafeRemoteResourceError",
    "get_public_remote_resource",
    "is_same_remote_origin",
    "normalize_remote_http_url",
    "remote_url_origin",
    "resolve_public_addresses",
    "validate_public_remote_url",
]
