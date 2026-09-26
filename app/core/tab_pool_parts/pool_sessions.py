"""TabPoolManager 的一部分（R2-3 拆分）：会话封装、移除、请求取消、隔离区（quarantine）与恢复服务。

方法原样从 manager.py 迁出，行为不变；通过 mixin 组合回 TabPoolManager，
类属性与实例状态都在 TabPoolManager（manager.py）里定义。
"""

from typing import Any, Optional

from app.core.config import logger
from app.utils.site_url import extract_remote_site_domain

from .recovery import TabQuarantineEntry, TabRecoveryService
from .session import TabSession


class TabPoolSessionsMixin:
    """会话封装、移除、请求取消、隔离区（quarantine）与恢复服务"""

    def _get_domain_abbr(self, url: str) -> str:
        try:
            if not url or "://" not in url:
                return "tab"

            domain = url.split("//")[-1].split("/")[0].lower()
            clean_domain = domain.replace("www.", "")

            for key, abbr in self.DOMAIN_ABBR_MAP.items():
                if key in clean_domain:
                    return abbr

            first_part = clean_domain.split(".")[0]
            return first_part[:10]

        except Exception:
            return "tab"
    @staticmethod
    def _get_tab_ref_id(tab_ref: Any) -> Optional[str]:
        if tab_ref is None:
            return None

        if isinstance(tab_ref, tuple) and tab_ref:
            return str(tab_ref[0] or "").strip() or None
        if isinstance(tab_ref, dict):
            target_id = str(tab_ref.get("targetId") or tab_ref.get("tab_id") or "").strip()
            return target_id or None

        tab_id = getattr(tab_ref, "tab_id", None)
        if tab_id:
            return str(tab_id)

        raw = str(tab_ref or "").strip()
        return raw or None
    @staticmethod
    def _get_tab_ref_url(tab_ref: Any) -> str:
        if isinstance(tab_ref, tuple) and len(tab_ref) >= 2 and isinstance(tab_ref[1], dict):
            return str(tab_ref[1].get("url") or "").strip()
        if isinstance(tab_ref, dict):
            return str(tab_ref.get("url") or "").strip()
        try:
            return str(getattr(tab_ref, "url", "") or "").strip()
        except Exception:
            return ""
    @staticmethod
    def _tab_ref_has_url_field(tab_ref: Any) -> bool:
        if isinstance(tab_ref, tuple) and len(tab_ref) >= 2 and isinstance(tab_ref[1], dict):
            return "url" in tab_ref[1]
        if isinstance(tab_ref, dict):
            return "url" in tab_ref
        return False
    def _resolve_tab_from_ref(self, tab_ref: Any) -> Optional[Any]:
        if tab_ref is None:
            return None
        if isinstance(tab_ref, tuple) and len(tab_ref) >= 3:
            existing_tab = tab_ref[2]
            if existing_tab is not None:
                return existing_tab

        try:
            existing_tab_id = getattr(tab_ref, "tab_id", None)
        except Exception:
            existing_tab_id = None
        if existing_tab_id:
            return tab_ref

        raw_tab_id = self._get_tab_ref_id(tab_ref)
        if not raw_tab_id:
            return None

        try:
            browser = self._get_browser_handle()
            return browser.get_tab(raw_tab_id)
        except Exception as exc:
            if self._is_execution_context_error(exc) and self._schedule_context_recovery(raw_tab_id):
                logger.warning(
                    f"[TabPool] 标签页 {raw_tab_id} 丢失 JS 执行上下文，"
                    "已异步请求 CDP 刷新唤醒"
                )
            return None
    def _wrap_tab(
        self,
        tab,
        raw_tab_id: str = None,
        *,
        browser_context_id: Optional[str] = None,
        is_isolated_context: bool = False,
        initial_url: str = "",
    ) -> TabSession:
        self._tab_counter += 1

        url = str(initial_url or "").strip()
        if not url:
            try:
                url = tab.url or ""
            except Exception:
                pass

        abbr = self._get_domain_abbr(url)
        tab_id = f"{abbr}_{self._tab_counter}"

        session = TabSession(
            id=tab_id,
            tab=tab,
            browser_context_id=browser_context_id,
            is_isolated_context=bool(is_isolated_context),
        )
        session._remember_url(url)

        try:
            session.current_domain = extract_remote_site_domain(url)
        except:
            pass

        # 记录底层标签页 ID
        if raw_tab_id:
            self._known_tab_ids.add(raw_tab_id)
            if session.is_isolated_context:
                self._register_isolated_context(raw_tab_id, browser_context_id)

            # 🆕 分配持久化编号
            if raw_tab_id not in self._raw_id_to_persistent:
                persistent_idx = self._next_persistent_index
                self._next_persistent_index += 1
                self._raw_id_to_persistent[raw_tab_id] = persistent_idx
            else:
                persistent_idx = self._raw_id_to_persistent[raw_tab_id]

            session.persistent_index = persistent_idx
            self._persistent_to_session_id[persistent_idx] = session.id
            logger.debug(f"标签页 {session.id} 分配编号 #{persistent_idx}")

        self._apply_preset_overrides(session)
        try:
            setattr(tab, "_session", session)
        except Exception:
            pass
        session.notify_navigated(reason="tab_created")
        return session
    def _on_session_removed(self, session_id: str):
        """Notify command engine after a pool session is removed without blocking pool locks."""
        session_key = str(session_id or "").strip()
        if not session_key:
            return

        def _evict():
            try:
                from app.services.command_engine import command_engine

                if hasattr(command_engine, "evict_session"):
                    command_engine.evict_session(session_key)
            except Exception as e:
                logger.debug(f"[TabPool] evict session {session_key} failed: {e}")

        executor = self._maintenance_executor
        if executor is None:
            return
        try:
            executor.submit(_evict)
        except RuntimeError as e:
            logger.debug(f"[TabPool] maintenance submit failed (evict:{session_key}): {e}")
    def _submit_request_cancel(
        self,
        task_id: str,
        reason: str,
        *,
        session_id: str = "",
        detail: str = "",
    ) -> bool:
        """Cancel a request from the maintenance worker so pool locks never call outward."""
        task_key = str(task_id or "").strip()
        if not task_key:
            return False
        reason_key = str(reason or "").strip() or "unknown"
        session_key = str(session_id or "").strip()
        detail_text = str(detail or "").strip()

        def _cancel():
            cancelled = False
            try:
                from app.services.request_manager import request_manager

                cancelled = bool(request_manager.cancel_request(task_key, reason_key))
            except Exception as e:
                logger.debug(
                    f"[{session_key or 'TabPool'}] async cancel failed "
                    f"(task={task_key}, reason={reason_key}, detail={detail_text or '-'}): {e}"
                )
                return

            logger.debug(
                f"[{session_key or 'TabPool'}] async cancel result "
                f"(task={task_key}, reason={reason_key}, cancelled={cancelled}, "
                f"detail={detail_text or '-'})"
            )

        executor = self._maintenance_executor
        if executor is None:
            logger.debug(
                f"[{session_key or 'TabPool'}] async cancel skipped: maintenance executor unavailable "
                f"(task={task_key}, reason={reason_key}, detail={detail_text or '-'})"
            )
            return False
        try:
            executor.submit(_cancel)
            return True
        except RuntimeError as e:
            logger.debug(
                f"[{session_key or 'TabPool'}] maintenance submit failed "
                f"(cancel:{task_key}, reason={reason_key}, detail={detail_text or '-'}): {e}"
            )
            return False
    def _close_quarantined_tab(self, tab: Any, raw_tab_id: str) -> bool:
        """Close a permanently quarantined browser target outside the pool lock."""
        close = getattr(tab, "close", None)
        if callable(close):
            try:
                close_result = close()
                if close_result is not False:
                    return True
                logger.debug(
                    f"[TabRecovery] 标签页对象 close() 返回失败，尝试 CDP 兜底 "
                    f"(raw={raw_tab_id})"
                )
            except Exception as exc:
                logger.debug(
                    f"[TabRecovery] 关闭隔离标签页对象失败，尝试 CDP 兜底 "
                    f"(raw={raw_tab_id}, error={exc})"
                )

        try:
            browser = self._get_browser_handle()
            run_cdp = getattr(browser, "_run_cdp", None)
            if callable(run_cdp):
                result = run_cdp("Target.closeTarget", targetId=str(raw_tab_id))
                if not isinstance(result, dict) or result.get("success", True):
                    return True
        except Exception as exc:
            logger.warning(
                f"[TabRecovery] 关闭永久隔离标签页失败 "
                f"(raw={raw_tab_id}, error={exc})"
            )
        return False
    def resolve_quarantine(self, raw_tab_id: str, release: bool) -> bool:
        """解除或永久化一个隔离条目（供 TabRecoveryService 回调）。

        release=True：从隔离集合移除，下一次 _scan_new_tabs 自然重新入池；
        release=False：关闭浏览器标签页并释放强引用；关闭失败时保留不带
        tab 引用的轻量永久隔离记录，避免扫描期间重新入池。
        """
        raw_id = str(raw_tab_id or "").strip()
        if not raw_id:
            return False

        tab_to_close = None
        entry = None
        with self._condition:
            entry = self._quarantined_raw_ids.get(raw_id)
            if entry is None:
                return False
            if release:
                self._quarantined_raw_ids.pop(raw_id, None)
                # 恢复任务结束后不再需要保留 DrissionPage tab wrapper。
                entry.tab = None
                self._condition.notify_all()
                return True

            entry.permanent = True
            tab_to_close = getattr(entry, "tab", None)
            # 先在锁内摘掉强引用；真实 CDP 调用必须在锁外执行。
            entry.tab = None

        closed = self._close_quarantined_tab(tab_to_close, raw_id)
        with self._condition:
            if self._quarantined_raw_ids.get(raw_id) is entry:
                if closed:
                    self._quarantined_raw_ids.pop(raw_id, None)
                self._condition.notify_all()
        return True
    def get_quarantine_entry(self, raw_tab_id: str) -> Optional[TabQuarantineEntry]:
        with self._condition:
            return self._quarantined_raw_ids.get(str(raw_tab_id or "").strip())
    def _get_recovery_service(self) -> Optional[TabRecoveryService]:
        if self._shutdown:
            return None
        service = self._recovery_service
        if service is not None:
            return service
        with self._recovery_service_lock:
            if self._recovery_service is None:
                self._recovery_service = TabRecoveryService(
                    resolve_quarantine=self.resolve_quarantine,
                    get_quarantine_entry=self.get_quarantine_entry,
                )
            return self._recovery_service
    def _submit_recovery(self, entry: TabQuarantineEntry) -> None:
        """把隔离条目投递给恢复服务；持池锁调用也安全（仅入队，不阻塞外呼）。"""

        def _submit() -> None:
            try:
                service = self._get_recovery_service()
                if service is not None:
                    service.submit(entry)
            except Exception as e:
                logger.warning(
                    f"[TabRecovery] 投递恢复任务失败 ({entry.raw_tab_id}): {e}"
                )

        executor = self._maintenance_executor
        if executor is not None:
            try:
                executor.submit(_submit)
                return
            except RuntimeError as e:
                logger.debug(
                    f"[TabRecovery] maintenance submit failed "
                    f"(recovery:{entry.raw_tab_id}): {e}"
                )
        # 兜底：maintenance executor 不可用时直接入队（submit 本身非阻塞）。
        _submit()
