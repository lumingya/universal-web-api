"""CommandEngine 的一部分（R2-3 拆分）：周期调度：保活、标签页池刷新、周期检查循环。

方法原样从 command_engine.py 迁出，行为不变；通过 mixin 组合回 CommandEngine，
类属性与实例状态都在 CommandEngine（command_engine.py）里定义。
"""

import os
import random
import threading
import time
from typing import Dict, List, Any, TYPE_CHECKING
from app.services.command_engine_common import logger

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession  # noqa: F401


class CommandEngineSchedulerMixin:
    """周期调度：保活、标签页池刷新、周期检查循环"""

    @staticmethod
    def _should_auto_start_scheduler() -> bool:
        # The application lifespan explicitly owns scheduler startup/shutdown.
        # Starting here makes a plain module import (including pytest test
        # collection) leak a background thread with no matching shutdown.
        return str(os.getenv("CMD_ENGINE_AUTO_START", "false")).strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }
    def _should_log_periodic_summary(self, now_ts: float, due_total: int) -> bool:
        if not self._periodic_summary_log_enabled:
            return False
        interval = (
            self._periodic_active_log_interval_sec
            if due_total > 0
            else self._periodic_idle_log_interval_sec
        )
        if (now_ts - self._last_periodic_summary_log_at) < interval:
            return False
        self._last_periodic_summary_log_at = now_ts
        return True
    def _refresh_tab_pool_if_due(self, pool: Any):
        if not self._tab_pool_auto_refresh:
            return
        now = time.time()
        if (now - self._last_tab_pool_refresh_at) < self._tab_pool_refresh_interval_sec:
            return
        self._last_tab_pool_refresh_at = now
        try:
            if hasattr(pool, "refresh_tabs"):
                pool.refresh_tabs()
        except Exception as e:
            logger.debug(f"[CMD] 刷新标签页池失败（忽略）: {e}")
    def _maybe_periodic_keepalive(self, session: 'TabSession', now_ts: float):
        if not self._periodic_keepalive_enabled:
            return
        sid = str(getattr(session, "id", "") or "")
        if not sid:
            return
        last_at = float(self._last_keepalive_by_session.get(sid, 0.0) or 0.0)
        if (now_ts - last_at) < self._periodic_keepalive_interval_sec:
            return
        self._last_keepalive_by_session[sid] = now_ts
        self._try_wake_tab(session, reason="periodic_keepalive")
    def _start_periodic_scheduler(self):
        with self._scheduler_lifecycle_lock:
            if self._shutdown_requested:
                return False
            if self._periodic_thread and self._periodic_thread.is_alive():
                return True
            self._periodic_stop_event.clear()
            self._periodic_thread = threading.Thread(
                target=self._periodic_loop,
                daemon=True,
                name="cmd-periodic-checker",
            )
            self._periodic_thread.start()
        logger.debug("[CMD] 周期调度器已启动")
        return True
    def is_scheduler_running(self) -> bool:
        with self._scheduler_lifecycle_lock:
            thread = self._periodic_thread
            return bool(
                not self._shutdown_requested
                and thread
                and thread.is_alive()
            )
    def ensure_scheduler_running(self):
        """Best-effort watchdog: start periodic checker if it is not running."""
        return self._start_periodic_scheduler()
    def _periodic_loop(self):
        while not self._periodic_stop_event.wait(1.0):
            try:
                self._run_periodic_checks()
            except Exception as e:
                logger.debug(f"[CMD] 周期调度循环异常（忽略）: {e}")
    def _get_periodic_trigger_timing(self, trigger: Dict, session_id: str) -> tuple[float, float]:
        interval = max(1.0, self._coerce_float(trigger.get("periodic_interval_sec", 8), 8.0))
        jitter = max(0.0, self._coerce_float(trigger.get("periodic_jitter_sec", 2), 2.0))

        trigger_type = str(trigger.get("type", "")).strip().lower()
        has_keyword_expression = bool(str(trigger.get("value", "") or "").strip())
        if trigger_type == "page_check" and has_keyword_expression:
            with self._lock:
                has_observer = session_id in self._observer_keywords_by_session
            if has_observer:
                interval = min(interval, 1.5)
                jitter = 0.0

        return interval, jitter
    def _run_periodic_checks(self):
        try:
            commands = self._load_commands_for_checks()
        except Exception:
            return
        if not commands:
            return

        try:
            browser = self._get_browser()
        except Exception:
            return

        pool = getattr(browser, "_tab_pool", None)
        if pool is None:
            try:
                pool = browser.tab_pool
                logger.debug("[CMD] 周期调度器已初始化标签页池")
            except Exception as e:
                now = time.time()
                if (now - self._last_tab_pool_wait_log_at) >= 10:
                    self._last_tab_pool_wait_log_at = now
                    logger.debug(f"[CMD] 周期调度器等待标签页池初始化: {e}")
                return

        if not hasattr(pool, "get_idle_sessions_snapshot"):
            return
        self._refresh_tab_pool_if_due(pool)

        if hasattr(pool, "get_sessions_snapshot"):
            sessions = pool.get_sessions_snapshot()
        else:
            sessions = pool.get_idle_sessions_snapshot()
        if not sessions:
            return
        current_session_ids = {str(getattr(s, "id", "") or "") for s in sessions}

        now = time.time()
        active_keys = set()
        due_total = 0

        enabled_commands = [
            (idx, cmd) for idx, cmd in enumerate(commands)
            if cmd.get("enabled", True)
        ]

        for session in sessions:
            session_id = str(getattr(session, "id", "") or "")
            if not session_id or session_id not in current_session_ids:
                continue
            session_status = self._session_status_value(session)
            if session_status not in {"idle", "busy"}:
                continue
            is_busy_workflow = session_status == "busy" and self._has_active_workflow(session)
            if session_status == "busy" and not is_busy_workflow:
                continue
            if session_status == "idle":
                self._maybe_periodic_keepalive(session, now)
                try:
                    self._sync_session_bootstrap_js_files(commands, session)
                except Exception as error:
                    logger.debug(f"[CMD] 启动 JS 文件同步异常（忽略）: {error}")

            # Inject/update MutationObserver for page_check keywords on this session
            try:
                pc_keywords = self._collect_page_check_keywords(commands, session)
                if pc_keywords:
                    self._ensure_page_check_observer(session, pc_keywords)
                else:
                    self._clear_page_check_observer(session)
            except Exception:
                pass

            due_commands: List[tuple[int, int, Dict[str, Any]]] = []
            for idx, cmd in enabled_commands:
                cmd_id = str(cmd.get("id", "")).strip()
                if not cmd_id:
                    continue

                trigger = cmd.get("trigger", {}) or {}
                if not bool(trigger.get("periodic_enabled", True)):
                    continue

                trigger_type = str(trigger.get("type", "")).strip().lower()
                if (
                    is_busy_workflow
                    and trigger_type == "page_check"
                    and not self._should_evaluate_page_check_while_busy_workflow(cmd)
                ):
                    # While a workflow is actively running, low-priority page_check
                    # commands should not consume transient intermediate states.
                    continue

                key = (cmd_id, session.id)
                active_keys.add(key)

                interval, jitter = self._get_periodic_trigger_timing(trigger, session_id)

                with self._lock:
                    next_at = float(self._periodic_next_run.get(key, 0.0))
                if now < next_at:
                    continue

                delay = interval + (random.uniform(0.0, jitter) if jitter > 0 else 0.0)
                with self._lock:
                    self._periodic_next_run[key] = now + delay

                due_commands.append((self._get_command_priority(cmd), idx, cmd))

            due_total += len(due_commands)
            due_commands.sort(key=lambda item: (-item[0], item[1]))
            for _, _, cmd in due_commands:
                with self._command_logging_context(cmd):
                    current_status = str(getattr(getattr(session, "status", None), "value", "")).lower()
                    if current_status == "busy" and not self._has_active_workflow(session):
                        break
                    if current_status not in {"idle", "busy"}:
                        break
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

        if self._should_log_periodic_summary(now, due_total):
            logger.debug(
                f"[CMD] 周期检查: 会话数={len(sessions)}, "
                f"已启用周期命令={len(active_keys)}, 本轮到点={due_total}"
            )

        with self._lock:
            stale_keys = [k for k in self._periodic_next_run if k not in active_keys]
            for key in stale_keys:
                self._periodic_next_run.pop(key, None)
            stale_keepalive_keys = [k for k in self._last_keepalive_by_session if k not in {s.id for s in sessions}]
            for key in stale_keepalive_keys:
                self._last_keepalive_by_session.pop(key, None)
