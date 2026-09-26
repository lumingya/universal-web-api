"""TabPoolManager 的一部分（R2-3 拆分）：借用与归还：等待队列、通用/按编号/按原始 ID 借用、释放。

方法原样从 manager.py 迁出，行为不变；通过 mixin 组合回 TabPoolManager，
类属性与实例状态都在 TabPoolManager（manager.py）里定义。
"""

import asyncio
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from app.core.config import logger

from .session import TabSession, TabStatus


class TabPoolAcquireMixin:
    """借用与归还：等待队列、通用/按编号/按原始 ID 借用、释放"""

    def _normalize_acquire_timeout(self, timeout: Optional[float]) -> float:
        """统一各 acquire 路径的 timeout 语义。

        原先大多数路径写 `timeout = timeout or self.acquire_timeout`，于是
        timeout=0（调用方想表达"不等待，立刻失败"）会被当成"未传"而退化成默认
        60 秒阻塞，只有 acquire_by_route_group 一条路径按显式值处理。
        统一成：None -> 池默认；其余按显式值处理，负数收敛到 0。
        """
        if timeout is None:
            return float(self.acquire_timeout)
        try:
            return max(0.0, float(timeout))
        except (TypeError, ValueError):
            return float(self.acquire_timeout)
    @staticmethod
    def _request_should_stop(task_id: str) -> bool:
        """Return whether the request owning a blocking acquire was cancelled."""
        request_id = str(task_id or "").strip()
        if not request_id:
            return False
        try:
            from app.services.request_manager import request_manager

            context = request_manager.get_request(request_id)
            return bool(context is not None and context.should_stop())
        except Exception:
            # Pool acquisition is also used by command/internal callers that do
            # not have a RequestContext; cancellation lookup must stay best effort.
            return False
    def _total_acquire_waiters(self) -> int:
        total = len(self._acquire_waiters)
        for group in (self._index_waiters, self._route_waiters, self._group_waiters):
            for waiters in group.values():
                total += len(waiters)
        return total
    def _acquire_queue_full(self, task_id: str) -> bool:
        """H9：所有 acquire 等待队列的总量上限（调用方持有 self._condition）。

        满了立即返回 True：调用方直接返回 None，并记下该 task 是「队列满」而不是
        「没有标签页」，工作流层据此给出明确的 503（可重试）而不是再排 60 秒。
        """
        limit = int(getattr(self, "_max_acquire_waiters", 0) or 0)
        if limit <= 0:
            return False
        total = self._total_acquire_waiters()
        if total < limit:
            return False
        rejections = getattr(self, "_queue_full_rejections", None)
        if rejections is None:
            rejections = OrderedDict()
            self._queue_full_rejections = rejections
        rejections[str(task_id or "")] = time.time()
        while len(rejections) > 256:
            rejections.popitem(last=False)
        logger.warning(
            f"[TabPool] acquire 等待队列已满（{total}/{limit}），立即拒绝 task={task_id}"
        )
        return True
    def consume_queue_full_rejection(self, task_id: str) -> bool:
        """工作流层查询：该 task 的 acquire 是否因等待队列已满被拒（查询即清除）。"""
        rejections = getattr(self, "_queue_full_rejections", None)
        if not rejections:
            return False
        with self._condition:
            return rejections.pop(str(task_id or ""), None) is not None
    def _next_waiter_token(self, task_id: str) -> str:
        self._waiter_counter += 1
        base = str(task_id or "task").strip() or "task"
        return f"{base}#{self._waiter_counter}"
    @staticmethod
    def _is_waiter_turn(waiters: deque[str], waiter_token: str) -> bool:
        return not waiters or waiters[0] == waiter_token
    @staticmethod
    def _count_waiters_ahead(waiters: deque[str], waiter_token: str) -> int:
        try:
            return waiters.index(waiter_token)
        except ValueError:
            return 0
    def _unregister_waiter(
        self,
        waiters: deque[str],
        waiter_token: str,
        *,
        owner_map: Optional[Dict[Any, deque[str]]] = None,
        owner_key: Optional[Any] = None,
    ) -> bool:
        removed = False
        try:
            waiters.remove(waiter_token)
            removed = True
        except ValueError:
            removed = False

        # 只有当 owner_map 里登记的仍是同一个队列对象时才摘除。
        # 成功路径会先显式 _unregister_waiter 一次（此时队列可能已空并被摘除），
        # 随后 finally 再调一次；期间池锁可能被 _complete_acquired_session_for_return
        # 短暂释放，其他线程会用同一个 key 建出**新的**队列。若这里无条件 pop，
        # 就会把新队列连同里面排队的等待者一起丢掉，破坏 FIFO 公平性。
        if (
            owner_map is not None
            and owner_key is not None
            and not waiters
            and owner_map.get(owner_key) is waiters
        ):
            owner_map.pop(owner_key, None)

        if removed:
            self._condition.notify_all()
        return removed
    @staticmethod
    def _describe_session(session: Optional[TabSession]) -> str:
        if session is None:
            return "session=-"
        try:
            return session.debug_summary()
        except Exception as e:
            return f"session={getattr(session, 'id', '-')}, snapshot_error={e}"
    def _describe_index_wait_state(
        self,
        persistent_index: int,
        waiters: deque[str],
        waiter_token: str,
    ) -> str:
        session_id = self._persistent_to_session_id.get(persistent_index)
        session = self._tabs.get(session_id) if session_id else None
        ahead = self._count_waiters_ahead(waiters, waiter_token)
        busy_for = "-"
        if session is not None:
            try:
                if getattr(session, "status", None) == TabStatus.BUSY:
                    busy_for = (
                        f"{max(0.0, time.time() - float(getattr(session, 'last_used_at', 0.0) or 0.0)):.1f}s"
                    )
            except Exception:
                busy_for = "?"
        return (
            f"idx=#{persistent_index}, ahead={ahead}, waiters={len(waiters)}, "
            f"busy_for={busy_for}, holder={self._describe_session(session)}"
        )
    async def acquire_async(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        return await self._run_cancellable_acquire(
            lambda: self.acquire(task_id, timeout),
            task_id,
        )
    def _ensure_acquire_executor(self) -> ThreadPoolExecutor:
        # 双重检查懒创建：读属性无锁，创建走专用小锁（不碰池锁，事件循环线程不会
        # 被池维护路径阻塞）。max_workers=32 覆盖"池满 + 一批客户端断连"的堆积场景。
        executor = self._acquire_executor
        if executor is not None:
            return executor
        with self._acquire_executor_lock:
            if self._acquire_executor is None:
                self._acquire_executor = ThreadPoolExecutor(
                    max_workers=32,
                    thread_name_prefix="tab-acquire",
                )
            return self._acquire_executor
    async def _run_cancellable_acquire(self, acquire_fn, task_id: str) -> Optional[TabSession]:
        """Release a session acquired after the awaiting task was cancelled."""
        # 改用专用 executor：默认 executor 线程数有限（约 min(32, cpu+4)），被取消的
        # acquire 仍会占线程直到超时，占满后所有 to_thread 排不上队。
        # run_in_executor 返回的 Future 同样兼容 shield 与 add_done_callback，
        # shield 语义不变：外层取消不打断底层 acquire 线程，结果由回调兜底释放。
        loop = asyncio.get_running_loop()
        acquire_task = loop.run_in_executor(self._ensure_acquire_executor(), acquire_fn)
        try:
            return await asyncio.shield(acquire_task)
        except asyncio.CancelledError:
            def _release_late_session(completed_task) -> None:
                try:
                    session = completed_task.result()
                except BaseException:
                    return
                if session is None:
                    return

                def _do_release() -> None:
                    try:
                        self.release(
                            session.id,
                            check_triggers=False,
                            rollback_request_count=True,
                            expected_task_id=task_id,
                        )
                    except Exception as exc:
                        logger.warning(
                            f"Cancelled acquire cleanup failed (task={task_id}, "
                            f"tab={getattr(session, 'id', '-')}, error={exc})"
                        )

                # 锁序考量：done callback 在事件循环线程执行，release 含池锁竞争与
                # CDP 调用（visibility 恢复各 0.5s 超时），不能同步跑在事件循环里。
                # 投递到 maintenance executor；submit 失败（已关闭）回退 daemon 线程。
                executor = self._maintenance_executor
                submitted = False
                if executor is not None:
                    try:
                        executor.submit(_do_release)
                        submitted = True
                    except RuntimeError as exc:
                        logger.debug(
                            f"[TabPool] maintenance submit failed "
                            f"(late-release:{getattr(session, 'id', '-')}): {exc}"
                        )
                if not submitted:
                    threading.Thread(
                        target=_do_release,
                        name="tab-late-release",
                        daemon=True,
                    ).start()

            acquire_task.add_done_callback(_release_late_session)
            raise
    def release(
        self,
        tab_id: str,
        clear_page: bool = False,
        check_triggers: bool = True,
        rollback_request_count: bool = False,
        expected_task_id: str = "",
    ) -> bool:
        """释放标签页"""
        with self._condition:
            session = self._tabs.get(tab_id)
            if not session:
                return False

            before_snapshot = self._describe_session(session)
            expected_task = str(expected_task_id or "").strip()
            current_task = str(getattr(session, "current_task_id", "") or "").strip()
            session_status = getattr(getattr(session, "status", None), "value", "")
            if current_task and not expected_task:
                logger.warning(
                    f"[{tab_id}] 跳过无所有权释放 "
                    f"(current_task={current_task}, snapshot={before_snapshot})"
                )
                return False
            if expected_task:
                if current_task and current_task != expected_task:
                    logger.warning(
                        f"[{tab_id}] 跳过释放：标签页已被其他任务接管 "
                        f"(expected_task={expected_task}, current_task={current_task}, "
                        f"snapshot={before_snapshot})"
                    )
                    return False
                if not current_task:
                    logger.warning(
                        f"[{tab_id}] 跳过释放：标签页 task_id 已丢失 "
                        f"(expected_task={expected_task}, status={session_status or 'unknown'}, "
                        f"snapshot={before_snapshot})"
                    )
                    return False

            release_state = session._begin_release_state(
                clear_page=clear_page,
                rollback_request_count=rollback_request_count,
                force=False,
                expected_task_id=expected_task,
                reject_ownerless=True,
            )
            if release_state is None:
                self._condition.notify_all()
                return False

        session._run_release_from_state(
            release_state,
            clear_page=clear_page,
            check_triggers=check_triggers,
            rollback_request_count=rollback_request_count,
        )

        with self._condition:
            if self._tabs.get(tab_id) is session:
                # 锁序考量：不在持池锁时同步启动 monitor——start_for_session 可能
                # join 停止中的 worker 最长 2s（本文件他处已注明持池锁禁止 join worker）。
                # 复用 _restart_global_monitor_for_session_async 投递到 maintenance
                # executor：它会在池锁外复核会话仍存在且 IDLE 再启动，start_for_session
                # 本身幂等（已有存活 worker 时直接返回）；若会话已被再次 acquire，
                # 复核发现非 IDLE 即放弃，acquire 侧的 handoff stop 亦会兜底摘除。
                self._restart_global_monitor_for_session_async(tab_id, "release")
                self._condition.notify_all()
                logger.debug(
                    f"[{tab_id}] 已释放 "
                    f"(expected_task={expected_task or '-'}, clear_page={clear_page}, "
                    f"check_triggers={check_triggers}, rollback_request_count={rollback_request_count}, "
                    f"before={before_snapshot}, after={self._describe_session(session)})"
                )
                return session.status == TabStatus.IDLE
        return False
    def force_release_all(self):
        """强制释放所有标签页（调试用）"""
        with self._condition:
            pending: List[tuple[TabSession, Dict[str, Any]]] = []
            for session in self._tabs.values():
                if session.status == TabStatus.BUSY:
                    # 先请求取消旧工作流再强制释放：否则 BUSY 会话被洗成 IDLE 后，
                    # 旧工作流尚未退出，同一 tab 可能立刻被再分配，新旧任务并发操作
                    # 同一页面。锁序安全：_cancel_active_request_for_session 只向
                    # maintenance executor 投递，持池锁时不外呼。
                    busy_task = str(session.current_task_id or "").strip()
                    cancel_submitted = self._cancel_active_request_for_session(
                        session,
                        "force_release_all",
                        detail=f"task={busy_task or '-'}",
                    )
                    logger.warning(
                        f"[{session.id}] force_release_all 强制释放忙碌会话 "
                        f"(task={busy_task or '-'}, cancel_submitted={cancel_submitted})"
                    )
                    release_state = session._begin_release_state(
                        clear_page=False,
                        force=True,
                    )
                    if release_state is not None:
                        pending.append((session, release_state))

        for session, release_state in pending:
            session._run_force_release_from_state(
                release_state,
                clear_page=False,
                check_triggers=False,
            )

        with self._condition:
            for session, _release_state in pending:
                if self._tabs.get(session.id) is session and session.status == TabStatus.IDLE:
                    self._start_global_monitor_for_session(session)
            self._condition.notify_all()
            count = len(pending)
            logger.info(f"强制释放 {count} 个标签页")
            return count
    @asynccontextmanager
    async def get_tab(self, task_id: str, timeout: float = None):
        session = await self.acquire_async(task_id, timeout)
        if session is None:
            raise TimeoutError(f"获取标签页超时 (task: {task_id})")

        try:
            yield session
        except Exception as e:
            session.mark_error(str(e))
            raise
        finally:
            # 锁序考量：release 含池锁竞争与 CDP 调用（visibility 恢复各 0.5s 超时），
            # 移出事件循环线程执行。shield 保证 __aexit__ 期间再次被取消时，
            # release 仍在后台任务/线程中完成，不会泄漏标签页占用。
            await asyncio.shield(
                asyncio.to_thread(self.release, session.id, expected_task_id=task_id)
            )
    def acquire_by_raw_tab_id(
        self,
        raw_tab_id: str,
        task_id: str,
        timeout: float = None,
        count_request: bool = True,
        activate: Optional[bool] = None,
    ) -> Optional[TabSession]:
        """
        根据底层浏览器标签页 ID 获取指定标签页会话。

        这用于外部已经明确知道目标标签页时，复用 TabPool 的运行时上下文，
        保持与正常工作流执行一致的监听、占用与释放行为。
        """
        raw_tab_id = str(raw_tab_id or "").strip()
        if not raw_tab_id:
            return None

        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout

        with self._condition:
            while True:
                if self._shutdown:
                    return None
                if self._request_should_stop(task_id):
                    logger.debug(f"Raw tab acquire cancelled (task={task_id})")
                    return None

                if self._should_scan():
                    self._scan_new_tabs()

                self._check_stuck_tabs()
                self._cleanup_unhealthy_tabs()

                persistent_index = self._raw_id_to_persistent.get(raw_tab_id)
                if not persistent_index:
                    logger.warning(f"底层标签页 ID 不存在: {raw_tab_id}")
                    return None

                session_id = self._persistent_to_session_id.get(persistent_index)
                if not session_id:
                    logger.warning(f"底层标签页 {raw_tab_id} 未绑定会话")
                    return None

                session = self._tabs.get(session_id)
                if not session:
                    logger.warning(f"底层标签页 {raw_tab_id} 对应会话已移除")
                    return None

                if not session.is_healthy(allow_live_check=False):
                    logger.warning(f"[{session.id}] 标签页不健康")
                    return None

                if self._should_defer_to_command(session, task_id):
                    logger.debug(f"[{session.id}] defer raw-tab acquire to high-priority command")
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return None
                    self._condition.wait(timeout=min(remaining, 0.5))
                    continue

                if session.status == TabStatus.IDLE:
                    acquired = (
                        session.acquire(task_id)
                        if count_request
                        else session.acquire_for_command(task_id)
                    )
                    if acquired:
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_raw_tab_id",
                            task_id,
                            rollback_request_count=count_request,
                            activate=(
                                self._auto_activate_on_acquire and session.id != self._active_session_id
                                if activate is None
                                else bool(activate)
                            ),
                        ):
                            continue
                        logger.debug(
                            f"TabPool → {session.id} "
                            f"(task={task_id}, raw_tab_id={raw_tab_id}, idx=#{persistent_index}, "
                            f"count_request={count_request}, snapshot={self._describe_session(session)})"
                        )
                        return session

                remaining = deadline - time.time()
                if remaining <= 0:
                    logger.warning(
                        f"获取底层标签页 {raw_tab_id} 超时 "
                        f"(task={task_id}, 当前状态: {session.status.value}, "
                        f"snapshot={self._describe_session(session)})"
                    )
                    return None

                logger.debug_throttled(
                    f"tab_pool.wait_raw.{raw_tab_id}",
                    f"等待底层标签页 {raw_tab_id} 释放...",
                    interval_sec=5.0,
                )
                self._condition.wait(
                    timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                )
    async def acquire_by_index_async(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """异步版本的按编号获取"""
        return await self._run_cancellable_acquire(
            lambda: self.acquire_by_index(persistent_index, task_id, timeout),
            task_id,
        )
    def acquire(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """ASCII-safe fair acquire for generic request routing."""
        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout
        logged_waiting = False
        first_iteration = True

        with self._condition:
            if self._acquire_queue_full(task_id):
                return None
            waiter_token = self._next_waiter_token(task_id)
            self._acquire_waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None
                    if self._request_should_stop(task_id):
                        logger.debug(f"Acquire cancelled (task={task_id})")
                        return None

                    if first_iteration or self._should_scan():
                        self._scan_new_tabs()
                        first_iteration = False

                    self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()

                    if not self._is_waiter_turn(self._acquire_waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            busy_info = [
                                f"{s.id}({s.current_task_id})"
                                for s in self._tabs.values()
                                if s.status == TabStatus.BUSY
                            ]
                            unhealthy_count = sum(
                                1 for s in self._tabs.values()
                                if s.status == TabStatus.IDLE and not s.is_healthy(allow_live_check=False)
                            )
                            logger.warning(
                                f"Acquire tab timed out (task={task_id}, busy: {', '.join(busy_info) or 'none'}, "
                                f"unhealthy: {unhealthy_count})"
                            )
                            return None

                        if not logged_waiting:
                            busy_tabs = [s.id for s in self._tabs.values() if s.status == TabStatus.BUSY]
                            ahead = self._count_waiters_ahead(self._acquire_waiters, waiter_token)
                            if busy_tabs:
                                logger.debug(f"Waiting for tab (busy: {', '.join(busy_tabs)})")
                            elif ahead > 0:
                                logger.debug(f"Waiting in queue (ahead: {ahead})")
                            logged_waiting = True

                        self._condition.wait(
                            timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                        )
                        continue

                    dynamic_sessions = self._get_sessions_for_dynamic_routing()
                    if not dynamic_sessions and self._tabs:
                        logger.warning(
                            f"Acquire tab skipped: all open tabs are excluded from dynamic routing "
                            f"(task={task_id})"
                        )
                        return None

                    session = self._try_acquire_session_for_request(
                        dynamic_sessions,
                        task_id,
                    )
                    if session is not None:
                        self._unregister_waiter(self._acquire_waiters, waiter_token)
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            return None
                        self._mark_allocation_cursor(session)

                        if logged_waiting:
                            logger.debug(
                                f"Acquire finished -> {session.id} "
                                f"(task={task_id}, snapshot={self._describe_session(session)})"
                            )
                        else:
                            logger.debug(
                                f"TabPool -> {session.id} "
                                f"(task={task_id}, snapshot={self._describe_session(session)})"
                            )
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        busy_info = [
                            f"{s.id}({s.current_task_id})"
                            for s in self._tabs.values()
                            if s.status == TabStatus.BUSY
                        ]
                        unhealthy_count = sum(
                            1 for s in self._tabs.values()
                            if s.status == TabStatus.IDLE and not s.is_healthy(allow_live_check=False)
                        )
                        logger.warning(
                            f"Acquire tab timed out (task={task_id}, busy: {', '.join(busy_info) or 'none'}, "
                            f"unhealthy: {unhealthy_count})"
                        )
                        return None

                    if not logged_waiting:
                        busy_tabs = [s.id for s in self._tabs.values() if s.status == TabStatus.BUSY]
                        if busy_tabs:
                            logger.debug(f"Waiting for tab (busy: {', '.join(busy_tabs)})")
                        logged_waiting = True

                    self._condition.wait(
                        timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                    )
            finally:
                self._unregister_waiter(self._acquire_waiters, waiter_token)
    def acquire_by_index(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """ASCII-safe fair acquire for a fixed persistent tab index."""
        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout

        with self._condition:
            if self._acquire_queue_full(task_id):
                return None
            waiters = self._index_waiters.setdefault(persistent_index, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None
                    if self._request_should_stop(task_id):
                        logger.debug(f"Index acquire cancelled (task={task_id})")
                        return None

                    if self._should_scan():
                        self._scan_new_tabs()

                    # Keep fixed-index acquisition aligned with other wait paths so a wedged
                    # holder can be cancelled and released instead of blocking the queue.
                    released_stuck = self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()
                    if released_stuck:
                        logger.debug(
                            f"Rechecking tab #{persistent_index} after stuck-tab maintenance "
                            f"(task={task_id}, wait_state={self._describe_index_wait_state(persistent_index, waiters, waiter_token)})"
                        )

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(
                                f"Timed out waiting for tab #{persistent_index} "
                                f"(task={task_id}, wait_state={self._describe_index_wait_state(persistent_index, waiters, waiter_token)})"
                            )
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_index.{persistent_index}",
                            f"Waiting for tab #{persistent_index} release... "
                            f"({self._describe_index_wait_state(persistent_index, waiters, waiter_token)})",
                            interval_sec=5.0,
                        )
                        self._condition.wait(
                            timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                        )
                        continue

                    session_id = self._persistent_to_session_id.get(persistent_index)
                    if not session_id:
                        logger.warning(f"Persistent tab #{persistent_index} does not exist")
                        return None

                    session = self._tabs.get(session_id)
                    if not session:
                        logger.warning(f"Tab {session_id} (#{persistent_index}) was removed")
                        return None

                    if not session.is_healthy(allow_live_check=False):
                        logger.warning(
                            f"[{session.id}] tab is unhealthy "
                            f"(task={task_id}, snapshot={self._describe_session(session)})"
                        )
                        return None

                    if self._should_defer_to_command(session, task_id):
                        logger.debug(
                            f"[{session.id}] defer by index acquire to high-priority command "
                            f"(task={task_id}, snapshot={self._describe_session(session)})"
                        )
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            return None
                        self._condition.wait(timeout=min(remaining, 0.5))
                        continue

                    if session.status == TabStatus.IDLE and session.acquire(task_id):
                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._index_waiters,
                            owner_key=persistent_index,
                        )
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_index",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            return None
                        logger.debug(
                            f"TabPool -> {session.id} "
                            f"(task={task_id}, idx=#{persistent_index}, "
                            f"snapshot={self._describe_session(session)})"
                        )
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        logger.warning(
                            f"Timed out waiting for tab #{persistent_index} "
                            f"(task={task_id}, status: {session.status.value}, "
                            f"wait_state={self._describe_index_wait_state(persistent_index, waiters, waiter_token)}, "
                            f"snapshot={self._describe_session(session)})"
                        )
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_index.{persistent_index}",
                        f"Waiting for tab #{persistent_index} release... "
                        f"({self._describe_index_wait_state(persistent_index, waiters, waiter_token)})",
                        interval_sec=5.0,
                    )
                    self._condition.wait(
                        timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                    )
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._index_waiters,
                    owner_key=persistent_index,
                )
