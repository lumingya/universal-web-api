"""TabPoolManager 的一部分（R2-3 拆分）：状态查询与管理操作（编号列表、预设/模型名设置、状态与看门狗摘要、按编号终止）。

方法原样从 manager.py 迁出，行为不变；通过 mixin 组合回 TabPoolManager，
类属性与实例状态都在 TabPoolManager（manager.py）里定义。
"""

import time
from typing import Any, Dict, List, Optional

from app.core.config import logger
from app.utils.tab_route_groups import normalize_route_groups

from .session import TabSession, TabStatus


class TabPoolStatusMixin:
    """状态查询与管理操作（编号列表、预设/模型名设置、状态与看门狗摘要、按编号终止）"""

    def terminate_by_index(
        self,
        persistent_index: int,
        reason: str = "manual_terminate",
        clear_page: bool = True,
        scope: str = "task",
        expected_session_id: str = "",
        expected_task_id: str = "",
    ) -> Dict[str, Any]:
        """
        按标签页编号终止当前任务，并在旧所有者退出前隔离该成员。

        行为：
        1) 校验调用方看到的 session/task 所有权快照；
        2) 设置终止隔离闸门，阻止组/域名/编号路由重新分配；
        3) 取消该成员关联请求并中断页面活动；
        4) 等待旧工作线程完成正常释放，超时则继续保持隔离。
        """
        normalized_scope = str(scope or "task").strip().lower()
        if normalized_scope not in {"task", "loop"}:
            normalized_scope = "task"
        expected_session = str(expected_session_id or "").strip()
        expected_task = str(expected_task_id or "").strip()

        with self._condition:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}

            session = self._tabs.get(session_id)
            if not session:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}
            if expected_session and session.id != expected_session:
                return {
                    "ok": False,
                    "error": "session_ownership_changed",
                    "tab_index": persistent_index,
                    "expected_session_id": expected_session,
                    "current_session_id": session.id,
                }

            current_task = str(session.current_task_id or "").strip()
            if expected_task and current_task != expected_task:
                return {
                    "ok": False,
                    "error": "task_ownership_changed",
                    "tab_index": persistent_index,
                    "tab_id": session.id,
                    "expected_task_id": expected_task,
                    "current_task_id": current_task,
                }

            if normalized_scope == "loop":
                loop_cancelled = session.request_command_loop_cancel(reason)
                self._condition.notify_all()
                if not loop_cancelled:
                    return {
                        "ok": False,
                        "error": "no_active_command_loop",
                        "tab_index": persistent_index,
                        "tab_id": session.id,
                        "status": session.status.value,
                        "reason": reason,
                        "scope": normalized_scope,
                    }
                logger.warning(
                    f"[{session.id}] 手动终止当前循环: idx=#{persistent_index}, "
                    f"status={session.status.value}, reason={reason}, "
                    f"loop={session.get_command_loop_info()}"
                )
                return {
                    "ok": True,
                    "tab_index": persistent_index,
                    "tab_id": session.id,
                    "was_busy": session.status == TabStatus.BUSY,
                    "task_id": str(session.current_task_id or "").strip(),
                    "cancelled": True,
                    "status": session.status.value,
                    "reason": reason,
                    "scope": normalized_scope,
                    "loop_cancelled": True,
                    "command_loop": session.get_command_loop_info(),
                }

            before_snapshot = self._describe_session(session)
            was_busy = session.status == TabStatus.BUSY
            termination_state = session.begin_termination(expected_task)
            if not termination_state.get("ok"):
                # A second terminate request can race with the first one while
                # the old owner is still unwinding.  That is an idempotent
                # in-progress result, not a failed termination.
                if termination_state.get("error") == "termination_in_progress":
                    return {
                        "ok": True,
                        "tab_index": persistent_index,
                        "tab_id": session.id,
                        "was_busy": was_busy,
                        "task_id": str(
                            termination_state.get("current_task_id") or current_task
                        ).strip(),
                        "cancelled": False,
                        "status": session.status.value,
                        "pending": True,
                        "released": False,
                        "interrupt_success": False,
                        "already_in_progress": True,
                        "reason": reason,
                        "scope": normalized_scope,
                    }
                return {
                    "ok": False,
                    **termination_state,
                    "tab_index": persistent_index,
                    "tab_id": session.id,
                    "status": session.status.value,
                    "reason": reason,
                }
            task_id = str(termination_state.get("task_id") or "").strip()
            command_request_id = str(
                termination_state.get("command_request_id") or ""
            ).strip()
            command_loop_request_id = str(
                termination_state.get("command_loop_request_id") or ""
            ).strip()

        cancelled = False
        cancel_error = ""

        request_ids_to_cancel = []
        for request_id in (task_id, command_request_id, command_loop_request_id):
            if request_id and request_id not in request_ids_to_cancel:
                request_ids_to_cancel.append(request_id)

        if request_ids_to_cancel:
            try:
                from app.services.request_manager import request_manager
                for request_id in request_ids_to_cancel:
                    cancelled = bool(request_manager.cancel_request(request_id, reason)) or cancelled
            except Exception as e:
                cancel_error = str(e)
                logger.debug(f"[{session.id}] 取消任务失败（忽略）: {e}")

        self._stop_global_monitor_for_session(session.id, reason=f"terminate:{reason}", wait=True)
        interrupt_success = session.interrupt_for_termination(clear_page=clear_page)

        with self._condition:
            wait_deadline = time.monotonic() + self.TERMINATION_RELEASE_WAIT_SEC
            while (
                self._tabs.get(session.id) is session
                and session.is_termination_in_progress()
            ):
                remaining = wait_deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=min(remaining, 0.1))

            pending = bool(
                self._tabs.get(session.id) is session
                and session.is_termination_in_progress()
            )
            if not pending and self._tabs.get(session.id) is session and session.status == TabStatus.IDLE:
                self._start_global_monitor_for_session(session)

            self._condition.notify_all()

            logger.warning(
                f"[{session.id}] 手动终止: idx=#{persistent_index}, "
                f"task={task_id or '-'}, cancelled={cancelled}, "
                f"pending={pending}, interrupt_success={interrupt_success}, "
                f"status={session.status.value}, reason={reason}, "
                f"before={before_snapshot}, after={self._describe_session(session)}"
            )

            result = {
                "ok": True,
                "tab_index": persistent_index,
                "tab_id": session.id,
                "was_busy": was_busy,
                "task_id": task_id,
                "cancelled": cancelled,
                "status": session.status.value,
                "pending": pending,
                "released": not pending and session.status == TabStatus.IDLE,
                "interrupt_success": interrupt_success,
                "reason": reason,
                "scope": normalized_scope,
            }
            if cancel_error:
                result["cancel_error"] = cancel_error
            return result
    def get_tabs_with_index(self) -> List[Dict]:
        """获取所有标签页及其持久编号（供 API 调用）"""
        with self._lock:
            if self._should_scan_for_query():
                self._scan_new_tabs()

            sessions = list(self._tabs.values())
            groups_by_session: Dict[str, List[str]] = {}
            for group in normalize_route_groups(getattr(self, "route_groups", [])):
                for member_session in self._get_sessions_for_route_group(group["id"], bind=False):
                    groups_by_session.setdefault(member_session.id, []).append(group["id"])

        result = []
        for session in sessions:
            info = session.get_info(use_cached_url=True)
            tab_route_prefix = f"/tab/{session.persistent_index}"
            route_domain = str(info.get("route_domain") or "").strip()
            domain_route_prefix = f"/url/{route_domain}" if route_domain else ""
            preset_route_domain = str(info.get("current_domain") or route_domain).strip()
            url_route_token = str(info.get("url_route_token") or "").strip()
            exact_url_route_prefix = f"/tab-url/{url_route_token}" if url_route_token else ""
            info["tab_route_prefix"] = tab_route_prefix
            info["domain_route_prefix"] = domain_route_prefix
            info["preset_route_domain"] = preset_route_domain
            info["preset_domain_route_prefix"] = f"/url/{preset_route_domain}" if preset_route_domain else ""
            info["exact_url_route_prefix"] = exact_url_route_prefix
            info["route_prefix"] = domain_route_prefix or tab_route_prefix
            info["route_groups"] = groups_by_session.get(session.id, [])
            self._apply_preset_overrides(session, info)
            self._apply_exposed_model_name(info)
            result.append(info)

        # 按编号排序
        result.sort(key=lambda x: x.get("persistent_index", 0))
        return result
    def set_tab_preset(self, persistent_index: int, preset_name: str) -> bool:
        """
        为指定标签页设置预设

        Args:
            persistent_index: 标签页持久化编号
            preset_name: 预设名称（None 或空字符串表示恢复为跟随站点默认预设）

        Returns:
            是否成功
        """
        with self._lock:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                logger.warning(f"标签页 #{persistent_index} 不存在")
                return False

            session = self._tabs.get(session_id)
            if not session:
                logger.warning(f"标签页 {session_id} 已被移除")
                return False

            old_preset = session.preset_name
            session.set_preset(preset_name, source="tab" if preset_name else None)

            logger.debug(
                f"[{session.id}] 预设切换: "
                f"'{old_preset or '跟随站点默认预设'}' → '{preset_name or '跟随站点默认预设'}'"
            )
            return True
    def get_tab_preset(self, persistent_index: int) -> Optional[str]:
        """获取指定标签页的当前预设名称"""
        with self._lock:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                return None

            session = self._tabs.get(session_id)
            if not session:
                return None

            return session.preset_name
    def set_tab_model_name(self, persistent_index: int, model_name: Optional[str]) -> Dict[str, Any]:
        """为指定标签页设置临时暴露模型名。空值表示恢复到持久化/默认规则。"""
        normalized_name = self._normalize_model_name(model_name)
        with self._lock:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}

            session = self._tabs.get(session_id)
            if not session:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}

            session.model_name_override = normalized_name or None
            info = session.get_info(use_cached_url=True)
            tab_route_prefix = f"/tab/{session.persistent_index}"
            route_domain = str(info.get("route_domain") or "").strip()
            domain_route_prefix = f"/url/{route_domain}" if route_domain else ""
            preset_route_domain = str(info.get("current_domain") or route_domain).strip()
            url_route_token = str(info.get("url_route_token") or "").strip()
            info["tab_route_prefix"] = tab_route_prefix
            info["domain_route_prefix"] = domain_route_prefix
            info["preset_route_domain"] = preset_route_domain
            info["preset_domain_route_prefix"] = f"/url/{preset_route_domain}" if preset_route_domain else ""
            info["exact_url_route_prefix"] = f"/tab-url/{url_route_token}" if url_route_token else ""
            info["route_prefix"] = domain_route_prefix or tab_route_prefix
            self._apply_exposed_model_name(info)

            return {
                "ok": True,
                "tab_index": persistent_index,
                "tab": info,
            }
    def get_status(self) -> Dict:
        with self._lock:
            sessions = list(self._tabs.values())
            total = len(sessions)
            idle = sum(1 for s in sessions if s.status == TabStatus.IDLE)
            busy = sum(1 for s in sessions if s.status == TabStatus.BUSY)
            max_tabs = self.max_tabs
            min_tabs = self.min_tabs
            idle_timeout = self.idle_timeout
            acquire_timeout = self.acquire_timeout
            stuck_timeout = self.stuck_timeout
            allocation_mode = self.allocation_mode
            excluded_urls = list(self.excluded_urls)
            preserve_error_tabs = self.preserve_error_tabs
            route_groups = normalize_route_groups(getattr(self, "route_groups", []))
            global_network_enabled = self._global_network_enabled
            known_raw_tabs = len(self._known_tab_ids)
            last_scan = round(time.time() - self._last_scan_time, 1)

        tabs_info = [s.get_info(use_cached_url=True) for s in sessions]

        return {
            "total": total,
            "idle": idle,
            "busy": busy,
            "max_tabs": max_tabs,
            "min_tabs": min_tabs,
            "idle_timeout": idle_timeout,
            "acquire_timeout": acquire_timeout,
            "stuck_timeout": stuck_timeout,
            "allocation_mode": allocation_mode,
            "excluded_urls": excluded_urls,
            "preserve_error_tabs": preserve_error_tabs,
            "route_groups": route_groups,
            "global_network_enabled": global_network_enabled,
            "known_raw_tabs": known_raw_tabs,
            "last_scan": last_scan,
            "tabs": tabs_info
        }
    def get_watchdog_summary(self, limit: int = 4) -> Dict[str, Any]:
        """Return the lightweight tab pool fields needed by BrowserWatchdog logs."""
        try:
            max_items = max(0, int(limit))
        except Exception:
            max_items = 4

        with self._lock:
            sessions = list(self._tabs.values())
            total = len(sessions)
            idle = sum(1 for s in sessions if s.status == TabStatus.IDLE)
            busy = sum(1 for s in sessions if s.status == TabStatus.BUSY)
            preview = sessions[:max_items]

        tabs = []
        for session in preview:
            status = getattr(getattr(session, "status", None), "value", None)
            tabs.append({
                "id": getattr(session, "id", None),
                "status": status or str(getattr(session, "status", "") or "?"),
                "is_isolated_context": bool(getattr(session, "is_isolated_context", False)),
            })

        return {
            "total": total,
            "idle": idle,
            "busy": busy,
            "tabs": tabs,
        }
    def get_idle_sessions_snapshot(self) -> List[TabSession]:
        """Return a shallow snapshot of currently idle tab sessions."""
        with self._lock:
            return [s for s in self._tabs.values() if s.status == TabStatus.IDLE]
    def get_sessions_snapshot(self) -> List[TabSession]:
        """Return a shallow snapshot of all current tab sessions."""
        with self._lock:
            return list(self._tabs.values())
    def get_session_by_tab(self, tab: Any) -> Optional[TabSession]:
        """Look up a TabSession for a given tab instance."""
        if tab is None:
            return None
        direct = getattr(tab, "_session", None)
        if direct is not None and isinstance(direct, TabSession):
            return direct
        with self._lock:
            for session in self._tabs.values():
                if getattr(session, "tab", None) is tab:
                    return session
        return None
