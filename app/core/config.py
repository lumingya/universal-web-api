"""
app/core/config.py - 配置和基础设施

职责：
- 环境变量配置管理（从 .env 加载）
- 浏览器常量配置（从 JSON 加载）
- 异常定义
- 日志系统
- SSE 格式化器
- 消息验证器

此模块是基础层，不依赖其他 app.core 模块
"""
import contextvars
import contextlib
import os
import time
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import sys
import threading
import uuid
import ctypes
from pathlib import Path
from typing import Any, Dict, List, Optional
from functools import lru_cache
from collections import deque
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
_shared_file_log_handler: Optional[logging.Handler] = None
_shared_file_log_handler_lock = threading.Lock()
# ================= 环境变量加载 =================


class classproperty:
    """Allow config access via both `AppConfig.X` and `app_config.X`."""

    def __init__(self, fget):
        self.fget = fget

    def __get__(self, obj, owner=None):
        return self.fget(owner)


def load_dotenv(env_file: str = ".env", override: bool = True):
    """
    手动加载 .env 文件（不依赖 python-dotenv）
    """
    env_path = Path(env_file)
    if not env_path.exists():
        return
    
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip()
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    if key:
                        if override or key not in os.environ:
                            os.environ[key] = value
    except Exception as e:
        print(f"[Config] 加载 .env 失败: {e}")

load_dotenv()


# ================= 应用配置（环境变量）=================

class AppConfig:
    """应用配置（从环境变量读取）"""
    
    # ===== 服务配置 =====
    @staticmethod
    def get_host() -> str:
        return os.getenv("APP_HOST", "127.0.0.1")
    
    @staticmethod
    def get_port() -> int:
        return int(os.getenv("APP_PORT", "8199"))
    
    @staticmethod
    def is_debug() -> bool:
        return os.getenv("APP_DEBUG", "false").lower() in ("true", "1", "yes")
    
    @staticmethod
    def get_log_level() -> str:
        return os.getenv("LOG_LEVEL", "INFO").upper()
    
    # ===== 认证配置 =====
    @staticmethod
    def is_auth_enabled() -> bool:
        return os.getenv("AUTH_ENABLED", "false").lower() in ("true", "1", "yes")
    
    @staticmethod
    def get_auth_token() -> str:
        return os.getenv("AUTH_TOKEN", "")
    
    # ===== CORS 配置 =====
    @staticmethod
    def is_cors_enabled() -> bool:
        return os.getenv("CORS_ENABLED", "true").lower() in ("true", "1", "yes")
    
    @staticmethod
    def get_cors_origins() -> List[str]:
        origins = os.getenv("CORS_ORIGINS", "*")
        if origins == "*":
            return ["*"]
        return [o.strip() for o in origins.split(",") if o.strip()]
    
    # ===== 浏览器配置 =====
    @staticmethod
    def get_browser_port() -> int:
        return int(os.getenv("BROWSER_PORT", "9222"))
    
    # ===== Dashboard 配置 =====
    @staticmethod
    def is_dashboard_enabled() -> bool:
        return os.getenv("DASHBOARD_ENABLED", "true").lower() in ("true", "1", "yes")
    
    @staticmethod
    def get_dashboard_file() -> str:
        return os.getenv("DASHBOARD_FILE", "static/index.html")
    
    # ===== AI 分析配置 =====
    @staticmethod
    def get_helper_api_key() -> str:
        return os.getenv("HELPER_API_KEY", "")
    
    @staticmethod
    def get_helper_base_url() -> str:
        return os.getenv("HELPER_BASE_URL", "")
    
    @staticmethod
    def get_helper_model() -> str:
        return os.getenv("HELPER_MODEL", "gpt-4")
        
    @staticmethod
    def get_helper_api_provider() -> str:
        return os.getenv("HELPER_API_PROVIDER", "auto").lower()
    
    @staticmethod
    def get_max_html_chars() -> int:
        return int(os.getenv("MAX_HTML_CHARS", "120000"))
    
    # ===== 配置文件路径 =====
    @staticmethod
    def get_sites_config_file() -> str:
        return os.getenv("SITES_CONFIG_FILE", "config/sites.json")

    @staticmethod
    def get_marketplace_file() -> str:
        return os.getenv("MARKETPLACE_FILE", "config/marketplace.json")

    @staticmethod
    def get_marketplace_cache_file() -> str:
        return os.getenv("MARKETPLACE_CACHE_FILE", "config/marketplace_cache.json")

    @staticmethod
    def get_marketplace_index_url() -> str:
        explicit_url = os.getenv("MARKETPLACE_INDEX_URL", "").strip()
        if explicit_url:
            return explicit_url

        repo = AppConfig.get_marketplace_repo()
        if not repo:
            return ""

        branch = AppConfig.get_marketplace_branch()
        index_path = AppConfig.get_marketplace_index_path()
        return f"https://raw.githubusercontent.com/{repo}/{branch}/{index_path}"

    @staticmethod
    def get_marketplace_upload_url() -> str:
        explicit_url = os.getenv("MARKETPLACE_UPLOAD_URL", "").strip()
        if explicit_url:
            return explicit_url

        repo = AppConfig.get_marketplace_repo()
        if not repo:
            return ""

        template_name = AppConfig.get_marketplace_issue_template()
        base_url = f"https://github.com/{repo}/issues/new"
        if not template_name:
            return base_url
        return f"{base_url}?template={quote(template_name, safe='')}"

    @staticmethod
    def get_marketplace_timeout() -> float:
        try:
            return float(os.getenv("MARKETPLACE_TIMEOUT", "6"))
        except Exception:
            return 6.0

    @staticmethod
    def get_marketplace_repo() -> str:
        repo = os.getenv("MARKETPLACE_GITHUB_REPO", "").strip()
        if repo:
            return repo.strip("/")
        return os.getenv("GITHUB_REPO", "").strip().strip("/")

    @staticmethod
    def get_marketplace_repo_url() -> str:
        repo = AppConfig.get_marketplace_repo()
        return f"https://github.com/{repo}" if repo else ""

    @staticmethod
    def get_marketplace_github_token() -> str:
        token = os.getenv("MARKETPLACE_GITHUB_TOKEN", "").strip()
        if token:
            return token

        token = os.getenv("GITHUB_TOKEN", "").strip()
        if token:
            return token

        return os.getenv("GH_TOKEN", "").strip()

    @staticmethod
    def get_marketplace_branch() -> str:
        return os.getenv("MARKETPLACE_GITHUB_BRANCH", "main").strip() or "main"

    @staticmethod
    def get_marketplace_index_path() -> str:
        return os.getenv("MARKETPLACE_INDEX_PATH", "config/marketplace.json").strip().lstrip("/") or "config/marketplace.json"

    @staticmethod
    def get_marketplace_issue_template() -> str:
        return os.getenv("MARKETPLACE_ISSUE_TEMPLATE", "marketplace_submission.md").strip()

    @staticmethod
    def get_marketplace_issues_api_url() -> str:
        explicit_url = os.getenv("MARKETPLACE_ISSUES_API_URL", "").strip()
        if explicit_url:
            return explicit_url

        repo = AppConfig.get_marketplace_repo()
        if not repo:
            return ""
        return f"https://api.github.com/repos/{repo}/issues?state=open&per_page=100"

    @staticmethod
    def get_marketplace_issues_web_url() -> str:
        explicit_url = os.getenv("MARKETPLACE_ISSUES_WEB_URL", "").strip()
        if explicit_url:
            return explicit_url

        repo_url = AppConfig.get_marketplace_repo_url()
        if not repo_url:
            return ""
        return f"{repo_url}/issues?q=is%3Aissue+is%3Aopen"

    @staticmethod
    def get_marketplace_submit_mode() -> str:
        mode = os.getenv("MARKETPLACE_SUBMIT_MODE", "auto").strip().lower()
        if mode == "local":
            return "local"
        if mode in ("external", "redirect", "github", "public"):
            return "external"
        return "external" if AppConfig.get_marketplace_upload_url() else "local"

    @staticmethod
    def is_marketplace_pending_enabled() -> bool:
        raw_value = os.getenv("MARKETPLACE_PENDING_ENABLED", "").strip().lower()
        if raw_value in ("true", "1", "yes", "on"):
            return True
        if raw_value in ("false", "0", "no", "off"):
            return False
        return bool(AppConfig.get_marketplace_issues_api_url())

    @staticmethod
    def is_marketplace_local_overlay_enabled() -> bool:
        raw_value = os.getenv("MARKETPLACE_LOCAL_OVERLAY_ENABLED", "").strip().lower()
        if raw_value in ("true", "1", "yes", "on"):
            return True
        if raw_value in ("false", "0", "no", "off"):
            return False
        return not bool(AppConfig.get_marketplace_index_url())
    
    # ===== 便捷属性（支持类/实例两种访问方式）=====
    @classproperty
    def HOST(cls) -> str:
        return cls.get_host()

    @classproperty
    def PORT(cls) -> int:
        return cls.get_port()

    @classproperty
    def DEBUG(cls) -> bool:
        return cls.is_debug()

    @classproperty
    def LOG_LEVEL(cls) -> str:
        return cls.get_log_level()

    @classproperty
    def AUTH_TOKEN(cls) -> str:
        return cls.get_auth_token()


# 创建全局配置实例
app_config = AppConfig()


# ================= 浏览器常量配置（JSON文件）=================

class BrowserConstants:
    """浏览器相关常量（从 JSON 文件加载，支持热重载）"""
    
    # ===== 配置缓存 =====
    _config: Optional[Dict] = None
    _config_file = Path("config/browser_config.json")
    
    # ===== 默认值字典 =====
    _DEFAULTS = {
        'DEFAULT_PORT': 9222,
        'CONNECTION_TIMEOUT': 10,
        'STEALTH_DELAY_MIN': 0.1,
        'STEALTH_DELAY_MAX': 0.3,
        'ACTION_DELAY_MIN': 0.15,
        'ACTION_DELAY_MAX': 0.3,
        'DEFAULT_ELEMENT_TIMEOUT': 3,
        'FALLBACK_ELEMENT_TIMEOUT': 1,
        'ELEMENT_CACHE_MAX_AGE': 5.0,
        'LOG_INFO_CUTE_MODE': False,
        'LOG_DEBUG_CUTE_MODE': False,
        'STREAM_CHECK_INTERVAL_MIN': 0.1,
        'STREAM_CHECK_INTERVAL_MAX': 1.0,
        'STREAM_CHECK_INTERVAL_DEFAULT': 0.3,
        'STREAM_SILENCE_THRESHOLD': 6.0,
        'STREAM_MAX_TIMEOUT': 600,
        'STREAM_INITIAL_WAIT': 180,
        'STREAM_CONTENT_SHRINK_TOLERANCE': 3,
        'STREAM_STABLE_COUNT_THRESHOLD': 5,
        'STREAM_SILENCE_THRESHOLD_FALLBACK': 10.0,
        'MAX_MESSAGE_LENGTH': 100000,
        'MAX_MESSAGES_COUNT': 100,
        'STREAM_USER_MSG_WAIT': 1.5,
        'STREAM_PRE_BASELINE_DELAY': 0.3,
        'GLOBAL_NETWORK_INTERCEPTION_ENABLED': False,
        'GLOBAL_NETWORK_INTERCEPTION_LISTEN_PATTERN': 'http',
        'GLOBAL_NETWORK_INTERCEPTION_WAIT_TIMEOUT': 0.5,
        'GLOBAL_NETWORK_INTERCEPTION_RETRY_DELAY': 1.0,
        'NETWORK_DEBUG_CAPTURE_ENABLED': False,
        'NETWORK_DEBUG_CAPTURE_MAX_BODY_CHARS': 200000,
        'NETWORK_DEBUG_CAPTURE_PARSER_FILTER': '',
    }
    
    # ===== 类属性（会被配置文件覆盖）=====
    
    # 连接配置
    DEFAULT_PORT = 9222
    CONNECTION_TIMEOUT = 10
    
    # 延迟配置
    STEALTH_DELAY_MIN = 0.1
    STEALTH_DELAY_MAX = 0.3
    ACTION_DELAY_MIN = 0.15
    ACTION_DELAY_MAX = 0.3
    
    # 元素查找
    DEFAULT_ELEMENT_TIMEOUT = 3
    FALLBACK_ELEMENT_TIMEOUT = 1
    ELEMENT_CACHE_MAX_AGE = 5.0

    # 日志
    LOG_INFO_CUTE_MODE = False
    LOG_DEBUG_CUTE_MODE = False
    
    # 流式监控
    STREAM_CHECK_INTERVAL_MIN = 0.1
    STREAM_CHECK_INTERVAL_MAX = 1.0
    STREAM_CHECK_INTERVAL_DEFAULT = 0.3
    
    STREAM_SILENCE_THRESHOLD = 6.0
    STREAM_MAX_TIMEOUT = 600
    STREAM_INITIAL_WAIT = 180
    
    # 流式监控增强配置
    STREAM_CONTENT_SHRINK_TOLERANCE = 3
    
    STREAM_STABLE_COUNT_THRESHOLD = 5
    STREAM_SILENCE_THRESHOLD_FALLBACK = 10.0
    
    # 输入验证
    MAX_MESSAGE_LENGTH = 100000
    MAX_MESSAGES_COUNT = 100
    
    # 两阶段 baseline 配置
    STREAM_USER_MSG_WAIT = 1.5
    STREAM_PRE_BASELINE_DELAY = 0.3

    # 全局常驻网络监听（仅事件上报）
    GLOBAL_NETWORK_INTERCEPTION_ENABLED = False
    GLOBAL_NETWORK_INTERCEPTION_LISTEN_PATTERN = "http"
    GLOBAL_NETWORK_INTERCEPTION_WAIT_TIMEOUT = 0.5
    GLOBAL_NETWORK_INTERCEPTION_RETRY_DELAY = 1.0

    # 网络解析调试捕获
    NETWORK_DEBUG_CAPTURE_ENABLED = False
    NETWORK_DEBUG_CAPTURE_MAX_BODY_CHARS = 200000
    NETWORK_DEBUG_CAPTURE_PARSER_FILTER = ""

    @classmethod
    def _load_config(cls):
        """从文件加载配置"""
        if cls._config_file.exists():
            try:
                with open(cls._config_file, 'r', encoding='utf-8') as f:
                    cls._config = json.load(f)
                return
            except Exception as e:
                print(f"[BrowserConstants] 加载配置失败: {e}")
        
        # 加载失败或文件不存在，使用默认值
        cls._config = cls._DEFAULTS.copy()
    
    @classmethod
    def _apply_to_class_attrs(cls):
        """将配置值应用到类属性（兼容旧代码直接访问类属性的方式）"""
        if cls._config is None:
            cls._load_config()
        
        for key, value in cls._config.items():
            if hasattr(cls, key):
                setattr(cls, key, value)
        
        # 同步环境变量中的浏览器端口
        env_port = AppConfig.get_browser_port()
        if env_port:
            cls.DEFAULT_PORT = env_port
    
    @classmethod
    def get(cls, key: str):
        """获取配置值（支持动态加载）"""
        if cls._config is None:
            cls._load_config()
        
        return cls._config.get(key, cls._DEFAULTS.get(key))
    
    @classmethod
    def get_defaults(cls) -> Dict:
        """获取所有默认值"""
        return cls._DEFAULTS.copy()
    
    @classmethod
    def reload(cls):
        """重新加载配置（热重载）"""
        cls._config = None
        cls._load_config()
        cls._apply_to_class_attrs()


# ================= 安全日志配置 =================

# ================= 日志收集器（供前端展示）=================

class LogCollector:
    """收集日志用于前端展示"""

    def __init__(self, max_logs: int = 1200):
        self.logs: deque = deque(maxlen=max_logs)
        self.lock = threading.Lock()
        self._next_seq = 1

    def add(self, entry: Dict[str, Any]):
        with self.lock:
            payload = dict(entry or {})
            payload["seq"] = self._next_seq
            payload.setdefault("timestamp", time.time())
            payload.setdefault("level", "INFO")
            payload.setdefault("kind", payload["level"])
            payload.setdefault("message", "")
            payload.setdefault("display_message", payload["message"])
            payload.setdefault("message_text", payload["message"])
            payload.setdefault("original_message_text", payload["message_text"])
            payload.setdefault("message_alias", "")
            payload.setdefault("logger", "")
            payload.setdefault("request_id", "SYSTEM")
            self.logs.append(payload)
            self._next_seq += 1

    def get_recent(self, since: float = 0, after_seq: int = 0) -> tuple:
        with self.lock:
            cursor = max(0, int(after_seq or 0))
            if cursor > 0:
                logs = [log for log in self.logs if int(log.get("seq", 0) or 0) > cursor]
            else:
                logs = [log for log in self.logs if float(log.get("timestamp", 0) or 0) > since]
            return logs, self._next_seq - 1

    def clear(self):
        with self.lock:
            self.logs.clear()


# 全局日志收集器实例
log_collector = LogCollector()


class _WebLogHandler(logging.Handler):
    """将日志发送到 Web 收集器（内部类）"""

    def emit(self, record):
        try:
            msg = self.format(record)
            raw_message = str(getattr(record, "codex_message", "") or "")
            if not raw_message:
                raw_message = str(record.getMessage() or msg)
            message_text = str(getattr(record, "codex_display_message_text", "") or "")
            original_message_text = str(
                getattr(record, "codex_original_message_text", "") or raw_message
            )
            parts = str(msg).split(" │ ", 3)
            if not message_text and len(parts) == 4 and parts[3].strip():
                message_text = parts[3]
            if not message_text:
                message_text = raw_message
            logger_name = str(
                getattr(record, "codex_logger_name", "") or getattr(record, "name", "") or ""
            ).upper()[:8]
            request_id = str(getattr(record, "codex_request_id", "") or "SYSTEM")
            kind = str(getattr(record, "codex_kind", "") or record.levelname or "INFO").upper()
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

        # Windows Terminal / ANSICON / ConEmu 等环境通常已支持 ANSI，
        # 即使 sys.stdout.isatty() 或 GetConsoleMode 判断不稳定，也可直接输出颜色。
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
    COLORS = {
        "ERROR": "\033[31m",
        "WARN": "\033[33m",
        "KEY": "\033[94m",
        "INFO": "\033[92m",
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
        super().__init__("%(message)s")
        self._use_color = _should_use_console_color()

    def _resolve_tone(self, record: logging.LogRecord, message: str) -> Optional[str]:
        level = str(record.levelname or "").upper()
        if level in ("ERROR", "CRITICAL"):
            return "ERROR"
        if level == "WARNING":
            return "WARN"
        if level == "DEBUG":
            return None
        if any(pattern in message for pattern in self.KEY_PATTERNS):
            return "KEY"
        if level == "INFO":
            return "INFO"
        return None

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if not self._use_color:
            return message

        tone = self._resolve_tone(record, message)
        if not tone:
            return message

        color = self.COLORS.get(tone)
        if not color:
            return message

        return f"{color}{message}{self.RESET}"


# 创建全局 Web 日志处理器
_web_log_handler = _WebLogHandler()
_web_log_handler.setLevel(logging.DEBUG)
_web_log_handler.setFormatter(logging.Formatter('%(message)s'))


def _get_positive_int_env(name: str, default: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return value if value > 0 else default


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

            handler = RotatingFileHandler(
                log_file,
                maxBytes=_get_positive_int_env("LOG_MAX_BYTES", 5 * 1024 * 1024),
                backupCount=_get_positive_int_env("LOG_BACKUP_COUNT", 5),
                encoding="utf-8",
            )
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(
                logging.Formatter(
                    '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
                    '%Y-%m-%d %H:%M:%S',
                )
            )
            _shared_file_log_handler = handler
        except Exception as e:
            try:
                print(f"[Config] failed to initialize file logging: {e}", file=sys.stderr)
            except Exception:
                pass
            _shared_file_log_handler = None

        return _shared_file_log_handler


_REQUEST_FINISH_PATTERN = re.compile(r"^完成 \(([\d.]+)s\)$")
_TAB_START_INDEX_PATTERN = re.compile(r"^开始 \(标签页 #(\d+)\)$")
_TAB_START_ROUTE_PATTERN = re.compile(r"^开始 \(域名路由 (.+)\)$")
_CHUNKED_LONG_TEXT_PATTERN = re.compile(
    r"^\[CHUNKED_INPUT\] 长文本模式: (\d+) 字符，分块大小 (\d+)，预计 (\d+) 块$"
)
_CHUNKED_DONE_PATTERN = re.compile(
    r"^\[CHUNKED_INPUT\] 全部完成: (\d+) 块，共 (\d+) 字符$"
)
_CHUNKED_SHORT_TEXT_PATTERN = re.compile(
    r"^\[CHUNKED_INPUT\] 短文本模式: (\d+) 字符，直接写入$"
)
_CHUNKED_FIRST_BLOCK_PATTERN = re.compile(
    r"^\[CHUNKED_INPUT\] 首块完成: 1/(\d+) \(chars=0-(\d+)\)$"
)
_CHUNKED_PROGRESS_PATTERN = re.compile(
    r"^\[CHUNKED_INPUT\] 进度 (\d+)/(\d+) \((\d+)%, chars=(\d+)-(\d+)\)$"
)
_VERIFY_OK_EXACT_PATTERN = re.compile(
    r"^\[VERIFY_OK\] attempt=(\d+) len=(\d+) \(exact match\)$"
)
_SEND_SUCCESS_RETRY_PATTERN = re.compile(r"^发送成功 \(重试([\d.]+)s\)$")
_FILE_PASTE_DONE_PATTERN = re.compile(r"^\[FILE_PASTE\] 文件粘贴完成 \((\d+) 字符\)$")
_CLIPBOARD_OK_PATTERN = re.compile(r"^\[CLIPBOARD_OK\] 粘贴成功，长度 (\d+)$")
_NETWORK_OUTPUT_CHUNK_PATTERN = re.compile(
    r"^\[NetworkMonitor\] 输出片段 #(\d+) \(len=(\d+), total_chars=(\d+), preview=(.+)\)$"
)
_FILE_PASTE_UPLOAD_SIGNAL_PATTERN = re.compile(
    r"^\[FILE_PASTE\] 检测到文件上传信号 "
    r"\(file_count=(\d+), matched_name=(True|False), "
    r"matched_file_node=(True|False), file_node_count=(\d+)\)$"
)
_FILE_PASTE_WEAK_SIGNAL_PATTERN = re.compile(
    r"^\[FILE_PASTE\] 检测到弱上传信号，继续等待强信号 "
    r"\(matched_name=(True|False), file_node_count=(\d+), "
    r"pending=(\d+), pending_text=(True|False)\)$"
)
_FILE_PASTE_INPUT_SIGNAL_PATTERN = re.compile(
    r"^\[FILE_PASTE\] file input #(\d+) 已触发页面附件信号，"
    r"视为上传成功 \(selector=(.+)\)$"
)
_FILE_PASTE_INPUT_UPLOADED_PATTERN = re.compile(
    r"^\[FILE_PASTE\] 已通过 file input 上传文件 \(candidate=(\d+), files=(\d+)\)$"
)
_FILE_PASTE_HINT_PATTERN = re.compile(r"^\[FILE_PASTE\] 追加引导文本: (.+)$")
_FILE_PASTE_TEMP_FILE_PATTERN = re.compile(r"^\[FILE_PASTE\] 临时文件: (.+)$")
_STEALTH_SEND_RETRY_PATTERN = re.compile(
    r"^\[STEALTH\] 发送重试 #(\d+) \(elapsed=([\d.]+)s\)$"
)
_STEALTH_REVIEW_DELAY_PATTERN = re.compile(
    r"^\[STEALTH\] 粘贴后阅读延迟 ([\d.]+)s \(文本长度=(\d+)\)$"
)
_STEALTH_CLIPBOARD_PASTE_NOCLICK_PATTERN = re.compile(
    r"^\[STEALTH\] 使用剪贴板粘贴（无click），长度 (\d+)$"
)
_STEALTH_CLIPBOARD_PASTE_PATTERN = re.compile(
    r"^\[STEALTH\] 使用剪贴板粘贴，长度 (\d+)$"
)
_STEALTH_RANDOM_PAUSE_PATTERN = re.compile(r"^\[STEALTH\] 随机停顿 \+([\d.]+)s$")
_STEALTH_PRE_SEND_HESITATE_PATTERN = re.compile(
    r"^\[STEALTH\] 发送前犹豫 ([\d.]+)s$"
)
_STEALTH_HUMAN_CLICK_PATTERN = re.compile(
    r"^\[STEALTH\] 人类化点击完成: \(([-\d]+), ([-\d]+)\)$"
)
_SEND_ATTACHMENT_WAIT_PATTERN = re.compile(
    r"^\[SEND\] 检测到附件仍在处理，发送前等待 "
    r"\(attachments=(\d+), pending=(\d+), send_disabled=(True|False)\)$"
)
_SEND_ATTACHMENT_READY_PATTERN = re.compile(
    r"^\[SEND\] 附件已就绪，继续发送 \(waited=([\d.]+)s, attachments=(\d+)\)$"
)
_SEND_ATTACHMENT_SETTLE_PATTERN = re.compile(
    r"^\[SEND\] 附件刚上传完成，额外等待解析稳定 ([\d.]+)s$"
)


def _bool_phrase(raw_value: str, true_text: str, false_text: str) -> str:
    return true_text if str(raw_value).strip().lower() == "true" else false_text


def _split_suppressed_suffix(text: str) -> tuple[str, int]:
    raw_text = str(text or "")
    match = re.match(r"^(.*) \(suppressed=(\d+)\)$", raw_text, re.S)
    if not match:
        return raw_text, 0
    return str(match.group(1) or ""), int(match.group(2) or 0)


def _restore_suppressed_hint(text: str, suppressed_count: int) -> str:
    if suppressed_count <= 0 or not text:
        return text
    return f"{text}（刚刚先按住了 {suppressed_count} 条重复日志喵）"


def _cuteify_info_message(logger_name: str, message_text: str) -> str:
    text = str(message_text or "")
    name = str(logger_name or "").upper().strip()
    if not text or not bool(BrowserConstants.get("LOG_INFO_CUTE_MODE")):
        return text

    if name == "REQUEST":
        if text == "创建":
            return "请求已经开始创建了喵"
        finish_match = _REQUEST_FINISH_PATTERN.match(text)
        if finish_match:
            return f"这个请求已经圆满完成了喵（耗时 {finish_match.group(1)}s）"

    if name == "API.CHAT" and text == "开始":
        return "后端同意了请求并开始执行工作流了喵"

    if name == "API.TAB":
        index_match = _TAB_START_INDEX_PATTERN.match(text)
        if index_match:
            return f"后端同意了请求喵，准备在标签页 #{index_match.group(1)} 开始执行工作流了喵"
        route_match = _TAB_START_ROUTE_PATTERN.match(text)
        if route_match:
            return f"后端同意了请求喵，准备按域名路由 {route_match.group(1)} 开始执行工作流了喵"

    long_text_match = _CHUNKED_LONG_TEXT_PATTERN.match(text)
    if long_text_match:
        total_len, chunk_size, total_chunks = long_text_match.groups()
        return (
            "是长文本模式喵，开始分块了喵"
            f"（{total_len} 字符，分块大小 {chunk_size}，预计 {total_chunks} 块）"
        )

    chunked_done_match = _CHUNKED_DONE_PATTERN.match(text)
    if chunked_done_match:
        total_chunks, total_len = chunked_done_match.groups()
        return f"都分好了喵，一共 {total_chunks} 块，共 {total_len} 字符喵"

    verify_exact_match = _VERIFY_OK_EXACT_PATTERN.match(text)
    if verify_exact_match:
        attempt, length = verify_exact_match.groups()
        return f"输入内容已经核对通过了喵（第 {attempt} 次尝试，长度 {length}，完全一致）"
    if text == "[VERIFY_OK] 最终检查通过 (normalized)":
        return "输入内容最后检查也通过了喵（规范化后完全对上啦）"
    if text == "[VERIFY_OK] 最终检查通过 (rich editor core match)":
        return "输入内容最后检查也通过了喵（富文本核心内容已经对上啦）"

    file_paste_done_match = _FILE_PASTE_DONE_PATTERN.match(text)
    if file_paste_done_match:
        return f"文件已经贴好啦喵（正文 {file_paste_done_match.group(1)} 字符）"
    if text == "[FILE_PASTE] 已点击上传按钮":
        return "上传按钮已经帮你点好了喵"
    if text == "[FILE_PASTE] 已通过拖拽区域上传文件":
        return "文件已经从拖拽区域稳稳投递上去了喵"

    clipboard_ok_match = _CLIPBOARD_OK_PATTERN.match(text)
    if clipboard_ok_match:
        return f"剪贴板内容已经稳稳贴好了喵（长度 {clipboard_ok_match.group(1)}）"
    if text == "[CLIPBOARD_OK] 重试成功":
        return "剪贴板这次重试成功啦喵"
    if text == "[CLIPBOARD_OK] 重试成功（富文本匹配）":
        return "剪贴板这次重试成功啦喵（富文本内容也对上了）"

    if text == "浏览器连接成功":
        return "浏览器已经顺利连上了喵"
    if text == "关闭浏览器连接":
        return "浏览器连接准备收工了喵"
    if text == "发送成功":
        return "消息已经顺利发出去啦喵"
    if text == "发送成功（附件场景，已避免重复点击发送按钮）":
        return "附件场景也顺利发出去了喵，而且乖乖避开了重复点击发送按钮"
    send_retry_match = _SEND_SUCCESS_RETRY_PATTERN.match(text)
    if send_retry_match:
        return f"消息补发成功啦喵（等了 {send_retry_match.group(1)}s 终于发出去了）"

    return text


def _cuteify_debug_message(logger_name: str, message_text: str) -> str:
    raw_text = str(message_text or "")
    if not raw_text or not bool(BrowserConstants.get("LOG_DEBUG_CUTE_MODE")):
        return raw_text

    text, suppressed_count = _split_suppressed_suffix(raw_text)

    def cute(alias: str) -> str:
        return _restore_suppressed_hint(alias, suppressed_count)

    short_text_match = _CHUNKED_SHORT_TEXT_PATTERN.match(text)
    if short_text_match:
        return cute(f"这次文本不长喵，小鹿一口气就写进去了喵（{short_text_match.group(1)} 字符）")

    first_block_match = _CHUNKED_FIRST_BLOCK_PATTERN.match(text)
    if first_block_match:
        total_chunks, first_end = first_block_match.groups()
        return cute(f"小鹿开始分块啦，第一块先放好了喵（1/{total_chunks}，字符 0-{first_end}）")

    chunked_progress_match = _CHUNKED_PROGRESS_PATTERN.match(text)
    if chunked_progress_match:
        current_chunk, total_chunks, progress_pct, start_pos, end_pos = chunked_progress_match.groups()
        return cute(
            f"小鹿继续分块中喵，现在到第 {current_chunk}/{total_chunks} 块啦"
            f"（{progress_pct}%，字符 {start_pos}-{end_pos}）"
        )

    verify_pass_match = re.match(r"^输入验证通过 \(len=(\d+), diff=([+-]\d+)\)$", text)
    if verify_pass_match:
        actual_len, diff = verify_pass_match.groups()
        return cute(f"小鹿核对过输入啦，长度 {actual_len}，和目标差了 {diff} 个字符喵")

    verify_rich_match = re.match(
        r"^\[VERIFY_OK\] attempt=(\d+) len=(\d+) "
        r"\(rich editor core match, diff=([+-]\d+) chars\)$",
        text,
    )
    if verify_rich_match:
        attempt, actual_len, diff = verify_rich_match.groups()
        return cute(f"小鹿在第 {attempt} 次核对时确认富文本核心已经对上啦（长度 {actual_len}，差值 {diff}）")

    verify_fail_match = re.match(
        r"^\[VERIFY_FAIL\] attempt=(\d+) actual_len=(\d+) expected_len=(\d+) "
        r"mismatch_at=(-?\d+) is_rich=(True|False)",
        text,
        re.S,
    )
    if verify_fail_match:
        attempt, actual_len, expected_len, mismatch_at, is_rich = verify_fail_match.groups()
        return cute(
            f"小鹿发现输入还没完全对齐喵（第 {attempt} 次，当前 {actual_len}，目标 {expected_len}，"
            f"最早差异在 {mismatch_at}，富文本模式={is_rich}）"
        )

    verify_retry_match = re.match(r"^\[VERIFY\] attempt=(\d+) 原子写入返回 False，尝试备用方案$", text)
    if verify_retry_match:
        return cute(f"小鹿发现第 {verify_retry_match.group(1)} 次原子写入没成喵，准备切备用方案")

    input_snapshot_match = re.match(
        r"^\[INPUT_SNAPSHOT\] len=(\d+) nl=(\d+) head=(.+)\.\.\. tail=\.\.\.(.+)$",
        text,
        re.S,
    )
    if input_snapshot_match:
        text_len, nl_count, head_preview, tail_preview = input_snapshot_match.groups()
        return cute(
            f"小鹿偷看了一眼输入框现状喵（长度 {text_len}，换行 {nl_count}，"
            f"开头 {head_preview}，结尾 {tail_preview}）"
        )

    if text == "JS 备用方案返回 false":
        return cute("小鹿试了 JS 备用写法，但这条路也没走通喵")
    if text == "JS 分块输入遇到问题，准备进行后续修正...":
        return cute("小鹿发现分块输入有点卡壳喵，准备开始补救修正")

    physical_activate_match = re.match(r"^物理激活异常（可忽略）: (.+)$", text, re.S)
    if physical_activate_match:
        return cute(f"小鹿刚才想顺手激活输入框时绊了一下喵（可忽略：{physical_activate_match.group(1)}）")

    if text == "[NetworkMonitor] 监听被取消":
        return cute("小鹿收到取消信号啦，这轮监听先停下喵")

    network_init_match = re.match(
        r"^\[NetworkMonitor\] 初始化完成 \(pattern=(.+), parser=(.+)\)$",
        text,
    )
    if network_init_match:
        listen_pattern, parser_name = network_init_match.groups()
        return cute(f"小鹿把网络监听器准备好了喵（目标 {listen_pattern}，解析器 {parser_name}）")

    network_body_ready_match = re.match(
        r"^\[NetworkMonitor\] 流响应正文已就绪 \(source=(.+), size=(\d+) chars\)$",
        text,
    )
    if network_body_ready_match:
        source_name, body_size = network_body_ready_match.groups()
        return cute(f"小鹿等到响应正文冒出来啦（来源 {source_name}，长度 {body_size}）")

    network_body_growth_match = re.match(
        r"^\[NetworkMonitor\] 流响应继续增长 \(source=(.+), size=(\d+) chars\)$",
        text,
    )
    if network_body_growth_match:
        source_name, body_size = network_body_growth_match.groups()
        return cute(f"小鹿看到响应还在继续长大喵（来源 {source_name}，现在 {body_size} 字符）")

    network_prestart_match = re.match(
        r"^\[NetworkMonitor\] 发送前启动监听 - 复用模式 \(pattern=(.+)\)$",
        text,
    )
    if network_prestart_match:
        return cute(f"小鹿已经提前把监听架好啦（目标 {network_prestart_match.group(1)}）")

    network_silence_match = re.match(r"^\[NetworkMonitor\] 静默超时 \(([\d.]+)s\)，结束监听$", text)
    if network_silence_match:
        return cute(f"小鹿等了 {network_silence_match.group(1)}s 都没新动静喵，准备收监听了")

    network_non_target_match = re.match(
        r"^\[NetworkMonitor\] 非流式目标响应，跳过解析 \(count=(\d+), url=(.*)\)$",
        text,
    )
    if network_non_target_match:
        skip_count, url = network_non_target_match.groups()
        return cute(f"小鹿又排除了一条无关响应喵（累计跳过 {skip_count} 条，地址 {url}）")

    network_target_hit_match = re.match(
        r"^\[NetworkMonitor\] 命中流目标 \(status=(.*), method=(.*), url=(.*), count=(\d+)\)$",
        text,
    )
    if network_target_hit_match:
        status, method, url, hit_count = network_target_hit_match.groups()
        return cute(f"小鹿再次命中目标流啦（第 {hit_count} 次，状态 {status}，方法 {method}，地址 {url}）")

    network_empty_body_match = re.match(
        r"^\[NetworkMonitor\] 响应体为空，跳过 \(count=(\d+), stream=(True|False), source=(.+)\)$",
        text,
    )
    if network_empty_body_match:
        skip_count, is_stream, source_name = network_empty_body_match.groups()
        return cute(f"小鹿这次捞到的响应体还是空的喵（第 {skip_count} 次，流式={is_stream}，来源 {source_name}）")

    network_body_captured_match = re.match(
        r"^\[NetworkMonitor\] 捕获响应 "
        r"\(responses=(\d+), targets=(\d+), source=(.+), size=(\d+) chars\)$",
        text,
    )
    if network_body_captured_match:
        response_count, target_count, source_name, body_size = network_body_captured_match.groups()
        return cute(
            f"小鹿已经把这次响应内容捞上来了喵（总响应 {response_count}，目标命中 {target_count}，"
            f"来源 {source_name}，长度 {body_size}）"
        )

    if text == "[NetworkMonitor] 已捕获到首次响应":
        return cute("小鹿先蹲到了第一条网络响应喵")
    if text == "[NetworkMonitor] event-only 已捕获到首个网络事件":
        return cute("小鹿先抓到第一条网络事件喵")
    if text == "[NetworkMonitor] 已捕获到首个流目标响应":
        return cute("小鹿已经锁定第一个目标流响应啦喵")
    if text == "[NetworkMonitor] 已捕获到首个有效流响应":
        return cute("小鹿确认第一条有效流响应到啦喵")
    if text == "[NetworkMonitor] 检测到结束标志，完成监听":
        return cute("小鹿看到结束标记啦，这轮监听可以收尾了喵")

    network_chunk_match = _NETWORK_OUTPUT_CHUNK_PATTERN.match(text)
    if network_chunk_match:
        chunk_no, chunk_len, total_chars, preview = network_chunk_match.groups()
        return cute(
            f"小鹿又叼来一段响应啦（第 {chunk_no} 段，本段 {chunk_len} 字符，"
            f"累计 {total_chars} 字符，预览“{preview}”）"
        )

    file_paste_signal_match = _FILE_PASTE_UPLOAD_SIGNAL_PATTERN.match(text)
    if file_paste_signal_match:
        file_count, matched_name, matched_file_node, file_node_count = file_paste_signal_match.groups()
        return cute(
            "小鹿看到文件已经冒出明显上传信号啦"
            f"（文件计数 {file_count}，名字匹配{_bool_phrase(matched_name, '命中', '未命中')}，"
            f"文件节点{_bool_phrase(matched_file_node, '已对上', '还没对上')}，"
            f"页面文件节点 {file_node_count} 个）"
        )

    file_paste_weak_signal_match = _FILE_PASTE_WEAK_SIGNAL_PATTERN.match(text)
    if file_paste_weak_signal_match:
        matched_name, file_node_count, pending_count, pending_text = file_paste_weak_signal_match.groups()
        return cute(
            "小鹿先闻到一点上传动静喵，继续等它完全准备好"
            f"（名字匹配{_bool_phrase(matched_name, '命中', '未命中')}，"
            f"页面文件节点 {file_node_count} 个，待处理 {pending_count} 个，"
            f"等待提示{_bool_phrase(pending_text, '已经出现', '还没出现')}）"
        )

    file_paste_find_fail_match = re.match(r"^\[FILE_PASTE\] 查找元素失败 (.+): (.+)$", text, re.S)
    if file_paste_find_fail_match:
        selector, error_text = file_paste_find_fail_match.groups()
        return cute(f"小鹿去找目标元素时扑了个空喵（选择器 {selector}，原因 {error_text}）")

    file_paste_signal_fail_match = re.match(r"^\[FILE_PASTE\] 检查文件上传信号失败: (.+)$", text, re.S)
    if file_paste_signal_fail_match:
        return cute(f"小鹿刚才没看清上传信号喵（{file_paste_signal_fail_match.group(1)}）")

    if text == "[FILE_PASTE] 已配置 upload_btn，但当前页面未找到":
        return cute("小鹿知道这里应该有上传按钮喵，但这次页面上没看见它")

    file_paste_click_fail_match = re.match(r"^\[FILE_PASTE\] 点击上传按钮失败: (.+)$", text, re.S)
    if file_paste_click_fail_match:
        return cute(f"小鹿点上传按钮时手滑了一下喵（{file_paste_click_fail_match.group(1)}）")

    file_paste_input_list_fail_match = re.match(r"^\[FILE_PASTE\] 查找通用 file input 失败: (.+)$", text, re.S)
    if file_paste_input_list_fail_match:
        return cute(f"小鹿翻通用 file input 时遇到阻碍喵（{file_paste_input_list_fail_match.group(1)}）")

    if text == "[FILE_PASTE] 当前没有可用的 file input":
        return cute("小鹿这次没找到能直接塞文件的 input 入口喵")

    file_paste_input_signal_match = _FILE_PASTE_INPUT_SIGNAL_PATTERN.match(text)
    if file_paste_input_signal_match:
        input_index, selector = file_paste_input_signal_match.groups()
        return cute(f"小鹿发现第 {input_index} 个上传入口已经把附件挂上页面啦（入口 {selector}）")

    file_paste_input_not_ready_match = re.match(
        r"^\[FILE_PASTE\] file input #(\d+) 未真正挂载文件 \(selector=(.+)\)$",
        text,
    )
    if file_paste_input_not_ready_match:
        input_index, selector = file_paste_input_not_ready_match.groups()
        return cute(f"小鹿试了第 {input_index} 个上传入口喵，但文件还没真正挂上去（入口 {selector}）")

    file_paste_uploaded_match = _FILE_PASTE_INPUT_UPLOADED_PATTERN.match(text)
    if file_paste_uploaded_match:
        candidate_index, file_count = file_paste_uploaded_match.groups()
        return cute(
            f"小鹿已经通过第 {candidate_index} 个上传入口把文件送上去了喵"
            f"（这次挂上了 {file_count} 个文件）"
        )

    file_paste_input_fail_match = re.match(r"^\[FILE_PASTE\] file input #(\d+) 上传失败: (.+)$", text, re.S)
    if file_paste_input_fail_match:
        input_index, error_text = file_paste_input_fail_match.groups()
        return cute(f"小鹿操作第 {input_index} 个上传入口时翻车了喵（{error_text}）")

    file_paste_hint_match = _FILE_PASTE_HINT_PATTERN.match(text)
    if file_paste_hint_match:
        return cute(f"小鹿顺手补了一句提示语喵（{file_paste_hint_match.group(1)}）")

    file_paste_temp_file_match = _FILE_PASTE_TEMP_FILE_PATTERN.match(text)
    if file_paste_temp_file_match:
        return cute(f"小鹿先把长文本装进临时小纸条里啦（{file_paste_temp_file_match.group(1)}）")

    if text == "[FILE_PASTE] 已通过 CDP 原生拖拽投递文件":
        return cute("小鹿已经把文件稳稳拖进目标区域啦")

    file_paste_drop_coord_match = re.match(r"^\[FILE_PASTE\] 读取 drop zone 坐标失败: (.+)$", text, re.S)
    if file_paste_drop_coord_match:
        return cute(f"小鹿没读到拖拽区域坐标喵（{file_paste_drop_coord_match.group(1)}）")

    if text == "[FILE_PASTE] drop zone 坐标无效，跳过原生拖拽":
        return cute("小鹿量出来的拖拽落点不太靠谱喵，这次先跳过原生拖拽")
    if text == "[FILE_PASTE] 已配置 drop_zone，但当前页面未找到":
        return cute("小鹿知道这里应该有拖拽区域喵，但页面上没找到")

    file_paste_drag_fail_match = re.match(r"^\[FILE_PASTE\] CDP 原生拖拽失败: (.+)$", text, re.S)
    if file_paste_drag_fail_match:
        return cute(f"小鹿原生拖文件时被拦了一下喵（{file_paste_drag_fail_match.group(1)}）")

    file_paste_drop_zone_fail_match = re.match(r"^\[FILE_PASTE\] drop zone 上传失败: (.+)$", text, re.S)
    if file_paste_drop_zone_fail_match:
        return cute(f"小鹿往拖拽区域投文件时没成功喵（{file_paste_drop_zone_fail_match.group(1)}）")

    settle_wait_match = _SEND_ATTACHMENT_SETTLE_PATTERN.match(text)
    if settle_wait_match:
        return cute(f"小鹿看到附件刚安顿好喵，再等 {settle_wait_match.group(1)}s 让它稳定一下")

    send_attachment_wait_match = _SEND_ATTACHMENT_WAIT_PATTERN.match(text)
    if send_attachment_wait_match:
        attachments, pending, send_disabled = send_attachment_wait_match.groups()
        return cute(
            "小鹿发现附件还在忙喵，先等等再发送"
            f"（附件 {attachments} 个，待处理 {pending} 个，"
            f"发送按钮{_bool_phrase(send_disabled, '暂时点不了', '已经能点了')}）"
        )

    send_attachment_ready_match = _SEND_ATTACHMENT_READY_PATTERN.match(text)
    if send_attachment_ready_match:
        waited, attachments = send_attachment_ready_match.groups()
        return cute(f"小鹿确认附件已经准备好啦喵（等了 {waited}s，附件 {attachments} 个）")

    if text == "[SEND] 已通过网络监听捕获到发送后的目标流事件":
        return cute("小鹿已经盯到发送后的目标流事件啦喵")

    if text == "[STEALTH] 隐身模式已启用":
        return cute("小鹿已经切进隐身模式啦")

    stealth_retry_match = _STEALTH_SEND_RETRY_PATTERN.match(text)
    if stealth_retry_match:
        retry_count, elapsed = stealth_retry_match.groups()
        return cute(f"小鹿又悄悄帮你重试了一次发送喵（第 {retry_count} 次，已经等了 {elapsed}s）")

    stealth_clipboard_no_click_match = _STEALTH_CLIPBOARD_PASTE_NOCLICK_PATTERN.match(text)
    if stealth_clipboard_no_click_match:
        return cute(
            f"小鹿这次不点输入框，直接悄悄把内容贴上去啦"
            f"（长度 {stealth_clipboard_no_click_match.group(1)}）"
        )

    stealth_clipboard_match = _STEALTH_CLIPBOARD_PASTE_PATTERN.match(text)
    if stealth_clipboard_match:
        return cute(f"小鹿已经把内容轻轻贴进输入框啦（长度 {stealth_clipboard_match.group(1)}）")

    stealth_pause_match = _STEALTH_RANDOM_PAUSE_PATTERN.match(text)
    if stealth_pause_match:
        return cute(f"小鹿故意停顿了一小下喵（+{stealth_pause_match.group(1)}s）")

    stealth_hesitate_match = _STEALTH_PRE_SEND_HESITATE_PATTERN.match(text)
    if stealth_hesitate_match:
        return cute(f"小鹿发送前先犹豫了一小会儿喵（{stealth_hesitate_match.group(1)}s）")

    stealth_review_match = _STEALTH_REVIEW_DELAY_PATTERN.match(text)
    if stealth_review_match:
        delay_sec, text_len = stealth_review_match.groups()
        return cute(f"小鹿贴完内容后先装作认真看一眼喵（停留 {delay_sec}s，文本长度 {text_len}）")

    stealth_click_match = _STEALTH_HUMAN_CLICK_PATTERN.match(text)
    if stealth_click_match:
        click_x, click_y = stealth_click_match.groups()
        return cute(f"小鹿已经像人一样点下去啦（落点 {click_x}, {click_y}）")

    stealth_smooth_fail_match = re.match(r"^\[STEALTH\] 平滑移动异常（可忽略）: (.+)$", text, re.S)
    if stealth_smooth_fail_match:
        return cute(f"小鹿移动鼠标时轻轻绊了一下喵（可忽略：{stealth_smooth_fail_match.group(1)}）")

    stealth_coord_fallback_match = re.match(
        r"^\[STEALTH\] 原生属性获取坐标失败，JS getBoundingClientRect 获取: \(([-\d]+), ([-\d]+)\)$",
        text,
    )
    if stealth_coord_fallback_match:
        pos_x, pos_y = stealth_coord_fallback_match.groups()
        return cute(f"小鹿原本的坐标线索失效了喵，改走 JS 坐标兜底（{pos_x}, {pos_y}）")

    stealth_coord_fail_match = re.match(r"^\[STEALTH\] JS 坐标获取也失败: (.+)$", text, re.S)
    if stealth_coord_fail_match:
        return cute(f"小鹿连 JS 坐标也没拿到喵（{stealth_coord_fail_match.group(1)}）")

    stealth_verify_ok_match = re.match(
        r"^\[STEALTH_VERIFY\] 粘贴检查通过: actual=(\d+), expected=(\d+), ratio=([\d.]+)$",
        text,
    )
    if stealth_verify_ok_match:
        actual_len, expected_len, ratio = stealth_verify_ok_match.groups()
        return cute(f"小鹿悄悄核对过粘贴结果啦（实际 {actual_len}，目标 {expected_len}，匹配比例 {ratio}）")

    stealth_verify_skip_match = re.match(r"^\[STEALTH_VERIFY\] 检查跳过: (.+)$", text, re.S)
    if stealth_verify_skip_match:
        return cute(f"小鹿这次没继续深挖粘贴检查喵（{stealth_verify_skip_match.group(1)}）")

    if text == "[STEALTH] 跳过粘贴验证":
        return cute("小鹿这次跳过额外粘贴核对啦，继续往后走喵")
    if text == "[STEALTH] 跳过粘贴验证（STEALTH_SKIP_PASTE_VERIFY=true）":
        return cute("小鹿按配置跳过了额外粘贴核对喵，直接继续后面的动作")

    if text == "[STEALTH] 执行页面预热":
        return cute("小鹿先活动活动爪子喵，准备开始隐身操作啦")
    if text.startswith("[STEALTH] 页面预热完成（") and text.endswith(" 次移动）"):
        move_count = text.removeprefix("[STEALTH] 页面预热完成（").removesuffix(" 次移动）")
        return cute(f"小鹿热身完毕喵，一共晃了 {move_count} 次小步子")

    stealth_warmup_fail_match = re.match(r"^\[STEALTH\] 页面预热异常（可忽略）: (.+)$", text, re.S)
    if stealth_warmup_fail_match:
        return cute(f"小鹿热身时有点小插曲喵（可忽略：{stealth_warmup_fail_match.group(1)}）")

    send_probe_fail_match = re.match(r"^\[SEND\] 附件状态探测失败: (.+)$", text, re.S)
    if send_probe_fail_match:
        return cute(f"小鹿暂时没探明附件状态喵（{send_probe_fail_match.group(1)}）")

    send_post_state_fail_match = re.match(r"^\[SEND\] 发送后状态探测失败: (.+)$", text, re.S)
    if send_post_state_fail_match:
        return cute(f"小鹿发送后回头确认状态时没看清喵（{send_post_state_fail_match.group(1)}）")

    send_pre_read_fail_match = re.match(r"^\[SEND\] 网络活动预读失败: (.+)$", text, re.S)
    if send_pre_read_fail_match:
        return cute(f"小鹿预读网络动静时被打断了一下喵（{send_pre_read_fail_match.group(1)}）")

    executor_network_enabled_match = re.match(
        r"^\[Executor\] 网络监听器已启用 \(parser=(.+), listen_pattern=(.+)\)$",
        text,
    )
    if executor_network_enabled_match:
        parser_name, listen_pattern = executor_network_enabled_match.groups()
        return cute(f"小鹿把执行器的网络监听接上啦（解析器 {parser_name}，目标 {listen_pattern}）")

    executor_intercept_match = re.match(
        r"^\[Executor\] 网络异常拦截已启用（event-only） \(pattern=(.+)\)$",
        text,
    )
    if executor_intercept_match:
        return cute(f"小鹿把异常拦截监听也挂好了喵（目标 {executor_intercept_match.group(1)}）")

    extractor_match = re.match(r"^WorkflowExecutor 使用提取器: (.+)$", text)
    if extractor_match:
        return cute(f"小鹿这轮准备交给提取器 {extractor_match.group(1)} 来收尾喵")

    if text == "[IMAGE] 图片提取已启用":
        return cute("小鹿已经把图片提取路线也准备好了喵")

    kimi_register_match = re.match(r"^\[Executor\] Kimi 页面抓流已注册 document-start 注入: (.+)$", text)
    if kimi_register_match:
        return cute(f"小鹿已经把 Kimi 抓流注入挂到 document-start 啦（{kimi_register_match.group(1)}）")

    kimi_register_fail_match = re.match(r"^\[Executor\] Kimi document-start 注入失败: (.+)$", text, re.S)
    if kimi_register_fail_match:
        return cute(f"小鹿挂 Kimi 抓流注入时遇到点阻碍喵（{kimi_register_fail_match.group(1)}）")

    if text == "[Executor] Kimi 页面抓流被取消":
        return cute("小鹿这轮 Kimi 页面抓流先停下来了喵")
    if text == "[Executor] Kimi 页面抓流完成":
        return cute("小鹿确认 Kimi 页面抓流已经顺利收尾啦")
    if text == "[Executor] Kimi 页面抓流请求已结束但无有效内容":
        return cute("小鹿等到 Kimi 请求结束了喵，但这次没捞到有效内容")
    if text == "[Executor] 尝试 Kimi 页面抓流模式":
        return cute("小鹿准备切到 Kimi 页面抓流模式喵")
    if text == "[Executor] 尝试网络监听模式":
        return cute("小鹿准备切到常规网络监听模式喵")

    kimi_hit_match = re.match(
        r"^\[Executor\] Kimi 页面抓流已命中请求 \(request_id=(.+), token=(.+)\)$",
        text,
    )
    if kimi_hit_match:
        request_id, token = kimi_hit_match.groups()
        return cute(f"小鹿已经抓到 Kimi 那边的目标请求啦（request_id={request_id}, token={token}）")

    kimi_output_match = re.match(r"^\[Executor\] Kimi 页面抓流产出: (.+)$", text, re.S)
    if kimi_output_match:
        return cute(f"小鹿从 Kimi 页面抓流里叼出一段内容啦（预览 {kimi_output_match.group(1)}）")

    kimi_silence_match = re.match(r"^\[Executor\] Kimi 页面抓流静默超时 \(([\d.]+)s\)$", text)
    if kimi_silence_match:
        return cute(f"小鹿发现 Kimi 页面抓流安静太久啦（{kimi_silence_match.group(1)}s）")

    executor_bg_done_match = re.match(r"^\[Executor\] 后台网络事件监听结束: (.+)$", text, re.S)
    if executor_bg_done_match:
        return cute(f"小鹿这边的后台网络监听先结束啦（{executor_bg_done_match.group(1)}）")

    executor_listen_done_match = re.match(r"^\[Executor\] 监听完成 \(mode=(.+)\)$", text)
    if executor_listen_done_match:
        return cute(f"小鹿确认这轮监听流程跑完啦（模式 {executor_listen_done_match.group(1)}）")

    step_cancelled_match = re.match(r"^步骤 (.+) 跳过（已取消）$", text)
    if step_cancelled_match:
        return cute(f"小鹿把步骤 {step_cancelled_match.group(1)} 先跳过啦，因为已经收到取消信号")

    step_executing_match = re.match(r"^执行: (.+) -> (.+)$", text)
    if step_executing_match:
        action_name, target_key = step_executing_match.groups()
        return cute(f"小鹿开始执行步骤啦（动作 {action_name}，目标 {target_key}）")

    js_exec_match = re.match(r"^\[JS_EXEC\] 执行完成: (.+)$", text, re.S)
    if js_exec_match:
        return cute(f"小鹿刚跑完一段页面脚本喵（返回预览 {js_exec_match.group(1)}）")

    click_exception_match = re.match(r"^点击异常: (.+)$", text, re.S)
    if click_exception_match:
        return cute(f"小鹿点这里时被绊了一下喵（{click_exception_match.group(1)}）")

    content_parse_start_match = re.match(
        r"^\[CONTENT_PARSE\] 开始解析: type=(.+), raw_len=(\d+), preview=(.+)$",
        text,
        re.S,
    )
    if content_parse_start_match:
        content_type, raw_len, preview = content_parse_start_match.groups()
        return cute(f"小鹿开始拆内容啦（类型 {content_type}，原始长度 {raw_len}，预览 {preview}）")

    if text == "[CONTENT_PARSE] 内容为 None，返回空字符串":
        return cute("小鹿发现这次内容是空的喵，先按空字符串处理")

    content_parse_stringified_match = re.match(
        r"^\[CONTENT_PARSE\] 识别为字符串化多模态内容，递归解析 \(parser=(.+), items=(\d+)\)$",
        text,
    )
    if content_parse_stringified_match:
        parse_method, item_count = content_parse_stringified_match.groups()
        return cute(f"小鹿发现这是字符串化的多模态内容喵，准备递归拆开（解析器 {parse_method}，项目 {item_count} 个）")

    content_parse_plain_match = re.match(r"^\[CONTENT_PARSE\] 纯字符串返回: len=(\d+)$", text)
    if content_parse_plain_match:
        return cute(f"小鹿确认这就是普通字符串喵（长度 {content_parse_plain_match.group(1)}）")

    content_parse_list_match = re.match(r"^\[CONTENT_PARSE\] 已转换为 list: items=(\d+)$", text)
    if content_parse_list_match:
        return cute(f"小鹿先把内容转成列表啦（项目 {content_parse_list_match.group(1)} 个）")

    content_parse_done_match = re.match(
        r"^\[CONTENT_PARSE\] 多模态解析完成: text_items=(\d+), images=(\d+), result_len=(\d+), samples=(.+)$",
        text,
        re.S,
    )
    if content_parse_done_match:
        text_items, image_count, result_len, sample_summary = content_parse_done_match.groups()
        return cute(f"小鹿把多模态内容拆完啦（文本 {text_items} 段，图片 {image_count} 张，结果长度 {result_len}，样本 {sample_summary}）")

    browser_connect_match = re.match(r"^连接浏览器 127\.0\.0\.1:(\d+)$", text)
    if browser_connect_match:
        return cute(f"小鹿准备去连浏览器啦（127.0.0.1:{browser_connect_match.group(1)}）")

    browser_domain_match = re.match(r"^\[(.+)\] 域名: (.+)$", text)
    if browser_domain_match:
        session_id, domain_name = browser_domain_match.groups()
        return cute(f"小鹿确认标签页 {session_id} 这次跑的是域名 {domain_name}")

    image_history_match = re.match(r"^图片历史上传: (True|False)$", text)
    if image_history_match:
        return cute(f"小鹿这轮的历史图片上传开关是 {image_history_match.group(1)} 喵")

    image_source_count_match = re.match(r"^图片源消息数: (\d+)/(\d+)$", text)
    if image_source_count_match:
        selected_count, total_count = image_source_count_match.groups()
        return cute(f"小鹿这次会从 {total_count} 条消息里挑 {selected_count} 条来找图片喵")

    session_extractor_match = re.match(r"^\[(.+)\] 使用提取器: (.+) \[预设: (.+)\]$", text)
    if session_extractor_match:
        session_id, extractor_id, preset_name = session_extractor_match.groups()
        return cute(f"小鹿给标签页 {session_id} 选好了提取器 {extractor_id} 喵（预设 {preset_name}）")

    probe_step_done_match = re.match(r"^\[PROBE\] execute_step 完成: action=(.+), target=(.+)$", text)
    if probe_step_done_match:
        action_name, target_key = probe_step_done_match.groups()
        return cute(f"小鹿刚把步骤跑完啦（动作 {action_name}，目标 {target_key}）")

    probe_end_match = re.match(
        r"^\[PROBE\] Workflow 循环结束，image_enabled=(True|False), should_stop=(True|False)$",
        text,
    )
    if probe_end_match:
        image_enabled, should_stop = probe_end_match.groups()
        return cute(f"小鹿把主工作流先跑完啦（图片提取={image_enabled}，停止信号={should_stop}）")

    if text == "[PROBE] 进入图片提取分支":
        return cute("小鹿准备拐进图片提取分支继续收尾啦")

    tabpool_acquire_match = re.match(r"^TabPool → (.+)$", text)
    if tabpool_acquire_match:
        return cute(f"小鹿把这轮请求领到 {tabpool_acquire_match.group(1)} 啦")

    tabpool_wait_done_match = re.match(r"^等待结束 → (.+)$", text)
    if tabpool_wait_done_match:
        return cute(f"小鹿终于等到 {tabpool_wait_done_match.group(1)} 空出来啦")

    tabpool_queue_match = re.match(r"^排队等待 \(忙碌: (.+)\)$", text)
    if tabpool_queue_match:
        return cute(f"小鹿正在排队等标签页喵（忙碌中：{tabpool_queue_match.group(1)}）")

    tabpool_wait_index_match = re.match(r"^等待标签页 #(\d+) 释放\.\.\.$", text)
    if tabpool_wait_index_match:
        return cute(f"小鹿正在等固定编号 #{tabpool_wait_index_match.group(1)} 的标签页空出来喵")

    tabpool_wait_route_match = re.match(r"^等待域名路由 '(.+)' 的标签页释放\.\.\.$", text)
    if tabpool_wait_route_match:
        return cute(f"小鹿正在等域名路由 {tabpool_wait_route_match.group(1)} 对应的标签页空出来喵")

    tabpool_session_active_match = re.match(r"^\[(.+)\] 已激活$", text)
    if tabpool_session_active_match:
        return cute(f"小鹿已经把标签页 {tabpool_session_active_match.group(1)} 激活好啦")

    tabpool_session_release_match = re.match(r"^\[(.+)\] 已释放$", text)
    if tabpool_session_release_match:
        return cute(f"小鹿已经把标签页 {tabpool_session_release_match.group(1)} 放回池子里啦")

    tabpool_temp_assigned_match = re.match(r"^\[(.+)\] 临时标签页已分配$", text)
    if tabpool_temp_assigned_match:
        return cute(f"小鹿先借出临时标签页 {tabpool_temp_assigned_match.group(1)} 给这轮请求喵")

    tabpool_temp_released_match = re.match(r"^\[(.+)\] 临时标签页已释放$", text)
    if tabpool_temp_released_match:
        return cute(f"小鹿把临时标签页 {tabpool_temp_released_match.group(1)} 还回去了喵")

    tabpool_assign_index_match = re.match(r"^标签页 (.+) 分配编号 #(\d+)$", text)
    if tabpool_assign_index_match:
        session_id, index_no = tabpool_assign_index_match.groups()
        return cute(f"小鹿给标签页 {session_id} 贴上了固定编号 #{index_no} 喵")

    return raw_text


# 上下文变量，存储当前请求的 request_id
_request_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
_command_log_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "command_log_context",
    default=None,
)

class SecureLogger:
    """安全日志器，带图标和格式化（支持上下文自动注入 request_id）"""
    _debug_throttle_lock = threading.Lock()
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
        # 截取前8位并大写，保证对齐
        self._name = name.upper()[:8]
        
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
        
        # 防止日志向上层冒泡导致重复打印
        logger.propagate = False 
        
        # 清除旧 handler
        if logger.handlers:
            logger.handlers.clear()
        
        # 控制台输出 handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(_ConsoleColorFormatter())
        logger.addHandler(console_handler)

        file_handler = get_shared_file_log_handler()
        if file_handler is not None:
            logger.addHandler(file_handler)
        
        # Web 前端日志收集 handler（始终添加）
        logger.addHandler(_web_log_handler)
        
        logger.setLevel(logging.DEBUG)
        return logger

    def _format(self, level_key: str, msg: str) -> str:
        """核心格式化逻辑（简洁版）"""
        import datetime
        now = datetime.datetime.now().strftime('%H:%M:%S')
        
        request_id = _request_context.get()
        ctx_str = request_id if request_id else "SYSTEM"
        return f"{now} │ {ctx_str:<8} │ {self._name:<8} │ {msg}"

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
        if upper_level_key == "INFO":
            display_message_text = _cuteify_info_message(self._name, original_message_text)
        elif upper_level_key == "DEBUG":
            display_message_text = _cuteify_debug_message(self._name, original_message_text)
        formatted = self._format(level_key, display_message_text)
        self._logger.log(
            level,
            formatted,
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

        with self._debug_throttle_lock:
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
            suffix = f" (suppressed={suppressed})" if suppressed > 0 else ""
            self.debug(f"{msg}{suffix}")

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

# ================= 异常定义 =================

class BrowserError(Exception):
    """浏览器相关错误基类"""
    pass


class BrowserConnectionError(BrowserError):
    """浏览器连接错误"""
    pass


class ElementNotFoundError(BrowserError):
    """元素未找到错误"""
    pass


class WorkflowError(BrowserError):
    """工作流执行错误"""
    pass


class WorkflowCancelledError(WorkflowError):
    """工作流被取消"""
    pass


class ConfigurationError(BrowserError):
    """配置错误"""
    pass


# ================= SSE 格式化器 =================

class SSEFormatter:
    """SSE 响应格式化器"""
    
    _sequence = 0
    _sequence_lock = threading.Lock()
    
    @classmethod
    def _generate_id(cls) -> str:
        timestamp = int(time.time() * 1000)
        with cls._sequence_lock:
            cls._sequence += 1
            seq = cls._sequence
        short_uuid = uuid.uuid4().hex[:6]
        return f"chatcmpl-{timestamp}-{seq}-{short_uuid}"
    
    @classmethod
    def pack_chunk(
        cls,
        content: str,
        model: str = "web-browser",
        completion_id: str = None,
        images: list[str] | None = None,
    ) -> str:
        """打包流式 chunk。

        注意：为了保持 OpenAI 兼容性，不再向 delta 注入自定义 images 字段。
        图片应通过 content 中的 Markdown 链接传递。
        """
        chunk_id = completion_id or cls._generate_id()
        delta = {"content": content}
        data = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": None
            }]
        }
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    
    @classmethod
    def pack_finish(cls, model: str = "web-browser") -> str:
        data = {
            "id": cls._generate_id(),
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        }
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\ndata: [DONE]\n\n"

    @staticmethod
    def pack_comment(comment: str = "keepalive") -> str:
        """打包 SSE 注释帧，用于长连接保活。"""
        safe_comment = " ".join(str(comment or "keepalive").splitlines()).strip() or "keepalive"
        return f": {safe_comment}\n\n"
    
    @staticmethod
    def pack_error(message: str, error_type: str = "execution_error",
                   code: str = "workflow_failed") -> str:
        data = {
            "id": f"chatcmpl-error-{int(time.time() * 1000)}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "web-browser",
            "choices": [{
                "index": 0,
                "delta": {"content": f"[错误] {message}"},
                "finish_reason": None
            }],
            "error": {
                "message": message,
                "type": error_type,
                "code": code
            }
        }
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    
    @staticmethod
    def pack_error_json(message: str, error_type: str = "execution_error",
                        code: str = "workflow_failed") -> Dict:
        return {
            "error": {
                "message": message,
                "type": error_type,
                "code": code
            }
        }
    
    @staticmethod
    def pack_non_stream(content: str, model: str = "web-browser") -> Dict:
        return {
            "id": f"chatcmpl-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }

    @staticmethod
    def _build_markdown_image_block(images: list) -> str:
        refs = []
        for item in images or []:
            if isinstance(item, dict):
                ref = str(item.get("url") or item.get("data_uri") or "").strip()
            else:
                ref = str(item or "").strip()
            if ref:
                refs.append(ref)

        if not refs:
            return ""

        return "".join(f"\n\n![image_{idx}]({ref})" for idx, ref in enumerate(refs)) + "\n\n"

    @classmethod
    def pack_images_chunk(cls, images: list, completion_id: str = None) -> str:
        """
        打包携带图片的 SSE chunk。

        为保持 OpenAI 兼容性，图片会转成 Markdown 内容，而不是放进 delta.images。
        
        Args:
            images: 图片数据列表，每项符合 ImageData 格式
            completion_id: 补全 ID
        
        Returns:
            SSE 格式的字符串
        
        Example:
            >>> chunk = SSEFormatter.pack_images_chunk([{"kind": "url", "url": "..."}])
        """
        markdown = cls._build_markdown_image_block(images)
        if not markdown:
            return ""
        return cls.pack_chunk(markdown, completion_id=completion_id)

    def pack_final_chunk_with_images(self, images: list, completion_id: str = None) -> str:
        """
        打包包含图片的最终 chunk。

        为保持 OpenAI 兼容性，图片会转成 Markdown 内容，而不是放进 delta.images。
        """
        markdown = self._build_markdown_image_block(images)
        if not markdown:
            return ""
        return self.pack_chunk(markdown, completion_id=completion_id)
# ================= 消息验证器 =================

class MessageValidator:
    """消息验证器"""
    
    VALID_ROLES = {'user', 'assistant', 'system'}
    _IMAGE_PLACEHOLDER = "[图片]"

    @classmethod
    def _parse_multimodal_string(cls, content: str):
        """尝试把字符串形式的多模态 content 还原成列表。"""
        text = str(content or "")
        stripped = text.strip()
        if not stripped.startswith('[') or not stripped.endswith(']'):
            return text, False

        parsed = None
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = None

        if parsed is None:
            try:
                import ast
                parsed = ast.literal_eval(stripped)
            except Exception:
                parsed = None

        if isinstance(parsed, list):
            return parsed, True
        return text, False

    @classmethod
    def _normalize_content(cls, content: Any) -> Any:
        """保留多模态结构，避免把图片/base64 粗暴 str() 化。"""
        if content is None:
            return ""
        if isinstance(content, list):
            return content
        if isinstance(content, tuple):
            return list(content)
        if isinstance(content, str):
            parsed, ok = cls._parse_multimodal_string(content)
            return parsed if ok else content
        return str(content)

    @classmethod
    def _effective_content_length(cls, content: Any) -> int:
        """按网页执行真实会使用的语义估算 content 长度。"""
        normalized = cls._normalize_content(content)

        if isinstance(normalized, str):
            text = normalized
            if text.startswith("data:image") and "base64," in text and len(text) > 1000:
                return len("[图片内容]")
            return len(text)

        if isinstance(normalized, list):
            total = 0
            for item in normalized:
                if item is None:
                    continue
                if not isinstance(item, dict):
                    total += len(str(item))
                    continue

                item_type = str(item.get("type", "") or "").strip()
                if item_type == "text":
                    total += len(str(item.get("text", "") or ""))
                elif item_type == "image_url":
                    total += len(cls._IMAGE_PLACEHOLDER)
                else:
                    total += len(str(item))
            return total

        return len(str(normalized))
    
    @classmethod
    def validate(cls, messages: Any) -> tuple:
        if messages is None:
            return False, "messages 不能为空", None
        
        if not isinstance(messages, list):
            return False, f"messages 应该是列表", None
        
        if len(messages) == 0:
            return False, "messages 不能为空列表", None
        
        message_count = len(messages)
        max_messages = int(BrowserConstants.MAX_MESSAGES_COUNT)
        if message_count > max_messages:
            return False, (
                f"消息数量超过限制（当前 {message_count} 条，最大允许 {max_messages} 条）"
            ), None
        
        sanitized = []
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                return False, f"messages[{i}] 不是字典类型", None
            
            role = msg.get('role', 'user')
            if role not in cls.VALID_ROLES:
                role = 'user'
            
            content = cls._normalize_content(msg.get('content', ''))
            
            content_length = cls._effective_content_length(content)
            max_length = int(BrowserConstants.MAX_MESSAGE_LENGTH)
            if content_length > max_length:
                return False, (
                    f"messages[{i}].content 超过长度限制"
                    f"（当前 {content_length} 字符，最大允许 {max_length} 字符）"
                ), None
            
            sanitized.append({'role': role, 'content': content})
        
        return True, None, sanitized

# ================= 日志工厂函数 =================

def get_logger(name: str) -> SecureLogger:
    """获取 SecureLogger 实例（统一日志入口）"""
    return SecureLogger(name)


# 创建常用 logger 实例（向后兼容）
logger = get_logger("BROWSER")
# ================= 模块初始化 =================

# 加载浏览器配置并应用到类属性
BrowserConstants._load_config()
BrowserConstants._apply_to_class_attrs()


# 启动时打印配置确认
logger.info(f"[CONFIG] 日志级别: {AppConfig.get_log_level()}")
logger.info(f"[CONFIG] 调试模式: {AppConfig.is_debug()}")
logger.info(f"[CONFIG] 浏览器端口: {BrowserConstants.DEFAULT_PORT}")
logger.info(f"[CONFIG] 配置文件: {BrowserConstants._config_file} (存在: {BrowserConstants._config_file.exists()})")
logger.info(f"[CONFIG] STREAM_SILENCE_THRESHOLD = {BrowserConstants.STREAM_SILENCE_THRESHOLD}")
logger.info(f"[CONFIG] STREAM_STABLE_COUNT_THRESHOLD = {BrowserConstants.STREAM_STABLE_COUNT_THRESHOLD}")
logger.debug(f"[CONFIG] 这条 DEBUG 日志仅在 LOG_LEVEL=DEBUG 时显示")


# ================= 导出 =================

__all__ = [
    # 应用配置
    'AppConfig',
    'app_config',
    
    # 浏览器常量
    'BrowserConstants',
    
    # 日志
    'SecureLogger',
    'logger',
    'get_logger',
    'log_collector',  # 🆕 供 routes.py 使用
    
    # 异常
    'BrowserError',
    'BrowserConnectionError',
    'ElementNotFoundError',
    'WorkflowError',
    'WorkflowCancelledError',
    'ConfigurationError',
    
    # 工具
    'SSEFormatter',
    'MessageValidator',
]
