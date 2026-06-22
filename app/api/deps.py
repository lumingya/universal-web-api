"""
app/api/deps.py - API 共享依赖
"""

from typing import Iterable, Optional

from fastapi import Header, HTTPException

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

    if expected not in normalized:
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


async def verify_service_auth(authorization: Optional[str] = Header(None)) -> bool:
    """验证对外服务 API 的 Bearer Token。"""
    verify_service_token(authorization=authorization)
    return True


async def verify_dashboard_auth(authorization: Optional[str] = Header(None)) -> bool:
    """验证控制面板管理接口的访问密钥。"""
    verify_dashboard_token(authorization=authorization)
    return True


async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    """向后兼容：默认用于控制面板管理接口。"""
    return await verify_dashboard_auth(authorization)
