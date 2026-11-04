"""标签页池管理 API（原 app/api/tab_routes.py 的 `# === 标签页管理 API ===` 区块）。

只做搬迁，不改任何 URL、请求模型与业务逻辑。
依赖方向：``app.api.admin.pool`` -> ``app.api.tab_routes``（单向），
因为标签页池的辅助函数（浏览器配置读写锁、模型名/预设覆盖、路由组等）
目前仍与标签页路由共用，待 tab_routes 进一步拆分时再下沉。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import verify_dashboard_auth
from app.api.deps import verify_dashboard_auth as verify_auth
from app.api.tab_routes import (
    FOLLOW_DEFAULT_PRESET,
    TAB_POOL_ALLOCATION_OPTIONS,
    TAB_ROUTE_METHOD_OPTIONS,
    _attach_preset_info_to_tabs,
    _browser_config_lock,
    _coerce_bool,
    _get_config_allocation_mode,
    _get_enabled_route_methods_from_config,
    _get_tab_info_by_index,
    _get_tab_item_exclusion_url,
    _get_tab_pool_allocation_mode,
    _get_tab_pool_auto_remember_url_presets,
    _get_tab_pool_excluded_urls,
    _get_tab_pool_preserve_error_tabs,
    _get_tab_pool_route_groups,
    _model_name_overrides_lock,
    _normalize_enabled_route_methods,
    _normalize_excluded_urls,
    _normalize_model_name,
    _preset_overrides_lock,
    _read_browser_config,
    _read_model_name_overrides_unlocked,
    _read_preset_overrides_unlocked,
    _sync_tab_pool_model_name_overrides,
    _sync_tab_pool_preset_overrides,
    _write_browser_config_unlocked,
    _write_model_name_overrides_unlocked,
    _write_preset_overrides_unlocked,
)
from app.core import get_browser
from app.core.config import get_logger
from app.core.tab_pool_parts._utils import _should_skip_pool_url
from app.utils.site_url import (
    extract_remote_site_domain,
    normalize_exact_tab_url,
    normalize_route_domain,
)
from app.utils.tab_route_groups import normalize_route_groups

logger = get_logger("API.TAB.POOL")

router = APIRouter()

class TabPoolConfigRequest(BaseModel):
    """标签页池配置更新请求。"""
    # allocation_mode / enabled_route_methods 用 None 表示"本次请求没带这个字段"，
    # 与 excluded_urls / preserve_error_tabs / route_groups 的部分更新语义保持一致。
    # 此前 allocation_mode 有默认值 "first_idle"、enabled_route_methods 为 None 时
    # 被归一成"全部启用"，于是任何只带部分字段的 PUT（例如只保存路由组）都会把
    # 用户设好的分配模式和路由方式开关静默重置掉。
    allocation_mode: Optional[str] = Field(default=None)
    enabled_route_methods: Optional[List[str]] = Field(default=None)
    excluded_urls: Optional[List[str]] = Field(default=None)
    preserve_error_tabs: Optional[bool] = Field(default=None)
    auto_remember_url_presets: Optional[bool] = Field(default=None)
    route_groups: Optional[List[Dict[str, Any]]] = Field(default=None)


class TabModelNameRequest(BaseModel):
    """标签页暴露模型名更新请求。"""
    model_name: Optional[str] = Field(default=None, max_length=200)
    persist_scope: Optional[str] = Field(default=None)
    reset: Optional[bool] = Field(default=False)


@router.get("/api/tab-pool/tabs")
async def get_tab_pool_tabs(authenticated: bool = Depends(verify_auth)):
    """
    获取所有标签页及其持久编号和预设信息

    返回格式：
    {
        "tabs": [
            {
                "persistent_index": 1,
                "id": "gpt_1",
                "url": "https://chatgpt.com/",
                "status": "idle",
                "route_prefix": "/url/chatgpt.com",
                "tab_route_prefix": "/tab/1",
                "domain_route_prefix": "/url/chatgpt.com",
                "preset_name": null,
                "available_presets": ["主预设", "无临时聊天"]
            },
            ...
        ],
        "count": 3
    }
    """
    try:
        browser = get_browser(auto_connect=False)
        tabs = browser.tab_pool.get_tabs_with_index()
        allocation_mode = _get_tab_pool_allocation_mode(browser.tab_pool)
        browser_config = _read_browser_config()
        enabled_route_methods = _get_enabled_route_methods_from_config(browser_config)
        excluded_urls = _get_tab_pool_excluded_urls(browser.tab_pool, browser_config)
        preserve_error_tabs = _get_tab_pool_preserve_error_tabs(browser.tab_pool, browser_config)
        auto_remember_url_presets = _get_tab_pool_auto_remember_url_presets(browser.tab_pool, browser_config)
        route_groups = _get_tab_pool_route_groups(browser.tab_pool, browser_config)
        for tab_info in tabs:
            exclusion_url = _get_tab_item_exclusion_url(tab_info, excluded_urls)
            tab_info["route_excluded"] = bool(exclusion_url)
            tab_info["route_exclusion_url"] = exclusion_url

        # 🆕 为每个标签页附加可用预设列表
        try:
            from app.services.config_engine import config_engine
            _attach_preset_info_to_tabs(tabs, config_engine, auto_remember_url_presets)
        except Exception as e:
            logger.debug(f"获取预设列表失败: {e}")
            for tab_info in tabs:
                tab_info["available_presets"] = []
                tab_info["default_preset"] = None
                tab_info["effective_preset_name"] = tab_info.get("preset_name")
                tab_info["is_using_default_preset"] = not bool(tab_info.get("preset_name"))

        return {
            "tabs": tabs,
            "count": len(tabs),
            "allocation_mode": allocation_mode,
            "allocation_mode_options": TAB_POOL_ALLOCATION_OPTIONS,
            "enabled_route_methods": enabled_route_methods,
            "route_method_options": TAB_ROUTE_METHOD_OPTIONS,
            "excluded_urls": excluded_urls,
            "preserve_error_tabs": preserve_error_tabs,
            "auto_remember_url_presets": auto_remember_url_presets,
            "route_groups": route_groups,
        }
    except Exception as e:
        logger.error(f"获取标签页列表失败: {e}")
        # 修复：不再伪造 allocation_mode / preserve_error_tabs / route_groups 等持久化配置，
        # 否则前端会把这些默认值当作真实配置展示并在下次保存时写回磁盘，抹掉用户设置。
        return {
            "tabs": [],
            "count": 0,
            "error": str(e),
            "allocation_mode_options": TAB_POOL_ALLOCATION_OPTIONS,
            "route_method_options": TAB_ROUTE_METHOD_OPTIONS,
        }


@router.put("/api/tab-pool/tabs/{tab_index}/model-name")
async def update_tab_model_name(
    tab_index: int,
    body: TabModelNameRequest,
    authenticated: bool = Depends(verify_auth),
):
    """更新标签页暴露给前端模型列表的名称。"""
    if tab_index < 1:
        raise HTTPException(status_code=400, detail="标签页编号必须大于 0")

    model_name = _normalize_model_name(body.model_name)
    persist_scope = str(body.persist_scope or "tab").strip().lower()
    reset = bool(body.reset)
    if persist_scope in {"", "temporary"}:
        persist_scope = "tab"
    if persist_scope not in {"tab", "site", "url"}:
        raise HTTPException(status_code=400, detail="invalid_persist_scope")
    if not reset and not model_name:
        raise HTTPException(status_code=400, detail="模型显示名称不能为空")

    browser = get_browser(auto_connect=False)
    tab_info = _get_tab_info_by_index(browser, tab_index)
    if tab_info is None:
        raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")

    route_key = normalize_route_domain(
        tab_info.get("route_domain") or tab_info.get("current_domain") or ""
    )
    url_key = normalize_exact_tab_url(str(tab_info.get("url") or "").strip())
    pool_synced = False
    saved_scope = ""
    removed_scopes: List[str] = []

    if reset:
        try:
            browser.tab_pool.set_tab_model_name(tab_index, None)
        except Exception as e:
            logger.warning(f"清空标签页临时模型显示名称失败: {e}")

        with _model_name_overrides_lock:
            overrides = _read_model_name_overrides_unlocked()
            if route_key and route_key in overrides["sites"]:
                overrides["sites"].pop(route_key, None)
                removed_scopes.append("site")
            if url_key and url_key in overrides["urls"]:
                overrides["urls"].pop(url_key, None)
                removed_scopes.append("url")
            overrides = _write_model_name_overrides_unlocked(overrides)

        pool_synced = _sync_tab_pool_model_name_overrides(overrides)
    elif persist_scope == "tab":
        result = browser.tab_pool.set_tab_model_name(tab_index, model_name)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "tab_not_found")
    else:
        if persist_scope == "site" and not route_key:
            raise HTTPException(status_code=400, detail="当前标签页无法解析站点域名")
        if persist_scope == "url" and not url_key:
            raise HTTPException(status_code=400, detail="当前标签页无法解析网页 URL")

        with _model_name_overrides_lock:
            overrides = _read_model_name_overrides_unlocked()
            if persist_scope == "site":
                overrides["sites"][route_key] = model_name
            else:
                overrides["urls"][url_key] = model_name
            overrides = _write_model_name_overrides_unlocked(overrides)

        pool_synced = _sync_tab_pool_model_name_overrides(overrides)
        browser.tab_pool.set_tab_model_name(tab_index, None)
        saved_scope = persist_scope

    refreshed_tab = _get_tab_info_by_index(browser, tab_index)
    return {
        "success": True,
        "tab_index": tab_index,
        "tab": refreshed_tab,
        "saved_scope": saved_scope,
        "removed_scopes": removed_scopes,
        "pool_synced": pool_synced,
    }


@router.put("/api/tab-pool/config")
async def update_tab_pool_config(
    body: TabPoolConfigRequest,
    authenticated: bool = Depends(verify_auth)
):
    """更新标签页池运行模式并持久化到 browser_config.json。"""
    request_includes_allocation_mode = body.allocation_mode is not None
    allocation_mode = str(body.allocation_mode or "").strip().lower()
    if request_includes_allocation_mode and allocation_mode not in {"first_idle", "round_robin", "random"}:
        raise HTTPException(status_code=400, detail="invalid_allocation_mode")
    request_includes_enabled_route_methods = body.enabled_route_methods is not None
    enabled_route_methods = _normalize_enabled_route_methods(body.enabled_route_methods)
    request_includes_excluded_urls = body.excluded_urls is not None
    excluded_urls = _normalize_excluded_urls(body.excluded_urls)
    request_includes_preserve_error_tabs = body.preserve_error_tabs is not None
    preserve_error_tabs = _coerce_bool(body.preserve_error_tabs, False)
    request_includes_auto_remember_url_presets = body.auto_remember_url_presets is not None
    auto_remember_url_presets = _coerce_bool(body.auto_remember_url_presets, False)
    request_includes_route_groups = body.route_groups is not None
    route_groups = normalize_route_groups(body.route_groups)

    try:
        with _browser_config_lock:
            config = _read_browser_config()
            tab_pool_config = config.get("tab_pool") or {}
            if not isinstance(tab_pool_config, dict):
                tab_pool_config = {}
            if request_includes_allocation_mode:
                tab_pool_config["allocation_mode"] = allocation_mode
            if request_includes_enabled_route_methods:
                tab_pool_config["enabled_route_methods"] = enabled_route_methods
            if request_includes_excluded_urls:
                tab_pool_config["excluded_urls"] = excluded_urls
            if request_includes_preserve_error_tabs:
                tab_pool_config["preserve_error_tabs"] = preserve_error_tabs
            if request_includes_auto_remember_url_presets:
                tab_pool_config["auto_remember_url_presets"] = auto_remember_url_presets
            if request_includes_route_groups:
                tab_pool_config["route_groups"] = route_groups
            current_excluded_urls = _normalize_excluded_urls(tab_pool_config.get("excluded_urls"))
            current_preserve_error_tabs = _coerce_bool(tab_pool_config.get("preserve_error_tabs"), False)
            current_auto_remember_url_presets = _coerce_bool(tab_pool_config.get("auto_remember_url_presets"), False)
            current_route_groups = normalize_route_groups(tab_pool_config.get("route_groups"))
            current_allocation_mode = _get_config_allocation_mode(tab_pool_config)
            current_enabled_route_methods = _normalize_enabled_route_methods(
                tab_pool_config.get("enabled_route_methods")
            )
            config["tab_pool"] = tab_pool_config
            _write_browser_config_unlocked(config)

        try:
            from app.core.config import BrowserConstants
            if hasattr(BrowserConstants, "reload"):
                BrowserConstants.reload()
        except Exception as reload_error:
            logger.warning(f"热重载浏览器常量失败: {reload_error}")

        pool_synced = False
        try:
            browser = get_browser(auto_connect=False)
            runtime_kwargs: Dict[str, Any] = {
                "allocation_mode": current_allocation_mode,
                "preserve_error_tabs": current_preserve_error_tabs,
                "auto_remember_url_presets": current_auto_remember_url_presets,
            }
            if request_includes_excluded_urls:
                runtime_kwargs["excluded_urls"] = excluded_urls
            if request_includes_route_groups:
                runtime_kwargs["route_groups"] = route_groups
            browser.tab_pool.apply_runtime_config(**runtime_kwargs)
            pool_synced = True
        except Exception as sync_error:
            logger.warning(f"同步运行中标签页池配置失败: {sync_error}")

        return {
            "success": True,
            "message": "标签页池分配模式已更新",
            "allocation_mode": current_allocation_mode,
            "allocation_mode_options": TAB_POOL_ALLOCATION_OPTIONS,
            "enabled_route_methods": current_enabled_route_methods,
            "route_method_options": TAB_ROUTE_METHOD_OPTIONS,
            "excluded_urls": current_excluded_urls,
            "preserve_error_tabs": current_preserve_error_tabs,
            "auto_remember_url_presets": current_auto_remember_url_presets,
            "route_groups": current_route_groups,
            "pool_synced": pool_synced,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新标签页池配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class PresetRequest(BaseModel):
    """预设操作请求"""
    preset_name: str = Field(..., min_length=1, max_length=50)


class TerminateTabRequest(BaseModel):
    """终止标签页当前任务请求"""
    reason: str = Field(default="manual_terminate_from_tab_pool", max_length=120)
    clear_page: bool = Field(default=True)
    scope: str = Field(default="task", max_length=20)
    expected_session_id: str = Field(default="", max_length=200)
    expected_task_id: str = Field(default="", max_length=200)


@router.put("/api/tab-pool/tabs/{tab_index}/preset")
async def set_tab_preset(
    tab_index: int,
    body: PresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """为指定标签页设置预设"""
    try:
        browser = get_browser(auto_connect=False)
        tab_info = _get_tab_info_by_index(browser, tab_index)
        if tab_info is None:
            raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")

        preset_value = None if body.preset_name == FOLLOW_DEFAULT_PRESET else body.preset_name

        domain = str(tab_info.get("current_domain") or tab_info.get("route_domain") or "").strip()
        if preset_value:
            try:
                from app.services.config_engine import config_engine
                available = config_engine.list_presets(domain)
                if available and preset_value not in available:
                    # 尝试别名容错
                    aliased = [p for p in available if p == f"预设_{preset_value}" or (preset_value.startswith("预设_") and p == preset_value[3:])]
                    if aliased:
                        preset_value = aliased[0]
                    else:
                        raise HTTPException(status_code=400, detail=f"预设 '{preset_value}' 在站点 '{domain}' 中不存在")
            except HTTPException:
                raise
            except Exception as e:
                logger.debug(f"校验站点预设可用性异常（已忽略）: {e}")

        success = browser.tab_pool.set_tab_preset(tab_index, preset_value)
        if not success:
            raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")

        # 检查是否开启了按 URL 自动记忆预设（需过滤空白页及非网页）
        auto_remember = _get_tab_pool_auto_remember_url_presets(browser.tab_pool)
        tab_url = str(tab_info.get("url") or "").strip()
        url_key = normalize_exact_tab_url(tab_url)
        is_valid_web_url = bool(url_key and not _should_skip_pool_url(tab_url) and extract_remote_site_domain(tab_url))

        if auto_remember and is_valid_web_url:
            with _preset_overrides_lock:
                overrides = _read_preset_overrides_unlocked()
                if preset_value:
                    overrides["urls"][url_key] = preset_value
                else:
                    overrides["urls"].pop(url_key, None)
                overrides = _write_preset_overrides_unlocked(overrides)
            _sync_tab_pool_preset_overrides(overrides)

        preset_label = "跟随站点默认预设" if preset_value is None else body.preset_name
        return {"success": True, "message": f"标签页 #{tab_index} 已切换到预设: {preset_label}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置标签页预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tab-pool/tabs/{tab_index}/terminate")
async def terminate_tab_task(
    tab_index: int,
    body: TerminateTabRequest,
    authenticated: bool = Depends(verify_auth)
):
    """按标签页编号终止当前任务并释放占用。"""
    if tab_index < 1:
        raise HTTPException(status_code=400, detail="标签页编号必须大于 0")

    try:
        browser = get_browser(auto_connect=False)
        result = await asyncio.to_thread(
            browser.tab_pool.terminate_by_index,
            tab_index,
            reason=(body.reason or "manual_terminate_from_tab_pool"),
            clear_page=bool(body.clear_page),
            scope=(body.scope or "task"),
            expected_session_id=body.expected_session_id,
            expected_task_id=body.expected_task_id,
        )
        if not result.get("ok"):
            if result.get("error") == "tab_not_found":
                raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")
            if result.get("error") in {
                "session_ownership_changed",
                "task_ownership_changed",
            }:
                raise HTTPException(
                    status_code=409,
                    detail="标签页任务已经变化，请刷新后重试",
                )
            raise HTTPException(status_code=400, detail=result.get("error", "terminate_failed"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"终止标签页任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ================= 标签页池状态（原 app/api/chat.py） =================

@router.get("/api/pool/status")
async def get_pool_status(authenticated: bool = Depends(verify_dashboard_auth)):
    """获取标签页池状态"""
    try:
        browser = get_browser(auto_connect=False)
        return browser.get_pool_status()
    except Exception as e:
        return {"error": str(e), "initialized": False}
