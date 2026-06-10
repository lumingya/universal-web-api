# app/core/browser/connection.py

import json
import os
import socket
import threading
import time
import contextlib
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from DrissionPage import Chromium, ChromiumPage, ChromiumOptions
from app.core.config import logger, BrowserConstants, BrowserConnectionError, SSEFormatter

if TYPE_CHECKING:
    from .main import BrowserCore
    from app.core.tab_pool import TabPoolManager, TabSession


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


def _load_tab_pool_config() -> Dict:
    """从配置文件和环境变量加载标签页池配置"""
    config = {
        "max_tabs": 5,
        "min_tabs": 1,
        "idle_timeout": 300,
        "acquire_timeout": 60,
        "stuck_timeout": 180,
        "allocation_mode": "first_idle",
    }
    
    # 从 browser_config.json 加载
    try:
        config_path = "config/browser_config.json"
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
                pool_config = file_config.get("tab_pool", {})
                config.update(pool_config)
    except Exception as e:
        logger.debug(f"加载 tab_pool 配置失败: {e}")
    
    # 环境变量覆盖
    if os.getenv("MAX_TABS"):
        config["max_tabs"] = int(os.getenv("MAX_TABS"))
    if os.getenv("MIN_TABS"):
        config["min_tabs"] = int(os.getenv("MIN_TABS"))

    allowed_keys = {
        "max_tabs",
        "min_tabs",
        "idle_timeout",
        "acquire_timeout",
        "stuck_timeout",
        "allocation_mode",
    }
    return {key: value for key, value in config.items() if key in allowed_keys}


class BrowserConnectionMixin:
    """浏览器连接管理、生命周期、Watchdog 及 Tab 巡检混入类"""

    @staticmethod
    def _to_bool_env(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _shutdown_tab_pool_for_reconnect(self, reason: str = "reconnect") -> None:
        pool = getattr(self, "_tab_pool", None)
        if pool is None:
            return
        self._tab_pool = None
        try:
            pool.shutdown(close_browser_tabs=False)
            logger.info(f"[BrowserCore] 已重置旧 TabPool ({reason})")
        except Exception as e:
            logger.warning(f"[BrowserCore] 重置旧 TabPool 失败 ({reason}): {e}")

    def _dispose_previous_browser_handle(self, browser_handle: Any, reason: str = "reconnect") -> None:
        if browser_handle is None:
            return

        if self._to_bool_env("BROWSER_RECONNECT_QUIT_OLD_HANDLE", False):
            try:
                browser_handle.quit(timeout=1, force=False, del_data=False)
                logger.info(f"[BrowserCore] 已关闭旧浏览器句柄 ({reason})")
                return
            except Exception as e:
                logger.debug(f"[BrowserCore] 关闭旧浏览器句柄失败 ({reason}): {e}")

        stopped = 0
        for driver in [getattr(browser_handle, "_driver", None)]:
            stop = getattr(driver, "stop", None)
            if callable(stop):
                try:
                    stop()
                    stopped += 1
                except Exception:
                    pass
        try:
            all_drivers = getattr(browser_handle, "_all_drivers", {}) or {}
            for driver_group in list(all_drivers.values()):
                for driver in list(driver_group or []):
                    stop = getattr(driver, "stop", None)
                    if callable(stop):
                        try:
                            stop()
                            stopped += 1
                        except Exception:
                            pass
        except Exception:
            pass
        if stopped:
            logger.debug(f"[BrowserCore] 已释放旧 CDP driver 引用 ({reason}, count={stopped})")

    def _connect(self) -> bool:
        previous_handle = getattr(self, "browser_handle", None)
        self._shutdown_tab_pool_for_reconnect("connect")
        try:
            logger.debug(f"连接浏览器 127.0.0.1:{self.port}")
            opts = ChromiumOptions()
            opts.set_address(f"127.0.0.1:{self.port}")
            opts.existing_only()
            self.browser_handle = Chromium(addr_or_opts=opts)
            if previous_handle is not None and previous_handle is not self.browser_handle:
                self._dispose_previous_browser_handle(previous_handle, "connect")
            try:
                self.page = self.browser_handle.latest_tab
            except Exception:
                self.page = None
            self._connected = True
            self._start_connection_watchdog()
            logger.info("浏览器连接成功")
            return True
        except Exception as e:
            logger.error(f"浏览器连接失败: {e}")
            self.browser_handle = None
            self.page = None
            self._connected = False
            return False

    def _is_debug_port_open(self, timeout: float = 0.4) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(self.port)), timeout=float(timeout)):
                self._last_debug_port_error = ""
                return True
        except Exception as e:
            self._last_debug_port_error = str(e)
            return False

    def _probe_browser_connection_via_cdp(self) -> bool:
        """Prefer the long-lived CDP session over spawning /json short connections."""
        browser = self.get_browser_handle()
        if not browser or not hasattr(browser, "_run_cdp"):
            return False

        try:
            result = browser._run_cdp("Target.getTargets") or {}
            target_infos = result.get("targetInfos")
            return isinstance(target_infos, list)
        except Exception:
            return False

    def _list_tabs_via_cdp(self) -> List[Any]:
        browser = self.get_browser_handle()
        if not browser or not hasattr(browser, "_run_cdp"):
            return []

        try:
            result = browser._run_cdp("Target.getTargets") or {}
            target_infos = result.get("targetInfos") or []
        except Exception:
            return []

        tabs: List[Any] = []
        for info in target_infos:
            if not isinstance(info, dict):
                continue
            if str(info.get("type") or "").strip().lower() != "page":
                continue
            target_id = str(info.get("targetId") or "").strip()
            if not target_id:
                continue
            try:
                resolved = browser.get_tab(target_id)
            except Exception:
                resolved = None
            tabs.append(resolved or target_id)
        return tabs

    def _log_get_tabs_fallback(self, message: str, error: Any = None) -> None:
        now = time.time()
        if now - self._last_get_tabs_fallback_log_at < 10.0:
            return
        self._last_get_tabs_fallback_log_at = now
        if _looks_like_transient_local_debug_error(error):
            logger.debug(message)
        else:
            logger.warning(message)

    def _probe_browser_connection(self) -> bool:
        """Lightweight liveness probe for the underlying browser session."""
        return self._probe_browser_connection_via_cdp()

    def get_browser_handle(self):
        if self.browser_handle is not None:
            return self.browser_handle
        browser = getattr(self.page, "browser", None)
        if browser is not None:
            self.browser_handle = browser
            return browser
        return self.page

    def get_tabs(self):
        browser = self.get_browser_handle()
        if browser is None:
            raise BrowserConnectionError("无法连接到浏览器")
        cdp_tabs = self._list_tabs_via_cdp()
        if cdp_tabs:
            self._get_tabs_retry_after = 0.0
            return cdp_tabs
        now = time.time()
        if now < self._get_tabs_retry_after:
            return []
        try:
            tabs = browser.get_tabs()
            self._get_tabs_retry_after = 0.0
            return tabs
        except Exception as e:
            self._get_tabs_retry_after = max(self._get_tabs_retry_after, time.time() + 5.0)
            fallback_tabs = self._list_tabs_via_cdp()
            if fallback_tabs:
                self._log_get_tabs_fallback(
                    f"[BrowserCore] browser.get_tabs() 失败，回退到 Target.getTargets: {e}",
                    error=e,
                )
                return fallback_tabs
            raise

    def get_tab_ids(self) -> List[str]:
        browser = self.get_browser_handle()
        if browser is None:
            raise BrowserConnectionError("无法连接到浏览器")
        return list(getattr(browser, "tab_ids", []) or [])

    def get_tab(self, id_or_num=None):
        browser = self.get_browser_handle()
        if browser is None:
            raise BrowserConnectionError("无法连接到浏览器")
        return browser.get_tab(id_or_num)

    def get_latest_tab(self):
        browser = self.get_browser_handle()
        if browser is None:
            raise BrowserConnectionError("无法连接到浏览器")
        return browser.latest_tab

    def _get_watchdog_tab_snapshot(self) -> str:
        try:
            if not self._tab_pool:
                return "tab_pool=uninitialized"
            if hasattr(self._tab_pool, "get_watchdog_summary"):
                status = self._tab_pool.get_watchdog_summary(limit=4)
            else:
                status = self._tab_pool.get_status()
            total = int(status.get("total", 0) or 0)
            idle = int(status.get("idle", 0) or 0)
            busy = int(status.get("busy", 0) or 0)
            tabs = status.get("tabs") or []
            labels = []
            for item in tabs[:4]:
                if not isinstance(item, dict):
                    continue
                labels.append(
                    f"{item.get('id', '?')}:{item.get('status', '?')}:{'iso' if item.get('is_isolated_context') else 'shared'}"
                )
            extra = f", tabs=[{', '.join(labels)}]" if labels else ""
            return f"tab_pool(total={total}, idle={idle}, busy={busy}{extra})"
        except Exception as e:
            return f"tab_pool=error({e})"

    @staticmethod
    def _get_watchdog_float_env(name: str, default: float, minimum: float = 0.5) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except Exception:
            return default
        return max(float(minimum), value)

    def _has_recent_watchdog_activity(self) -> bool:
        now = time.time()
        recent_window = self._get_watchdog_float_env(
            "BROWSER_WATCHDOG_RECENT_ACTIVITY_WINDOW",
            60.0,
            minimum=1.0,
        )

        try:
            from app.services.request_manager import request_manager

            status = request_manager.get_status()
            if int(status.get("running_count", 0) or 0) > 0:
                self._last_watchdog_activity_at = now
                return True
        except Exception:
            return True

        try:
            pool = self._tab_pool
            sessions = pool.get_sessions_snapshot() if pool is not None else []
            for session in sessions:
                status_value = getattr(getattr(session, "status", None), "value", "")
                if status_value == "busy":
                    self._last_watchdog_activity_at = now
                    return True
        except Exception:
            return True

        last_activity = float(getattr(self, "_last_watchdog_activity_at", 0.0) or 0.0)
        return bool(last_activity and now - last_activity < recent_window)

    def _get_connection_watchdog_interval(self) -> float:
        active_interval = self._get_watchdog_float_env(
            "BROWSER_WATCHDOG_ACTIVE_INTERVAL",
            3.0,
            minimum=0.5,
        )
        idle_interval = self._get_watchdog_float_env(
            "BROWSER_WATCHDOG_IDLE_INTERVAL",
            5.0,
            minimum=active_interval,
        )
        return active_interval if self._has_recent_watchdog_activity() else idle_interval

    def _connection_watchdog_loop(self) -> None:
        while not self._watchdog_stop.wait(self._get_connection_watchdog_interval()):
            try:
                pool = self._tab_pool
                if pool is not None:
                    pool.run_watchdog_tick()
            except Exception as e:
                logger.debug(f"[BrowserWatchdog] 标签页巡检异常（忽略）: {e}")

            port_open = self._is_debug_port_open()
            session_alive = self._probe_browser_connection()
            health = "alive" if session_alive else ("port_only" if port_open else "dead")

            if health == self._last_watchdog_health:
                continue

            snapshot = self._get_watchdog_tab_snapshot()
            if health == "alive":
                if port_open:
                    logger.info(
                        f"[BrowserWatchdog] 受控浏览器连接正常 (port={self.port}, session=alive, {snapshot})"
                    )
                elif _looks_like_transient_local_debug_error(self._last_debug_port_error):
                    logger.debug(
                        f"[BrowserWatchdog] 长连接正常，已忽略瞬时调试端口探测失败 "
                        f"(port={self.port}, session=alive, {snapshot})"
                    )
                else:
                    logger.info(
                        f"[BrowserWatchdog] 受控浏览器连接正常 "
                        f"(port={self.port}, session=alive, port_probe=failed, {snapshot})"
                    )
            elif health == "port_only":
                logger.warning(
                    f"[BrowserWatchdog] 调试端口可达，但当前页面会话已失效 "
                    f"(port={self.port}, session=dead, connected={self._connected}, {snapshot})"
                )
            else:
                detail = ""
                if self._last_debug_port_error and not _looks_like_transient_local_debug_error(self._last_debug_port_error):
                    detail = f", probe_error={self._last_debug_port_error}"
                logger.error(
                    f"[BrowserWatchdog] 受控浏览器调试端口不可达 "
                    f"(port={self.port}, connected={self._connected}, {snapshot}{detail})"
                )

            self._last_watchdog_port_open = port_open
            self._last_watchdog_session_alive = session_alive
            self._last_watchdog_health = health

    def _start_connection_watchdog(self) -> None:
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return

        self._watchdog_stop.clear()
        self._last_watchdog_port_open = None
        self._last_watchdog_session_alive = None
        self._last_watchdog_health = None
        self._watchdog_thread = threading.Thread(
            target=self._connection_watchdog_loop,
            daemon=True,
            name="browser-connection-watchdog",
        )
        self._watchdog_thread.start()
    
    def health_check(self) -> Dict[str, Any]:
        result = {
            "status": "unhealthy",
            "connected": False,
            "port": self.port,
            "tab_pool": None,
            "error": None
        }
        
        try:
            if not self.get_browser_handle():
                if not self._connect():
                    result["error"] = "无法连接到浏览器"
                    return result
            elif not self._probe_browser_connection():
                previous_handle = self.browser_handle
                self._connected = False
                self.browser_handle = None
                self.page = None
                self._dispose_previous_browser_handle(previous_handle, "health_check")
                if not self._connect():
                    result["error"] = "无法连接到浏览器"
                    return result
            
            result["status"] = "healthy"
            result["connected"] = True
            
            if self._tab_pool:
                result["tab_pool"] = self._tab_pool.get_status()
        
        except Exception as e:
            result["error"] = str(e)
            self._connected = False
        
        return result
    
    def ensure_connection(self) -> bool:
        if self._connected:
            if self._probe_browser_connection():
                return True
            previous_handle = self.browser_handle
            self._connected = False
            self.browser_handle = None
            self.page = None
            self._dispose_previous_browser_handle(previous_handle, "ensure_connection")
        
        return self._connect()
    
    def get_active_tab(self):
        """
        获取一个可用的标签页（兼容旧接口）
        
        注意：新代码应使用 execute_workflow_with_session
        """
        # 生成临时任务 ID
        task_id = f"legacy_{int(time.time() * 1000)}"
        session = self.tab_pool.acquire(task_id, timeout=30)
        if session is None:
            raise BrowserConnectionError("无法获取可用标签页")
        return session.tab

    @contextlib.contextmanager
    def get_temporary_tab(self, timeout: int = 30):
        """
        获取临时标签页的上下文管理器（推荐使用）
    
        使用方式:
            with browser.get_temporary_tab() as tab:
                elements = tab.eles(selector)
            # 退出 with 块后自动释放
    
        Args:
            timeout: 获取标签页的超时时间（秒）
    
        Yields:
            tab: 浏览器标签页对象
    
        Raises:
            BrowserConnectionError: 无法获取可用标签页时抛出
        """
        task_id = f"temp_{int(time.time() * 1000)}"
        session = None
    
        try:
            session = self.tab_pool.acquire(task_id, timeout=timeout)
        
            if session is None:
                raise BrowserConnectionError("无法获取可用标签页，服务繁忙请稍后重试")
        
            logger.debug(f"[{session.id}] 临时标签页已分配")
            yield session.tab
        
        finally:
            if session is not None:
                self.tab_pool.release(session.id)
                logger.debug(f"[{session.id}] 临时标签页已释放")

    def get_pool_status(self) -> Dict:
        """获取标签页池状态"""
        if self._tab_pool:
            return self._tab_pool.get_status()
        return {"initialized": False}

    def _check_page_status(self, tab) -> Dict[str, Any]:
        """检查页面状态"""
        result = {"ready": True, "reason": None}
        
        try:
            url = tab.url or ""
            
            if not url or url in ("about:blank", "chrome://newtab/"):
                result["ready"] = False
                result["reason"] = "请先打开目标AI网站"
                return result
            
            error_indicators = ["chrome-error://", "about:neterror"]
            for indicator in error_indicators:
                if indicator in url:
                    result["ready"] = False
                    result["reason"] = "页面加载错误"
                    return result
        
        except Exception as e:
            logger.debug(f"页面状态检查异常: {e}")
        
        return result
