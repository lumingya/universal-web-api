"""
app/core/config_parts/log_formatters.py - 日志格式化器与 Handler 管理模块
"""
import ctypes
import datetime
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, List, Optional, Tuple

from .env_config import PROJECT_ROOT, DEFAULT_LOG_DIR
from .browser_constants import _BrowserConstantEnabledFilter
from .log_collector import log_collector
from .log_redaction import _sanitize_sensitive_text

_shared_file_log_handler: Optional[logging.Handler] = None
_shared_file_log_handler_lock = threading.Lock()


def _get_positive_int_env(name: str, default: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return value if value > 0 else default


def _truncate_long_message(text: str, max_chars: int) -> tuple[str, bool]:
    raw_text = str(text or "")
    if max_chars <= 0 or len(raw_text) <= max_chars:
        return raw_text, False
    omitted = len(raw_text) - max_chars
    return (
        f"{raw_text[:max_chars]}... [truncated {omitted} chars; see app.log for full sanitized text]",
        True,
    )


def _get_log_display_limit(env_name: str, default: int) -> int:
    return _get_positive_int_env(env_name, default)


def _record_request_id(record: logging.LogRecord) -> str:
    return str(getattr(record, "codex_request_id", "") or "SYSTEM")


_REQUEST_SHORT_ID_PATTERN = re.compile(r"^req-(\d+)$", re.IGNORECASE)


def _request_display_tag(request_id: Any) -> str:
    raw = str(request_id or "").strip()
    if not raw or raw.upper() == "SYSTEM":
        return "SYSTEM"

    match = _REQUEST_SHORT_ID_PATTERN.match(raw)
    if match:
        try:
            return f"#{int(match.group(1)):03d}"
        except Exception:
            return f"#{match.group(1)}"

    return raw if len(raw) <= 8 else f"#{raw[-6:]}"


def _record_request_tag(record: logging.LogRecord) -> str:
    return _request_display_tag(_record_request_id(record))


def _compact_logger_name_impl(name: Any, max_chars: int = 16) -> str:
    raw = str(name or "").strip().upper()
    if not raw:
        return ""
    if len(raw) <= max_chars:
        return raw

    def cap(label: str) -> str:
        label = str(label or "")
        if len(label) <= max_chars:
            return label
        if max_chars <= 3:
            return label[:max_chars]
        head = max(1, (max_chars - 1) // 2)
        tail = max(1, max_chars - head - 1)
        return f"{label[:head]}~{label[-tail:]}"

    parts = [part for part in raw.split(".") if part]
    if len(parts) > 1:
        prefix = ".".join(part[:1] for part in parts[:-1] if part)
        tail = parts[-1]
        candidate = f"{prefix}.{tail}" if prefix else tail
        if len(candidate) <= max_chars:
            return candidate

        tail_budget = max(3, max_chars - len(prefix) - (1 if prefix else 0))
        short_tail = tail[-tail_budget:]
        return cap(f"{prefix}.{short_tail}" if prefix else short_tail)

    if max_chars <= 3:
        return raw[:max_chars]
    return cap(raw)


def _compact_logger_name(name: Any, max_chars: int = 16) -> str:
    res = _compact_logger_name_impl(name, max_chars)
    if len(res) <= max_chars:
        return res
    if max_chars <= 3:
        return res[:max_chars]
    head = max(1, (max_chars - 1) // 2)
    tail = max(1, max_chars - head - 1)
    return f"{res[:head]}~{res[-tail:]}"


_LOGGER_DISPLAY_WIDTH = 12


def _record_logger_name(record: logging.LogRecord) -> str:
    return _compact_logger_name(
        getattr(record, "codex_logger_name", "") or getattr(record, "name", "") or "",
        max_chars=_LOGGER_DISPLAY_WIDTH,
    )


def _record_kind(record: logging.LogRecord) -> str:
    return str(getattr(record, "codex_kind", "") or record.levelname or "INFO").upper()


# 级别徽标：控制台/展示行使用的 3 字符级别标识
_LEVEL_BADGES = {
    "DEBUG": "DBG",
    "INFO": "INF",
    "WARNING": "WRN",
    "WARN": "WRN",
    "ERROR": "ERR",
    "CRITICAL": "CRI",
    "SUCCESS": "SUC",
    "STREAM": "STM",
    "NETWORK": "NET",
}


def _record_level_badge(record: logging.LogRecord) -> str:
    kind = _record_kind(record)
    if kind in _LEVEL_BADGES:
        return _LEVEL_BADGES[kind]
    level = str(record.levelname or "").upper()
    return _LEVEL_BADGES.get(level, (kind[:3] or "LOG").ljust(3))


def _replace_log_tag(text: str, source_tag: str, target_tag: str) -> str:
    if text.startswith(source_tag):
        rest = text[len(source_tag):].lstrip()
        return f"{target_tag} {rest}".rstrip()
    return text


def _normalize_log_display_expression(logger_name: str, message: str) -> str:
    """Normalize legacy log expressions for display without changing raw logs."""
    text = str(message or "")
    if not text:
        return text

    input_tags = (
        "[FILE_PASTE]",
        "[CHUNKED_INPUT]",
        "[CLIPBOARD_OK]",
        "[VERIFY_OK]",
        "[VERIFY_FAIL]",
        "[VERIFY]",
        "[INPUT_SNAPSHOT]",
        "[STEALTH_VERIFY]",
    )
    for tag in input_tags:
        normalized = _replace_log_tag(text, tag, "[INPUT]")
        if normalized != text:
            return normalized

    for tag in ("[NetworkMonitor]", "[NETWORK_MONITOR]"):
        normalized = _replace_log_tag(text, tag, "[MONIT]")
        if normalized != text:
            return normalized

    for tag in ("[JS_EXEC]", "[CONTENT_PARSE]", "[PROBE]", "[IMAGE]", "[STEALTH_CLICK]", "[STEALTH]"):
        normalized = _replace_log_tag(text, tag, "[PAGE]")
        if normalized != text:
            return normalized

    if text.startswith("[REQUEST_TRANSPORT]"):
        return _replace_log_tag(text, "[REQUEST_TRANSPORT]", "[ROUTE]")

    if text.startswith("[Executor]"):
        rest = text[len("[Executor]"):].lstrip()
        target = "[MONIT]" if any(hint in rest for hint in ("抓流", "监听", "网络")) else "[PAGE]"
        return f"{target} {rest}".rstrip()

    if text.startswith("[TabPool]"):
        return _replace_log_tag(text, "[TabPool]", "[POOL]")

    tabpool_match = re.match(r"^TabPool\s*(?:→|->)\s*(.+)$", text, re.S)
    if tabpool_match:
        return f"[POOL] 标签页已被占用: tab_id={tabpool_match.group(1)}"

    if text.startswith("TabPoolManager "):
        return f"[POOL] {text}"

    wait_done_match = re.match(r"^等待结束\s*(?:→|->)\s*(.+)$", text, re.S)
    if wait_done_match:
        return f"[POOL] 标签页等待结束: {wait_done_match.group(1)}"

    if text.startswith("排队等待 "):
        return f"[POOL] {text}"

    if text.startswith("等待标签页 ") or text.startswith("等待域名路由 "):
        return f"[POOL] {text}"

    assign_match = re.match(r"^标签页 (.+) 分配编号 #(\d+)$", text)
    if assign_match:
        session_id, index_no = assign_match.groups()
        return f"[POOL] 标签页分配编号: tab_id={session_id}, idx=#{index_no}"

    if text.startswith("发送成功"):
        return f"[SEND] {text}"

    if text == "浏览器连接成功" or text == "关闭浏览器连接":
        return f"[SYS] {text}"

    if logger_name == "REQUEST" and text == "创建":
        return "[ROUTE] 请求上下文已创建"

    if logger_name == "API.CHAT" and text == "开始":
        return "[ROUTE] 聊天补全请求开始处理"

    return text


def _record_display_message(record: logging.LogRecord) -> str:
    message = str(getattr(record, "codex_display_message_text", "") or "")
    if not message:
        message = str(record.getMessage() or "")
    original_message = str(getattr(record, "codex_original_message_text", "") or "")
    if message == original_message or not original_message:
        message = _normalize_log_display_expression(_record_logger_name(record), message)
    return _sanitize_sensitive_text(message)


def _format_log_display_parts(
    record: logging.LogRecord,
    message: str,
    *,
    max_chars: int = 0,
) -> tuple[list[str], str, bool]:
    """构建展示行的各个片段。

    返回 (prefix_parts, body, truncated)：
    - prefix_parts = [时间, 级别徽标, 请求标签, 日志器名]，便于控制台按片段分别上色；
    - body 已按前缀宽度做了多行缩进对齐。
    """
    now = datetime.datetime.fromtimestamp(
        float(getattr(record, "created", time.time()) or time.time())
    ).strftime("%H:%M:%S")
    badge = _record_level_badge(record)
    request_tag = _record_request_tag(record)
    logger_name = _record_logger_name(record)
    parts = [
        now,
        f"{badge:<3}",
        f"{request_tag:<8}",
        f"{logger_name:<{_LOGGER_DISPLAY_WIDTH}}",
    ]
    prefix_width = sum(len(part) for part in parts) + 3 * len(parts)
    body, truncated = _truncate_long_message(str(message or ""), max_chars)
    body = body.replace("\n", "\n" + " " * prefix_width)
    return parts, body, truncated


def _join_log_display_parts(parts: list[str], body: str) -> str:
    return " │ ".join(parts) + " │ " + body


def _format_log_display_line(
    record: logging.LogRecord,
    message: str,
    *,
    max_chars: int = 0,
) -> tuple[str, bool]:
    parts, body, truncated = _format_log_display_parts(record, message, max_chars=max_chars)
    return _join_log_display_parts(parts, body), truncated


class _WebLogHandler(logging.Handler):
    """将日志发送到 Web 收集器（内部类）"""

    def emit(self, record):
        try:
            if not log_collector.is_enabled():
                return
            raw_message = _sanitize_sensitive_text(str(getattr(record, "codex_message", "") or ""))
            if not raw_message:
                raw_message = _sanitize_sensitive_text(str(record.getMessage() or ""))
            message_text = _record_display_message(record)
            original_message_text = _sanitize_sensitive_text(str(
                getattr(record, "codex_original_message_text", "") or raw_message
            ))
            if not message_text:
                message_text = raw_message
            web_limit = _get_log_display_limit("LOG_WEB_MAX_CHARS", 2000)
            message_text, message_truncated = _truncate_long_message(message_text, web_limit)
            original_message_text, original_truncated = _truncate_long_message(
                original_message_text,
                web_limit,
            )
            msg, line_truncated = _format_log_display_line(
                record,
                message_text,
            )
            logger_name = _record_logger_name(record)
            request_id = _record_request_id(record)
            request_tag = _record_request_tag(record)
            kind = _record_kind(record)
            log_collector.add({
                "timestamp": float(getattr(record, "created", time.time()) or time.time()),
                "level": str(record.levelname or "INFO").upper(),
                "kind": kind,
                "message": msg,
                "display_message": msg,
                "message_text": message_text,
                "original_message_text": original_message_text,
                "message_alias": message_text if message_text != original_message_text else "",
                "logger": logger_name,
                "request_id": request_id,
                "request_tag": request_tag,
                "truncated": bool(message_truncated or original_truncated or line_truncated),
            })
        except Exception:
            self.handleError(record)


def _enable_windows_ansi() -> bool:
    """在 Windows 控制台中尽量启用 ANSI 颜色支持。"""
    if os.name != "nt":
        return True

    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if handle in (0, -1):
            return False

        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False

        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True

        return kernel32.SetConsoleMode(
            handle,
            mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        ) != 0
    except Exception:
        return False


def _should_use_console_color() -> bool:
    """判断当前控制台是否应启用 ANSI 颜色。"""
    if os.environ.get("NO_COLOR"):
        return False

    if os.name == "nt":
        if _enable_windows_ansi():
            return True

        if os.environ.get("WT_SESSION"):
            return True
        if os.environ.get("ANSICON"):
            return True
        if os.environ.get("ConEmuANSI", "").upper() == "ON":
            return True
        if os.environ.get("TERM_PROGRAM") == "vscode":
            return True
        return False

    return bool(getattr(sys.stdout, "isatty", lambda: False)())


class _ConsoleColorFormatter(logging.Formatter):
    """仅用于控制台输出的彩色格式化器。"""

    RESET = "\033[0m"
    # 整行色调
    LINE_TONES = {
        "ERROR": "\033[31m",
        "WARN": "\033[33m",
        "KEY": "\033[94m",
        "SUCCESS": "\033[92m",
        "DEBUG": "\033[90m",
    }
    # 级别徽标色
    BADGE_TONES = {
        "INF": "\033[32m",
        "STM": "\033[36m",
        "NET": "\033[36m",
        "SUC": "\033[92m",
        "WRN": "\033[1;33m",
        "ERR": "\033[1;31m",
        "CRI": "\033[1;31m",
        "DBG": "\033[90m",
    }
    KEY_PATTERNS = (
        "[CMD] ▶ 执行:",
        "[CMD] 执行:",
        "[CMD] 开始执行工作流:",
        "[CMD] 触发命令:",
        "[CMD] 链式触发:",
        "[CMD] 条件分支触发:",
        "[CMD] 结果事件触发:",
    )

    def __init__(self):
        super().__init__()
        self._use_color = _should_use_console_color()

    def _resolve_tone(self, record: logging.LogRecord, message: str) -> Optional[str]:
        level = str(record.levelname or "").upper()
        if level in ("ERROR", "CRITICAL"):
            return "ERROR"
        if level == "WARNING":
            return "WARN"
        if level == "DEBUG":
            return "DEBUG"
        if any(pattern in message for pattern in self.KEY_PATTERNS):
            return "KEY"
        if _record_kind(record) == "SUCCESS":
            return "SUCCESS"
        return None

    def format(self, record: logging.LogRecord) -> str:
        message = _record_display_message(record)
        console_limit = _get_log_display_limit("LOG_CONSOLE_MAX_CHARS", 1200)
        message, _ = _truncate_long_message(message, console_limit)
        parts, body, _ = _format_log_display_parts(record, message)
        formatted = _join_log_display_parts(parts, body)
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            if exc_text:
                formatted = f"{formatted}\n{exc_text}"

        if not self._use_color:
            return formatted

        tone = self._resolve_tone(record, formatted)
        if tone:
            color = self.LINE_TONES.get(tone)
            if color:
                return f"{color}{formatted}{self.RESET}"
            return formatted

        badge = parts[1]
        badge_color = self.BADGE_TONES.get(badge.strip())
        if not badge_color:
            return formatted
        colored_parts = list(parts)
        colored_parts[1] = f"{badge_color}{badge}{self.RESET}"
        colored = _join_log_display_parts(colored_parts, body)
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            if exc_text:
                colored = f"{colored}\n{exc_text}"
        return colored


class _FileLogFormatter(logging.Formatter):
    """文件日志使用结构化字段，避免控制台前缀被再次包裹。"""

    def __init__(self):
        super().__init__(
            "%(asctime)s | %(levelname)s | %(request_tag)s | %(name)s | %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        original_message = str(
            getattr(record, "codex_original_message_text", "") or record.getMessage() or ""
        )
        message = _sanitize_sensitive_text(
            _normalize_log_display_expression(_record_logger_name(record), original_message)
        )
        prefix = (
            f"{self.formatTime(record, self.datefmt)} | "
            f"{record.levelname} | "
            f"{_record_request_tag(record)} | "
            f"{_record_logger_name(record) or record.name} | "
        )
        formatted = f"{prefix}{message.replace(chr(10), chr(10) + ' ' * len(prefix))}"
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            if exc_text:
                formatted = f"{formatted}\n{exc_text}"
        return formatted


class _DisplayLogFormatter(logging.Formatter):
    """保留旧式单行展示前缀，供兼容 handler 使用。"""

    def format(self, record: logging.LogRecord) -> str:
        message = _record_display_message(record)
        formatted, _ = _format_log_display_line(record, message)
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            if exc_text:
                formatted = f"{formatted}\n{exc_text}"
        return formatted


def _is_windows_file_lock_error(exc: BaseException) -> bool:
    """Return True for transient Windows sharing violations during file rotation."""
    if os.name != "nt" or not isinstance(exc, OSError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror in {32, 33}:
        return True
    return getattr(exc, "errno", None) in {32, 33}


class _SafeRotatingFileHandler(RotatingFileHandler):
    """Windows 上轮转目标被短暂占用时，降级为继续写当前日志。"""

    _ROLLOVER_RETRY_DELAY_SECONDS = 5.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rollover_retry_after = 0.0

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self._rollover_retry_after and time.monotonic() < self._rollover_retry_after:
            return False
        return super().shouldRollover(record)

    def doRollover(self) -> None:
        try:
            super().doRollover()
            self._rollover_retry_after = 0.0
        except OSError as exc:
            if not _is_windows_file_lock_error(exc):
                raise
            self._defer_rollover()
            if self.stream is None and not self.delay:
                self.stream = self._open()

    def handleError(self, record: logging.LogRecord) -> None:
        exc = sys.exc_info()[1]
        if _is_windows_file_lock_error(exc):
            self._defer_rollover()
            try:
                if self.stream is None and not self.delay:
                    self.stream = self._open()
                logging.FileHandler.emit(self, record)
            except Exception:
                pass
            return
        super().handleError(record)

    def _defer_rollover(self) -> None:
        self._rollover_retry_after = time.monotonic() + self._ROLLOVER_RETRY_DELAY_SECONDS


# 创建全局 Web 日志处理器
_web_log_handler = _WebLogHandler()
_web_log_handler.setLevel(logging.DEBUG)
_web_log_handler.setFormatter(_DisplayLogFormatter())
_web_log_handler.addFilter(_BrowserConstantEnabledFilter("LOG_WEB_COLLECTOR_ENABLED", True))
setattr(_web_log_handler, "_codex_secure_handler", "web")


def _resolve_log_dir() -> Path:
    configured = str(os.getenv("LOG_DIR", "") or "").strip()
    if not configured:
        return DEFAULT_LOG_DIR
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def get_log_file_path() -> Path:
    configured = str(os.getenv("LOG_FILE", "") or "").strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return candidate.resolve()
    return _resolve_log_dir() / "app.log"


def get_shared_file_log_handler() -> Optional[logging.Handler]:
    global _shared_file_log_handler

    with _shared_file_log_handler_lock:
        if _shared_file_log_handler is not None:
            return _shared_file_log_handler

        try:
            log_file = get_log_file_path()
            log_file.parent.mkdir(parents=True, exist_ok=True)

            handler = _SafeRotatingFileHandler(
                log_file,
                maxBytes=_get_positive_int_env("LOG_MAX_BYTES", 5 * 1024 * 1024),
                backupCount=_get_positive_int_env("LOG_BACKUP_COUNT", 5),
                encoding="utf-8",
            )
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(_FileLogFormatter())
            handler.addFilter(_BrowserConstantEnabledFilter("LOG_FILE_ENABLED", True))
            setattr(handler, "_codex_secure_handler", "file")
            _shared_file_log_handler = handler
        except Exception as e:
            try:
                print(f"[Config] failed to initialize file logging: {e}", file=sys.stderr)
            except Exception:
                pass
            _shared_file_log_handler = None

        return _shared_file_log_handler
