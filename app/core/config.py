"""
app/core/config.py - 配置和基础设施兼容性门面 (Compatibility Facade)

职责：
- 向后兼容：保持所有现有模块对 `app.core.config` 的导入有效
- 具体实现已按职责拆分至 `app.core.config_parts/` 子包

子模块分布：
- env_config.py: 环境变量加载与应用配置 (AppConfig, app_config, load_dotenv, atomic_write_json)
- browser_constants.py: 浏览器常量配置 (BrowserConstants)
- exceptions.py: 基础异常定义 (BrowserError, WorkflowError, etc.)
- sse_formatter.py: SSE 响应格式化器 (SSEFormatter)
- message_validator.py: 消息验证器 (MessageValidator)
- log_collector.py: 前端展示日志收集器 (LogCollector, log_collector)
- log_redaction.py: 敏感信息过滤与脱敏 (sanitize_sensitive_data, _sanitize_sensitive_text)
- log_formatters.py: 日志格式化器与 Handler 管理 (_ConsoleColorFormatter, _SafeRotatingFileHandler)
- cute_translator.py: 表驱动的展示层喵化翻译规则与逻辑 (_cuteify_*_message)
- secure_logger.py: 安全日志器实现与日志工厂 (SecureLogger, get_logger, logger)
"""

# 从子模块导入所有对外及内部关键接口，确保 100% 向后兼容
from app.core.config_parts import (
    # 环境变量与应用配置
    PROJECT_ROOT,
    DEFAULT_LOG_DIR,
    atomic_write_json,
    _replace_file_with_retry,
    classproperty,
    load_dotenv,
    AppConfig,
    app_config,

    # 浏览器常量
    BrowserConstants,
    _browser_constant_bool,
    _browser_constant_int,
    _BrowserConstantEnabledFilter,

    # 异常
    BrowserError,
    BrowserConnectionError,
    ElementNotFoundError,
    WorkflowError,
    WorkflowCancelledError,
    ConfigurationError,

    # SSE 与消息工具
    SSEFormatter,
    MessageValidator,

    # 日志收集器
    LogCollector,
    log_collector,

    # 敏感信息过滤脱敏
    _SENSITIVE_KEY_HINTS,
    _REDACTED_TEXT,
    _redact_data_uri_for_log,
    _redact_long_base64_for_log,
    _BASE64_LOG_CHARS,
    _SENSITIVE_TEXT_SCAN_HINT_RE,
    _SENSITIVE_TEXT_PRECHECK_MIN_CHARS,
    _SENSITIVE_TEXT_LARGE_OMIT_THRESHOLD,
    _SENSITIVE_TEXT_LARGE_EDGE_CHARS,
    _has_long_base64_candidate,
    _redact_long_base64_runs_for_log,
    _should_scan_sensitive_text,
    _SENSITIVE_TEXT_PATTERNS,
    _SENSITIVE_BEARER_PATTERN,
    _SENSITIVE_STANDALONE_TOKEN_PATTERNS,
    _SENSITIVE_HEADER_PATTERN,
    _SENSITIVE_PARAM_PATTERNS,
    _sanitize_standalone_sensitive_tokens,
    _is_sensitive_key,
    _sanitize_sensitive_text,
    sanitize_sensitive_data,

    # 日志格式化器与 Handler
    _truncate_long_message,
    _get_log_display_limit,
    _record_request_id,
    _REQUEST_SHORT_ID_PATTERN,
    _request_display_tag,
    _record_request_tag,
    _compact_logger_name_impl,
    _compact_logger_name,
    _LOGGER_DISPLAY_WIDTH,
    _record_logger_name,
    _record_kind,
    _LEVEL_BADGES,
    _record_level_badge,
    _replace_log_tag,
    _normalize_log_display_expression,
    _record_display_message,
    _format_log_display_parts,
    _join_log_display_parts,
    _format_log_display_line,
    _WebLogHandler,
    _web_log_handler,
    _enable_windows_ansi,
    _should_use_console_color,
    _ConsoleColorFormatter,
    _FileLogFormatter,
    _DisplayLogFormatter,
    _is_windows_file_lock_error,
    _SafeRotatingFileHandler,
    _get_positive_int_env,
    _resolve_log_dir,
    get_log_file_path,
    get_shared_file_log_handler,
    _shared_file_log_handler,
    _shared_file_log_handler_lock,

    # 喵化翻译规则
    _REQUEST_FINISH_PATTERN,
    _TAB_START_INDEX_PATTERN,
    _TAB_START_ROUTE_PATTERN,
    _CHUNKED_LONG_TEXT_PATTERN,
    _CHUNKED_DONE_PATTERN,
    _CHUNKED_SHORT_TEXT_PATTERN,
    _CHUNKED_FIRST_BLOCK_PATTERN,
    _CHUNKED_PROGRESS_PATTERN,
    _VERIFY_OK_EXACT_PATTERN,
    _SEND_SUCCESS_RETRY_PATTERN,
    _FILE_PASTE_DONE_PATTERN,
    _CLIPBOARD_OK_PATTERN,
    _FILE_PASTE_UPLOAD_SIGNAL_PATTERN,
    _FILE_PASTE_WEAK_SIGNAL_PATTERN,
    _FILE_PASTE_INPUT_SIGNAL_PATTERN,
    _FILE_PASTE_INPUT_UPLOADED_PATTERN,
    _FILE_PASTE_HINT_PATTERN,
    _FILE_PASTE_TEMP_FILE_PATTERN,
    _STEALTH_SEND_RETRY_PATTERN,
    _STEALTH_SEND_RETRY_SIMPLE_PATTERN,
    _STEALTH_WARMUP_DONE_PATTERN,
    _STEALTH_REVIEW_DELAY_PATTERN,
    _STEALTH_CLIPBOARD_PASTE_NOCLICK_PATTERN,
    _STEALTH_CLIPBOARD_PASTE_PATTERN,
    _STEALTH_RANDOM_PAUSE_PATTERN,
    _STEALTH_PRE_SEND_HESITATE_PATTERN,
    _STEALTH_HUMAN_CLICK_PATTERN,
    _SEND_ATTACHMENT_WAIT_PATTERN,
    _SEND_ATTACHMENT_READY_PATTERN,
    _SEND_ATTACHMENT_SETTLE_PATTERN,
    _SEND_RETRY_ACTION_PATTERN,
    _SEND_RETRY_WINDOW_PATTERN,
    _bool_phrase,
    _split_suppressed_suffix,
    _add_suppressed_marker,
    _restore_suppressed_hint,
    _apply_cute_rules,
    _cute_stage_title,
    _CUTE_EXTRA_RULES,
    _ERROR_KEYWORD_HINTS,
    _cuteify_error_message,
    _cuteify_info_message,
    _cuteify_warning_message,
    _cuteify_debug_message,

    # 安全日志器与工厂
    _request_context,
    _command_log_context,
    _logger_setup_lock,
    _logger_registry_lock,
    _logger_registry,
    SecureLogger,
    command_log_context,
    get_logger,
    logger,
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

from app.core.config_parts import __all__ as _config_parts_all

__all__ = list(_config_parts_all)
