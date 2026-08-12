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
import ast
import contextvars
import contextlib
import datetime
import os
import time
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import sys
import tempfile
import threading
import uuid
import ctypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache
from collections import defaultdict, deque

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
_shared_file_log_handler: Optional[logging.Handler] = None
_shared_file_log_handler_lock = threading.Lock()
_logger_setup_lock = threading.RLock()
_logger_registry_lock = threading.Lock()
_logger_registry: Dict[str, "SecureLogger"] = {}
# ================= 环境变量加载 =================


def atomic_write_json(path: str | Path, payload: Any) -> None:
    """Atomically write JSON to disk using a same-directory temporary file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    tmp_path: Optional[Path] = None

    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(target.parent),
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            fd = None
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        _replace_file_with_retry(tmp_path, target)
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        raise


def _replace_file_with_retry(source: str | Path, dest: str | Path) -> None:
    """Replace a file, retrying transient Windows sharing violations."""
    source_path = Path(source)
    dest_path = Path(dest)
    attempts = 3 if os.name == "nt" else 1
    delay = 0.02
    for attempt in range(attempts):
        try:
            os.replace(source_path, dest_path)
            return
        except PermissionError:
            if attempt >= attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2


class classproperty:
    """Allow config access via both `AppConfig.X` and `app_config.X`."""

    def __init__(self, fget):
        self.fget = fget

    def __get__(self, obj, owner=None):
        return self.fget(owner)


def load_dotenv(env_file: str = ".env", override: bool = False):
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

load_dotenv(override=os.getenv("UWAPI_DOTENV_OVERRIDE", "").lower() in ("1", "true", "yes", "on"))


# ================= 应用配置（环境变量）=================

class AppConfig:
    """应用配置（从环境变量读取）"""

    @staticmethod
    def _env_bool(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None or str(value).strip() == "":
            return bool(default)
        return str(value).strip().lower() in ("true", "1", "yes", "on")

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        """安全读取整数环境变量：空值/非法值一律回落默认值，避免服务启动期崩溃。"""
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return int(default)
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        """安全读取浮点环境变量：空值/非法值一律回落默认值，避免服务启动期崩溃。"""
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return float(default)
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            return float(default)

    # ===== 服务配置 =====
    @staticmethod
    def get_host() -> str:
        return os.getenv("APP_HOST", "127.0.0.1")
    
    @staticmethod
    def get_port() -> int:
        # 修复：改用安全解析，避免 .env 里 APP_PORT= 为空导致 int("") 崩溃、服务起不来
        return AppConfig._env_int("APP_PORT", 8199)
    
    @staticmethod
    def is_debug() -> bool:
        return os.getenv("APP_DEBUG", "false").lower() in ("true", "1", "yes")
    
    @staticmethod
    def get_log_level() -> str:
        return os.getenv("LOG_LEVEL", "INFO").upper()

    # ===== 认证配置 =====
    @staticmethod
    def is_auth_enabled() -> bool:
        return AppConfig._env_bool("AUTH_ENABLED", False)

    @staticmethod
    def get_auth_token() -> str:
        return os.getenv("AUTH_TOKEN", "").strip()

    @staticmethod
    def is_dashboard_auth_enabled() -> bool:
        value = os.getenv("DASHBOARD_AUTH_ENABLED")
        if value is None or str(value).strip() == "":
            return AppConfig.is_auth_enabled()
        return AppConfig._env_bool("DASHBOARD_AUTH_ENABLED", False)

    @staticmethod
    def get_dashboard_auth_token() -> str:
        token = os.getenv("DASHBOARD_AUTH_TOKEN", "").strip()
        return token or AppConfig.get_auth_token()
    
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
        # 修复：改用安全解析，空值/非法值回落默认端口
        return AppConfig._env_int("BROWSER_PORT", 9222)
    
    # ===== Dashboard 配置 =====
    @staticmethod
    def is_dashboard_enabled() -> bool:
        return os.getenv("DASHBOARD_ENABLED", "true").lower() in ("true", "1", "yes")
    
    @staticmethod
    def get_dashboard_file() -> str:
        return os.getenv("DASHBOARD_FILE", "static/index.html")

    # ===== 定时服务重启守护 =====
    @staticmethod
    def is_scheduled_restart_enabled() -> bool:
        return AppConfig._env_bool("SCHEDULED_RESTART_ENABLED", False)

    @staticmethod
    def get_scheduled_restart_interval_seconds() -> int:
        return max(60, AppConfig._env_int("SCHEDULED_RESTART_INTERVAL_SECONDS", 10800))

    @staticmethod
    def get_scheduled_restart_drain_timeout_seconds() -> int:
        return max(0, AppConfig._env_int("SCHEDULED_RESTART_DRAIN_TIMEOUT_SECONDS", 1800))

    @staticmethod
    def get_scheduled_restart_tab_state_policy() -> str:
        """Reserved for future tab-state cleanup; preserve is currently the only policy."""
        return os.getenv("SCHEDULED_RESTART_TAB_STATE_POLICY", "preserve").strip().lower() or "preserve"
    
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
        # 修复：改用安全解析，空值/非法值回落默认值
        return AppConfig._env_int("MAX_HTML_CHARS", 120000)

    @staticmethod
    def get_canvas_image_max_size() -> int:
        try:
            # 修复：改用安全解析，空值/非法值回落默认值
            value = AppConfig._env_int("CANVAS_IMAGE_MAX_SIZE", 1024)
        except Exception:
            value = 1024
        return max(1, value)

    # ===== 错误标签页 AI 恢复配置 =====
    @staticmethod
    def is_tab_recovery_enabled() -> bool:
        return AppConfig._env_bool("TAB_RECOVERY_ENABLED", False)

    @staticmethod
    def get_tab_recovery_api_url() -> str:
        return os.getenv("TAB_RECOVERY_API_URL", "").strip()

    @staticmethod
    def get_tab_recovery_api_key() -> str:
        return os.getenv("TAB_RECOVERY_API_KEY", "").strip()

    @staticmethod
    def get_tab_recovery_model() -> str:
        return os.getenv("TAB_RECOVERY_MODEL", "gpt-4o").strip() or "gpt-4o"

    @staticmethod
    def get_tab_recovery_max_attempts() -> int:
        try:
            # 修复：改用安全解析，空值/非法值回落默认值
            value = AppConfig._env_int("TAB_RECOVERY_MAX_ATTEMPTS", 1)
        except Exception:
            value = 1
        return max(0, value)

    @staticmethod
    def get_tab_recovery_timeout_sec() -> float:
        try:
            # 修复：改用安全解析，空值/非法值回落默认值
            value = AppConfig._env_float("TAB_RECOVERY_TIMEOUT_SEC", 120.0)
        except Exception:
            value = 120.0
        return max(1.0, value)

    @staticmethod
    def get_tab_recovery_worker_exit_wait_sec() -> float:
        try:
            # 修复：改用安全解析，空值/非法值回落默认值
            value = AppConfig._env_float("TAB_RECOVERY_WORKER_EXIT_WAIT_SEC", 600.0)
        except Exception:
            value = 600.0
        return max(0.0, value)

    @staticmethod
    def is_tab_recovery_refresh_on_unknown() -> bool:
        return AppConfig._env_bool("TAB_RECOVERY_REFRESH_ON_UNKNOWN", True)

    # ===== 配置文件路径 =====
    @staticmethod
    def get_sites_config_file() -> str:
        return os.getenv("SITES_CONFIG_FILE", "config/sites.json")

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

    @classproperty
    def DASHBOARD_AUTH_TOKEN(cls) -> str:
        return cls.get_dashboard_auth_token()


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
        'MAX_REQUEST_EXECUTE_TIME_SEC': 300.0,
        'STEALTH_DELAY_MIN': 0.03,
        'STEALTH_DELAY_MAX': 0.1,
        'ACTION_DELAY_MIN': 0.06,
        'ACTION_DELAY_MAX': 0.14,
        'STEALTH_PAUSE_PROBABILITY': 0.0,
        'STEALTH_PAUSE_EXTRA_MAX': 0.15,
        'STEALTH_KEY_DOWN_UP_MIN': 0.015,
        'STEALTH_KEY_DOWN_UP_MAX': 0.04,
        'STEALTH_KEY_BETWEEN_MIN': 0.02,
        'STEALTH_KEY_BETWEEN_MAX': 0.06,
        'STEALTH_PASTE_SETTLE_MIN': 0.12,
        'STEALTH_PASTE_SETTLE_MAX': 0.25,
        'STEALTH_SKIP_PASTE_VERIFY': True,
        'STEALTH_SEND_IMAGE_WAIT': 8.0,
        'STEALTH_SEND_IMAGE_RETRY_INTERVAL': 1.2,
        'STEALTH_MOUSE_WARMUP_ENABLED': False,
        'STEALTH_CLICK_STRATEGY': 'auto',
        'STEALTH_DOM_CLICK_TARGETS': ['new_chat_btn', 'input_box', 'send_btn'],
        'PAGE_INTERACTION_THROTTLE_ENABLED': True,
        'PAGE_INTERACTION_MAX_CONCURRENT': 3,
        'PAGE_INTERACTION_MAX_WAIT': 20.0,
        'PAGE_INTERACTION_MIN_INTERVAL': 0.25,
        'PAGE_INTERACTION_READY_TIMEOUT': 1.5,
        'PAGE_INTERACTION_STABLE_SAMPLES': 2,
        'PAGE_INTERACTION_SAMPLE_INTERVAL': 0.12,
        'PAGE_INTERACTION_RECT_TOLERANCE': 3,
        'COORD_CLICK_READY_TIMEOUT': 0.9,
        'COORD_CLICK_STABLE_SAMPLES': 2,
        'COORD_CLICK_SAMPLE_INTERVAL': 0.08,
        'COORD_CLICK_RECT_TOLERANCE': 3,
        'COORD_CLICK_EDGE_INSET': 4,
        'COORD_CLICK_RETRY_OFFSETS': [[0, 0], [4, 0], [-4, 0], [0, 4], [0, -4], [7, 3], [-7, 3]],
        'WORKFLOW_WAKE_TAB_BEFORE_INTERACTION': True,
        'WORKFLOW_FOCUS_EMULATION_ON_INTERACTION': True,
        'DEFAULT_ELEMENT_TIMEOUT': 3,
        'FALLBACK_ELEMENT_TIMEOUT': 1,
        'ELEMENT_CACHE_MAX_AGE': 5.0,
        'LOG_INFO_CUTE_MODE': True,
        'LOG_DEBUG_CUTE_MODE': True,
        'LOG_CONSOLE_ENABLED': True,
        'LOG_FILE_ENABLED': True,
        'LOG_WEB_COLLECTOR_ENABLED': True,
        'LOG_WEB_MAX_RECORDS': 5000,
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
        'TEXT_INPUT_CHUNK_SIZE': 30000,
        'STREAM_USER_MSG_WAIT': 1.5,
        'STREAM_PRE_BASELINE_DELAY': 0.3,
        'GLOBAL_NETWORK_INTERCEPTION_ENABLED': False,
        'GLOBAL_NETWORK_INTERCEPTION_LISTEN_PATTERN': 'http',
        'GLOBAL_NETWORK_INTERCEPTION_WAIT_TIMEOUT': 0.5,
        'GLOBAL_NETWORK_INTERCEPTION_RETRY_DELAY': 1.0,
        'NETWORK_DEBUG_CAPTURE_ENABLED': False,
        'NETWORK_DEBUG_CAPTURE_MAX_BODY_CHARS': 50000,
        'NETWORK_DEBUG_CAPTURE_MAX_FILES_PER_REQUEST': 3,
        'NETWORK_DEBUG_CAPTURE_PARSER_FILTER': '',
        'CONVERSATION_TIMEOUT_THRESHOLD': 0.0,
        'FORCE_NEW_CONVERSATION': False,
        'REQUEST_MONITOR_ENABLED': True,
        'REQUEST_MONITOR_MAX_RECORDS': 200,
        'REQUEST_MONITOR_DETAIL_ENABLED': True,
        'REQUEST_MONITOR_SAVE_TO_FILE': True,
        'REQUEST_MONITOR_MAX_CAPTURED_RESPONSE_CHARS': 30000,
        'DASHBOARD_LOG_POLL_INTERVAL_MS': 1000,
        'DASHBOARD_LOG_BACKGROUND_POLL_INTERVAL_MS': 5000,
        'DASHBOARD_REQUEST_HISTORY_POLL_INTERVAL_MS': 3000,
        'DASHBOARD_SYSTEM_STATS_ENABLED': True,
        'DASHBOARD_SYSTEM_STATS_POLL_INTERVAL_MS': 3000,
        'ATTACHMENT_READY_IDLE_TIMEOUT': 8.0,
        'ATTACHMENT_READY_HARD_MAX_WAIT': 90.0,
    }
    
    # ===== 类属性（会被配置文件覆盖）=====
    
    # 连接配置
    DEFAULT_PORT = 9222
    CONNECTION_TIMEOUT = 10
    MAX_REQUEST_EXECUTE_TIME_SEC = 300.0
    
    # 延迟配置
    STEALTH_DELAY_MIN = 0.03
    STEALTH_DELAY_MAX = 0.1
    ACTION_DELAY_MIN = 0.06
    ACTION_DELAY_MAX = 0.14
    STEALTH_PAUSE_PROBABILITY = 0.0
    STEALTH_PAUSE_EXTRA_MAX = 0.15
    STEALTH_KEY_DOWN_UP_MIN = 0.015
    STEALTH_KEY_DOWN_UP_MAX = 0.04
    STEALTH_KEY_BETWEEN_MIN = 0.02
    STEALTH_KEY_BETWEEN_MAX = 0.06
    STEALTH_PASTE_SETTLE_MIN = 0.12
    STEALTH_PASTE_SETTLE_MAX = 0.25
    STEALTH_SKIP_PASTE_VERIFY = True
    STEALTH_SEND_IMAGE_WAIT = 8.0
    STEALTH_SEND_IMAGE_RETRY_INTERVAL = 1.2
    STEALTH_MOUSE_WARMUP_ENABLED = False
    STEALTH_CLICK_STRATEGY = "auto"
    STEALTH_DOM_CLICK_TARGETS = ["new_chat_btn", "input_box", "send_btn"]
    PAGE_INTERACTION_THROTTLE_ENABLED = True
    PAGE_INTERACTION_MAX_CONCURRENT = 3
    PAGE_INTERACTION_MAX_WAIT = 20.0
    PAGE_INTERACTION_MIN_INTERVAL = 0.25
    PAGE_INTERACTION_READY_TIMEOUT = 1.5
    PAGE_INTERACTION_STABLE_SAMPLES = 2
    PAGE_INTERACTION_SAMPLE_INTERVAL = 0.12
    PAGE_INTERACTION_RECT_TOLERANCE = 3
    WORKFLOW_WAKE_TAB_BEFORE_INTERACTION = True
    WORKFLOW_FOCUS_EMULATION_ON_INTERACTION = True
    
    # 元素查找
    DEFAULT_ELEMENT_TIMEOUT = 3
    FALLBACK_ELEMENT_TIMEOUT = 1
    ELEMENT_CACHE_MAX_AGE = 5.0

    # 日志
    LOG_INFO_CUTE_MODE = True
    LOG_DEBUG_CUTE_MODE = True
    LOG_CONSOLE_ENABLED = True
    LOG_FILE_ENABLED = True
    LOG_WEB_COLLECTOR_ENABLED = True
    LOG_WEB_MAX_RECORDS = 5000
    
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

    # 附件/图片上传就绪判定
    ATTACHMENT_READY_IDLE_TIMEOUT = 8.0
    ATTACHMENT_READY_HARD_MAX_WAIT = 90.0

    # 文本输入
    TEXT_INPUT_CHUNK_SIZE = 30000
    
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
    NETWORK_DEBUG_CAPTURE_MAX_BODY_CHARS = 50000
    NETWORK_DEBUG_CAPTURE_MAX_FILES_PER_REQUEST = 3
    NETWORK_DEBUG_CAPTURE_PARSER_FILTER = ""

    # 对话会话控制
    CONVERSATION_TIMEOUT_THRESHOLD = 0.0
    FORCE_NEW_CONVERSATION = False

    # 低资源运行
    REQUEST_MONITOR_ENABLED = True
    REQUEST_MONITOR_MAX_RECORDS = 200
    REQUEST_MONITOR_DETAIL_ENABLED = True
    REQUEST_MONITOR_SAVE_TO_FILE = True
    REQUEST_MONITOR_MAX_CAPTURED_RESPONSE_CHARS = 30000
    DASHBOARD_LOG_POLL_INTERVAL_MS = 1000
    DASHBOARD_LOG_BACKGROUND_POLL_INTERVAL_MS = 5000
    DASHBOARD_REQUEST_HISTORY_POLL_INTERVAL_MS = 3000
    DASHBOARD_SYSTEM_STATS_ENABLED = True
    DASHBOARD_SYSTEM_STATS_POLL_INTERVAL_MS = 3000

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

def _browser_constant_bool(key: str, default: bool = True) -> bool:
    value = BrowserConstants.get(key)
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _browser_constant_int(
    key: str,
    default: int,
    *,
    min_value: int = 0,
    max_value: Optional[int] = None,
) -> int:
    try:
        value = int(BrowserConstants.get(key))
    except Exception:
        value = int(default)
    value = max(int(min_value), value)
    if max_value is not None:
        value = min(int(max_value), value)
    return value


class _BrowserConstantEnabledFilter:
    def __init__(self, key: str, default: bool = True):
        self.key = key
        self.default = default

    def filter(self, record: logging.LogRecord) -> bool:
        return _browser_constant_bool(self.key, self.default)

# ================= 日志收集器（供前端展示）=================

class LogCollector:
    """收集日志用于前端展示"""

    def __init__(self, max_logs: int = 5000):
        self.logs: deque = deque(maxlen=max_logs)
        self.lock = threading.Lock()
        self._next_seq = 1
        self._last_clear_seq = 0

    def is_enabled(self) -> bool:
        return _browser_constant_bool("LOG_WEB_COLLECTOR_ENABLED", True)

    def _target_max_logs(self) -> int:
        return _browser_constant_int("LOG_WEB_MAX_RECORDS", 5000, min_value=0, max_value=10000)

    def _sync_limits_unlocked(self) -> None:
        target_max_logs = self._target_max_logs()
        if self.logs.maxlen == target_max_logs:
            return
        self.logs = deque(list(self.logs)[-target_max_logs:] if target_max_logs else [], maxlen=target_max_logs)

    def add(self, entry: Dict[str, Any]):
        with self.lock:
            self._sync_limits_unlocked()
            if not self.is_enabled() or self.logs.maxlen == 0:
                if self.logs:
                    self.logs.clear()
                return
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

    def get_recent(self, since: float = 0, after_seq: int = 0) -> Tuple[List[Dict[str, Any]], int, bool]:
        with self.lock:
            self._sync_limits_unlocked()
            if not self.is_enabled() or self.logs.maxlen == 0:
                if self.logs:
                    self.logs.clear()
                return [], self._next_seq - 1, False
            cursor = max(0, int(after_seq or 0))
            cleared = bool(cursor and self._last_clear_seq and cursor <= self._last_clear_seq)
            if cursor > 0:
                latest_seq = self._next_seq - 1
                if cursor == latest_seq:
                    return [], latest_seq, cleared
                if cursor > latest_seq:
                    return list(self.logs), latest_seq, True

                oldest_seq = int(self.logs[0].get("seq", 0) or 0) if self.logs else latest_seq
                if self.logs and cursor < oldest_seq:
                    return list(self.logs), latest_seq, True

                logs = [
                    log for log in self.logs
                    if int(log.get("seq", 0) or 0) > cursor
                ]
            else:
                logs = [log for log in self.logs if float(log.get("timestamp", 0) or 0) > since]
            return logs, self._next_seq - 1, cleared

    def clear(self):
        with self.lock:
            self.logs.clear()
            self._last_clear_seq = self._next_seq - 1
            self._next_seq += 1


# 全局日志收集器实例
log_collector = LogCollector()


_SENSITIVE_KEY_HINTS = (
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "client_secret",
    "refresh_token",
    "session",
    "csrf",
)
_REDACTED_TEXT = "******"


def _redact_data_uri_for_log(match: re.Match) -> str:
    media_type = str(match.group(1) or "media").lower()
    mime_suffix = str(match.group(2) or "octet-stream").lower()
    payload_len = len(str(match.group(3) or ""))
    return f"data:{media_type}/{mime_suffix};base64,[omitted {payload_len} chars]"


def _redact_long_base64_for_log(match: re.Match) -> str:
    return f"[base64 omitted: {len(match.group(0))} chars]"


_BASE64_LOG_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-")
_SENSITIVE_TEXT_SCAN_HINT_RE = re.compile(
    r"(?i)data:(?:image|audio|video)/|bearer\s+|authorization|set-cookie|cookie|"
    r"x[-_]api[-_]key|x[-_]github[-_]token|github[-_]token|"
    r"access_token|refresh_token|id_token|api_key|apikey|access_key|client_secret|"
    r"secret|password|passwd|token|session|csrf|sk-[A-Za-z0-9_-]{8,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"https?://[^/\s:@]+:[^/\s@]+@"
)
_SENSITIVE_TEXT_PRECHECK_MIN_CHARS = 4096
_SENSITIVE_TEXT_LARGE_OMIT_THRESHOLD = 512 * 1024
_SENSITIVE_TEXT_LARGE_EDGE_CHARS = 4096


def _has_long_base64_candidate(text: str, min_chars: int = 1024) -> bool:
    run_len = 0
    for char in text:
        if char in _BASE64_LOG_CHARS:
            run_len += 1
            if run_len >= min_chars:
                return True
        elif char in "\r\n" and run_len:
            continue
        else:
            run_len = 0
    return False


def _redact_long_base64_runs_for_log(text: str, min_chars: int = 1024) -> str:
    raw_text = str(text or "")
    if not raw_text:
        return raw_text

    parts = []
    copy_from = 0
    run_start = None
    run_payload_chars = 0

    for index, char in enumerate(raw_text):
        if char in _BASE64_LOG_CHARS:
            if run_start is None:
                run_start = index
                run_payload_chars = 0
            run_payload_chars += 1
            continue

        if char in "\r\n" and run_start is not None:
            continue

        if run_start is not None:
            if run_payload_chars >= min_chars:
                parts.append(raw_text[copy_from:run_start])
                parts.append(f"[base64 omitted: {index - run_start} chars]")
                copy_from = index
            run_start = None
            run_payload_chars = 0

    if run_start is not None and run_payload_chars >= min_chars:
        parts.append(raw_text[copy_from:run_start])
        parts.append(f"[base64 omitted: {len(raw_text) - run_start} chars]")
        copy_from = len(raw_text)

    if not parts:
        return raw_text
    parts.append(raw_text[copy_from:])
    return "".join(parts)


def _should_scan_sensitive_text(text: str) -> bool:
    if len(text) <= _SENSITIVE_TEXT_PRECHECK_MIN_CHARS:
        return True
    if _SENSITIVE_TEXT_SCAN_HINT_RE.search(text):
        return True
    return _has_long_base64_candidate(text)


_SENSITIVE_TEXT_PATTERNS = (
    (
        re.compile(
            r"(?i)data:(image|audio|video)/([a-zA-Z0-9.+-]{1,100});base64,"
            r"([A-Za-z0-9+/=_\-\r\n]{64,})"
        ),
        _redact_data_uri_for_log,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{1024,}(?![A-Za-z0-9+/=_-])"),
        _redact_long_base64_for_log,
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9+/=_-])"
            r"(?:[A-Za-z0-9+/=_-]{64,}[\r\n]+){7,}[A-Za-z0-9+/=_-]{64,}"
            r"(?![A-Za-z0-9+/=_-])"
        ),
        _redact_long_base64_for_log,
    ),
    (
        re.compile(r"(?i)\b(Bearer)\s+([A-Za-z0-9._~+/=-]{8,})"),
        r"\1 ******",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_-])"
            r"(sk-(?:proj-)?[A-Za-z0-9_-]{16,})"
            r"(?![A-Za-z0-9_-])"
        ),
        "sk-******",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_-])"
            r"(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,})"
            r"(?![A-Za-z0-9_-])"
        ),
        "[jwt omitted]",
    ),
    (
        re.compile(r"(?i)(https?://[^/\s:@]+):([^/\s@]+)(@)"),
        r"\1:******\3",
    ),
    (
        re.compile(
            r"(?i)\b(Authorization|Cookie|Set-Cookie|X[-_]Api[-_]Key|"
            r"X[-_]GitHub[-_]Token|GitHub[-_]Token)\s*:\s*[^\r\n;]+(?:;[^\r\n]*)?"
        ),
        lambda match: f"{match.group(1)}: ******",
    ),
    (
        re.compile(
            r"(?i)([?&](?:access_token|refresh_token|id_token|token|api_key|apikey|"
            r"access_key|client_secret|session|csrf|"
            r"x[-_]api[-_]key|x[-_]github[-_]token|github[-_]token|key|secret|password|passwd)=)"
            r"[^&\s]+"
        ),
        r"\1******",
    ),
    (
        re.compile(
            r"(?i)(\b(?:access_token|refresh_token|id_token|token|api_key|apikey|"
            r"access_key|client_secret|session|csrf|"
            r"x[-_]api[-_]key|x[-_]github[-_]token|github[-_]token|secret|password|passwd)"
            r"\s*=\s*)[^\s,&;]+"
        ),
        r"\1******",
    ),
    (
        re.compile(
            r'(?i)("(?:authorization|cookie|set-cookie|password|passwd|secret|token|api_key|apikey|'
            r'access_token|refresh_token|access_key|client_secret|session|csrf|'
            r'x[-_]api[-_]key|x[-_]github[-_]token|'
            r'github[-_]token)"\s*:\s*)"[^"]*"'
        ),
        r'\1"******"',
    ),
    (
        re.compile(
            r'(?i)("(?:authorization|cookie|set-cookie|password|passwd|secret|token|api_key|apikey|'
            r'access_token|refresh_token|access_key|client_secret|session|csrf|'
            r'x[-_]api[-_]key|x[-_]github[-_]token|'
            r'github[-_]token)"\s*:\s*")[^"\r\n]*$'
        ),
        r'\1******',
    ),
    (
        re.compile(
            r"(?i)('(?:authorization|cookie|set-cookie|password|passwd|secret|token|api_key|apikey|"
            r"access_token|refresh_token|access_key|client_secret|session|csrf|"
            r"x[-_]api[-_]key|x[-_]github[-_]token|"
            r"github[-_]token)'\s*:\s*)'[^']*'"
        ),
        r"\1'******'",
    ),
    (
        re.compile(
            r"(?i)('(?:authorization|cookie|set-cookie|password|passwd|secret|token|api_key|apikey|"
            r"access_token|refresh_token|access_key|client_secret|session|csrf|"
            r"x[-_]api[-_]key|x[-_]github[-_]token|"
            r"github[-_]token)'\s*:\s*)'[^'\r\n]*$"
        ),
        r"\1'******",
    ),
)
_SENSITIVE_BEARER_PATTERN = _SENSITIVE_TEXT_PATTERNS[3]
_SENSITIVE_STANDALONE_TOKEN_PATTERNS = _SENSITIVE_TEXT_PATTERNS[4:7]
_SENSITIVE_HEADER_PATTERN = _SENSITIVE_TEXT_PATTERNS[7]
_SENSITIVE_PARAM_PATTERNS = _SENSITIVE_TEXT_PATTERNS[8:]


def _sanitize_standalone_sensitive_tokens(text: str) -> str:
    if "sk-" not in text and "eyJ" not in text and "://" not in text:
        return text
    sanitized = text
    for pattern, replacement in _SENSITIVE_STANDALONE_TOKEN_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    return any(hint.replace("-", "_") in normalized for hint in _SENSITIVE_KEY_HINTS)


def _sanitize_sensitive_text(text: str) -> str:
    sanitized = str(text or "")
    if not sanitized:
        return sanitized

    if len(sanitized) > _SENSITIVE_TEXT_LARGE_OMIT_THRESHOLD:
        edge_chars = max(0, int(_SENSITIVE_TEXT_LARGE_EDGE_CHARS))
        head = sanitized[:edge_chars]
        tail = sanitized[-edge_chars:] if edge_chars else ""
        omitted = max(0, len(sanitized) - len(head) - len(tail))
        safe_head = _sanitize_sensitive_text(head)
        safe_tail = _sanitize_sensitive_text(tail) if tail else ""
        if safe_tail:
            return (
                f"{safe_head}... [omitted {omitted} chars from oversized log payload] "
                f"...{safe_tail}"
            )
        return f"{safe_head}... [omitted {omitted} chars from oversized log payload]"

    sanitized = _sanitize_standalone_sensitive_tokens(sanitized)

    if not _should_scan_sensitive_text(sanitized):
        return sanitized

    lower_text = sanitized.lower()

    # 1. 只有可能有 Base64 候选字符或包含 'data:' 才做前三个正则替换。
    # 这样大幅度优化了绝大多数短普通日志的处理耗时。
    if "data:" in lower_text or "base64" in lower_text or len(sanitized) >= 1024:
        if "data:" in lower_text and "base64," in lower_text:
            sanitized = _SENSITIVE_TEXT_PATTERNS[0][0].sub(_SENSITIVE_TEXT_PATTERNS[0][1], sanitized)
            lower_text = sanitized.lower()
        if len(sanitized) >= 1024 and _has_long_base64_candidate(sanitized):
            sanitized = _redact_long_base64_runs_for_log(sanitized)
            lower_text = sanitized.lower()

    # 2. 仅在包含 bearer 时替换 Bearer token
    if "bearer" in lower_text:
        sanitized = _SENSITIVE_BEARER_PATTERN[0].sub(_SENSITIVE_BEARER_PATTERN[1], sanitized)

    # 3. 仅在包含相关 header 名时替换 Auth, Cookie 等头部
    if any(k in lower_text for k in ("authorization", "cookie", "set-cookie", "x-api-key", "x_api_key", "x-github-token", "x_github_token", "github-token", "github_token")):
        sanitized = _SENSITIVE_HEADER_PATTERN[0].sub(_SENSITIVE_HEADER_PATTERN[1], sanitized)

    # 4. 仅在包含敏感关键字时执行 query/json param 相关的敏感信息提取正则
    if any(k in lower_text for k in ("access_token", "refresh_token", "id_token", "token", "api_key", "apikey", "access_key", "client_secret", "key", "secret", "password", "passwd", "session", "csrf", "x-api-key", "x_api_key", "x-github-token", "x_github_token", "github-token", "github_token")):
        for pattern, replacement in _SENSITIVE_PARAM_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)

    return sanitized


def sanitize_sensitive_data(value: Any, *, _depth: int = 0) -> Any:
    """Return a sanitized copy suitable for logs and debug artifacts."""
    if _depth > 8:
        return "[max-depth]"

    if isinstance(value, dict):
        sanitized_dict = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                sanitized_dict[key] = _REDACTED_TEXT
            else:
                sanitized_dict[key] = sanitize_sensitive_data(item, _depth=_depth + 1)
        return sanitized_dict

    if isinstance(value, list):
        return [sanitize_sensitive_data(item, _depth=_depth + 1) for item in value]

    if isinstance(value, tuple):
        return tuple(sanitize_sensitive_data(item, _depth=_depth + 1) for item in value)

    if isinstance(value, str):
        return _sanitize_sensitive_text(value)

    return value


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


# 级别徽标：控制台/展示行使用的 3 字符级别标识，让每一行都能直接看出级别
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
    """仅用于控制台输出的彩色格式化器。

    配色策略（让级别一眼可辨、重要信息突出、噪音后退）：
    - ERROR/CRITICAL：整行红色（必须一眼看到）
    - WARNING：整行黄色
    - DEBUG：整行暗灰（视觉降噪，不打扰主流程阅读）
    - KEY（命令触发等关键动作）：整行亮蓝
    - SUCCESS：整行绿色（值得庆祝的时刻）
    - 普通 INFO：正文保持默认色，仅级别徽标着绿色（避免满屏绿色疲劳）
    - STREAM/NETWORK：级别徽标着青色
    """

    RESET = "\033[0m"
    # 整行色调
    LINE_TONES = {
        "ERROR": "\033[31m",
        "WARN": "\033[33m",
        "KEY": "\033[94m",
        "SUCCESS": "\033[92m",
        "DEBUG": "\033[90m",
    }
    # 级别徽标色（仅在没有整行色调时用于徽标着色）
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

        # 无整行色调（普通 INFO / STREAM / NETWORK）：仅给级别徽标上色
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


_REQUEST_FINISH_PATTERN = re.compile(r"^完成 \(([\d.]+)s\)$")
_TAB_START_INDEX_PATTERN = re.compile(r"^开始 \(标签页 #(\d+)\)$")
_TAB_START_ROUTE_PATTERN = re.compile(r"^开始 \(域名路由 ([^,]+)\)$")
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
_STEALTH_SEND_RETRY_SIMPLE_PATTERN = re.compile(r"^\[STEALTH\] 发送重试 #(\d+)$")
_STEALTH_WARMUP_DONE_PATTERN = re.compile(
    r"^\[STEALTH\] 页面预热完成: moves=(\d+), origin=\(([-\d]+),([-\d]+)\), elapsed=([\d.]+)s$"
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
_SEND_RETRY_ACTION_PATTERN = re.compile(
    r"^\[SEND\] 执行发送重试动作 \(elapsed=([\d.]+)s, action=(.+)\)$"
)
_SEND_RETRY_WINDOW_PATTERN = re.compile(
    r"^\[SEND\] 发送未成功，进入重试窗口 \(max_wait=([\d.]+)s, action=(.+)\)$"
)


def _bool_phrase(raw_value: str, true_text: str, false_text: str) -> str:
    return true_text if str(raw_value).strip().lower() == "true" else false_text


def _split_suppressed_suffix(text: str) -> tuple[str, int]:
    raw_text = str(text or "")
    prefix_match = re.match(r"^(\[[^\]]+\])\s+\[已折叠 (\d+) 条\]\s+(.+)$", raw_text, re.S)
    if prefix_match:
        leading_tag, suppressed, rest = prefix_match.groups()
        return f"{leading_tag} {rest}", int(suppressed or 0)
    bare_prefix_match = re.match(r"^\[已折叠 (\d+) 条\]\s+(.+)$", raw_text, re.S)
    if bare_prefix_match:
        suppressed, rest = bare_prefix_match.groups()
        return str(rest or ""), int(suppressed or 0)
    match = re.match(r"^(.*) \(suppressed=(\d+)\)$", raw_text, re.S)
    if not match:
        return raw_text, 0
    return str(match.group(1) or ""), int(match.group(2) or 0)


def _add_suppressed_marker(text: str, suppressed_count: int) -> str:
    raw_text = str(text or "")
    if suppressed_count <= 0 or not raw_text:
        return raw_text
    marker = f"[已折叠 {suppressed_count} 条]"
    tag_match = re.match(r"^(\[[^\]]+\])\s*(.*)$", raw_text, re.S)
    if tag_match:
        leading_tag, rest = tag_match.groups()
        return f"{leading_tag} {marker} {rest}".rstrip()
    return f"{marker} {raw_text}"


def _restore_suppressed_hint(text: str, suppressed_count: int) -> str:
    if suppressed_count <= 0 or not text:
        return text
    return f"{text}（刚刚先按住了 {suppressed_count} 条重复日志喵）"


# ================= 表驱动的喵化翻译规则 =================
# 说明：
# - 这些规则在各级别的手写规则之后统一兜底，覆盖真实日志里高频出现的消息；
# - 只影响展示层（控制台/Web 面板），文件日志 app.log 始终保留技术原文；
# - 翻译原则：先说人话（发生了什么/接下来怎么办），技术细节放在（）里完整保留。


def _apply_cute_rules(rules, text: str) -> Optional[str]:
    """按顺序匹配规则表，命中则返回翻译文本，全部未命中返回 None。"""
    for pattern, render in rules:
        match = pattern.match(text)
        if match:
            try:
                rendered = render(match)
            except Exception:
                return None
            return rendered or None
    return None


def _cute_stage_title(raw_title: str) -> str:
    return str(raw_title or "").replace(" | ", "·").strip()


_CUTE_EXTRA_RULES: List[Tuple[re.Pattern, Any]] = [
    # ---- 请求生命周期 ----
    (re.compile(r"^完成 \(([\d.]+)s, status=completed, tab=(.+?), reason=(.+?)\)$"),
     lambda m: f"这个请求已经圆满完成了喵（耗时 {m.group(1)}s，标签页 {m.group(2)}）"),
    (re.compile(r"^完成 \(([\d.]+)s, status=failed, tab=(.+?), reason=(.+?)\)$"),
     lambda m: f"这个请求没能走完喵（耗时 {m.group(1)}s，标签页 {m.group(2)}，原因 {m.group(3)}）"),
    (re.compile(r"^完成 \(([\d.]+)s, status=(\w+), tab=(.+?), reason=(.+?)\)$"),
     lambda m: f"这个请求收尾啦（状态 {m.group(2)}，耗时 {m.group(1)}s，标签页 {m.group(3)}，原因 {m.group(4)}）"),
    (re.compile(r"^开始 \(标签页 #(\d+), preset=(.+?)\)$"),
     lambda m: f"后端同意了请求喵，准备在标签页 #{m.group(1)} 开始执行工作流（预设 {m.group(2)}）"),
    (re.compile(r"^开始 \(域名路由 (.+?) -> 标签页 #(\d+), selector=(.+?), preset=(.+?)\)$"),
     lambda m: f"后端同意了请求喵，按域名路由 {m.group(1)} 去固定标签页 #{m.group(2)} 开工（预设 {m.group(4)}）"),
    (re.compile(r"^开始 \(域名路由 (.+?) -> 动态同站点标签页, selector=(.+?), preset=(.+?)\)$"),
     lambda m: f"后端同意了请求喵，会在 {m.group(1)} 的同站点标签页里挑一个开工（挑选方式 {m.group(2)}，预设 {m.group(3)}）"),
    (re.compile(r"^开始 \(标签页路由组 (.+?) -> 组内动态标签页, selector=(.+?), preset=(.+?)\)$"),
     lambda m: f"后端同意了请求喵，会从路由组 {m.group(1)} 里挑个标签页开工（挑选方式 {m.group(2)}，预设 {m.group(3)}）"),
    (re.compile(r"^\[DIAG\] 接收到的原始请求 messages 总字符长度: (\d+) 字符, 消息数: (\d+)(.*)$", re.S),
     lambda m: f"小鹿收到这次请求的内容啦（正文共 {m.group(1)} 字符，{m.group(2)} 条消息{m.group(3) or ''}）"),
    (re.compile(r"^模型路由命中: model='(.+?)', .*?matched_id='(.+?)', match_type=(\w+), route_domain=([^,]+), available=.*$", re.S),
     lambda m: f"小鹿找到这次要用的模型啦（{m.group(1)} → {m.group(2)}，匹配方式 {m.group(3)}，路由 {m.group(4)}）"),
    # ---- 发送确认 ----
    (re.compile(r"^\[SEND_CONFIRM\] 发送按钮物理响应已确认: (.+)$", re.S),
     lambda m: f"小鹿确认发送按钮真的按下去了喵（{m.group(1)}）"),
    (re.compile(r"^\[STEALTH\] 发送完成（无图片，(.+?)）$"),
     lambda m: f"小鹿确认消息发出去啦（无图片场景，依据：{m.group(1)}）"),
    (re.compile(r"^\[STEALTH\] 发送成功（输入框已缩短）$"),
     lambda m: "小鹿确认消息发出去啦（输入框已经清空缩短）"),
    (re.compile(r"^\[STEALTH\] 有图片，发送后观察确认 \(observe=([\d.]+)s, max_retry=(\d+)\)$"),
     lambda m: f"带图片的消息已经点了发送喵，小鹿会盯 {m.group(1)}s 确认它真的发出去（最多重试 {m.group(2)} 次）"),
    (re.compile(r"^\[STEALTH\] 无图片发送未拿到确认信号，触发工作流重试$"),
     lambda m: "小鹿没等到发送确认信号喵，让工作流整个再来一次"),
    # ---- 标签页池 / 会话 ----
    (re.compile(r"^刷新完成: \+(\d+) -(\d+) = (\d+) 个标签页$"),
     lambda m: f"小鹿刷新完标签页名单啦（新增 {m.group(1)}，移除 {m.group(2)}，现在共 {m.group(3)} 个）"),
    (re.compile(r"^扫描完成: \+(\d+) 个，当前共 (\d+) 个标签页$"),
     lambda m: f"小鹿扫完一轮标签页喵（新发现 {m.group(1)} 个，现在共 {m.group(2)} 个）"),
    (re.compile(r"^逐出会话缓存: (.+)$"),
     lambda m: f"小鹿把标签页 {m.group(1)} 的旧会话缓存清出去了喵"),
    (re.compile(r"^\[error\] 错误状态已记录，按配置保留标签页 \((.+)\)$", re.S),
     lambda m: f"小鹿记下了这个错误状态喵，按配置把标签页留着没关（{m.group(1)}）"),
    (re.compile(r"^\[([^\]]+)\] 不健康或错误状态，从池中移除但保留浏览器标签页 \((\w+)\)$"),
     lambda m: f"标签页 {m.group(1)} 状态不太健康喵（{m.group(2)}），小鹿把它移出池子，浏览器标签页先留着"),
    (re.compile(r"^\[([^\]]+)\] 标签页已关闭但仍在忙碌，标记为关闭$"),
     lambda m: f"标签页 {m.group(1)} 已经被关掉但状态还挂着忙碌喵，小鹿把它标记成关闭"),
    (re.compile(r"^\[([^\]]+)\] 会话失效，已请求取消任务 \((.+)$", re.S),
     lambda m: f"标签页 {m.group(1)} 的会话失效了喵，小鹿已经请求取消它手上的任务（{m.group(2)}"),
    (re.compile(r"^\[([^\]]+)\] 手动终止: (.+)$", re.S),
     lambda m: f"小鹿按指令手动终止了标签页 {m.group(1)} 的任务喵（{m.group(2)}）"),
    (re.compile(r"^\[([^\]]+)\] 强制释放开始: (.+)$", re.S),
     lambda m: f"小鹿开始强制释放标签页 {m.group(1)} 喵（{m.group(2)}）"),
    (re.compile(r"^\[([^\]]+)\] 跳过无所有权释放 \((.+)$", re.S),
     lambda m: f"这个释放请求不是标签页 {m.group(1)} 当前任务发起的喵，小鹿按规矩跳过（{m.group(2)}"),
    (re.compile(r"^\[([^\]]+)\] 会话占用: mode=(\w+), (.+)$", re.S),
     lambda m: f"小鹿把标签页 {m.group(1)} 占下来干活啦（方式 {m.group(2)}，{m.group(3)}）"),
    (re.compile(r"^\[([^\]]+)\] 会话释放: (.+)$", re.S),
     lambda m: f"小鹿把标签页 {m.group(1)} 放回池子里啦（{m.group(2)}）"),
    (re.compile(r"^\[([^\]]+)\] 绑定请求标签页: (.+)$", re.S),
     lambda m: f"小鹿把请求和标签页 {m.group(1)} 绑好啦（{m.group(2)}）"),
    (re.compile(r"^Timed out waiting for tab #(\d+) \((.+)$", re.S),
     lambda m: f"小鹿等标签页 #{m.group(1)} 等到超时了喵（{m.group(2)}"),
    (re.compile(r"^Timed out waiting for route group '(.+?)' \((.+)$", re.S),
     lambda m: f"小鹿等路由组 {m.group(1)} 的空位等到超时了喵（{m.group(2)}"),
    (re.compile(r"^No tab matches exact URL '(.+)$", re.S),
     lambda m: f"小鹿没找到和这个 URL 完全匹配的标签页喵（'{m.group(1)}"),
    (re.compile(r"^No tab matches route domain '(.+?)'$"),
     lambda m: f"小鹿没找到匹配域名 {m.group(1)} 的标签页喵"),
    (re.compile(r"^(?:\[POOL\] )?skipped automatic isolated-cookie takeover because existing tabs are never closed: (.+)$"),
     lambda m: f"小鹿跳过了隔离 Cookie 的自动接管喵，因为现有标签页设置了永不关闭（{m.group(1)}）"),
    (re.compile(r"^扫描标签页失败: (.+)$", re.S),
     lambda m: f"小鹿这轮扫描标签页没成功喵（{m.group(1)}）——多半是浏览器暂时没响应，下轮会再试"),
    # ---- 全局网络监听 ----
    (re.compile(r"^\[GlobalNet\] 启动监听: (\S+) pattern=(.+)$"),
     lambda m: f"小鹿在标签页 {m.group(1)} 挂上了全局网络监听喵（目标 {m.group(2)}）"),
    (re.compile(r"^\[GlobalNet\] 停止监听: (\S+?)(?: \((.+)\))?$"),
     lambda m: f"小鹿把标签页 {m.group(1)} 的全局网络监听收起来了喵" + (f"（原因 {m.group(2)}）" if m.group(2) else "")),
    (re.compile(r"^\[GlobalNet\] 请求停止监听: (\S+) \((.+)\)$"),
     lambda m: f"小鹿请求停掉标签页 {m.group(1)} 的全局监听喵（原因 {m.group(2)}）"),
    (re.compile(r"^\[GlobalNet\] listen\.stop timed out after ([\d.]+)s; listener remains unavailable$"),
     lambda m: f"小鹿喊停全局监听，等了 {m.group(1)}s 它还没停喵，这个监听先标记为不可用"),
    (re.compile(r"^\[GlobalNet\] worker did not stop promptly: (\S+) \((.+)\)$"),
     lambda m: f"全局监听的后台工人没有立刻停下喵（标签页 {m.group(1)}，详情 {m.group(2)}）"),
    # ---- 网络监听回退 ----
    (re.compile(r"^\[(?:NetworkMonitor|NETWORK_MONITOR|MONIT)\] 网络监听超时，回退到 DOM 模式: (.+)$", re.S),
     lambda m: f"网络监听这条路超时了喵（{m.group(1)}），小鹿改用 DOM 模式接着收内容"),
    (re.compile(r"^\[(?:NetworkMonitor|NETWORK_MONITOR|MONIT)\] 网络监听错误，回退到 DOM 模式: (.+)$", re.S),
     lambda m: f"网络监听出了点状况喵（{m.group(1)}），小鹿回退到 DOM 模式继续收内容"),
    (re.compile(r"^\[(?:NetworkMonitor|NETWORK_MONITOR|MONIT)\] 首段等待超时且仍无有效正文，回退到 DOM 监听 \((.+)\)$", re.S),
     lambda m: f"等首段正文等太久了喵（{m.group(1)}），小鹿切到 DOM 监听兜底"),
    (re.compile(r"^\[(?:NetworkMonitor|NETWORK_MONITOR|MONIT)\] 流式响应静默但未收到完成标志，回退到 DOM 补齐 \((.+)\)$", re.S),
     lambda m: f"流式响应安静了但没看到完成标志喵（{m.group(1)}），小鹿用 DOM 把内容补齐"),
    (re.compile(r"^\[(?:NetworkMonitor|NETWORK_MONITOR|MONIT)\] 目标流响应缺少响应元数据和响应体，回退到 DOM 监听 \((.+)$", re.S),
     lambda m: f"目标流响应缺了元数据和正文喵（{m.group(1)}，小鹿回退到 DOM 监听"),
    (re.compile(r"^\[(?:NetworkMonitor|NETWORK_MONITOR)\] 已重建监听 \((.+)\)$"),
     lambda m: f"小鹿把网络监听重新架好啦（时机 {m.group(1)}）"),
    (re.compile(r"^\[(?:NetworkMonitor|NETWORK_MONITOR)\] 已记录发送基线 \((.+)$", re.S),
     lambda m: f"小鹿记下了发送前的网络基线喵（{m.group(1)}"),
    # ---- 工作流步骤 ----
    (re.compile(r"^\[STEP (\d+)\] 开始: action=([A-Z_]+|-), target=(.+?), selector=(.+?), (.+)$", re.S),
     lambda m: f"小鹿开始第 {m.group(1)} 步 {m.group(2)} 啦（目标 {m.group(3)}，{m.group(5)}）"),
    (re.compile(r"^\[STEP (\d+)\] 完成: action=([A-Z_]+|-), target=(.+?), elapsed=([\d.]+)s(?:, (.+))?$", re.S),
     lambda m: f"小鹿做完第 {m.group(1)} 步 {m.group(2)} 啦（目标 {m.group(3)}，耗时 {m.group(4)}s" + (f"，{m.group(5)}）" if m.group(5) else "）")),
    (re.compile(r"^\[STEP (\d+)\] 跳过: action=([A-Z_]+|-), target=(.+?), reason=(.+)$", re.S),
     lambda m: f"小鹿把第 {m.group(1)} 步 {m.group(2)} 跳过去了喵（目标 {m.group(3)}，原因 {m.group(4)}）"),
    (re.compile(r"^\[STEP (\d+)\] 中断: action=([A-Z_]+|-), target=(.+?), elapsed=([\d.]+)s, error=(.+)$", re.S),
     lambda m: f"第 {m.group(1)} 步 {m.group(2)} 中断了喵（目标 {m.group(3)}，进行到 {m.group(4)}s，原因 {m.group(5)}）"),
    (re.compile(r"^\[STEP (\d+)\] 失败: action=([A-Z_]+|-), target=(.+?), elapsed=([\d.]+)s, error=(.+)$", re.S),
     lambda m: f"第 {m.group(1)} 步 {m.group(2)} 失败了喵（目标 {m.group(3)}，进行到 {m.group(4)}s，原因 {m.group(5)}）"),
    (re.compile(r"^\[请求块 (\d+)/(\d+)\] -{3,}\s*(.+?)\s*-{3,}$"),
     lambda m: f"—— 请求进入第 {m.group(1)}/{m.group(2)} 阶段 · {_cute_stage_title(m.group(3))} ——"),
    (re.compile(r"^\[WORKFLOW\] 主循环结束: image_enabled=(True|False), should_stop=(True|False)$"),
     lambda m: f"小鹿把主工作流循环跑完啦（图片提取={m.group(1)}，停止信号={m.group(2)}）"),
    (re.compile(r"^\[FILL_INPUT_START\] 开始填充输入框, 当前 URL: (.+)$", re.S),
     lambda m: f"小鹿开始往输入框里填内容喵（页面 {m.group(1)}）"),
    (re.compile(r"^\[SEND_CLICK_START\] 开始点击发送按钮, 当前 URL: (.+?),? 发送前输入框长度: (\d+)$", re.S),
     lambda m: f"小鹿伸爪去点发送按钮啦（输入框现有 {m.group(2)} 字符，页面 {m.group(1)}）"),
    (re.compile(r"^\[CLICK_NEW_CHAT\] 点击新建对话前 URL: (.+)$", re.S),
     lambda m: f"小鹿准备点新建对话按钮喵（当前页面 {m.group(1)}）"),
    (re.compile(r"^\[FILL_COMPLETED\] 输入框填充完成: expected_len=(\d+), after_new_chat=(True|False)$"),
     lambda m: f"小鹿把输入框填好啦（目标长度 {m.group(1)}，新会话后首填={m.group(2)}）"),
    (re.compile(r"^\[DOM_BASELINE\] 已捕获发送后 DOM 基线: count=(\d+), text_len=(\d+), images=(\d+)$"),
     lambda m: f"小鹿拍下了发送后的页面基线快照喵（节点 {m.group(1)}，文本 {m.group(2)} 字符，图片 {m.group(3)} 张）"),
    (re.compile(r"^\[Executor\] 已预捕获 DOM 发送基线 \((.+)\)$"),
     lambda m: f"小鹿提前拍好了发送基线快照喵（时机 {m.group(1)}）"),
    (re.compile(r"^\[ACTIVE_INPUT_RESOLVED\] Found active text input: (.+)$", re.S),
     lambda m: f"小鹿定位到当前激活的输入框啦（{m.group(1)}）"),
    (re.compile(r"^\[ACTIVE_INPUT_RESOLVED\] 获取活动输入框特征异常: (.+)$", re.S),
     lambda m: f"小鹿没看清激活输入框的特征喵（{m.group(1)}）"),
    (re.compile(r"^\[INTERACT\] 检测到刚完成新会话/长文本填充，放宽发送按钮等待 \(target=(.+?), timeout=([\d.]+)s\)$"),
     lambda m: f"小鹿刚填完长内容，给发送按钮多留点等待时间喵（目标 {m.group(1)}，上限 {m.group(2)}s）"),
    (re.compile(r"^\[STEALTH_FOCUS\] Native DOM focus successful on attempt (\d+)$"),
     lambda m: f"小鹿第 {m.group(1)} 次尝试就把输入框焦点拿到手啦"),
    (re.compile(r"^\[PASTE_VERIFY\] 精确匹配成功: (.+)$", re.S),
     lambda m: f"小鹿逐字核对过粘贴内容，完全一致喵（{m.group(1)}）"),
    (re.compile(r"^\[STEALTH_INPUT\] 粘贴完成: (.+)$", re.S),
     lambda m: f"小鹿把内容悄悄贴进输入框啦（{m.group(1)}）"),
    (re.compile(r"^(?:\[[^\]]+\] )?工作流开始前启用后台保活$"),
     lambda m: "小鹿先给页面开了后台保活，防止它打瞌睡喵"),
    (re.compile(r"^已记录发送前图片基线: nodes=(\d+), marked=(\d+), urls=(\d+)$"),
     lambda m: f"小鹿记下了发送前的图片基线喵（节点 {m.group(1)}，标记 {m.group(2)}，链接 {m.group(3)}）"),
    (re.compile(r"^(?:\[[^\]]+\] )?多模态结果: items=(\d+), text_items=(\d+), images=(\d+), result_len=(\d+)$"),
     lambda m: f"小鹿汇总好这轮的多模态结果啦（共 {m.group(1)} 项：文本 {m.group(2)} 段、图片 {m.group(3)} 张，正文 {m.group(4)} 字符）"),
    (re.compile(r"^\[IMAGE_FLOW_DIAG\] (.+)$", re.S),
     lambda m: f"小鹿记了一笔图片流程账喵（{m.group(1)}）"),
    # ---- 解析器 ----
    (re.compile(r"^\[LmarenaParser\] 检测到重复(思考|正文)响应，跳过$"),
     lambda m: f"小鹿又滤掉了一条重复的{m.group(1)}响应喵"),
    (re.compile(r"^\[([A-Za-z]+Parser)\] duplicate (\w+) response ignored$"),
     lambda m: f"小鹿忽略了一条重复的 {m.group(2)} 侧回答喵（{m.group(1)}）"),
    (re.compile(r"^已注册响应解析器: (\S+) -> (\S+)$"),
     lambda m: f"小鹿把解析器 {m.group(2)} 挂到 {m.group(1)} 名下啦"),
    (re.compile(r"^动态加载解析器成功: (\S+) -> (\S+)$"),
     lambda m: f"小鹿按配置动态加载了解析器喵（{m.group(1)} → {m.group(2)}）"),
    (re.compile(r"^\[StreamMonitor\] large cached stream results cleared$"),
     lambda m: "小鹿顺手清掉了缓存的大块流结果，给内存腾地方喵"),
    (re.compile(r"^站点列表过滤: 总数 (\d+) -> 过滤后 (\d+) \(移除 (\d+) 个\)$"),
     lambda m: f"小鹿筛完站点列表啦（{m.group(1)} 个里保留 {m.group(2)} 个，移除 {m.group(3)} 个）"),
    # ---- 新建对话 / 页面切换 ----
    (re.compile(r"^\[TRANSITION_EMPTY\] 新建对话后已离开旧会话 URL，当前是空白/入口路由: (.+)$", re.S),
     lambda m: f"小鹿确认新建对话已经离开旧会话，现在停在入口页啦（{m.group(1)}）"),
    (re.compile(r"^\[TRANSITION_TIMEOUT\] 新建对话后 URL 未在限定时间内切换，触发工作流重试: (.+)$", re.S),
     lambda m: f"新建对话后页面一直没切换喵（{m.group(1)}），小鹿触发工作流重试"),
    (re.compile(r"^(?:\[WARNING\] )?\[FILL_INPUT\] 新建对话后填充输入框，但 URL 未切换! (.+)$", re.S),
     lambda m: f"小鹿发现新建对话后 URL 没变喵，可能还停在旧会话（{m.group(1)}）"),
    (re.compile(r"^\[SELECT_MODEL\] 页面已是目标模型，跳过切换: model=(.+)$"),
     lambda m: f"小鹿看了一眼模型选择器，已经是 {m.group(1)} 啦，不用再切喵"),
    (re.compile(r"^\[CLICK_RETRY\] 验证失败，准备重试: target=(\S+), attempt=(\d+)/(\d+), interval=([\d.]+)s$"),
     lambda m: f"点击 {m.group(1)} 后没通过验证喵，小鹿准备第 {m.group(2)}/{m.group(3)} 次重试（间隔 {m.group(4)}s）"),
    # ---- 附件 / 图片 / 媒体 ----
    (re.compile(r"^\[ATTACHMENT\] (\S+) not ready after ([\d.]+)s: (.+)$", re.S),
     lambda m: f"附件 {m.group(1)} 等了 {m.group(2)}s 还没就绪喵（{m.group(3)}）"),
    (re.compile(r"^(?:\[[^\]]+\] )?图片格式或尺寸验证失败: (.+)$", re.S),
     lambda m: f"这张图片没通过格式/尺寸检查喵（{m.group(1)}），小鹿先跳过它"),
    (re.compile(r"^(?:\[[^\]]+\] )?不支持的图片格式: (.+)$", re.S),
     lambda m: f"这张图片的格式小鹿处理不了喵（{m.group(1)}）"),
    (re.compile(r"^(?:\[[^\]]+\] )?图片 (\d+) 上传后未观察到任何附件活动: (.+)$", re.S),
     lambda m: f"第 {m.group(1)} 张图片传上去后页面没什么动静喵（{m.group(2)}）"),
    (re.compile(r"^后台图片下载完成: (.+?) \((\d+) bytes\)$"),
     lambda m: f"小鹿在后台把图片下载好啦（{m.group(1)}，{m.group(2)} 字节）"),
    (re.compile(r"^后台图片下载失败（忽略）: (.+)$", re.S),
     lambda m: f"后台有张图片没下载成喵（{m.group(1)}），不影响主流程"),
    (re.compile(r"^图片预设文件不存在: (.+)$"),
     lambda m: f"小鹿没找到这个图片预设文件喵（{m.group(1)}）"),
    (re.compile(r"^媒体转码失败: (.+)$", re.S),
     lambda m: f"媒体转码没成功喵（{m.group(1)}）"),
    # ---- Auto-Battle 自动对战脚本 ----
    (re.compile(r"^\[Auto-Battle\] 正在执行第 (\d+)/(\d+) 次流程\.\.\.$"),
     lambda m: f"自动对战开始第 {m.group(1)}/{m.group(2)} 轮流程啦喵"),
    (re.compile(r"^\[Auto-Battle\] 正在输入提示词\.\.\.$"),
     lambda m: "小鹿正在往输入框里写提示词喵"),
    (re.compile(r"^\[Auto-Battle\] 本轮提示词已随机插入 (\d+) 个字符$"),
     lambda m: f"小鹿往这轮提示词里随机撒了 {m.group(1)} 个字符做扰动喵"),
    (re.compile(r"^\[Auto-Battle\] 点击 New Chat 新建对话: attempt=(\d+)/(\d+)$"),
     lambda m: f"小鹿去点 New Chat 开新对话喵（第 {m.group(1)}/{m.group(2)} 次尝试）"),
    (re.compile(r"^\[Auto-Battle\] 准备点击 New Chat: \((\d+), (\d+)\)$"),
     lambda m: f"小鹿瞄准了 New Chat 按钮喵（落点 {m.group(1)}, {m.group(2)}）"),
    (re.compile(r"^\[Auto-Battle\] 正在按 Enter 提交: attempt=(\d+)/(\d+)$"),
     lambda m: f"小鹿按下 Enter 提交问题喵（第 {m.group(1)}/{m.group(2)} 次尝试）"),
    (re.compile(r"^\[Auto-Battle\] 发送后等待 AI 输出完成，最长 (\d+) 秒；完成后才进入下一轮 New Chat。$"),
     lambda m: f"发送完毕，小鹿最长等 {m.group(1)}s 让 AI 把话说完，再开下一轮喵"),
    (re.compile(r"^\[Auto-Battle\] 仍在等待输出完成：(.+)$", re.S),
     lambda m: f"小鹿还在等 AI 把话说完喵（{m.group(1)}）"),
    (re.compile(r"^\[Auto-Battle\] 发送已确认: (.+)$", re.S),
     lambda m: f"小鹿确认这轮问题已经发出去啦（{m.group(1)}）"),
    (re.compile(r"^\[Auto-Battle\] New Chat 已切换页面: (.+)$", re.S),
     lambda m: f"小鹿确认 New Chat 已经翻到新页面啦喵（{m.group(1)}）"),
    (re.compile(r"^\[Auto-Battle\] New Chat 落地页已就绪（URL 可保持不变）: url=(.+?) input_len=(\d+)$", re.S),
     lambda m: f"New Chat 落地页已经就绪喵（输入框长度 {m.group(2)}，页面 {m.group(1)}）"),
    (re.compile(r"^\[Auto-Battle\] 第 (\d+) 次提问输出完成，保持不投票、不翻牌；下一轮将 New Chat。$"),
     lambda m: f"第 {m.group(1)} 次提问的输出收完啦，小鹿按规矩不投票不翻牌，下一轮直接 New Chat 喵"),
    (re.compile(r"^\[Auto-Battle\] 等待 AI 输出完成超时（(\d+) 秒），不再继续等，直接进入下一轮 New Chat；url=(.+)$", re.S),
     lambda m: f"小鹿等了 {m.group(1)}s 还没等到 AI 说完喵，先不等了，直接开下一轮 New Chat（页面 {m.group(2)}）"),
    (re.compile(r"^\[Auto-Battle\] 第 (\d+) 次提问等待超时，保持不投票、不翻牌；下一轮将直接 New Chat。$"),
     lambda m: f"第 {m.group(1)} 次提问等超时了喵，小鹿保持不投票不翻牌，下一轮直接 New Chat"),
    (re.compile(r"^\[Auto-Battle\] New Chat 后未在 (\d+) 秒内确认页面切换，本次点击视为失败。 ?latest=(.+)$", re.S),
     lambda m: f"小鹿点了 New Chat 但 {m.group(1)}s 内页面没切换，这次点击当作没成功喵（当前 {m.group(2)}）"),
    (re.compile(r"^\[Auto-Battle\] New Chat 尚未切到新对话，等待后重试: attempt=(\d+)/(\d+)$"),
     lambda m: f"New Chat 还没切到新对话喵，小鹿等一下再试（第 {m.group(1)}/{m.group(2)} 次）"),
    (re.compile(r"^\[Auto-Battle\] 未找到可见 New Chat 按钮，等待后重试: attempt=(\d+)/(\d+)$"),
     lambda m: f"小鹿暂时没看到 New Chat 按钮喵，等一下再找（第 {m.group(1)}/{m.group(2)} 次）"),
    (re.compile(r"^\[Auto-Battle\] New Chat 后未检测到 URL 切换，但当前不像旧会话页且输入框已清空，继续本轮。$"),
     lambda m: "URL 虽然没变，但页面不像旧会话、输入框也清空了喵，小鹿判断可以继续本轮"),
    (re.compile(r"^\[Auto-Battle\] New Chat 多次重试仍未确认成功，本轮不输入、不发送，避免浪费到旧对话。$"),
     lambda m: "New Chat 试了好几次都没确认成功喵，这轮小鹿就不输入不发送了，免得把内容浪费在旧对话里"),
    (re.compile(r"^\[Auto-Battle\] New Chat failed (\d+) consecutive times; navigating to (.+?) and continuing$"),
     lambda m: f"New Chat 连续失败 {m.group(1)} 次了喵，小鹿直接导航到 {m.group(2)} 换个姿势继续"),
    (re.compile(r"^\[Auto-Battle\] 发送动作后未确认请求已提交: (.+)$", re.S),
     lambda m: f"小鹿按了发送但没看到提交确认喵（{m.group(1)}）"),
    (re.compile(r"^\[Auto-Battle\] 第 (\d+) 次循环因人机验证跳转 (.+)$", re.S),
     lambda m: f"第 {m.group(1)} 轮撞上人机验证了喵，小鹿先跳转 {m.group(2)} 绕一下"),
    (re.compile(r"^\[Auto-Battle\]\[CAPTCHA\] detected: (.+?); will recheck for up to (\d+) seconds$", re.S),
     lambda m: f"小鹿撞见人机验证了喵（{m.group(1)}），先观察最多 {m.group(2)}s 看它会不会自己消失"),
    (re.compile(r"^\[Auto-Battle\]\[CAPTCHA\] still visible after ([\d.]+)s: (.+?); navigating to (.+)$", re.S),
     lambda m: f"人机验证 {m.group(1)}s 后还立在那儿喵（{m.group(2)}），小鹿只好跳转 {m.group(3)} 绕过去"),
    (re.compile(r"^\[Auto-Battle\]\[RATE-LIMIT\] detected at (.+?): (.+?); rotating Clash proxy immediately$", re.S),
     lambda m: f"小鹿撞上限流了喵（{m.group(1)} 阶段：{m.group(2)}），马上换个代理出口"),
    (re.compile(r"^\[Auto-Battle\] 第 (\d+) 次提问命中限流，已立即切换 IP；下一轮将 New Chat。$"),
     lambda m: f"第 {m.group(1)} 次提问被限流了喵，小鹿已经切了 IP，下一轮直接 New Chat"),
    (re.compile(r"^\[Auto-Battle\]\[CLAUDE-CHECK\] no individual response side passed the complete filter; url=(.+)$", re.S),
     lambda m: f"小鹿检查了两侧回答，这轮没有一侧通过完整度筛选喵（{m.group(1)}）"),
    (re.compile(r"^\[Auto-Battle\]\[CLAUDE-WATCH\] (.+)$", re.S),
     lambda m: f"小鹿的 Claude 观察哨有新动静喵（{m.group(1)}）"),
    (re.compile(r"^\[Auto-Battle\] interrupted at run (\d+); returning collected URLs: (.+)$", re.S),
     lambda m: f"自动对战在第 {m.group(1)} 轮被叫停了喵（{m.group(2)}），小鹿先把收集到的 URL 交上来"),
    (re.compile(r"^\[Auto-Battle\] failed to persist final snapshot before stopping: (.+)$", re.S),
     lambda m: f"停下来之前想存个最终快照但没存上喵（{m.group(1)}）"),
    # ---- 工具调用 ----
    (re.compile(r"^\[tool_calling\] 函数调用候选校验未通过，准备进行内部修复重试 (.+)$", re.S),
     lambda m: f"函数调用的格式没过校验喵，小鹿准备在内部悄悄修一轮（{m.group(1)}）"),
    (re.compile(r"^\[tool_calling\] 修复耗尽后没有通过校验的 tool_calls，拒绝降级 原因=(.+)$", re.S),
     lambda m: f"修了几轮还是没有合格的函数调用喵（原因 {m.group(1)}），小鹿按规矩拒绝降级输出"),
    # ---- 命令引擎 ----
    (re.compile(r"^\[CMD\] 外部结果事件已记录: (\S+) \(标签页=([^,]+), 摘要=(.+)\)$", re.S),
     lambda m: f"小鹿把外部结果事件记下来了喵（{m.group(1)}，标签页 {m.group(2)}，摘要 {m.group(3)}）"),
    (re.compile(r"^\[CMD\] Python 脚本正在以非沙箱模式执行，请仅用于完全可信配置$"),
     lambda m: "提醒喵：Python 脚本现在是非沙箱模式在跑，请只用于完全可信的配置"),
    (re.compile(r"^\[CMD_RESULT\] browser profile name 为空，(\d+) 条候选命中将被跳过。(.+)$", re.S),
     lambda m: f"浏览器 profile 名是空的喵，{m.group(1)} 条候选命中只能先跳过（{m.group(2)}）"),
    # ---- 等待 / 退出 ----
    (re.compile(r"^\[Timeout\] 等待 AI 开始超时（([\d.]+)s）$"),
     lambda m: f"小鹿等 AI 开口等了 {m.group(1)}s 都没动静喵，这轮先超时收场"),
    (re.compile(r"^\[Exit\] 未检测到 AI 回复，退出监控$"),
     lambda m: "小鹿没等到 AI 回复喵，先退出这轮监控"),
    # ---- 网络监听（跨级别出现的形态）----
    (re.compile(r"^\[(?:NetworkMonitor|NETWORK_MONITOR|MONIT)\] 成功锁定流目标响应 \(status=(\d+), method=(\S+), url=(.+?), 初始长度=(\d+) 字符\)$", re.S),
     lambda m: f"小鹿已经盯上目标流响应啦，正在努力把它捞出来喵（状态 {m.group(1)}，方法 {m.group(2)}，初始 {m.group(4)} 字符，地址 {m.group(3)}）"),
    (re.compile(r"^\[(?:NetworkMonitor|NETWORK_MONITOR|MONIT)\] 解析失败: (.+)$", re.S),
     lambda m: f"这轮流内容解析失败了喵（{m.group(1)}）"),
    # ---- 标签页池补充形态 ----
    (re.compile(r"^\[([^\]]+)\] 已释放 \((.+)$", re.S),
     lambda m: f"小鹿把标签页 {m.group(1)} 放回池子里啦（{m.group(2)}"),
    (re.compile(r"^\[(req-[^\]]+)\] 绑定标签页: (.+?) -> (\S+) \((.+)\)$", re.S),
     lambda m: f"小鹿把请求 {m.group(1)} 和标签页 {m.group(3)} 牵好手啦（之前 {m.group(2)}，{m.group(4)}）"),
    (re.compile(r"^\[([^\]]+)\] 工作流释放请求: (.+)$", re.S),
     lambda m: f"工作流请求释放标签页 {m.group(1)} 喵（{m.group(2)}）"),
    (re.compile(r"^\[([^\]]+)\] 本轮将新建对话: (.+)$", re.S),
     lambda m: f"标签页 {m.group(1)} 这轮会先新建对话喵（{m.group(2)}）"),
    (re.compile(r"^\[([^\]]+)\] 记录会话活动: (.+)$", re.S),
     lambda m: f"小鹿记下了标签页 {m.group(1)} 的最新活动啦（{m.group(2)}）"),
    (re.compile(r"^TabPool\s*(?:→|->)\s*(.+)$", re.S),
     lambda m: f"小鹿把这轮请求领到 {m.group(1)} 啦"),
    (re.compile(r"^\[POOL\] 标签页已被占用: tab_id=(.+)$", re.S),
     lambda m: f"小鹿把这轮请求领到 {m.group(1)} 啦"),
    (re.compile(r"^\[ROUTE\] 请求上下文已创建$"),
     lambda m: "请求已经开始创建了喵"),
    (re.compile(r"^\[(req-[^\]]+)\] completed request worker cleanup is still running; defer tab retirement check \((.+)$", re.S),
     lambda m: f"上个请求（{m.group(1)}）的清理还在收尾喵，标签页退休检查先推迟一下（{m.group(2)}"),
    (re.compile(r"^使用缓存配置: (\S+) \[预设: (.+)\]$"),
     lambda m: f"小鹿直接用上了缓存配置喵（{m.group(1)}，预设 {m.group(2)}）"),
    (re.compile(r"^清理了 (\d+) 个旧请求$"),
     lambda m: f"小鹿清掉了 {m.group(1)} 个旧请求记录喵"),
    # ---- 页面交互补充形态 ----
    (re.compile(r"^(?:\[[^\]]+\] )?后台安全 DOM 点击完成: target=(.+?), total=([\d.]+)s, (.+)$", re.S),
     lambda m: f"小鹿在后台稳稳点完了 {m.group(1)} 喵（耗时 {m.group(2)}s，{m.group(3)}）"),
    (re.compile(r"^(?:\[[^\]]+\] )?消息汇总: messages=(\d+), used=(\d+), prompt_len=(\d+), (.+)$", re.S),
     lambda m: f"小鹿汇总好这次的消息啦（共 {m.group(1)} 条、用了 {m.group(2)} 条，正文 {m.group(3)} 字符，{m.group(4)}）"),
    (re.compile(r"^(?:\[[^\]]+\] )?跳过鼠标预热: (.+)$", re.S),
     lambda m: f"小鹿这次省掉了鼠标预热喵（{m.group(1)}）"),
    (re.compile(r"^(?:\[[^\]]+\] )?CDP BoxModel 坐标获取失败: (.+)$", re.S),
     lambda m: f"小鹿这次没拿到元素的 BoxModel 坐标喵（{m.group(1)}），会换别的办法定位"),
    (re.compile(r"^\[INTERACT_TIMING\] (.+)$", re.S),
     lambda m: f"小鹿记了一条交互耗时打点喵（{m.group(1)}）"),
    (re.compile(r"^\[FILL_STABLE\] 输入框已稳定 \((.+)\)$", re.S),
     lambda m: f"输入框内容已经稳稳当当了喵（{m.group(1)}）"),
    # ---- 图片粘贴 / 提取补充形态 ----
    (re.compile(r"^(?:\[[^\]]+\] )?开始粘贴 (\d+) 张图片$"),
     lambda m: f"小鹿开始往页面里贴 {m.group(1)} 张图片喵"),
    (re.compile(r"^(?:\[[^\]]+\] )?执行 Control\+V \(图片 (\d+), attempt (\d+)/(\d+)\)$"),
     lambda m: f"小鹿按下 Ctrl+V 贴第 {m.group(1)} 张图片喵（第 {m.group(2)}/{m.group(3)} 次尝试）"),
    (re.compile(r"^(?:\[[^\]]+\] )?第 (\d+) 张上传完成$"),
     lambda m: f"第 {m.group(1)} 张图片上传完成喵"),
    (re.compile(r"^(?:\[[^\]]+\] )?图片粘贴完成$"),
     lambda m: "图片全部贴好啦喵"),
    (re.compile(r"^(?:\[[^\]]+\] )?成功处理 (\d+) 张图片$"),
     lambda m: f"小鹿把 {m.group(1)} 张图片都处理好啦喵"),
    (re.compile(r"^提取完成: (\d+) 张图片, (\d+) 个视频, (\d+) 个音频$"),
     lambda m: f"小鹿提取收工啦：图片 {m.group(1)} 张、视频 {m.group(2)} 个、音频 {m.group(3)} 个喵"),
    (re.compile(r"^\[ATTACHMENT\] (\S+) ready after ([\d.]+)s: (.+)$", re.S),
     lambda m: f"附件 {m.group(1)} 已经就绪喵（等了 {m.group(2)}s，{m.group(3)}）"),
    (re.compile(r"^\[SEND\] Recent attachment upload detected before submit; reusing existing attachment tracking \(age=([\d.]+)s\)$"),
     lambda m: f"发送前发现附件刚上传过喵（{m.group(1)}s 前），小鹿直接复用现有的附件跟踪"),
    (re.compile(r"^\[PASTE_VERIFY\] 检测到异常粘贴结果: (.+)$", re.S),
     lambda m: f"粘贴结果看起来不太对劲喵（{m.group(1)}），小鹿会按流程补救"),
    (re.compile(r"^当前回复包含终止错误且没有本轮新图，跳过图片等待与历史图回退$"),
     lambda m: "这轮回复带着终止错误而且没有新图喵，小鹿跳过图片等待和历史图回退"),
    # ---- 工具调用补充 ----
    (re.compile(r"^\[tool_calling\] falling back to final-text mode \(len=(\d+)\)$"),
     lambda m: f"函数调用没走通喵，小鹿回退到纯文本模式输出（长度 {m.group(1)}）"),
    (re.compile(r"^\[tool_calling\] 并行工具调用部分成功，已放行通过校验的 tool_calls (.+)$", re.S),
     lambda m: f"并行函数调用只成功了一部分喵，通过校验的已经放行（{m.group(1)}）"),
    (re.compile(r"^\[tool_calling\] 函数调用候选已在内部修复后通过校验 (.+)$", re.S),
     lambda m: f"函数调用在内部修好啦，校验通过喵（{m.group(1)}）"),
    # ---- 告警桥 / 结果事件 ----
    (re.compile(r"^\[ALERT\]\[([^\]]+)\] 目标流告警：(.+)$", re.S),
     lambda m: f"目标流有告警喵（标签页 {m.group(1)}：{m.group(2)}）"),
    (re.compile(r"^\[RESULT_EVENT_BRIDGE\] Arena event emitted \((.+)\)$", re.S),
     lambda m: f"小鹿把 Arena 结果事件发出去啦（{m.group(1)}）"),
    (re.compile(r"^\[GlobalNet\] wait 异常: (\S+?),? err=(.*)$", re.S),
     lambda m: f"全局监听等待时出了点小异常喵（标签页 {m.group(1)}，err={m.group(2) or '-'}）"),
    (re.compile(r"^\[CMD\] 页面检查观察器已注入: 标签页=(.+?), 关键词=(.+)$", re.S),
     lambda m: f"小鹿把页面检查观察器注入好啦（标签页 {m.group(1)}，关键词 {m.group(2)}）"),
    (re.compile(r"^\[tab\] 请求消息摘要: (.+)$", re.S),
     lambda m: f"小鹿记了一份请求消息摘要喵（{m.group(1)}）"),
    # ---- Auto-Battle 诊断 ----
    (re.compile(r"^\[Auto-Battle\] 检测到可按下的投票按钮，判定 AI 输出完成；button=(.+)$", re.S),
     lambda m: f"小鹿看到投票按钮亮起来了，判定 AI 输出完成喵（按钮 {m.group(1)}）"),
    (re.compile(r"^\[Auto-Battle\]\[CLAUDE-FILTER\] skipped: (.+)$", re.S),
     lambda m: f"小鹿按筛选规则跳过了这轮候选喵（{m.group(1)}）"),
    (re.compile(r"^\[Auto-Battle\]\[THOUGHT-FOR-DIAG\] (.+)$", re.S),
     lambda m: f"小鹿记了一条思考诊断喵（{m.group(1)}）"),
    (re.compile(r"^\[Auto-Battle\]\[ROUND-DIAG\] (.+)$", re.S),
     lambda m: f"小鹿记了一条回合诊断喵（{m.group(1)}）"),
    (re.compile(r"^Arena Direct 模型目录已刷新: (\d+) 个文本模型$"),
     lambda m: f"小鹿刷新完 Arena Direct 的模型目录啦（{m.group(1)} 个文本模型）"),
    # ---- 服务启动 / 停止 ----
    (re.compile(r"^Universal Web-to-API 服务启动中\.\.\.$"),
     lambda m: "小鹿的 Universal Web-to-API 服务正在启动喵…"),
    (re.compile(r"^监听地址: (.+)$"),
     lambda m: f"服务监听地址是 {m.group(1)} 喵"),
    (re.compile(r"^📁 图片下载目录: (.+)$"),
     lambda m: f"图片会保存到 {m.group(1)} 喵"),
    (re.compile(r"^\[startup\] 已启动一次性版本检查$"),
     lambda m: "小鹿顺手做了一次版本检查喵"),
    (re.compile(r"^\[startup\] 命令调度器: running$"),
     lambda m: "命令调度器已经跑起来啦喵"),
    (re.compile(r"^🚀 服务已就绪！$"),
     lambda m: "🚀 小鹿这边一切就绪，可以开工啦喵！"),
    (re.compile(r"^👋 服务已停止$"),
     lambda m: "👋 小鹿收工，服务已经停止喵"),
    (re.compile(r"^服务正在关闭\.\.\.$"),
     lambda m: "小鹿开始收拾东西，服务正在关闭喵…"),
    # ---- 启动配置回显 ----
    (re.compile(r"^(?:\[CONFIG\] )?日志级别: (.+)$"),
     lambda m: f"小鹿这次的日志级别是 {m.group(1)} 喵"),
    (re.compile(r"^(?:\[CONFIG\] )?调试模式: (True|False)$"),
     lambda m: "调试模式开着呢喵" if m.group(1) == "True" else "调试模式没开喵"),
    (re.compile(r"^(?:\[CONFIG\] )?浏览器端口: (\d+)$"),
     lambda m: f"受控浏览器的调试端口是 {m.group(1)} 喵"),
    (re.compile(r"^\[CONFIG\] 配置文件: (.+?) \(存在: (True|False)\)$"),
     lambda m: f"配置文件在 {m.group(1)} 喵（" + ("已经找到啦" if m.group(2) == "True" else "还没找到") + "）"),
    (re.compile(r"^\[CONFIG\] ([A-Z_]+) = (.+)$", re.S),
     lambda m: f"小鹿读到配置 {m.group(1)} = {m.group(2)} 喵"),
    (re.compile(r"^\[SYS\] 浏览器连接成功$"),
     lambda m: "浏览器已经顺利连上了喵"),
    (re.compile(r"^\[SYS\] 关闭浏览器连接$"),
     lambda m: "浏览器连接准备收工了喵"),
    # ---- 点击 / 输入确认补充 ----
    (re.compile(r"^\[INTERACT_CLICK\] 后台 CDP 点击完成: target=(.+?), total=([\d.]+)s, (.+)$", re.S),
     lambda m: f"小鹿用 CDP 在后台把 {m.group(1)} 点好了喵（耗时 {m.group(2)}s，{m.group(3)}）"),
    (re.compile(r"^\[INPUT_ACTIVATE\] JS 输入结果已稳定，跳过物理激活 \((.+)\)$", re.S),
     lambda m: f"JS 写入的内容已经稳了喵，物理激活就省了（{m.group(1)}）"),
    (re.compile(r"^\[SEND_CLICK_END\] 发送按钮点击完成\. 发送前长度: (\d+), 发送后长度: (\d+), 判定成功=(True|False), 当前 URL: (.+)$", re.S),
     lambda m: (
         f"发送按钮点完啦，输入框 {m.group(1)}→{m.group(2)} 字符，小鹿判定发送成功喵（页面 {m.group(4)}）"
         if m.group(3) == "True"
         else f"发送按钮点完了但看起来还没成喵（输入框 {m.group(1)}→{m.group(2)} 字符，页面 {m.group(4)}）"
     )),
    (re.compile(r"^\[CLICK_VERIFY\] 点击结果已确认: (.+)$", re.S),
     lambda m: f"小鹿确认这次点击生效了喵（{m.group(1)}）"),
    (re.compile(r"^\[(?:NetworkMonitor|NETWORK_MONITOR|MONIT)\] 检测到流响应结构: (.+)$", re.S),
     lambda m: f"小鹿认出了流响应的结构喵（{m.group(1)}）"),
    (re.compile(r"^\[WORKFLOW\] 进入多模态提取分支：(.+)$", re.S),
     lambda m: f"小鹿拐进多模态提取分支啦（{m.group(1)}）"),
    (re.compile(r"^\[WORKFLOW_HINT\] (.+)$", re.S),
     lambda m: f"工作流小贴士喵：{m.group(1)}"),
    (re.compile(r"^Waiting for tab #(\d+) release\.\.\. \((.+)$", re.S),
     lambda m: f"小鹿还在等标签页 #{m.group(1)} 空出来喵（{m.group(2)}"),
    (re.compile(r"^图片查找完成: (\d+) 张图片 \((.+)\)$", re.S),
     lambda m: f"小鹿找图收工：{m.group(1)} 张图片喵（{m.group(2)}）"),
    (re.compile(r"^(?:\[[^\]]+\] )?即将发送多模态资源（Markdown），数量=(\d+)$"),
     lambda m: f"小鹿准备把 {m.group(1)} 项多模态资源用 Markdown 发出去喵"),
    (re.compile(r"^\[MD_MEDIA\] 已发送结构化多模态资源，共 (\d+) 项$"),
     lambda m: f"小鹿把 {m.group(1)} 项结构化多模态资源发出去啦喵"),
    # ---- 尾部高频补充 ----
    (re.compile(r"^结果容器已按发送前基线收敛到本轮新增图片: fresh=(\d+), total=(\d+)$"),
     lambda m: f"小鹿按发送前基线筛出了本轮新图喵（新增 {m.group(1)}，总共 {m.group(2)}）"),
    (re.compile(r"^\[CMD\] 周期调度器等待标签页池初始化: (.+)$", re.S),
     lambda m: f"周期调度器还在等标签页池就绪喵（{m.group(1)}）"),
    (re.compile(r"^\[POOL\] 标签页分配编号: tab_id=(.+?), idx=#(\d+)$"),
     lambda m: f"小鹿给标签页 {m.group(1)} 贴上了固定编号 #{m.group(2)} 喵"),
    (re.compile(r"^\[GlobalNet\] Arena 翻牌快照更新: (.+)$", re.S),
     lambda m: f"小鹿更新了 Arena 翻牌快照喵（{m.group(1)}）"),
    (re.compile(r"^命令引擎已初始化$"),
     lambda m: "命令引擎已经准备就绪喵"),
    (re.compile(r"^命令调度器已初始化$"),
     lambda m: "命令调度器已经准备就绪喵"),
    (re.compile(r"^(?:\[[^\]]+\] )?完成: target=(.+?), click=\((-?\d+),(-?\d+)\), (.+)$", re.S),
     lambda m: f"小鹿把 {m.group(1)} 点完啦（落点 {m.group(2)},{m.group(3)}，{m.group(4)}）"),
    (re.compile(r"^🆕 发现新标签页: (\S+) (?:→|->) (.+)$", re.S),
     lambda m: f"🆕 小鹿发现了一个新标签页喵（{m.group(1)} → {m.group(2)}）"),
    (re.compile(r"^网络流图片已替换 DOM 图片候选: stream=(\d+), dom=(\d+)$"),
     lambda m: f"网络流抓到的图片更靠谱，小鹿用它替换了 DOM 候选喵（流 {m.group(1)} 张，DOM {m.group(2)} 张）"),
    (re.compile(r"^图片定位：在当前回复内使用 '(.+?)'，找到 (\d+) 个$"),
     lambda m: f"小鹿用选择器 {m.group(1)} 在当前回复里找到 {m.group(2)} 张图喵"),
    (re.compile(r"^✅ 图片本地化完成: (\d+)/(\d+) 张$"),
     lambda m: f"✅ 小鹿把图片都搬回本地啦（{m.group(1)}/{m.group(2)} 张）"),
    (re.compile(r"^STREAM b'(\w+)' (\d+) (\d+)$"),
     lambda m: f"图片数据块 {m.group(1)} 经过喵（{m.group(2)}, {m.group(3)}）"),
    (re.compile(r"^\[CONFIG\] 这条 DEBUG 日志仅在 LOG_LEVEL=DEBUG 时显示$"),
     lambda m: "这条是 DEBUG 试音喵，看到它说明 DEBUG 日志已经打开啦"),
]


# ERROR 级别关键词兜底：没有命中具体规则时，按错误类别给出人话提示 + 完整原文
_ERROR_KEYWORD_HINTS: List[Tuple[Tuple[str, ...], str]] = [
    (("无法连接到浏览器", "浏览器连接失败", "connection refused", "err_connection", "调试端口", "browser closed", "disconnected"),
     "小鹿现在够不到浏览器喵，请确认受控浏览器还开着、调试端口正常"),
    (("超时", "timeout", "timed out"),
     "小鹿等到超时了喵，页面可能太慢或者卡住了"),
    (("元素未找到", "未找到", "not found", "找不到", "no such element"),
     "小鹿在页面上没找到要找的东西喵，可能页面改版或还没加载出来"),
    (("clipboard", "剪贴板", "粘贴失败", "paste_failed"),
     "剪贴板这条路出了问题喵，稍后会自动重试或换别的输入方式"),
    (("permission", "denied", "权限", "access is denied"),
     "被权限拦住了喵，请检查相关文件或系统权限"),
    (("已取消", "cancelled", "canceled", "取消", "中断", "interrupt"),
     "这个流程被取消或打断了喵"),
]


def _cuteify_error_message(logger_name: str, message_text: str) -> str:
    """ERROR/CRITICAL 展示层翻译：先解释哪里出了问题、可能怎么办，技术细节完整保留在括号里。"""
    text = str(message_text or "")
    if not text or not bool(BrowserConstants.get("LOG_INFO_CUTE_MODE")):
        return text
    if len(text) > 4000:
        return text

    # 高频具体错误
    browser_connect_fail = re.match(r"^浏览器连接失败: (.+)$", text, re.S)
    if browser_connect_fail:
        return (
            f"小鹿连不上浏览器了喵（浏览器连接失败: {browser_connect_fail.group(1)}）。"
            "请确认受控浏览器还开着、调试端口能访问；必要时用启动器重新拉起浏览器"
        )

    tab_list_fail = re.match(r"^获取标签页列表失败: (.+)$", text, re.S)
    if tab_list_fail:
        return (
            f"小鹿拿不到标签页列表喵（{tab_list_fail.group(1)}）。"
            "浏览器可能已经关闭或调试端口断开，请检查受控浏览器状态"
        )

    step_fail = re.match(r"^步骤执行失败 \[([A-Z_]+)\]: (.+)$", text, re.S)
    if step_fail:
        return f"工作流在 {step_fail.group(1)} 这一步摔了一跤喵（原因 {step_fail.group(2)}）"

    tool_calling_fail = re.match(r"^tool_calling_failed(\(tab=\d+\))?: (.+)$", text, re.S)
    if tool_calling_fail:
        tab_part = tool_calling_fail.group(1) or ""
        return f"这次函数调用流程失败了喵{tab_part}（{tool_calling_fail.group(2)}）"

    watchdog_fail = re.match(r"^\[BrowserWatchdog\] 受控浏览器调试端口不可达 \((.+)$", text, re.S)
    if watchdog_fail:
        return (
            f"看门狗发现受控浏览器的调试端口连不上了喵（{watchdog_fail.group(1)}。"
            "请确认浏览器没被关掉、端口没被占用"
        )

    workflow_interrupted = re.match(r"^\[([^\]]+)\] 工作流被命令打断: (.+)$", text, re.S)
    if workflow_interrupted:
        return f"标签页 {workflow_interrupted.group(1)} 的工作流被命令打断了喵（原因 {workflow_interrupted.group(2)}）"

    element_not_found = re.match(r"^元素未找到: (.+)$", text, re.S)
    if element_not_found:
        return (
            f"小鹿在页面上没找到目标元素喵（{element_not_found.group(1)}）。"
            "页面可能改版、还没加载完，或者选择器需要更新"
        )

    if text == "Future exception was never retrieved":
        return (
            "有个后台任务的异常没人接喵（Future exception was never retrieved）。"
            "通常是连接中断的余波，问题本体请看相邻的错误日志"
        )

    # 表驱动规则（与其他级别共用）
    alias = _apply_cute_rules(_CUTE_EXTRA_RULES, text)
    if alias:
        return alias

    # 关键词类别兜底：人话提示 + 完整原文
    lowered = text.lower()
    for keywords, hint in _ERROR_KEYWORD_HINTS:
        if any(keyword in lowered for keyword in (k.lower() for k in keywords)):
            return f"{hint}（{text}）"

    return text


def _cuteify_info_message(logger_name: str, message_text: str) -> str:
    text = str(message_text or "")
    name = str(logger_name or "").upper().strip()
    if not text or not bool(BrowserConstants.get("LOG_INFO_CUTE_MODE")):
        return text
    if len(text) > 4000:
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
        return f"小鹿把内容都分块写进去啦（共 {total_chunks} 块，共 {total_len} 字符）喵"

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
        return "小鹿重新贴了一下，这次内容（富文本格式）终于对上啦喵"

    if text == "浏览器连接成功":
        return "浏览器已经顺利连上了喵"
    if text == "关闭浏览器连接":
        return "浏览器连接准备收工了喵"
    if text == "发送成功":
        return "消息已经顺利发出去啦喵"
    if text == "发送成功（附件场景，已避免重复点击发送按钮）":
        return "附件场景也顺利发出去了喵，而且乖乖避开了重复点击发送按钮"
    network_finish_new_match = re.match(
        r"^\[NetworkMonitor\] 网络流监听正常完成 "
        r"\(检测到结束标志, 历时=([\d.]+)s, 捕获响应=(\d+), 产出文本块=(\d+), 提取字符数=(\d+)\)$",
        text,
    )
    if network_finish_new_match:
        duration, responses, _, _ = network_finish_new_match.groups()
        return f"小鹿盯完这轮监听流程啦，顺利收工喵（历时 {duration}s，抓取响应数 {responses}）"

    network_finish_reason_match = re.match(
        r"^\[NetworkMonitor\] 网络流监听完成 "
        r"\(reason=(.+?), 历时=([\d.]+)s, 捕获响应=(\d+), 产出文本块=(\d+), 提取字符数=(\d+)\)$",
        text,
    )
    if network_finish_reason_match:
        reason, duration, responses, _, chars = network_finish_reason_match.groups()
        return (
            f"小鹿盯完这轮网络监听啦，顺利收工喵"
            f"（{reason}，历时 {duration}s，捕获响应 {responses}，提取 {chars} 字符）"
        )

    extra_alias = _apply_cute_rules(_CUTE_EXTRA_RULES, text)
    if extra_alias:
        return extra_alias

    return text


def _cuteify_warning_message(logger_name: str, message_text: str) -> str:
    text = str(message_text or "")
    if not text or not bool(BrowserConstants.get("LOG_INFO_CUTE_MODE")):
        return text
    if len(text) > 4000:
        return text

    send_retry_window_match = _SEND_RETRY_WINDOW_PATTERN.match(text)
    if send_retry_window_match:
        max_wait, action = send_retry_window_match.groups()
        return f"小鹿发现发送还没确认，准备进入补发观察窗口（最长 {max_wait}s，动作 {action}）喵"

    send_retry_action_match = _SEND_RETRY_ACTION_PATTERN.match(text)
    if send_retry_action_match:
        elapsed, action = send_retry_action_match.groups()
        return f"小鹿发现消息没动静，又轻轻试了一次发送动作（已等待 {elapsed}s，动作 {action}）喵"

    attachment_retry_match = re.match(
        r"^\[SEND\] 附件发送首轮未确认，准备自动重试 "
        r"\(attempt=(\d+)/(\d+), interval=([\d.]+)s\)$",
        text,
    )
    if attachment_retry_match:
        attempt, max_attempts, interval = attachment_retry_match.groups()
        return f"小鹿还没看到附件发送确认，准备第 {attempt}/{max_attempts} 次补发（间隔 {interval}s）喵"

    attachment_retry_stop_match = re.match(
        r"^\[SEND\] 附件重试停止 \(reason=(.+), attempt=(\d+)/(\d+)\)$",
        text,
    )
    if attachment_retry_stop_match:
        reason, attempt, max_attempts = attachment_retry_stop_match.groups()
        return f"小鹿停止附件补发啦（原因 {reason}，进度 {attempt}/{max_attempts}）喵"

    extra_alias = _apply_cute_rules(_CUTE_EXTRA_RULES, text)
    if extra_alias:
        return extra_alias

    return text


def _cuteify_debug_message(logger_name: str, message_text: str) -> str:
    raw_text = str(message_text or "")
    if not raw_text or not bool(BrowserConstants.get("LOG_DEBUG_CUTE_MODE")):
        return raw_text
    if len(raw_text) > 4000:
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
        return cute(f"小鹿等了 {network_silence_match.group(1)}s 发现页面安静下来了，收起监听网啦喵")

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

    network_lock_match = re.match(
        r"^\[NetworkMonitor\] 成功锁定流目标响应 "
        r"\(status=(\d+), method=(.*), url=(.*), 初始长度=(\d+) 字符\)$",
        text,
    )
    if network_lock_match:
        status, method, url, body_size = network_lock_match.groups()
        return cute(f"小鹿已经盯上目标请求啦，正在努力把它捞出来喵（状态 {status}，初始长度 {body_size} 字符）")

    network_growth_new_match = re.match(
        r"^\[NetworkMonitor\] 流响应增长中 \(当前大小=(\d+) 字节\)$",
        text,
    )
    if network_growth_new_match:
        body_size = network_growth_new_match.group(1)
        return cute(f"小鹿看到响应正文还在继续长大喵（已接收 {body_size} 字节）")

    network_wait_parser_match = re.match(
        r"^\[NetworkMonitor\] 等待流式文本解析产出 "
        r"\(已捕获流长度=(\d+), 已静默等待=([\d.]+)s/上限=([\d.]+)s\)$",
        text,
    )
    if network_wait_parser_match:
        body_len, idle_sec, limit_sec = network_wait_parser_match.groups()
        return cute(f"小鹿正在努力拆包中，再给小鹿一点时间确认有效内容喵（已捕获 {body_len} 字节，已静默等待 {idle_sec}s）")

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

    if text == "[STEALTH] 低熵模式已启用":
        return cute("小鹿已经切进低熵模式啦")

    stealth_retry_match = _STEALTH_SEND_RETRY_PATTERN.match(text)
    if stealth_retry_match:
        retry_count, elapsed = stealth_retry_match.groups()
        return cute(f"小鹿又悄悄帮你重试了一次发送喵（第 {retry_count} 次，已经等了 {elapsed}s）")

    stealth_retry_simple_match = _STEALTH_SEND_RETRY_SIMPLE_PATTERN.match(text)
    if stealth_retry_simple_match:
        retry_count = stealth_retry_simple_match.group(1)
        return cute(f"小鹿发现消息没动静，又轻轻点了一下发送按钮（第 {retry_count} 次）喵")

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
        return cute("小鹿先活动活动爪子喵，准备开始低熵操作啦")
    if text.startswith("[STEALTH] 页面预热完成（") and text.endswith(" 次移动）"):
        move_count = text.removeprefix("[STEALTH] 页面预热完成（").removesuffix(" 次移动）")
        return cute(f"小鹿活动了一下手脚，在页面上悄悄晃了 {move_count} 次小步子喵")

    stealth_warmup_done_match = _STEALTH_WARMUP_DONE_PATTERN.match(text)
    if stealth_warmup_done_match:
        move_count, origin_x, origin_y, elapsed = stealth_warmup_done_match.groups()
        return cute(
            f"小鹿活动了一下手脚，在页面上悄悄晃了 {move_count} 次小步子"
            f"（起点 {origin_x},{origin_y}，耗时 {elapsed}s）喵"
        )

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

    extra_alias = _apply_cute_rules(_CUTE_EXTRA_RULES, text)
    if extra_alias:
        return cute(extra_alias)

    return raw_text


# 上下文变量，存储当前请求的 request_id
_request_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
_command_log_context: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "command_log_context",
    default=None,
)

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
        content: str | None = None,
        model: str = "web-browser",
        completion_id: str | None = None,
        images: list[str] | None = None,
        media: list[dict] | None = None,
        reasoning_content: str | None = None,
    ) -> str:
        """打包流式 chunk。

        为兼容现有前端，content 仍保留 Markdown 媒体链接。
        同时补充自定义 media 字段，供需要结构化媒体数据的前端直接消费。
        """
        chunk_id = completion_id or cls._generate_id()
        delta = {}
        if content is not None:
            delta["content"] = content
        if reasoning_content:
            delta["reasoning_content"] = reasoning_content
        if media is not None:
            delta["media"] = media
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
        if media is not None:
            data["media"] = media
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
                   code: str = "workflow_failed", *,
                   status_code: Optional[int] = None,
                   retryable: Optional[bool] = None) -> str:
        error = {
            "message": message,
            "type": error_type,
            "code": code,
        }
        if status_code is not None:
            error["status_code"] = int(status_code)
        if retryable is not None:
            error["retryable"] = bool(retryable)
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
            "error": error
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
    def pack_non_stream(content: str, model: str = "web-browser", media: list | None = None) -> Dict:
        message = {
            "role": "assistant",
            "content": content
        }
        if media is not None:
            message["media"] = media

        data = {
            "id": f"chatcmpl-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }
        if media is not None:
            data["media"] = media
        return data

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
    ROLE_ALIASES = {
        'developer': 'system',
    }
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
            
            role = str(msg.get('role', 'user') or 'user').strip().lower()
            role = cls.ROLE_ALIASES.get(role, role)
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
    normalized = str(name or "APP").strip() or "APP"
    with _logger_registry_lock:
        instance = _logger_registry.get(normalized)
        if instance is None:
            instance = SecureLogger(normalized)
            _logger_registry[normalized] = instance
        return instance


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
    'atomic_write_json',
    '_replace_file_with_retry',
    'SSEFormatter',
    'MessageValidator',
]
