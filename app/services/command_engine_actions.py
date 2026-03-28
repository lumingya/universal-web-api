import copy
import json
import os
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import urlsplit

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from app.core.config import get_logger
from app.services.command_defs import ACTION_TYPES, TRIGGER_TYPES, CommandFlowAbort
from app.utils.site_url import extract_remote_site_domain

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession


logger = get_logger("CMD_ENG")


class CommandEngineActionsMixin:
    @staticmethod
    def _is_action_soft_failure(action_result: Any) -> bool:
        return isinstance(action_result, dict) and action_result.get("ok") is False

    @staticmethod
    def _wrap_run_js_for_return(code: Any) -> Optional[str]:
        stripped = str(code or "").strip()
        if not stripped:
            return None
        if re.match(r"^return\b", stripped):
            return None

        normalized = stripped.rstrip(";").strip()
        looks_like_iife = (
            normalized.startswith("(()")
            or normalized.startswith("((async")
            or normalized.startswith("(function")
            or normalized.startswith("(async function")
        )
        if not looks_like_iife:
            return None
        if not normalized.endswith(")()"):
            return None

        return f"return {normalized};"

    def _run_command_js(self, tab: Any, code: Any) -> Any:
        result = tab.run_js(code)
        if result is not None:
            return result

        wrapped_code = self._wrap_run_js_for_return(code)
        if not wrapped_code:
            return result

        try:
            wrapped_result = tab.run_js(wrapped_code)
            logger.debug("[CMD] JS 首次返回空，已自动补 return 重试一次")
            return wrapped_result
        except Exception as e:
            logger.debug(f"[CMD] JS return 包装重试失败（忽略）: {e}")
            return result

    def _execute_command_async(
        self,
        command: Dict,
        session: 'TabSession',
        chain: Optional[List[str]] = None,
        interrupt_context: Optional[Dict[str, Any]] = None,
        trigger_rollback: Optional[Dict[str, Any]] = None,
    ) -> bool:
        exec_key = (command["id"], session.id)
        priority = self._get_command_priority(command)
        baseline = self._get_request_priority_baseline()
        is_high = priority > baseline
        domain = self._get_session_domain(session)
        domain_sensitive = bool(domain) and self._command_affects_domain(command)

        should_rollback_immediately = False
        with self._lock:
            if exec_key in self._executing:
                should_rollback_immediately = True
            else:
                self._executing.add(exec_key)
                if is_high:
                    self._counter_inc(self._pending_high_by_session, session.id)
                    if domain_sensitive:
                        self._counter_inc(self._pending_high_by_domain, domain)
        if should_rollback_immediately:
            if trigger_rollback:
                self._rollback_trigger_consumption(command, session, trigger_rollback)
            return False

        def _run():
            acquired = False
            moved_running = False
            focus_emulation_applied = False
            cmd_task_id = f"cmd_{command['id'][:8]}_{int(time.time() * 1000)}"
            trigger = command.get("trigger", {}) or {}
            acquire_timeout = max(1.0, self._coerce_float(trigger.get("acquire_timeout_sec", 20), 20.0))
            deadline = time.time() + acquire_timeout

            try:
                while time.time() < deadline:
                    if is_high and domain_sensitive:
                        if self._has_busy_peer_on_domain(domain, exclude_session_id=session.id):
                            time.sleep(0.05)
                            continue

                    if not is_high:
                        try:
                            from app.services.request_manager import request_manager
                            status_counts = (request_manager.get_status() or {}).get("status_counts", {})
                            queued_count = int(status_counts.get("queued", 0) or 0)
                            running_count = int(status_counts.get("running", 0) or 0)
                            if queued_count > 0 or running_count > 0:
                                time.sleep(0.05)
                                continue
                        except Exception:
                            pass

                    if hasattr(session, "acquire_for_command") and session.acquire_for_command(cmd_task_id):
                        acquired = True
                        break
                    status_value = str(getattr(getattr(session, "status", None), "value", "")).lower()
                    if status_value in {"closed", "error"}:
                        break
                    time.sleep(0.05)

                if not acquired:
                    logger.info(
                        f"[CMD] 跳过执行（标签页忙碌或等待超时）: {command.get('name')} "
                        f"优先级={priority}, 等待超时={acquire_timeout}秒, 标签页={session.id}"
                    )
                    self._finalize_request_count_trigger_state(command, session, rollback=True)
                    self._reset_page_check_latch(command, session, reason="acquire_timeout")
                    if trigger_rollback:
                        self._rollback_trigger_consumption(command, session, trigger_rollback)
                    return

                self._finalize_request_count_trigger_state(command, session, rollback=False)

                # Optional focus behavior: disabled by default to avoid stealing user focus.
                if self._activate_tab_on_command:
                    try:
                        browser = self._get_browser()
                        pool = getattr(browser, "_tab_pool", None)
                        active_id = getattr(pool, "_active_session_id", None) if pool is not None else None
                        if active_id != session.id and hasattr(session, "activate"):
                            session.activate()
                            if pool is not None:
                                pool._active_session_id = session.id
                    except Exception as e:
                        logger.debug(f"[CMD] 激活目标标签页失败（忽略）: {e}")
                elif self._use_focus_emulation_on_command:
                    self._set_focus_emulation(session, True)
                    focus_emulation_applied = True

                if is_high:
                    with self._lock:
                        self._counter_dec(self._pending_high_by_session, session.id)
                        self._counter_inc(self._running_high_by_session, session.id)
                        if domain_sensitive:
                            self._counter_dec(self._pending_high_by_domain, domain)
                            self._counter_inc(self._running_high_by_domain, domain)
                    moved_running = True

                execution_result = self._execute_command(
                    command,
                    session,
                    chain=chain,
                    interrupt_context=interrupt_context,
                )
                if self._execution_needs_page_check_retry(execution_result):
                    self._reset_page_check_latch(command, session, reason="execution_not_ok")
            except Exception as e:
                logger.error(f"[CMD] 命令执行失败 [{command.get('name')}]: {e}")
                if not acquired:
                    self._finalize_request_count_trigger_state(command, session, rollback=True)
                    if trigger_rollback:
                        self._rollback_trigger_consumption(command, session, trigger_rollback)
                self._reset_page_check_latch(command, session, reason="execution_exception")
            finally:
                if focus_emulation_applied:
                    self._set_focus_emulation(session, False)
                if acquired:
                    try:
                        browser = self._get_browser()
                        pool = getattr(browser, "_tab_pool", None)
                        if pool is not None and hasattr(pool, "release"):
                            pool.release(session.id, check_triggers=False)
                        else:
                            session.release(clear_page=False, check_triggers=False)
                    except Exception as e:
                        logger.debug(f"[CMD] 命令释放标签页失败（忽略）: {e}")
                    try:
                        self.flush_deferred_workflow_commands(session)
                    except Exception as e:
                        logger.debug(f"[CMD] 补跑延后命令失败（忽略）: {e}")

                with self._lock:
                    if is_high:
                        if moved_running:
                            self._counter_dec(self._running_high_by_session, session.id)
                            if domain_sensitive:
                                self._counter_dec(self._running_high_by_domain, domain)
                        else:
                            self._counter_dec(self._pending_high_by_session, session.id)
                            if domain_sensitive:
                                self._counter_dec(self._pending_high_by_domain, domain)
                    self._executing.discard(exec_key)

        try:
            thread = threading.Thread(
                target=_run,
                daemon=True,
                name=f"cmd-{command['id'][:8]}"
            )
            thread.start()
        except Exception:
            with self._lock:
                if is_high:
                    self._counter_dec(self._pending_high_by_session, session.id)
                    if domain_sensitive:
                        self._counter_dec(self._pending_high_by_domain, domain)
                self._executing.discard(exec_key)
            if trigger_rollback:
                self._rollback_trigger_consumption(command, session, trigger_rollback)
            raise
        return True

    def _execute_command(
        self,
        command: Dict,
        session: 'TabSession',
        chain: Optional[List[str]] = None,
        interrupt_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cmd_name = command.get("name", "未命名")
        mode = command.get("mode", "simple")
        previous_command_priority = getattr(session, "_current_command_priority", None)
        previous_command_id = getattr(session, "_current_command_id", None)
        previous_command_chain = getattr(session, "_current_command_chain", None)
        previous_command_context = getattr(session, "_current_command_context", None)
        session._current_command_priority = self._get_command_priority(command)
        session._current_command_id = command.get("id")
        current_chain = list(chain or [])
        command_id = str(command.get("id", "") or "").strip()
        if command_id:
            current_chain.append(command_id)
        session._current_command_chain = current_chain
        session._current_command_context = copy.deepcopy(interrupt_context) if interrupt_context else None

        mode_label = "高级模式" if mode == "advanced" else "简易模式"
        logger.debug(f"[CMD] ▶ 执行: {cmd_name} (模式={mode_label}, 标签页={session.id})")
        self._suspend_tab_global_network(session, reason=f"command:{command.get('id', '')}")
        try:
            self._update_trigger_stats(command["id"])

            execution_result: Dict[str, Any]
            if mode == "advanced":
                execution_result = self._execute_advanced(command, session)
            else:
                execution_result = self._execute_simple(command, session)

            self._record_command_result(command, session, execution_result)

            logger.debug(f"[CMD] ✅ 完成: {cmd_name}")
            self._trigger_chained_commands(
                command, session, chain=chain, interrupt_context=interrupt_context
            )
            self._trigger_result_match_commands(
                command, session, chain=chain, interrupt_context=interrupt_context
            )
            self._trigger_result_event_commands(
                command, session, chain=chain, interrupt_context=interrupt_context
            )
            return execution_result
        finally:
            session._current_command_priority = previous_command_priority
            session._current_command_id = previous_command_id
            session._current_command_chain = previous_command_chain
            session._current_command_context = previous_command_context
            self._resume_tab_global_network(session, reason=f"command:{command.get('id', '')}")

    def _execute_simple(self, command: Dict, session: 'TabSession') -> Dict[str, Any]:
        actions = command.get("actions", [])
        step_results: List[Dict[str, Any]] = []
        last_result: Any = ""
        stop_on_error = bool(command.get("stop_on_error", False))
        stopped_on_error = False

        for i, action in enumerate(actions):
            action_type = action.get("type", "")
            action_ref = str(action.get("action_id") or f"step_{i + 1}")
            action_label = ACTION_TYPES.get(action_type, action_type)
            logger.debug(f"[CMD] 步骤 {i + 1}/{len(actions)}: {action_label}")
            try:
                action_result = self._execute_action(action, session)
                last_result = action_result
                step_ok = not self._is_action_soft_failure(action_result)
                step_results.append({
                    "index": i,
                    "action_ref": action_ref,
                    "type": action_type,
                    "result": action_result,
                    "ok": step_ok,
                })
                if not step_ok and stop_on_error:
                    stopped_on_error = True
                    logger.info(f"[CMD] 动作链因 stop_on_error 提前结束: 步骤={action_ref}")
                    break
            except CommandFlowAbort as e:
                last_result = str(e)
                step_results.append({
                    "index": i,
                    "action_ref": action_ref,
                    "type": action_type,
                    "result": last_result,
                    "ok": False,
                })
                logger.info(f"[CMD] 动作链提前结束: {e}")
                break
            except Exception as e:
                logger.error(f"[CMD] 步骤 {i + 1} 失败（{action_label}）: {e}")
                last_result = f"ERROR: {e}"
                step_results.append({
                    "index": i,
                    "action_ref": action_ref,
                    "type": action_type,
                    "result": last_result,
                    "ok": False,
                })
                if stop_on_error:
                    stopped_on_error = True
                    logger.info(f"[CMD] 动作链因 stop_on_error 提前结束: 步骤={action_ref}")
                    break

        return {
            "mode": "simple",
            "result": last_result,
            "steps": step_results,
            "stopped_on_error": stopped_on_error,
        }

    def _execute_action(self, action: Dict, session: 'TabSession') -> Any:
        action_type = action.get("type", "")
        tab = session.tab

        if action_type == "clear_cookies":
            try:
                current_url = str(getattr(tab, "url", "") or "").strip()
                split = urlsplit(current_url) if current_url else None
                origin = ""
                hostname = ""
                if split and split.scheme in {"http", "https"} and split.netloc:
                    origin = f"{split.scheme}://{split.netloc}"
                    hostname = split.hostname or ""

                deleted_cookies = 0
                origin_cleared = False

                if origin:
                    try:
                        tab.run_cdp("Storage.clearDataForOrigin", origin=origin, storageTypes="all")
                        origin_cleared = True
                    except Exception as e:
                        logger.debug(f"[CMD] 按源清空存储失败（忽略）: {e}")

                cookie_items = []
                for kwargs in (
                    {"urls": [current_url]} if current_url else None,
                    {},
                ):
                    if kwargs is None:
                        continue
                    try:
                        result = tab.run_cdp("Network.getCookies", **kwargs) or {}
                        cookies = result.get("cookies") or []
                        if cookies:
                            cookie_items = cookies
                            break
                    except Exception as e:
                        logger.debug(f"[CMD] 获取 Cookie 失败（忽略）: {e}")

                seen_keys = set()
                for cookie in cookie_items:
                    name = str(cookie.get("name", "") or "").strip()
                    domain = str(cookie.get("domain", "") or "").strip()
                    path = str(cookie.get("path", "/") or "/").strip() or "/"
                    if not name:
                        continue
                    if hostname:
                        normalized_domain = domain.lstrip(".").lower()
                        if normalized_domain and normalized_domain != hostname.lower() and not hostname.lower().endswith(f".{normalized_domain}"):
                            continue
                    dedupe_key = (name, domain, path)
                    if dedupe_key in seen_keys:
                        continue
                    seen_keys.add(dedupe_key)
                    delete_kwargs = {"name": name, "path": path}
                    if domain:
                        delete_kwargs["domain"] = domain
                    elif current_url:
                        delete_kwargs["url"] = current_url
                    try:
                        tab.run_cdp("Network.deleteCookies", **delete_kwargs)
                        deleted_cookies += 1
                    except Exception as e:
                        logger.debug(f"[CMD] 删除 Cookie 失败（忽略）: {e}")

                try:
                    tab.run_js(
                        "try { localStorage.clear(); } catch (e) {}"
                        "try { sessionStorage.clear(); } catch (e) {}"
                        "try { document.cookie.split(';').forEach(function(c) {"
                        "  var name = c.trim().split('=')[0];"
                        "  if (!name) return;"
                        "  document.cookie = name + '=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/;';"
                        "}); } catch (e) {}"
                    )
                except Exception as e:
                    logger.debug(f"[CMD] 清空页面存储失败（忽略）: {e}")

                logger.debug(
                    f"[CMD] Cookie/存储已清除: 地址={current_url or '-'}, "
                    f"按源清空={origin_cleared}, 已删除 Cookie 数={deleted_cookies}"
                )
                return "cookies_cleared"
            except Exception as e:
                logger.warning(f"[CMD] 清除 Cookie 失败: {e}")
                return f"cookies_clear_failed: {e}"

        elif action_type == "refresh_page":
            try:
                tab.refresh()
                time.sleep(2)
                logger.debug("[CMD] 页面已刷新")
                return "page_refreshed"
            except Exception as e:
                logger.warning(f"[CMD] 刷新页面失败: {e}")
                return f"refresh_failed: {e}"

        elif action_type == "new_chat":
            try:
                engine = self._get_config_engine()
                domain = self._get_session_domain(session)
                site_data = engine._get_site_data(domain, session.preset_name)
                if site_data:
                    selector = site_data.get("selectors", {}).get("new_chat_btn", "")
                    if selector:
                        ele = tab.ele(selector, timeout=3)
                        if ele:
                            ele.click()
                            time.sleep(1)
                            logger.debug("[CMD] 新建对话完成")
                            return "new_chat_clicked"
                        else:
                            logger.warning("[CMD] 新建对话按钮未找到")
                            return "new_chat_button_not_found"
                    else:
                        logger.warning("[CMD] 未配置新建对话按钮选择器（new_chat_btn）")
                        return "new_chat_selector_missing"
            except Exception as e:
                logger.warning(f"[CMD] 新建对话失败: {e}")
                return f"new_chat_failed: {e}"

        elif action_type == "run_js":
            code = action.get("code", "")
            if code:
                try:
                    result = self._run_command_js(tab, code)
                    retry_on_results = action.get("retry_on_results", [])
                    if isinstance(retry_on_results, str):
                        retry_on_results = [
                            item.strip() for item in retry_on_results.split(",") if item.strip()
                        ]
                    elif not isinstance(retry_on_results, list):
                        retry_on_results = []

                    try:
                        retry_attempts = max(0, int(action.get("retry_attempts", 0)))
                    except Exception:
                        retry_attempts = 0

                    retry_after_refresh = bool(action.get("retry_after_refresh", False))
                    try:
                        retry_wait_seconds = max(0.0, float(action.get("retry_wait_seconds", 0)))
                    except Exception:
                        retry_wait_seconds = 0.0

                    attempt = 0
                    while attempt < retry_attempts and str(result) in retry_on_results:
                        attempt += 1
                        logger.info(
                            f"[CMD] JS 命中重试条件: 返回值={result}, "
                            f"第 {attempt}/{retry_attempts} 次, 刷新后重试={retry_after_refresh}"
                        )
                        if retry_after_refresh:
                            try:
                                tab.refresh()
                                time.sleep(2)
                            except Exception as refresh_error:
                                logger.warning(f"[CMD] JS 重试前刷新失败: {refresh_error}")
                                break
                        if retry_wait_seconds > 0:
                            time.sleep(retry_wait_seconds)
                        result = self._run_command_js(tab, code)
                    logger.debug(f"[CMD] JS 执行完成: {str(result)[:100]}")

                    # 当 JS 返回假值（None/""/False/0）时，视为执行未成功，
                    # 返回 {ok: False} 以便 page_check 触发器重置 latch 并重试。
                    # 默认开启；如 JS 本身就应返回假值，用户可在动作中设置 fail_on_falsy: false
                    fail_on_falsy = action.get("fail_on_falsy", True)
                    if fail_on_falsy and not result:
                        logger.info(f"[CMD] JS 返回假值，按 fail_on_falsy 记为失败: {result!r}")
                        return {"ok": False, "js_result": result, "reason": "falsy_result"}

                    return result
                except Exception as e:
                    logger.warning(f"[CMD] JS 执行失败: {e}")
                    return f"js_failed: {e}"
            return ""

        elif action_type == "wait":
            seconds = float(action.get("seconds", 1))
            time.sleep(seconds)
            logger.debug(f"[CMD] 等待 {seconds}秒")
            return f"waited:{seconds}"

        elif action_type in {"execute_preset", "switch_preset"}:
            return self._execute_preset_action(action, session)

        elif action_type == "click_element":
            selector = action.get("selector", "")
            if selector:
                try:
                    ele = tab.ele(selector, timeout=3)
                    if ele:
                        # 获取当前站点的 stealth 配置
                        config_engine = self._get_config_engine()
                        site_cfg = config_engine._get_site_data(
                            self._get_session_domain(session),
                            session.preset_name
                        )
                        is_stealth = site_cfg.get("stealth", False) if site_cfg else False
                        
                        if is_stealth:
                            logger.debug(f"[CMD] 准备隐身模式点击元素: {selector}")
                            # 尝试通过 JS 获取元素中心坐标
                            rect = ele.run_js(
                                "const r = this.getBoundingClientRect();"
                                "return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}"
                            )
                            click_x_raw = rect.get('x') if isinstance(rect, dict) else None
                            click_y_raw = rect.get('y') if isinstance(rect, dict) else None
                            if click_x_raw is not None and click_y_raw is not None:
                                click_x = int(click_x_raw) + __import__('random').randint(-4, 4)
                                click_y = int(click_y_raw) + __import__('random').randint(-4, 4)
                                from app.utils.human_mouse import cdp_precise_click, smooth_move_mouse
                                # 平滑移动到坐标再进行精确按压
                                smooth_move_mouse(tab, (click_x - 50, click_y - 50), (click_x, click_y))
                                time.sleep(__import__('random').uniform(0.05, 0.15))
                                success = cdp_precise_click(tab, click_x, click_y)
                                if success:
                                    logger.debug(f"[CMD] 元素已隐身点击: {selector} at ({click_x}, {click_y})")
                                    return f"element_stealth_clicked:{selector}"
                                else:
                                    logger.warning(f"[CMD] 元素隐身点击事件派发失败: {selector}")
                                    return f"element_stealth_click_failed:{selector}"
                            else:
                                logger.warning(f"[CMD] 隐身点击无法获取目标坐标，取消普通点击降级: {selector}")
                                return f"element_stealth_click_unavailable:{selector}"
                        else:
                            ele.click()
                            time.sleep(1)
                            logger.debug(f"[CMD] 元素已点击: {selector}")
                            return f"element_clicked:{selector}"
                    else:
                        logger.warning(f"[CMD] 待点击的元素未找到: {selector}")
                        return f"element_not_found:{selector}"
                except Exception as e:
                    logger.warning(f"[CMD] 点击元素失败: {e}")
                    return f"click_element_failed:{e}"
            return "click_element_skipped_no_selector"

        elif action_type == "click_coordinates":
            try:
                x = int(action.get("x", 0))
                y = int(action.get("y", 0))
                from app.utils.human_mouse import cdp_precise_click
                # cdp_precise_click handles its own debug logging and execution securely via CDP
                success = cdp_precise_click(tab, x, y)
                if success:
                    time.sleep(0.5)
                    logger.debug(f"[CMD] 坐标已点击: ({x}, {y})")
                    return f"coordinates_clicked:({x},{y})"
                else:
                    logger.warning(f"[CMD] 坐标点击失败: ({x}, {y})")
                    return f"coordinates_click_failed:({x},{y})"
            except Exception as e:
                logger.warning(f"[CMD] 坐标点击失败，参数异常: {e}")
                return f"click_coordinates_failed:{e}"

        elif action_type == "execute_workflow":
            return self._execute_workflow_action(action, session)

        elif action_type == "navigate":
            url = action.get("url", "")
            if url:
                try:
                    tab.get(url)
                    time.sleep(2)
                    logger.debug(f"[CMD] 已导航到: {url}")
                    return f"navigated:{url}"
                except Exception as e:
                    logger.warning(f"[CMD] 导航失败: {e}")
                    return f"navigate_failed:{e}"
            return "navigate_skipped"

        elif action_type == "switch_proxy":
            return self._execute_switch_proxy(action, session)

        elif action_type == "send_webhook":
            return self._execute_webhook_action(action, session)

        elif action_type == "send_napcat":
            return self._execute_napcat_action(action, session)

        elif action_type == "execute_command_group":
            return self._execute_command_group_action(action, session)

        elif action_type == "abort_task":
            result = self._execute_abort_task(action, session)
            if bool(action.get("stop_actions", True)):
                raise CommandFlowAbort("abort_task_triggered")
            return result

        elif action_type == "release_tab_lock":
            result = self._execute_release_tab_lock(action, session)
            if bool(action.get("stop_actions", True)):
                raise CommandFlowAbort("release_tab_lock_triggered")
            return result

        else:
            logger.warning(f"[CMD] 未知动作类型: {action_type}")
            return ""

    def _execute_preset_action(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        """执行预设动作，兼容旧版 switch_preset。"""
        raw_preset_name = action.get("preset_name", "")
        if self._should_follow_default_preset(raw_preset_name):
            try:
                browser = self._get_browser()
                success = browser.tab_pool.set_tab_preset(session.persistent_index, None)
                if not success:
                    return {"ok": False, "error": "set_default_preset_failed"}
                domain = self._get_session_domain(session)
                effective_preset = self._get_config_engine().get_default_preset(domain) or "主预设"
                logger.debug(f"[CMD] 预设已切换为跟随站点默认: {effective_preset}")
                return {"ok": True, "preset": effective_preset, "follow_default": True}
            except Exception as e:
                logger.warning(f"[CMD] 切换为默认预设失败: {e}")
                return {"ok": False, "error": str(e)}

        preset_name = self._resolve_preset_name(action.get("preset_name", ""), session)
        if not preset_name:
            logger.warning("[CMD] 预设名称为空，跳过执行")
            return {"ok": False, "error": "empty_preset"}

        try:
            browser = self._get_browser()
            success = browser.tab_pool.set_tab_preset(
                session.persistent_index, preset_name
            )
            if not success:
                return {"ok": False, "error": "set_preset_failed"}
            logger.debug(f"[CMD] 预设已切换: {preset_name}")
            return {"ok": True, "preset": preset_name}
        except Exception as e:
            logger.warning(f"[CMD] 切换预设失败: {e}")
            return {"ok": False, "error": str(e)}

    def _execute_workflow_action(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        """在当前标签页上立即执行目标预设的工作流。"""
        try:
            browser = self._get_browser()
            raw_preset_name = action.get("preset_name", "")
            preset_name = self._resolve_preset_name(raw_preset_name, session)
            if self._should_follow_default_preset(raw_preset_name):
                domain = self._get_session_domain(session)
                preset_name = self._get_config_engine().get_default_preset(domain) or ""
            prompt = str(action.get("prompt", ""))
            inherited_workflow_priority = self._normalize_priority(
                action.get("workflow_priority", getattr(session, "_current_command_priority", None)),
                self._get_request_priority_baseline(),
            )
            timeout_default_raw = os.getenv("CMD_EXECUTE_WORKFLOW_TIMEOUT_SEC", "45")
            timeout_sec = max(
                1.0,
                self._coerce_float(action.get("timeout_sec", timeout_default_raw), 45.0)
            )
            started_at = time.time()
            deadline = started_at + timeout_sec
            timed_out = False
            previous_stop_reason = str(getattr(session, "_workflow_stop_reason", "") or "").strip()
            # High-priority commands may launch a nested workflow while the parent workflow
            # has already marked the session as interrupted. Clear that inherited stop flag
            # before starting the nested workflow, otherwise the new workflow self-cancels
            # immediately. Only restore it afterwards when there is still an active parent
            # workflow that needs to observe the original interrupt.
            preserved_interrupt = (
                previous_stop_reason in {"command_interrupt", "command_interrupt_abort"}
                and self._has_active_workflow(session)
            )
            setattr(session, "_workflow_stop_reason", None)

            def _action_stop_checker() -> bool:
                nonlocal timed_out
                current_reason = str(getattr(session, "_workflow_stop_reason", "") or "").strip()
                if current_reason in {"command_interrupt", "command_interrupt_abort"}:
                    return True
                if time.time() >= deadline:
                    timed_out = True
                    setattr(session, "_workflow_stop_reason", "timeout")
                    return True
                return False
            try:
                if preset_name:
                    effective_preset = preset_name
                    logger.debug(
                        f"[CMD] 开始执行工作流: 标签页=#{session.persistent_index}, "
                        f"预设={effective_preset}, 超时={timeout_sec}秒"
                    )

                    messages = [{"role": "user", "content": prompt}]
                    for chunk in browser._execute_workflow_non_stream(
                        session,
                        messages,
                        preset_name=preset_name,
                        stop_checker=_action_stop_checker,
                        workflow_priority=inherited_workflow_priority,
                    ):
                        payload = chunk[6:].strip() if chunk.startswith("data: ") else chunk
                        if not payload:
                            continue
                        try:
                            data = json.loads(payload)
                        except Exception:
                            continue
                        if isinstance(data, dict) and data.get("error"):
                            logger.warning(f"[CMD] 工作流返回错误: {data['error']}")
                            return {"ok": False, "error": data["error"]}
                    if timed_out:
                        logger.warning(
                            f"[CMD] 工作流执行超时: 标签页=#{session.persistent_index}, "
                            f"预设={effective_preset}, 超时={timeout_sec}秒"
                        )
                        return {
                            "ok": False,
                            "error": f"workflow_timeout:{timeout_sec}s",
                            "timeout": timeout_sec,
                            "preset": effective_preset,
                        }

                    logger.debug(
                        f"[CMD] 工作流执行完成: 标签页=#{session.persistent_index}, 预设={effective_preset}"
                    )
                    return {"ok": True, "preset": effective_preset}

                effective_preset = session.preset_name or "主预设"
                logger.debug(
                    f"[CMD] 开始执行工作流: 标签页=#{session.persistent_index}, "
                    f"预设={effective_preset}, 超时={timeout_sec}秒"
                )

                messages = [{"role": "user", "content": prompt}]
                for chunk in browser._execute_workflow_non_stream(
                    session,
                    messages,
                    stop_checker=_action_stop_checker,
                    workflow_priority=inherited_workflow_priority,
                ):
                    payload = chunk[6:].strip() if chunk.startswith("data: ") else chunk
                    if not payload:
                        continue
                    try:
                        data = json.loads(payload)
                    except Exception:
                        continue
                    if isinstance(data, dict) and data.get("error"):
                        logger.warning(f"[CMD] 工作流返回错误: {data['error']}")
                        return {"ok": False, "error": data["error"]}
                if timed_out:
                    logger.warning(
                        f"[CMD] 工作流执行超时: 标签页=#{session.persistent_index}, "
                        f"预设={effective_preset}, 超时={timeout_sec}秒"
                    )
                    return {
                        "ok": False,
                        "error": f"workflow_timeout:{timeout_sec}s",
                        "timeout": timeout_sec,
                        "preset": effective_preset,
                    }

                logger.debug(
                    f"[CMD] 工作流执行完成: 标签页=#{session.persistent_index}, 预设={effective_preset}"
                )
                return {"ok": True, "preset": effective_preset}
            finally:
                current_reason = str(getattr(session, "_workflow_stop_reason", "") or "").strip()
                if preserved_interrupt:
                    setattr(session, "_workflow_stop_reason", previous_stop_reason)
                elif current_reason == "timeout" and not timed_out:
                    setattr(session, "_workflow_stop_reason", "")
        except Exception as e:
            logger.warning(f"[CMD] 执行工作流失败: {e}")
            return {"ok": False, "error": str(e)}

    def _build_template_context(self, session: 'TabSession') -> Dict[str, Any]:
        current_context = getattr(session, "_current_command_context", None) or {}
        latest_event = copy.deepcopy(current_context.get("network_event") or {})
        latest_result_event = copy.deepcopy(current_context.get("command_result_event") or {})
        domain = self._get_session_domain(session)
        effective_preset = session.preset_name or self._get_config_engine().get_default_preset(domain) or "主预设"
        return {
            "tab_id": session.id,
            "tab_index": session.persistent_index,
            "domain": domain,
            "preset": effective_preset,
            "request_count": session.request_count,
            "error_count": session.error_count,
            "task_id": session.current_task_id or "",
            "timestamp": int(time.time()),
            "iso_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "network_url": str(latest_event.get("url", "") or ""),
            "network_status": str(latest_event.get("status", "") or ""),
            "network_method": str(latest_event.get("method", "") or ""),
            "source_command_id": str(latest_result_event.get("source_command_id", "") or ""),
            "source_command_name": str(latest_result_event.get("source_command_name", "") or ""),
            "source_group_name": str(latest_result_event.get("source_group_name", "") or ""),
            "command_result": str(latest_result_event.get("result", "") or ""),
            "command_result_summary": str(latest_result_event.get("summary", "") or ""),
            "command_result_mode": str(latest_result_event.get("mode", "") or ""),
            "command_result_informative": str(bool(latest_result_event.get("informative", False))).lower(),
            "command_result_time": str(int(latest_result_event.get("timestamp", 0) or 0)),
        }

    def _render_template(self, template: Any, context: Dict[str, Any]) -> str:
        raw = str(template or "")

        def _replace(match: re.Match) -> str:
            key = match.group(1).strip()
            return str(context.get(key, ""))

        return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", _replace, raw)

    def _render_template_data(self, value: Any, context: Dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {str(key): self._render_template_data(item, context) for key, item in value.items()}
        if isinstance(value, list):
            return [self._render_template_data(item, context) for item in value]
        if isinstance(value, tuple):
            return [self._render_template_data(item, context) for item in value]
        if isinstance(value, str):
            return self._render_template(value, context)
        return value

    def _parse_json_or_string(self, raw: str) -> Any:
        text = str(raw or "").strip()
        if not text:
            return ""
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except Exception:
                return text
        return text

    def _execute_webhook_action(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        if not HAS_REQUESTS:
            logger.error("[CMD] send_webhook 需要 requests 库，请运行: pip install requests")
            return {"ok": False, "error": "requests_not_installed"}

        ctx = self._build_template_context(session)
        method = str(action.get("method", "POST") or "POST").strip().upper()
        timeout = float(action.get("timeout", 8))
        url = self._render_template(action.get("url", ""), ctx).strip()
        if not url:
            logger.warning("[CMD] Webhook URL 为空，跳过执行")
            return {"ok": False, "error": "empty_url"}

        raw_payload = action.get("payload", "")
        if isinstance(raw_payload, (dict, list, tuple)):
            payload = self._render_template_data(raw_payload, ctx)
        else:
            payload_text = self._render_template(raw_payload, ctx)
            payload = self._parse_json_or_string(payload_text)

        raw_headers = action.get("headers")
        headers: Dict[str, str] = {}
        if isinstance(raw_headers, dict):
            rendered_headers = self._render_template_data(raw_headers, ctx)
            headers = {str(key): str(value) for key, value in rendered_headers.items()}
        elif isinstance(raw_headers, str) and raw_headers.strip():
            parsed = self._parse_json_or_string(self._render_template(raw_headers, ctx))
            if isinstance(parsed, dict):
                headers = {str(k): str(v) for k, v in parsed.items()}

        request_kwargs: Dict[str, Any] = {
            "method": method,
            "url": url,
            "timeout": timeout,
            "headers": headers or None,
        }

        if method == "GET":
            if isinstance(payload, dict):
                request_kwargs["params"] = payload
            elif payload:
                request_kwargs["params"] = {"payload": payload}
        else:
            if isinstance(payload, (dict, list)):
                request_kwargs["json"] = payload
            elif payload:
                request_kwargs["data"] = payload

        try:
            response = requests.request(**request_kwargs)
            if bool(action.get("raise_for_status", False)):
                response.raise_for_status()

            logger.info(f"[CMD] Webhook 已发送: {method} {url} -> {response.status_code}")
            return {
                "ok": response.ok,
                "status_code": response.status_code,
                "url": url,
                "body_preview": response.text[:200],
            }
        except Exception as e:
            logger.warning(f"[CMD] Webhook 发送失败: {e}")
            return {"ok": False, "error": str(e), "url": url}

    def _execute_napcat_action(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        if not HAS_REQUESTS:
            logger.error("[CMD] send_napcat 需要 requests 库，请运行: pip install requests")
            return {"ok": False, "error": "requests_not_installed"}

        ctx = self._build_template_context(session)
        base_url = self._render_template(action.get("base_url", ""), ctx).strip().rstrip("/")
        target_type = str(action.get("target_type", "private") or "private").strip().lower()
        timeout = float(action.get("timeout", 8))
        access_token = self._render_template(action.get("access_token", ""), ctx).strip()
        message = self._render_template(
            action.get("message", "{{command_result_summary}}"),
            ctx,
        ).strip()

        if not base_url:
            return {"ok": False, "error": "empty_base_url"}
        if not message:
            return {"ok": False, "error": "empty_message"}

        if target_type == "group":
            api_path = "/send_group_msg"
            target_id = str(action.get("group_id", "") or "").strip()
            payload = {"group_id": int(target_id), "message": message} if target_id.isdigit() else {"group_id": target_id, "message": message}
        else:
            api_path = "/send_private_msg"
            target_id = str(action.get("user_id", "") or "").strip()
            payload = {"user_id": int(target_id), "message": message} if target_id.isdigit() else {"user_id": target_id, "message": message}

        if not target_id:
            return {"ok": False, "error": f"empty_{target_type}_id"}

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = access_token if " " in access_token else access_token

        url = f"{base_url}{api_path}"
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if bool(action.get("raise_for_status", True)):
                response.raise_for_status()
            logger.info(f"[CMD] NapCat 已发送: {target_type} {target_id} -> {response.status_code}")
            return {
                "ok": response.ok,
                "status_code": response.status_code,
                "url": url,
                "target_type": target_type,
                "target_id": target_id,
                "body_preview": response.text[:200],
            }
        except Exception as e:
            logger.warning(f"[CMD] NapCat 发送失败: {e}")
            return {"ok": False, "error": str(e), "url": url, "target_type": target_type, "target_id": target_id}

    def _execute_command_group_action(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        group_name = self._normalize_group_name(action.get("group_name"))
        include_disabled = bool(action.get("include_disabled", False))
        acquire_policy = action.get("acquire_policy", "inherit_session")
        if not group_name:
            logger.warning("[CMD] execute_command_group 缺少 group_name，跳过执行")
            return {"ok": False, "error": "empty_group_name"}

        logger.info(
            f"[CMD] 执行命令组动作: {group_name} "
            f"(include_disabled={include_disabled}, acquire_policy={acquire_policy})"
        )
        ancestry_chain = list(getattr(session, "_current_command_chain", None) or [])
        return self.execute_command_group(
            group_name=group_name,
            session=session,
            include_disabled=include_disabled,
            source_command_id=str(getattr(session, "_current_command_id", "") or "").strip() or None,
            ancestry_chain=ancestry_chain,
            acquire_policy=acquire_policy,
        )

    def _execute_abort_task(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        reason = str(action.get("reason", "abort_task_action")).strip() or "abort_task_action"
        cancelled = False
        try:
            from app.services.request_manager import request_manager
            cancelled = request_manager.cancel_current(reason, tab_id=session.id)
        except Exception as e:
            logger.debug(f"[CMD] 取消请求失败（可忽略）: {e}")

        try:
            if hasattr(session.tab, "stop_loading"):
                session.tab.stop_loading()
            session.tab.run_js("if (window.stop) { window.stop(); }")
        except Exception:
            pass

        logger.info(f"[CMD] 中断任务动作已执行 (已取消={cancelled}, 原因={reason})")
        return {"ok": True, "cancelled": cancelled, "reason": reason}

    def _execute_release_tab_lock(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        """
        解除当前标签页占用：
        - 尝试取消该标签页关联请求；
        - 强制释放标签页 BUSY 状态；
        - 可选重置到 about:blank。
        """
        reason = str(action.get("reason", "release_tab_lock_action")).strip() or "release_tab_lock_action"
        clear_page = bool(action.get("clear_page", True))
        try:
            browser = self._get_browser()
            pool = browser.tab_pool
            result = pool.terminate_by_index(
                session.persistent_index,
                reason=reason,
                clear_page=clear_page,
            )
            logger.info(
                f"[CMD] 解除标签页占用完成: 标签页=#{session.persistent_index}, "
                f"已取消={result.get('cancelled')}, 状态={result.get('status')}, 原因={reason}"
            )
            return result
        except Exception as e:
            logger.warning(f"[CMD] 解除标签页占用失败: {e}")
            return {"ok": False, "error": str(e), "reason": reason}

    # ================= 代理切换 =================

    def _execute_switch_proxy(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        """
        执行代理节点切换（通过 Clash API）
        """
        if not HAS_REQUESTS:
            logger.error("[CMD] 切换代理需要 requests 库，请运行: pip install requests")
            return {"ok": False, "error": "requests_not_installed"}

        # 读取配置
        clash_api = action.get("clash_api", "http://127.0.0.1:9090").rstrip("/")
        clash_secret = action.get("clash_secret", "")
        selector = action.get("selector", "Proxy")
        mode = action.get("mode", "random")
        node_name = action.get("node_name", "")
        exclude_str = action.get("exclude_keywords", "DIRECT,REJECT,GLOBAL,自动选择,故障转移")
        refresh_after = action.get("refresh_after", True)

        exclude_keywords = [k.strip() for k in exclude_str.split(",") if k.strip()]

        headers = {"Content-Type": "application/json"}
        if clash_secret:
            headers["Authorization"] = f"Bearer {clash_secret}"

        try:
            resp = requests.get(
                f"{clash_api}/proxies/{selector}",
                headers=headers,
                timeout=5
            )

            if resp.status_code == 404:
                logger.error(f"[CMD] 代理组 '{selector}' 不存在，请检查 Clash 配置")
                return {"ok": False, "error": "proxy_group_not_found"}

            resp.raise_for_status()
            data = resp.json()

            current_node = data.get("now", "")
            all_nodes = data.get("all", [])

            available = []
            for node in all_nodes:
                should_exclude = False
                for keyword in exclude_keywords:
                    if keyword and keyword in node:
                        should_exclude = True
                        break
                if not should_exclude:
                    available.append(node)

            if not available:
                logger.warning("[CMD] 没有可用的代理节点")
                return {"ok": False, "error": "no_available_nodes"}

            new_node = None

            if mode == "specific":
                if node_name in available:
                    new_node = node_name
                else:
                    logger.warning(f"[CMD] 指定节点 '{node_name}' 不可用，回退到随机模式")
                    mode = "random"

            if mode == "random":
                candidates = [n for n in available if n != current_node]
                if candidates:
                    new_node = random.choice(candidates)
                else:
                    new_node = random.choice(available)

            elif mode == "round_robin":
                try:
                    current_idx = available.index(current_node)
                    next_idx = (current_idx + 1) % len(available)
                    new_node = available[next_idx]
                except ValueError:
                    new_node = available[0]

            if not new_node:
                logger.warning("[CMD] 无法选择新节点")
                return {"ok": False, "error": "cannot_pick_node"}

            if new_node == current_node:
                logger.info(f"[CMD] 当前已是节点: {current_node}，跳过切换")
                return {"ok": True, "switched": False, "node": current_node}

            switch_resp = requests.put(
                f"{clash_api}/proxies/{selector}",
                json={"name": new_node},
                headers=headers,
                timeout=5
            )
            switch_resp.raise_for_status()

            logger.info(f"[CMD] ✅ 代理已切换: {current_node} → {new_node}")

            if refresh_after:
                time.sleep(1)
                try:
                    session.tab.refresh()
                    time.sleep(2)
                    logger.debug("[CMD] 页面已刷新")
                except Exception as e:
                    logger.warning(f"[CMD] 刷新页面失败: {e}")

            return {
                "ok": True,
                "switched": True,
                "from": current_node,
                "to": new_node,
            }

        except requests.exceptions.ConnectionError:
            logger.error(f"[CMD] ❌ 无法连接到 Clash API ({clash_api})，请检查 Clash 是否运行")
            return {"ok": False, "error": "connection_error"}
        except requests.exceptions.Timeout:
            logger.error("[CMD] ❌ Clash API 请求超时")
            return {"ok": False, "error": "timeout"}
        except requests.exceptions.HTTPError as e:
            logger.error(f"[CMD] ❌ Clash API 错误: {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.error(f"[CMD] ❌ 切换代理失败: {e}")
            return {"ok": False, "error": str(e)}

    # ================= 高级模式 =================

    def _execute_advanced(self, command: Dict, session: 'TabSession') -> Dict[str, Any]:
        script = command.get("script", "")
        lang = command.get("script_lang", "javascript")

        if not script.strip():
            logger.warning("[CMD] 高级模式脚本为空")
            return {"mode": "advanced", "result": "", "steps": []}

        if lang == "javascript":
            try:
                result = session.tab.run_js(script)
                logger.info(f"[CMD] JS 脚本执行完成: {str(result)[:200]}")
                return {"mode": "advanced", "result": result, "steps": []}
            except Exception as e:
                logger.error(f"[CMD] JS 脚本执行失败: {e}")
                return {"mode": "advanced", "result": f"js_failed: {e}", "steps": []}

        if lang == "python":
            import json as json_module
            context = {
                "tab": session.tab,
                "session": session,
                "browser": self._get_browser(),
                "config_engine": self._get_config_engine(),
                "logger": logger,
                "time": time,
                "json": json_module,
                "result": "",
            }
            try:
                exec(script, {"__builtins__": __builtins__}, context)
                logger.info("[CMD] Python 脚本执行完成")
                return {"mode": "advanced", "result": context.get("result", ""), "steps": []}
            except Exception as e:
                logger.error(f"[CMD] Python 脚本执行失败: {e}")
                return {"mode": "advanced", "result": f"python_failed: {e}", "steps": []}

        logger.warning(f"[CMD] 不支持的脚本语言: {lang}")
        return {"mode": "advanced", "result": f"unsupported_lang:{lang}", "steps": []}

    # ================= 统计 =================

    def _update_trigger_stats(self, command_id: str):
        with self._lock:
            state = self._command_runtime_stats.setdefault(command_id, {})
            state["last_triggered"] = time.time()
            state["trigger_count"] = int(state.get("trigger_count", 0) or 0) + 1

    # ================= 元信息 =================

    def get_trigger_types(self) -> Dict[str, str]:
        return copy.deepcopy(TRIGGER_TYPES)

    def get_action_types(self) -> Dict[str, str]:
        return copy.deepcopy(ACTION_TYPES)

    def get_trigger_states(self) -> Dict[str, Any]:
        with self._lock:
            return {
                f"{cmd_id}:{tab_id}": copy.deepcopy(state)
                for (cmd_id, tab_id), state in self._trigger_states.items()
            }
