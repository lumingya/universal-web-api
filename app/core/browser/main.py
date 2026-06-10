# app/core/browser/main.py

import threading
from typing import Optional, Dict, Any, List, Callable

from DrissionPage import Chromium, ChromiumPage
from app.core.config import logger, BrowserConstants, BrowserConnectionError, SSEFormatter
from app.core.tab_pool import TabPoolManager

# Mixins
from .prompt import BrowserPromptMixin
from .connection import BrowserConnectionMixin, _load_tab_pool_config
from .workflow import BrowserWorkflowMixin
from .media import BrowserMediaMixin


class BrowserCore(
    BrowserPromptMixin,
    BrowserConnectionMixin,
    BrowserWorkflowMixin,
    BrowserMediaMixin,
):
    """浏览器核心类 - 单例模式（v2.0）"""
    
    _instance: Optional['BrowserCore'] = None
    _lock = threading.Lock()

    def __new__(cls, port: int = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance
    
    def __init__(self, port: int = None):
        if self._initialized:
            return
        
        self.port = port or BrowserConstants.DEFAULT_PORT
        self.browser_handle: Optional[Chromium] = None
        self.page: Optional[ChromiumPage] = None
        
        self._connected = False
        self._should_stop_checker: Callable[[], bool] = lambda: False
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()
        self._last_watchdog_port_open: Optional[bool] = None
        self._last_watchdog_session_alive: Optional[bool] = None
        self._last_watchdog_health: Optional[str] = None
        self._last_debug_port_error: str = ""
        self._get_tabs_retry_after: float = 0.0
        self._last_get_tabs_fallback_log_at: float = 0.0
        
        self.formatter = SSEFormatter()
        self.config_engine = None
        
        # v2.0: 使用 TabPoolManager 替代 TabManager
        self._tab_pool: Optional[TabPoolManager] = None
        
        self._initialized = True
        logger.debug("BrowserCore 初始化 (v2.0 多标签页版)")

    @property
    def tab_pool(self) -> TabPoolManager:
        """获取标签页池（延迟初始化 + 线程安全）"""
        if self._tab_pool is None:
            with self._lock:  # 使用类级别的锁
                if self._tab_pool is None:  # 双重检查
                    if not self.ensure_connection():
                        raise BrowserConnectionError("无法连接到浏览器")
                
                    pool_config = _load_tab_pool_config()
                    self._tab_pool = TabPoolManager(
                        browser_page=self.get_browser_handle(),
                        **pool_config
                    )
                    self._tab_pool.initialize()
    
        return self._tab_pool
    
    def _get_config_engine(self):
        if self.config_engine is None:
            from app.services.config_engine import config_engine
            self.config_engine = config_engine
        return self.config_engine

    def close(self):
        """关闭浏览器连接"""
        logger.info("关闭浏览器连接")
        self._watchdog_stop.set()
        watchdog_thread = self._watchdog_thread
        if (
            watchdog_thread
            and watchdog_thread.is_alive()
            and watchdog_thread is not threading.current_thread()
        ):
            watchdog_thread.join(timeout=1.0)
        self._watchdog_thread = None
        
        if self._tab_pool:
            self._tab_pool.shutdown()
            self._tab_pool = None
        
        self._connected = False
        self.browser_handle = None
        self.page = None
        
        with self._lock:
            BrowserCore._instance = None
            self._initialized = False


# ================= 工厂函数 =================

_browser_instance: Optional[BrowserCore] = None
_browser_lock = threading.Lock()


def get_browser(port: int = None, auto_connect: bool = True) -> BrowserCore:
    """获取浏览器实例"""
    global _browser_instance
    
    if _browser_instance is not None:
        return _browser_instance
    
    with _browser_lock:
        if _browser_instance is None:
            instance = BrowserCore(port)
            
            if auto_connect:
                if not instance.ensure_connection():
                    raise BrowserConnectionError(
                        f"无法连接到浏览器 (端口: {instance.port})"
                    )
            
            _browser_instance = instance
    
    return _browser_instance


class _LazyBrowser:
    """浏览器延迟初始化代理"""
    
    def __getattr__(self, name):
        return getattr(get_browser(auto_connect=False), name)
    
    def __call__(self, *args, **kwargs):
        return get_browser(*args, **kwargs)


browser = _LazyBrowser()
