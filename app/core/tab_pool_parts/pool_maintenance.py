"""TabPoolManager 的一部分（R2-3 拆分）：看门狗、卡死检测、空闲维护与内存清理、不健康标签页清理。

方法原样从 manager.py 迁出，行为不变；通过 mixin 组合回 TabPoolManager，
类属性与实例状态都在 TabPoolManager（manager.py）里定义。
"""

import time
from typing import Dict, List, Optional

from app.core.config import logger

from .idle_maintenance import schedule_idle_maintenance
from .recovery import TabQuarantineEntry
from .session import TabSession, TabStatus


class TabPoolMaintenanceMixin:
    """看门狗、卡死检测、空闲维护与内存清理、不健康标签页清理"""

    def _check_stuck_tabs(self):
        """检查并释放卡死的标签页"""
        now = time.time()
        released_any = False

        for session in self._tabs.values():
            if session.status == TabStatus.BUSY:
                try:
                    timeout_info = session.get_busy_timeout_info(now=now)
                except Exception:
                    timeout_info = {
                        "basis": "session",
                        "duration": now - session.last_used_at,
                        "command_loop": {},
                    }
                busy_duration = float(timeout_info.get("duration") or 0.0)
                timeout_basis = str(timeout_info.get("basis") or "session")
                command_loop = timeout_info.get("command_loop") if isinstance(timeout_info, dict) else {}

                if busy_duration > self.stuck_timeout:
                    task_id = session.current_task_id or ""
                    detail_parts = [
                        f"busy_duration={busy_duration:.0f}s",
                        f"basis={timeout_basis}",
                    ]
                    if isinstance(command_loop, dict) and command_loop.get("active"):
                        loop_iteration = command_loop.get("iteration")
                        loop_total = command_loop.get("total")
                        loop_label = str(command_loop.get("label") or "").strip()
                        loop_text = f"loop={loop_iteration or '-'}"
                        if loop_total:
                            loop_text = f"{loop_text}/{loop_total}"
                        if loop_label:
                            loop_text = f"{loop_label}:{loop_text}"
                        detail_parts.append(loop_text)
                    cancel_submitted = self._cancel_active_request_for_session(
                        session,
                        "stuck_timeout",
                        detail=", ".join(detail_parts),
                    )
                    snapshot = self._describe_session(session)
                    action_label = "record stuck session" if self.preserve_error_tabs else "retire session"
                    logger.warning(
                        f"[{session.id}] stuck for {busy_duration:.0f}s "
                        f"(basis={timeout_basis}), {action_label} "
                        f"(task={task_id or '-'}, cancel_submitted={cancel_submitted}, "
                        f"snapshot={snapshot})"
                    )
                    session.mark_error("stuck_timeout")
                    released_any = True

        if released_any:
            self._condition.notify_all()
        return released_any
    def run_watchdog_tick(self) -> bool:
        """Run periodic stuck-tab maintenance from an external watchdog thread."""
        with self._condition:
            if self._shutdown:
                return False
            changed = bool(self._check_stuck_tabs())
            self._cleanup_unhealthy_tabs()

        self._schedule_idle_memory_purge()
        self._schedule_idle_maintenance()
        return changed
    def _schedule_idle_maintenance(self) -> int:
        """P0-3 / P0-6：空闲标签页 CDP 会话回收与冻结（在 maintenance 线程池执行）。"""
        try:
            return schedule_idle_maintenance(self, getattr(self, "_idle_maintenance_config", None))
        except Exception as e:
            logger.debug(f"[TabPool] idle maintenance scheduling failed: {e}")
            return 0
    def _schedule_idle_memory_purge(self) -> bool:
        """Request best-effort JS GC for long-idle tabs without holding pool locks."""
        if not self._idle_memory_purge_enabled:
            return False

        now_wall = time.time()
        now_mono = time.monotonic()
        with self._lock:
            if self._shutdown:
                return False
            sessions = list(self._tabs.values())

        candidates: List[TabSession] = []
        for session in sessions:
            try:
                with session._lock:
                    if session.status != TabStatus.IDLE or session._termination_in_progress:
                        continue
                    idle_for = now_wall - float(session.last_used_at or now_wall)
                    last_requested = float(
                        getattr(session, "_last_memory_purge_requested_at", 0.0) or 0.0
                    )
                    if idle_for < self._idle_memory_purge_after_sec:
                        continue
                    if last_requested and (now_mono - last_requested) < self._idle_memory_purge_interval_sec:
                        continue
                    setattr(session, "_last_memory_purge_requested_at", now_mono)
                candidates.append(session)
            except Exception:
                continue

        if not candidates:
            return False

        executor = self._maintenance_executor
        if executor is None:
            return False

        scheduled = False
        for session in candidates:
            try:
                executor.submit(self._purge_idle_session_memory, session)
                scheduled = True
            except RuntimeError:
                break
        return scheduled
    def _purge_idle_session_memory(self, session: TabSession) -> None:
        """Force renderer GC for one idle session; never changes page lifecycle."""
        if self._shutdown:
            return
        heap_profiler_enabled = False
        try:
            with session._lock:
                if session.status != TabStatus.IDLE or session._termination_in_progress:
                    return

            try:
                self._run_cdp_compat(
                    session.tab,
                    "HeapProfiler.enable",
                    timeout=1.0,
                )
                heap_profiler_enabled = True
            except Exception:
                # collectGarbage is available without enabling the domain on
                # some older Chromium/DrissionPage combinations.
                pass

            self._run_cdp_compat(
                session.tab,
                "HeapProfiler.collectGarbage",
                timeout=1.0,
            )
            logger.debug_throttled(
                f"tab_pool.memory_purge.{session.id}",
                f"[{session.id}] 空闲标签页已执行 JavaScript 内存回收",
                interval_sec=self._idle_memory_purge_interval_sec,
            )
        except Exception as e:
            logger.debug_throttled(
                f"tab_pool.memory_purge_error.{session.id}",
                f"[{session.id}] 空闲标签页内存回收失败（忽略）: {e}",
                interval_sec=self._idle_memory_purge_interval_sec,
            )
        finally:
            if heap_profiler_enabled:
                try:
                    self._run_cdp_compat(
                        session.tab,
                        "HeapProfiler.disable",
                        timeout=1.0,
                    )
                except Exception as e:
                    logger.debug_throttled(
                        f"tab_pool.memory_purge_disable_error.{session.id}",
                        f"[{session.id}] HeapProfiler 关闭失败（忽略）: {e}",
                        interval_sec=self._idle_memory_purge_interval_sec,
                    )
    def _cancel_active_request_for_session(
        self,
        session: Optional[TabSession],
        reason: str,
        *,
        detail: str = "",
    ) -> bool:
        """Request the bound workflow to stop when a session becomes unusable."""
        if session is None:
            return False

        request_ids = []
        for attr_name in ("current_task_id", "_command_request_id", "_bound_request_id", "_command_loop_request_id"):
            try:
                request_id = str(getattr(session, attr_name, "") or "").strip()
            except Exception:
                request_id = ""
            if request_id and request_id not in request_ids:
                request_ids.append(request_id)
        if not request_ids:
            return False

        task_id = request_ids[0]
        cancel_key = "|".join(request_ids)
        if getattr(session, "_last_cancel_request_task_id", None) == cancel_key:
            logger.debug(
                f"[{session.id}] duplicate cancel skipped "
                f"(task={cancel_key}, reason={reason}, "
                f"previous_reason={getattr(session, '_last_cancel_request_reason', '-')}, "
                f"detail={detail or '-'})"
            )
            return False

        try:
            setattr(session, "_workflow_stop_reason", reason)
            setattr(session, "_last_cancel_request_task_id", cancel_key)
            setattr(session, "_last_cancel_request_reason", reason)
        except Exception:
            pass

        cancel_submitted = False
        for request_id in request_ids:
            cancel_submitted = bool(self._submit_request_cancel(
                request_id,
                reason,
                session_id=session.id,
                detail=detail,
            )) or cancel_submitted

        logger.warning(
            f"[{session.id}] 会话失效，已请求取消任务 "
            f"(task={task_id}, requests={','.join(request_ids)}, reason={reason}, "
            f"cancel_submitted={cancel_submitted}, detail={detail or '-'})"
        )
        return cancel_submitted
    def _cleanup_unhealthy_tabs(self):
        """清理不健康的空闲标签页、关闭状态和错误状态的标签页"""
        to_remove: Dict[str, str] = {}

        for tab_id, session in list(self._tabs.items()):
            try:
                if session.is_isolated_context and session.is_healthy(allow_live_check=False):
                    session.clear_transient_disconnect()
                if session.status != TabStatus.ERROR:
                    self._preserved_error_session_ids.discard(tab_id)

                if session.status == TabStatus.CLOSED:
                    to_remove[tab_id] = "closed"
                # 清理 ERROR 状态的标签页（包括强制释放失败的）
                elif session.status == TabStatus.ERROR:
                    if self.preserve_error_tabs:
                        if tab_id not in self._preserved_error_session_ids:
                            logger.warning(
                                f"[{tab_id}] 错误状态已记录，按配置保留标签页 "
                                "(preserve_error_tabs=True)"
                            )
                            self._preserved_error_session_ids.add(tab_id)
                        continue
                    to_remove[tab_id] = "error"
                # 清理空闲但不健康的标签页
                elif session.status == TabStatus.IDLE and not session.is_healthy(allow_live_check=False):
                    if session.is_isolated_context:
                        if session.is_in_transient_disconnect():
                            continue
                        if self._arm_isolated_rebind_grace(
                            session,
                            reason="health_probe_failed",
                            detail="health probe failed",
                        ):
                            continue
                    to_remove[tab_id] = "unhealthy"
            except Exception as e:
                logger.warning(f"[TabPool] cleanup check failed for tab {tab_id}: {e}")
                to_remove[tab_id] = "health_check_failed"

        newly_quarantined: List[TabQuarantineEntry] = []
        for tab_id, removal_reason in list(to_remove.items()):
            try:
                session = self._tabs.get(tab_id)
                if not session:
                    continue
                # mark_closed 会清空 current_task_id，先捕获用于隔离条目
                quarantine_task_id = str(
                    getattr(session, "current_task_id", "") or ""
                ).strip()
                self._cancel_active_request_for_session(
                    session,
                    removal_reason,
                    detail=(
                        f"status={getattr(getattr(session, 'status', None), 'value', 'unknown')}, "
                        f"reason={removal_reason}"
                    ),
                )
                logger.warning(
                    f"[{tab_id}] 不健康或错误状态，从池中移除但保留浏览器标签页 "
                    f"({removal_reason})"
                )
                session.mark_closed(removal_reason)
                self._detach_global_monitor_for_session(tab_id, reason=removal_reason)

                # 清理映射表，允许相同 raw_tab_id 被重新扫描
                raw_ids_to_remove = [
                    raw_id for raw_id, p_idx in list(self._raw_id_to_persistent.items())
                    if self._persistent_to_session_id.get(p_idx) == tab_id
                ]
                for raw_id in raw_ids_to_remove:
                    self._known_tab_ids.discard(raw_id)
                    self._raw_id_to_persistent.pop(raw_id, None)
                    browser_context_id = self._isolated_context_by_raw_id.pop(raw_id, None)
                    if browser_context_id:
                        self._mark_orphaned_isolated_context(browser_context_id)
                    if removal_reason == "error":
                        # ERROR 会话移出池但浏览器标签页保留：旧 worker 可能仍
                        # 阻塞在该 tab 的同步调用里。先隔离 raw id，阻止下次扫描
                        # 立即重新入池（双工作流串扰的根源）；由 TabRecoveryService
                        # 在锁外确认旧 worker 退出后决定解除或永久隔离。
                        entry = TabQuarantineEntry(
                            raw_tab_id=raw_id,
                            persistent_index=int(
                                getattr(session, "persistent_index", 0) or 0
                            ),
                            task_id=quarantine_task_id,
                            tab=getattr(session, "tab", None),
                            session_id=tab_id,
                            reason=str(
                                getattr(session, "_workflow_stop_reason", "") or ""
                            ).strip() or removal_reason,
                            url=str(getattr(session, "last_known_url", "") or ""),
                        )
                        self._quarantined_raw_ids[raw_id] = entry
                        newly_quarantined.append(entry)
                        logger.info(
                            f"[TabRecovery] 已隔离错误标签页 "
                            f"(raw={raw_id}, tab={tab_id}, "
                            f"task={quarantine_task_id or '-'}, "
                            f"reason={entry.reason})"
                        )

                # 清理持久编号映射
                p_idx = session.persistent_index
                if p_idx and self._persistent_to_session_id.get(p_idx) == tab_id:
                    self._persistent_to_session_id.pop(p_idx, None)

                # 清理活动标签页记录
                if self._active_session_id == tab_id:
                    self._active_session_id = None

                self._tabs.pop(tab_id, None)
                self._preserved_error_session_ids.discard(tab_id)
                self._on_session_removed(tab_id)
            except Exception as e:
                logger.warning(f"[TabPool] cleanup remove failed for tab {tab_id}: {e}")

        # 投递到 maintenance executor 由恢复服务处理（submit 仅入队，
        # 持池锁调用不会阻塞，也不触发任何 CDP/网络操作）。
        for entry in newly_quarantined:
            self._submit_recovery(entry)

        if to_remove:
            self._condition.notify_all()
    def _should_defer_to_command(self, session: TabSession, task_id: str) -> bool:
        """Whether request acquisition should defer to high-priority pending/running commands."""
        task = str(task_id or "").strip().lower()
        if task.startswith("cmd_") or task.startswith("group_"):
            return False
        try:
            from app.services.command_engine import command_engine
            if hasattr(command_engine, "should_block_request_for_session"):
                return bool(command_engine.should_block_request_for_session(session, task_id=task_id))
        except Exception:
            return False
        return False
