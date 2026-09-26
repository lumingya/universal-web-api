"""TabPoolManager 的一部分（R2-3 拆分）：标签页的创建、扫描发现、初始化与刷新。

方法原样从 manager.py 迁出，行为不变；通过 mixin 组合回 TabPoolManager，
类属性与实例状态都在 TabPoolManager（manager.py）里定义。
"""

import time
from typing import Any, Dict, Optional

from app.core.config import logger
from app.utils.site_url import extract_remote_site_domain, route_domain_matches

from ._utils import _should_skip_pool_url
from .session import TabStatus


class TabPoolTabsMixin:
    """标签页的创建、扫描发现、初始化与刷新"""

    def _build_site_entry_url(self, domain: str) -> str:
        normalized_domain = str(domain or "").strip()
        if not normalized_domain:
            return ""

        for session in self._tabs.values():
            current_url, actual_domain = session.get_cached_route_snapshot()
            if route_domain_matches(normalized_domain, actual_domain):
                if current_url and not _should_skip_pool_url(current_url):
                    return current_url
                built = session._build_domain_url(current_url, actual_domain)
                if built:
                    return built

        return f"https://{normalized_domain}/"
    def _create_isolated_tab(self, url: str, background: bool = False) -> Optional[Dict[str, Any]]:
        try:
            browser = self._get_browser_handle()
            tab = browser.new_tab(background=background, new_context=True, new_window=True)
            raw_tab_id = getattr(tab, "tab_id", None)
            browser_context_id = self._get_browser_context_id(raw_tab_id)
            self._register_isolated_context(raw_tab_id, browser_context_id)
            if url:
                tab.get(url)
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "url": str(getattr(tab, "url", "") or url or ""),
            }
        except Exception as e:
            logger.warning(f"[TabPool] create isolated tab failed: {e}")
            return None
    def _create_shared_tab(
        self,
        url: str,
        *,
        background: bool = False,
        new_window: bool = True,
    ) -> Optional[Dict[str, Any]]:
        try:
            browser = self._get_browser_handle()
            tab = browser.new_tab(url=url or None, background=background, new_window=new_window)
            raw_tab_id = getattr(tab, "tab_id", None)
            browser_context_id = self._get_browser_context_id(raw_tab_id)
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "url": str(getattr(tab, "url", "") or url or ""),
            }
        except Exception as e:
            logger.warning(f"[TabPool] create shared tab failed: {e}")
            return None
    def _ensure_cookie_isolation_for_new_tab(self, tab: Any, raw_tab_id: str, url: str) -> Dict[str, Any]:
        final_url = str(url or "").strip()
        browser_context_id = self._get_browser_context_id(raw_tab_id)
        if raw_tab_id in self._isolated_context_by_raw_id:
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "is_isolated_context": True,
                "url": final_url,
            }

        domain = extract_remote_site_domain(final_url) or ""
        if not self._is_site_independent_cookie_enabled(domain):
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "is_isolated_context": False,
                "url": final_url,
            }

        if not self._is_site_independent_cookie_auto_takeover_enabled(domain):
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "is_isolated_context": False,
                "url": final_url,
            }

        logger.warning(
            f"[TabPool] skipped automatic isolated-cookie takeover because existing tabs are never closed: "
            f"{domain or final_url}"
        )
        return {
            "tab": tab,
            "raw_tab_id": raw_tab_id,
            "browser_context_id": browser_context_id,
            "is_isolated_context": False,
            "url": final_url,
        }
    def create_isolated_site_tab(self, domain: str) -> Dict[str, Any]:
        target_domain = str(domain or "").strip()
        if not target_domain:
            return {"ok": False, "error": "domain_required"}

        with self._condition:
            self._scan_new_tabs()
            if len(self._tabs) >= self.max_tabs:
                return {"ok": False, "error": "tab_pool_full"}

            target_url = self._build_site_entry_url(target_domain)

        created = self._create_isolated_tab(target_url, background=False)
        if not created:
            return {"ok": False, "error": "create_isolated_tab_failed"}

        with self._condition:
            if len(self._tabs) >= self.max_tabs:
                logger.warning(
                    "[TabPool] pool reached capacity while creating an isolated tab; "
                    "keeping the newly created tab managed"
                )

            session = self._wrap_tab(
                created["tab"],
                created["raw_tab_id"],
                browser_context_id=created.get("browser_context_id"),
                is_isolated_context=True,
                initial_url=created.get("url") or target_url,
            )
            self._tabs[session.id] = session
            self._start_global_monitor_for_session(session)
            self._last_scan_time = time.time()
            self._condition.notify_all()

            info = session.get_info(use_cached_url=True)
            info["tab_route_prefix"] = f"/tab/{session.persistent_index}"
            route_domain = str(info.get("route_domain") or "").strip()
            info["domain_route_prefix"] = f"/url/{route_domain}" if route_domain else ""
            preset_route_domain = str(info.get("current_domain") or route_domain).strip()
            info["preset_route_domain"] = preset_route_domain
            info["preset_domain_route_prefix"] = f"/url/{preset_route_domain}" if preset_route_domain else ""
            url_route_token = str(info.get("url_route_token") or "").strip()
            info["exact_url_route_prefix"] = f"/tab-url/{url_route_token}" if url_route_token else ""
            info["route_prefix"] = info["domain_route_prefix"] or info["tab_route_prefix"]
            self._apply_exposed_model_name(info)

            return {
                "ok": True,
                "domain": target_domain,
                "message": f"已为 {target_domain} 新建独立 Cookie 标签页",
                "tab": info,
            }
    def create_shared_site_tab(self, domain: str) -> Dict[str, Any]:
        target_domain = str(domain or "").strip()
        if not target_domain:
            return {"ok": False, "error": "domain_required"}

        with self._condition:
            self._scan_new_tabs()
            if len(self._tabs) >= self.max_tabs:
                return {"ok": False, "error": "tab_pool_full"}

            target_url = self._build_site_entry_url(target_domain)

        created = self._create_shared_tab(target_url, background=False, new_window=True)
        if not created:
            return {"ok": False, "error": "create_shared_tab_failed"}

        with self._condition:
            if len(self._tabs) >= self.max_tabs:
                logger.warning(
                    "[TabPool] pool reached capacity while creating a shared tab; "
                    "keeping the newly created tab managed"
                )

            session = self._wrap_tab(
                created["tab"],
                created["raw_tab_id"],
                browser_context_id=created.get("browser_context_id"),
                is_isolated_context=False,
                initial_url=created.get("url") or target_url,
            )
            self._tabs[session.id] = session
            self._start_global_monitor_for_session(session)
            self._last_scan_time = time.time()
            self._condition.notify_all()

            info = session.get_info(use_cached_url=True)
            info["tab_route_prefix"] = f"/tab/{session.persistent_index}"
            route_domain = str(info.get("route_domain") or "").strip()
            info["domain_route_prefix"] = f"/url/{route_domain}" if route_domain else ""
            preset_route_domain = str(info.get("current_domain") or route_domain).strip()
            info["preset_route_domain"] = preset_route_domain
            info["preset_domain_route_prefix"] = f"/url/{preset_route_domain}" if preset_route_domain else ""
            url_route_token = str(info.get("url_route_token") or "").strip()
            info["exact_url_route_prefix"] = f"/tab-url/{url_route_token}" if url_route_token else ""
            info["route_prefix"] = info["domain_route_prefix"] or info["tab_route_prefix"]
            self._apply_exposed_model_name(info)

            return {
                "ok": True,
                "domain": target_domain,
                "message": f"已为 {target_domain} 打开共享 Cookie 受控窗口",
                "tab": info,
            }
    def _should_scan(self) -> bool:
        """检查是否需要扫描新标签页"""
        return time.time() - self._last_scan_time >= self.SCAN_INTERVAL
    def _should_scan_for_query(self) -> bool:
        """读接口需要较新的浏览器 target 状态，但按 QUERY_SCAN_MIN_INTERVAL_SEC 节流。

        此前恒返回 True，导致每个 /v1/chat/completions、/v1/models 请求都触发
        一次全量 CDP 扫描（Target.getTargets 等同步网络 I/O），并发时扫描成本
        线性叠加到首字延迟上；浏览器卡顿时还会拖住调用线程。
        1 秒内的快照对路由决策而言足够新鲜。
        """
        return time.time() - self._last_scan_time >= self.QUERY_SCAN_MIN_INTERVAL_SEC
    def _scan_new_tabs(self):
        """扫描并添加新标签页（已持有锁）"""
        try:
            # 先占住本次扫描窗口，避免唤醒后多个等待线程重复扫描。
            self._last_scan_time = time.time()
            # 锁序考量：下面会释放池锁去采集浏览器快照，期间新入池的会话
            # （create_shared_site_tab / create_isolated_site_tab 等）不会出现在
            # 这份过期快照里。记录快照起始时间，应用移除逻辑时据此跳过晚入池的会话
            # （session.created_at 即入池时刻，构造后随即持池锁入池）。
            snapshot_started_at = time.time()
            self._condition.release()
            try:
                with self._scan_snapshot_lock:
                    (
                        current_tabs,
                        current_tab_ids,
                        current_context_by_raw,
                        current_url_by_raw,
                        current_url_known_raw,
                    ) = self._snapshot_current_tab_targets()
            finally:
                self._condition.acquire()

            if self._shutdown:
                return

            current_tab_set = set(current_tab_ids)
            # 物理关闭成功后，Target.getTargets 可能要到下一轮才反映结果；
            # 这里清理已永久隔离且不再持有 tab 引用的陈旧元数据，避免隔离
            # 字典只增不减。
            for raw_id, entry in list(self._quarantined_raw_ids.items()):
                if (
                    getattr(entry, "permanent", False)
                    and getattr(entry, "tab", None) is None
                    and raw_id not in current_tab_set
                ):
                    self._quarantined_raw_ids.pop(raw_id, None)
            self._cleanup_orphaned_isolated_contexts(list(current_context_by_raw.values()))
            changed = False

            session_raw_by_id: Dict[str, str] = {}
            for rid, pidx in self._raw_id_to_persistent.items():
                sid = self._persistent_to_session_id.get(pidx)
                if sid and sid in self._tabs:
                    session_raw_by_id[sid] = rid

            for session_id, session in self._tabs.items():
                if session_raw_by_id.get(session_id):
                    continue
                candidate_raw_id = self._get_tab_ref_id(getattr(session, "tab", None))
                if not candidate_raw_id or candidate_raw_id not in current_tab_set:
                    continue

                existing_persistent_idx = self._raw_id_to_persistent.get(candidate_raw_id)
                if existing_persistent_idx is not None:
                    existing_session_id = self._persistent_to_session_id.get(existing_persistent_idx)
                    if (
                        existing_session_id
                        and existing_session_id != session_id
                        and existing_session_id in self._tabs
                    ):
                        continue

                persistent_idx = int(
                    existing_persistent_idx
                    or getattr(session, "persistent_index", 0)
                    or 0
                )
                if persistent_idx <= 0:
                    persistent_idx = self._next_persistent_index
                    self._next_persistent_index += 1
                session.persistent_index = persistent_idx

                self._raw_id_to_persistent[candidate_raw_id] = persistent_idx
                self._persistent_to_session_id[persistent_idx] = session_id
                self._known_tab_ids.add(candidate_raw_id)
                if session.is_isolated_context:
                    context_id = (
                        str(session.browser_context_id or "").strip()
                        or current_context_by_raw.get(candidate_raw_id)
                    )
                    if context_id:
                        session.browser_context_id = context_id
                        self._register_isolated_context(candidate_raw_id, context_id)
                session_raw_by_id[session_id] = candidate_raw_id
                changed = True
                logger.warning(
                    f"[{session_id}] repaired missing raw tab mapping "
                    f"(raw={candidate_raw_id}, idx=#{persistent_idx})"
                )

            for session_id, session in self._tabs.items():
                raw_id = session_raw_by_id.get(session_id)
                if not raw_id or raw_id not in current_tab_set:
                    continue
                if raw_id in current_url_known_raw:
                    current_url = current_url_by_raw.get(raw_id, "")
                    old_url = session.last_known_url
                    session._remember_url(current_url)
                    try:
                        session.current_domain = extract_remote_site_domain(current_url)
                    except Exception:
                        session.current_domain = None
                    if old_url and current_url and old_url != current_url:
                        session.notify_navigated(reason="url_changed")
                if not session.is_isolated_context:
                    continue
                if session.status == TabStatus.BUSY:
                    continue
                bound_raw_id = self._get_tab_ref_id(getattr(session, "tab", None))
                if bound_raw_id == raw_id:
                    session.clear_transient_disconnect()
                    continue
                if self._refresh_isolated_session_binding(session, raw_id):
                    logger.info(f"[{session_id}] isolated session target recovered: {raw_id}")
                    continue
                if session.is_in_transient_disconnect():
                    logger.debug(
                        f"[{session_id}] isolated session still waiting for target recovery: {raw_id}"
                    )

            reserved_raw_ids = {
                raw_id
                for raw_id in session_raw_by_id.values()
                if raw_id in current_tab_set
            }

            # ===== 第一步：清理已关闭的标签页 =====
            # 找出池中存在、但浏览器中已消失的标签页
            sessions_to_remove = []
            for session_id, session in self._tabs.items():
                # 查找该 session 对应的 raw_tab_id
                raw_id = session_raw_by_id.get(session_id)

                if raw_id is None or raw_id not in current_tab_set:
                    # 入池晚于本次快照起始的会话必然不在快照里，不能据此判定"已关闭"
                    # （isolated 会话有 rebind 宽限兜底，shared 会话没有，会被直接移除）。
                    # 跳过本轮，等下一轮新快照再判定。
                    joined_at = float(getattr(session, "created_at", 0.0) or 0.0)
                    if joined_at > snapshot_started_at:
                        logger.debug(
                            f"[{session_id}] 跳过过期快照的移除判定 "
                            f"(入池晚于快照起始 {joined_at - snapshot_started_at:.3f}s, raw={raw_id})"
                        )
                        continue
                    sessions_to_remove.append((session_id, raw_id, session))

            for session_id, raw_id, session in sessions_to_remove:
                replacement_raw_id = None
                replacement_tab = None
                browser_context_id = str(session.browser_context_id or "").strip()
                if session.is_isolated_context and browser_context_id:
                    for candidate_raw_id in current_tab_ids:
                        if candidate_raw_id in reserved_raw_ids:
                            continue
                        if current_context_by_raw.get(candidate_raw_id) != browser_context_id:
                            continue
                        replacement_tab = self._resolve_tab_from_ref(candidate_raw_id)
                        if not replacement_tab:
                            continue
                        replacement_raw_id = candidate_raw_id
                        break

                if replacement_raw_id and replacement_tab:
                    self._detach_global_monitor_for_session(session_id, reason="target_rebind")
                    p_idx = self._raw_id_to_persistent.pop(raw_id, None) if raw_id else None
                    if p_idx is None:
                        p_idx = int(getattr(session, "persistent_index", 0) or 0) or None
                    if p_idx is not None:
                        self._raw_id_to_persistent[replacement_raw_id] = p_idx
                        self._persistent_to_session_id[p_idx] = session_id
                    if raw_id:
                        self._known_tab_ids.discard(raw_id)
                    self._known_tab_ids.add(replacement_raw_id)
                    if raw_id:
                        self._isolated_context_by_raw_id.pop(raw_id, None)
                    self._register_isolated_context(replacement_raw_id, browser_context_id)
                    session.tab = replacement_tab
                    session.browser_context_id = browser_context_id or current_context_by_raw.get(replacement_raw_id)
                    session.clear_transient_disconnect()
                    try:
                        _cached_url, cached_domain = session.get_cached_route_snapshot()
                        if cached_domain:
                            session.current_domain = cached_domain
                    except Exception:
                        pass
                    reserved_raw_ids.add(replacement_raw_id)
                    if session.status == TabStatus.IDLE:
                        self._restart_global_monitor_for_session_async(session_id, "target_rebind")
                    logger.info(
                        f"[{session_id}] rebound isolated context target: {raw_id} -> {replacement_raw_id}"
                    )
                    changed = True
                    continue

                if session.is_isolated_context and browser_context_id:
                    if self._arm_isolated_rebind_grace(
                        session,
                        reason="target_missing",
                        detail=f"target missing (raw={raw_id})",
                    ):
                        continue

                if session.status == TabStatus.BUSY:
                    self._cancel_active_request_for_session(
                        session,
                        "tab_closed",
                        detail=f"raw={raw_id}",
                    )
                    logger.warning(f"[{session_id}] 标签页已关闭但仍在忙碌，标记为关闭")
                    session.mark_closed("tab_closed")
                    self._detach_global_monitor_for_session(session_id, reason="tab_closed")
                else:
                    logger.info(f"[{session_id}] 标签页已关闭，从池中移除")
                    session.mark_closed("tab_closed")
                    self._detach_global_monitor_for_session(session_id, reason="tab_closed")
                    del self._tabs[session_id]
                    self._on_session_removed(session_id)

                # 清理映射
                if raw_id:
                    self._known_tab_ids.discard(raw_id)
                p_idx = self._raw_id_to_persistent.pop(raw_id, None) if raw_id else None
                if p_idx is None:
                    p_idx = int(getattr(session, "persistent_index", 0) or 0) or None
                if p_idx is not None:
                    if self._persistent_to_session_id.get(p_idx) == session_id:
                        self._persistent_to_session_id.pop(p_idx, None)
                browser_context_id = self._isolated_context_by_raw_id.pop(raw_id, None) if raw_id else None
                if not browser_context_id and session.is_isolated_context:
                    browser_context_id = str(session.browser_context_id or "").strip()
                if browser_context_id:
                    self._mark_orphaned_isolated_context(browser_context_id)
                if self._active_session_id == session_id:
                    self._active_session_id = None
                changed = True

            # 顺手清理已切换到本地页/无效页的空闲标签，避免继续展示和参与调度。
            self._cleanup_unhealthy_tabs()

            # ===== 第二步：构建"已在池中的 tab 对象"集合 =====
            tabs_in_pool = set()
            for rid in self._raw_id_to_persistent:
                pidx = self._raw_id_to_persistent[rid]
                sid = self._persistent_to_session_id.get(pidx)
                if sid and sid in self._tabs:
                    tabs_in_pool.add(rid)

            # ===== 第三步：扫描新标签页 =====
            new_count = 0
            for tab_ref in current_tabs:
                if len(self._tabs) >= self.max_tabs:
                    break

                raw_tab = self._get_tab_ref_id(tab_ref)
                if not raw_tab:
                    continue

                # 已在池中，跳过
                if raw_tab in tabs_in_pool:
                    continue

                # 隔离中的错误标签页不重新入池：旧 worker 可能尚未退出，
                # 由 TabRecoveryService 决定何时解除隔离（或永久排除）。
                if raw_tab in self._quarantined_raw_ids:
                    logger.debug(f"[TabRecovery] 扫描跳过隔离中的标签页: {raw_tab}")
                    continue

                try:
                    url = current_url_by_raw.get(raw_tab) or self._get_tab_ref_url(tab_ref)

                    # 本地页、浏览器内部页、空白页都不纳入标签页池。
                    if _should_skip_pool_url(url):
                        continue

                    tab = self._resolve_tab_from_ref(tab_ref)
                    if not tab:
                        continue

                    isolation_result = self._ensure_cookie_isolation_for_new_tab(tab, raw_tab, url)
                    tab = isolation_result["tab"]
                    raw_tab = isolation_result["raw_tab_id"]
                    url = isolation_result["url"]

                    # 有效页面 - 添加到池
                    session = self._wrap_tab(
                        tab,
                        raw_tab,
                        browser_context_id=isolation_result.get("browser_context_id"),
                        is_isolated_context=isolation_result.get("is_isolated_context", False),
                        initial_url=url,
                    )
                    self._tabs[session.id] = session
                    self._start_global_monitor_for_session(session)
                    new_count += 1
                    changed = True

                    display_url = url[:60] + "..." if len(url) > 60 else url
                    logger.debug(f"🆕 发现新标签页: {session.id} -> {display_url}")

                except Exception as e:
                    logger.debug(f"处理标签页出错: {e}")
                    continue

            # Remove unusable pool sessions after discovery without closing targets.
            self._cleanup_unhealthy_tabs()
            self._last_scan_time = time.time()
            if changed:
                self._condition.notify_all()

            if new_count > 0:
                logger.info(f"扫描完成: +{new_count} 个，当前共 {len(self._tabs)} 个标签页")

        except Exception as e:
            logger.warning(f"扫描标签页失败: {e}")
    def initialize(self):
        """初始化标签页池"""
        with self._lock:
            if self._initialized:
                return

            raw_target_count = 0
            try:
                browser = self._get_browser_handle()
                existing_tabs = self._list_current_tab_refs()
                raw_target_count = len(existing_tabs)
                logger.debug(f"[TabPool] 检测到 {raw_target_count} 个浏览器 page target")

                for tab_ref in existing_tabs:
                    if len(self._tabs) >= self.max_tabs:
                        break

                    try:
                        raw_tab = self._get_tab_ref_id(tab_ref)
                        if not raw_tab:
                            continue

                        url = self._get_tab_ref_url(tab_ref)
                        # 初始化时直接跳过本地页和浏览器内部页。
                        if _should_skip_pool_url(url):
                            continue

                        tab = self._resolve_tab_from_ref(tab_ref)
                        if not tab:
                            continue

                        isolation_result = self._ensure_cookie_isolation_for_new_tab(tab, raw_tab, url)
                        tab = isolation_result["tab"]
                        raw_tab = isolation_result["raw_tab_id"]
                        url = isolation_result["url"]

                        # 有效页面 - 添加到池
                        session = self._wrap_tab(
                            tab,
                            raw_tab,
                            browser_context_id=isolation_result.get("browser_context_id"),
                            is_isolated_context=isolation_result.get("is_isolated_context", False),
                            initial_url=url,
                        )
                        self._tabs[session.id] = session

                        display_url = url[:60] + "..." if len(url) > 60 else url
                        logger.info(f"TabPool: {session.id} -> {display_url}")
                    except Exception as e:
                        logger.debug(f"处理标签页出错: {e}")
                        continue

            except Exception as e:
                logger.warning(f"扫描标签页失败: {e}")

            # 重置所有状态为 IDLE
            for session in self._tabs.values():
                # 锁序考量：状态迁移须在 session._lock 内进行，与 acquire/release 的
                # 状态检查互斥。既有锁序为 池锁 → session._lock（release 等路径同序），
                # session._lock 内不会反向取池锁，无死锁风险。
                with session._lock:
                    session.status = TabStatus.IDLE
                    session.current_task_id = None
                self._start_global_monitor_for_session(session)

            self._initialized = True
            self._last_scan_time = time.time()
            ignored_count = max(0, raw_target_count - len(self._tabs))
            logger.info(
                f"TabPool 就绪: {len(self._tabs)} 个远程网页"
                + (f"（已忽略 {ignored_count} 个内部/无效 target）" if ignored_count else "")
            )
    def refresh_tabs(self) -> Dict:
        """手动刷新标签页列表（供外部调用）"""
        with self._condition:
            old_count = len(self._tabs)
            old_ids = set(self._tabs.keys())

            # 强制扫描（不受时间间隔限制）
            self._last_scan_time = 0
            self._scan_new_tabs()

            # 同时清理不健康的标签页
            self._cleanup_unhealthy_tabs()

            new_ids = set(self._tabs.keys())
            added = new_ids - old_ids
            removed = old_ids - new_ids

            if added or removed:
                self._condition.notify_all()
                logger.info(f"刷新完成: +{len(added)} -{len(removed)} = {len(self._tabs)} 个标签页")

            return {
                "added": len(added),
                "removed": len(removed),
                "total": len(self._tabs)
            }
