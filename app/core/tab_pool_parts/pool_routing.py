"""TabPoolManager 的一部分（R2-3 拆分）：按域名、路由组、精确 URL 借用标签页。

方法原样从 manager.py 迁出，行为不变；通过 mixin 组合回 TabPoolManager，
类属性与实例状态都在 TabPoolManager（manager.py）里定义。
"""

import time
from collections import deque
from typing import Any, Dict, List, Optional

from app.core.config import logger
from app.utils.site_url import (
    encode_tab_url_route_token,
    normalize_exact_tab_url,
    normalize_route_domain,
    route_domain_matches,
    tab_url_matches,
)
from app.utils.tab_route_groups import (
    normalize_route_group_id,
    normalize_route_groups,
    route_group_member_key,
    route_groups_by_id,
)

from .session import TabSession, TabStatus


class TabPoolRoutingMixin:
    """按域名、路由组、精确 URL 借用标签页"""

    def _get_sessions_for_route_domain(self, route_domain: str) -> List[TabSession]:
        target = normalize_route_domain(route_domain)
        if not target:
            return []

        matches: List[TabSession] = []
        for session in self._tabs.values():
            current_url, actual_domain = session.get_cached_route_snapshot()
            if route_domain_matches(target, actual_domain):
                if self._is_session_excluded_from_dynamic_routing(session, current_url):
                    continue
                matches.append(session)

        return matches
    def _get_route_group_config(self, group_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = normalize_route_group_id(group_id)
        if not normalized_id:
            return None
        return route_groups_by_id(getattr(self, "route_groups", [])).get(normalized_id)
    @staticmethod
    def _session_matches_route_group_member(
        session: TabSession,
        member: Dict[str, Any],
    ) -> bool:
        current_url, _actual_domain = session.get_cached_route_snapshot()
        member_url = str(member.get("url") or "").strip()
        member_token = str(member.get("url_token") or "").strip().lower()
        if member_url and tab_url_matches(member_url, current_url):
            return True
        if member_token and encode_tab_url_route_token(current_url) == member_token:
            return True
        return False
    def _get_sessions_for_route_group(
        self,
        group_id: str,
        *,
        bind: bool = True,
    ) -> List[TabSession]:
        """解析路由组当前的成员标签页。

        bind=False 供只读查询（/api/tabs 面板轮询、组快照）使用：只做匹配，
        不写 _route_group_bindings。否则一次与请求无关的面板轮询就会替真实请求
        提前把成员钉死在某个标签页上（绑定是粘滞的），后续请求再也轮不到同 URL
        的其他标签页。
        """
        group = self._get_route_group_config(group_id)
        if not group:
            return []

        normalized_id = group["id"]
        members = group.get("members") or []
        valid_member_keys = {route_group_member_key(member) for member in members}
        if bind:
            bindings = self._route_group_bindings.setdefault(normalized_id, {})
            for member_key in list(bindings):
                session_id = bindings.get(member_key)
                if member_key not in valid_member_keys or session_id not in self._tabs:
                    bindings.pop(member_key, None)
        else:
            bindings = {
                member_key: session_id
                for member_key, session_id in self._route_group_bindings.get(normalized_id, {}).items()
                if member_key in valid_member_keys and session_id in self._tabs
            }

        sessions: List[TabSession] = []
        used_session_ids = set()
        ordered_sessions = sorted(self._tabs.values(), key=self._session_allocation_key)
        for member in members:
            member_key = route_group_member_key(member)
            bound_session = self._tabs.get(bindings.get(member_key, ""))
            if bound_session is not None and bound_session.id not in used_session_ids:
                sessions.append(bound_session)
                used_session_ids.add(bound_session.id)
                continue

            matching = [
                session
                for session in ordered_sessions
                if session.id not in used_session_ids
                and self._session_matches_route_group_member(session, member)
            ]
            try:
                preferred_index = int(member.get("tab_index") or 0)
            except (TypeError, ValueError):
                preferred_index = 0
            if preferred_index > 0:
                matching.sort(
                    key=lambda session: (
                        int(session.persistent_index or 0) != preferred_index,
                        self._session_allocation_key(session),
                    )
                )
            if not matching:
                bindings.pop(member_key, None)
                continue

            selected = matching[0]
            bindings[member_key] = selected.id
            sessions.append(selected)
            used_session_ids.add(selected.id)

        return sessions
    def get_route_groups_snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            groups = normalize_route_groups(getattr(self, "route_groups", []))
            result = []
            for group in groups:
                sessions = self._get_sessions_for_route_group(group["id"], bind=False)
                item = dict(group)
                item["live_member_count"] = len(sessions)
                item["idle_member_count"] = sum(
                    1 for session in sessions if session.status == TabStatus.IDLE
                )
                item["busy_member_count"] = sum(
                    1 for session in sessions if session.status == TabStatus.BUSY
                )
                item["live_tab_indices"] = [
                    int(session.persistent_index or 0) for session in sessions
                ]
                result.append(item)
            return result
    def _refresh_route_snapshots_for_sessions(self, sessions: List[TabSession]) -> None:
        """Refresh live tab URL/domain snapshots; callers must not hold self._condition."""
        for session in sessions:
            try:
                # BUSY 标签页跳过实时读取：
                # 1) 该 tab 正被别的工作流线程用同步 DrissionPage 调用驱动，
                #    这里再发一次 CDP 是并发访问同一 target（_scan_new_tabs 同样避开 BUSY）；
                # 2) 工作流中途导航时 tab.url 会瞬时变成 about:blank，
                #    _refresh_current_domain 会据此把 current_domain 清空，
                #    使并发的 /url/<domain> 请求匹配不到这个标签页而误报 404。
                # BUSY 标签页的 URL 已由 _scan_new_tabs 从 Target.getTargets 快照刷新。
                if session.status == TabStatus.BUSY:
                    continue
                current_url = session._safe_get_url(allow_live_when_busy=True)
                if current_url:
                    session._refresh_current_domain(current_url)
            except Exception as e:
                logger.debug(f"[{session.id}] failed to refresh route snapshot: {e}")
    def _refresh_route_snapshots_unlocked_once(self) -> None:
        """Refresh route snapshots once without holding the pool condition."""
        sessions_to_refresh = list(self._tabs.values())
        self._condition.release()
        try:
            self._refresh_route_snapshots_for_sessions(sessions_to_refresh)
        finally:
            self._condition.acquire()
    async def acquire_by_route_domain_async(
        self,
        route_domain: str,
        task_id: str,
        timeout: float = None,
        allocation_mode: Optional[str] = None,
    ) -> Optional[TabSession]:
        """异步版本的按域名路由获取。"""
        return await self._run_cancellable_acquire(
            lambda: self.acquire_by_route_domain(
                route_domain,
                task_id,
                timeout,
                allocation_mode,
            ),
            task_id,
        )
    async def acquire_by_route_group_async(
        self,
        group_id: str,
        task_id: str,
        timeout: float = None,
        allocation_mode: Optional[str] = None,
        requested_model: Optional[str] = None,
    ) -> Optional[TabSession]:
        return await self._run_cancellable_acquire(
            lambda: self.acquire_by_route_group(
                group_id,
                task_id,
                timeout,
                allocation_mode,
                requested_model=requested_model,
            ),
            task_id,
        )
    def _get_sessions_for_exact_url(self, exact_url: str) -> List[TabSession]:
        target = str(exact_url or "").strip()
        if not target:
            return []

        matches: List[TabSession] = []
        for session in self._tabs.values():
            current_url, _actual_domain = session.get_cached_route_snapshot()
            if tab_url_matches(target, current_url):
                matches.append(session)

        return matches
    def acquire_by_exact_url(self, exact_url: str, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """Acquire a tab by strict full-URL match, round-robin within identical URLs."""
        raw_target = str(exact_url or "").strip()
        if not raw_target:
            logger.warning("Exact tab URL is empty; cannot acquire a tab")
            return None

        # 队列 key / 轮询游标 key 必须用归一化后的 URL：匹配侧 tab_url_matches 会剥掉
        # 默认端口等差异，若这里仍用原始串，"https://x/a" 与 "https://x:443/a"
        # 会落进两条互不相干的等待队列，各自都是队首，FIFO 互斥形同虚设。
        target = normalize_exact_tab_url(raw_target) or raw_target
        waiter_key = f"url::{target}"

        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout

        with self._condition:
            if self._acquire_queue_full(task_id):
                return None
            waiters = self._route_waiters.setdefault(waiter_key, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None
                    if self._request_should_stop(task_id):
                        logger.debug(f"Exact URL acquire cancelled (task={task_id})")
                        return None

                    if self._should_scan():
                        self._scan_new_tabs()

                    self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(
                                f"Timed out waiting for exact URL '{target}' (task={task_id})"
                            )
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_exact_url.{target}",
                            f"Waiting for exact URL '{target}' release...",
                            interval_sec=5.0,
                        )
                        self._condition.wait(
                            timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                        )
                        continue

                    self._refresh_route_snapshots_unlocked_once()
                    matching_sessions = self._get_sessions_for_exact_url(target)
                    if not matching_sessions:
                        logger.warning(f"No tab matches exact URL '{target}'")
                        return None
                    session = self._try_acquire_session_for_request(
                        matching_sessions,
                        task_id,
                        route_domain=waiter_key,
                        defer_context="exact-url acquire",
                    )
                    if session is not None:
                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._route_waiters,
                            owner_key=waiter_key,
                        )
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_exact_url",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            return None
                        self._mark_allocation_cursor(session, route_domain=waiter_key)

                        logger.debug(
                            f"TabPool -> {session.id} "
                            f"(task={task_id}, exact_url={target}, "
                            f"idx=#{session.persistent_index}, snapshot={self._describe_session(session)})"
                        )
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        logger.warning(
                            f"Timed out waiting for exact URL '{target}' "
                            f"(task={task_id}, matching tabs: "
                            f"{', '.join(f'{s.id}(#{s.persistent_index}:{s.status.value})' for s in matching_sessions) or 'none'})"
                        )
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_exact_url.{target}",
                        f"Waiting for exact URL '{target}' release...",
                        interval_sec=5.0,
                    )
                    self._condition.wait(
                        timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                    )
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._route_waiters,
                    owner_key=waiter_key,
                )
    def acquire_by_route_domain(
        self,
        route_domain: str,
        task_id: str,
        timeout: float = None,
        allocation_mode: Optional[str] = None,
    ) -> Optional[TabSession]:
        """ASCII-safe fair acquire for tabs matching the same route domain."""
        target = normalize_route_domain(route_domain)
        if not target:
            logger.warning("Route domain is empty; cannot acquire a tab")
            return None

        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout

        with self._condition:
            if self._acquire_queue_full(task_id):
                return None
            waiters = self._route_waiters.setdefault(target, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None
                    if self._request_should_stop(task_id):
                        logger.debug(f"Route domain acquire cancelled (task={task_id})")
                        return None

                    if self._should_scan():
                        self._scan_new_tabs()

                    self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(
                                f"Timed out waiting for route domain '{target}' (task={task_id})"
                            )
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_route.{target}",
                            f"Waiting for route domain '{target}' release...",
                            interval_sec=5.0,
                        )
                        self._condition.wait(
                            timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                        )
                        continue

                    self._refresh_route_snapshots_unlocked_once()
                    matching_sessions = self._get_sessions_for_route_domain(target)
                    if not matching_sessions:
                        logger.warning(f"No tab matches route domain '{target}'")
                        return None

                    session = self._try_acquire_session_for_request(
                        matching_sessions,
                        task_id,
                        route_domain=target,
                        allocation_mode=allocation_mode,
                        defer_context="route-domain acquire",
                    )
                    if session is not None:
                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._route_waiters,
                            owner_key=target,
                        )
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_route_domain",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            return None
                        self._mark_allocation_cursor(session, route_domain=target)

                        logger.debug(
                            f"TabPool -> {session.id} "
                            f"(task={task_id}, route_domain={target}, "
                            f"idx=#{session.persistent_index}, snapshot={self._describe_session(session)})"
                        )
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        busy_info = [
                            f"{session.id}(#{session.persistent_index}:{session.status.value})"
                            for session in matching_sessions
                        ]
                        logger.warning(
                            f"Timed out waiting for route domain '{target}' "
                            f"(task={task_id}, matching tabs: {', '.join(busy_info) or 'none'})"
                        )
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_route.{target}",
                        f"Waiting for route domain '{target}' release...",
                        interval_sec=5.0,
                    )
                    self._condition.wait(
                        timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                    )
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._route_waiters,
                    owner_key=target,
                )
    def acquire_by_route_group(
        self,
        group_id: str,
        task_id: str,
        timeout: float = None,
        allocation_mode: Optional[str] = None,
        requested_model: Optional[str] = None,
    ) -> Optional[TabSession]:
        target = normalize_route_group_id(group_id)
        if not target:
            logger.warning("Route group ID is invalid")
            return None

        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout
        first_iteration = True

        with self._condition:
            group = self._get_route_group_config(target)
            if not group:
                logger.warning(f"Route group '{target}' does not exist")
                return None

            if self._acquire_queue_full(task_id):
                return None
            waiters = self._group_waiters.setdefault(target, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None
                    if self._request_should_stop(task_id):
                        logger.debug(f"Route group acquire cancelled (task={task_id})")
                        return None

                    # 组配置每轮重新读取：apply_runtime_config 可能在等待期间
                    # 删除/改名该组或改掉分配模式。用循环外的旧快照会让等待者既不
                    # 报"组不存在"也匹配不到成员，只能空转到超时。
                    group = self._get_route_group_config(target)
                    if not group:
                        logger.warning(
                            f"Route group '{target}' no longer exists (task={task_id})"
                        )
                        return None

                    if first_iteration or self._should_scan():
                        self._scan_new_tabs()
                        first_iteration = False

                    self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(
                                f"Timed out waiting in route group '{target}' (task={task_id})"
                            )
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_group.{target}",
                            f"Waiting in route group '{target}' queue...",
                            interval_sec=5.0,
                        )
                        self._condition.wait(
                            timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                        )
                        continue

                    matching_sessions = self._get_sessions_for_route_group(target)
                    # 空组没有任何可能在后续释放的成员。立即返回，让上游的
                    # 多 URL 轮询跳到下一个 URL，而不是按默认 60 秒超时等待。
                    if not matching_sessions:
                        logger.info(
                            f"Route group '{target}' has no live members; skip immediately "
                            f"(task={task_id})"
                        )
                        return None

                    if requested_model:
                        clean_req_model = str(requested_model).strip().lower()
                        model_specific = [
                            s for s in matching_sessions
                            if self._get_session_override_model_name(s).strip().lower() == clean_req_model
                        ]
                        if model_specific:
                            matching_sessions = model_specific

                    mode = allocation_mode or group.get("allocation_mode") or self.allocation_mode
                    session = self._try_acquire_session_for_request(
                        matching_sessions,
                        task_id,
                        route_domain=f"group::{target}",
                        allocation_mode=mode,
                        defer_context=f"route-group {target} acquire",
                    )
                    if session is not None:
                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._group_waiters,
                            owner_key=target,
                        )
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_route_group",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            # 与 acquire/acquire_by_route_domain 等路径保持一致：
                            # handoff 失败直接返回 None。此处 waiter 已出队，若 continue
                            # 会因 _is_waiter_turn 永远轮不到而空转到超时。
                            # 调用方（workflow.py）已有 session is None 的处理分支。
                            return None
                        self._mark_allocation_cursor(session, route_domain=f"group::{target}")
                        logger.debug(
                            f"TabPool -> {session.id} "
                            f"(task={task_id}, route_group={target}, "
                            f"idx=#{session.persistent_index}, snapshot={self._describe_session(session)})"
                        )
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        member_state = ", ".join(
                            f"{session.id}(#{session.persistent_index}:{session.status.value})"
                            for session in matching_sessions
                        ) or "no live members"
                        logger.warning(
                            f"Timed out waiting for route group '{target}' "
                            f"(task={task_id}, members: {member_state})"
                        )
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_group.{target}",
                        f"Waiting for route group '{target}' member release...",
                        interval_sec=5.0,
                    )
                    self._condition.wait(
                        timeout=min(remaining, self.ACQUIRE_CANCEL_POLL_SEC)
                    )
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._group_waiters,
                    owner_key=target,
                )
