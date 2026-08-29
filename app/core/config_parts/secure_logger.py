"""
app/core/config_parts/secure_logger.py - 安全日志器实现与日志工厂模块
"""
from collections import defaultdict
import contextlib
import contextvars
import logging
import sys
import threading
import time
from typing import Any, Dict, Optional

from .env_config import AppConfig
from .browser_constants import _BrowserConstantEnabledFilter
from .log_formatters import (
    _compact_logger_name,
    _ConsoleColorFormatter,
    _format_log_display_line,
    _web_log_handler,
    get_shared_file_log_handler,
)
from .cute_translator import (
    _cuteify_info_message,
    _cuteify_debug_message,
    _cuteify_warning_message,
    _cuteify_error_message,
    _add_suppressed_marker,
)

# 上下文变量，存储当前请求的 request_id
_request_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
_command_log_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "command_log_context",
    default=None,
)

_logger_setup_lock = threading.RLock()
_logger_registry_lock = threading.Lock()
_logger_registry: Dict[str, "SecureLogger"] = {}


class SecureLogger:
    """安全日志器，带图标和格式化（支持上下文自动注入 request_id）"""
    _debug_throttle_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
    _debug_throttle_state: Dict[str, Dict[str, Any]] = {}
    
    ICONS = {
        'DEBUG': '▫️',
        'INFO': '🔹',
        'WARNING': '⚠️',
        'ERROR': '❌',
        'SUCCESS': '✅',
        'STREAM': '🌊',
        'NETWORK': '🌐',
    }
    
    # 日志级别映射
    LEVEL_MAP = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL,
    }

    def __init__(self, name: str, level: Optional[int] = None):
        self._name = _compact_logger_name(name)
        
        # 如果未指定级别，从环境变量获取
        if level is None:
            level = self._get_level_from_env()
        
        self._level = level
        self._logger = self._setup_logger(name, level)
    
    @classmethod
    def _get_level_from_env(cls) -> int:
        """从环境变量获取日志级别"""
        level_str = AppConfig.get_log_level()
        return cls.LEVEL_MAP.get(level_str, logging.INFO)
    
    def _setup_logger(self, name: str, level: int) -> logging.Logger:
        logger = logging.getLogger(name)

        with _logger_setup_lock:
            # 防止日志向上层冒泡导致重复打印
            logger.propagate = False

            existing_kinds = {
                getattr(handler, "_codex_secure_handler", None)
                for handler in logger.handlers
            }

            if "console" not in existing_kinds:
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setLevel(logging.DEBUG)
                console_handler.setFormatter(_ConsoleColorFormatter())
                console_handler.addFilter(_BrowserConstantEnabledFilter("LOG_CONSOLE_ENABLED", True))
                setattr(console_handler, "_codex_secure_handler", "console")
                logger.addHandler(console_handler)

            file_handler = get_shared_file_log_handler()
            if file_handler is not None and file_handler not in logger.handlers:
                logger.addHandler(file_handler)

            if _web_log_handler not in logger.handlers:
                logger.addHandler(_web_log_handler)

            logger.setLevel(logging.DEBUG)
        return logger

    def _format(self, level_key: str, msg: str) -> str:
        """核心格式化逻辑（简洁版）"""
        record = logging.LogRecord(
            name=self._name,
            level=self.LEVEL_MAP.get(str(level_key or "").upper(), logging.INFO),
            pathname="",
            lineno=0,
            msg=str(msg or ""),
            args=(),
            exc_info=None,
        )
        record.codex_request_id = _request_context.get() or "SYSTEM"
        record.codex_logger_name = self._name
        formatted, _ = _format_log_display_line(record, msg)
        return formatted

    def _make_debug_throttle_key(self, key: str) -> str:
        normalized = str(key or "").strip() or "__default__"
        return f"{self._name}:{normalized}"

    @staticmethod
    def _coerce_bool(value: Any, default: bool = True) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return default

    def _get_effective_emit_level(self) -> Optional[int]:
        context = _command_log_context.get()
        if not isinstance(context, dict):
            return self._level

        if not self._coerce_bool(context.get("enabled", True), True):
            return None

        override = str(context.get("level", "GLOBAL") or "GLOBAL").strip().upper()
        if override == "GLOBAL":
            return self._level
        return self.LEVEL_MAP.get(override, self._level)

    def _emit(self, level: int, level_key: str, msg: str, *, exc_info: bool = False):
        effective_level = self._get_effective_emit_level()
        if effective_level is None or level < effective_level:
            return
        request_id = _request_context.get() or "SYSTEM"
        original_message_text = str(msg or "")
        display_message_text = original_message_text
        upper_level_key = str(level_key or "").upper()
        if upper_level_key in ("INFO", "SUCCESS", "STREAM", "NETWORK"):
            display_message_text = _cuteify_info_message(self._name, original_message_text)
        elif upper_level_key == "DEBUG":
            display_message_text = _cuteify_debug_message(self._name, original_message_text)
        elif upper_level_key == "WARNING":
            display_message_text = _cuteify_warning_message(self._name, original_message_text)
        elif upper_level_key in ("ERROR", "CRITICAL"):
            display_message_text = _cuteify_error_message(self._name, original_message_text)
        self._logger.log(
            level,
            original_message_text,
            exc_info=exc_info,
            extra={
                "codex_request_id": request_id,
                "codex_logger_name": self._name,
                "codex_message": original_message_text,
                "codex_original_message_text": original_message_text,
                "codex_display_message_text": display_message_text,
                "codex_kind": str(level_key or "").upper(),
            },
        )

    def set_level(self, level: int):
        """动态调整日志级别"""
        self._level = level
        self._logger.setLevel(logging.DEBUG)
        for handler in self._logger.handlers:
            handler.setLevel(logging.DEBUG)

    def debug(self, msg: str):
        self._emit(logging.DEBUG, 'DEBUG', msg)

    def debug_throttled(self, key: str, msg: str, interval_sec: float = 5.0):
        """在高频路径里限频输出 DEBUG，并附带被抑制次数。"""
        effective_level = self._get_effective_emit_level()
        if effective_level is None or logging.DEBUG < effective_level:
            return

        now = time.time()
        interval = max(0.0, float(interval_sec or 0.0))
        throttle_key = self._make_debug_throttle_key(key)
        suppressed = 0
        should_log = False

        lock = self._debug_throttle_locks[throttle_key]
        with lock:
            state = self._debug_throttle_state.get(throttle_key)
            last_at = float(state.get("last_at", 0.0) or 0.0) if state else 0.0
            if state is None or (now - last_at) >= interval:
                suppressed = int(state.get("suppressed", 0) or 0) if state else 0
                self._debug_throttle_state[throttle_key] = {
                    "last_at": now,
                    "suppressed": 0,
                }
                should_log = True
            else:
                state["suppressed"] = int(state.get("suppressed", 0) or 0) + 1

        if should_log:
            self.debug(_add_suppressed_marker(msg, suppressed))

    def info(self, msg: str):
        self._emit(logging.INFO, 'INFO', msg)

    def warning(self, msg: str):
        self._emit(logging.WARNING, 'WARNING', msg)

    def error(self, msg: str):
        self._emit(logging.ERROR, 'ERROR', msg)

    def exception(self, msg: str):
        self._emit(logging.ERROR, 'ERROR', msg, exc_info=True)
        
    def success(self, msg: str):
        self._emit(logging.INFO, 'SUCCESS', msg)

    def stream(self, msg: str):
        self._emit(logging.INFO, 'STREAM', msg)
        
    def network(self, msg: str):
        self._emit(logging.INFO, 'NETWORK', msg)

    @contextlib.contextmanager
    def context(self, request_id: str):
        """上下文管理器，用于在代码块中自动设置 request_id"""
        token = _request_context.set(request_id)
        try:
            yield
        finally:
            _request_context.reset(token)


@contextlib.contextmanager
def command_log_context(config: Optional[Dict[str, Any]] = None):
    token = _command_log_context.set(config if isinstance(config, dict) else None)
    try:
        yield
    finally:
        _command_log_context.reset(token)


def get_logger(name: str) -> SecureLogger:
    """获取 SecureLogger 实例（统一日志入口）"""
    normalized = str(name or "APP").strip() or "APP"
    with _logger_registry_lock:
        instance = _logger_registry.get(normalized)
        if instance is None:
            instance = SecureLogger(normalized)
            _logger_registry[normalized] = instance
        return instance


# 创建常用 logger 实例（向后兼容）
logger = get_logger("BROWSER")
