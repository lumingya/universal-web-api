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
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from functools import lru_cache
from collections import deque
# ================= 环境变量加载 =================

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
        return os.getenv("DASHBOARD_FILE", "dashboard.html")
    
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
    def get_max_html_chars() -> int:
        return int(os.getenv("MAX_HTML_CHARS", "120000"))
    
    # ===== 配置文件路径 =====
    @staticmethod
    def get_sites_config_file() -> str:
        return os.getenv("SITES_CONFIG_FILE", "config/sites.json")
    
    # ===== 便捷属性（类属性风格访问）=====
    HOST = property(lambda self: self.get_host())
    PORT = property(lambda self: self.get_port())
    DEBUG = property(lambda self: self.is_debug())
    LOG_LEVEL = property(lambda self: self.get_log_level())


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
        'STREAM_CHECK_INTERVAL_MIN': 0.1,
        'STREAM_CHECK_INTERVAL_MAX': 1.0,
        'STREAM_CHECK_INTERVAL_DEFAULT': 0.3,
        'STREAM_SILENCE_THRESHOLD': 6.0,
        'STREAM_MAX_TIMEOUT': 600,
        'STREAM_INITIAL_WAIT': 180,
        'STREAM_RERENDER_WAIT': 0.5,
        'STREAM_CONTENT_SHRINK_TOLERANCE': 3,
        'STREAM_MIN_VALID_LENGTH': 10,
        'STREAM_STABLE_COUNT_THRESHOLD': 5,
        'STREAM_SILENCE_THRESHOLD_FALLBACK': 10.0,
        'MAX_MESSAGE_LENGTH': 100000,
        'MAX_MESSAGES_COUNT': 100,
        'STREAM_INITIAL_ELEMENT_WAIT': 10,
        'STREAM_MAX_ABNORMAL_COUNT': 5,
        'STREAM_MAX_ELEMENT_MISSING': 10,
        'STREAM_CONTENT_SHRINK_THRESHOLD': 0.3,
        'STREAM_USER_MSG_WAIT': 1.5,
        'STREAM_PRE_BASELINE_DELAY': 0.3,
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
    
    # 流式监控
    STREAM_CHECK_INTERVAL_MIN = 0.1
    STREAM_CHECK_INTERVAL_MAX = 1.0
    STREAM_CHECK_INTERVAL_DEFAULT = 0.3
    
    STREAM_SILENCE_THRESHOLD = 6.0
    STREAM_MAX_TIMEOUT = 600
    STREAM_INITIAL_WAIT = 180
    
    # 流式监控增强配置
    STREAM_RERENDER_WAIT = 0.5
    STREAM_CONTENT_SHRINK_TOLERANCE = 3
    STREAM_MIN_VALID_LENGTH = 10
    
    STREAM_STABLE_COUNT_THRESHOLD = 5
    STREAM_SILENCE_THRESHOLD_FALLBACK = 10.0
    
    # 输入验证
    MAX_MESSAGE_LENGTH = 100000
    MAX_MESSAGES_COUNT = 100

    # 异常检测配置
    STREAM_INITIAL_ELEMENT_WAIT = 10
    STREAM_MAX_ABNORMAL_COUNT = 5
    STREAM_MAX_ELEMENT_MISSING = 10
    STREAM_CONTENT_SHRINK_THRESHOLD = 0.3
    
    # 两阶段 baseline 配置
    STREAM_USER_MSG_WAIT = 1.5
    STREAM_PRE_BASELINE_DELAY = 0.3

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

    def __init__(self, max_logs: int = 500):
        self.logs: deque = deque(maxlen=max_logs)
        self.lock = threading.Lock()

    def add(self, level: str, message: str):
        with self.lock:
            self.logs.append({
                "timestamp": time.time(),
                "level": level,
                "message": message
            })

    def get_recent(self, since: float = 0) -> list:
        with self.lock:
            return [log for log in self.logs if log["timestamp"] > since]

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
            log_collector.add(record.levelname, msg)
        except Exception:
            self.handleError(record)


# 创建全局 Web 日志处理器
_web_log_handler = _WebLogHandler()
_web_log_handler.setLevel(logging.INFO)
_web_log_handler.setFormatter(logging.Formatter('%(message)s'))


# 上下文变量，存储当前请求的 request_id
_request_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)

class SecureLogger:
    """安全日志器，带图标和格式化（支持上下文自动注入 request_id）"""
    
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
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(console_handler)
        
        # Web 前端日志收集 handler（始终添加）
        logger.addHandler(_web_log_handler)
        
        logger.setLevel(level)
        return logger

    def _format(self, level_key: str, msg: str) -> str:
        """核心格式化逻辑（简洁版）"""
        import datetime
        now = datetime.datetime.now().strftime('%H:%M:%S')
        
        request_id = _request_context.get()
        ctx_str = request_id if request_id else "SYSTEM"
        return f"{now} │ {ctx_str:<8} │ {msg}"

    def set_level(self, level: int):
        """动态调整日志级别"""
        self._level = level
        self._logger.setLevel(level)
        for handler in self._logger.handlers:
            handler.setLevel(level)

    def debug(self, msg: str):
        self._logger.debug(self._format('DEBUG', msg))

    def info(self, msg: str):
        self._logger.info(self._format('INFO', msg))

    def warning(self, msg: str):
        self._logger.warning(self._format('WARNING', msg))

    def error(self, msg: str):
        self._logger.error(self._format('ERROR', msg))

    def exception(self, msg: str):
        self._logger.exception(self._format('ERROR', msg))
        
    def success(self, msg: str):
        self._logger.info(self._format('SUCCESS', msg))

    def stream(self, msg: str):
        self._logger.info(self._format('STREAM', msg))
        
    def network(self, msg: str):
        self._logger.info(self._format('NETWORK', msg))
    @contextlib.contextmanager
    def context(self, request_id: str):
        """上下文管理器，用于在代码块中自动设置 request_id"""
        token = _request_context.set(request_id)
        try:
            yield
        finally:
            _request_context.reset(token)

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
    def pack_chunk(cls, content: str, model: str = "web-browser", 
                   completion_id: str = None) -> str:
        """打包流式 chunk"""
        chunk_id = completion_id or cls._generate_id()
        data = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": content},
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
    def pack_error(message: str, error_type: str = "execution_error",
                   code: str = "workflow_failed") -> str:
        data = {
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
    @classmethod
    def pack_images_chunk(cls, images: list, completion_id: str = None) -> str:
        """
        打包携带图片的 SSE chunk（Phase B 新增）
        
        这个 chunk 会在最后一个文本 chunk 之后、[DONE] 之前发送。
        客户端可以从 delta.images 中获取图片数据。
        
        Args:
            images: 图片数据列表，每项符合 ImageData 格式
            completion_id: 补全 ID
        
        Returns:
            SSE 格式的字符串
        
        Example:
            >>> chunk = SSEFormatter.pack_images_chunk([{"kind": "url", "url": "..."}])
            >>> # "data: {"choices": [{"delta": {"images": [...]}}]}\n\n"
        """
        import time
        import json
        
        if not images:
            return ""
        
        data = {
            "id": completion_id or cls._generate_id(),
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "web-browser",
            "choices": [{
                "index": 0,
                "delta": {
                    "images": images
                },
                "finish_reason": None
            }]
        }
        
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    def pack_final_chunk_with_images(self, images: list, completion_id: str = None) -> str:
        """
        🆕 打包包含图片的最终 chunk
        
        在流式输出的最后一个 chunk 中附带图片信息
        """
        if not completion_id:
            completion_id = self._generate_id()
        
        data = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "web-browser",
            "choices": [{
                "index": 0,
                "delta": {
                    "images": images  # 图片数据放在 delta.images 中
                },
                "finish_reason": None
            }]
        }
        
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
# ================= 消息验证器 =================

class MessageValidator:
    """消息验证器"""
    
    VALID_ROLES = {'user', 'assistant', 'system'}
    
    @classmethod
    def validate(cls, messages: Any) -> tuple:
        if messages is None:
            return False, "messages 不能为空", None
        
        if not isinstance(messages, list):
            return False, f"messages 应该是列表", None
        
        if len(messages) == 0:
            return False, "messages 不能为空列表", None
        
        if len(messages) > BrowserConstants.MAX_MESSAGES_COUNT:
            return False, f"消息数量超过限制", None
        
        sanitized = []
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                return False, f"messages[{i}] 不是字典类型", None
            
            role = msg.get('role', 'user')
            if role not in cls.VALID_ROLES:
                role = 'user'
            
            content = msg.get('content', '')
            if not isinstance(content, str):
                content = str(content) if content is not None else ''
            
            if len(content) > BrowserConstants.MAX_MESSAGE_LENGTH:
                return False, f"messages[{i}].content 超过长度限制", None
            
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