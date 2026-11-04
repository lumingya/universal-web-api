"""
app/models - 数据模型

职责：
- API 数据模型
- 配置数据模型
- 类型定义
"""

from .schemas import (
    ALL_SELECTOR_KEYS,
    DEFAULT_SELECTOR_DEFINITIONS,
    OPTIONAL_SELECTOR_KEYS,
    # 常量
    REQUIRED_SELECTOR_KEYS,
    # 工作流相关
    ActionType,
    ChatCompletionRequest,
    # API 相关
    ChatMessage,
    ErrorResponse,
    GlobalConfig,
    NonStreamResponse,
    # 选择器相关
    SelectorDefinition,
    SiteConfig,
    StreamConfig,
    # SSE 响应
    StreamResponse,
    WorkflowStep,
    get_default_selector_definitions,
    validate_site_config,
    # 验证
    validate_workflow_step,
)

__all__ = [
    # 工作流
    'ActionType',
    'WorkflowStep',
    'SiteConfig',
    'StreamConfig',

    # 选择器
    'SelectorDefinition',
    'GlobalConfig',
    'DEFAULT_SELECTOR_DEFINITIONS',
    'get_default_selector_definitions',

    # API
    'ChatMessage',
    'ChatCompletionRequest',

    # 响应
    'StreamResponse',
    'NonStreamResponse',
    'ErrorResponse',

    # 验证
    'validate_workflow_step',
    'validate_site_config',

    # 常量
    'REQUIRED_SELECTOR_KEYS',
    'OPTIONAL_SELECTOR_KEYS',
    'ALL_SELECTOR_KEYS',
]
