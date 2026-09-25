"""
app/api/deps.py - API 共享依赖
"""

import hmac
import ipaddress
from typing import Iterable, Optional
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request

from app.core.config import AppConfig


def extract_authorization_token(authorization: Optional[str]) -> str:
    """Extract a token from Authorization, accepting raw tokens and Bearer schemes."""
    raw = str(authorization or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def _auth_candidates(
    authorization: Optional[str],
    x_api_key: Optional[str] = None,
) -> list[str]:
    candidates: list[str] = []
    if isinstance(authorization, str) and authorization.strip():
        raw = authorization.strip()
        candidates.append(raw)
        extracted = extract_authorization_token(raw)
        if extracted and extracted != raw:
            candidates.append(extracted)
    if isinstance(x_api_key, str) and x_api_key.strip():
        candidates.append(x_api_key.strip())
    return candidates


def _verify_token_candidates(
    *,
    enabled: bool,
    token_value: str,
    candidates: Iterable[str],
) -> None:
    if not enabled:
        return

    expected = str(token_value or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="服务配置错误")

    normalized = [str(item or "").strip() for item in candidates if str(item or "").strip()]
    if not normalized:
        raise HTTPException(
            status_code=401,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 修复6: str 版 compare_digest 遇到非 ASCII 令牌会抛 TypeError 导致 500，统一按 UTF-8 编码后比较
    expected_bytes = expected.encode("utf-8")
    if not any(
        hmac.compare_digest(expected_bytes, candidate.encode("utf-8"))
        for candidate in normalized
    ):
        raise HTTPException(
            status_code=401,
            detail="认证令牌无效",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_service_token(
    authorization: Optional[str] = None,
    x_api_key: Optional[str] = None,
) -> None:
    _verify_token_candidates(
        enabled=AppConfig.is_auth_enabled(),
        token_value=AppConfig.get_auth_token(),
        candidates=_auth_candidates(authorization, x_api_key),
    )


def verify_dashboard_token(authorization: Optional[str] = None) -> None:
    _verify_token_candidates(
        enabled=AppConfig.is_dashboard_auth_enabled(),
        token_value=AppConfig.get_dashboard_auth_token(),
        candidates=_auth_candidates(authorization),
    )


async def verify_service_auth(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> bool:
    """验证对外服务 API 的 Bearer Token 或 X-API-Key。"""
    verify_service_token(authorization=authorization, x_api_key=x_api_key)
    return True


async def verify_dashboard_auth(authorization: Optional[str] = Header(None)) -> bool:
    """验证控制面板管理接口的访问密钥。"""
    verify_dashboard_token(authorization=authorization)
    return True


async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    """向后兼容：默认用于控制面板管理接口。"""
    return await verify_dashboard_auth(authorization)


# ==================== 敏感管理接口的强制鉴权（修复 S13 / S1 / S4）====================

def client_is_loopback(request: Optional[Request]) -> bool:
    """请求是否来自本机回环地址。

    注意：这**不是**身份凭证。本机上的任意进程、以及配置不当的反向代理/隧道
    都可能让外来请求看起来来自回环（见 S4）。它只用于在「未配置任何管理令牌」
    的单机默认部署下，保留开箱即用体验，同时挡住所有远程访问。
    """
    if request is None:
        return False
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    if not host:
        return False
    candidate = str(host).strip().strip("[]")
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _request_origin_is_same(request: Optional[Request]) -> bool:
    """跨源判定：没有 Origin 视为同源（非浏览器客户端），有则必须与 Host 完全一致。"""
    if request is None:
        return True
    origin = (request.headers.get("origin") or "").strip()
    if not origin or origin.lower() == "null":
        return not origin
    allowed = AppConfig.get_cors_origins() if AppConfig.is_cors_enabled() else []
    if origin in allowed:
        return True
    parsed = urlsplit(origin)
    origin_host = (parsed.netloc or "").lower()
    request_host = (request.headers.get("host") or "").lower()
    return bool(origin_host) and origin_host == request_host


def verify_admin_access(
    request: Optional[Request] = None,
    authorization: Optional[str] = None,
) -> bool:
    """敏感管理接口的访问校验，**不受 DASHBOARD_AUTH_ENABLED 默认关闭的影响**。

    修复 S13：`GET /api/settings/backup` 这类会原样吐出 `.env` 的接口，
    过去完全依赖默认关闭的全局开关，等于对任何能连上端口的人开放。

    规则（按顺序）：
    1. 浏览器跨源请求一律拒绝（403），避免恶意页面借用户身份读取管理数据；
    2. 已配置管理令牌 → 必须提供正确令牌（沿用 `verify_dashboard_token` 的比较逻辑）；
    3. 未配置任何令牌 → 只允许本机回环访问，远程访问返回 401 并提示配置令牌。

    返回 True 表示「请求者出示了真实令牌」，False 表示「靠本机回环放行」。
    调用方可据此决定是否输出最敏感的内容（例如备份里的明文密钥）。
    """
    if not _request_origin_is_same(request):
        raise HTTPException(status_code=403, detail="跨源访问管理接口已被拒绝")

    expected = AppConfig.get_dashboard_auth_token()
    if expected:
        _verify_token_candidates(
            enabled=True,
            token_value=expected,
            candidates=_auth_candidates(authorization),
        )
        return True

    if AppConfig.is_dashboard_auth_enabled():
        # 开了认证却没有令牌：这是配置错误，绝不能当成「放行」
        raise HTTPException(status_code=500, detail="服务配置错误")

    if client_is_loopback(request):
        return False

    raise HTTPException(
        status_code=401,
        detail=(
            "该接口仅允许本机访问。如需远程管理，请设置 DASHBOARD_AUTH_ENABLED=true "
            "与 DASHBOARD_AUTH_TOKEN 后使用 Bearer 令牌访问。"
        ),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def verify_admin_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> bool:
    """FastAPI 依赖版本，用于敏感管理接口。"""
    return verify_admin_access(request=request, authorization=authorization)
