"""
app/api/config_routes.py - Config management API

Responsibilities:
- Site config CRUD
- Extractor management
- Image config and presets
- Workflow editor
- Selector definitions
"""

import copy
import asyncio
import json
import math
import time
from typing import Optional, Dict, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse

from app.api.config_compare_support import (
    _build_main_branch_compare_summary,
    _load_git_branch_sites_config,
    _resolve_branch_preset_config,
)
from app.api.config_route_models import (
    ConfigUpdateRequest,
    SiteAdvancedConfigRequest,
    PresetConfigUpdateRequest,
    _extract_site_advanced_update_payload,
    _get_model_fields_set,
    _merge_preset_config_payload,
    _normalize_preset_config_payload,
)
from app.api.config_workflow_support import (
    _execute_workflow_editor_test_payload,
    _notify_workflow_editor_action_result,
    _notify_workflow_editor_action_status,
    _save_site_workflow_payload,
)
from app.api.deps import verify_auth
from app.core import get_browser, BrowserConnectionError
from app.core.config import get_logger
from app.models.schemas import PRESET_ADVANCED_FIELDS, SITE_ADVANCED_FIELDS
from app.services.config_engine import config_engine
from app.services.extractor_manager import extractor_manager
from app.utils.site_url import extract_remote_site_domain
from app.utils.similarity import verify_extraction

logger = get_logger('API.CONFIG')

router = APIRouter()


LOCAL_SITE_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1", "[::1]"}


def _site_config_domain_host(domain: str) -> str:
    value = str(domain or "").strip()
    if not value:
        return ""

    parsed = urlparse(value if "://" in value else f"//{value}")
    host = parsed.hostname or value.split("/")[0].split(":")[0]
    return str(host or "").strip().lower().strip("[]")


def _is_local_site_domain(domain: str) -> bool:
    host = _site_config_domain_host(domain)
    return host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def _resolve_requested_preset_or_404(domain: str, preset_name: Optional[str]) -> Optional[str]:
    """解析显式预设名；未命中时直接返回 404。"""
    config_engine.refresh_if_changed()

    if domain not in config_engine.sites:
        raise HTTPException(status_code=404, detail=f"配置不存在: {domain}")

    requested_preset = "" if preset_name is None else str(preset_name).strip()
    if not requested_preset:
        return None

    site = config_engine.sites.get(domain) or {}
    presets = site.get("presets", {}) if isinstance(site, dict) else {}
    resolved_preset = config_engine._resolve_preset_alias_key(requested_preset, presets)
    if not isinstance(presets, dict) or resolved_preset not in presets:
        raise HTTPException(status_code=404, detail=f"预设不存在: {requested_preset}")

    return resolved_preset


def _unwrap_config_payload(data: Dict[str, Any], wrapper_key: str) -> Dict[str, Any]:
    """兼容 GET 返回的包裹形状，同时保留旧版扁平更新 payload。"""
    payload = copy.deepcopy(data)
    nested = payload.pop(wrapper_key, None)
    if nested is None:
        return payload
    if not isinstance(nested, dict) or isinstance(nested, list):
        raise HTTPException(status_code=400, detail=f"{wrapper_key} 必须是对象")

    nested_payload = copy.deepcopy(nested)
    return _merge_preset_config_payload(nested_payload, payload)


async def _read_json_object_or_400(request: Request) -> Dict[str, Any]:
    """读取 JSON 请求体，并要求顶层必须是对象。"""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="请求体必须是有效 JSON")
    except ValueError:
        raise HTTPException(status_code=400, detail="请求体必须是有效 JSON")
    if not isinstance(data, dict) or isinstance(data, list):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    return data

@router.get("/api/config")
async def get_config(authenticated: bool = Depends(verify_auth)):
    """获取站点配置（安全版：过滤内部键和本地地址）"""
    try:
        all_sites = config_engine.list_sites()
        
        filtered_sites = {
            domain: config
            for domain, config in all_sites.items()
            if not _is_local_site_domain(domain)
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
    rollback_state = None
    try:
        with config_engine._io_lock:
            try:
                config_engine.refresh_if_changed()
                rollback_state = (
                    copy.deepcopy(getattr(config_engine, "sites", {})),
                    copy.deepcopy(getattr(config_engine, "_global_default_presets", {})),
                    copy.deepcopy(getattr(config_engine, "_local_default_presets", {})),
                    copy.deepcopy(getattr(config_engine, "_local_sites_payload", {})),
                )

                # 过滤掉前端可能误传的内部键
                new_sites = {
                    k: v for k, v in request.config.items()
                    if not k.startswith('_')
                }

                existing_local_sites = {
                    domain: copy.deepcopy(site)
                    for domain, site in config_engine.sites.items()
                    if (
                        isinstance(domain, str)
                        and not domain.startswith('_')
                        and _is_local_site_domain(domain)
                        and domain not in new_sites
                    )
                }
                if existing_local_sites:
                    new_sites = {
                        **new_sites,
                        **existing_local_sites,
                    }

                config_engine.sites = new_sites
                config_engine._refresh_global_default_presets_from_sites()
                config_engine._apply_local_site_overrides()

                # 通过引擎保存（自动包含 _global）
                success = config_engine.save_config()

                if not success:
                    (
                        config_engine.sites,
                        config_engine._global_default_presets,
                        config_engine._local_default_presets,
                        config_engine._local_sites_payload,
                    ) = rollback_state
                    raise HTTPException(status_code=500, detail="配置文件写入失败")
            except HTTPException:
                raise
            except Exception as e:
                if rollback_state is not None:
                    (
                        config_engine.sites,
                        config_engine._global_default_presets,
                        config_engine._local_default_presets,
                        config_engine._local_sites_payload,
                    ) = rollback_state
                raise HTTPException(status_code=500, detail=str(e))

        return {
            "status": "success",
            "message": "配置已保存",
            "sites_count": len(new_sites)
        }
    except HTTPException:
        raise


@router.delete("/api/config/{domain}")
async def delete_site_config(
    domain: str,
    authenticated: bool = Depends(verify_auth)
):
    """删除站点配置"""
    config_engine.refresh_if_changed()
    if domain not in config_engine.sites:
        raise HTTPException(status_code=404, detail=f"配置不存在: {domain}")

    success = config_engine.delete_site_config(domain)

    if success:
        return {"status": "success", "message": f"已删除: {domain}"}
    else:
        raise HTTPException(status_code=500, detail="删除配置保存失败")


@router.get("/api/config/compare-main-summary")
async def get_main_branch_compare_summary(
    authenticated: bool = Depends(verify_auth)
):
    """汇总本地配置与 Git main 分支配置的差异。"""
    return _build_main_branch_compare_summary()


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
    resolved_preset = _resolve_requested_preset_or_404(domain, preset_name)

    if resolved_preset:
        # 返回指定预设的扁平配置
        data = config_engine._get_site_data_readonly(domain, resolved_preset)
        if data is None:
            raise HTTPException(status_code=404, detail=f"预设不存在: {resolved_preset}")
        return data
    else:
        # 返回整个站点结构（含所有预设）
        return copy.deepcopy(config_engine.sites[domain])


@router.put("/api/sites/{domain}/preset-config")
async def set_site_preset_config(
    domain: str,
    body: PresetConfigUpdateRequest,
    authenticated: bool = Depends(verify_auth)
):
    """保存单个站点预设的完整配置。"""
    with config_engine._io_lock:
        config_engine.refresh_if_changed()

        if domain not in config_engine.sites:
            raise HTTPException(status_code=404, detail=f"配置不存在: {domain}")

        site = config_engine.sites.get(domain) or {}
        presets = site.get("presets", {})
        if not isinstance(presets, dict) or not presets:
            raise HTTPException(status_code=404, detail=f"站点 {domain} 没有可用预设")

        requested_preset = str(
            body.preset_name
            or config_engine.get_default_preset(domain)
            or "主预设"
        ).strip()
        resolved_preset = config_engine._resolve_preset_alias_key(requested_preset, presets)
        if resolved_preset not in presets:
            raise HTTPException(status_code=404, detail=f"预设不存在: {requested_preset}")

        if bool(body.replace):
            normalized = _normalize_preset_config_payload(body.config)
        else:
            existing_preset = presets.get(resolved_preset)
            merged = _merge_preset_config_payload(
                existing_preset if isinstance(existing_preset, dict) else {},
                body.config,
            )
            normalized = _normalize_preset_config_payload(merged)
        previous_preset = copy.deepcopy(presets[resolved_preset])
        presets[resolved_preset] = normalized
        site["presets"] = presets

        try:
            success = config_engine.save_config()
        except Exception:
            presets[resolved_preset] = previous_preset
            site["presets"] = presets
            raise
        if not success:
            presets[resolved_preset] = previous_preset
            site["presets"] = presets
            raise HTTPException(status_code=500, detail="保存预设配置失败")

    logger.info(f"站点 {domain} [{resolved_preset}] 整体配置已更新")
    return {
        "status": "success",
        "message": "预设配置已保存",
        "domain": domain,
        "preset_name": resolved_preset,
        "config": copy.deepcopy(normalized),
    }


@router.get("/api/sites/{domain}/main-branch-config")
async def get_site_main_branch_config(
    domain: str,
    preset_name: Optional[str] = None,
    authenticated: bool = Depends(verify_auth)
):
    """读取 Git main 分支中 config/sites.json 的站点预设配置。"""
    branch_payload = _load_git_branch_sites_config("main")
    sites = branch_payload["sites"]

    if domain not in sites:
        raise HTTPException(status_code=404, detail=f"main 分支中不存在站点配置: {domain}")

    requested_preset = "" if preset_name is None else str(preset_name).strip()
    resolved_requested_preset = None
    if requested_preset:
        presets = sites[domain].get("presets", {}) if isinstance(sites[domain], dict) else {}
        resolved_requested_preset = config_engine._resolve_preset_alias_key(requested_preset, presets)
        if not isinstance(presets, dict) or resolved_requested_preset not in presets:
            raise HTTPException(status_code=404, detail=f"预设不存在: {requested_preset}")

    resolved = _resolve_branch_preset_config(sites[domain], resolved_requested_preset)
    return {
        "branch": "main",
        "path": branch_payload["path"],
        "domain": domain,
        "requested_preset_name": requested_preset,
        "preset_name": resolved["preset_name"],
        "match_mode": resolved["match_mode"],
        "config": resolved["config"],
    }


@router.get("/api/sites/{domain}/advanced-config")
async def get_site_advanced_config(
    domain: str,
    preset_name: Optional[str] = None,
    authenticated: bool = Depends(verify_auth)
):
    """获取站点/预设合并后的高级配置。"""
    resolved_preset = _resolve_requested_preset_or_404(domain, preset_name)

    try:
        config = config_engine.get_site_advanced_config(domain, preset_name=resolved_preset)
        return {
            "domain": domain,
            "preset_name": resolved_preset,
            "advanced": config,
        }
    except Exception as e:
        logger.error(f"获取站点高级配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/sites/{domain}/advanced-config")
async def set_site_advanced_config(
    domain: str,
    body: SiteAdvancedConfigRequest,
    preset_name: Optional[str] = None,
    authenticated: bool = Depends(verify_auth)
):
    """更新站点级高级配置。"""
    body_preset = "" if body.preset_name is None else str(body.preset_name).strip()
    query_preset = "" if preset_name is None else str(preset_name).strip()
    if body_preset and query_preset:
        resolved_body_preset = _resolve_requested_preset_or_404(domain, body_preset)
        resolved_query_preset = _resolve_requested_preset_or_404(domain, query_preset)
        if resolved_body_preset != resolved_query_preset:
            raise HTTPException(
                status_code=400,
                detail="preset_name 查询参数与请求体不一致",
            )
        resolved_preset = resolved_query_preset
    else:
        requested_preset = query_preset or body_preset or None
        resolved_preset = _resolve_requested_preset_or_404(domain, requested_preset)
    try:
        provided_fields = _get_model_fields_set(body)
        nested_advanced = body.advanced if isinstance(body.advanced, dict) else None
        prune_inherited_fields = set()
        allow_inherited_site_fields = False
        if resolved_preset and nested_advanced is not None:
            nested_site_fields = {
                key
                for key in nested_advanced.keys()
                if key in SITE_ADVANCED_FIELDS
            }
            if nested_site_fields:
                site_advanced = config_engine.get_site_advanced_config(domain)
                mismatched_site_fields = {
                    key
                    for key in nested_site_fields
                    if nested_advanced.get(key) != site_advanced.get(key)
                }
                if mismatched_site_fields:
                    joined = ", ".join(sorted(mismatched_site_fields))
                    raise HTTPException(
                        status_code=400,
                        detail=f"预设级高级配置不能包含站点级字段: {joined}",
                    )
                allow_inherited_site_fields = True
                prune_inherited_fields = {
                    key
                    for key in nested_advanced.keys()
                    if key in PRESET_ADVANCED_FIELDS and key not in provided_fields
                }
        payload = _extract_site_advanced_update_payload(
            body,
            preset_scope=bool(resolved_preset),
            allow_inherited_site_fields=allow_inherited_site_fields,
        )
        if resolved_preset:
            success = config_engine.set_preset_advanced_config(
                domain,
                payload,
                preset_name=resolved_preset,
                prune_inherited_fields=prune_inherited_fields,
            )
        else:
            success = config_engine.set_site_advanced_config(domain, payload)
        if not success:
            raise HTTPException(status_code=500, detail="高级配置保存失败")

        return {
            "status": "success",
            "message": f"站点 {domain} 高级配置已更新",
            "domain": domain,
            "preset_name": resolved_preset,
            "advanced": config_engine.get_site_advanced_config(
                domain,
                preset_name=resolved_preset,
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新站点高级配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/sites/{domain}/isolated-tab")
async def create_site_isolated_tab(
    domain: str,
    authenticated: bool = Depends(verify_auth)
):
    """为指定站点新建一个独立 Cookie 标签页。"""
    if domain not in config_engine.sites:
        raise HTTPException(status_code=404, detail=f"配置不存在: {domain}")

    try:
        browser = get_browser(auto_connect=False)
        result = browser.tab_pool.create_isolated_site_tab(domain)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "create_isolated_tab_failed"))
        return result
    except HTTPException:
        raise
    except BrowserConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"创建独立 Cookie 标签页失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/sites/{domain}/shared-tab")
async def create_site_shared_tab(
    domain: str,
    authenticated: bool = Depends(verify_auth)
):
    """为指定站点打开一个共享 Cookie 的受控窗口。"""
    if domain not in config_engine.sites:
        raise HTTPException(status_code=404, detail=f"配置不存在: {domain}")

    try:
        browser = get_browser(auto_connect=False)
        result = browser.tab_pool.create_shared_site_tab(domain)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "create_shared_tab_failed"))
        return result
    except HTTPException:
        raise
    except BrowserConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"打开共享 Cookie 受控窗口失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ================= 图片提取配置 API =================

@router.get("/api/sites/{domain}/image-config")
async def get_site_image_config(
    domain: str,
    preset_name: Optional[str] = None,
    authenticated: bool = Depends(verify_auth)
):
    """获取站点的多模态提取配置"""
    resolved_preset = _resolve_requested_preset_or_404(domain, preset_name)
    try:
        config = config_engine.get_site_image_config(domain, preset_name=resolved_preset)
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
    """设置站点的多模态提取配置"""
    try:
        data = await _read_json_object_or_400(request)
        resolved_preset = _resolve_requested_preset_or_404(domain, data.pop("preset_name", None))
        payload = _unwrap_config_payload(data, "image_extraction")

        success = config_engine.set_site_image_config(domain, payload, preset_name=resolved_preset)
        
        if success:
            return {
                "status": "success",
                "message": f"站点 {domain} 多模态提取配置已更新",
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
    """快速开关站点的多模态提取功能"""
    try:
        data = await _read_json_object_or_400(request)
        enabled = data.get("enabled", False)
        resolved_preset = _resolve_requested_preset_or_404(domain, data.get("preset_name"))

        current_config = config_engine.get_site_image_config(domain, preset_name=resolved_preset)
        current_config["enabled"] = enabled

        success = config_engine.set_site_image_config(domain, current_config, preset_name=resolved_preset)

        if success:
            status = "已启用" if enabled else "已禁用"
            return {
                "status": "success",
                "message": f"站点 {domain} 多模态提取{status}",
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
    """获取多模态提取的默认配置"""
    from app.models.schemas import get_default_image_extraction_config
    
    return {
        "defaults": get_default_image_extraction_config(),
        "limits": {
            "debounce_seconds": {"min": 0, "max": 30},
            "load_timeout_seconds": {"min": 1, "max": 60},
            "max_size_mb": {"min": 1, "max": 100}
        },
        "mode_options": ["all", "first", "last"],
        "modalities": ["image", "audio", "video"]
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
        data = await _read_json_object_or_400(request)
        preset_domain = str(data.get("preset_domain") or "").strip()
        if not preset_domain:
            raise HTTPException(status_code=400, detail="缺少 preset_domain")

        success = config_engine.apply_image_preset(domain, preset_domain)
        
        if success:
            return {
                "status": "success",
                "message": f"已应用图片预设到 {domain}",
                "domain": domain,
                "preset_domain": preset_domain
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"应用预设失败：站点不存在或预设无效"
            )
    
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
        preset_name = None
        try:
            body = await request.json()
            target_domain = body.get("target_domain")
            preset_name = body.get("preset_name")
        except Exception:
            pass
        
        browser_instance = get_browser(auto_connect=True)
        
        if not browser_instance.get_browser_handle():
            return JSONResponse(
                status_code=503,
                content={"success": False, "message": "浏览器未连接"}
            )
        
        try:
            tab = browser_instance.get_latest_tab()
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
        
        actual_domain = extract_remote_site_domain(url)
        if not actual_domain:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "当前页面不是可解析的网站，请先打开真实的远程站点"}
            )
        
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
        requested_preset = "" if preset_name is None else str(preset_name).strip()
        if requested_preset:
            preset_name = _resolve_requested_preset_or_404(config_domain, requested_preset)

        site_config = None
        try:
            site_config = config_engine.get_site_config(
                config_domain,
                tab.html,
                preset_name=preset_name
            )
        except Exception as e:
            logger.debug(f"获取站点配置失败: {e}")
        
        result = workflow_editor_injector.inject(
            tab,
            site_config,
            target_domain=config_domain,
            preset_name=preset_name
        )
        
        if result["success"]:
            return JSONResponse(content=result)
        else:
            return JSONResponse(status_code=500, content=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"注入编辑器失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)}
        )


@router.post("/api/workflow-editor/test")
async def test_workflow_editor_steps(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """在当前活动标签页上按真实执行器测试工作流步骤。"""
    from app.core.browser import get_browser

    try:
        data = await _read_json_object_or_400(request)
        logger.debug(f"[WFE_TEST] direct api request keys={sorted(list(data.keys()))}")
        domain = str(data.get("domain") or "").strip()
        requested_preset = "" if data.get("preset_name") is None else str(data.get("preset_name")).strip()
        if domain and requested_preset:
            data = dict(data)
            data["preset_name"] = _resolve_requested_preset_or_404(domain, requested_preset)

        browser_instance = get_browser(auto_connect=True)
        result = await asyncio.to_thread(
            _execute_workflow_editor_test_payload,
            browser_instance,
            data,
        )
        result.pop("_tab_ref", None)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"工作流编辑器测试失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/workflow-editor/consume-actions")
async def consume_workflow_editor_actions(
    authenticated: bool = Depends(verify_auth)
):
    """由本地控制台轮询，消费远端页面注入编辑器排队的动作请求。"""
    from app.core.browser import get_browser

    browser_instance = get_browser(auto_connect=True)
    if not browser_instance.get_browser_handle():
        return {
            "success": False,
            "message": "浏览器未连接",
            "executed_count": 0,
            "results": [],
        }

    consume_started_at = time.time()
    results = []
    tabs = []
    queued_total = 0
    try:
        tabs = browser_instance.get_tabs() or []
    except Exception as e:
        logger.debug(f"读取浏览器标签页失败: {e}")
        tabs = []

    for tab in tabs:
        try:
            queued_actions = tab.run_js(
                """
                return (function() {
                  const queue = Array.isArray(window.__WORKFLOW_EDITOR_PENDING_ACTIONS__)
                    ? window.__WORKFLOW_EDITOR_PENDING_ACTIONS__
                    : [];
                  if (!queue.length) {
                    return [];
                  }
                  const pending = queue.splice(0, queue.length);
                  return pending;
                })();
                """
            )
        except Exception:
            continue

        if not isinstance(queued_actions, list) or not queued_actions:
            continue

        queued_total += len(queued_actions)

        logger.debug(
            "[WFE_BRIDGE] queued actions "
            f"tab_id={getattr(tab, 'tab_id', None)!r} count={len(queued_actions)}"
        )

        for action in queued_actions:
            action_id = str((action or {}).get("id") or "").strip()
            action_type = str((action or {}).get("type") or "").strip()
            payload = (action or {}).get("payload") or {}

            logger.debug(
                "[WFE_BRIDGE] consume action "
                f"id={action_id!r} type={action_type!r} "
                f"payload_keys={sorted(list(payload.keys())) if isinstance(payload, dict) else 'invalid'}"
            )

            if action_type not in {"test_workflow", "save_workflow"}:
                logger.debug(f"忽略未知编辑器动作: {action_type}")
                continue

            try:
                payload = dict(payload)
                domain_for_preset = str(payload.get("domain") or "").strip()
                requested_preset = "" if payload.get("preset_name") is None else str(payload.get("preset_name")).strip()
                if domain_for_preset and requested_preset:
                    payload["preset_name"] = _resolve_requested_preset_or_404(domain_for_preset, requested_preset)

                action_started_at = time.time()
                queue_wait_ms = max(
                    0,
                    int((time.time() * 1000) - float((action or {}).get("created_at") or 0))
                )
                logger.debug(
                    "[WFE_BRIDGE] execute "
                    f"id={action_id!r} type={action_type!r} "
                    f"preset={str(payload.get('preset_name') or '')!r} "
                    f"steps={len(payload.get('workflow') or [])} "
                    f"queue_wait_ms={queue_wait_ms}"
                )
                if action_type == "test_workflow":
                    payload.setdefault("tab_id", str(getattr(tab, "tab_id", "") or ""))
                    result = _execute_workflow_editor_test_payload(
                        browser_instance,
                        payload,
                        progress_callback=lambda phase, message, _tab=tab, _action_id=action_id: _notify_workflow_editor_action_status(
                            _tab,
                            _action_id,
                            phase,
                            message,
                        )
                    )
                    tab_ref = result.pop("_tab_ref", tab)
                else:
                    domain = str(payload.get("domain") or "").strip()
                    result = _save_site_workflow_payload(domain, payload)
                    tab_ref = tab
                _notify_workflow_editor_action_result(
                    tab_ref,
                    action_id,
                    True,
                    str(result.get("message") or "测试完成"),
                )
                logger.debug(
                    "[WFE_BRIDGE] action success "
                    f"id={action_id!r} "
                    f"duration={time.time() - action_started_at:.2f}s "
                    f"message={str(result.get('message') or '')!r}"
                )
                results.append({
                    "id": action_id,
                    "type": action_type,
                    "success": True,
                    "message": result.get("message") or "测试完成",
                })
            except HTTPException as e:
                message = str(e.detail or "测试失败")
                logger.debug(
                    f"[WFE_BRIDGE] action http error id={action_id!r} "
                    f"message={message!r}"
                )
                _notify_workflow_editor_action_result(tab, action_id, False, message)
                results.append({
                    "id": action_id,
                    "type": action_type,
                    "success": False,
                    "message": message,
                })
            except Exception as e:
                message = str(e or "测试失败")
                logger.error(f"[WFE_BRIDGE] action exception id={action_id!r}: {message}")
                _notify_workflow_editor_action_result(tab, action_id, False, message)
                results.append({
                    "id": action_id,
                    "type": action_type,
                    "success": False,
                    "message": message,
                })

    if queued_total > 0:
        logger.debug(
            "[WFE_BRIDGE] consume done "
            f"queued_count={queued_total} executed_count={len(results)} "
            f"duration={time.time() - consume_started_at:.2f}s"
        )
    return {
        "success": True,
        "message": f"已消费 {len(results)} 个动作",
        "executed_count": len(results),
        "results": results,
    }


@router.put("/api/sites/{domain}/workflow")
async def update_site_workflow(
    domain: str,
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """更新站点的工作流配置（可视化编辑器保存）"""
    try:
        data = await _read_json_object_or_400(request)
        requested_preset = "" if data.get("preset_name") is None else str(data.get("preset_name")).strip()
        if requested_preset:
            data = dict(data)
            data["preset_name"] = _resolve_requested_preset_or_404(domain, requested_preset)

        return _save_site_workflow_payload(domain, data)
        
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
        data = await _read_json_object_or_400(request)
        extractor_id = data.get("extractor_id")
        
        if not extractor_id:
            raise HTTPException(status_code=400, detail="缺少 extractor_id")
        
        extractor_exists = extractor_manager.has_extractor(extractor_id)
        success = extractor_manager.set_default(extractor_id)
        
        if success:
            return {
                "status": "success",
                "message": f"默认提取器已设置为: {extractor_id}",
                "default": extractor_id
            }

        if extractor_exists:
            raise HTTPException(status_code=500, detail="默认提取器保存失败")
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
        config = await _read_json_object_or_400(request)

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
    resolved_preset = _resolve_requested_preset_or_404(domain, preset_name)
    try:
        preset_data = config_engine._get_site_data_readonly(domain, resolved_preset)
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
        data = await _read_json_object_or_400(request)
        extractor_id = data.get("extractor_id")
        resolved_preset = _resolve_requested_preset_or_404(domain, data.get("preset_name"))

        if not extractor_id:
            raise HTTPException(status_code=400, detail="缺少 extractor_id")

        success = config_engine.set_site_extractor(domain, extractor_id, preset_name=resolved_preset)

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
        data = await _read_json_object_or_400(request)

        extracted_text = data.get("extracted_text", "")
        expected_text = data.get("expected_text", "")
        if not isinstance(extracted_text, str) or not isinstance(expected_text, str):
            raise HTTPException(status_code=400, detail="提取文本和预期文本必须是字符串")
        try:
            threshold = float(data.get("threshold", 0.95))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="threshold 必须是有限数字")
        if not math.isfinite(threshold):
            raise HTTPException(status_code=400, detail="threshold 必须是有限数字")
        if threshold < 0 or threshold > 1:
            raise HTTPException(status_code=400, detail="threshold 必须在 0 到 1 之间")

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
        data = await _read_json_object_or_400(request)
        verified = data.get("verified", True)
        resolved_preset = _resolve_requested_preset_or_404(domain, data.get("preset_name"))

        success = config_engine.set_site_extractor_verified(domain, verified, preset_name=resolved_preset)

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
        data = await _read_json_object_or_400(request)
        definitions = data.get("definitions", [])

        for d in definitions:
            if not isinstance(d, dict):
                raise HTTPException(status_code=400, detail="无效的定义格式")
            if "key" not in d or "description" not in d:
                raise HTTPException(status_code=400, detail="缺少必需字段 key 或 description")

        if not config_engine.set_selector_definitions(definitions):
            raise HTTPException(status_code=500, detail="元素定义保存失败")

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
        if not config_engine.set_selector_definitions(defaults):
            raise HTTPException(status_code=500, detail="元素定义保存失败")

        return {
            "status": "success",
            "message": "已重置为默认值",
            "definitions": defaults
        }
    except HTTPException:
        raise
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
    resolved_preset = _resolve_requested_preset_or_404(domain, preset_name)
    try:
        config = config_engine.get_site_file_paste_config(domain, preset_name=resolved_preset)
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
        data = await _read_json_object_or_400(request)
        resolved_preset = _resolve_requested_preset_or_404(domain, data.pop("preset_name", None))
        payload = _unwrap_config_payload(data, "file_paste")

        success = config_engine.set_site_file_paste_config(domain, payload, preset_name=resolved_preset)
        
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
        data = await _read_json_object_or_400(request)
        if "configs" not in data:
            raise HTTPException(status_code=400, detail="缺少 configs 字段")
        configs = data.get("configs")
        if not isinstance(configs, dict):
            raise HTTPException(status_code=400, detail="configs 必须是对象")
        if not configs:
            raise HTTPException(status_code=400, detail="缺少 configs 字段")

        updated = []
        failed = []

        for domain, config in configs.items():
            if not isinstance(config, dict):
                failed.append(domain)
                continue

            config_payload = copy.deepcopy(config)
            try:
                requested_preset = config_payload.pop("preset_name", None)
                resolved_preset = _resolve_requested_preset_or_404(domain, requested_preset)
                config_payload = _unwrap_config_payload(config_payload, "file_paste")
                success = config_engine.set_site_file_paste_config(
                    domain,
                    config_payload,
                    preset_name=resolved_preset,
                )
            except HTTPException:
                success = False
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
    resolved_preset = _resolve_requested_preset_or_404(domain, preset_name)
    try:
        config = config_engine.get_site_stream_config(domain, preset_name=resolved_preset)
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
        data = await _read_json_object_or_400(request)
        resolved_preset = _resolve_requested_preset_or_404(domain, data.pop("preset_name", None))
        payload = _unwrap_config_payload(data, "stream_config")

        success = config_engine.set_site_stream_config(domain, payload, preset_name=resolved_preset)
        
        if success:
            return {
                "status": "success",
                "message": f"站点 {domain} 流式配置已更新",
                "domain": domain,
                "mode": payload.get("mode", "dom")
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
    from app.core.request_transport import get_request_transport_defaults_payload
    
    return {
        "defaults": get_default_stream_config(),
        "network_defaults": get_default_network_config(),
        "request_transport": get_request_transport_defaults_payload(),
        "limits": {
            "hard_timeout": {"min": 10, "max": 600},
            "silence_threshold": {"min": 0.5, "max": 30},
            "response_interval": {"min": 0.1, "max": 5}
        },
        "mode_options": ["dom", "network"],
        "stream_match_mode_options": ["keyword", "regex"],
    }
