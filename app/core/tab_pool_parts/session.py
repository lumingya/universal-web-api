import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from app.core.config import logger
from app.utils.site_url import (
    encode_tab_url_route_token,
    extract_remote_site_domain,
    get_preferred_route_domain,
)

from ._utils import _TAB_HEALTH_CACHE_TTL_SEC, _should_skip_pool_url


class TabStatus(Enum):
    """标签页状态"""
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    CLOSED = "closed"

@dataclass
class TabSession:
    """标签页会话"""
    id: str
    tab: Any
    status: TabStatus = TabStatus.IDLE
    current_task_id: Optional[str] = None
    current_domain: Optional[str] = None
    last_known_url: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    request_count: int = 0
    error_count: int = 0
    persistent_index: int = 0  # 🆕 持久化编号（重启前不变）
    preset_name: Optional[str] = None  # 🆕 当前显式指定的预设名称（None = 跟随站点默认预设）
    browser_context_id: Optional[str] = None
    is_isolated_context: bool = False
    transient_disconnect_until: float = 0.0
    transient_disconnect_reason: Optional[str] = None
    last_conversation_activity_at: float = 0.0
    last_conversation_domain: Optional[str] = None
    last_conversation_preset_name: Optional[str] = None
    _health_cache_until: float = field(default=0.0, repr=False)
    _health_cache_result: bool = field(default=False, repr=False)
    _health_cache_url: str = field(default="", repr=False)
    _health_cache_domain: str = field(default="", repr=False)
    _release_in_progress: bool = field(default=False, repr=False)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def is_available(self) -> bool:
        return self.status == TabStatus.IDLE

    def is_in_transient_disconnect(self) -> bool:
        return self.transient_disconnect_until > time.time()

    def mark_transient_disconnect(self, seconds: float, reason: str = None):
        with self._lock:
            self.transient_disconnect_until = time.time() + max(0.1, float(seconds))
            self.transient_disconnect_reason = str(reason or "").strip() or None

    def clear_transient_disconnect(self):
        with self._lock:
            self.transient_disconnect_until = 0.0
            self.transient_disconnect_reason = None

    def _clear_health_cache_unlocked(self):
        self._health_cache_until = 0.0
        self._health_cache_result = False
        self._health_cache_url = ""
        self._health_cache_domain = ""

    def is_healthy(self, *, allow_live_check: bool = True) -> bool:
        """检查标签页是否健康，避免在忙碌时额外触发 live CDP 读取。"""
        now = time.time()
        with self._lock:
            status = self.status
            cached_url = str(self.last_known_url or "").strip()
            cached_domain = str(self.current_domain or "").strip()
            cache_valid = (
                status == TabStatus.IDLE
                and self._health_cache_until > now
                and self._health_cache_url == cached_url
                and self._health_cache_domain == cached_domain
            )
            if cache_valid:
                return bool(self._health_cache_result)

        if status == TabStatus.CLOSED:
            return False

        if status == TabStatus.BUSY:
            if cached_url:
                return not _should_skip_pool_url(cached_url)
            return bool(cached_domain)

        if not allow_live_check:
            if cached_url:
                return not _should_skip_pool_url(cached_url)
            return bool(cached_domain)

        url = self._safe_get_url()
        result = bool(url and not _should_skip_pool_url(url))
        with self._lock:
            self._health_cache_result = result
            self._health_cache_url = str(self.last_known_url or "").strip()
            self._health_cache_domain = str(self.current_domain or "").strip()
            self._health_cache_until = time.time() + _TAB_HEALTH_CACHE_TTL_SEC
        return result

    def get_cached_route_snapshot(self) -> tuple[str, str]:
        """Return cached URL/domain without touching the live tab/CDP endpoint."""
        with self._lock:
            cached_url = str(self.last_known_url or "").strip()
            cached_domain = str(self.current_domain or "").strip()
        if cached_url:
            cached_domain = self._refresh_current_domain(cached_url)
        return cached_url, cached_domain

    def _debug_summary_unlocked(self) -> str:
        status_value = getattr(self.status, "value", str(self.status))
        return (
            f"idx=#{self.persistent_index or '-'}, "
            f"status={status_value}, "
            f"task={str(self.current_task_id or '').strip() or '-'}, "
            f"bound_req={str(getattr(self, '_bound_request_id', '') or '').strip() or '-'}, "
            f"cmd_req={str(getattr(self, '_command_request_id', '') or '').strip() or '-'}, "
            f"req_count={self.request_count}, "
            f"domain={str(self.current_domain or '').strip() or '-'}"
        )

    def debug_summary(self) -> str:
        with self._lock:
            return self._debug_summary_unlocked()

    def acquire(self, task_id: str) -> bool:
        with self._lock:
            if self.status != TabStatus.IDLE:
                return False

            prev_status = self.status.value
            prev_task = str(self.current_task_id or "").strip()
            prev_request_count = self.request_count
            self.status = TabStatus.BUSY
            self.current_task_id = task_id
            self._clear_health_cache_unlocked()
            setattr(self, "_last_cancel_request_task_id", None)
            setattr(self, "_last_cancel_request_reason", None)
            self.last_used_at = time.time()
            self.request_count += 1
            logger.debug(
                f"[{self.id}] 会话占用: mode=request, idx=#{self.persistent_index or '-'}, "
                f"prev_status={prev_status}, prev_task={prev_task or '-'}, "
                f"new_task={str(task_id or '').strip() or '-'}, "
                f"req_count={prev_request_count}->{self.request_count}"
            )
            return True

    def acquire_for_command(self, task_id: str) -> bool:
        """Acquire tab for command execution without incrementing request counter."""
        with self._lock:
            if self.status != TabStatus.IDLE:
                return False
            prev_status = self.status.value
            prev_task = str(self.current_task_id or "").strip()
            self.status = TabStatus.BUSY
            self.current_task_id = task_id
            self._clear_health_cache_unlocked()
            setattr(self, "_last_cancel_request_task_id", None)
            setattr(self, "_last_cancel_request_reason", None)
            self.last_used_at = time.time()
            logger.debug(
                f"[{self.id}] 会话占用: mode=command, idx=#{self.persistent_index or '-'}, "
                f"prev_status={prev_status}, prev_task={prev_task or '-'}, "
                f"new_task={str(task_id or '').strip() or '-'}, "
                f"req_count={self.request_count}"
            )
            return True

    def _begin_release_state(
        self,
        *,
        clear_page: bool,
        rollback_request_count: bool = False,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._release_in_progress:
                action = "force_release" if force else "release"
                logger.debug(f"[{self.id}] {action} skipped: another release is already in progress")
                return None

            prev_status_obj = self.status
            prev_status = prev_status_obj.value
            prev_task = str(self.current_task_id or "").strip()
            prev_bound_request_id = str(getattr(self, "_bound_request_id", "") or "").strip()
            prev_command_request_id = str(getattr(self, "_command_request_id", "") or "").strip()
            prev_request_count = self.request_count
            prev_was_error = prev_status_obj == TabStatus.ERROR
            prev_was_closed = prev_status_obj == TabStatus.CLOSED

            if rollback_request_count and self.request_count > 0 and (
                prev_task or prev_status_obj == TabStatus.BUSY
            ):
                self.request_count -= 1

            needs_cleanup_window = True
            if needs_cleanup_window:
                self._release_in_progress = True
                if not prev_was_error and not prev_was_closed:
                    self.status = TabStatus.BUSY
            elif not prev_was_error and not prev_was_closed:
                self.status = TabStatus.IDLE

            self.current_task_id = None
            self._clear_health_cache_unlocked()
            setattr(self, "_bound_request_id", None)
            setattr(self, "_command_request_id", None)
            setattr(self, "_command_vars", {})
            self.last_used_at = time.time()

            return {
                "prev_status": prev_status,
                "prev_task": prev_task,
                "prev_bound_request_id": prev_bound_request_id,
                "prev_command_request_id": prev_command_request_id,
                "prev_request_count": prev_request_count,
                "new_request_count": self.request_count,
                "prev_was_error": prev_was_error,
                "prev_was_closed": prev_was_closed,
                "needs_cleanup_window": needs_cleanup_window,
            }

    def _finish_release_state(self, state: Dict[str, Any], success: bool) -> None:
        if not state.get("needs_cleanup_window"):
            return

        with self._lock:
            if success:
                if (
                    self.status == TabStatus.BUSY
                    and not state.get("prev_was_error")
                    and not state.get("prev_was_closed")
                ):
                    self.status = TabStatus.IDLE
            elif self.status != TabStatus.CLOSED:
                self.status = TabStatus.ERROR
                self.error_count += 1
            self._release_in_progress = False

    def _run_release_from_state(
        self,
        state: Dict[str, Any],
        *,
        clear_page: bool,
        check_triggers: bool,
        rollback_request_count: bool,
    ) -> None:
        clear_page_success = True
        if clear_page:
            try:
                self.tab.get("about:blank")
                with self._lock:
                    self.current_domain = None
                    self.last_known_url = None
                    self._clear_health_cache_unlocked()
                    self.last_conversation_activity_at = 0.0
                    self.last_conversation_domain = None
                    self.last_conversation_preset_name = None
            except Exception as e:
                clear_page_success = False
                logger.debug(f"clear page failed: {e}")

        logger.debug(
            f"[{self.id}] 会话释放: idx=#{self.persistent_index or '-'}, "
            f"prev_status={state['prev_status']}, prev_task={state['prev_task'] or '-'}, "
            f"prev_bound_req={state['prev_bound_request_id'] or '-'}, "
            f"prev_cmd_req={state['prev_command_request_id'] or '-'}, "
            f"clear_page={clear_page}, check_triggers={check_triggers}, "
            f"rollback_request_count={rollback_request_count}, "
            f"req_count={state['prev_request_count']}->{state['new_request_count']}, "
            f"clear_page_success={clear_page_success}"
        )

        self.clear_visibility_emulation("release")
        self._finish_release_state(state, clear_page_success)

        try:
            from app.services.command_engine import command_engine
            if check_triggers:
                command_engine.check_triggers(self)
        except Exception as e:
            logger.debug(f"命令触发检查异常: {e}")

    def release(
        self,
        clear_page: bool = False,
        check_triggers: bool = True,
        rollback_request_count: bool = False
    ):
        state = self._begin_release_state(
            clear_page=clear_page,
            rollback_request_count=rollback_request_count,
            force=False,
        )
        if state is None:
            return
        self._run_release_from_state(
            state,
            clear_page=clear_page,
            check_triggers=check_triggers,
            rollback_request_count=rollback_request_count,
        )

    def _run_force_release_from_state(
        self,
        state: Dict[str, Any],
        *,
        clear_page: bool,
        check_triggers: bool,
    ) -> None:
        logger.warning(
            f"[{self.id}] 强制释放开始: idx=#{self.persistent_index or '-'}, "
            f"prev_status={state['prev_status']}, prev_task={state['prev_task'] or '-'}, "
            f"prev_bound_req={state['prev_bound_request_id'] or '-'}, "
            f"prev_cmd_req={state['prev_command_request_id'] or '-'}, "
            f"req_count={state['prev_request_count']}, clear_page={clear_page}, "
            f"check_triggers={check_triggers}"
        )

        self.clear_visibility_emulation("force_release")

        try:
            if hasattr(self.tab, "stop_loading"):
                self.tab.stop_loading()
            self.tab.run_js("if (window.stop) { window.stop(); }")
        except Exception:
            pass

        reset_success = True
        if clear_page:
            try:
                self.tab.refresh()
                self.reset_conversation_state()
            except Exception as e:
                logger.warning(f"[{self.id}] force_release refresh failed: {e}")
                reset_success = False

        self._finish_release_state(state, reset_success)

        with self._lock:
            final_status = self.status.value
            final_task = str(self.current_task_id or "").strip()
            request_count = self.request_count
            error_count = self.error_count

        if reset_success:
            logger.info(
                f"[{self.id}] force_release done "
                f"(idx=#{self.persistent_index or '-'}, final_status={final_status}, "
                f"task={final_task or '-'}, req_count={request_count})"
            )
        else:
            logger.warning(
                f"[{self.id}] force_release failed, set ERROR "
                f"(idx=#{self.persistent_index or '-'}, req_count={request_count}, "
                f"error_count={error_count})"
            )

        if check_triggers:
            try:
                from app.services.command_engine import command_engine
                command_engine.check_triggers(self)
            except Exception as e:
                logger.debug(f"command trigger check failed: {e}")

    def force_release(self, clear_page: bool = False, check_triggers: bool = False):
        """Force release tab lock and optionally refresh current page."""
        state = self._begin_release_state(clear_page=clear_page, force=True)
        if state is None:
            return
        self._run_force_release_from_state(
            state,
            clear_page=clear_page,
            check_triggers=check_triggers,
        )

    def activate(self) -> bool:
        """激活标签页（使其成为浏览器焦点）"""
        try:
            self.tab.set.activate()
            logger.debug(f"[{self.id}] 已激活")
            return True
        except Exception as e:
            logger.warning(f"[{self.id}] 激活失败: {e}")
            return False

    def mark_error(self, reason: str = None):
        with self._lock:
            self.status = TabStatus.ERROR
            self._clear_health_cache_unlocked()
            self.error_count += 1
            logger.warning(f"[{self.id}] 标记为错误: {reason}")

    def reset_conversation_state(self):
        with self._lock:
            self.last_conversation_activity_at = 0.0
            self.last_conversation_domain = None
            self.last_conversation_preset_name = None

    def mark_conversation_activity(self, domain: str = "", preset_name: str = ""):
        normalized_domain = str(domain or "").strip().lower() or None
        normalized_preset = str(preset_name or "").strip() or None
        now = time.time()
        with self._lock:
            self.last_conversation_activity_at = now
            self.last_conversation_domain = normalized_domain
            self.last_conversation_preset_name = normalized_preset
        logger.debug(
            f"[{self.id}] 记录会话活动: idx=#{self.persistent_index or '-'}, "
            f"domain={normalized_domain or '-'}, preset={normalized_preset or '-'}, "
            f"at={round(now, 3)}"
        )

    def get_conversation_status(
        self,
        current_domain: str = "",
        preset_name: str = "",
        threshold_seconds: float = 0.0,
        force_new: bool = False,
    ) -> Dict[str, Any]:
        normalized_domain = str(current_domain or self.current_domain or "").strip().lower()
        normalized_preset = str(preset_name or "").strip() or None
        threshold_value = max(0.0, float(threshold_seconds or 0.0))

        with self._lock:
            last_activity_at = float(self.last_conversation_activity_at or 0.0)
            last_domain = str(self.last_conversation_domain or "").strip().lower()
            last_preset = str(self.last_conversation_preset_name or "").strip() or None

        elapsed_seconds = None
        if last_activity_at > 0:
            elapsed_seconds = max(0.0, time.time() - last_activity_at)

        reason = "no_previous_conversation"
        will_start_new = True

        if force_new:
            reason = "force_new_enabled"
        elif threshold_value <= 0:
            reason = "threshold_disabled_reuse"
        elif not normalized_domain:
            reason = "missing_current_domain"
        elif not last_activity_at:
            reason = "no_previous_conversation"
        elif last_domain and normalized_domain != last_domain:
            reason = "domain_changed"
        elif normalized_preset and last_preset and normalized_preset != last_preset:
            reason = "preset_changed"
        elif elapsed_seconds is not None and elapsed_seconds < threshold_value:
            will_start_new = False
            reason = "reuse_existing"
        else:
            reason = "timeout_expired"

        return {
            "id": self.id,
            "persistent_index": self.persistent_index,
            "current_domain": normalized_domain,
            "current_preset_name": normalized_preset,
            "last_conversation_domain": last_domain or None,
            "last_conversation_preset_name": last_preset,
            "last_conversation_at": last_activity_at or None,
            "elapsed_seconds": round(elapsed_seconds, 1) if elapsed_seconds is not None else None,
            "threshold_seconds": threshold_value,
            "force_new_conversation": bool(force_new),
            "will_start_new_conversation": will_start_new,
            "reason": reason,
        }

    def should_start_new_conversation(
        self,
        current_domain: str = "",
        preset_name: str = "",
        threshold_seconds: float = 0.0,
        force_new: bool = False,
    ) -> bool:
        return bool(
            self.get_conversation_status(
                current_domain=current_domain,
                preset_name=preset_name,
                threshold_seconds=threshold_seconds,
                force_new=force_new,
            ).get("will_start_new_conversation", True)
        )

    def get_info(
        self,
        *,
        use_cached_url: bool = False,
        allow_live_when_busy: bool = False,
    ) -> Dict:
        with self._lock:
            status = self.status
            last_used_at = self.last_used_at
            current_task_id = self.current_task_id
            current_domain_snapshot = str(self.current_domain or "").strip()
            cached_url = str(self.last_known_url or "").strip()
            request_count = self.request_count
            preset_name = self.preset_name
            is_isolated_context = self.is_isolated_context
            browser_context_id = self.browser_context_id
            last_conversation_activity_at = self.last_conversation_activity_at
            last_conversation_domain = self.last_conversation_domain
            last_conversation_preset_name = self.last_conversation_preset_name
        busy_duration = None
        if status == TabStatus.BUSY:
            busy_duration = round(time.time() - last_used_at, 1)

        if use_cached_url:
            current_url = cached_url
            if current_url:
                current_domain = self._refresh_current_domain(current_url)
            else:
                current_domain = current_domain_snapshot
        else:
            current_url = self._safe_get_url(allow_live_when_busy=allow_live_when_busy)
            current_domain = self._refresh_current_domain(current_url)

        url_route_token = encode_tab_url_route_token(current_url)

        return {
            "id": self.id,
            "persistent_index": self.persistent_index,
            "status": status.value,
            "current_task": current_task_id,
            "current_domain": current_domain,
            "route_domain": get_preferred_route_domain(current_domain),
            "domain_url": self._build_domain_url(current_url, current_domain),
            "url": current_url,
            "url_route_token": url_route_token,
            "request_count": request_count,
            "busy_duration": busy_duration,
            "preset_name": preset_name,  # 🆕
            "is_isolated_context": is_isolated_context,
            "browser_context_id": browser_context_id,
            "last_conversation_at": last_conversation_activity_at or None,
            "last_conversation_domain": last_conversation_domain,
            "last_conversation_preset_name": last_conversation_preset_name,
        }

    def _refresh_current_domain(self, url: str = "") -> str:
        current_url = str(url or "").strip()
        try:
            resolved = extract_remote_site_domain(current_url) or ""
        except Exception:
            resolved = ""

        if resolved:
            with self._lock:
                self.current_domain = resolved
            return resolved

        with self._lock:
            fallback = str(self.current_domain or "").strip()
        if _should_skip_pool_url(current_url) or "://" in current_url:
            with self._lock:
                self.current_domain = None
            return ""
        return fallback

    @staticmethod
    def _build_domain_url(url: str, current_domain: str) -> str:
        source_url = str(url or "").strip()
        domain = str(current_domain or "").strip()
        if not source_url or not domain:
            return ""

        try:
            parsed = urlsplit(source_url)
        except Exception:
            return ""

        scheme = parsed.scheme if parsed.scheme in {"http", "https", "ws", "wss"} else "https"
        return f"{scheme}://{domain}/"

    def _remember_url(self, url: str) -> str:
        normalized = str(url or "").strip()
        with self._lock:
            if str(self.last_known_url or "").strip() != normalized:
                self._clear_health_cache_unlocked()
            self.last_known_url = normalized or None
        return normalized

    def _safe_get_url(self, allow_live_when_busy: bool = False) -> str:
        with self._lock:
            status = self.status
            cached_url = str(self.last_known_url or "").strip()

        if status == TabStatus.CLOSED:
            return ""
        if status == TabStatus.BUSY and not allow_live_when_busy:
            return cached_url

        try:
            current_url = self._remember_url(self.tab.url or "")
            if current_url:
                return current_url
        except Exception:
            pass
        return cached_url

    def clear_visibility_emulation(self, reason: str = "") -> None:
        try:
            from app.core.page_lifecycle import restore_visibility_emulation

            restore_visibility_emulation(self.tab, owner=self, reason=reason)
        except Exception as e:
            logger.debug(f"[{self.id}] visibility emulation cleanup failed: {e}")

