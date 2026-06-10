"""
app/api/deps.py - API 共享依赖
"""

from typing import Optional

from fastapi import Header, HTTPException

from app.core.config import AppConfig


def extract_authorization_token(authorization: Optional[str]) -> str:
    """Extract a token from Authorization, accepting raw tokens and Bearer schemes."""
    raw = str(authorization or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    """验证 Bearer Token。"""
    if not AppConfig.is_auth_enabled():
        return True

    token_value = AppConfig.get_auth_token()
    if not token_value:
        raise HTTPException(status_code=500, detail="服务配置错误")

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = extract_authorization_token(authorization)
    if token != token_value:
        raise HTTPException(
            status_code=401,
            detail="认证令牌无效",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True
