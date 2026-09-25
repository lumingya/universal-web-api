"""R1-3：站点适配器在线更新接口。"""

from __future__ import annotations

from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import verify_auth
from app.core.config import get_logger
from app.utils.remote_resource import UnsafeRemoteResourceError

logger = get_logger("API.ADAPTER")
router = APIRouter()


class ApplyAdapterUpdatesRequest(BaseModel):
    sites: Optional[List[str]] = Field(default=None, description="要更新的站点；为空时应用全部安全更新")
    include_conflicts: bool = Field(default=False, description="是否同时处理本地改过的站点（按本地优先合并）")
    branch: str = Field(default="main", max_length=100)


def _run(action):
    try:
        return action()
    except (requests.RequestException, UnsafeRemoteResourceError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"无法获取官方适配器清单：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/adapters/updates")
async def check_adapter_updates(branch: str = "main", authenticated: bool = Depends(verify_auth)):
    from app.services.adapter_updates import default_updater

    return _run(lambda: default_updater(branch).check())


@router.post("/api/adapters/updates/apply")
async def apply_adapter_updates(body: ApplyAdapterUpdatesRequest, authenticated: bool = Depends(verify_auth)):
    from app.services.adapter_updates import default_updater

    return _run(lambda: default_updater(body.branch).apply(body.sites, include_conflicts=body.include_conflicts))


@router.get("/api/adapters/health")
async def adapter_health(site: str, preset: Optional[str] = None, authenticated: bool = Depends(verify_auth)):
    """R1-6：在浏览器里该站点的空闲标签页上巡检选择器（只查 DOM，不输入、不点击、不发送）。"""
    import asyncio

    from app.services.adapter_health import check_site_health

    try:
        return await asyncio.to_thread(check_site_health, site, preset)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # 浏览器未连接等
        logger.warning(f"适配器巡检失败: {exc}")
        raise HTTPException(status_code=503, detail=f"巡检失败：{exc}") from exc
