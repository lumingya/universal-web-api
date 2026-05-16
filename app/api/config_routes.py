"""
app/api/config_routes.py - 配置管理 API

职责：
- 站点配置 CRUD
- 提取器管理
- 图片配置与预设
- 工作流编辑器
- 元素定义
"""

import copy
import json
import os
import subprocess
import time
from typing import Optional, Dict, Any, Callable

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.config import AppConfig, get_logger
from app.core import get_browser, BrowserConnectionError
from app.core.request_transport import get_request_transport_defaults_payload
from app.core.workflow import WorkflowExecutor
from app.core.workflow_editor import workflow_editor_injector
from app.models.schemas import get_default_image_extraction_config, get_default_selector_definitions
from app.services.config.engine import get_default_stream_config, get_default_network_config
from app.services.config_engine import config_engine, ConfigConstants
from app.services.extractor_manager import extractor_manager
from app.utils.site_url import extract_remote_site_domain
from app.utils.similarity import verify_extraction

logger = get_logger("API.CONFIG")

router = APIRouter()


def _notify_workflow_editor_action_result(tab, action_id: str, success: bool, message: str) -> None:
    """将测试结果回推给已注入的可视化编辑器页面。"""
    try:
        tab.run_js(
            """
            return (function(actionId, ok, text) {
              if (window.WorkflowEditor && typeof window.WorkflowEditor.handleBackendResult === 'function') {
                window.WorkflowEditor.handleBackendResult(actionId, ok, text);
                return true;
              }
              return false;
            })(arguments[0], arguments[1], arguments[2]);
            """,
            str(action_id or ""),
            bool(success),
            str(message or ""),
        )
    except Exception as e:
        logger.debug(f"回推编辑器测试结果失败（忽略）: {e}")


def _notify_workflow_editor_action_status(tab, action_id: str, phase: str, message: str) -> None:
    """将测试中间状态回推给已注入的可视化编辑器页面。"""
    try:
        tab.run_js(
            """
            return (function(actionId, phaseName, text) {
              if (window.WorkflowEditor && typeof window.WorkflowEditor.handleBackendStatus === 'function') {
                window.WorkflowEditor.handleBackendStatus(actionId, phaseName, text);
                return true;
              }
              return false;
            })(arguments[0], arguments[1], arguments[2]);
            """,
            str(action_id or ""),
            str(phase or ""),
            str(message or ""),
        )
    except Exception as e:
        logger.debug(f"回推编辑器测试状态失败（忽略）: {e}")


def _wake_workflow_editor_test_tab(session) -> None:
    """在测试前尽量唤醒后台标签页，避免页面被冻结导致点击无效。"""
    if session is None:
        return

    try:
        if hasattr(session, "activate"):
            session.activate()
    except Exception:
        pass

    focus_emulation_set = False
    try:
        session.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=True)
        focus_emulation_set = True
    except Exception:
        pass

    try:
        session.tab.run_cdp("Page.setWebLifecycleState", state="active")
    except Exception:
        pass

    try:
        session.tab.run_js("return document.readyState || '';")
    except Exception:
        pass
    finally:
        if focus_emulation_set:
            try:
                session.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=False)
            except Exception:
                pass


def _execute_workflow_editor_test_payload(
    browser_instance,
    data: Dict[str, Any],
    progress_callback: Optional[Callable[[str, str], None]] = None
) -> Dict[str, Any]:
    """复用真实执行器执行可视化编辑器测试。"""

    action_labels = {
        "CLICK": "点击元素",
        "COORD_CLICK": "坐标点击",
        "COORD_SCROLL": "模拟滑动",
        "FILL_INPUT": "填入内容",
        "STREAM_WAIT": "流式等待",
        "WAIT": "等待",
        "KEY_PRESS": "按键",
        "JS_EXEC": "执行脚本",
        "PAGE_FETCH": "页面直发",
    }

    domain = str(data.get("domain") or "").strip()
    tab_id = str(data.get("tab_id") or "").strip()
    workflow = data.get("workflow") or []
    selectors = data.get("selectors") or {}
    preset_name = str(data.get("preset_name") or "").strip() or None
    prompt_text = str(data.get("prompt") or "")
    stealth = bool(data.get("stealth", False))
    stream_config = dict(data.get("stream_config") or {})
    image_config = data.get("image_extraction") or {}
    file_paste_config = data.get("file_paste") or {}

    if not domain:
        raise HTTPException(status_code=400, detail="缺少 domain")
    if not isinstance(workflow, list) or not workflow:
        raise HTTPException(status_code=400, detail="workflow 必须是非空数组")
    if not isinstance(selectors, dict):
        raise HTTPException(status_code=400, detail="selectors 必须是对象")

    if not browser_instance.get_browser_handle():
        raise HTTPException(status_code=503, detail="浏览器未连接")

    task_id = f"workflow_editor_test_{int(time.time() * 1000)}"
    session = None
    started_at = time.time()

    try:
        if tab_id and getattr(browser_instance, "tab_pool", None) is not None:
            session = browser_instance.tab_pool.acquire_by_raw_tab_id(
                tab_id,
                task_id,
                timeout=5,
                count_request=False,
            )

        tab = session.tab if session is not None else (
            browser_instance.get_tab(tab_id) if tab_id else browser_instance.get_latest_tab()
        )

        logger.debug(
            "[WFE_TEST] tab resolved "
            f"session={getattr(session, 'id', None)!r} "
            f"raw_tab_id={getattr(tab, 'tab_id', None)!r} "
            f"url={str(getattr(tab, 'url', '') or '')[:160]!r}"
        )

        test_stream_config = dict(stream_config or {})
        test_network_config = dict(test_stream_config.get("network") or {})
        test_stream_config["hard_timeout"] = min(
            float(test_stream_config.get("hard_timeout", 45) or 45),
            45.0,
        )
        test_network_config["first_response_timeout"] = min(
            float(test_network_config.get("first_response_timeout", 12) or 12),
            12.0,
        )
        test_network_config["silence_threshold"] = min(
            float(test_network_config.get("silence_threshold", 3) or 3),
            3.0,
        )
        test_stream_config["network"] = test_network_config

        logger.debug(
            "[WFE_TEST] start "
            f"domain={domain!r} tab_id={tab_id!r} preset={preset_name!r} "
            f"steps={len(workflow)} selectors={len(selectors)} "
            f"hard_timeout={test_stream_config['hard_timeout']:.1f}s "
            f"first_response_timeout={test_network_config['first_response_timeout']:.1f}s "
            f"silence_threshold={test_network_config['silence_threshold']:.1f}s"
        )

        if progress_callback:
            progress_callback("running", f"本地控制台已接管，准备执行 {len(workflow)} 个动作")

        _wake_workflow_editor_test_tab(session)

        url = str(getattr(tab, "url", "") or "")
        actual_domain = extract_remote_site_domain(url)
        if not actual_domain:
            raise HTTPException(status_code=400, detail="当前页面不是可解析的网站")
        if actual_domain != domain:
            raise HTTPException(
                status_code=400,
                detail=f"域名不匹配：当前页面是 {actual_domain}，测试目标是 {domain}"
            )

        resolved_site_config = config_engine.get_site_config(
            domain,
            getattr(tab, "html", "") or "",
            preset_name=preset_name
        ) or {}

        extractor = config_engine.get_site_extractor(domain, preset_name=preset_name)
        executor = WorkflowExecutor(
            tab=tab,
            stealth_mode=stealth,
            should_stop_checker=lambda: False,
            extractor=extractor,
            image_config=image_config,
            stream_config=test_stream_config or resolved_site_config.get("stream_config") or {},
            file_paste_config=file_paste_config,
            selectors=selectors,
            session=session,
        )

        executed = 0
        context = {
            "prompt": prompt_text,
            "images": [],
        }

        step_index = 0
        while step_index < len(workflow):
            step = workflow[step_index]
            action = str(step.get("action") or "").strip()
            target_key = str(step.get("target") or "")
            optional = bool(step.get("optional", False))
            value = step.get("value")
            selector = selectors.get(target_key, "")
            current_index = step_index + 1

            logger.debug(
                "[WFE_TEST] step "
                f"index={current_index} action={action!r} target={target_key!r} "
                f"selector={selector!r} optional={optional}"
            )

            if progress_callback:
                progress_callback(
                    "step",
                    f"执行 {current_index}/{len(workflow)} · {action_labels.get(action, action)}"
                )

            if not action:
                raise HTTPException(status_code=400, detail="workflow 中存在缺少 action 的步骤")

            if action == "FILL_INPUT" and value is not None:
                context["prompt"] = str(value)

            for _ in executor.execute_step(
                action=action,
                selector=selector,
                target_key=target_key,
                value=value,
                optional=optional,
                context=context
            ):
                pass

            executed += 1
            if (
                action == "PAGE_FETCH"
                and hasattr(executor, "consume_last_request_transport_sent")
                and executor.consume_last_request_transport_sent()
            ):
                step_index = executor._consume_request_transport_followup_steps(
                    workflow,
                    step_index,
                )
            step_index += 1

        logger.debug(
            "[WFE_TEST] done "
            f"domain={domain!r} tab_id={tab_id or str(getattr(tab, 'tab_id', '') or '')!r} "
            f"executed_steps={executed} duration={time.time() - started_at:.2f}s"
        )

        return {
            "success": True,
            "message": f"已测试 {executed} 个步骤",
            "domain": domain,
            "tab_id": tab_id or str(getattr(tab, "tab_id", "") or ""),
            "preset_name": preset_name or config_engine.get_default_preset(domain) or "主预设",
            "executed_steps": executed,
            "_tab_ref": tab,
        }
    finally:
        if session is not None and getattr(browser_instance, "tab_pool", None) is not None:
            logger.debug(f"[WFE_TEST] release session={session.id!r}")
            browser_instance.tab_pool.release(session.id, check_triggers=False)


def _save_site_workflow_payload(domain: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """保存工作流配置，供直接 API 和桥接模式复用。"""
    new_workflow = data.get("workflow")
    new_selectors = data.get("selectors")
    preset_name = data.get("preset_name")

    if new_workflow is None:
        raise HTTPException(status_code=400, detail="缺少 workflow 字段")

    if not isinstance(new_workflow, list):
        raise HTTPException(status_code=400, detail="workflow 必须是数组")

    if new_selectors is not None and (
        not isinstance(new_selectors, dict) or isinstance(new_selectors, list)
    ):
        raise HTTPException(status_code=400, detail="selectors 必须是对象")

    config_engine.refresh_if_changed()
    if domain not in config_engine.sites:
        raise HTTPException(status_code=404, detail=f"站点不存在: {domain}")

    site = config_engine.sites[domain]
    presets = site.get("presets", {})
    resolved_preset_name = None
    if preset_name:
        resolved_preset_name = config_engine._resolve_preset_alias_key(preset_name, presets)
        if resolved_preset_name not in presets:
            raise HTTPException(status_code=404, detail=f"预设不存在: {preset_name}")

    preset_data = config_engine._get_site_data(domain, resolved_preset_name)
    if preset_data is None:
        raise HTTPException(status_code=404, detail="站点或预设不存在")

    preset_data["workflow"] = new_workflow
    if new_selectors is not None:
        preset_data["selectors"] = new_selectors

    success = config_engine.save_config()
    if not success:
        raise HTTPException(status_code=500, detail="保存配置文件失败")

    used_preset = (
        resolved_preset_name
        or config_engine.get_default_preset(domain)
        or "主预设"
    )
    logger.info(f"站点 {domain} [{used_preset}] 工作流已更新: {len(new_workflow)} 个步骤")

    return {
        "status": "success",
        "message": f"工作流已保存",
        "domain": domain,
        "preset_name": used_preset,
        "steps_count": len(new_workflow)
    }


# ================= 请求模型 =================

class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    config: Dict[str, Any] = Field(...)


class SiteAdvancedConfigRequest(BaseModel):
    """站点级高级配置更新请求。"""
    independent_cookies: bool = Field(default=False)
    independent_cookies_auto_takeover: bool = Field(default=False)


class PresetConfigUpdateRequest(BaseModel):
    """单个预设完整配置更新请求。"""
    preset_name: Optional[str] = Field(default=None)
    config: Dict[str, Any] = Field(...)


def _normalize_preset_config_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验并规范化单个预设配置对象。"""
    if not isinstance(payload, dict) or isinstance(payload, list):
        raise HTTPException(status_code=400, detail="config 必须是对象")

    reserved_site_fields = {"presets", "default_preset", "advanced"}
    invalid_fields = [key for key in reserved_site_fields if key in payload]
    if invalid_fields:
        joined = ", ".join(invalid_fields)
        raise HTTPException(
            status_code=400,
            detail=f"这里只接受单个预设配置对象，不能包含站点级字段: {joined}",
        )

    normalized = copy.deepcopy(payload)

    selectors = normalized.get("selectors")
    if selectors is None:
        normalized["selectors"] = {}
    elif not isinstance(selectors, dict) or isinstance(selectors, list):
        raise HTTPException(status_code=400, detail="selectors 必须是对象")

    workflow = normalized.get("workflow")
    if workflow is None:
        normalized["workflow"] = []
    elif not isinstance(workflow, list):
        raise HTTPException(status_code=400, detail="workflow 必须是数组")

    normalized["stealth"] = bool(normalized.get("stealth", False))
    return normalized


def _load_git_branch_sites_config(branch_name: str = "main") -> Dict[str, Any]:
    """从 Git 分支中读取 config/sites.json 的已提交版本。"""
    project_root = getattr(ConfigConstants, "_PROJECT_ROOT", "") or os.getcwd()
    relative_config_path = os.path.relpath(
        ConfigConstants.CONFIG_FILE,
        project_root
    ).replace("\\", "/")

    try:
        result = subprocess.run(
            ["git", "show", f"{branch_name}:{relative_config_path}"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        stderr = str(exc.stderr or "").strip()
        detail = stderr or f"无法读取分支 {branch_name} 中的 {relative_config_path}"
        raise HTTPException(status_code=404, detail=detail)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="系统未找到 git 命令")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"{branch_name} 分支中的 {relative_config_path} 不是合法 JSON: {exc}"
        )

    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail=f"{relative_config_path} 顶层必须是对象")

    return {
        "path": relative_config_path,
        "sites": {
            key: value
            for key, value in payload.items()
            if not str(key).startswith("_")
        }
    }


def _resolve_branch_preset_config(
    site_config: Dict[str, Any],
    requested_preset_name: Optional[str] = None,
) -> Dict[str, Any]:
    """从分支中的站点配置里解析最合适的预设。"""
    if not isinstance(site_config, dict):
        raise HTTPException(status_code=500, detail="站点配置格式无效")

    presets = site_config.get("presets")
    if not isinstance(presets, dict) or not presets:
        return {
            "preset_name": str(requested_preset_name or "主预设").strip() or "主预设",
            "config": _normalize_preset_config_payload(site_config),
            "match_mode": "legacy_flat",
        }

    requested = str(requested_preset_name or "").strip()
    if requested:
        resolved = config_engine._resolve_preset_alias_key(requested, presets)
        if resolved in presets:
            return {
                "preset_name": resolved,
                "config": _normalize_preset_config_payload(presets[resolved]),
                "match_mode": "exact",
            }

    default_preset = str(site_config.get("default_preset") or "").strip()
    if default_preset in presets:
        return {
            "preset_name": default_preset,
            "config": _normalize_preset_config_payload(presets[default_preset]),
            "match_mode": "default",
        }

    if "主预设" in presets:
        return {
            "preset_name": "主预设",
            "config": _normalize_preset_config_payload(presets["主预设"]),
            "match_mode": "main_preset",
        }

    first_key = next(iter(presets))
    return {
        "preset_name": first_key,
        "config": _normalize_preset_config_payload(presets[first_key]),
        "match_mode": "first",
    }


_PRESET_COMPARE_FIELD_ORDER = [
    "selectors",
    "workflow",
    "stream_config",
    "image_extraction",
    "file_paste",
    "stealth",
    "extractor_id",
    "extractor_verified",
]

_PRESET_COMPARE_FIELD_LABELS = {
    "selectors": "选择器",
    "workflow": "工作流",
    "stream_config": "流式配置",
    "image_extraction": "图片提取",
    "file_paste": "文件粘贴",
    "stealth": "隐身模式",
    "extractor_id": "提取器",
    "extractor_verified": "提取器验证",
}


def _stable_compare_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _extract_site_presets_for_compare(site_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(site_config, dict):
        return {}

    presets = site_config.get("presets")
    if isinstance(presets, dict) and presets:
        normalized = {}
        for preset_name, preset_config in presets.items():
            if not isinstance(preset_config, dict):
                continue
            normalized[str(preset_name)] = _normalize_preset_config_payload(preset_config)
        if normalized:
            return normalized

    try:
        fallback_name = str(site_config.get("default_preset") or "主预设").strip() or "主预设"
        return {
            fallback_name: _normalize_preset_config_payload(site_config)
        }
    except HTTPException:
        return {}


def _get_preset_compare_keys(local_config: Dict[str, Any], main_config: Dict[str, Any]) -> list[str]:
    remaining = set(local_config.keys()) | set(main_config.keys())
    ordered = []

    for key in _PRESET_COMPARE_FIELD_ORDER:
        if key in remaining:
            ordered.append(key)
            remaining.remove(key)

    ordered.extend(sorted(remaining, key=lambda item: str(item)))
    return ordered


def _collect_preset_different_fields(local_config: Dict[str, Any], main_config: Dict[str, Any]) -> list[str]:
    different_fields = []
    for key in _get_preset_compare_keys(local_config, main_config):
        local_has = key in local_config
        main_has = key in main_config
        if not local_has or not main_has:
            different_fields.append(key)
            continue
        if _stable_compare_dump(local_config[key]) != _stable_compare_dump(main_config[key]):
            different_fields.append(key)
    return different_fields


def _build_main_branch_compare_summary() -> Dict[str, Any]:
    config_engine.refresh_if_changed()
    branch_payload = _load_git_branch_sites_config("main")
    local_sites = {
        key: value
        for key, value in config_engine.sites.items()
        if not str(key).startswith("_") and isinstance(value, dict)
    }
    main_sites = branch_payload["sites"]

    items = []
    counts = {
        "same": 0,
        "different": 0,
        "local_only_preset": 0,
        "local_only_site": 0,
        "main_only_preset": 0,
        "main_only_site": 0,
    }

    for domain in sorted(local_sites.keys(), key=lambda item: str(item)):
        local_site = local_sites[domain]
        local_presets = _extract_site_presets_for_compare(local_site)
        main_site = main_sites.get(domain)
        main_presets = _extract_site_presets_for_compare(main_site) if isinstance(main_site, dict) else {}
        matched_main_presets = set()

        for local_preset_name in sorted(local_presets.keys(), key=lambda item: str(item)):
            local_preset_config = local_presets[local_preset_name]
            item = {
                "domain": domain,
                "local_preset_name": local_preset_name,
                "main_preset_name": "",
                "local_exists": True,
                "main_exists": bool(main_site),
                "match_mode": "",
                "different_fields": [],
                "different_field_labels": [],
                "difference_count": 0,
                "detail_available": True,
                "summary_text": "",
                "status": "same",
            }

            if not main_site:
                item["status"] = "local_only_site"
                item["difference_count"] = 1
                item["summary_text"] = "main 分支中没有这个站点"
                counts["local_only_site"] += 1
                items.append(item)
                continue

            resolved_main_preset_name = config_engine._resolve_preset_alias_key(local_preset_name, main_presets)
            if resolved_main_preset_name not in main_presets:
                item["status"] = "local_only_preset"
                item["difference_count"] = 1
                item["summary_text"] = "main 分支中没有同名预设"
                counts["local_only_preset"] += 1
                items.append(item)
                continue

            matched_main_presets.add(resolved_main_preset_name)
            main_preset_config = main_presets[resolved_main_preset_name]
            different_fields = _collect_preset_different_fields(local_preset_config, main_preset_config)

            item["main_preset_name"] = resolved_main_preset_name
            item["match_mode"] = "exact" if resolved_main_preset_name == local_preset_name else "alias"
            item["different_fields"] = different_fields
            item["different_field_labels"] = [
                _PRESET_COMPARE_FIELD_LABELS.get(field, field)
                for field in different_fields
            ]
            item["difference_count"] = len(different_fields)

            if different_fields:
                item["status"] = "different"
                item["summary_text"] = f"{len(different_fields)} 项字段与官方预设不同"
                counts["different"] += 1
            else:
                item["status"] = "same"
                item["summary_text"] = "与官方预设一致"
                counts["same"] += 1

            items.append(item)

        for main_preset_name in sorted(main_presets.keys(), key=lambda item: str(item)):
            if main_preset_name in matched_main_presets:
                continue
            counts["main_only_preset"] += 1

    for domain in sorted(set(main_sites.keys()) - set(local_sites.keys()), key=lambda item: str(item)):
        main_presets = _extract_site_presets_for_compare(main_sites.get(domain))
        counts["main_only_site"] += max(1, len(main_presets))

    status_priority = {
        "different": 0,
        "local_only_preset": 1,
        "local_only_site": 2,
        "same": 3,
    }
    items.sort(
        key=lambda item: (
            status_priority.get(str(item.get("status") or ""), 99),
            str(item.get("domain") or ""),
            str(item.get("local_preset_name") or ""),
        )
    )

    return {
        "branch": "main",
        "path": branch_payload["path"],
        "counts": counts,
        "items": items,
    }


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
        config_engine._apply_local_site_overrides()
        
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
        return copy.deepcopy(config_engine.sites[domain])


@router.put("/api/sites/{domain}/preset-config")
async def set_site_preset_config(
    domain: str,
    body: PresetConfigUpdateRequest,
    authenticated: bool = Depends(verify_auth)
):
    """保存单个站点预设的完整配置。"""
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

    normalized = _normalize_preset_config_payload(body.config)
    presets[resolved_preset] = normalized
    site["presets"] = presets

    success = config_engine.save_config()
    if not success:
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

    resolved = _resolve_branch_preset_config(sites[domain], preset_name)
    return {
        "branch": "main",
        "path": branch_payload["path"],
        "domain": domain,
        "requested_preset_name": str(preset_name or "").strip(),
        "preset_name": resolved["preset_name"],
        "match_mode": resolved["match_mode"],
        "config": resolved["config"],
    }


@router.get("/api/sites/{domain}/advanced-config")
async def get_site_advanced_config(
    domain: str,
    authenticated: bool = Depends(verify_auth)
):
    """获取站点级高级配置。"""
    if domain not in config_engine.sites:
        raise HTTPException(status_code=404, detail=f"配置不存在: {domain}")

    try:
        config = config_engine.get_site_advanced_config(domain)
        return {
            "domain": domain,
            "advanced": config,
        }
    except Exception as e:
        logger.error(f"获取站点高级配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/sites/{domain}/advanced-config")
async def set_site_advanced_config(
    domain: str,
    body: SiteAdvancedConfigRequest,
    authenticated: bool = Depends(verify_auth)
):
    """更新站点级高级配置。"""
    if domain not in config_engine.sites:
        raise HTTPException(status_code=404, detail=f"配置不存在: {domain}")

    payload = {
        "independent_cookies": bool(body.independent_cookies),
        "independent_cookies_auto_takeover": bool(body.independent_cookies_auto_takeover),
    }

    try:
        success = config_engine.set_site_advanced_config(domain, payload)
        if not success:
            raise HTTPException(status_code=500, detail="高级配置保存失败")

        return {
            "status": "success",
            "message": f"站点 {domain} 高级配置已更新",
            "domain": domain,
            "advanced": config_engine.get_site_advanced_config(domain),
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
    """设置站点的多模态提取配置"""
    try:
        data = await request.json()
        preset_name = data.pop("preset_name", None)
        
        success = config_engine.set_site_image_config(domain, data, preset_name=preset_name)
        
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

    try:
        data = await request.json()
        logger.debug(f"[WFE_TEST] direct api request keys={sorted(list(data.keys()))}")
        browser_instance = get_browser(auto_connect=True)
        result = _execute_workflow_editor_test_payload(browser_instance, data)
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
        data = await request.json()
        return _save_site_workflow_payload(domain, data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新工作流失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/workflow-editor/clear-cache")
async def clear_editor_cache():
    """清除编辑器脚本缓存（开发调试用）"""
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
