"""Workflow-editor helpers for config routes."""

import copy
import time
from typing import Optional, Dict, Any, Callable, get_args

from fastapi import HTTPException

from app.core.config import get_logger
from app.core.page_lifecycle import BACKGROUND_WAKE_CDP_TIMEOUT, BACKGROUND_WAKE_JS_TIMEOUT
from app.models.schemas import ActionType
from app.services.config_engine import config_engine
from app.utils.site_url import extract_remote_site_domain

logger = get_logger('API.CONFIG.WORKFLOW')

# 修复：保存时校验动作名。合法集合直接取自 schemas.ActionType；
# 运行期 executor 的 dispatch 用原始 action 精确匹配大写字面量、不做归一化，故此处也要求精确大写。
VALID_WORKFLOW_ACTIONS = frozenset(get_args(ActionType))

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
        session.tab.run_cdp(
            "Emulation.setFocusEmulationEnabled",
            enabled=True,
            _timeout=BACKGROUND_WAKE_CDP_TIMEOUT,
        )
        focus_emulation_set = True
    except Exception:
        pass

    try:
        session.tab.run_cdp(
            "Page.setWebLifecycleState",
            state="active",
            _timeout=BACKGROUND_WAKE_CDP_TIMEOUT,
        )
    except Exception:
        pass

    try:
        session.tab.run_js("return document.readyState || '';", timeout=BACKGROUND_WAKE_JS_TIMEOUT)
    except Exception:
        pass
    finally:
        if focus_emulation_set:
            try:
                session.tab.run_cdp(
                    "Emulation.setFocusEmulationEnabled",
                    enabled=False,
                    _timeout=BACKGROUND_WAKE_CDP_TIMEOUT,
                )
            except Exception:
                pass


def _execute_workflow_editor_test_payload(
    browser_instance,
    data: Dict[str, Any],
    progress_callback: Optional[Callable[[str, str], None]] = None
) -> Dict[str, Any]:
    """复用真实执行器执行可视化编辑器测试。"""
    from app.core.workflow.executor import WorkflowExecutor

    action_labels = {
        "CLICK": "点击元素",
        "SELECT_MODEL": "选择请求模型",
        "COORD_CLICK": "坐标点击",
        "COORD_SCROLL": "模拟滑动",
        "FILL_INPUT": "填入内容",
        "STREAM_WAIT": "流式等待",
        # 修复：补上缺失的两项，否则测试进度提示会退化成显示英文原始 action 名
        "STREAM_OUTPUT": "流式等待",
        "WAIT": "等待",
        "KEY_PRESS": "按键",
        "JS_EXEC": "执行脚本",
        "PAGE_FETCH": "页面直发",
        "READONLY_HINT": "只读提示",
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

    task_id = f"workflow_editor_test_{time.time_ns()}"
    session = None
    executor = None
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
        test_network_config["silence_threshold"] = max(
            0.5,
            min(float(test_network_config.get("silence_threshold", 3) or 3), 30.0),
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
            html_content=lambda: getattr(tab, "html", ""),
            preset_name=preset_name,
        ) or {}
        site_advanced_config = config_engine.get_site_advanced_config(
            domain,
            preset_name=preset_name,
        )

        extractor = config_engine.get_site_extractor(domain, preset_name=preset_name)
        executor = WorkflowExecutor(
            tab=tab,
            stealth_mode=stealth,
            should_stop_checker=lambda: False,
            extractor=extractor,
            image_config=image_config,
            stream_config=test_stream_config or resolved_site_config.get("stream_config") or {},
            file_paste_config=file_paste_config,
            site_advanced_config=site_advanced_config,
            selectors=selectors,
            session=session,
        )

        executed = 0
        context = {
            "prompt": prompt_text,
            "images": [],
        }
        # 修复：此前不透传 model/model_catalog，导致 SELECT_MODEL 步骤在编辑器测试里必然 early return，
        # 但仍计入 executed_steps，测试显示“成功”而实际未执行。
        requested_model = str((data or {}).get("model") or "").strip()
        if requested_model:
            context["model"] = requested_model
        model_catalog = (data or {}).get("model_catalog")
        if isinstance(model_catalog, dict) and model_catalog:
            context["model_catalog"] = model_catalog
        skipped_select_model = 0

        with executor.workflow_execution_scope():
            step_index = 0
            while step_index < len(workflow):
                step = workflow[step_index]
                action = str(step.get("action") or "").strip()
                target_key = str(step.get("target") or "")
                optional = bool(step.get("optional", False))
                value = step.get("value")
                execution_policy = step.get("execution")
                selector = selectors.get(target_key, "")
                current_index = step_index + 1

                logger.debug(
                    "[WFE_TEST] step "
                    f"index={current_index} action={action!r} target={target_key!r} "
                    f"selector={selector!r} optional={optional}"
                )

                if not action:
                    raise HTTPException(status_code=400, detail="workflow 中存在缺少 action 的步骤")

                # 修复：未指定模型时 SELECT_MODEL 必然 early return（executor_actions.py:1284-1289），
                # 此前仍会计入 executed_steps，让"已测试 N 个步骤"虚高、掩盖空跑。
                # 注意：这是 while 循环，跳过前必须自行推进 step_index，否则死循环。
                if action == "SELECT_MODEL" and not requested_model:
                    skipped_select_model += 1
                    if progress_callback:
                        progress_callback(
                            "step",
                            f"跳过 {current_index}/{len(workflow)} · 选择请求模型（未指定测试模型名）"
                        )
                    step_index += 1
                    continue

                if progress_callback:
                    progress_callback(
                        "step",
                        f"执行 {current_index}/{len(workflow)} · {action_labels.get(action, action)}"
                    )

                if action == "FILL_INPUT" and value is not None:
                    context["prompt"] = str(value)

                for _ in executor.execute_step(
                    action=action,
                    selector=selector,
                    target_key=target_key,
                    value=value,
                    optional=optional,
                    context=context,
                    execution=execution_policy,
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

        # 修复：把被跳过的 SELECT_MODEL 步骤如实告知前端，避免“已测试 N 个步骤”掩盖空跑
        message = f"已测试 {executed} 个步骤"
        if skipped_select_model:
            message += f"（其中 {skipped_select_model} 个选择模型步骤已跳过：未指定测试模型名）"

        return {
            "success": True,
            "message": message,
            "domain": domain,
            "tab_id": tab_id or str(getattr(tab, "tab_id", "") or ""),
            "preset_name": preset_name or config_engine.get_default_preset(domain) or "主预设",
            "executed_steps": executed,
            "skipped_select_model_steps": skipped_select_model,
            "_tab_ref": tab,
        }
    finally:
        if executor is not None:
            try:
                executor.cleanup_after_workflow()
            except Exception as e:
                logger.debug(f"[WFE_TEST] executor cleanup skipped: {e}")
        if session is not None and getattr(browser_instance, "tab_pool", None) is not None:
            logger.debug(f"[WFE_TEST] release session={session.id!r}")
            browser_instance.tab_pool.release(
                session.id,
                check_triggers=False,
                expected_task_id=task_id,
            )


def _save_site_workflow_payload(domain: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """保存工作流配置，供直接 API 和桥接模式复用。"""
    new_workflow = data.get("workflow")
    new_selectors = data.get("selectors")
    preset_name = data.get("preset_name")

    if new_workflow is None:
        raise HTTPException(status_code=400, detail="缺少 workflow 字段")

    if not isinstance(new_workflow, list):
        raise HTTPException(status_code=400, detail="workflow 必须是数组")

    # 修复：此前不校验动作名，拼错（如 STREAM_WIAT）或小写（click）会静默入库，
    # 运行期只在 debug 日志里记一行“未知动作”，表现为工作流跑完但什么都没发生。
    for i, step in enumerate(new_workflow):
        if not isinstance(step, dict):
            raise HTTPException(status_code=400, detail=f"第 {i + 1} 步必须是对象")
        action = step.get("action")
        if not isinstance(action, str) or action not in VALID_WORKFLOW_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"第 {i + 1} 步的动作类型无效: {action}"
                    f"（动作名必须大写，可选值：{'、'.join(sorted(VALID_WORKFLOW_ACTIONS))}）"
                ),
            )

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

    workflow_existed = "workflow" in preset_data
    previous_workflow = copy.deepcopy(preset_data.get("workflow"))
    selectors_existed = "selectors" in preset_data
    previous_selectors = copy.deepcopy(preset_data.get("selectors"))

    preset_data["workflow"] = new_workflow
    if new_selectors is not None:
        preset_data["selectors"] = new_selectors

    def rollback_workflow_memory() -> None:
        if workflow_existed:
            preset_data["workflow"] = previous_workflow
        else:
            preset_data.pop("workflow", None)
        if new_selectors is not None:
            if selectors_existed:
                preset_data["selectors"] = previous_selectors
            else:
                preset_data.pop("selectors", None)

    try:
        success = config_engine.save_config()
    except Exception as exc:
        rollback_workflow_memory()
        raise HTTPException(status_code=500, detail=f"保存配置文件失败: {exc}") from exc

    if not success:
        rollback_workflow_memory()
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


def _scan_workflow_scripts() -> Dict[str, Any]:
    """扫描可用工作流脚本列表"""
    from app.core.workflow.script_loader import script_loader
    scripts = script_loader.scan_available_scripts()
    return {"scripts": scripts}


def _load_workflow_script_content(target_path: str) -> Dict[str, Any]:
    """加载指定脚本的内容与元数据（用于前端预览）"""
    from app.core.workflow.script_loader import script_loader
    from app.core.config import WorkflowError

    if not target_path:
        raise HTTPException(status_code=400, detail="缺少脚本路径参数")

    try:
        resolved_path = script_loader.resolve_script_path(target_path)
        content = script_loader.load_script_content(resolved_path)
        stat = resolved_path.stat()
        mtime = int(stat.st_mtime)
        desc = script_loader.extract_script_description(content, resolved_path.name)
        rel_path = resolved_path.relative_to(script_loader.base_dir).as_posix()
        return {
            "path": rel_path,
            "uri": f"file://{rel_path}",
            "name": resolved_path.name,
            "description": desc,
            "content": content,
            "mtime": mtime,
        }
    except WorkflowError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"读取脚本内容失败 ({target_path!r}): {e}")
        raise HTTPException(status_code=500, detail=f"读取脚本失败: {e}")


# ================= 请求模型 =================

__all__ = [
    '_notify_workflow_editor_action_result',
    '_notify_workflow_editor_action_status',
    '_execute_workflow_editor_test_payload',
    '_save_site_workflow_payload',
    '_scan_workflow_scripts',
    '_load_workflow_script_content',
]
