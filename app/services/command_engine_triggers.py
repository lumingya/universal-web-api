"""CommandEngine 的一部分（R2-3 拆分）：触发判定：请求计数、网络事件、优先级与忙碌判断、导航事件分发。

方法原样从 command_engine.py 迁出，行为不变；通过 mixin 组合回 CommandEngine，
类属性与实例状态都在 CommandEngine（command_engine.py）里定义。
"""

import time
import uuid
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from app.utils.site_url import extract_remote_site_domain
from app.services.command_engine_common import logger

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession  # noqa: F401


class CommandEngineTriggersMixin:
    """触发判定：请求计数、网络事件、优先级与忙碌判断、导航事件分发"""

    def check_triggers(self, session: 'TabSession'):
        """
        检查所有命令的触发条件

        在 TabSession.release() 后调用（锁外、后台，不阻塞主流程）
        """
        self.ensure_scheduler_running()
        try:
            commands = self._load_commands_for_checks()
        except Exception as e:
            logger.debug(f"命令加载失败，跳过触发检查: {e}")
            return

        if not commands:
            return


        ordered_commands = [
            (idx, cmd) for idx, cmd in enumerate(commands)
            if cmd.get("enabled", True)
        ]
        ordered_commands.sort(key=lambda item: (-self._get_command_priority(item[1]), item[0]))

        for _, cmd in ordered_commands:
            with self._command_logging_context(cmd):
                try:
                    if self._should_trigger(cmd, session):
                        meta = self._take_pending_async_trigger_meta(cmd, session) or {}
                        self._execute_command_async(
                            cmd,
                            session,
                            interrupt_context=meta.get("interrupt_context"),
                            trigger_rollback=meta.get("rollback"),
                        )
                except Exception as e:
                    logger.error(f"trigger check failed [{cmd.get('name')}]: {e}")
    def check_workflow_triggers_now(self, session: 'TabSession') -> bool:
        """Check commands for an active workflow and enqueue matching interrupts immediately."""
        if not self._has_active_workflow(session):
            return False

        self.ensure_scheduler_running()
        try:
            commands = self._load_commands_for_checks()
        except Exception as e:
            logger.debug(f"命令加载失败，跳过工作流触发检查: {e}")
            return False

        ordered_commands = [
            (idx, cmd) for idx, cmd in enumerate(commands)
            if cmd.get("enabled", True)
        ]
        ordered_commands.sort(key=lambda item: (-self._get_command_priority(item[1]), item[0]))

        scheduled_any = False
        for _, cmd in ordered_commands:
            with self._command_logging_context(cmd):
                try:
                    trigger = cmd.get("trigger", {}) or {}
                    trigger_type = str(trigger.get("type", "")).strip().lower()
                    if (
                        trigger_type == "page_check"
                        and not self._should_evaluate_page_check_while_busy_workflow(cmd)
                    ):
                        continue
                    if not self._should_trigger(cmd, session):
                        continue
                    meta = self._take_pending_async_trigger_meta(cmd, session) or {}
                    scheduled = self._schedule_command_for_active_workflow(
                        cmd,
                        session,
                        interrupt_context=meta.get("interrupt_context"),
                        trigger_rollback=meta.get("rollback"),
                    )
                    if scheduled:
                        scheduled_any = True
                    elif meta.get("rollback"):
                        self._rollback_trigger_consumption(cmd, session, meta.get("rollback"))
                except Exception as e:
                    logger.error(f"workflow trigger check failed [{cmd.get('name')}]: {e}")

        return scheduled_any
    def submit_background_task(self, fn, *args, **kwargs):
        """Submit non-trigger command work to the bounded command executor."""
        return self._command_executor.submit(fn, *args, **kwargs)
    def handle_network_event(self, session: 'TabSession', event: Dict[str, Any]) -> bool:
        """
        处理实时网络事件。

        返回值：
        - True: 命中了网络拦截，当前监听应暂停并交回工作流处理
        - False: 不需要暂停当前监听
        """
        self.ensure_scheduler_running()
        if not event:
            return False

        event_copy = dict(event)
        if not event_copy.get("event_id"):
            event_copy["event_id"] = f"net_{uuid.uuid4().hex[:10]}"
        event_copy.setdefault("timestamp", time.time())

        with self._lock:
            self._append_bounded_event(self._network_events, session.id, event_copy)

        try:
            commands = self._load_commands_for_checks()
        except Exception as e:
            logger.debug(f"命令加载失败，跳过网络事件触发: {e}")
            return False

        should_interrupt_listener = False

        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            trigger = cmd.get("trigger", {})
            if trigger.get("type") != "network_request_error":
                continue
            if not self._matches_scope(cmd, session):
                continue
            with self._command_logging_context(cmd):
                dispatch = self._prepare_network_trigger_dispatch(cmd, session, event_copy)
                if not dispatch:
                    continue
                if self._has_active_workflow(session):
                    scheduled = self._schedule_command_for_active_workflow(
                        cmd,
                        session,
                        interrupt_context=dispatch.get("interrupt_context"),
                        trigger_rollback=dispatch.get("rollback"),
                    )
                    if scheduled:
                        should_interrupt_listener = (
                            should_interrupt_listener
                            or self.workflow_interrupt_requested(session)
                        )
                else:
                    scheduled = self._execute_command_async(
                        cmd,
                        session,
                        interrupt_context=dispatch.get("interrupt_context"),
                        trigger_rollback=dispatch.get("rollback"),
                    )
                    if scheduled:
                        should_interrupt_listener = (
                            should_interrupt_listener
                            or bool(trigger.get("abort_on_match", True))
                        )
                if scheduled:
                    continue
                if dispatch.get("rollback"):
                    self._rollback_trigger_consumption(cmd, session, dispatch.get("rollback"))

        return should_interrupt_listener
    def has_network_interception_for_session(self, session: 'TabSession') -> bool:
        """当前会话是否存在可生效的网络异常拦截触发器。"""
        try:
            commands = self._load_commands_for_checks()
        except Exception:
            return False

        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            trigger = cmd.get("trigger", {})
            if trigger.get("type") != "network_request_error":
                continue
            if self._matches_scope(cmd, session):
                return True
        return False
    def get_network_listen_pattern(self, session: 'TabSession') -> str:
        """
        依据网络异常拦截命令推断一个 listen_pattern。
        仅用于事件监听，不要求完全精准。
        """
        try:
            commands = self._load_commands_for_checks()
        except Exception:
            return "http"

        hints: List[str] = []
        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            trigger = cmd.get("trigger", {})
            if trigger.get("type") != "network_request_error":
                continue
            if not self._matches_scope(cmd, session):
                continue

            pattern = str(trigger.get("url_pattern") or trigger.get("value") or "").strip()
            if not pattern:
                continue
            hint = self._pattern_to_listen_hint(pattern, str(trigger.get("match_mode", "keyword")))
            if hint:
                hints.append(hint)

        if not hints:
            return "http"
        hints.sort(key=len, reverse=True)
        return hints[0]
    def _ensure_trigger_state(
        self,
        command_id: str,
        session: 'TabSession',
        state_key: Optional[tuple] = None,
        initial_req: Optional[int] = None,
        initial_err: Optional[int] = None,
    ) -> tuple[Dict[str, Any], bool]:
        state_key = state_key or (command_id, session.id)
        req_baseline = session.request_count if initial_req is None else int(initial_req)
        err_baseline = session.error_count if initial_err is None else int(initial_err)
        with self._lock:
            state = self._trigger_states.get(state_key)
            if state is None:
                state = {
                    "req": req_baseline,
                    "err": err_baseline,
                    "result_token": "",
                    "net_sig": "",
                    "page_key": "",
                    "page_hit": False,
                    "page_last_fire_at": 0.0,
                }
                self._trigger_states[state_key] = state
                return state, True
            return state, False
    def _coerce_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default
    def _coerce_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default
    @staticmethod
    def _counter_inc(counter: Dict[str, int], key: str):
        if not key:
            return
        counter[key] = int(counter.get(key, 0)) + 1
    @staticmethod
    def _counter_dec(counter: Dict[str, int], key: str):
        if not key:
            return
        next_value = int(counter.get(key, 0)) - 1
        if next_value > 0:
            counter[key] = next_value
        else:
            counter.pop(key, None)
    def _normalize_priority(self, value: Any, default: int = 2) -> int:
        try:
            p = int(value)
        except Exception:
            p = int(default)
        return p
    def _get_request_priority_baseline(self) -> int:
        return self._normalize_priority(getattr(self, "_request_priority_baseline", 2), 2)
    def _get_command_priority(self, command: Dict) -> int:
        trigger = command.get("trigger", {}) or {}
        raw = trigger.get("priority", trigger.get("command_priority", 2))
        return self._normalize_priority(raw, 2)
    def _should_evaluate_page_check_while_busy_workflow(self, command: Dict) -> bool:
        trigger = command.get("trigger", {}) or {}
        raw = trigger.get("check_while_busy_workflow", None)
        if raw is None:
            return True
        return bool(raw)
    def _command_affects_domain(self, command: Dict) -> bool:
        actions = command.get("actions", [])
        for action in actions or []:
            if str((action or {}).get("type", "")).strip() == "clear_cookies":
                return True
        return False
    def _has_busy_peer_on_domain(self, domain: str, exclude_session_id: str = "") -> bool:
        normalized = str(domain or "").strip().lower()
        if not normalized:
            return False
        try:
            browser = self._get_browser()
            pool = getattr(browser, "_tab_pool", None)
            if pool is None:
                return False
            status = pool.get_status() if hasattr(pool, "get_status") else {}
            for tab in status.get("tabs", []) or []:
                sid = str(tab.get("id", "") or "")
                if exclude_session_id and sid == exclude_session_id:
                    continue
                if str(tab.get("status", "")).lower() != "busy":
                    continue
                tab_domain = str(tab.get("current_domain", "") or "").strip().lower()
                if self._domain_matches(normalized, tab_domain):
                    return True
        except Exception:
            return False
        return False
    def should_block_request_for_session(self, session: "TabSession", task_id: str = "") -> bool:
        if session is None:
            return False
        task = str(task_id or "").strip().lower()
        if task.startswith("cmd_") or task.startswith("group_") or task.startswith("cmd_test_") or task.startswith("group_test_"):
            return False

        session_id = str(getattr(session, "id", "") or "")
        domain = self._get_session_domain(session)
        with self._lock:
            if int(self._pending_high_by_session.get(session_id, 0)) > 0:
                return True
            if int(self._running_high_by_session.get(session_id, 0)) > 0:
                return True
            if domain and int(self._pending_high_by_domain.get(domain, 0)) > 0:
                return True
            if domain and int(self._running_high_by_domain.get(domain, 0)) > 0:
                return True
        return False
    def _get_session_domain(self, session: 'TabSession') -> str:
        try:
            url = str(getattr(session.tab, "url", "") or "")
            domain = str(extract_remote_site_domain(url) or "").strip().lower()
            if domain:
                session.current_domain = domain
                return domain
        except Exception:
            pass
        domain = str(getattr(session, "current_domain", "") or "").strip().lower()
        if domain:
            return domain
        return ""
    def _get_active_workflow_runtime(self, session: 'TabSession') -> Optional[Dict[str, Any]]:
        stack = getattr(session, "_workflow_runtime_stack", None) or []
        if not stack:
            return None
        runtime = stack[-1]
        return runtime if isinstance(runtime, dict) else None
    def _build_request_count_state_key(self, command: Dict, session: 'TabSession') -> tuple:
        trigger = command.get("trigger", {}) or {}
        scope = str(trigger.get("scope", "all") or "all").strip().lower()

        if scope == "all":
            return (command["id"], "__scope:all")

        if scope == "domain":
            domain_key = str(trigger.get("domain", "") or "").strip().lower()
            if not domain_key:
                domain_key = self._get_session_domain(session)
            return (command["id"], f"__scope:domain:{domain_key or '_'}")

        return (command["id"], session.id)
    def _get_scope_request_count(self, command: Dict, session: 'TabSession') -> int:
        trigger = command.get("trigger", {}) or {}
        scope = str(trigger.get("scope", "all") or "all").strip().lower()

        if scope == "tab":
            try:
                return int(getattr(session, "request_count", 0) or 0)
            except Exception:
                return 0

        try:
            browser = self._get_browser()
            pool = getattr(browser, "_tab_pool", None)
            if pool is None:
                return int(getattr(session, "request_count", 0) or 0)
            status = pool.get_status() if hasattr(pool, "get_status") else {}
            tabs = status.get("tabs", []) or []
        except Exception:
            return int(getattr(session, "request_count", 0) or 0)

        if scope == "all":
            total = 0
            for tab in tabs:
                try:
                    total += int(tab.get("request_count", 0) or 0)
                except Exception:
                    continue
            return total

        if scope == "domain":
            target_domain = str(trigger.get("domain", "") or "").strip().lower()
            if not target_domain:
                target_domain = self._get_session_domain(session)
            if not target_domain:
                return int(getattr(session, "request_count", 0) or 0)

            total = 0
            for tab in tabs:
                try:
                    tab_domain = str(tab.get("current_domain", "") or "").strip().lower()
                    if not tab_domain:
                        url = str(tab.get("url", "") or "")
                        if "://" in url:
                            tab_domain = url.split("//", 1)[1].split("/", 1)[0].strip().lower()
                    if self._domain_matches(target_domain, tab_domain):
                        total += int(tab.get("request_count", 0) or 0)
                except Exception:
                    continue
            return total

        try:
            return int(getattr(session, "request_count", 0) or 0)
        except Exception:
            return 0
    def _should_trigger(self, command: Dict, session: 'TabSession') -> bool:
        trigger = command.get("trigger", {})
        trigger_type = trigger.get("type", "")
        scope = trigger.get("scope", "all")

        # Scope pre-check
        if scope == "domain":
            target_domain = str(trigger.get("domain", "") or "").strip().lower()
            session_domain = self._get_session_domain(session)
            if target_domain and session_domain:
                if not self._domain_matches(target_domain, session_domain):
                    return False
            elif target_domain:
                return False
        elif scope == "tab":
            target_index = trigger.get("tab_index")
            if target_index is not None and session.persistent_index != target_index:
                return False

        # Skip if same command already executing on this tab
        exec_key = (command["id"], session.id)
        with self._lock:
            if exec_key in self._executing:
                return False

        request_state_key = None
        scope_request_count = None
        initial_req = None
        if trigger_type == "request_count":
            request_state_key = self._build_request_count_state_key(command, session)
            scope_request_count = self._get_scope_request_count(command, session)
            try:
                # Count the current completed request as the first hit so
                # a threshold of N fires on the Nth completed request rather
                # than the (N+1)th after state initialization.
                initial_req = max(0, int(scope_request_count) - 1)
            except Exception:
                initial_req = scope_request_count

        # Initialize or load trigger state
        state, is_new = self._ensure_trigger_state(
            command["id"],
            session,
            state_key=request_state_key,
            initial_req=initial_req if trigger_type == "request_count" else scope_request_count,
        )
        if is_new and trigger_type not in {"page_check", "command_check"}:
            return False  # Newly initialized: wait for next check cycle

        # Evaluate trigger condition by trigger type
        if trigger_type == "request_count":
            threshold = max(1, self._coerce_int(trigger.get("value", 10), 10))
            current_count = (
                int(scope_request_count)
                if scope_request_count is not None
                else self._get_scope_request_count(command, session)
            )
            with self._lock:
                baseline = int(state.get("req", 0))
                delta = current_count - baseline
                should_fire = delta >= threshold
                if should_fire:
                    # 先记录旧基线；若后续因标签页忙碌/超时未实际执行，可回滚以便重试。
                    state["req_prev"] = baseline
                    state["req_pending"] = True
                    state["req"] = current_count
            logger.debug_throttled(
                f"cmd.request_count.{command.get('id')}:{session.id}:{self._format_scope_label(scope)}",
                f"[CMD] 请求计数检查: {command.get('name')} "
                f"(当前={current_count}, 基线={baseline}, 增量={delta}, 阈值={threshold}, "
                f"标签页={session.id}, 范围={self._format_scope_label(scope)})",
                interval_sec=10.0,
            )
            if should_fire:
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(请求增量={delta}, 阈值={threshold}, 标签页={session.id}, "
                    f"范围={self._format_scope_label(scope)})"
                )
                return True

        elif trigger_type == "error_count":
            threshold = max(1, self._coerce_int(trigger.get("value", 3), 3))
            delta = session.error_count - state["err"]
            if delta >= threshold:
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(错误增量={delta}, 阈值={threshold})"
                )
                with self._lock:
                    state["err"] = session.error_count
                return True

        elif trigger_type == "idle_timeout":
            threshold_sec = max(1.0, self._coerce_float(trigger.get("value", 300), 300.0))
            idle = time.time() - session.last_used_at
            if idle >= threshold_sec:
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(空闲时长={idle:.0f}秒, 阈值={threshold_sec}秒)"
                )
                return True

        elif trigger_type == "page_check":
            check_text = str(trigger.get("value", ""))
            normalized_text = check_text.lower().strip()
            op, keywords = self._parse_page_check_expression(check_text)
            probe_js = str(trigger.get("probe_js", "") or "").strip()
            match_info = (
                self._evaluate_page_check_expr(session, op, keywords)
                if keywords else {
                    "hit": True if probe_js else False,
                    "matched_keywords": [],
                    "snapshot_preview": "",
                }
            )
            current_hit = bool(match_info.get("hit"))
            probe_always = bool(trigger.get("probe_always", False))
            if probe_js and (current_hit or probe_always):
                probe_info = self._evaluate_page_check_probe(session, probe_js)
                match_info["probe_hit"] = bool(probe_info.get("hit"))
                match_info["probe_result"] = probe_info.get("result")
                match_info["probe_summary"] = str(probe_info.get("summary") or "").strip()
                current_hit = bool(probe_info.get("hit"))
                match_info["hit"] = current_hit
            fire_mode = str(trigger.get("fire_mode", "edge") or "edge").strip().lower()
            cooldown_sec = max(0.0, self._coerce_float(trigger.get("cooldown_sec", 0), 0.0))
            stable_for_sec = max(0.0, self._coerce_float(trigger.get("stable_for_sec", 0), 0.0))
            now_ts = time.time()

            once_per_request = bool(trigger.get("once_per_request", False))
            current_request_id = ""
            if once_per_request:
                try:
                    for attr_name in ("_command_request_id", "_bound_request_id"):
                        req_id = str(getattr(session, attr_name, "") or "").strip()
                        if req_id:
                            current_request_id = req_id
                            break
                    if not current_request_id:
                        task_id = str(getattr(session, "current_task_id", "") or "").strip()
                        if task_id.startswith("req-"):
                            current_request_id = task_id
                except Exception:
                    pass

            with self._lock:
                if once_per_request and current_request_id:
                    triggered_reqs = state.setdefault("triggered_requests", set())
                    if current_request_id in triggered_reqs:
                        return False

                prev_key = str(state.get("page_key", ""))
                prev_hit = bool(state.get("page_hit", False)) if prev_key == normalized_text else False
                prev_stable = bool(state.get("page_stable", False)) if prev_key == normalized_text else False
                hit_since = float(state.get("page_hit_since", 0.0) or 0.0) if prev_key == normalized_text else 0.0
                if current_hit:
                    if not prev_hit or hit_since <= 0:
                        hit_since = now_ts
                else:
                    hit_since = 0.0
                state["page_key"] = normalized_text
                state["page_hit"] = current_hit
                state["page_hit_since"] = hit_since
                last_fire_at = float(state.get("page_last_fire_at", 0.0) or 0.0)
                stable_hit = bool(current_hit and ((now_ts - hit_since) >= stable_for_sec))
                state["page_stable"] = stable_hit

                if fire_mode == "level":
                    if stable_hit and (cooldown_sec <= 0 or (now_ts - last_fire_at) >= cooldown_sec):
                        if once_per_request and current_request_id:
                            triggered_reqs = state.setdefault("triggered_requests", set())
                            if current_request_id in triggered_reqs:
                                return False
                            triggered_reqs.add(current_request_id)
                        state["page_last_fire_at"] = now_ts
                        logger.info(
                            f"[CMD] 触发命令: {command.get('name')} "
                            f"(页面检查命中, 模式=持续触发, 文本='{check_text[:30]}', "
                            f"冷却={cooldown_sec}秒)"
                        )
                        self._log_page_check_hit_details(command, session, match_info)
                        return True
                else:
                    if stable_hit and not prev_stable:
                        if once_per_request and current_request_id:
                            triggered_reqs = state.setdefault("triggered_requests", set())
                            if current_request_id in triggered_reqs:
                                return False
                            triggered_reqs.add(current_request_id)
                        state["page_last_fire_at"] = now_ts
                        logger.info(
                            f"[CMD] 触发命令: {command.get('name')} "
                            f"(页面检查命中, 模式=边沿触发, 文本='{check_text[:30]}')"
                        )
                        self._log_page_check_hit_details(command, session, match_info)
                        return True

        elif trigger_type == "command_result_match":
            dispatch = self._prepare_command_result_trigger_dispatch(command, session)
            if dispatch:
                self._set_pending_async_trigger_meta(command, session, dispatch)
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(命中命令结果匹配条件)"
                )
                return True

        elif trigger_type == "command_check":
            dispatch = self._prepare_command_check_dispatch(command, session)
            if dispatch:
                self._set_pending_async_trigger_meta(command, session, dispatch)
                check_info = (dispatch.get("interrupt_context") or {}).get("command_check", {})
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(命令检查命中: 来源={check_info.get('source_command_name', '')}, "
                    f"结果={str(check_info.get('actual_result', '') or '')[:80]})"
                )
                return True

        elif trigger_type == "command_result_event":
            dispatch = self._prepare_command_result_event_dispatch(command, session)
            if dispatch:
                self._set_pending_async_trigger_meta(command, session, dispatch)
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(命中命令结果事件条件)"
                )
                return True

        elif trigger_type == "network_request_error":
            dispatch = self._prepare_network_trigger_dispatch(command, session)
            if dispatch:
                event = (dispatch.get("interrupt_context") or {}).get("network_event", {})
                self._set_pending_async_trigger_meta(command, session, dispatch)
                if (dispatch.get("rollback") or {}).get("token"):
                    logger.info(
                        f"[CMD] 触发命令: {command.get('name')} "
                        f"(网络异常状态码={event.get('status')}, 地址={event.get('url', '')[:80]})"
                    )
                    return True

        return False
    def _is_command_applicable_on_navigation(self, cmd: Dict[str, Any]) -> bool:
        """判断命令是否适用于页面导航/刷新事件触发。"""
        if not isinstance(cmd, dict) or not bool(cmd.get("enabled", True)):
            return False

        trigger = cmd.get("trigger", {}) or {}
        # 1. 显式配置 check_on_navigation / check_on_page_load
        if bool(trigger.get("check_on_navigation", False)) or bool(cmd.get("check_on_navigation", False)):
            return True
        if bool(trigger.get("check_on_page_load", False)) or bool(cmd.get("check_on_page_load", False)):
            return True

        # 2. page_check 触发器天然依赖页面 DOM/内容，页面跳转刷新后必须立即重新检查
        trigger_type = str(trigger.get("type", "")).strip().lower()
        if trigger_type == "page_check":
            return True

        # 3. 包含 bootstrap_on_session_ready 的 run_js_file 命令需要在页面就绪后注入/执行
        for action in list(cmd.get("actions") or []):
            if isinstance(action, dict) and bool(action.get("bootstrap_on_session_ready", False)):
                return True

        return False
    def notify_tab_navigated(
        self,
        session: 'TabSession',
        reason: str = "page_reload",
        async_dispatch: bool = True,
    ) -> None:
        """
        通用的页面导航/刷新通知接口。

        当标签页发生刷新（F5、location.reload、断流恢复刷新等）或导航（URL 跳转、新标签页打开等）时调用。

        执行操作：
        1. 即时重置 JS 执行失败退避锁（_pc_js_backoff_until = 0.0）和失败计数（_pc_js_failures = 0）
        2. 清空页面快照缓存与刷新宽限期，重置 observer 探测节流与缓存
        3. 清除 observer 缓存与 haystack 缓存，重置适用命令的周期倒计时与 edge 锁存
        4. 异步或同步派发页面导航后评估（_dispatch_tab_navigated_evaluation）：
           - 同步 bootstrap 启动 JS 文件注入（Page.addScriptToEvaluateOnNewDocument + 立即执行）
           - 重新注入/同步 MutationObserver
           - 毫秒级触发并评估适用命令
        """
        if session is None:
            return

        session_id = getattr(session, "id", "")
        if not session_id:
            return

        # 1. 同步清理 session 上的快照与退避状态
        try:
            setattr(session, "_pc_js_failures", 0)
            setattr(session, "_pc_js_backoff_until", 0.0)
            setattr(session, "_pc_refresh_grace_until", 0.0)
            setattr(session, "_pc_snapshot_cached", None)
            setattr(session, "_last_pc_observer_check_at", 0.0)
            setattr(session, "_pc_observer_empty_cleanup_done", False)
            if hasattr(self, "_invalidate_wake_throttle"):
                self._invalidate_wake_throttle(session)
        except Exception as e:
            logger.debug(f"[CMD] 重置会话退避状态失败（忽略）: {e}")

        # 2. 清理引擎层全局缓存与适用命令的状态
        try:
            commands = self._load_commands_for_checks()
        except Exception as e:
            logger.debug(f"[CMD] 导航通知加载命令失败: {e}")
            commands = []

        applicable_cmds = []
        with self._lock:
            self._observer_keywords_by_session.pop(session_id, None)
            self._compact_haystack_cache = None
            for cmd in commands:
                cmd_id = cmd.get("id")
                if not cmd_id:
                    continue
                if self._is_command_applicable_on_navigation(cmd):
                    self._periodic_next_run[(cmd_id, session_id)] = 0.0
                    applicable_cmds.append(cmd)

        for cmd in applicable_cmds:
            self._reset_page_check_latch(cmd, session, reason=f"navigated:{reason}")

        logger.info(
            f"[CMD] 标签页导航/刷新通知: session={session_id}, reason={reason}, async={async_dispatch}"
        )

        # 3. 派发评估
        if async_dispatch:
            self.submit_background_task(self._dispatch_tab_navigated_evaluation, session, reason)
        else:
            self._dispatch_tab_navigated_evaluation(session, reason)
    def _dispatch_tab_navigated_evaluation(
        self,
        session: 'TabSession',
        reason: str = "page_reload",
    ) -> None:
        """在页面导航/刷新后执行 bootstrap 脚本注入、observer 同步及命令评估调度。"""
        if self._is_session_closed(session):
            return

        self.ensure_scheduler_running()
        try:
            commands = self._load_commands_for_checks()
        except Exception as e:
            logger.debug(f"[CMD] 导航后评估加载命令失败: {e}")
            return

        if not commands:
            return

        # 1. 优先执行/注入 bootstrap 启动脚本（例如 Page.addScriptToEvaluateOnNewDocument 及即时注入）
        try:
            self._sync_session_bootstrap_js_files(commands, session)
        except Exception as e:
            logger.warning(f"[CMD] 导航后同步 bootstrap 脚本失败: {e}")

        # 2. 同步 Page Check Observer（如果有 page_check 关键词）
        try:
            pc_keywords = self._collect_page_check_keywords(commands, session)
            if pc_keywords:
                self._ensure_page_check_observer(session, pc_keywords)
            else:
                self._clear_page_check_observer(session)
        except Exception as e:
            logger.debug(f"[CMD] 导航后同步 observer 失败: {e}")

        # 3. 评估并调度适用的命令
        applicable_commands = [
            (idx, cmd) for idx, cmd in enumerate(commands)
            if self._is_command_applicable_on_navigation(cmd) and self._matches_scope(cmd, session)
        ]
        applicable_commands.sort(key=lambda item: (-self._get_command_priority(item[1]), item[0]))

        for _, cmd in applicable_commands:
            with self._command_logging_context(cmd):
                try:
                    current_status = str(getattr(getattr(session, "status", None), "value", "")).lower()
                    if current_status == "busy" and not self._has_active_workflow(session):
                        continue
                    if current_status not in {"idle", "busy"}:
                        continue
                    if self._should_trigger(cmd, session):
                        meta = self._take_pending_async_trigger_meta(cmd, session) or {}
                        if current_status == "busy":
                            scheduled = self._schedule_command_for_active_workflow(
                                cmd,
                                session,
                                interrupt_context=meta.get("interrupt_context"),
                                trigger_rollback=meta.get("rollback"),
                            )
                            if not scheduled:
                                if meta.get("rollback"):
                                    self._rollback_trigger_consumption(cmd, session, meta.get("rollback"))
                                self._finalize_request_count_trigger_state(cmd, session, rollback=True)
                                self._reset_page_check_latch(cmd, session, reason="workflow_schedule_failed")
                        else:
                            self._execute_command_async(
                                cmd,
                                session,
                                interrupt_context=meta.get("interrupt_context"),
                                trigger_rollback=meta.get("rollback"),
                            )
                except Exception as e:
                    logger.error(f"[CMD] 导航后触发检查失败 [{cmd.get('name')}]: {e}")
    def _finalize_request_count_trigger_state(
        self,
        command: Dict,
        session: 'TabSession',
        *,
        rollback: bool = False
    ):
        """
        收尾 request_count 触发状态：
        - rollback=True: 未实际执行时回滚 req 到触发前基线，避免触发被“吃掉”
        - rollback=False: 实际开始执行后清理 pending 标记
        """
        trigger = command.get("trigger", {}) or {}
        if str(trigger.get("type", "")).strip().lower() != "request_count":
            return

        key = self._build_request_count_state_key(command, session)
        with self._lock:
            state = self._trigger_states.get(key)
            if not state:
                return

            pending = bool(state.pop("req_pending", False))
            prev_req = state.pop("req_prev", None)
            if rollback and pending and prev_req is not None:
                try:
                    state["req"] = int(prev_req)
                except Exception:
                    pass
    @staticmethod
    def _execution_needs_page_check_retry(execution_result: Any) -> bool:
        """
        Determine whether a page_check-triggered command should be retried.
        Retry signal is inferred from:
        - Step-level ok flag being False (exception during execution)
        - Action results shaped like {"ok": False, ...}
        - String results containing known failure prefixes (e.g. "js_failed:")
        """
        if not isinstance(execution_result, dict):
            return False

        _FAIL_PREFIXES = (
            "js_failed:",
            "python_failed:",
            "unsupported_lang:",
            "ERROR:",
            "refresh_failed:",
            "navigate_failed:",
        )

        direct_result = execution_result.get("result")
        if isinstance(direct_result, dict) and direct_result.get("ok") is False:
            return True
        if isinstance(direct_result, str) and direct_result.startswith(_FAIL_PREFIXES):
            return True

        steps = execution_result.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                # Check step-level ok flag (set by _execute_simple on exceptions)
                if step.get("ok") is False:
                    return True
                step_result = step.get("result")
                if isinstance(step_result, dict) and step_result.get("ok") is False:
                    return True
                if isinstance(step_result, str) and step_result.startswith(_FAIL_PREFIXES):
                    return True
        return False
    def _normalize_match_rule(self, rule: Any) -> str:
        rule_value = str(rule or "").strip().lower()
        mapping = {
            "eq": "equals",
            "equal": "equals",
            "equals": "equals",
            "is": "equals",
            "contains": "contains",
            "include": "contains",
            "includes": "contains",
            "ne": "not_equals",
            "not_equal": "not_equals",
            "not_equals": "not_equals",
        }
        return mapping.get(rule_value, "equals")
