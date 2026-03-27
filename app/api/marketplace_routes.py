"""
app/api/marketplace_routes.py - 配置市场 API
"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import verify_auth
from app.core.config import get_logger
from app.services.marketplace_service import marketplace_service

logger = get_logger("API.MARKETPLACE")

router = APIRouter(tags=["marketplace"])


class MarketplaceSubmissionRequest(BaseModel):
    item_type: Literal["site_config", "command_bundle"] = Field(default="site_config")
    title: str = Field(..., max_length=120)
    summary: str = Field(..., max_length=400)
    author: str = Field(default="本地投稿", max_length=80)
    category: str = Field(default="", max_length=120)
    site_domain: Optional[str] = Field(default=None, max_length=200)
    preset_name: str = Field(default="主预设", max_length=120)
    version: str = Field(default="1.0.0", max_length=40)
    compatibility: str = Field(default="", max_length=80)
    tags: List[str] = Field(default_factory=list)
    site_config: Optional[Dict[str, Any]] = None
    command_bundle: Optional[Dict[str, Any]] = None


@router.get("/api/marketplace")
async def get_marketplace_catalog(
    refresh: bool = Query(False),
    authenticated: bool = Depends(verify_auth),
):
    try:
        return marketplace_service.list_catalog(force_refresh=refresh)
    except Exception as exc:
        logger.error(f"获取插件市场失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/marketplace/items/{item_id}")
async def get_marketplace_item(
    item_id: str,
    refresh: bool = Query(False),
    authenticated: bool = Depends(verify_auth),
):
    try:
        return marketplace_service.get_item(item_id, force_refresh=refresh)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error(f"获取市场项目失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/marketplace/items")
async def submit_marketplace_item(
    body: MarketplaceSubmissionRequest,
    authenticated: bool = Depends(verify_auth),
):
    try:
        result = marketplace_service.submit_item(body.model_dump())
        return {
            "success": True,
            **result,
        }
    except Exception as exc:
        logger.error(f"提交市场项目失败: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
