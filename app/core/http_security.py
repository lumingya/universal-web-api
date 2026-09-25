"""
app/core/http_security.py - 控制面 HTTP 安全边界（审查条目 S1 / S13 / H1）

集中放置与部署安全相关的纯逻辑，便于单元测试：

- ``is_loopback_host``：判断客户端/监听地址是否为回环；
- ``origin_is_allowed``：跨源请求（CSRF / 跨站读取）来源判定；
- ``startup_security_errors``：公开监听但未启用认证、认证开关写成非布尔值
  等不安全配置在启动期直接报错，而不是静默回退为「关闭认证」；
- ``is_secret_env_key`` / ``redact_env_for_backup``：备份导出时剔除秘密。
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

TRUE_VALUES = ("true", "1", "yes", "on")
FALSE_VALUES = ("false", "0", "no", "off")

# 显式放行「公开监听 + 无认证」的逃生开关（仅限完全可信的隔离网络）。
INSECURE_OVERRIDE_ENV = "ALLOW_INSECURE_PUBLIC_BIND"

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def is_loopback_host(host: Optional[str]) -> bool:
    """回环地址判定（127.0.0.0/8、::1、IPv4 映射的 ::ffff:127.x、localhost）。"""
    raw = str(host or "").strip().lower()
    if not raw:
        return False
    if raw.startswith("[") and "]" in raw:
        raw = raw[1:raw.index("]")]
    if raw in _LOOPBACK_NAMES:
        return True
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return bool(addr.is_loopback)


def is_public_bind_host(host: Optional[str]) -> bool:
    """监听地址是否会暴露到回环以外（0.0.0.0 / :: / 具体网卡地址 / 主机名）。"""
    raw = str(host or "").strip()
    if not raw:
        # AppConfig.get_host() 默认 127.0.0.1；空串交给 uvicorn 时等价于默认回环。
        return False
    return not is_loopback_host(raw)


def _normalize_origin(value: str) -> Optional[Tuple[str, str]]:
    """返回 (scheme, host[:port]) 小写形式；默认端口剥离，无法解析返回 None。"""
    try:
        parts = urlsplit(str(value or "").strip())
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        return None
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return scheme, host


def _normalize_host_header(value: str, scheme: str) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    normalized = _normalize_origin(f"{scheme}://{raw}")
    return normalized[1] if normalized else None


def origin_is_allowed(
    origin: Optional[str],
    host_header: Optional[str],
    allowed_origins: Iterable[str] = (),
    forwarded_host: Optional[str] = None,
) -> bool:
    """判断带 ``Origin`` 头的请求是否可被接受。

    - 无 ``Origin`` 头：非浏览器客户端或同源导航，放行（交给认证层）；
    - 与请求 Host（或可信反代给出的 X-Forwarded-Host）同源：放行；
    - 命中显式配置的 ``CORS_ORIGINS``（含用户显式配置的 ``*``）：放行；
    - 其它（含 ``Origin: null``）：拒绝。
    """
    if origin is None:
        return True
    raw_origin = str(origin).strip()
    if raw_origin == "":
        return True

    allowed = [str(item or "").strip() for item in (allowed_origins or []) if str(item or "").strip()]
    if "*" in allowed:
        return True

    parsed = _normalize_origin(raw_origin)
    if parsed is None:
        # "null"（沙箱 iframe / file://）或畸形值
        return False
    scheme, origin_host = parsed

    for candidate in (host_header, forwarded_host):
        if not candidate:
            continue
        # X-Forwarded-Host 可能是逗号分隔的链，取第一个（最靠近客户端）
        first = str(candidate).split(",")[0].strip()
        if _normalize_host_header(first, scheme) == origin_host:
            return True

    for item in allowed:
        normalized = _normalize_origin(item)
        if normalized is not None and normalized == parsed:
            return True
    return False


def parse_cors_origins(raw: Optional[str]) -> List[str]:
    """``CORS_ORIGINS`` 解析：空 = 不允许任何跨源（同源控制面板不需要 CORS）。"""
    value = str(raw or "").strip()
    if not value:
        return []
    if value == "*":
        return ["*"]
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


def _bool_value_error(name: str, env: Mapping[str, str]) -> Optional[str]:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    lowered = str(raw).strip().lower()
    if lowered in TRUE_VALUES or lowered in FALSE_VALUES:
        return None
    return (
        f"{name}={raw!r} 不是合法布尔值（应为 true/false）。"
        f"此前该值会被静默当作 false 从而关闭认证，现拒绝启动。"
    )


def _env_true(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name) or "").strip().lower() in TRUE_VALUES


def startup_security_errors(
    host: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> List[str]:
    """返回会导致拒绝启动的安全配置错误列表（空列表 = 通过）。"""
    env = os.environ if env is None else env
    errors: List[str] = []

    for name in ("AUTH_ENABLED", "DASHBOARD_AUTH_ENABLED"):
        message = _bool_value_error(name, env)
        if message:
            errors.append(message)

    bind_host = host if host is not None else env.get("APP_HOST", "127.0.0.1")
    if not is_public_bind_host(bind_host):
        return errors
    if _env_true(env, INSECURE_OVERRIDE_ENV):
        return errors

    service_enabled = _env_true(env, "AUTH_ENABLED")
    service_token = str(env.get("AUTH_TOKEN") or "").strip()
    raw_dash = env.get("DASHBOARD_AUTH_ENABLED")
    if raw_dash is None or str(raw_dash).strip() == "":
        dash_enabled = service_enabled
    else:
        dash_enabled = _env_true(env, "DASHBOARD_AUTH_ENABLED")
    dash_token = str(env.get("DASHBOARD_AUTH_TOKEN") or "").strip() or service_token

    if not (dash_enabled and dash_token):
        errors.append(
            f"APP_HOST={bind_host} 会把控制面板暴露到本机以外，但控制面板认证未启用或未设置令牌。"
            "请设置 DASHBOARD_AUTH_ENABLED=true 与 DASHBOARD_AUTH_TOKEN（或 AUTH_ENABLED=true 与 AUTH_TOKEN），"
            f"或改回 APP_HOST=127.0.0.1。确认处于完全可信的隔离网络时可设 {INSECURE_OVERRIDE_ENV}=true 跳过此检查。"
        )
    if not (service_enabled and service_token):
        errors.append(
            f"APP_HOST={bind_host} 会把 /v1 API 暴露到本机以外，但 API 认证未启用或未设置令牌。"
            "请设置 AUTH_ENABLED=true 与 AUTH_TOKEN，"
            f"或改回 APP_HOST=127.0.0.1（可信隔离网络可设 {INSECURE_OVERRIDE_ENV}=true）。"
        )
    return errors


# ---------------------------------------------------------------------------
# 备份脱敏（S13）
# ---------------------------------------------------------------------------

_SECRET_KEY_PATTERN = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|"
    r"COOKIE|CREDENTIAL|AUTH_USER|SESSION)",
    re.IGNORECASE,
)
_URL_USERINFO_PATTERN = re.compile(r"^[a-z][a-z0-9+.\-]*://[^/@\s]+@", re.IGNORECASE)


def is_secret_env_key(key: str) -> bool:
    return bool(_SECRET_KEY_PATTERN.search(str(key or "")))


def value_contains_credentials(value: Any) -> bool:
    """形如 scheme://user:pass@host 的值（代理地址等）视为含凭据。"""
    if not isinstance(value, str):
        return False
    return any(_URL_USERINFO_PATTERN.match(part.strip()) for part in value.split(",") if part.strip())


def redact_env_for_backup(env: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """剔除秘密键/含凭据的值，返回 (安全子集, 被剔除的键名列表)。

    剔除而不是替换为占位符：导入时缺失的键会原样保留目标机器 ``.env`` 里的现值，
    避免把占位符写回去覆盖真实令牌。
    """
    safe: Dict[str, Any] = {}
    removed: List[str] = []
    for key, value in (env or {}).items():
        if is_secret_env_key(key) or value_contains_credentials(value):
            # 空值不算泄露，但为了语义一致也一并剔除（导入时保持目标现值）
            removed.append(str(key))
            continue
        safe[key] = value
    return safe, sorted(removed)


# ---------------------------------------------------------------------------
# 「本机请求」判定（S13 回退路径 / S4）
# ---------------------------------------------------------------------------

# 反向代理 / 隧道（ngrok、cloudflared、nginx 等）常见的转发头。回环连接上出现这些头，
# 说明真实客户端在别处，不能再把「连接来自 127.0.0.1」当作授权依据。
FORWARDING_HEADERS = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-real-ip",
    "cf-connecting-ip",
    "true-client-ip",
    "x-client-ip",
)

# start.py 的定时重启 handoff 代理会用这两个头把真实客户端地址传给子进程；
# 共享密钥每次启动随机生成并通过环境变量传递，外部请求无法伪造。
PROXY_CLIENT_HEADER = "x-uwa-client-addr"
PROXY_SECRET_HEADER = "x-uwa-proxy-secret"
PROXY_SECRET_ENV = "UWAPI_PROXY_SECRET"


def _header(headers: Any, name: str) -> Optional[str]:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except Exception:
        value = None
    if value is None and isinstance(headers, Mapping):
        for key, item in headers.items():
            if str(key).lower() == name:
                value = item
                break
    return None if value is None else str(value)


def effective_client_host(
    client_host: Optional[str],
    headers: Any = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """返回真实客户端地址：只有来自受信 handoff 代理（回环 + 密钥匹配）时才采信转发头。"""
    env = os.environ if env is None else env
    secret = str(env.get(PROXY_SECRET_ENV) or "").strip()
    if secret and is_loopback_host(client_host):
        supplied = _header(headers, PROXY_SECRET_HEADER)
        if supplied is not None:
            import hmac

            if hmac.compare_digest(supplied.strip().encode("utf-8"), secret.encode("utf-8")):
                forwarded = (_header(headers, PROXY_CLIENT_HEADER) or "").strip()
                return forwarded or None
            return None  # 伪造/错误密钥：不采信也不当作本机
    return client_host


def is_trusted_local_request(
    client_host: Optional[str],
    headers: Any = None,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """连接来自本机回环，且没有任何迹象表明它是被代理/隧道转发进来的。"""
    env = os.environ if env is None else env
    real = effective_client_host(client_host, headers, env)
    if not is_loopback_host(real):
        return False
    # 受信 handoff 代理已给出真实地址时，它自己会剥离外部转发头；
    # 其余情况下，出现任何转发头都视为经反代/隧道进入。
    trusted_proxy = real != client_host
    if not trusted_proxy:
        for name in FORWARDING_HEADERS:
            if _header(headers, name):
                return False
    return True
