"""站点预设管理 API（原 app/api/tab_routes.py 的 `# === 预设管理 API ===` 区块）。

只做搬迁，不改 URL 与请求模型。这些端点只依赖配置引擎，不碰浏览器，
因此不再与 5k 行的标签页路由文件纠缠。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import verify_dashboard_auth as verify_auth
from app.core.config import get_logger

logger = get_logger("API.PRESETS")

router = APIRouter()

class CreatePresetRequest(BaseModel):
    """创建预设请求"""
    new_name: str = Field(..., min_length=1, max_length=50)
    source_name: Optional[str] = Field(default=None)


class RenamePresetRequest(BaseModel):
    """重命名预设请求"""
    old_name: str = Field(..., min_length=1, max_length=50)
    new_name: str = Field(..., min_length=1, max_length=50)


class SetDefaultPresetRequest(BaseModel):
    """设置默认预设请求"""
    preset_name: str = Field(..., min_length=1, max_length=50)


@router.get("/api/presets/{domain}")
async def get_site_presets(
    domain: str,
    authenticated: bool = Depends(verify_auth)
):
    """获取指定站点的所有预设"""
    try:
        from app.services.config_engine import config_engine
        presets = config_engine.list_presets(domain)
        default_preset = config_engine.get_default_preset(domain)
        return {"domain": domain, "presets": presets, "default_preset": default_preset}
    except Exception as e:
        logger.error(f"获取预设列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/presets/{domain}")
async def create_site_preset(
    domain: str,
    body: CreatePresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """为站点创建新预设（克隆自现有预设）"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.create_preset(domain, body.new_name, body.source_name)

        if success:
            return {"success": True, "message": f"预设 '{body.new_name}' 已创建"}
        else:
            raise HTTPException(status_code=400, detail="创建失败（预设已存在或站点不存在）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/presets/{domain}/rename")
async def rename_site_preset(
    domain: str,
    body: RenamePresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """重命名指定预设"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.rename_preset(domain, body.old_name, body.new_name)

        if success:
            return {
                "success": True,
                "message": f"预设 '{body.old_name}' 已重命名为 '{body.new_name}'",
            }
        else:
            raise HTTPException(status_code=400, detail="重命名失败（预设不存在或新名称已存在）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重命名预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/presets/{domain}/default")
async def set_site_default_preset(
    domain: str,
    body: SetDefaultPresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """设置站点默认预设（本地覆盖）"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.set_default_preset(domain, body.preset_name)

        if success:
            return {
                "success": True,
                "message": f"默认预设已设置为 '{body.preset_name}'（本地覆盖）",
                "domain": domain,
                "default_preset": body.preset_name
            }
        else:
            raise HTTPException(status_code=400, detail="设置失败（站点或预设不存在）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置默认预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/presets/{domain}/{preset_name}")
async def delete_site_preset(
    domain: str,
    preset_name: str,
    authenticated: bool = Depends(verify_auth)
):
    """删除指定预设（不能删除最后一个）"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.delete_preset(domain, preset_name)

        if success:
            return {"success": True, "message": f"预设 '{preset_name}' 已删除"}
        else:
            raise HTTPException(status_code=400, detail="删除失败（预设不存在或是最后一个）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

