"""
app/api/config_routes.py - 配置管理 API

职责：
- 站点配置 CRUD
- 提取器管理
- 图片配置与预设
- 工作流编辑器
- 元素定义
"""

import json
from typing import Optional, Dict, Any

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.config import AppConfig, get_logger
from app.core import get_browser, BrowserConnectionError
from app.services.config_engine import config_engine, ConfigConstants
from app.services.extractor_manager import extractor_manager
from app.utils.similarity import verify_extraction

logger = get_logger("API.CONFIG")

router = APIRouter()


# ================= 请求模型 =================

class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    config: Dict[str, Any] = Field(...)


# ================= 认证依赖 =================

async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    """验证 Bearer Token"""
    if not AppConfig.is_auth_enabled():
        return True

    if not AppConfig.AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="服务配置错误")

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = authorization.replace("Bearer ", "").strip()

    if token != AppConfig.get_auth_token():
        raise HTTPException(
            status_code=401,
            detail="认证令牌无效",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return True


# ================= 站点配置 API =================

@router.get("/api/config")
async def get_config(authenticated: bool = Depends(verify_auth)):
    """获取站点配置（安全版：过滤内部键和本地地址）"""
    try:
        all_sites = config_engine.list_sites()
        
        local_patterns = ["127.0.0.1", "localhost", "0.0.0.0", "::1"]
        
        filtered_sites = {
            domain: config 
            for domain, config in all_sites.items()
            if not any(pattern in domain for pattern in local_patterns)
        }
        
        logger.debug(
            f"站点列表过滤: 总数 {len(all_sites)} -> "
            f"过滤后 {len(filtered_sites)} (移除 {len(all_sites) - len(filtered_sites)} 个)"
        )
        
        return filtered_sites
    
    except Exception as e:
        logger.error(f"获取配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/config")
async def save_config(
    request: ConfigUpdateRequest,
    authenticated: bool = Depends(verify_auth)
):
    """保存站点配置"""
    try:
        # 过滤掉前端可能误传的内部键
        new_sites = {
            k: v for k, v in request.config.items()
            if not k.startswith('_')
        }
        config_engine.sites = new_sites
        
        # 通过引擎保存（自动包含 _global）
        success = config_engine.save_config()
        
        if not success:
            raise HTTPException(status_code=500, detail="配置文件写入失败")

        return {
            "status": "success",
            "message": "配置已保存",
            "sites_count": len(new_sites)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/config/{domain}")
async def delete_site_config(
    domain: str,
    authenticated: bool = Depends(verify_auth)
):
    """删除站点配置"""
    success = config_engine.delete_site_config(domain)

    if success:
        return {"status": "success", "message": f"已删除: {domain}"}
    else:
        raise HTTPException(status_code=404, detail=f"配置不存在: {domain}")


@router.get("/api/config/{domain}")
async def get_site_config(
    domain: str,
    preset_name: Optional[str] = None,
    authenticated: bool = Depends(verify_auth)
):
    """
    获取单个站点配置
    
    Query 参数:
        preset_name: 预设名称（可选，默认返回整个站点结构含所有预设）
    
    - 不传 preset_name: 返回 { "presets": { "主预设": {...}, ... } }
    - 传 preset_name: 返回该预设的扁平配置 { "selectors": {...}, "workflow": [...], ... }
    """
    if domain not in config_engine.sites:
        raise HTTPException(status_code=404, detail=f"配置不存在: {domain}")
    
    if preset_name:
        # 返回指定预设的扁平配置
        data = config_engine._get_site_data_readonly(domain, preset_name)
        if data is None:
            raise HTTPException(status_code=404, detail=f"预设不存在: {preset_name}")
        return data
    else:
        # 返回整个站点结构（含所有预设）
        import copy
        return copy.deepcopy(config_engine.sites[domain])


# ================= 图片提取配置 API =================

@router.get("/api/sites/{domain}/image-config")
async def get_site_image_config(
    domain: str,
    preset_name: Optional[str] = None,
    authenticated: bool = Depends(verify_auth)
):
    """获取站点的图片提取配置"""
    try:
        config = config_engine.get_site_image_config(domain, preset_name=preset_name)
        return {
            "domain": domain,
            "image_extraction": config,
            "is_enabled": config.get("enabled", False)
        }
    except Exception as e:
        logger.error(f"获取图片配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/sites/{domain}/image-config")
async def set_site_image_config(
    domain: str,
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """设置站点的图片提取配置"""
    try:
        data = await request.json()
        preset_name = data.pop("preset_name", None)
        
        success = config_engine.set_site_image_config(domain, data, preset_name=preset_name)
        
        if success:
            return {
                "status": "success",
                "message": f"站点 {domain} 图片提取配置已更新",
                "domain": domain
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"设置失败：站点或预设不存在"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置图片配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/sites/{domain}/image-config/toggle")
async def toggle_site_image_extraction(
    domain: str,
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """快速开关站点的图片提取功能"""
    try:
        data = await request.json()
        enabled = data.get("enabled", False)
        preset_name = data.get("preset_name")
        
        current_config = config_engine.get_site_image_config(domain, preset_name=preset_name)
        current_config["enabled"] = enabled
        
        success = config_engine.set_site_image_config(domain, current_config, preset_name=preset_name)
        
        if success:
            status = "已启用" if enabled else "已禁用"
            return {
                "status": "success",
                "message": f"站点 {domain} 图片提取{status}",
                "enabled": enabled
            }
        else:
            raise HTTPException(status_code=400, detail=f"站点 {domain} 不存在")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换图片提取状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/settings/image-extraction-defaults")
async def get_image_extraction_defaults(authenticated: bool = Depends(verify_auth)):
    """获取图片提取的默认配置"""
    from app.models.schemas import get_default_image_extraction_config
    
    return {
        "defaults": get_default_image_extraction_config(),
        "limits": {
            "debounce_seconds": {"min": 0, "max": 30},
            "load_timeout_seconds": {"min": 1, "max": 60},
            "max_size_mb": {"min": 1, "max": 100}
        },
        "mode_options": ["all", "first", "last"]
    }


# ================= 图片预设 API =================

@router.get("/api/image-presets")
async def list_image_presets(authenticated: bool = Depends(verify_auth)):
    """获取所有可用的图片预设"""
    try:
        presets = config_engine.list_image_presets()
        return {
            "presets": presets,
            "count": len(presets)
        }
    except Exception as e:
        logger.error(f"获取图片预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/sites/{domain}/image-preset")
async def get_site_image_preset(
    domain: str,
    authenticated: bool = Depends(verify_auth)
):
    """获取站点的图片预设信息"""
    try:
        preset_info = config_engine.get_image_preset(domain)
        return {
            "domain": domain,
            **preset_info
        }
    except Exception as e:
        logger.error(f"获取站点预设信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/sites/{domain}/apply-image-preset")
async def apply_image_preset(
    domain: str,
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """应用图片预设到站点"""
    try:
        data = await request.json()
        preset_domain = data.get("preset_domain")
        
        success = config_engine.apply_image_preset(domain, preset_domain)
        
        if success:
            return {
                "status": "success",
                "message": f"已应用图片预设到 {domain}",
                "domain": domain,
                "preset_domain": preset_domain or "auto"
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"应用预设失败：站点不存在或预设无效"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"应用图片预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/image-presets/reload")
async def reload_image_presets(authenticated: bool = Depends(verify_auth)):
    """重新加载图片预设文件"""
    try:
        config_engine.reload_presets()
        presets = config_engine.list_image_presets()
        
        return {
            "status": "success",
            "message": "图片预设已重新加载",
            "count": len(presets)
        }
    except Exception as e:
        logger.error(f"重新加载预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ================= 工作流编辑器 API =================

@router.post("/api/workflow-editor/inject")
async def inject_workflow_editor(request: Request):
    """向当前活动标签页注入可视化工作流编辑器"""
    from app.core.workflow_editor import workflow_editor_injector
    from app.core.browser import get_browser
    
    try:
        target_domain = None
        try:
            body = await request.json()
            target_domain = body.get("target_domain")
        except Exception:
            pass
        
        browser_instance = get_browser(auto_connect=True)
        
        if not browser_instance.page:
            return JSONResponse(
                status_code=503,
                content={"success": False, "message": "浏览器未连接"}
            )
        
        try:
            tab = browser_instance.page.latest_tab
        except Exception as e:
            return JSONResponse(
                status_code=503,
                content={"success": False, "message": f"无法获取标签页: {str(e)}"}
            )
        
        url = tab.url or ""
        if not url or url in ("about:blank", "chrome://newtab/", "chrome://new-tab-page/"):
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "请先打开目标网站"}
            )
        
        actual_domain = url.split("//")[-1].split("/")[0]
        
        if target_domain and target_domain != actual_domain:
            logger.warning(f"域名不匹配: 期望 {target_domain}, 实际 {actual_domain}")
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"域名不匹配！\n\n配置目标: {target_domain}\n当前页面: {actual_domain}\n\n请先在浏览器中打开 {target_domain} 的页面。",
                    "domain_mismatch": True,
                    "expected_domain": target_domain,
                    "actual_domain": actual_domain
                }
            )
        
        config_domain = target_domain or actual_domain
        site_config = None
        try:
            site_config = config_engine.get_site_config(config_domain, tab.html)
        except Exception as e:
            logger.debug(f"获取站点配置失败: {e}")
        
        result = workflow_editor_injector.inject(tab, site_config, target_domain=config_domain)
        
        if result["success"]:
            return JSONResponse(content=result)
        else:
            return JSONResponse(status_code=500, content=result)
            
    except Exception as e:
        logger.error(f"注入编辑器失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)}
        )


@router.put("/api/sites/{domain}/workflow")
async def update_site_workflow(
    domain: str,
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """更新站点的工作流配置（可视化编辑器保存）"""
    try:
        data = await request.json()
        new_workflow = data.get("workflow")
        preset_name = data.get("preset_name")
        
        if new_workflow is None:
            raise HTTPException(status_code=400, detail="缺少 workflow 字段")
        
        if domain not in config_engine.sites:
            raise HTTPException(status_code=404, detail=f"站点不存在: {domain}")
        
        success = config_engine.set_preset_workflow(domain, new_workflow, preset_name=preset_name)
        
        if not success:
            raise HTTPException(status_code=500, detail="保存配置文件失败")
        
        used_preset = preset_name or "主预设"
        logger.info(f"站点 {domain} [{used_preset}] 工作流已更新: {len(new_workflow)} 个步骤")
        
        return {
            "status": "success",
            "message": f"工作流已保存",
            "domain": domain,
            "preset_name": used_preset,
            "steps_count": len(new_workflow)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新工作流失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/workflow-editor/clear-cache")
async def clear_editor_cache():
    """清除编辑器脚本缓存（开发调试用）"""
    from app.core.workflow_editor import workflow_editor_injector
    workflow_editor_injector.clear_cache()
    return {"success": True, "message": "缓存已清除"}


# ================= 提取器管理 API =================

@router.get("/api/extractors")
async def list_extractors(authenticated: bool = Depends(verify_auth)):
    """获取所有可用的提取器"""
    try:
        extractors = extractor_manager.list_extractors()
        default_id = extractor_manager.get_default_id()
        
        return {
            "extractors": extractors,
            "default": default_id,
            "count": len(extractors)
        }
    except Exception as e:
        logger.error(f"获取提取器列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/extractors/default")
async def set_default_extractor(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """设置默认提取器"""
    try:
        data = await request.json()
        extractor_id = data.get("extractor_id")
        
        if not extractor_id:
            raise HTTPException(status_code=400, detail="缺少 extractor_id")
        
        success = extractor_manager.set_default(extractor_id)
        
        if success:
            return {
                "status": "success",
                "message": f"默认提取器已设置为: {extractor_id}",
                "default": extractor_id
            }
        else:
            raise HTTPException(status_code=400, detail=f"提取器不存在: {extractor_id}")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置默认提取器失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/extractors/export")
async def export_extractors(authenticated: bool = Depends(verify_auth)):
    """导出提取器配置"""
    try:
        config = extractor_manager.export_config()
        return JSONResponse(
            content=config,
            headers={
                "Content-Disposition": "attachment; filename=extractors.json"
            }
        )
    except Exception as e:
        logger.error(f"导出提取器配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/extractors/import")
async def import_extractors(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """导入提取器配置"""
    try:
        config = await request.json()
        
        if "extractors" not in config:
            raise HTTPException(status_code=400, detail="无效的配置格式：缺少 extractors 字段")
        
        success = extractor_manager.import_config(config)
        
        if success:
            return {
                "status": "success",
                "message": f"成功导入 {len(config.get('extractors', {}))} 个提取器配置",
                "extractors_count": len(config.get('extractors', {}))
            }
        else:
            raise HTTPException(status_code=400, detail="导入失败")
    
    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="无效的 JSON 格式")
    except Exception as e:
        logger.error(f"导入提取器配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/sites/{domain}/extractor")
async def get_site_extractor(
    domain: str,
    preset_name: Optional[str] = None,
    authenticated: bool = Depends(verify_auth)
):
    """获取站点当前使用的提取器"""
    try:
        if domain not in config_engine.sites:
            raise HTTPException(status_code=404, detail=f"站点不存在: {domain}")
        
        preset_data = config_engine._get_site_data_readonly(domain, preset_name)
        if preset_data is None:
            raise HTTPException(status_code=404, detail=f"预设不存在")
        
        extractor_id = preset_data.get("extractor_id")
        extractor_verified = preset_data.get("extractor_verified", False)
        
        if not extractor_id:
            extractor_id = extractor_manager.get_default_id()
        
        extractor_config = extractor_manager.get_extractor_config(extractor_id)
        
        return {
            "domain": domain,
            "extractor_id": extractor_id,
            "extractor_name": extractor_config.get("name", extractor_id) if extractor_config else extractor_id,
            "verified": extractor_verified,
            "is_default": extractor_id == extractor_manager.get_default_id()
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取站点提取器失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/sites/{domain}/extractor")
async def set_site_extractor(
    domain: str,
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """为站点分配提取器"""
    try:
        data = await request.json()
        extractor_id = data.get("extractor_id")
        preset_name = data.get("preset_name")
        
        if not extractor_id:
            raise HTTPException(status_code=400, detail="缺少 extractor_id")
        
        success = config_engine.set_site_extractor(domain, extractor_id, preset_name=preset_name)
        
        if success:
            return {
                "status": "success",
                "message": f"站点 {domain} 已绑定提取器: {extractor_id}",
                "domain": domain,
                "extractor_id": extractor_id
            }
        else:
            raise HTTPException(
                status_code=400, 
                detail=f"设置失败：站点或预设不存在，或提取器无效"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置站点提取器失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/extractors/verify")
async def verify_extractor_result(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """验证提取结果的准确性"""
    try:
        data = await request.json()
        
        extracted_text = data.get("extracted_text", "")
        expected_text = data.get("expected_text", "")
        threshold = float(data.get("threshold", 0.95))
        
        if not extracted_text and not expected_text:
            raise HTTPException(status_code=400, detail="提取文本和预期文本不能同时为空")
        
        passed, similarity, message = verify_extraction(
            extracted_text, 
            expected_text, 
            threshold=threshold
        )
        
        return {
            "similarity": round(similarity, 4),
            "passed": passed,
            "message": message,
            "threshold": threshold,
            "extracted_length": len(extracted_text),
            "expected_length": len(expected_text)
        }
    
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"参数错误: {e}")
    except Exception as e:
        logger.error(f"验证提取结果失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/sites/{domain}/extractor/verify")
async def mark_site_extractor_verified(
    domain: str,
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """标记站点提取器验证状态"""
    try:
        data = await request.json()
        verified = data.get("verified", True)
        preset_name = data.get("preset_name")
        
        success = config_engine.set_site_extractor_verified(domain, verified, preset_name=preset_name)
        
        if success:
            return {
                "status": "success",
                "message": f"站点 {domain} 验证状态已更新",
                "domain": domain,
                "verified": verified
            }
        else:
            raise HTTPException(status_code=404, detail=f"站点不存在: {domain}")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新验证状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ================= 元素定义 API =================

@router.get("/api/settings/selector-definitions")
async def get_selector_definitions(authenticated: bool = Depends(verify_auth)):
    """获取元素定义列表"""
    try:
        definitions = config_engine.get_selector_definitions()
        return {"definitions": definitions}
    except Exception as e:
        logger.error(f"获取元素定义失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/settings/selector-definitions")
async def save_selector_definitions(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """保存元素定义列表"""
    try:
        data = await request.json()
        definitions = data.get("definitions", [])

        for d in definitions:
            if not isinstance(d, dict):
                raise HTTPException(status_code=400, detail="无效的定义格式")
            if "key" not in d or "description" not in d:
                raise HTTPException(status_code=400, detail="缺少必需字段 key 或 description")

        config_engine.set_selector_definitions(definitions)

        return {
            "status": "success",
            "message": "元素定义已保存",
            "count": len(definitions)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存元素定义失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/settings/selector-definitions/reset")
async def reset_selector_definitions(authenticated: bool = Depends(verify_auth)):
    """重置元素定义为默认值"""
    try:
        from app.models.schemas import get_default_selector_definitions

        defaults = get_default_selector_definitions()
        config_engine.set_selector_definitions(defaults)

        return {
            "status": "success",
            "message": "已重置为默认值",
            "definitions": defaults
        }
    except Exception as e:
        logger.error(f"重置元素定义失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    # ================= 文件粘贴配置 API =================

@router.get("/api/file-paste/configs")
async def get_all_file_paste_configs(authenticated: bool = Depends(verify_auth)):
    """获取所有站点的文件粘贴配置"""
    try:
        configs = config_engine.get_all_file_paste_configs()
        return {
            "configs": configs,
            "count": len(configs)
        }
    except Exception as e:
        logger.error(f"获取文件粘贴配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/sites/{domain}/file-paste")
async def get_site_file_paste_config(
    domain: str,
    preset_name: Optional[str] = None,
    authenticated: bool = Depends(verify_auth)
):
    """获取站点的文件粘贴配置"""
    try:
        config = config_engine.get_site_file_paste_config(domain, preset_name=preset_name)
        return {
            "domain": domain,
            "file_paste": config
        }
    except Exception as e:
        logger.error(f"获取文件粘贴配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/sites/{domain}/file-paste")
async def set_site_file_paste_config(
    domain: str,
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """设置站点的文件粘贴配置"""
    try:
        data = await request.json()
        preset_name = data.pop("preset_name", None)
        
        success = config_engine.set_site_file_paste_config(domain, data, preset_name=preset_name)
        
        if success:
            return {
                "status": "success",
                "message": f"站点 {domain} 文件粘贴配置已更新",
                "domain": domain
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"设置失败：站点或预设不存在"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置文件粘贴配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/file-paste/batch")
async def batch_update_file_paste_configs(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """批量更新多个站点的文件粘贴配置"""
    try:
        data = await request.json()
        configs = data.get("configs", {})
        
        if not configs:
            raise HTTPException(status_code=400, detail="缺少 configs 字段")
        
        updated = []
        failed = []
        
        for domain, config in configs.items():
            success = config_engine.set_site_file_paste_config(domain, config)
            if success:
                updated.append(domain)
            else:
                failed.append(domain)
        
        return {
            "status": "success",
            "message": f"已更新 {len(updated)} 个站点",
            "updated": updated,
            "failed": failed
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量更新文件粘贴配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 🆕 ================= 流式配置 API =================

@router.get("/api/sites/{domain}/stream-config")
async def get_site_stream_config(
    domain: str,
    preset_name: Optional[str] = None,
    authenticated: bool = Depends(verify_auth)
):
    """获取站点的流式配置"""
    try:
        config = config_engine.get_site_stream_config(domain, preset_name=preset_name)
        return {
            "domain": domain,
            "stream_config": config,
            "mode": config.get("mode", "dom"),
            "has_network_config": config.get("network") is not None
        }
    except Exception as e:
        logger.error(f"获取流式配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/sites/{domain}/stream-config")
async def set_site_stream_config(
    domain: str,
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """设置站点的流式配置"""
    try:
        data = await request.json()
        preset_name = data.pop("preset_name", None)
        
        success = config_engine.set_site_stream_config(domain, data, preset_name=preset_name)
        
        if success:
            return {
                "status": "success",
                "message": f"站点 {domain} 流式配置已更新",
                "domain": domain,
                "mode": data.get("mode", "dom")
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"设置失败：站点或预设不存在"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置流式配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/parsers")
async def list_parsers(authenticated: bool = Depends(verify_auth)):
    """获取所有可用的响应解析器"""
    try:
        parsers = config_engine.list_available_parsers()
        return {
            "parsers": parsers,
            "count": len(parsers)
        }
    except Exception as e:
        logger.error(f"获取解析器列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/settings/stream-config-defaults")
async def get_stream_config_defaults(authenticated: bool = Depends(verify_auth)):
    """获取流式配置的默认值和限制"""
    from app.services.config.engine import get_default_stream_config, get_default_network_config
    
    return {
        "defaults": get_default_stream_config(),
        "network_defaults": get_default_network_config(),
        "limits": {
            "hard_timeout": {"min": 10, "max": 600},
            "silence_threshold": {"min": 0.5, "max": 30},
            "initial_wait": {"min": 5, "max": 120},
            "first_response_timeout": {"min": 1, "max": 30},
            "response_interval": {"min": 0.1, "max": 5}
        },
        "mode_options": ["dom", "network"]
    }