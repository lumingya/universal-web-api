"""
app/core/tab_pool.py - 标签页池管理器 (v1.05)

修复：
- 添加卡死检测和自动释放
- 初始化时重置状态
- 不自动创建空白标签页
- 🆕 动态扫描新标签页（基于时间间隔）
"""

import asyncio
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import Enum
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from app.core.config import logger, BrowserConstants
from app.utils.site_url import (
    extract_remote_site_domain,
    get_preferred_route_domain,
    is_remote_site_url,
    normalize_route_domain,
    route_domain_matches,
)


_POOL_SKIP_URL_PREFIXES = (
    "about:",
    "chrome://",
    "chrome-devtools://",
    "devtools://",
    "edge://",
    "brave://",
    "javascript:",
    "data:",
    "blob:",
)

_POOL_SKIP_URL_CONTAINS = (
    "chrome-error://",
    "about:neterror",
)


def _looks_like_transient_local_debug_error(error: Any) -> bool:
    text = str(error or "").strip().lower()
    if not text:
        return False
    if "winerror 10048" in text:
        return True
    if "max retries exceeded" in text and "127.0.0.1" in text and "/json" in text:
        return True
    if "failed to establish a new connection" in text and "127.0.0.1" in text:
        return True
    return False


def _should_skip_pool_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return True

    lowered = raw.lower()
    if lowered.startswith(_POOL_SKIP_URL_PREFIXES):
        return True
    if any(marker in lowered for marker in _POOL_SKIP_URL_CONTAINS):
        return True

    return not is_remote_site_url(lowered)


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
    
    def is_healthy(self) -> bool:
        """检查标签页是否健康（增强版 + 无效协议过滤）"""
        if self.status == TabStatus.CLOSED:
            return False
    
        try:
            url = self.tab.url
            return not _should_skip_pool_url(url)
        
        except Exception:
            return False

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
            self.last_used_at = time.time()
            logger.debug(
                f"[{self.id}] 会话占用: mode=command, idx=#{self.persistent_index or '-'}, "
                f"prev_status={prev_status}, prev_task={prev_task or '-'}, "
                f"new_task={str(task_id or '').strip() or '-'}, "
                f"req_count={self.request_count}"
            )
            return True
    
    def release(
        self,
        clear_page: bool = False,
        check_triggers: bool = True,
        rollback_request_count: bool = False
    ):
        with self._lock:
            prev_status = self.status.value
            prev_task = str(self.current_task_id or "").strip()
            prev_bound_request_id = str(getattr(self, "_bound_request_id", "") or "").strip()
            prev_command_request_id = str(getattr(self, "_command_request_id", "") or "").strip()
            prev_request_count = self.request_count
            if rollback_request_count and self.request_count > 0:
                self.request_count -= 1
            self.status = TabStatus.IDLE
            self.current_task_id = None
            setattr(self, "_bound_request_id", None)
            setattr(self, "_command_request_id", None)
            setattr(self, "_command_vars", {})
            self.last_used_at = time.time()
            new_request_count = self.request_count
            
            if clear_page:
                try:
                    self.tab.get("about:blank")
                    self.current_domain = None
                except Exception as e:
                    logger.debug(f"clear page failed: {e}")

        logger.debug(
            f"[{self.id}] 会话释放: idx=#{self.persistent_index or '-'}, "
            f"prev_status={prev_status}, prev_task={prev_task or '-'}, "
            f"prev_bound_req={prev_bound_request_id or '-'}, "
            f"prev_cmd_req={prev_command_request_id or '-'}, "
            f"clear_page={clear_page}, check_triggers={check_triggers}, "
            f"rollback_request_count={rollback_request_count}, "
            f"req_count={prev_request_count}->{new_request_count}"
        )
        
        # Trigger command checks outside the lock to avoid blocking
        try:
            from app.services.command_engine import command_engine
            if check_triggers:
                command_engine.check_triggers(self)
        except Exception as e:
            logger.debug(f"命令触发检查异常: {e}")
    
    def force_release(self, clear_page: bool = False, check_triggers: bool = False):
        """Force release tab lock and optionally refresh current page."""
        with self._lock:
            prev_status = self.status.value
            prev_task = str(self.current_task_id or "").strip()
            prev_bound_request_id = str(getattr(self, "_bound_request_id", "") or "").strip()
            prev_command_request_id = str(getattr(self, "_command_request_id", "") or "").strip()
            prev_request_count = self.request_count
            self.status = TabStatus.IDLE
            self.current_task_id = None
            setattr(self, "_bound_request_id", None)
            setattr(self, "_command_request_id", None)
            setattr(self, "_command_vars", {})
            self.last_used_at = time.time()

        logger.warning(
            f"[{self.id}] 强制释放开始: idx=#{self.persistent_index or '-'}, "
            f"prev_status={prev_status}, prev_task={prev_task or '-'}, "
            f"prev_bound_req={prev_bound_request_id or '-'}, "
            f"prev_cmd_req={prev_command_request_id or '-'}, "
            f"req_count={prev_request_count}, clear_page={clear_page}, "
            f"check_triggers={check_triggers}"
        )

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
            except Exception as e:
                logger.warning(f"[{self.id}] force_release refresh failed: {e}")
                reset_success = False

        with self._lock:
            if reset_success:
                self.status = TabStatus.IDLE
                logger.info(
                    f"[{self.id}] force_release done "
                    f"(idx=#{self.persistent_index or '-'}, final_status={self.status.value}, "
                    f"task={str(self.current_task_id or '').strip() or '-'}, req_count={self.request_count})"
                )
            else:
                self.status = TabStatus.ERROR
                self.error_count += 1
                logger.warning(
                    f"[{self.id}] force_release failed, set ERROR "
                    f"(idx=#{self.persistent_index or '-'}, req_count={self.request_count}, "
                    f"error_count={self.error_count})"
                )

        if check_triggers:
            try:
                from app.services.command_engine import command_engine
                command_engine.check_triggers(self)
            except Exception as e:
                logger.debug(f"command trigger check failed: {e}")

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
            self.error_count += 1
            logger.warning(f"[{self.id}] 标记为错误: {reason}")
    
    def get_info(self) -> Dict:
        busy_duration = None
        if self.status == TabStatus.BUSY:
            busy_duration = round(time.time() - self.last_used_at, 1)

        current_url = self._safe_get_url()
        current_domain = self._refresh_current_domain(current_url)
        
        return {
            "id": self.id,
            "persistent_index": self.persistent_index,
            "status": self.status.value,
            "current_task": self.current_task_id,
            "current_domain": current_domain,
            "route_domain": get_preferred_route_domain(current_domain),
            "domain_url": self._build_domain_url(current_url, current_domain),
            "url": current_url,
            "request_count": self.request_count,
            "busy_duration": busy_duration,
            "preset_name": self.preset_name,  # 🆕
            "is_isolated_context": self.is_isolated_context,
            "browser_context_id": self.browser_context_id,
        }
    
    def _refresh_current_domain(self, url: str = "") -> str:
        current_url = str(url or "").strip()
        try:
            resolved = extract_remote_site_domain(current_url) or ""
        except Exception:
            resolved = ""

        if resolved:
            self.current_domain = resolved
            return resolved

        fallback = str(self.current_domain or "").strip()
        if _should_skip_pool_url(current_url) or "://" in current_url:
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

    def _safe_get_url(self) -> str:
        try:
            return self.tab.url or ""
        except:
            return ""


@dataclass
class _GlobalNetworkWorker:
    """单个标签页的全局网络监听工作线程。"""
    session_id: str
    thread: threading.Thread
    stop_event: threading.Event


class _GlobalNetworkInterceptionManager:
    """
    全局常驻网络事件监听（仅负责把事件上报给 CommandEngine）。

    设计要点：
    - 仅在标签页空闲时运行；
    - 标签页被任务占用时暂停，让位给工作流内监听器；
    - 事件命中逻辑仍由 CommandEngine 决定。
    """

    def __init__(
        self,
        get_session_fn,
        is_shutdown_fn,
        listen_pattern: str = "http",
        wait_timeout: float = 0.5,
        retry_delay: float = 1.0,
    ):
        self._get_session = get_session_fn
        self._is_shutdown = is_shutdown_fn
        self._listen_pattern = str(listen_pattern or "http").strip() or "http"
        self._wait_timeout = max(0.1, float(wait_timeout or 0.5))
        self._retry_delay = max(0.2, float(retry_delay or 1.0))
        self._workers: Dict[str, _GlobalNetworkWorker] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _extract_event(response: Any) -> Dict[str, Any]:
        req = getattr(response, "request", None)
        resp = getattr(response, "response", None)

        url = (
            getattr(req, "url", None)
            or getattr(resp, "url", None)
            or getattr(response, "url", None)
            or ""
        )
        method = (
            getattr(req, "method", None)
            or getattr(response, "method", None)
            or ""
        )
        status = (
            getattr(resp, "status", None)
            or getattr(resp, "status_code", None)
            or getattr(response, "status", None)
            or 0
        )

        try:
            status = int(status)
        except Exception:
            status = 0

        return {
            "url": str(url or ""),
            "method": str(method or "").upper(),
            "status": status,
            "timestamp": time.time(),
        }

    @staticmethod
    def _safe_stop_listen(tab: Any):
        try:
            if hasattr(tab, "listen") and getattr(tab.listen, "listening", False):
                tab.listen.stop()
        except Exception:
            pass

    def _should_cleanup_worker_listener(
        self,
        session_id: str,
        stop_event: threading.Event,
    ) -> bool:
        with self._lock:
            current = self._workers.get(session_id)
            return current is None or current.stop_event is stop_event

    def _dispatch_event(self, session: TabSession, event: Dict[str, Any]):
        try:
            from app.services.command_engine import command_engine
            command_engine.handle_network_event(session, event)
        except Exception as e:
            logger.debug(f"[GlobalNet] 事件上报失败（忽略）: {e}")

    def start_for_session(self, session: TabSession):
        if not session:
            return
        with self._lock:
            existing = self._workers.get(session.id)
            if existing and existing.thread.is_alive():
                return

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._worker_loop,
                args=(session.id, stop_event),
                daemon=True,
                name=f"global-net-{session.id}",
            )
            self._workers[session.id] = _GlobalNetworkWorker(
                session_id=session.id,
                thread=thread,
                stop_event=stop_event,
            )
            thread.start()
            logger.debug(f"[GlobalNet] 启动监听: {session.id} pattern={self._listen_pattern!r}")

    def stop_for_session(self, session_id: str, reason: str = ""):
        if not session_id:
            return

        worker = None
        with self._lock:
            worker = self._workers.pop(session_id, None)
        if not worker:
            return

        worker.stop_event.set()

        session = self._get_session(session_id)
        if session is not None:
            self._safe_stop_listen(session.tab)

        if reason:
            logger.debug(f"[GlobalNet] 停止监听: {session_id} ({reason})")
        else:
            logger.debug(f"[GlobalNet] 停止监听: {session_id}")

    def shutdown(self):
        with self._lock:
            session_ids = list(self._workers.keys())
        for session_id in session_ids:
            self.stop_for_session(session_id, reason="shutdown")
        logger.info("[GlobalNet] 全局网络监听已关闭")

    def _worker_loop(self, session_id: str, stop_event: threading.Event):
        tab = None
        listening = False

        try:
            while not stop_event.is_set():
                if self._is_shutdown():
                    break

                session = self._get_session(session_id)
                if session is None:
                    break

                tab = session.tab

                if not listening:
                    try:
                        # 复用连接，降低对 CDP session 的额外占用
                        tab.listen._reuse_driver = True
                        tab.listen.start(self._listen_pattern)
                        listening = True
                    except Exception as e:
                        logger.debug(f"[GlobalNet] 启动监听失败: {session_id}, err={e}")
                        time.sleep(self._retry_delay)
                        continue

                try:
                    response = tab.listen.wait(timeout=self._wait_timeout)
                except Exception as e:
                    if stop_event.is_set() or self._is_shutdown():
                        break
                    err_text = str(e)
                    if "NoneType" in err_text and "is_running" in err_text:
                        logger.debug(f"[GlobalNet] 监听状态失效，准备重启: {session_id}")
                    else:
                        logger.debug(f"[GlobalNet] wait 异常: {session_id}, err={e}")
                    listening = False
                    self._safe_stop_listen(tab)
                    time.sleep(self._retry_delay)
                    continue

                if response is None or response is False:
                    continue

                event = self._extract_event(response)
                self._dispatch_event(session, event)

        finally:
            if tab is not None and self._should_cleanup_worker_listener(session_id, stop_event):
                self._safe_stop_listen(tab)


class TabPoolManager:
    """标签页池管理器"""
    
    DOMAIN_ABBR_MAP = {
        "chatgpt": "gpt",
        "openai": "gpt",
        "gemini": "gemini", 
        "aistudio": "aistudio",
        "claude": "claude",
        "anthropic": "claude",
        "poe": "poe",
        "bing": "bing",
        "copilot": "copilot",
        "perplexity": "pplx",
        "lmarena": "lmarena",
        "chat": "chat",
    }
    
    # 卡死超时时间（秒）
    STUCK_TIMEOUT = 180
    
    # 新标签页扫描间隔（秒）
    SCAN_INTERVAL = 10
    QUERY_SCAN_MIN_INTERVAL_SEC = 1.0
    ISOLATED_CONTEXT_ORPHAN_GRACE_SEC = 3.0
    ISOLATED_CONTEXT_REBIND_GRACE_SEC = 20.0
    GET_TABS_FAILURE_COOLDOWN_SEC = 5.0
    GET_TABS_WARNING_INTERVAL_SEC = 10.0

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _normalize_allocation_mode(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized == "round_robin":
            return "round_robin"
        return "first_idle"
    
    def __init__(
        self,
        browser_page,
        max_tabs: int = 5,
        min_tabs: int = 1,
        idle_timeout: float = 300,
        acquire_timeout: float = 60,
        stuck_timeout: float = STUCK_TIMEOUT,
        allocation_mode: str = "first_idle",
    ):
        self.page = browser_page
        self.max_tabs = max_tabs
        self.min_tabs = min_tabs
        self.idle_timeout = idle_timeout
        self.acquire_timeout = acquire_timeout
        self.stuck_timeout = max(1.0, float(stuck_timeout))
        self.allocation_mode = self._normalize_allocation_mode(allocation_mode)
        
        self._tabs: Dict[str, TabSession] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        
        self._initialized = False
        self._shutdown = False
        self._tab_counter = 0
        
        self._last_scan_time: float = 0
        self._get_tabs_retry_after: float = 0.0
        self._last_get_tabs_warning_at: float = 0.0
        
        # 记录已知的标签页底层 ID（用于检测新标签页）
        self._known_tab_ids: set = set()
        # 🆕 记录当前活动的标签页 ID（避免重复激活）
        self._active_session_id: Optional[str] = None
        self._auto_activate_on_acquire = self._to_bool(
            os.getenv("TAB_AUTO_ACTIVATE_ON_ACQUIRE"), False
        )
        self._acquire_waiters: deque[str] = deque()
        self._index_waiters: Dict[int, deque[str]] = {}
        self._route_waiters: Dict[str, deque[str]] = {}
        self._waiter_counter = 0
        
        # 🆕 持久化编号系统
        self._next_persistent_index: int = 1  # 下一个可分配的编号
        self._raw_id_to_persistent: Dict[str, int] = {}  # raw_tab_id → persistent_index
        self._persistent_to_session_id: Dict[int, str] = {}  # persistent_index → session.id
        self._isolated_context_by_raw_id: Dict[str, str] = {}
        self._orphaned_isolated_contexts: Dict[str, float] = {}
        self._round_robin_cursor: int = 0
        self._route_round_robin_cursor: Dict[str, int] = {}

        # 全局常驻网络监听（可配置）
        self._global_network_enabled = self._to_bool(
            BrowserConstants.get("GLOBAL_NETWORK_INTERCEPTION_ENABLED"), False
        )
        self._global_network_listen_pattern = str(
            BrowserConstants.get("GLOBAL_NETWORK_INTERCEPTION_LISTEN_PATTERN") or "http"
        ).strip() or "http"
        self._global_network_wait_timeout = max(
            0.1,
            self._to_float(BrowserConstants.get("GLOBAL_NETWORK_INTERCEPTION_WAIT_TIMEOUT"), 0.5),
        )
        self._global_network_retry_delay = max(
            0.2,
            self._to_float(BrowserConstants.get("GLOBAL_NETWORK_INTERCEPTION_RETRY_DELAY"), 1.0),
        )
        self._global_network_monitor: Optional[_GlobalNetworkInterceptionManager] = None
        if self._global_network_enabled:
            self._global_network_monitor = _GlobalNetworkInterceptionManager(
                get_session_fn=self._get_session_for_monitor,
                is_shutdown_fn=lambda: self._shutdown,
                listen_pattern=self._global_network_listen_pattern,
                wait_timeout=self._global_network_wait_timeout,
                retry_delay=self._global_network_retry_delay,
            )

        logger.debug(
            f"TabPoolManager 初始化 (max={max_tabs}, stuck_timeout={self.stuck_timeout}s, "
            f"allocation_mode={self.allocation_mode})"
        )

    def apply_runtime_config(
        self,
        *,
        max_tabs: Optional[int] = None,
        min_tabs: Optional[int] = None,
        idle_timeout: Optional[float] = None,
        acquire_timeout: Optional[float] = None,
        stuck_timeout: Optional[float] = None,
        allocation_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """同步更新运行中的标签页池参数。"""
        with self._lock:
            new_max_tabs = self.max_tabs if max_tabs is None else max(1, int(max_tabs))
            new_min_tabs = self.min_tabs if min_tabs is None else max(1, int(min_tabs))
            if new_min_tabs > new_max_tabs:
                new_min_tabs = new_max_tabs

            self.max_tabs = new_max_tabs
            self.min_tabs = new_min_tabs

            if idle_timeout is not None:
                self.idle_timeout = max(1.0, float(idle_timeout))
            if acquire_timeout is not None:
                self.acquire_timeout = max(1.0, float(acquire_timeout))
            if stuck_timeout is not None:
                self.stuck_timeout = max(1.0, float(stuck_timeout))
            if allocation_mode is not None:
                self.allocation_mode = self._normalize_allocation_mode(allocation_mode)

            updated = {
                "max_tabs": self.max_tabs,
                "min_tabs": self.min_tabs,
                "idle_timeout": self.idle_timeout,
                "acquire_timeout": self.acquire_timeout,
                "stuck_timeout": self.stuck_timeout,
                "allocation_mode": self.allocation_mode,
            }

            logger.info(
                "[TabPool] 运行时配置已更新: "
                f"max_tabs={self.max_tabs}, min_tabs={self.min_tabs}, "
                f"idle_timeout={self.idle_timeout}, acquire_timeout={self.acquire_timeout}, "
                f"stuck_timeout={self.stuck_timeout}, allocation_mode={self.allocation_mode}"
            )
            return updated

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
            logger.info(
                f"[TabPool] forgot orphaned isolated context after grace "
                f"(skip auto-dispose for safety): {context_id}"
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

    def _close_raw_tab(self, raw_tab_id: str) -> bool:
        if not raw_tab_id:
            return False

        try:
            browser = self._get_browser_handle()
            if browser is None or not hasattr(browser, "_run_cdp"):
                return False
            browser._run_cdp("Target.closeTarget", targetId=raw_tab_id)
            return True
        except Exception as e:
            logger.debug(f"[TabPool] close raw tab failed ({raw_tab_id}): {e}")
            return False

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

        session.tab = replacement_tab
        session.clear_transient_disconnect()
        try:
            session._refresh_current_domain(session._safe_get_url())
        except Exception:
            pass
        return True

    def _get_browser_handle(self):
        browser = getattr(self.page, "browser", None)
        if browser is not None:
            return browser
        return self.page

    def _list_current_tab_ids_via_cdp(self) -> List[str]:
        browser = self._get_browser_handle()
        if browser is None or not hasattr(browser, "_run_cdp"):
            return []

        try:
            result = browser._run_cdp("Target.getTargets") or {}
            target_infos = result.get("targetInfos") or []
        except Exception as e:
            logger.debug(f"[TabPool] Target.getTargets 失败: {e}")
            return []

        tab_ids: List[str] = []
        for info in target_infos:
            if not isinstance(info, dict):
                continue
            if str(info.get("type") or "").strip().lower() != "page":
                continue
            target_id = str(info.get("targetId") or "").strip()
            if target_id:
                tab_ids.append(target_id)
        return tab_ids

    def _log_get_tabs_warning(self, message: str) -> None:
        now = time.time()
        if now - self._last_get_tabs_warning_at < self.GET_TABS_WARNING_INTERVAL_SEC:
            return
        self._last_get_tabs_warning_at = now
        logger.warning(message)

    def _list_current_tab_refs(self) -> List[Any]:
        browser = self._get_browser_handle()
        if browser is None:
            return []

        cdp_tab_ids = self._list_current_tab_ids_via_cdp()
        if cdp_tab_ids:
            self._get_tabs_retry_after = 0.0
            return cdp_tab_ids

        now = time.time()
        if now < self._get_tabs_retry_after:
            return []

        try:
            tabs = browser.get_tabs()
            self._get_tabs_retry_after = 0.0
            return list(tabs or [])
        except Exception as e:
            self._get_tabs_retry_after = max(
                self._get_tabs_retry_after,
                time.time() + self.GET_TABS_FAILURE_COOLDOWN_SEC,
            )
            cdp_tab_ids = self._list_current_tab_ids_via_cdp()
            if cdp_tab_ids:
                message = f"[TabPool] browser.get_tabs() 失败，回退到 Target.getTargets 扫描: {e}"
                if _looks_like_transient_local_debug_error(e):
                    logger.debug(message)
                else:
                    self._log_get_tabs_warning(message)
                return cdp_tab_ids
            fallback_ids = list(getattr(browser, "tab_ids", []) or [])
            if fallback_ids:
                message = f"[TabPool] browser.get_tabs() 失败，回退到 tab_ids 扫描: {e}"
                if _looks_like_transient_local_debug_error(e):
                    logger.debug(message)
                else:
                    self._log_get_tabs_warning(message)
                return fallback_ids
            raise

    def _build_site_entry_url(self, domain: str) -> str:
        normalized_domain = str(domain or "").strip()
        if not normalized_domain:
            return ""

        for session in self._tabs.values():
            current_url = session._safe_get_url()
            actual_domain = session._refresh_current_domain(current_url)
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

        created = self._create_isolated_tab(final_url, background=False)
        if not created:
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "is_isolated_context": False,
                "url": final_url,
            }

        if not self._close_raw_tab(raw_tab_id):
            created_raw_id = created.get("raw_tab_id")
            self._close_raw_tab(created_raw_id)
            self._dispose_browser_context(created.get("browser_context_id"))
            self._isolated_context_by_raw_id.pop(created_raw_id, None)
            logger.warning(f"[TabPool] fallback to shared tab because original tab could not be closed: {raw_tab_id}")
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "is_isolated_context": False,
                "url": final_url,
            }

        logger.info(f"[TabPool] auto converted tab to isolated cookie context: {domain or final_url}")
        return {
            "tab": created["tab"],
            "raw_tab_id": created["raw_tab_id"],
            "browser_context_id": created.get("browser_context_id"),
            "is_isolated_context": True,
            "url": created.get("url") or final_url,
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

            session = self._wrap_tab(
                created["tab"],
                created["raw_tab_id"],
                browser_context_id=created.get("browser_context_id"),
                is_isolated_context=True,
            )
            self._tabs[session.id] = session
            self._start_global_monitor_for_session(session)
            self._last_scan_time = time.time()
            self._condition.notify_all()

            info = session.get_info()
            info["tab_route_prefix"] = f"/tab/{session.persistent_index}"
            route_domain = str(info.get("route_domain") or "").strip()
            info["domain_route_prefix"] = f"/url/{route_domain}" if route_domain else ""
            info["route_prefix"] = info["domain_route_prefix"] or info["tab_route_prefix"]

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

            session = self._wrap_tab(
                created["tab"],
                created["raw_tab_id"],
                browser_context_id=created.get("browser_context_id"),
                is_isolated_context=False,
            )
            self._tabs[session.id] = session
            self._start_global_monitor_for_session(session)
            self._last_scan_time = time.time()
            self._condition.notify_all()

            info = session.get_info()
            info["tab_route_prefix"] = f"/tab/{session.persistent_index}"
            route_domain = str(info.get("route_domain") or "").strip()
            info["domain_route_prefix"] = f"/url/{route_domain}" if route_domain else ""
            info["route_prefix"] = info["domain_route_prefix"] or info["tab_route_prefix"]

            return {
                "ok": True,
                "domain": target_domain,
                "message": f"已为 {target_domain} 打开共享 Cookie 受控窗口",
                "tab": info,
            }

    def _order_sessions_for_allocation(
        self,
        sessions: List[TabSession],
        *,
        route_domain: Optional[str] = None,
    ) -> List[TabSession]:
        ordered = sorted(
            list(sessions or []),
            key=lambda item: (item.persistent_index or 0, item.id),
        )
        if self.allocation_mode != "round_robin" or len(ordered) <= 1:
            return ordered

        cursor = (
            self._route_round_robin_cursor.get(route_domain, 0)
            if route_domain
            else self._round_robin_cursor
        )
        next_items = [item for item in ordered if (item.persistent_index or 0) > cursor]
        return next_items + [item for item in ordered if (item.persistent_index or 0) <= cursor]

    def _mark_allocation_cursor(self, session: TabSession, route_domain: Optional[str] = None) -> None:
        current_index = int(session.persistent_index or 0)
        if route_domain:
            self._route_round_robin_cursor[route_domain] = current_index
        else:
            self._round_robin_cursor = current_index

    def _get_session_for_monitor(self, session_id: str) -> Optional[TabSession]:
        with self._lock:
            return self._tabs.get(session_id)

    def _start_global_monitor_for_session(self, session: Optional[TabSession]):
        if not session or not self._global_network_monitor:
            return
        if self._shutdown:
            return
        # 仅在空闲标签页常驻监听，任务执行时让位
        if session.status != TabStatus.IDLE:
            return
        self._global_network_monitor.start_for_session(session)

    def _stop_global_monitor_for_session(self, session_id: str, reason: str = ""):
        if not self._global_network_monitor:
            return
        self._global_network_monitor.stop_for_session(session_id, reason=reason)

    def suspend_global_network_monitor(self, tab_id: str, reason: str = "manual"):
        with self._lock:
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

        tab_id = getattr(tab_ref, "tab_id", None)
        if tab_id:
            return str(tab_id)

        raw = str(tab_ref or "").strip()
        return raw or None

    def _resolve_tab_from_ref(self, tab_ref: Any) -> Optional[Any]:
        if tab_ref is None:
            return None

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
        except Exception:
            return None
    
    def _wrap_tab(
        self,
        tab,
        raw_tab_id: str = None,
        *,
        browser_context_id: Optional[str] = None,
        is_isolated_context: bool = False,
    ) -> TabSession:
        self._tab_counter += 1
        
        url = ""
        try:
            url = tab.url or ""
        except:
            pass
        
        abbr = self._get_domain_abbr(url)
        tab_id = f"{abbr}_{self._tab_counter}"
        
        session = TabSession(
            id=tab_id,
            tab=tab,
            browser_context_id=browser_context_id,
            is_isolated_context=bool(is_isolated_context),
        )
        
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
        
        return session
    
    def _should_scan(self) -> bool:
        """检查是否需要扫描新标签页"""
        return time.time() - self._last_scan_time >= self.SCAN_INTERVAL

    def _should_scan_for_query(self) -> bool:
        """读接口节流扫描，避免高频查询放大浏览器探测压力。"""
        if not self._tabs:
            return True
        return time.time() - self._last_scan_time >= self.QUERY_SCAN_MIN_INTERVAL_SEC
    
    def _scan_new_tabs(self):
        """扫描并添加新标签页（已持有锁）"""
        try:
            browser = self._get_browser_handle()
            current_tabs = self._list_current_tab_refs()
            current_tab_ids = [
                raw_id for raw_id in
                (self._get_tab_ref_id(tab_ref) for tab_ref in current_tabs)
                if raw_id
            ]
            current_tab_set = set(current_tab_ids)
            current_context_by_raw = {}
            for raw_id in current_tab_ids:
                try:
                    current_context_by_raw[raw_id] = self._get_browser_context_id(raw_id)
                except Exception as e:
                    logger.debug(f"[TabPool] skip context lookup for {raw_id}: {e}")
            self._cleanup_orphaned_isolated_contexts(list(current_context_by_raw.values()))
            changed = False

            session_raw_by_id: Dict[str, str] = {}
            for rid, pidx in self._raw_id_to_persistent.items():
                sid = self._persistent_to_session_id.get(pidx)
                if sid:
                    session_raw_by_id[sid] = rid

            for session_id, session in self._tabs.items():
                raw_id = session_raw_by_id.get(session_id)
                if not raw_id or raw_id not in current_tab_set:
                    continue
                if not session.is_isolated_context:
                    continue
                try:
                    _ = session.tab.url
                    session.clear_transient_disconnect()
                    continue
                except Exception:
                    pass
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
                
                if raw_id is not None and raw_id not in current_tab_set:
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
                    self._stop_global_monitor_for_session(session_id, reason="target_rebind")
                    p_idx = self._raw_id_to_persistent.pop(raw_id, None)
                    if p_idx is not None:
                        self._raw_id_to_persistent[replacement_raw_id] = p_idx
                    self._known_tab_ids.discard(raw_id)
                    self._known_tab_ids.add(replacement_raw_id)
                    self._isolated_context_by_raw_id.pop(raw_id, None)
                    self._register_isolated_context(replacement_raw_id, browser_context_id)
                    session.tab = replacement_tab
                    session.browser_context_id = browser_context_id or current_context_by_raw.get(replacement_raw_id)
                    session.clear_transient_disconnect()
                    try:
                        session._refresh_current_domain(session._safe_get_url())
                    except Exception:
                        pass
                    reserved_raw_ids.add(replacement_raw_id)
                    if session.status == TabStatus.IDLE:
                        self._start_global_monitor_for_session(session)
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
                    logger.warning(f"[{session_id}] 标签页已关闭但仍在忙碌，标记为错误")
                    session.mark_error("标签页已被关闭")
                else:
                    logger.info(f"[{session_id}] 标签页已关闭，从池中移除")
                    del self._tabs[session_id]
                self._stop_global_monitor_for_session(session_id, reason="tab_closed")
                
                # 清理映射
                self._known_tab_ids.discard(raw_id)
                p_idx = self._raw_id_to_persistent.pop(raw_id, None)
                if p_idx is not None:
                    self._persistent_to_session_id.pop(p_idx, None)
                browser_context_id = self._isolated_context_by_raw_id.pop(raw_id, None)
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
                
                try:
                    tab = self._resolve_tab_from_ref(tab_ref)
                    if not tab:
                        continue
                    
                    url = ""
                    try:
                        url = tab.url or ""
                    except Exception:
                        pass
                    
                    # 本地页、浏览器内部页、空白页都不纳入标签页池。
                    if _should_skip_pool_url(url):
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

                        tab = self._resolve_tab_from_ref(tab_ref)
                        if not tab:
                            continue
                        
                        url = ""
                        try:
                            url = tab.url or ""
                        except Exception:
                            pass
                        
                        # 初始化时直接跳过本地页和浏览器内部页。
                        if _should_skip_pool_url(url):
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
            
    def _check_stuck_tabs(self):
        """检查并释放卡死的标签页"""
        now = time.time()
        
        for session in self._tabs.values():
            if session.status == TabStatus.BUSY:
                busy_duration = now - session.last_used_at
                
                if busy_duration > self.stuck_timeout:
                    task_id = session.current_task_id or ""
                    cancelled = False
                    if task_id:
                        try:
                            from app.services.request_manager import request_manager
                            cancelled = bool(request_manager.cancel_request(task_id, "stuck_timeout"))
                        except Exception as e:
                            logger.debug(f"[{session.id}] stuck cancel failed (ignored): {e}")
                    logger.warning(
                        f"[{session.id}] stuck for {busy_duration:.0f}s, force release "
                        f"(task={task_id or '-'}, cancelled={cancelled})"
                    )
                    session.force_release(clear_page=False, check_triggers=False)

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

        task_id = str(getattr(session, "current_task_id", "") or "").strip()
        if not task_id:
            return False

        try:
            setattr(session, "_workflow_stop_reason", reason)
        except Exception:
            pass

        cancelled = False
        try:
            from app.services.request_manager import request_manager

            cancelled = bool(request_manager.cancel_request(task_id, reason))
        except Exception as e:
            logger.debug(f"[{session.id}] cancel on invalid session failed (ignored): {e}")

        logger.warning(
            f"[{session.id}] 会话失效，已请求取消任务 "
            f"(task={task_id}, reason={reason}, cancelled={cancelled}, detail={detail or '-'})"
        )
        return cancelled
    
    def _cleanup_unhealthy_tabs(self):
        """清理不健康的空闲标签页和错误状态的标签页"""
        to_remove = []
    
        for tab_id, session in self._tabs.items():
            if session.is_isolated_context and session.is_healthy():
                session.clear_transient_disconnect()

            # 清理 ERROR 状态的标签页（包括强制释放失败的）
            if session.status == TabStatus.ERROR:
                to_remove.append(tab_id)
            # 清理空闲但不健康的标签页
            elif session.status == TabStatus.IDLE and not session.is_healthy():
                if session.is_isolated_context:
                    if session.is_in_transient_disconnect():
                        continue
                    if self._arm_isolated_rebind_grace(
                        session,
                        reason="health_probe_failed",
                        detail="health probe failed",
                    ):
                        continue
                to_remove.append(tab_id)
    
        for tab_id in to_remove:
            session = self._tabs[tab_id]
            self._cancel_active_request_for_session(
                session,
                "tab_unhealthy",
                detail=f"status={getattr(getattr(session, 'status', None), 'value', 'unknown')}",
            )
            logger.warning(f"[{tab_id}] 不健康或错误状态，从池中移除")
            self._stop_global_monitor_for_session(tab_id, reason="unhealthy")
            
            # 清理映射表，允许相同 raw_tab_id 被重新扫描
            raw_ids_to_remove = [
                raw_id for raw_id, p_idx in self._raw_id_to_persistent.items()
                if self._persistent_to_session_id.get(p_idx) == tab_id
            ]
            for raw_id in raw_ids_to_remove:
                self._known_tab_ids.discard(raw_id)
                del self._raw_id_to_persistent[raw_id]
                browser_context_id = self._isolated_context_by_raw_id.pop(raw_id, None)
                if browser_context_id:
                    self._mark_orphaned_isolated_context(browser_context_id)
            
            # 清理持久编号映射
            p_idx = session.persistent_index
            if p_idx and self._persistent_to_session_id.get(p_idx) == tab_id:
                del self._persistent_to_session_id[p_idx]
            
            # 清理活动标签页记录
            if self._active_session_id == tab_id:
                self._active_session_id = None
            
            del self._tabs[tab_id]

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

        if owner_map is not None and owner_key is not None and not waiters:
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

    def acquire(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """获取一个可用的标签页（增强版）"""
        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout
        logged_waiting = False
        first_iteration = True

        with self._condition:
            while True:
                if self._shutdown:
                    return None
                
                # 首次进入或定期扫描新标签页
                if first_iteration or self._should_scan():
                    self._scan_new_tabs()
                    first_iteration = False
                
                # 检查卡死的标签页
                self._check_stuck_tabs()
                
                # 清理不健康的空闲标签页
                self._cleanup_unhealthy_tabs()
                
                # 寻找空闲且健康的标签页
                for session in self._tabs.values():
                    if session.status == TabStatus.IDLE:
                        # 检查健康状态
                        if not session.is_healthy():
                            logger.warning(f"[{session.id}] 标签页不健康，跳过")
                            continue
                        
                        if self._should_defer_to_command(session, task_id):
                            logger.debug(f"[{session.id}] defer acquire to high-priority command")
                            continue

                        if session.acquire(task_id):
                            # 忙碌前先暂停该标签页全局监听，避免和工作流监听冲突
                            self._stop_global_monitor_for_session(session.id, reason="acquire")
                            # 可选：自动激活标签页（默认关闭，避免抢占用户焦点）
                            if self._auto_activate_on_acquire and session.id != self._active_session_id:
                                session.activate()
                                self._active_session_id = session.id
                            
                            # task_id 已在上下文中，无需重复
                            if logged_waiting:
                                logger.debug(f"等待结束 → {session.id}")
                            else:
                                logger.debug(f"TabPool → {session.id}")
                            return session
                
                # 检查超时
                remaining = deadline - time.time()
                if remaining <= 0:
                    busy_info = [
                        f"{s.id}({s.current_task_id})" 
                        for s in self._tabs.values() 
                        if s.status == TabStatus.BUSY
                    ]
                    unhealthy_count = sum(
                        1 for s in self._tabs.values() 
                        if s.status == TabStatus.IDLE and not s.is_healthy()
                    )
                    logger.warning(
                        f"获取标签页超时 (忙碌: {', '.join(busy_info) or 'none'}, "
                        f"不健康: {unhealthy_count})"
                    )
                    return None
                
                # 等待
                if not logged_waiting:
                    busy_tabs = [s.id for s in self._tabs.values() if s.status == TabStatus.BUSY]
                    if busy_tabs:
                        logger.debug(f"排队等待 (忙碌: {', '.join(busy_tabs)})")
                    logged_waiting = True
                
                self._condition.wait(timeout=min(remaining, 1.0))
    
    async def acquire_async(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        return await asyncio.to_thread(self.acquire, task_id, timeout)
    
    def release(
        self,
        tab_id: str,
        clear_page: bool = False,
        check_triggers: bool = True,
        rollback_request_count: bool = False,
        expected_task_id: str = "",
    ):
        """释放标签页"""
        with self._condition:
            session = self._tabs.get(tab_id)
            if session:
                before_snapshot = self._describe_session(session)
                expected_task = str(expected_task_id or "").strip()
                current_task = str(getattr(session, "current_task_id", "") or "").strip()
                session_status = getattr(getattr(session, "status", None), "value", "")
                if expected_task:
                    if current_task and current_task != expected_task:
                        logger.warning(
                            f"[{tab_id}] 跳过释放：标签页已被其他任务接管 "
                            f"(expected_task={expected_task}, current_task={current_task}, "
                            f"snapshot={before_snapshot})"
                        )
                        return
                    if not current_task:
                        logger.warning(
                            f"[{tab_id}] 跳过释放：标签页 task_id 已丢失 "
                            f"(expected_task={expected_task}, status={session_status or 'unknown'}, "
                            f"snapshot={before_snapshot})"
                        )
                        return
                session.release(
                    clear_page=clear_page,
                    check_triggers=check_triggers,
                    rollback_request_count=rollback_request_count
                )
                self._start_global_monitor_for_session(session)
                self._condition.notify_all()
                logger.debug(
                    f"[{tab_id}] 已释放 "
                    f"(expected_task={expected_task or '-'}, clear_page={clear_page}, "
                    f"check_triggers={check_triggers}, rollback_request_count={rollback_request_count}, "
                    f"before={before_snapshot}, after={self._describe_session(session)})"
                )
    
    def force_release_all(self):
        """强制释放所有标签页（调试用）"""
        with self._condition:
            count = 0
            for session in self._tabs.values():
                if session.status == TabStatus.BUSY:
                    session.force_release(clear_page=False, check_triggers=False)
                    if session.status == TabStatus.IDLE:
                        self._start_global_monitor_for_session(session)
                    count += 1
            self._condition.notify_all()
            logger.info(f"强制释放 {count} 个标签页")
            return count
    
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
            self.release(session.id)

    def acquire_by_raw_tab_id(
        self,
        raw_tab_id: str,
        task_id: str,
        timeout: float = None,
        count_request: bool = True
    ) -> Optional[TabSession]:
        """
        根据底层浏览器标签页 ID 获取指定标签页会话。

        这用于外部已经明确知道目标标签页时，复用 TabPool 的运行时上下文，
        保持与正常工作流执行一致的监听、占用与释放行为。
        """
        raw_tab_id = str(raw_tab_id or "").strip()
        if not raw_tab_id:
            return None

        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout

        with self._condition:
            while True:
                if self._shutdown:
                    return None

                if self._should_scan():
                    self._scan_new_tabs()

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

                if not session.is_healthy():
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
                        self._stop_global_monitor_for_session(session.id, reason="acquire_by_raw_tab_id")
                        if self._auto_activate_on_acquire and session.id != self._active_session_id:
                            session.activate()
                            self._active_session_id = session.id
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
                self._condition.wait(timeout=min(remaining, 1.0))

    def acquire_by_index(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """
        根据持久化编号获取指定标签页
        
        Args:
            persistent_index: 持久化编号（1, 2, 3...）
            task_id: 任务 ID
            timeout: 超时时间
            
        Returns:
            TabSession 或 None（如果编号无效或标签页不可用）
        """
        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout
        
        with self._condition:
            while True:
                if self._shutdown:
                    return None
                
                # 定期扫描新标签页
                if self._should_scan():
                    self._scan_new_tabs()
                
                # 查找对应的 session
                session_id = self._persistent_to_session_id.get(persistent_index)
                if not session_id:
                    logger.warning(f"持久编号 #{persistent_index} 不存在")
                    return None
                
                session = self._tabs.get(session_id)
                if not session:
                    logger.warning(f"标签页 {session_id} (#{persistent_index}) 已被移除")
                    return None
                
                # 检查健康状态
                if not session.is_healthy():
                    logger.warning(f"[{session.id}] 标签页不健康")
                    return None
                
                # 尝试获取
                if self._should_defer_to_command(session, task_id):
                    logger.debug(f"[{session.id}] defer by index acquire to high-priority command")
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return None
                    self._condition.wait(timeout=min(remaining, 0.5))
                    continue

                if session.status == TabStatus.IDLE:
                    if session.acquire(task_id):
                        # 忙碌前先暂停该标签页全局监听，避免和工作流监听冲突
                        self._stop_global_monitor_for_session(session.id, reason="acquire_by_index")
                        # 可选：自动激活标签页（默认关闭，避免抢占用户焦点）
                        if self._auto_activate_on_acquire and session.id != self._active_session_id:
                            session.activate()
                            self._active_session_id = session.id
                        logger.debug(f"TabPool → {session.id} (#{persistent_index})")
                        return session
                
                # 标签页忙碌，等待
                remaining = deadline - time.time()
                if remaining <= 0:
                    logger.warning(f"获取标签页 #{persistent_index} 超时（当前状态: {session.status.value}）")
                    return None
                
                logger.debug_throttled(
                    f"tab_pool.wait_index.{persistent_index}",
                    f"等待标签页 #{persistent_index} 释放...",
                    interval_sec=5.0,
                )
                self._condition.wait(timeout=min(remaining, 1.0))
    
    async def acquire_by_index_async(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """异步版本的按编号获取"""
        return await asyncio.to_thread(self.acquire_by_index, persistent_index, task_id, timeout)

    def _get_sessions_for_route_domain(self, route_domain: str) -> List[TabSession]:
        target = normalize_route_domain(route_domain)
        if not target:
            return []

        matches: List[TabSession] = []
        for session in self._tabs.values():
            current_url = session._safe_get_url()
            actual_domain = session._refresh_current_domain(current_url)
            if route_domain_matches(target, actual_domain):
                matches.append(session)

        matches.sort(key=lambda item: item.persistent_index or 0)
        return matches

    def acquire_by_route_domain(self, route_domain: str, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """根据域名路由获取匹配的标签页。"""
        target = normalize_route_domain(route_domain)
        if not target:
            logger.warning("域名路由为空，无法获取标签页")
            return None

        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout

        with self._condition:
            while True:
                if self._shutdown:
                    return None

                if self._should_scan():
                    self._scan_new_tabs()

                self._check_stuck_tabs()
                self._cleanup_unhealthy_tabs()

                matching_sessions = self._get_sessions_for_route_domain(target)
                if not matching_sessions:
                    logger.warning(f"域名路由 '{target}' 没有匹配的标签页")
                    return None

                for session in matching_sessions:
                    if not session.is_healthy():
                        continue

                    if self._should_defer_to_command(session, task_id):
                        logger.debug(f"[{session.id}] defer by route-domain acquire to high-priority command")
                        continue

                    if session.status == TabStatus.IDLE and session.acquire(task_id):
                        self._stop_global_monitor_for_session(session.id, reason="acquire_by_route_domain")
                        if self._auto_activate_on_acquire and session.id != self._active_session_id:
                            session.activate()
                            self._active_session_id = session.id
                        logger.debug(
                            f"TabPool → {session.id} (route_domain={target}, idx=#{session.persistent_index})"
                        )
                        return session

                remaining = deadline - time.time()
                if remaining <= 0:
                    busy_info = [
                        f"{session.id}(#{session.persistent_index}:{session.status.value})"
                        for session in matching_sessions
                    ]
                    logger.warning(
                        f"获取域名路由 '{target}' 超时（匹配标签页: {', '.join(busy_info) or 'none'}）"
                    )
                    return None

                logger.debug_throttled(
                    f"tab_pool.wait_route.{target}",
                    f"等待域名路由 '{target}' 的标签页释放...",
                    interval_sec=5.0,
                )
                self._condition.wait(timeout=min(remaining, 1.0))

    async def acquire_by_route_domain_async(
        self,
        route_domain: str,
        task_id: str,
        timeout: float = None
    ) -> Optional[TabSession]:
        """异步版本的按域名路由获取。"""
        return await asyncio.to_thread(self.acquire_by_route_domain, route_domain, task_id, timeout)

    def acquire(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """Fair queued acquire for generic request routing."""
        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout
        logged_waiting = False
        first_iteration = True

        with self._condition:
            waiter_token = self._next_waiter_token(task_id)
            self._acquire_waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
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
                                if s.status == TabStatus.IDLE and not s.is_healthy()
                            )
                            logger.warning(
                                f"获取标签页超时 (忙碌: {', '.join(busy_info) or 'none'}, "
                                f"不健康: {unhealthy_count})"
                            )
                            return None

                        if not logged_waiting:
                            busy_tabs = [s.id for s in self._tabs.values() if s.status == TabStatus.BUSY]
                            ahead = self._count_waiters_ahead(self._acquire_waiters, waiter_token)
                            if busy_tabs:
                                logger.debug(f"排队等待 (忙碌: {', '.join(busy_tabs)})")
                            elif ahead > 0:
                                logger.debug(f"排队等待 (前面还有 {ahead} 个请求)")
                            logged_waiting = True

                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    for session in self._tabs.values():
                        if session.status != TabStatus.IDLE:
                            continue
                        if not session.is_healthy():
                            logger.warning(f"[{session.id}] 标签页不健康，跳过")
                            continue
                        if self._should_defer_to_command(session, task_id):
                            logger.debug(f"[{session.id}] defer acquire to high-priority command")
                            continue
                        if session.acquire(task_id):
                            self._stop_global_monitor_for_session(session.id, reason="acquire")
                            if self._auto_activate_on_acquire and session.id != self._active_session_id:
                                session.activate()
                                self._active_session_id = session.id

                            self._unregister_waiter(self._acquire_waiters, waiter_token)

                            if logged_waiting:
                                logger.debug(f"等待结束 → {session.id}")
                            else:
                                logger.debug(f"TabPool → {session.id}")
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
                            if s.status == TabStatus.IDLE and not s.is_healthy()
                        )
                        logger.warning(
                            f"获取标签页超时 (忙碌: {', '.join(busy_info) or 'none'}, "
                            f"不健康: {unhealthy_count})"
                        )
                        return None

                    if not logged_waiting:
                        busy_tabs = [s.id for s in self._tabs.values() if s.status == TabStatus.BUSY]
                        if busy_tabs:
                            logger.debug(f"排队等待 (忙碌: {', '.join(busy_tabs)})")
                        logged_waiting = True

                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(self._acquire_waiters, waiter_token)

    def acquire_by_index(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """Fair queued acquire for a fixed persistent tab index."""
        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout

        with self._condition:
            waiters = self._index_waiters.setdefault(persistent_index, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None

                    if self._should_scan():
                        self._scan_new_tabs()

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(f"获取标签页 #{persistent_index} 超时")
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_index.{persistent_index}",
                            f"等待标签页 #{persistent_index} 释放...",
                            interval_sec=5.0,
                        )
                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    session_id = self._persistent_to_session_id.get(persistent_index)
                    if not session_id:
                        logger.warning(f"持久编号 #{persistent_index} 不存在")
                        return None

                    session = self._tabs.get(session_id)
                    if not session:
                        logger.warning(f"标签页 {session_id} (#{persistent_index}) 已被移除")
                        return None

                    if not session.is_healthy():
                        logger.warning(f"[{session.id}] 标签页不健康")
                        return None

                    if self._should_defer_to_command(session, task_id):
                        logger.debug(f"[{session.id}] defer by index acquire to high-priority command")
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            return None
                        self._condition.wait(timeout=min(remaining, 0.5))
                        continue

                    if session.status == TabStatus.IDLE and session.acquire(task_id):
                        self._stop_global_monitor_for_session(session.id, reason="acquire_by_index")
                        if self._auto_activate_on_acquire and session.id != self._active_session_id:
                            session.activate()
                            self._active_session_id = session.id

                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._index_waiters,
                            owner_key=persistent_index,
                        )
                        logger.debug(f"TabPool → {session.id} (#{persistent_index})")
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        logger.warning(f"获取标签页 #{persistent_index} 超时（当前状态: {session.status.value}）")
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_index.{persistent_index}",
                        f"等待标签页 #{persistent_index} 释放...",
                        interval_sec=5.0,
                    )
                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._index_waiters,
                    owner_key=persistent_index,
                )

    def acquire_by_route_domain(self, route_domain: str, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """Fair queued acquire for tabs matching the same route domain."""
        target = normalize_route_domain(route_domain)
        if not target:
            logger.warning("鍩熷悕璺敱涓虹┖锛屾棤娉曡幏鍙栨爣绛鹃〉")
            return None

        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout

        with self._condition:
            waiters = self._route_waiters.setdefault(target, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None

                    if self._should_scan():
                        self._scan_new_tabs()

                    self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(f"鑾峰彇鍩熷悕璺敱 '{target}' 瓒呮椂")
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_route.{target}",
                            f"绛夊緟鍩熷悕璺敱 '{target}' 鐨勬爣绛鹃〉閲婃斁...",
                            interval_sec=5.0,
                        )
                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    matching_sessions = self._get_sessions_for_route_domain(target)
                    if not matching_sessions:
                        logger.warning(f"鍩熷悕璺敱 '{target}' 娌℃湁鍖归厤鐨勬爣绛鹃〉")
                        return None

                    for session in matching_sessions:
                        if not session.is_healthy():
                            continue
                        if self._should_defer_to_command(session, task_id):
                            logger.debug(f"[{session.id}] defer by route-domain acquire to high-priority command")
                            continue
                        if session.status == TabStatus.IDLE and session.acquire(task_id):
                            self._stop_global_monitor_for_session(session.id, reason="acquire_by_route_domain")
                            if self._auto_activate_on_acquire and session.id != self._active_session_id:
                                session.activate()
                                self._active_session_id = session.id

                            self._unregister_waiter(
                                waiters,
                                waiter_token,
                                owner_map=self._route_waiters,
                                owner_key=target,
                            )
                            logger.debug(
                                f"TabPool → {session.id} (route_domain={target}, idx=#{session.persistent_index})"
                            )
                            return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        busy_info = [
                            f"{session.id}(#{session.persistent_index}:{session.status.value})"
                            for session in matching_sessions
                        ]
                        logger.warning(
                            f"获取域名路由 '{target}' 超时（匹配标签页: {', '.join(busy_info) or 'none'}）"
                        )
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_route.{target}",
                        f"等待域名路由 '{target}' 的标签页释放...",
                        interval_sec=5.0,
                    )
                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._route_waiters,
                    owner_key=target,
                )

    def acquire(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """ASCII-safe fair acquire for generic request routing."""
        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout
        logged_waiting = False
        first_iteration = True

        with self._condition:
            waiter_token = self._next_waiter_token(task_id)
            self._acquire_waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
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
                                if s.status == TabStatus.IDLE and not s.is_healthy()
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

                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    for session in self._order_sessions_for_allocation(list(self._tabs.values())):
                        if session.status != TabStatus.IDLE:
                            continue
                        if not session.is_healthy():
                            logger.warning(f"[{session.id}] skip unhealthy tab")
                            continue
                        if self._should_defer_to_command(session, task_id):
                            logger.debug(f"[{session.id}] defer acquire to high-priority command")
                            continue
                        if session.acquire(task_id):
                            self._stop_global_monitor_for_session(session.id, reason="acquire")
                            if self._auto_activate_on_acquire and session.id != self._active_session_id:
                                session.activate()
                                self._active_session_id = session.id
                            self._mark_allocation_cursor(session)

                            self._unregister_waiter(self._acquire_waiters, waiter_token)

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
                            if s.status == TabStatus.IDLE and not s.is_healthy()
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

                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(self._acquire_waiters, waiter_token)

    def acquire_by_index(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """ASCII-safe fair acquire for a fixed persistent tab index."""
        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout

        with self._condition:
            waiters = self._index_waiters.setdefault(persistent_index, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None

                    if self._should_scan():
                        self._scan_new_tabs()

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(
                                f"Timed out waiting for tab #{persistent_index} (task={task_id})"
                            )
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_index.{persistent_index}",
                            f"Waiting for tab #{persistent_index} release...",
                            interval_sec=5.0,
                        )
                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    session_id = self._persistent_to_session_id.get(persistent_index)
                    if not session_id:
                        logger.warning(f"Persistent tab #{persistent_index} does not exist")
                        return None

                    session = self._tabs.get(session_id)
                    if not session:
                        logger.warning(f"Tab {session_id} (#{persistent_index}) was removed")
                        return None

                    if not session.is_healthy():
                        logger.warning(f"[{session.id}] tab is unhealthy")
                        return None

                    if self._should_defer_to_command(session, task_id):
                        logger.debug(f"[{session.id}] defer by index acquire to high-priority command")
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            return None
                        self._condition.wait(timeout=min(remaining, 0.5))
                        continue

                    if session.status == TabStatus.IDLE and session.acquire(task_id):
                        self._stop_global_monitor_for_session(session.id, reason="acquire_by_index")
                        if self._auto_activate_on_acquire and session.id != self._active_session_id:
                            session.activate()
                            self._active_session_id = session.id

                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._index_waiters,
                            owner_key=persistent_index,
                        )
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
                            f"snapshot={self._describe_session(session)})"
                        )
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_index.{persistent_index}",
                        f"Waiting for tab #{persistent_index} release...",
                        interval_sec=5.0,
                    )
                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._index_waiters,
                    owner_key=persistent_index,
                )

    def acquire_by_route_domain(self, route_domain: str, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """ASCII-safe fair acquire for tabs matching the same route domain."""
        target = normalize_route_domain(route_domain)
        if not target:
            logger.warning("Route domain is empty; cannot acquire a tab")
            return None

        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout

        with self._condition:
            waiters = self._route_waiters.setdefault(target, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
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
                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    matching_sessions = self._get_sessions_for_route_domain(target)
                    if not matching_sessions:
                        logger.warning(f"No tab matches route domain '{target}'")
                        return None

                    for session in self._order_sessions_for_allocation(
                        matching_sessions,
                        route_domain=target,
                    ):
                        if not session.is_healthy():
                            continue
                        if self._should_defer_to_command(session, task_id):
                            logger.debug(f"[{session.id}] defer by route-domain acquire to high-priority command")
                            continue
                        if session.status == TabStatus.IDLE and session.acquire(task_id):
                            self._stop_global_monitor_for_session(session.id, reason="acquire_by_route_domain")
                            if self._auto_activate_on_acquire and session.id != self._active_session_id:
                                session.activate()
                                self._active_session_id = session.id
                            self._mark_allocation_cursor(session, route_domain=target)

                            self._unregister_waiter(
                                waiters,
                                waiter_token,
                                owner_map=self._route_waiters,
                                owner_key=target,
                            )
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
                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._route_waiters,
                    owner_key=target,
                )

    def terminate_by_index(
        self,
        persistent_index: int,
        reason: str = "manual_terminate",
        clear_page: bool = True,
    ) -> Dict[str, Any]:
        """
        按标签页编号终止当前任务并释放占用。

        行为：
        1) 尝试取消该标签页 current_task 对应的请求；
        2) 若标签页忙碌，执行 force_release()；
        3) 若标签页空闲且 clear_page=True，重置到 about:blank；
        4) 成功空闲后恢复全局网络监听。
        """
        with self._condition:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}

            session = self._tabs.get(session_id)
            if not session:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}

            before_snapshot = self._describe_session(session)

            task_id = session.current_task_id or ""
            cancelled = False
            cancel_error = ""

            if task_id:
                try:
                    from app.services.request_manager import request_manager
                    cancelled = bool(request_manager.cancel_request(task_id, reason))
                except Exception as e:
                    cancel_error = str(e)
                    logger.debug(f"[{session.id}] 取消任务失败（忽略）: {e}")

            self._stop_global_monitor_for_session(session.id, reason=f"terminate:{reason}")

            was_busy = session.status == TabStatus.BUSY
            if was_busy:
                session.force_release(clear_page=clear_page, check_triggers=False)
            elif clear_page:
                session.force_release(clear_page=True, check_triggers=False)
            else:
                session.release(clear_page=False, check_triggers=False)

            # 尽量恢复可用状态的全局监听
            if session.status == TabStatus.IDLE:
                self._start_global_monitor_for_session(session)

            self._condition.notify_all()

            logger.warning(
                f"[{session.id}] 手动终止: idx=#{persistent_index}, "
                f"task={task_id or '-'}, cancelled={cancelled}, "
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
                "reason": reason,
            }
            if cancel_error:
                result["cancel_error"] = cancel_error
            return result
    
    def get_tabs_with_index(self) -> List[Dict]:
        """获取所有标签页及其持久编号（供 API 调用）"""
        with self._lock:
            if self._should_scan_for_query():
                self._scan_new_tabs()
            
            result = []
            for session in self._tabs.values():
                info = session.get_info()
                tab_route_prefix = f"/tab/{session.persistent_index}"
                route_domain = str(info.get("route_domain") or "").strip()
                domain_route_prefix = f"/url/{route_domain}" if route_domain else ""
                info["tab_route_prefix"] = tab_route_prefix
                info["domain_route_prefix"] = domain_route_prefix
                info["route_prefix"] = domain_route_prefix or tab_route_prefix
                result.append(info)
            
            # 按编号排序
            result.sort(key=lambda x: x.get("persistent_index", 0))
            return result

    # ================= 预设管理 =================
    
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
            session.preset_name = preset_name if preset_name else None
            
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

    # ================= 状态查询 =================

    def get_status(self) -> Dict:
        with self._lock:
            tabs_info = [s.get_info() for s in self._tabs.values()]
            
            return {
                "total": len(self._tabs),
                "idle": sum(1 for s in self._tabs.values() if s.status == TabStatus.IDLE),
                "busy": sum(1 for s in self._tabs.values() if s.status == TabStatus.BUSY),
                "max_tabs": self.max_tabs,
                "min_tabs": self.min_tabs,
                "idle_timeout": self.idle_timeout,
                "acquire_timeout": self.acquire_timeout,
                "stuck_timeout": self.stuck_timeout,
                "allocation_mode": self.allocation_mode,
                "global_network_enabled": self._global_network_enabled,
                "known_raw_tabs": len(self._known_tab_ids),
                "last_scan": round(time.time() - self._last_scan_time, 1),
                "tabs": tabs_info
            }

    def get_idle_sessions_snapshot(self) -> List[TabSession]:
        """Return a shallow snapshot of currently idle tab sessions."""
        with self._lock:
            return [s for s in self._tabs.values() if s.status == TabStatus.IDLE]

    def get_sessions_snapshot(self) -> List[TabSession]:
        """Return a shallow snapshot of all current tab sessions."""
        with self._lock:
            return list(self._tabs.values())
    
    def shutdown(self):
        with self._lock:
            self._shutdown = True
            if self._global_network_monitor:
                self._global_network_monitor.shutdown()
            for context_id in set(self._isolated_context_by_raw_id.values()):
                self._dispose_browser_context(context_id)
            self._tabs.clear()
            self._known_tab_ids.clear()
            self._active_session_id = None  # 🆕 重置活动标签页记录
            # 🆕 清理编号映射
            self._raw_id_to_persistent.clear()
            self._persistent_to_session_id.clear()
            self._isolated_context_by_raw_id.clear()
            self._orphaned_isolated_contexts.clear()
            self._round_robin_cursor = 0
            self._route_round_robin_cursor.clear()
            self._next_persistent_index = 1
            logger.info("TabPoolManager 已关闭")


# 剪贴板锁
_clipboard_lock = threading.Lock()

def get_clipboard_lock() -> threading.Lock:
    return _clipboard_lock


__all__ = [
    'TabStatus',
    'TabSession',
    'TabPoolManager',
    'get_clipboard_lock',
]
