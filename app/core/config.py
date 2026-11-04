"""
app/core/config.py - 配置与基础设施门面 (Facade)

职责：
- 提供配置与日志基础设施的**公开**入口：日志工厂、应用配置、浏览器常量、
  异常类型、SSE 格式化器等。
- 具体实现按职责拆分在 ``app.core.config_parts/`` 子包。

子模块分布：
- env_config.py: 环境变量加载与应用配置 (AppConfig, atomic_write_json, ...)
- browser_constants.py: 浏览器常量配置 (BrowserConstants)
- exceptions.py: 基础异常定义 (BrowserError, WorkflowError, ...)
- sse_formatter.py: SSE 响应格式化器 (SSEFormatter)
- message_validator.py: 消息验证器 (MessageValidator)
- log_collector.py: 前端展示日志收集器 (log_collector)
- log_redaction.py: 敏感信息过滤与脱敏 (sanitize_sensitive_data)
- log_formatters.py: 日志格式化器与 Handler 管理
- cute_translator.py: 表驱动的展示层喵化翻译规则与逻辑
- secure_logger.py: 安全日志器实现与日志工厂 (get_logger, logger)

关于导出面（见 docs/plans/REFACTOR_PLAN-2026-10.md 步骤 5c）
------------------------------------------------------------
本文件曾把 config_parts 的 **132 个名字（其中 106 个是下划线私有名）**原样
再导出，被 123 个模块导入。结果是"拆分"的收益被门面吃回去：调用方经由本文件
耦合到 10 个子模块的**内部实现**，任何子模块改动都会波及全部调用方，
也让 app.core.config 始终处在导入环里。

现已收窄为全库实际使用的 **20 个名字**。判定方式是对三种访问模式做全量扫描，
取并集：
1. ``from app.core.config import X``
2. ``config.X`` 属性访问（``import app.core.config`` / ``from app.core import config``）
3. 字符串引用 ``"app.core.config.X"``（monkeypatch.setattr 这类写法）

其余 112 个在这三种模式下均为零引用。

**需要子模块内部符号时，请直接从 ``app.core.config_parts.<子模块>`` 导入**，
不要把名字加回这里——加回来就等于重新引入上面那层耦合。
"""

from app.core.config_parts import (
    DEFAULT_LOG_DIR,
    PROJECT_ROOT,
    AppConfig,
    BrowserConnectionError,
    BrowserConstants,
    ElementNotFoundError,
    MessageValidator,
    SSEFormatter,
    WorkflowCancelledError,
    WorkflowError,
    _replace_file_with_retry,
    _request_context,
    atomic_write_json,
    command_log_context,
    get_logger,
    get_shared_file_log_handler,
    get_tutorial_selectors_url,
    log_collector,
    logger,
    sanitize_sensitive_data,
)

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
logger.debug("[CONFIG] 这条 DEBUG 日志仅在 LOG_LEVEL=DEBUG 时显示")


# ================= 导出 =================

__all__ = [
    "AppConfig",
    "BrowserConnectionError",
    "BrowserConstants",
    "DEFAULT_LOG_DIR",
    "ElementNotFoundError",
    "MessageValidator",
    "PROJECT_ROOT",
    "SSEFormatter",
    "WorkflowCancelledError",
    "WorkflowError",
    "_replace_file_with_retry",
    "_request_context",
    "atomic_write_json",
    "command_log_context",
    "get_logger",
    "get_shared_file_log_handler",
    "get_tutorial_selectors_url",
    "log_collector",
    "logger",
    "sanitize_sensitive_data",
]
