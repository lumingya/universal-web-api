"""TabPoolManager 的一部分（R2-3 拆分）：分配顺序与游标、全局网络监听的交接（借出/归还时的启停）。

方法原样从 manager.py 迁出，行为不变；通过 mixin 组合回 TabPoolManager，
类属性与实例状态都在 TabPoolManager（manager.py）里定义。
"""

import random
import threading
import time
from typing import Any, List, Optional

from app.core.config import logger

from .session import TabSession, TabStatus


class TabPoolAllocationMixin:
    """分配顺序与游标、全局网络监听的交接（借出/归还时的启停）"""

    def _order_sessions_for_allocation(
        self,
        sessions: List[TabSession],
        *,
        route_domain: Optional[str] = None,
        allocation_mode: Optional[str] = None,
    ) -> List[TabSession]:
        ordered = sorted(list(sessions or []), key=self._session_allocation_key)
        mode = self._normalize_allocation_mode(allocation_mode or self.allocation_mode)
        if mode == "random" and len(ordered) > 1:
            randomized = ordered[:]
            random.shuffle(randomized)
            return randomized

        if mode != "round_robin" or len(ordered) <= 1:
            return ordered

        cursor = (
            self._route_round_robin_cursor.get(route_domain, 0)
            if route_domain
            else self._round_robin_cursor
        )
        cursor_index = self._persistent_index_value(cursor)
        next_items = [
            item for item in ordered
            if self._persistent_index_value(item.persistent_index) > cursor_index
        ]
        return next_items + [
            item for item in ordered
            if self._persistent_index_value(item.persistent_index) <= cursor_index
        ]
    @staticmethod
    def _persistent_index_value(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
    @classmethod
    def _session_allocation_key(cls, session: TabSession):
        return (
            cls._persistent_index_value(getattr(session, "persistent_index", 0)),
            str(getattr(session, "id", "") or ""),
        )
    def _select_first_idle_acquire_candidate(
        self,
        sessions,
        task_id: str,
        attempted_ids: set,
        *,
        defer_context: str = "acquire",
    ) -> Optional[TabSession]:
        selected = None
        selected_key = None
        for session in sessions or []:
            session_id = str(getattr(session, "id", "") or "")
            if session_id in attempted_ids:
                continue
            if session.status != TabStatus.IDLE:
                continue
            if not session.is_healthy(allow_live_check=False):
                logger.warning(f"[{session.id}] skip unhealthy tab")
                continue
            if self._should_defer_to_command(session, task_id):
                logger.debug(f"[{session.id}] defer {defer_context} to high-priority command")
                continue

            candidate_key = self._session_allocation_key(session)
            if selected is None or candidate_key < selected_key:
                selected = session
                selected_key = candidate_key

        return selected
    def _try_acquire_session_for_request(
        self,
        sessions,
        task_id: str,
        *,
        route_domain: Optional[str] = None,
        allocation_mode: Optional[str] = None,
        defer_context: str = "acquire",
    ) -> Optional[TabSession]:
        mode = self._normalize_allocation_mode(allocation_mode or self.allocation_mode)
        if mode == "first_idle":
            attempted_ids = set()
            while True:
                session = self._select_first_idle_acquire_candidate(
                    sessions,
                    task_id,
                    attempted_ids,
                    defer_context=defer_context,
                )
                if session is None:
                    return None
                attempted_ids.add(str(session.id or ""))
                if session.acquire(task_id):
                    return session

        for session in self._order_sessions_for_allocation(
            sessions,
            route_domain=route_domain,
            allocation_mode=mode,
        ):
            if session.status != TabStatus.IDLE:
                continue
            if not session.is_healthy(allow_live_check=False):
                logger.warning(f"[{session.id}] skip unhealthy tab")
                continue
            if self._should_defer_to_command(session, task_id):
                logger.debug(f"[{session.id}] defer {defer_context} to high-priority command")
                continue
            if session.acquire(task_id):
                return session
        return None
    def _mark_allocation_cursor(self, session: TabSession, route_domain: Optional[str] = None) -> None:
        current_index = int(session.persistent_index or 0)
        if route_domain:
            self._route_round_robin_cursor[route_domain] = current_index
            self._route_round_robin_cursor.move_to_end(route_domain)
            while len(self._route_round_robin_cursor) > self.ROUTE_CURSOR_LIMIT:
                self._route_round_robin_cursor.popitem(last=False)
        else:
            self._round_robin_cursor = current_index
    def _get_session_for_monitor(self, session_id: str) -> Optional[TabSession]:
        with self._lock:
            return self._tabs.get(session_id)
    def _get_session_for_monitor_snapshot(self, session_id: str) -> Optional[TabSession]:
        # Global monitor workers may be joined while the pool lock is held elsewhere.
        # Use a lock-free snapshot lookup here so the worker can observe removal and exit
        # instead of blocking behind the pool lock during shutdown/rebind paths.
        return self._tabs.get(session_id)
    def _start_global_monitor_for_session(self, session: Optional[TabSession]) -> bool:
        if not session or not self._global_network_monitor:
            return False
        transition_lock = getattr(self, "_global_monitor_transition_lock", None)
        if transition_lock is None:
            transition_lock = self._global_monitor_transition_lock = threading.RLock()
        # Start and stop use one transition lock. If acquisition changes the
        # session to BUSY during a delayed start, its handoff stop waits here and
        # removes the newly registered worker before returning the session.
        with transition_lock:
            if self._shutdown:
                return False
            if self._tabs.get(session.id) is not session:
                return False
            # 仅在空闲标签页常驻监听，任务执行时让位
            if session.status != TabStatus.IDLE:
                return False
            return bool(self._global_network_monitor.start_for_session(session))
    def _restart_global_monitor_for_session_async(self, session_id: str, reason: str) -> None:
        """Restart an idle session monitor outside the pool lock after a target rebind."""
        session_key = str(session_id or "").strip()
        executor = self._maintenance_executor
        if not session_key or executor is None or not self._global_network_monitor:
            return

        def _restart() -> None:
            deadline = time.monotonic() + 5.0
            while True:
                with self._lock:
                    if self._shutdown:
                        return
                    session = self._tabs.get(session_key)
                    if session is None or session.status != TabStatus.IDLE:
                        return
                if self._start_global_monitor_for_session(session):
                    # logger.debug(f"[GlobalNet] rebound listener restarted: {session_key} ({reason})")
                    return
                if time.monotonic() >= deadline:
                    logger.warning(f"[GlobalNet] rebound listener restart failed: {session_key} ({reason})")
                    return
                time.sleep(0.1)

        try:
            executor.submit(_restart)
        except RuntimeError as e:
            logger.debug(f"[GlobalNet] restart submit failed ({session_key}): {e}")
    def _stop_global_monitor_for_session(self, session_id: str, reason: str = "", wait: bool = False) -> bool:
        if not self._global_network_monitor:
            return True
        transition_lock = getattr(self, "_global_monitor_transition_lock", None)
        if transition_lock is None:
            transition_lock = self._global_monitor_transition_lock = threading.RLock()
        # Do not hold the pool lock while joining a worker: command callbacks can
        # legitimately need that lock while the worker is winding down.
        with transition_lock:
            monitor = self._global_network_monitor
            if not monitor:
                return True
            return bool(monitor.stop_for_session(session_id, reason=reason, join=wait))
    def _request_global_monitor_stop_for_session(
        self,
        session_id: str,
        reason: str = "",
        *,
        detach: bool = False,
    ) -> bool:
        if not self._global_network_monitor:
            return True
        request_stop = getattr(self._global_network_monitor, "request_stop_for_session", None)
        if callable(request_stop):
            return bool(request_stop(session_id, reason=reason, detach=detach))
        return self._stop_global_monitor_for_session(session_id, reason=reason, wait=False)
    def _detach_global_monitor_for_session(
        self,
        session_id: str,
        reason: str = "",
    ) -> bool:
        session_id = str(session_id or "").strip()
        if not session_id:
            return True
        if not self._global_network_monitor:
            return True
        return self._request_global_monitor_stop_for_session(
            session_id,
            reason=reason,
            detach=True,
        )
    def _prepare_acquired_session_for_handoff(
        self,
        session: TabSession,
        reason: str,
        *,
        rollback_request_count: bool = True,
    ) -> bool:
        self._request_global_monitor_stop_for_session(session.id, reason=reason)
        return True
    def _finish_acquired_session_handoff(
        self,
        session: TabSession,
        reason: str,
        task_id: str,
        *,
        rollback_request_count: bool = True,
    ) -> bool:
        if self._stop_global_monitor_for_session(session.id, reason=reason, wait=True):
            return True

        logger.warning(
            f"[{session.id}] global monitor did not stop before handoff "
            f"(reason={reason}); marking session unhealthy"
        )
        expected_task = str(task_id or "").strip()
        release_state = None
        with self._condition:
            current = self._tabs.get(session.id)
            current_task = str(getattr(session, "current_task_id", "") or "").strip()
            if (
                current is session
                and session.status == TabStatus.BUSY
                and (not expected_task or current_task == expected_task)
            ):
                release_state = session._begin_release_state(
                    clear_page=False,
                    rollback_request_count=rollback_request_count,
                    force=False,
                )

        if release_state is not None:
            session._run_release_from_state(
                release_state,
                clear_page=False,
                check_triggers=False,
                rollback_request_count=rollback_request_count,
            )

        with self._condition:
            current = self._tabs.get(session.id)
            current_task = str(getattr(session, "current_task_id", "") or "").strip()
            if current is session and (not expected_task or not current_task or current_task == expected_task):
                session.mark_error("global_monitor_stop_timeout")
            self._condition.notify_all()
        return False
    def _complete_acquired_session_for_return(
        self,
        session: TabSession,
        reason: str,
        task_id: str,
        *,
        rollback_request_count: bool = True,
        activate: bool = False,
    ) -> bool:
        self._prepare_acquired_session_for_handoff(
            session,
            reason,
            rollback_request_count=rollback_request_count,
        )

        self._condition.release()
        try:
            handoff_ok = self._finish_acquired_session_handoff(
                session,
                reason,
                task_id,
                rollback_request_count=rollback_request_count,
            )
        finally:
            self._condition.acquire()

        if not handoff_ok:
            return False

        current_task = str(getattr(session, "current_task_id", "") or "").strip()
        expected_task = str(task_id or "").strip()
        if (
            self._tabs.get(session.id) is not session
            or session.status != TabStatus.BUSY
            or (expected_task and current_task != expected_task)
        ):
            logger.warning(
                f"[{session.id}] acquire handoff lost ownership "
                f"(reason={reason}, expected_task={expected_task or '-'}, "
                f"current_task={current_task or '-'}, status={session.status.value})"
            )
            return False

        if activate:
            self._condition.release()
            try:
                session.activate()
            finally:
                self._condition.acquire()

            current_task = str(getattr(session, "current_task_id", "") or "").strip()
            if (
                self._tabs.get(session.id) is not session
                or session.status != TabStatus.BUSY
                or (expected_task and current_task != expected_task)
            ):
                logger.warning(
                    f"[{session.id}] acquire activation lost ownership "
                    f"(reason={reason}, expected_task={expected_task or '-'}, "
                    f"current_task={current_task or '-'}, status={session.status.value})"
                )
                return False
            self._active_session_id = session.id
        return True
    def suspend_global_network_monitor(self, tab_id: str, reason: str = "manual"):
        self._stop_global_monitor_for_session(tab_id, reason=reason)
    def resume_global_network_monitor(self, tab_id: str, reason: str = "manual"):
        with self._lock:
            session = self._tabs.get(tab_id)
        if not session:
            return
        if session.status != TabStatus.IDLE or not session.is_healthy():
            return
        self._start_global_monitor_for_session(session)
        logger.debug(f"[GlobalNet] 恢复监听: {tab_id} ({reason})")
