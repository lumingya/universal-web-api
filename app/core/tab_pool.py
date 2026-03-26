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
    
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    
    def is_available(self) -> bool:
        return self.status == TabStatus.IDLE
    
    def is_healthy(self) -> bool:
        """检查标签页是否健康（增强版 + 无效协议过滤）"""
        if self.status == TabStatus.CLOSED:
            return False
    
        try:
            url = self.tab.url
            return not _should_skip_pool_url(url)
        
        except Exception:
            return False
    
    def acquire(self, task_id: str) -> bool:
        with self._lock:
            if self.status != TabStatus.IDLE:
                return False
            
            self.status = TabStatus.BUSY
            self.current_task_id = task_id
            self.last_used_at = time.time()
            self.request_count += 1
            return True

    def acquire_for_command(self, task_id: str) -> bool:
        """Acquire tab for command execution without incrementing request counter."""
        with self._lock:
            if self.status != TabStatus.IDLE:
                return False
            self.status = TabStatus.BUSY
            self.current_task_id = task_id
            self.last_used_at = time.time()
            return True
    
    def release(
        self,
        clear_page: bool = False,
        check_triggers: bool = True,
        rollback_request_count: bool = False
    ):
        with self._lock:
            if rollback_request_count and self.request_count > 0:
                self.request_count -= 1
            self.status = TabStatus.IDLE
            self.current_task_id = None
            self.last_used_at = time.time()
            
            if clear_page:
                try:
                    self.tab.get("about:blank")
                    self.current_domain = None
                except Exception as e:
                    logger.debug(f"clear page failed: {e}")
        
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
            self.status = TabStatus.IDLE
            self.current_task_id = None
            self.last_used_at = time.time()

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
                logger.info(f"[{self.id}] force_release done")
            else:
                self.error_count += 1
                logger.warning(f"[{self.id}] force_release failed, set ERROR")

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
            if tab is not None:
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
    
    def __init__(
        self,
        browser_page,
        max_tabs: int = 5,
        min_tabs: int = 1,
        idle_timeout: float = 300,
        acquire_timeout: float = 60,
        stuck_timeout: float = STUCK_TIMEOUT,
    ):
        self.page = browser_page
        self.max_tabs = max_tabs
        self.min_tabs = min_tabs
        self.idle_timeout = idle_timeout
        self.acquire_timeout = acquire_timeout
        self.stuck_timeout = max(1.0, float(stuck_timeout))
        
        self._tabs: Dict[str, TabSession] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        
        self._initialized = False
        self._shutdown = False
        self._tab_counter = 0
        
        self._last_scan_time: float = 0
        
        # 记录已知的标签页底层 ID（用于检测新标签页）
        self._known_tab_ids: set = set()
        # 🆕 记录当前活动的标签页 ID（避免重复激活）
        self._active_session_id: Optional[str] = None
        self._auto_activate_on_acquire = self._to_bool(
            os.getenv("TAB_AUTO_ACTIVATE_ON_ACQUIRE"), False
        )
        
        # 🆕 持久化编号系统
        self._next_persistent_index: int = 1  # 下一个可分配的编号
        self._raw_id_to_persistent: Dict[str, int] = {}  # raw_tab_id → persistent_index
        self._persistent_to_session_id: Dict[int, str] = {}  # persistent_index → session.id

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
            f"TabPoolManager 初始化 (max={max_tabs}, stuck_timeout={self.stuck_timeout}s)"
        )

    def apply_runtime_config(
        self,
        *,
        max_tabs: Optional[int] = None,
        min_tabs: Optional[int] = None,
        idle_timeout: Optional[float] = None,
        acquire_timeout: Optional[float] = None,
        stuck_timeout: Optional[float] = None,
    ) -> Dict[str, float]:
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

            updated = {
                "max_tabs": self.max_tabs,
                "min_tabs": self.min_tabs,
                "idle_timeout": self.idle_timeout,
                "acquire_timeout": self.acquire_timeout,
                "stuck_timeout": self.stuck_timeout,
            }

            logger.info(
                "[TabPool] 运行时配置已更新: "
                f"max_tabs={self.max_tabs}, min_tabs={self.min_tabs}, "
                f"idle_timeout={self.idle_timeout}, acquire_timeout={self.acquire_timeout}, "
                f"stuck_timeout={self.stuck_timeout}"
            )
            return updated

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
    
    def _wrap_tab(self, tab, raw_tab_id: str = None) -> TabSession:
        self._tab_counter += 1
        
        url = ""
        try:
            url = tab.url or ""
        except:
            pass
        
        abbr = self._get_domain_abbr(url)
        tab_id = f"{abbr}_{self._tab_counter}"
        
        session = TabSession(id=tab_id, tab=tab)
        
        try:
            session.current_domain = extract_remote_site_domain(url)
        except:
            pass
        
        # 记录底层标签页 ID
        if raw_tab_id:
            self._known_tab_ids.add(raw_tab_id)
            
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
    
    def _scan_new_tabs(self):
        """扫描并添加新标签页（已持有锁）"""
        try:
            current_tabs = self.page.get_tabs()
            current_tab_set = set(current_tabs)
            
            # ===== 第一步：清理已关闭的标签页 =====
            # 找出池中存在、但浏览器中已消失的标签页
            sessions_to_remove = []
            for session_id, session in self._tabs.items():
                # 查找该 session 对应的 raw_tab_id
                raw_id = None
                for rid, pidx in self._raw_id_to_persistent.items():
                    if self._persistent_to_session_id.get(pidx) == session_id:
                        raw_id = rid
                        break
                
                if raw_id is not None and raw_id not in current_tab_set:
                    sessions_to_remove.append((session_id, raw_id, session))
            
            for session_id, raw_id, session in sessions_to_remove:
                if session.status == TabStatus.BUSY:
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
                if self._active_session_id == session_id:
                    self._active_session_id = None

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
            for raw_tab in current_tabs:
                if len(self._tabs) >= self.max_tabs:
                    break
                
                # 已在池中，跳过
                if raw_tab in tabs_in_pool:
                    continue
                
                try:
                    tab = self.page.get_tab(raw_tab)
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
                    
                    # 有效页面 - 添加到池
                    session = self._wrap_tab(tab, raw_tab)
                    self._tabs[session.id] = session
                    self._start_global_monitor_for_session(session)
                    new_count += 1
                    
                    display_url = url[:60] + "..." if len(url) > 60 else url
                    logger.debug(f"🆕 发现新标签页: {session.id} -> {display_url}")
                    
                except Exception as e:
                    logger.debug(f"处理标签页出错: {e}")
                    continue
            
            self._last_scan_time = time.time()
            
            if new_count > 0:
                logger.info(f"扫描完成: +{new_count} 个，当前共 {len(self._tabs)} 个标签页")
                
        except Exception as e:
            logger.warning(f"扫描标签页失败: {e}")
    
    def initialize(self):
        """初始化标签页池"""
        with self._lock:
            if self._initialized:
                return
            
            try:
                existing_tabs = self.page.get_tabs()
                logger.debug(f"检测到 {len(existing_tabs)} 个标签页")
                
                for raw_tab in existing_tabs:
                    if len(self._tabs) >= self.max_tabs:
                        break
                    
                    try:
                        tab = self.page.get_tab(raw_tab)
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
                        
                        # 有效页面 - 添加到池
                        session = self._wrap_tab(tab, raw_tab)
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
            logger.info(f"TabPool 就绪: {len(self._tabs)} 个标签页")
            
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
    
    def _cleanup_unhealthy_tabs(self):
        """清理不健康的空闲标签页和错误状态的标签页"""
        to_remove = []
    
        for tab_id, session in self._tabs.items():
            # 清理 ERROR 状态的标签页（包括强制释放失败的）
            if session.status == TabStatus.ERROR:
                to_remove.append(tab_id)
            # 清理空闲但不健康的标签页
            elif session.status == TabStatus.IDLE and not session.is_healthy():
                to_remove.append(tab_id)
    
        for tab_id in to_remove:
            session = self._tabs[tab_id]
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
            
            # 清理持久编号映射
            p_idx = session.persistent_index
            if p_idx and self._persistent_to_session_id.get(p_idx) == tab_id:
                del self._persistent_to_session_id[p_idx]
            
            # 清理活动标签页记录
            if self._active_session_id == tab_id:
                self._active_session_id = None
            
            del self._tabs[tab_id]
    
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
        rollback_request_count: bool = False
    ):
        """释放标签页"""
        with self._condition:
            session = self._tabs.get(tab_id)
            if session:
                session.release(
                    clear_page=clear_page,
                    check_triggers=check_triggers,
                    rollback_request_count=rollback_request_count
                )
                self._start_global_monitor_for_session(session)
                self._condition.notify_all()
                logger.debug(f"[{tab_id}] 已释放")
    
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
        with self._lock:
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
                
                logger.debug(f"等待标签页 #{persistent_index} 释放...")
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

                logger.debug(f"等待域名路由 '{target}' 的标签页释放...")
                self._condition.wait(timeout=min(remaining, 1.0))

    async def acquire_by_route_domain_async(
        self,
        route_domain: str,
        task_id: str,
        timeout: float = None
    ) -> Optional[TabSession]:
        """异步版本的按域名路由获取。"""
        return await asyncio.to_thread(self.acquire_by_route_domain, route_domain, task_id, timeout)

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
                f"status={session.status.value}, reason={reason}"
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
            # 每次查询都扫描，确保前端看到最新状态
            self._scan_new_tabs()
            self._last_scan_time = time.time()
            
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
            self._tabs.clear()
            self._known_tab_ids.clear()
            self._active_session_id = None  # 🆕 重置活动标签页记录
            # 🆕 清理编号映射
            self._raw_id_to_persistent.clear()
            self._persistent_to_session_id.clear()
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
