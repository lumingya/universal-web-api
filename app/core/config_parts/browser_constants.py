"""
app/core/config_parts/browser_constants.py - 浏览器常量配置与过滤器模块
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from .env_config import AppConfig, PROJECT_ROOT


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


class _BrowserConstantEnabledFilter(logging.Filter):
    def __init__(self, key: str, default: bool = True):
        super().__init__()
        self.key = key
        self.default = default

    def filter(self, record: logging.LogRecord) -> bool:
        return _browser_constant_bool(self.key, self.default)


# 模块加载时执行自初始化
BrowserConstants._load_config()
BrowserConstants._apply_to_class_attrs()
