"""TabPoolManager 的一部分（R2-3 拆分）：浏览器上下文与 CDP：独立 Cookie 上下文、目标快照、强制重载、上下文恢复。

方法原样从 manager.py 迁出，行为不变；通过 mixin 组合回 TabPoolManager，
类属性与实例状态都在 TabPoolManager（manager.py）里定义。
"""

import json
import threading
import time
from typing import Any, Dict, List, Optional

from app.core.config import logger

from ._utils import _looks_like_transient_local_debug_error
from .session import TabSession, TabStatus


class TabPoolContextMixin:
    """浏览器上下文与 CDP：独立 Cookie 上下文、目标快照、强制重载、上下文恢复"""

    def _get_target_info(self, raw_tab_id: str) -> Dict[str, Any]:
        if not raw_tab_id:
            return {}

        try:
            browser = self._get_browser_handle()
            if browser is None or not hasattr(browser, "_run_cdp"):
                return {}

            result = browser._run_cdp("Target.getTargetInfo", targetId=raw_tab_id) or {}
            info = result.get("targetInfo") or {}
            return info if isinstance(info, dict) else {}
        except Exception as e:
            logger.debug(f"[TabPool] get target info failed ({raw_tab_id}): {e}")
            return {}
    def _get_browser_context_id(self, raw_tab_id: str) -> Optional[str]:
        if raw_tab_id in self._isolated_context_by_raw_id:
            return self._isolated_context_by_raw_id.get(raw_tab_id)

        info = self._get_target_info(raw_tab_id)
        browser_context_id = str(info.get("browserContextId") or "").strip()
        return browser_context_id or None
    def _snapshot_current_tab_targets(
        self,
    ) -> tuple[List[Any], List[str], Dict[str, str], Dict[str, str], set[str]]:
        current_tabs = self._list_current_tab_refs()
        current_tab_ids = [
            raw_id for raw_id in
            (self._get_tab_ref_id(tab_ref) for tab_ref in current_tabs)
            if raw_id
        ]

        current_context_by_raw: Dict[str, str] = {}
        current_url_by_raw: Dict[str, str] = {}
        current_url_known_raw: set[str] = set()
        for tab_ref in current_tabs:
            raw_id = self._get_tab_ref_id(tab_ref)
            if not raw_id:
                continue
            if self._tab_ref_has_url_field(tab_ref):
                current_url_known_raw.add(raw_id)
            url = self._get_tab_ref_url(tab_ref)
            if url:
                current_url_by_raw[raw_id] = url

        target_info_by_raw: Dict[str, Dict[str, Any]] = {}
        for tab_ref in current_tabs:
            raw_id = self._get_tab_ref_id(tab_ref)
            if not raw_id:
                continue
            if isinstance(tab_ref, tuple) and len(tab_ref) >= 2 and isinstance(tab_ref[1], dict):
                target_info_by_raw[raw_id] = tab_ref[1]
            elif isinstance(tab_ref, dict):
                target_info_by_raw[raw_id] = tab_ref

        with self._lock:
            cached_contexts = dict(self._isolated_context_by_raw_id)
        for raw_id in current_tab_ids:
            cached_context = str(cached_contexts.get(raw_id) or "").strip()
            if cached_context:
                current_context_by_raw[raw_id] = cached_context
                continue
            target_info = target_info_by_raw.get(raw_id) or {}
            context_id = str(target_info.get("browserContextId") or "").strip()
            if context_id:
                current_context_by_raw[raw_id] = context_id
            if raw_id in current_context_by_raw:
                continue
            try:
                info = self._get_target_info(raw_id)
                context_id = str(info.get("browserContextId") or "").strip()
                if context_id:
                    current_context_by_raw[raw_id] = context_id
            except Exception as e:
                logger.debug(f"[TabPool] skip context lookup for {raw_id}: {e}")

        return current_tabs, current_tab_ids, current_context_by_raw, current_url_by_raw, current_url_known_raw
    def _is_site_independent_cookie_enabled(self, domain: str) -> bool:
        normalized_domain = str(domain or "").strip()
        if not normalized_domain:
            return False

        try:
            from app.services.config_engine import config_engine

            advanced = config_engine.get_site_advanced_config(normalized_domain)
            return bool(advanced.get("independent_cookies", False))
        except Exception as e:
            logger.debug(f"[TabPool] load site advanced config failed ({normalized_domain}): {e}")
            return False
    def _is_site_independent_cookie_auto_takeover_enabled(self, domain: str) -> bool:
        normalized_domain = str(domain or "").strip()
        if not normalized_domain:
            return False

        try:
            from app.services.config_engine import config_engine

            advanced = config_engine.get_site_advanced_config(normalized_domain)
            return bool(advanced.get("independent_cookies_auto_takeover", False))
        except Exception as e:
            logger.debug(f"[TabPool] load site advanced config failed ({normalized_domain}): {e}")
            return False
    def _register_isolated_context(self, raw_tab_id: str, browser_context_id: Optional[str]) -> None:
        context_id = str(browser_context_id or "").strip()
        if raw_tab_id and context_id:
            self._isolated_context_by_raw_id[raw_tab_id] = context_id
            self._orphaned_isolated_contexts.pop(context_id, None)
    def _mark_orphaned_isolated_context(self, browser_context_id: Optional[str]) -> None:
        context_id = str(browser_context_id or "").strip()
        if context_id:
            self._orphaned_isolated_contexts[context_id] = (
                time.time() + self.ISOLATED_CONTEXT_ORPHAN_GRACE_SEC
            )
    def _cleanup_orphaned_isolated_contexts(
        self,
        current_context_ids: Optional[List[str]] = None,
    ) -> None:
        if not self._orphaned_isolated_contexts:
            return

        now = time.time()
        active_contexts = {
            str(context_id).strip()
            for context_id in (current_context_ids or [])
            if str(context_id).strip()
        }
        active_contexts.update(
            str(context_id).strip()
            for context_id in self._isolated_context_by_raw_id.values()
            if str(context_id).strip()
        )

        for context_id, expire_at in list(self._orphaned_isolated_contexts.items()):
            if context_id in active_contexts:
                self._orphaned_isolated_contexts.pop(context_id, None)
                continue
            if now < expire_at:
                continue
            self._orphaned_isolated_contexts.pop(context_id, None)
            self._dispose_browser_context_async(context_id)
            logger.info(
                f"[TabPool] disposed orphaned isolated context after grace: {context_id}"
            )
    def _dispose_browser_context(self, browser_context_id: Optional[str]) -> None:
        context_id = str(browser_context_id or "").strip()
        if not context_id:
            return

        try:
            browser = self._get_browser_handle()
            if browser is None or not hasattr(browser, "_run_cdp"):
                return
            browser._run_cdp("Target.disposeBrowserContext", browserContextId=context_id)
        except Exception as e:
            logger.debug(f"[TabPool] dispose browser context failed ({context_id}): {e}")
    def _dispose_browser_context_async(self, browser_context_id: Optional[str]) -> None:
        context_id = str(browser_context_id or "").strip()
        if not context_id:
            return

        executor = self._maintenance_executor
        if executor is None:
            return

        try:
            executor.submit(self._dispose_browser_context, context_id)
        except RuntimeError as e:
            logger.debug(f"[TabPool] maintenance submit failed (dispose:{context_id}): {e}")
    def _arm_isolated_rebind_grace(self, session: TabSession, reason: str, detail: str) -> bool:
        now = time.time()
        same_reason = session.transient_disconnect_reason == reason
        if same_reason and session.transient_disconnect_until > now:
            return True
        if same_reason and session.transient_disconnect_until > 0:
            logger.warning(
                f"[{session.id}] isolated session grace expired ({detail}), removing from pool"
            )
            session.clear_transient_disconnect()
            return False
        session.mark_transient_disconnect(self.ISOLATED_CONTEXT_REBIND_GRACE_SEC, reason=reason)
        logger.warning(
            f"[{session.id}] isolated session entered {self.ISOLATED_CONTEXT_REBIND_GRACE_SEC:.0f}s "
            f"rebind grace ({detail})"
        )
        return True
    def _refresh_isolated_session_binding(self, session: TabSession, raw_tab_id: str) -> bool:
        if not session.is_isolated_context or not raw_tab_id:
            return False

        replacement_tab = self._resolve_tab_from_ref(raw_tab_id)
        if not replacement_tab:
            return False

        self._detach_global_monitor_for_session(session.id, reason="target_rebind")
        session.tab = replacement_tab
        session.clear_transient_disconnect()
        try:
            _cached_url, cached_domain = session.get_cached_route_snapshot()
            if cached_domain:
                session.current_domain = cached_domain
        except Exception:
            pass
        if session.status == TabStatus.IDLE:
            self._restart_global_monitor_for_session_async(session.id, "target_rebind")
        return True
    def _get_browser_handle(self):
        browser = getattr(self.page, "browser", None)
        if browser is not None:
            return browser
        return self.page
    @staticmethod
    def _run_cdp_compat(target: Any, method: str, *, timeout: float = 2.0, **params):
        """Call DrissionPage CDP APIs across minor 4.x signature differences."""
        runner = getattr(target, "run_cdp", None)
        if not callable(runner):
            runner = getattr(target, "_run_cdp", None)
        if not callable(runner):
            raise AttributeError(f"CDP runner unavailable for {method}")

        timeout_value = max(0.1, float(timeout))
        try:
            return runner(method, _timeout=timeout_value, **params)
        except TypeError as exc:
            # A few older wrappers do not expose DrissionPage's private timeout
            # keyword. Retry only when the exception clearly concerns that kwarg.
            error_text = str(exc).lower()
            if "timeout" not in error_text:
                raise
            return runner(method, **params)
    @staticmethod
    def _is_execution_context_error(error: Any) -> bool:
        text = str(error or "").strip().lower()
        if not text:
            return False
        return any(
            marker in text
            for marker in (
                "cannot find default execution context",
                "execution context was destroyed",
                "execution context is not available",
                "context was destroyed",
            )
        )
    @staticmethod
    def _is_unsupported_cdp_method(error: Any) -> bool:
        text = str(error or "").strip().lower()
        return any(
            marker in text
            for marker in (
                "unknown method",
                "method not found",
                "not supported",
                "unsupported command",
            )
        )
    def _find_idle_tab_by_raw_id(self, raw_tab_id: str) -> Optional[Any]:
        """Find an existing idle wrapper before opening another CDP session."""
        raw_id = str(raw_tab_id or "").strip()
        if not raw_id:
            return None

        with self._lock:
            sessions = list(getattr(self, "_tabs", {}).values())
        for session in sessions:
            try:
                with session._lock:
                    if session.status != TabStatus.IDLE or session._termination_in_progress:
                        continue
                    tab = getattr(session, "tab", None)
                if self._get_tab_ref_id(tab) == raw_id:
                    return tab
            except Exception:
                continue
        return None
    def _force_reload_flat_via_cdp(self, browser: Any, target_id: str) -> bool:
        """Fallback for protocol versions where nested Target messaging is unavailable."""
        driver = getattr(browser, "_driver", None)
        send = getattr(driver, "_send", None)
        if not callable(send):
            return False

        session_id = None
        try:
            result = self._run_cdp_compat(
                browser,
                "Target.attachToTarget",
                timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                targetId=target_id,
                flatten=True,
            ) or {}
            session_id = result.get("sessionId") if isinstance(result, dict) else None
            if not session_id:
                return False

            message = {
                "method": "Page.reload",
                "params": {},
                "sessionId": session_id,
            }
            try:
                response = send(message, timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC)
            except TypeError as exc:
                if "timeout" not in str(exc).lower():
                    raise
                response = send(message)
            return isinstance(response, dict) and "error" not in response
        except Exception as exc:
            logger.debug(f"[TabPool] flat CDP 刷新唤醒失败 (target={target_id}): {exc}")
            return False
        finally:
            if session_id:
                try:
                    self._run_cdp_compat(
                        browser,
                        "Target.detachFromTarget",
                        timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                        sessionId=session_id,
                    )
                except Exception as exc:
                    logger.debug(
                        f"[TabPool] flat CDP session detach 失败 "
                        f"(target={target_id}, session={session_id}): {exc}"
                    )
    def _force_reload_tab_via_cdp(self, target_id: str) -> bool:
        """Best-effort reload that works with old nested and newer flat CDP sessions."""
        raw_id = str(target_id or "").strip()
        if not raw_id or self._shutdown:
            return False

        existing_tab = self._find_idle_tab_by_raw_id(raw_id)
        if existing_tab is not None:
            try:
                reload_result = self._run_cdp_compat(
                    existing_tab,
                    "Page.reload",
                    timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                )
                if isinstance(reload_result, dict) and reload_result.get("error"):
                    raise RuntimeError(str(reload_result.get("error")))
                return True
            except Exception as exc:
                logger.debug(
                    f"[TabPool] 复用现有 tab wrapper 刷新失败，改用 browser CDP "
                    f"(target={raw_id}): {exc}"
                )

        try:
            browser = self._get_browser_handle()
            if browser is None:
                return False
            run_cdp = getattr(browser, "_run_cdp", None)
            if not callable(run_cdp):
                # Some older/public wrappers expose only run_cdp().
                run_cdp = getattr(browser, "run_cdp", None)
            if not callable(run_cdp):
                return False
        except Exception:
            return False

        session_id = None
        use_flat_fallback = False
        try:
            # Omitting flatten keeps the legacy nested-session path compatible
            # with older Chromium versions. Current Chrome accepts this too.
            result = self._run_cdp_compat(
                browser,
                "Target.attachToTarget",
                timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                targetId=raw_id,
            ) or {}
            session_id = result.get("sessionId") if isinstance(result, dict) else None
            if not session_id:
                return False

            message = json.dumps(
                {"id": 1001, "method": "Page.reload", "params": {}},
                separators=(",", ":"),
            )
            try:
                nested_result = self._run_cdp_compat(
                    browser,
                    "Target.sendMessageToTarget",
                    timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                    message=message,
                    sessionId=session_id,
                )
                if isinstance(nested_result, dict) and nested_result.get("error"):
                    raise RuntimeError(str(nested_result.get("error")))
                return True
            except Exception as exc:
                use_flat_fallback = self._is_unsupported_cdp_method(exc)
                if not use_flat_fallback:
                    logger.debug(
                        f"[TabPool] 嵌套 CDP 刷新唤醒失败 "
                        f"(target={raw_id}): {exc}"
                    )
                    return False
        except Exception as exc:
            logger.debug(f"[TabPool] CDP attach 刷新唤醒失败 (target={raw_id}): {exc}")
            return False
        finally:
            if session_id:
                try:
                    self._run_cdp_compat(
                        browser,
                        "Target.detachFromTarget",
                        timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                        sessionId=session_id,
                    )
                except Exception as exc:
                    logger.debug(
                        f"[TabPool] CDP session detach 失败 "
                        f"(target={raw_id}, session={session_id}): {exc}"
                    )

        return use_flat_fallback and self._force_reload_flat_via_cdp(browser, raw_id)
    def _run_context_recovery(self, raw_tab_id: str) -> None:
        try:
            self._force_reload_tab_via_cdp(raw_tab_id)
        except Exception as exc:
            logger.debug(f"[TabPool] 上下文恢复任务失败 (target={raw_tab_id}): {exc}")
        finally:
            recovery_lock = getattr(self, "_context_recovery_lock", None)
            if recovery_lock is not None:
                with recovery_lock:
                    getattr(self, "_context_recovery_pending", set()).discard(raw_tab_id)
    def _schedule_context_recovery(self, raw_tab_id: str) -> bool:
        """Schedule one reload per target cooldown without holding the pool lock."""
        raw_id = str(raw_tab_id or "").strip()
        if not raw_id or self._shutdown:
            return False

        recovery_lock = getattr(self, "_context_recovery_lock", None)
        if recovery_lock is None:
            recovery_lock = threading.Lock()
            self._context_recovery_lock = recovery_lock
        pending = getattr(self, "_context_recovery_pending", None)
        requested_at = getattr(self, "_context_recovery_requested_at", None)
        if pending is None:
            pending = set()
            self._context_recovery_pending = pending
        if requested_at is None:
            requested_at = {}
            self._context_recovery_requested_at = requested_at

        now = time.monotonic()
        with recovery_lock:
            last_requested = float(requested_at.get(raw_id, 0.0) or 0.0)
            if raw_id in pending or (
                last_requested
                and now - last_requested < self.CONTEXT_RECOVERY_COOLDOWN_SEC
            ):
                return False

            requested_at[raw_id] = now
            pending.add(raw_id)
            if len(requested_at) > self.CONTEXT_RECOVERY_CACHE_LIMIT:
                oldest_id = min(requested_at, key=requested_at.get)
                requested_at.pop(oldest_id, None)

        executor = getattr(self, "_maintenance_executor", None)
        if executor is None:
            with recovery_lock:
                pending.discard(raw_id)
            return False
        try:
            executor.submit(self._run_context_recovery, raw_id)
            return True
        except RuntimeError as exc:
            with recovery_lock:
                pending.discard(raw_id)
            logger.debug(f"[TabPool] 上下文恢复任务提交失败 (target={raw_id}): {exc}")
            return False
    def _list_current_tab_ids_via_cdp(self) -> List[str]:
        return [
            item[0]
            for item in (self._list_current_tab_targets_via_cdp() or [])
            if item and item[0]
        ]
    def _list_current_tab_targets_via_cdp(self) -> Optional[List[tuple[str, Dict[str, Any]]]]:
        browser = self._get_browser_handle()
        if browser is None or not hasattr(browser, "_run_cdp"):
            return None

        try:
            result = browser._run_cdp("Target.getTargets")
            if not isinstance(result, dict) or not isinstance(result.get("targetInfos"), list):
                logger.debug("[TabPool] Target.getTargets returned an incomplete snapshot")
                return None
            target_infos = result["targetInfos"]
        except Exception as e:
            logger.debug(f"[TabPool] Target.getTargets 失败: {e}")
            return None

        targets: List[tuple[str, Dict[str, Any]]] = []
        for info in target_infos:
            if not isinstance(info, dict):
                continue
            if str(info.get("type") or "").strip().lower() != "page":
                continue
            target_id = str(info.get("targetId") or "").strip()
            if target_id:
                targets.append((target_id, info))
        return targets
    def _log_get_tabs_warning(self, message: str) -> None:
        now = time.time()
        if now - self._last_get_tabs_warning_at < self.GET_TABS_WARNING_INTERVAL_SEC:
            return
        self._last_get_tabs_warning_at = now
        logger.warning(message)
    def _list_current_tab_refs(self) -> List[Any]:
        browser = self._get_browser_handle()
        if browser is None:
            raise RuntimeError("browser_handle_unavailable")

        cdp_targets = self._list_current_tab_targets_via_cdp()
        if cdp_targets is not None:
            self._get_tabs_retry_after = 0.0
            return cdp_targets

        now = time.time()
        if now < self._get_tabs_retry_after:
            raise RuntimeError("tab_snapshot_temporarily_unavailable")

        try:
            tabs = browser.get_tabs()
            if tabs is None:
                raise RuntimeError("browser.get_tabs returned no snapshot")
            self._get_tabs_retry_after = 0.0
            return list(tabs)
        except Exception as e:
            self._get_tabs_retry_after = max(
                self._get_tabs_retry_after,
                time.time() + self.GET_TABS_FAILURE_COOLDOWN_SEC,
            )
            cdp_targets = self._list_current_tab_targets_via_cdp()
            if cdp_targets is not None:
                message = f"[TabPool] browser.get_tabs() 失败，回退到 Target.getTargets 扫描: {e}"
                if _looks_like_transient_local_debug_error(e):
                    logger.debug(message)
                else:
                    self._log_get_tabs_warning(message)
                return cdp_targets
            fallback_ids = list(getattr(browser, "tab_ids", []) or [])
            if fallback_ids:
                message = f"[TabPool] browser.get_tabs() 失败，回退到 tab_ids 扫描: {e}"
                if _looks_like_transient_local_debug_error(e):
                    logger.debug(message)
                else:
                    self._log_get_tabs_warning(message)
                return fallback_ids
            raise
