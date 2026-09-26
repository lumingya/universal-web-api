"""CommandEngine 的一部分（R2-3 拆分）：标签页交互：网络监听挂起/恢复、会话状态判断、焦点模拟、唤醒节流、页面检查脚本与观察者。

方法原样从 command_engine.py 迁出，行为不变；通过 mixin 组合回 CommandEngine，
类属性与实例状态都在 CommandEngine（command_engine.py）里定义。
"""

import copy
import json
import os
import time
from typing import Dict, List, Any, TYPE_CHECKING
from app.core.page_lifecycle import (
    BACKGROUND_WAKE_CDP_TIMEOUT,
    BACKGROUND_WAKE_JS_TIMEOUT,
    install_visibility_emulation,
    is_page_refresh_error,
    restore_visibility_emulation,
)
from app.services.command_engine_common import logger
from app.core.driver import driver_for_tab

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession  # noqa: F401


class CommandEnginePagesMixin:
    """标签页交互：网络监听挂起/恢复、会话状态判断、焦点模拟、唤醒节流、页面检查脚本与观察者"""

    def _suspend_tab_global_network(self, session: 'TabSession', reason: str = "command"):
        """命令执行期间暂停标签页全局网络监听，避免和工作流监听冲突。"""
        try:
            browser = self._get_browser()
            pool = getattr(browser, "_tab_pool", None)
            if pool is not None and hasattr(pool, "suspend_global_network_monitor"):
                pool.suspend_global_network_monitor(session.id, reason=reason)
        except Exception as e:
            logger.debug(f"[CMD] 暂停全局网络监听失败（忽略）: {e}")
    def _resume_tab_global_network(self, session: 'TabSession', reason: str = "command"):
        """命令执行结束后恢复标签页全局网络监听。"""
        try:
            browser = self._get_browser()
            pool = getattr(browser, "_tab_pool", None)
            if pool is not None and hasattr(pool, "resume_global_network_monitor"):
                pool.resume_global_network_monitor(session.id, reason=reason)
        except Exception as e:
            logger.debug(f"[CMD] 恢复全局网络监听失败（忽略）: {e}")
    @staticmethod
    def _session_status_value(session: 'TabSession') -> str:
        return str(getattr(getattr(session, "status", None), "value", "")).lower()
    def _is_session_closed(self, session: 'TabSession') -> bool:
        return self._session_status_value(session) == "closed"
    @staticmethod
    def _looks_like_page_disconnected_error(error: Any) -> bool:
        text = str(error or "").lower()
        return (
            "连接已断开" in text
            or "connection disconnected" in text
            or "target closed" in text
            or "session closed" in text
            or "websocket" in text and "closed" in text
        )
    @staticmethod
    def _looks_like_page_refresh_error(error: Any) -> bool:
        return is_page_refresh_error(error)
    def _mark_session_closed_if_disconnected(self, session: 'TabSession', error: Any, reason: str) -> bool:
        if not self._looks_like_page_disconnected_error(error):
            return False
        if getattr(session, "_cdp_recycle_in_progress", False):
            # P0-3：空闲 CDP 会话回收期间的短暂断开不是标签页关闭
            return True
        try:
            if hasattr(session, "mark_closed"):
                session.mark_closed(reason)
        except Exception:
            pass
        return True
    def _set_focus_emulation(self, session: 'TabSession', enabled: bool):
        """Best-effort focus emulation without stealing OS/browser foreground focus."""
        try:
            driver_for_tab(session.tab).run_cdp(
                "Emulation.setFocusEmulationEnabled",
                enabled=bool(enabled),
                _timeout=BACKGROUND_WAKE_CDP_TIMEOUT,
            )
            if enabled:
                install_visibility_emulation(
                    session.tab,
                    owner=session,
                    reason="command_focus_emulation",
                )
            else:
                restore_visibility_emulation(
                    session.tab,
                    owner=session,
                    reason="command_focus_emulation_end",
                )
        except Exception as e:
            logger.debug(f"[CMD] 焦点模拟设置失败（忽略）: enabled={enabled}, 错误={e}")
    def _should_wake_tab_now(self, session: 'TabSession') -> bool:
        """按会话限频唤醒动作：唤醒后的 CDP 状态是粘性的，不必每轮都重发。"""
        interval = float(self._wake_tab_min_interval_sec or 0.0)
        if interval <= 0.0:
            return True
        try:
            # 上一轮 page_check JS 已经出错，说明页面可能真的被冻结/丢弃了。
            # 这种情况下不限频，保持原来"每轮都唤醒"的行为，
            # 避免连续两轮失败把会话推进 30s 退避。
            if int(getattr(session, "_pc_js_failures", 0) or 0) > 0:
                setattr(session, "_pc_last_wake_at", time.time())
                return True
        except Exception:
            return True
        try:
            now = time.time()
            last_at = float(getattr(session, "_pc_last_wake_at", 0.0) or 0.0)
            if last_at > 0.0 and (now - last_at) < interval:
                return False
            setattr(session, "_pc_last_wake_at", now)
        except Exception:
            return True
        return True
    def _invalidate_wake_throttle(self, session: 'TabSession') -> None:
        """让下一次 page_check 立即重新唤醒（JS 失败/页面可能已被冻结时调用）。"""
        try:
            setattr(session, "_pc_last_wake_at", 0.0)
        except Exception:
            pass
    def _try_wake_tab(self, session: 'TabSession', reason: str = ""):
        """
        Best-effort wake-up for background/discard-prone tabs.
        Uses lifecycle and lightweight JS ping, without forcing browser focus.
        """
        if self._is_session_closed(session):
            return
        # P0-6：page_check / keepalive 之前先解除空闲冻结（与唤醒节流无关，必须执行）
        try:
            from app.core.tab_pool_parts.idle_maintenance import resume_if_frozen

            resume_if_frozen(session, reason=reason or "wake_tab")
        except Exception:
            pass
        if not self._wake_tab_before_page_check:
            return
        if self._is_page_check_backing_off(session):
            return
        if not self._should_wake_tab_now(session):
            return
        focus_emulation_set = False
        try:
            driver_for_tab(session.tab).run_cdp(
                "Emulation.setFocusEmulationEnabled",
                enabled=True,
                _timeout=BACKGROUND_WAKE_CDP_TIMEOUT,
            )
            focus_emulation_set = True
        except Exception as e:
            if self._mark_session_closed_if_disconnected(session, e, "wake_focus_emulation"):
                return
        try:
            install_visibility_emulation(session.tab, owner=session, reason=reason or "wake_tab")
        except Exception as e:
            if self._mark_session_closed_if_disconnected(session, e, "wake_visibility"):
                return
        try:
            driver_for_tab(session.tab).run_cdp(
                "Page.setWebLifecycleState",
                state="active",
                _timeout=BACKGROUND_WAKE_CDP_TIMEOUT,
            )
        except Exception as e:
            if self._mark_session_closed_if_disconnected(session, e, "wake_lifecycle"):
                return
        try:
            driver_for_tab(session.tab).run_js("return document.readyState || '';", timeout=BACKGROUND_WAKE_JS_TIMEOUT)
        except Exception as e:
            if self._mark_session_closed_if_disconnected(session, e, "wake_ready_state"):
                return
        finally:
            if focus_emulation_set:
                try:
                    driver_for_tab(session.tab).run_cdp(
                        "Emulation.setFocusEmulationEnabled",
                        enabled=False,
                        _timeout=BACKGROUND_WAKE_CDP_TIMEOUT,
                    )
                except Exception:
                    pass
    def _run_page_check_js(self, session: 'TabSession', script: str) -> Any:
        if self._is_session_closed(session):
            raise RuntimeError("page_check session is closed")
        return driver_for_tab(session.tab).run_js(script, timeout=self._page_check_js_timeout_sec)
    def _is_page_check_backing_off(self, session: 'TabSession') -> bool:
        until = float(getattr(session, "_pc_js_backoff_until", 0.0) or 0.0)
        return until > time.time()
    def _is_page_check_refreshing(self, session: 'TabSession') -> bool:
        until = float(getattr(session, "_pc_refresh_grace_until", 0.0) or 0.0)
        return until > time.time()
    def _mark_page_check_refreshing(self, session: 'TabSession') -> None:
        """Pause page checks briefly while navigation replaces the document."""
        self._invalidate_wake_throttle(session)
        sid = str(getattr(session, "id", "") or "")
        if sid:
            with self._lock:
                self._observer_keywords_by_session.pop(sid, None)
        try:
            setattr(
                session,
                "_pc_refresh_grace_until",
                time.time() + self._page_check_refresh_grace_sec,
            )
            setattr(session, "_pc_js_failures", 0)
            setattr(session, "_pc_js_backoff_until", 0.0)
            setattr(session, "_pc_snapshot_cached", None)
        except Exception:
            pass
    def _record_page_check_js_success(self, session: 'TabSession') -> None:
        try:
            setattr(session, "_pc_js_failures", 0)
            setattr(session, "_pc_js_backoff_until", 0.0)
            setattr(session, "_pc_refresh_grace_until", 0.0)
        except Exception:
            pass
    def _record_page_check_js_failure(self, session: 'TabSession', error: Any, context: str) -> None:
        if self._looks_like_page_refresh_error(error):
            self._mark_page_check_refreshing(session)
            return
        # JS 执行失败通常意味着页面被冻结/丢弃或已导航，
        # 清掉唤醒限频，让下一轮立刻重新唤醒，不受最小间隔限制。
        self._invalidate_wake_throttle(session)
        try:
            failures = int(getattr(session, "_pc_js_failures", 0) or 0) + 1
            setattr(session, "_pc_js_failures", failures)
            if failures >= 2:
                until = time.time() + self._page_check_failure_backoff_sec
                setattr(session, "_pc_js_backoff_until", until)
                logger.debug(
                    f"[CMD] 页面检查 JS 连续失败，暂停后台检查: "
                    f"标签页={getattr(session, 'id', '-')}, failures={failures}, "
                    f"backoff={self._page_check_failure_backoff_sec:.0f}s, context={context}, error={error}"
                )
        except Exception:
            pass
    def _collect_page_check_keywords(
        self,
        commands: List[Dict],
        session: 'TabSession',
    ) -> set:
        """Collect all page_check keywords that apply to this session."""
        keywords: set = set()
        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            trigger = cmd.get("trigger", {}) or {}
            if str(trigger.get("type", "")).strip().lower() != "page_check":
                continue
            if not bool(trigger.get("periodic_enabled", True)):
                continue
            if not self._matches_scope(cmd, session):
                continue
            value = str(trigger.get("value", "") or "").strip()
            if value:
                _, parts = self._parse_page_check_expression(value)
                for part in parts:
                    kw = part.lower().strip()
                    if kw:
                        keywords.add(kw)
        return keywords
    def _sync_session_bootstrap_js_files(
        self,
        commands: List[Dict],
        session: 'TabSession',
    ) -> None:
        """Install explicitly opted-in JS files when a managed tab becomes idle.

        `run_js_file` actions normally run only after their trigger fires. Some
        page-resilience scripts must also be registered before a later reload,
        so they opt in with ``bootstrap_on_session_ready``. The per-session
        mtime cache keeps this out of the hot periodic path while still
        replacing a registered script when its source changes.
        """
        state = getattr(session, "_command_bootstrap_js_files", None)
        if not isinstance(state, dict):
            state = {}
            setattr(session, "_command_bootstrap_js_files", state)

        for command in commands:
            if not isinstance(command, dict) or not command.get("enabled", True):
                continue
            if not self._matches_scope(command, session):
                continue

            command_id = str(command.get("id", "") or "").strip()
            for action in command.get("actions", []) or []:
                if not isinstance(action, dict):
                    continue
                if str(action.get("type", "")).strip().lower() != "run_js_file":
                    continue
                if not self._coerce_action_bool(action.get("bootstrap_on_session_ready"), False):
                    continue

                resolved_path = self._resolve_action_file_path(action.get("file_path", ""))
                if not resolved_path or not os.path.isfile(resolved_path):
                    logger.warning(
                        f"[CMD] 启动 JS 文件不存在: command={command_id or '-'}, path={resolved_path or action.get('file_path', '')!r}"
                    )
                    continue

                try:
                    source_mtime = os.path.getmtime(resolved_path)
                except OSError as error:
                    logger.warning(
                        f"[CMD] 读取启动 JS 文件时间失败: command={command_id or '-'}, path={resolved_path!r}, error={error}"
                    )
                    continue

                action_id = str(action.get("action_id", "") or "").strip()
                cache_key = (command_id, action_id, resolved_path.lower())
                if state.get(cache_key) == source_mtime:
                    continue

                try:
                    result = self._execute_action(copy.deepcopy(action), session)
                except Exception as error:
                    logger.warning(
                        f"[CMD] 启动 JS 文件同步失败: command={command_id or '-'}, path={resolved_path!r}, error={error}"
                    )
                    continue

                if self._is_action_soft_failure(result):
                    logger.warning(
                        f"[CMD] 启动 JS 文件同步未完成: command={command_id or '-'}, path={resolved_path!r}, result={result!r}"
                    )
                    continue

                state[cache_key] = source_mtime
                logger.debug(
                    f"[CMD] 已同步启动 JS 文件: command={command_id or '-'}, path={resolved_path!r}"
                )
    def _ensure_page_check_observer(
        self,
        session: 'TabSession',
        keywords: set,
    ):
        """Inject or update MutationObserver for real-time page_check text detection."""
        if not keywords:
            return
        if self._is_session_closed(session):
            return
        sid = str(getattr(session, "id", "") or "")
        if not sid:
            return
        if self._is_page_check_backing_off(session):
            return
        if self._is_page_check_refreshing(session):
            return
        setattr(session, "_pc_observer_empty_cleanup_done", False)
        # Skip if already installed with the same keywords AND observer is still alive
        with self._lock:
            installed = self._observer_keywords_by_session.get(sid)
        if installed == keywords:
            # Quick liveness check with throttling (outside lock — run_js is I/O).
            now = time.time()
            last_check = float(getattr(session, "_last_pc_observer_check_at", 0.0) or 0.0)
            if now - last_check < 5.0:
                return
            setattr(session, "_last_pc_observer_check_at", now)
            try:
                alive = self._run_page_check_js(session, "return !!window.__pcObserver")
                if alive:
                    self._record_page_check_js_success(session)
                    return
            except Exception as e:
                if self._is_session_closed(session):
                    return
                self._record_page_check_js_failure(session, e, "observer_alive")
                if self._mark_session_closed_if_disconnected(session, e, "page_check_observer_alive"):
                    return
                if self._is_page_check_refreshing(session):
                    return
                pass
            # Observer lost — clear cache, re-inject below
            with self._lock:
                if self._observer_keywords_by_session.get(sid) == installed:
                    self._observer_keywords_by_session.pop(sid, None)
        # Inject observer
        sorted_kws = sorted(keywords)
        js = self._PAGE_CHECK_OBSERVER_JS.replace(
            "%KEYWORDS%", json.dumps(sorted_kws)
        )
        try:
            result = self._run_page_check_js(session, js)
            self._record_page_check_js_success(session)
            with self._lock:
                self._observer_keywords_by_session[sid] = set(keywords)
            if result != "already_installed":
                logger.debug(
                    f"[CMD] 页面检查观察器已注入: "
                    f"标签页={sid}, 关键词={sorted_kws}"
                )
        except Exception as e:
            if self._is_session_closed(session):
                return
            self._record_page_check_js_failure(session, e, "observer_install")
            self._mark_session_closed_if_disconnected(session, e, "page_check_observer_install")
    def _clear_page_check_observer(self, session: 'TabSession') -> None:
        if self._is_session_closed(session):
            return
        sid = str(getattr(session, "id", "") or "")
        if not sid:
            return
        with self._lock:
            installed = self._observer_keywords_by_session.pop(sid, None)
        if installed is None and bool(getattr(session, "_pc_observer_empty_cleanup_done", False)):
            return
        if self._is_page_check_backing_off(session):
            return

        cleanup_js = """
        return (() => {
            if (window.__pcObserver && typeof window.__pcObserver.disconnect === 'function') {
                window.__pcObserver.disconnect();
            }
            window.__pcObserver = null;
            window.__pcKeywords = [];
            window.__pcHits = {};
            window.__pcSnapshot = '';
            return true;
        })();
        """
        try:
            self._run_page_check_js(session, cleanup_js)
            self._record_page_check_js_success(session)
            setattr(session, "_pc_observer_empty_cleanup_done", True)
        except Exception as e:
            if self._is_session_closed(session):
                return
            self._record_page_check_js_failure(session, e, "observer_cleanup")
            self._mark_session_closed_if_disconnected(session, e, "page_check_observer_cleanup")
            logger.debug(f"[CMD] 页面检查观察器注入失败: {e}")
