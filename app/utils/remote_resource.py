"""Security helpers for fetching user-controlled remote resources."""

from __future__ import annotations

import contextlib
import ipaddress
import socket
import threading
from typing import Any, Dict, Iterable, Optional
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


#: 单次抓取允许读取的最大字节数（修复 S10：原先没有任何下载体积上限）
DEFAULT_REMOTE_FETCH_MAX_BYTES = 32 * 1024 * 1024


class UnsafeRemoteResourceError(ValueError):
    """Raised when a remote URL can reach a non-public network address."""


class RemoteResourceTooLargeError(UnsafeRemoteResourceError):
    """Raised when a remote response exceeds the configured byte budget."""


# ---------------------------------------------------------------- DNS 固定（修复 S11）
#
# 校验和连接是两次独立的名字解析：我们先 resolve 出地址并确认都是公网地址，
# 但随后交给 requests 的仍然是**主机名**，HTTP 栈会重新解析一次。
# 攻击者只要控制该域名的 DNS（TTL=0 的重绑定），第二次解析就可以指向
# 127.0.0.1 / 169.254.169.254 等内网地址，前面的检查形同虚设。
#
# 这里把「已校验的地址」固定下来：连接时只允许用这些 IP，
# 同时保留主机名用于 SNI / Host 头（所以不能简单地把 URL 里的域名换成 IP）。
_dns_pin_state = threading.local()
_original_getaddrinfo = socket.getaddrinfo


def _pinned_getaddrinfo(host, port, *args, **kwargs):
    pins = getattr(_dns_pin_state, "pins", None)
    if pins:
        key = str(host or "").strip().lower().rstrip(".")
        addresses = pins.get(key)
        if addresses:
            results = []
            for address in addresses:
                try:
                    parsed = ipaddress.ip_address(str(address).split("%", 1)[0])
                except ValueError:
                    continue
                if parsed.version == 6:
                    results.append(
                        (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                         (str(parsed), port, 0, 0))
                    )
                else:
                    results.append(
                        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                         (str(parsed), port))
                    )
            if results:
                return results
    return _original_getaddrinfo(host, port, *args, **kwargs)


# 全局安装一次。没有设置 pin 的线程会原样走回系统解析，因此对其余代码无影响。
if socket.getaddrinfo is not _pinned_getaddrinfo:
    socket.getaddrinfo = _pinned_getaddrinfo


@contextlib.contextmanager
def pinned_dns(hostname: str, addresses: Iterable[str]):
    """在当前线程内把 hostname 固定解析到 addresses。

    线程局部存储：不会影响其他并发请求，也不会改变其他线程的解析行为。
    """
    host = str(hostname or "").strip().lower().rstrip(".")
    resolved = tuple(str(item) for item in addresses if str(item or "").strip())
    if not host or not resolved:
        yield
        return

    pins = getattr(_dns_pin_state, "pins", None)
    if pins is None:
        pins = {}
        _dns_pin_state.pins = pins
    previous = pins.get(host)
    pins[host] = resolved
    try:
        yield
    finally:
        if previous is None:
            pins.pop(host, None)
        else:
            pins[host] = previous


def read_remote_response_bytes(
    response: Any,
    max_bytes: int = DEFAULT_REMOTE_FETCH_MAX_BYTES,
    chunk_size: int = 64 * 1024,
) -> bytes:
    """按字节预算流式读取响应体（修复 S10）。

    先看 Content-Length 提前拒绝，再在读取过程中兜底，
    防止上游谎报长度或使用 chunked 编码绕过检查。
    """
    limit = max(1, int(max_bytes))
    declared = str((getattr(response, "headers", None) or {}).get("Content-Length") or "").strip()
    if declared.isdigit() and int(declared) > limit:
        raise RemoteResourceTooLargeError(f"remote_resource_too_large:{declared}")

    buffer = bytearray()
    for chunk in response.iter_content(chunk_size=chunk_size):
        if not chunk:
            continue
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise RemoteResourceTooLargeError(f"remote_resource_too_large:{len(buffer)}")
    return bytes(buffer)


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
    return _validate_fetch_remote_target(value)[0]


def _validate_fetch_remote_target(value: Any) -> tuple[str, str, tuple[str, ...]]:
    """校验抓取目标，并返回 (规范化 URL, 主机名, 已通过校验的地址)。

    修复 S11：把校验时解析到的地址一并带出来，供连接阶段固定使用。
    """
    normalized = normalize_remote_http_url(value)
    if not normalized:
        raise UnsafeRemoteResourceError("remote_url_invalid")

    parsed = urlsplit(normalized)
    host = str(parsed.hostname or "")
    try:
        return normalized, host, resolve_public_addresses(host)
    except UnsafeRemoteResourceError:
        addresses = _resolve_addresses(host)
        if _allows_proxy_fake_ip(host, parsed.scheme, addresses):
            return normalized, host, tuple(addresses)
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
    """GET a public URL, validating every redirect and scoping credentials.

    修复 S11：连接阶段固定使用校验时解析到的 IP，消除「校验后重新解析」的窗口。
    主机名保留在 URL 里，因此 SNI 与 Host 头仍然正确。
    """
    current, current_host, current_addresses = _validate_fetch_remote_target(url)
    credential_origin = remote_url_origin(credential_origin_url)
    redirects_left = max(0, int(max_redirects))

    while True:
        same_origin = bool(credential_origin and remote_url_origin(current) == credential_origin)
        with pinned_dns(current_host, current_addresses):
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
        current, current_host, current_addresses = _validate_fetch_remote_target(
            urljoin(current, location)
        )


__all__ = [
    "DEFAULT_REMOTE_FETCH_MAX_BYTES",
    "RemoteResourceTooLargeError",
    "UnsafeRemoteResourceError",
    "pinned_dns",
    "read_remote_response_bytes",
    "get_public_remote_resource",
    "is_same_remote_origin",
    "normalize_remote_http_url",
    "remote_url_origin",
    "resolve_public_addresses",
    "validate_public_remote_url",
]
