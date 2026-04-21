"""
schemas.py - 数据模型和 API Schema 定义

职责：
- 定义所有数据结构的类型（TypedDict）
- 定义 API 请求/响应模型（Pydantic）
- 提供类型检查支持
"""

from typing import TypedDict, List, Optional, Literal, Dict, Any
from pydantic import BaseModel

# ================= 动作类型 =================

ActionType = Literal[
    "FILL_INPUT",
    "CLICK",
    "COORD_CLICK",
    "COORD_SCROLL",
    "STREAM_WAIT",
    "STREAM_OUTPUT",
    "KEY_PRESS",
    "WAIT",
    "JS_EXEC"
]

# ================= 选择器字段名称 =================

REQUIRED_SELECTOR_KEYS = [
    "input_box",
    "send_btn", 
    "result_container",
]

OPTIONAL_SELECTOR_KEYS = [
    "new_chat_btn",
    "message_wrapper",
    "generating_indicator",
    "upload_btn",
    "file_input",
    "drop_zone",
]

ALL_SELECTOR_KEYS = REQUIRED_SELECTOR_KEYS + OPTIONAL_SELECTOR_KEYS


# ================= 工作流步骤 =================

class WorkflowStep(TypedDict):
    """工作流单步定义"""
    action: ActionType
    target: str
    optional: bool
    value: Optional[Any]


# ================= 元素定义 =================

class SelectorDefinition(TypedDict):
    """选择器定义 - 用于 AI 分析页面时的查找目标"""
    key: str
    description: str
    enabled: bool
    required: bool


class GlobalConfig(TypedDict, total=False):
    """全局配置 - 存储在 sites.json 的 _global 节点"""
    selector_definitions: List[SelectorDefinition]


# ================= 默认元素定义 =================

DEFAULT_SELECTOR_DEFINITIONS: List[SelectorDefinition] = [
    {
        "key": "input_box",
        "description": "用户输入文本的输入框（textarea 或 contenteditable 元素）",
        "enabled": True,
        "required": True
    },
    {
        "key": "send_btn",
        "description": "发送消息的按钮（通常是 type=submit 或带有发送图标的按钮）",
        "enabled": True,
        "required": True
    },
    {
        "key": "result_container",
        "description": "AI 回复内容的容器（仅包含 AI 的输出文本，不含用户消息）",
        "enabled": True,
        "required": True
    },
    {
        "key": "new_chat_btn",
        "description": "新建对话的按钮（点击后开始新的对话）",
        "enabled": True,
        "required": False
    },
    {
        "key": "message_wrapper",
        "description": "消息完整容器（包裹单条消息的外层元素，用于多节点拼接）",
        "enabled": False,
        "required": False
    },
    {
        "key": "generating_indicator",
        "description": "生成中指示器（如停止按钮、加载动画，用于检测是否还在输出）",
        "enabled": False,
        "required": False
    },
    {
        "key": "upload_btn",
        "description": "打开文件选择器的上传按钮（点击后通常会弹出原生选文件）",
        "enabled": False,
        "required": False
    },
    {
        "key": "file_input",
        "description": "原生文件输入框（input[type=file]），用于直接注入文件",
        "enabled": False,
        "required": False
    },
    {
        "key": "drop_zone",
        "description": "支持拖拽上传的区域（某些站点不支持粘贴但支持拖拽）",
        "enabled": False,
        "required": False
    }
]


def get_default_selector_definitions() -> List[SelectorDefinition]:
    """获取默认的元素定义列表（深拷贝）"""
    import copy
    return copy.deepcopy(DEFAULT_SELECTOR_DEFINITIONS)

# ================= 图片提取相关模型（Phase A 新增）=================

class ImageData(BaseModel):
    """
    图片数据模型
    
    kind 决定使用 url 还是 data_uri：
    - kind="url": 使用 url 字段
    - kind="data_uri": 使用 data_uri 字段
    """
    kind: Literal["url", "data_uri"]
    url: Optional[str] = None
    data_uri: Optional[str] = None
    
    mime: Optional[str] = None
    byte_size: Optional[int] = None
    
    alt: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    
    index: int = 0
    detected_at: Optional[str] = None  # ISO 格式时间戳
    source: Optional[Literal["currentSrc", "src", "blob", "data_uri", "relative"]] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "kind": "url",
                "url": "https://example.com/image.png",
                "mime": "image/png",
                "width": 800,
                "height": 600,
                "index": 0,
                "source": "currentSrc"
            }
        }

class FilePasteConfig(TypedDict, total=False):
    """
    文件粘贴配置
    
    当文本长度超过阈值时，将文本写入临时 txt 文件，
    然后以文件形式粘贴到输入框（通过 CF_HDROP 剪贴板格式）。
    粘贴文件后，自动在输入框中追加一句引导文本，确保能正常发送。
    
    用于 sites.json 中的 file_paste 字段
    """
    enabled: bool       # 是否启用文件粘贴模式，默认 False
    threshold: int      # 字符数阈值，超过此值时使用文件粘贴，默认 50000
    hint_text: str      # 粘贴文件后追加的引导文本，默认 "完全专注于文件内容"


def get_default_file_paste_config() -> 'FilePasteConfig':
    """获取默认的文件粘贴配置"""
    return {
        "enabled": False,
        "threshold": 50000,
        "hint_text": "完全专注于文件内容"
    }

class ExtractionModalitiesConfig(TypedDict, total=False):
    """多模态提取开关"""
    image: bool
    audio: bool
    video: bool


class ImageExtractionConfig(TypedDict, total=False):
    """
    多模态提取配置
    
    用于 sites.json 中的 image_extraction 字段
    """
    enabled: bool                    # 是否启用多模态提取（兼容旧字段）
    modalities: ExtractionModalitiesConfig  # 各模态开关
    selector: str                    # 图片选择器，默认 "img"
    audio_selector: str              # 音频选择器
    video_selector: str              # 视频选择器
    container_selector: Optional[str] # 容器选择器，限定查找范围
    debounce_seconds: float          # 文本稳定后等待时间
    wait_for_load: bool              # 是否等待媒体加载完成
    load_timeout_seconds: float      # 等待加载的超时时间
    download_blobs: bool             # 是否下载 blob 转 data_uri
    max_size_mb: int                 # blob 最大允许大小(MB)
    mode: Literal["all", "first", "last"]  # 每种模态的提取模式
# ================= 流式监控配置 =================

class SendConfirmationConfig(TypedDict, total=False):
    """Post-click send confirmation strategy."""
    attachment_sensitivity: Literal["low", "medium", "high"]
    post_click_observe_window: float
    pre_retry_probe_window: float
    retry_observe_window: float
    attachment_observe_window: float
    trust_network_activity: bool
    trust_generating_indicator: bool
    trust_send_disabled_with_input_shrink: bool


class StreamConfig(TypedDict, total=False):
    """流式监控配置（可选字段）"""
    send_confirmation: SendConfirmationConfig


# ================= 站点高级配置 =================

class SiteAdvancedConfig(TypedDict, total=False):
    """站点级高级功能配置（不随预设切换）"""
    independent_cookies: bool
    independent_cookies_auto_takeover: bool


def get_default_site_advanced_config() -> 'SiteAdvancedConfig':
    """获取默认站点高级配置"""
    return {
        "independent_cookies": False,
        "independent_cookies_auto_takeover": False,
    }


# ================= 站点配置 =================

class SiteConfig(TypedDict, total=False):
    """站点配置结构"""
    advanced: SiteAdvancedConfig
    selectors: Dict[str, Optional[str]]
    workflow: List[WorkflowStep]
    stealth: bool
    stream_config: StreamConfig
    image_extraction: ImageExtractionConfig  # 🆕 新增
    file_paste: FilePasteConfig
    extractor_id: str                        # 提取器 ID（已有）
    extractor_verified: bool                 # 提取器验证状态（已有）


# ================= 选择器验证结果 =================

class SelectorValidationResult(TypedDict):
    """选择器验证结果"""
    key: str
    selector: Optional[str]
    valid: bool
    reason: Optional[str]
    repaired: Optional[str]


# ================= AI 分析结果 =================

class AIAnalysisResult(TypedDict, total=False):
    """AI 分析返回的选择器结构"""
    input_box: Optional[str]
    send_btn: Optional[str]
    result_container: Optional[str]
    new_chat_btn: Optional[str]
    message_wrapper: Optional[str]
    generating_indicator: Optional[str]
    upload_btn: Optional[str]
    file_input: Optional[str]
    drop_zone: Optional[str]


# ================= 健康检查结果 =================

class HealthCheckResult(TypedDict):
    """健康检查结果"""
    status: Literal["healthy", "unhealthy"]
    connected: bool
    port: int
    tab_url: Optional[str]
    tab_title: Optional[str]
    error: Optional[str]


# ================= 页面状态检查结果 =================

class PageStatusResult(TypedDict):
    """页面状态检查结果"""
    ready: bool
    reason: Optional[str]


# ================= API 请求模型 =================

class ChatMessage(TypedDict, total=False):
    """聊天消息"""
    role: Literal["user", "assistant", "system"]
    content: str
    images: List[Dict]  # 🆕 新增：图片列表（可选字段）


class ChatCompletionRequest(TypedDict):
    """聊天补全请求"""
    model: str
    messages: List[ChatMessage]
    stream: Optional[bool]
    temperature: Optional[float]
    max_tokens: Optional[int]


# ================= SSE 响应模型 =================

class DeltaContent(TypedDict, total=False):
    """流式响应的增量内容"""
    content: str
    images: List[Dict]  # 🆕 新增：最后一个 chunk 携带


class StreamChoice(TypedDict):
    """流式响应的选项"""
    index: int
    delta: DeltaContent
    finish_reason: Optional[Literal["stop", None]]


class StreamResponse(TypedDict):
    """流式响应结构"""
    id: str
    object: str
    created: int
    model: str
    choices: List[StreamChoice]


# ================= 非流式响应模型 =================

class MessageContent(TypedDict, total=False):
    """消息内容"""
    role: Literal["assistant"]
    content: str
    images: List[Dict]  # 🆕 新增

class NonStreamChoice(TypedDict):
    """非流式响应的选项"""
    index: int
    message: MessageContent
    finish_reason: Literal["stop"]


class UsageInfo(TypedDict):
    """Token 使用信息（占位）"""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class NonStreamResponse(TypedDict):
    """非流式响应结构"""
    id: str
    object: str
    created: int
    model: str
    choices: List[NonStreamChoice]
    usage: UsageInfo


# ================= 错误响应模型 =================

class ErrorDetail(TypedDict):
    """错误详情"""
    message: str
    type: str
    code: str


class ErrorResponse(TypedDict):
    """错误响应结构"""
    error: ErrorDetail


# ================= 模型信息 =================

class ModelInfo(TypedDict):
    """模型信息"""
    id: str
    object: str
    created: int
    owned_by: str


class ModelsResponse(TypedDict):
    """模型列表响应"""
    object: str
    data: List[ModelInfo]


# ================= 提取器相关模型 =================

class ExtractorConfigDict(TypedDict, total=False):
    """提取器配置参数（内部类型定义）"""
    enable_latex: bool
    enable_shadow_dom: bool
    chunk_threshold: int


class ExtractorDefinition(TypedDict):
    """提取器定义（extractors.json 中的结构）"""
    id: str
    name: str
    description: str
    class_: str  # Python 类名（注意：JSON 中是 "class"）
    module: str
    enabled: bool
    config: ExtractorConfigDict


# ================= Pydantic API 模型（FastAPI 用）=================

class ExtractorListResponse(BaseModel):
    """API 响应：提取器列表"""
    extractors: List[Dict[str, Any]]
    default: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "extractors": [
                    {
                        "id": "deep_mode_v1",
                        "name": "深度模式",
                        "description": "JS 注入提取",
                        "enabled": True
                    }
                ],
                "default": "deep_mode_v1"
            }
        }


class ExtractorTestRequest(BaseModel):
    """API 请求：测试提取器"""
    site_id: str
    extractor_id: str
    test_prompt: str = "Hello, test."
    
    class Config:
        json_schema_extra = {
            "example": {
                "site_id": "chatgpt.com",
                "extractor_id": "deep_mode_v1",
                "test_prompt": "Write a short poem."
            }
        }


class ExtractorVerifyRequest(BaseModel):
    """API 请求：验证提取结果"""
    extracted_text: str
    expected_text: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "extracted_text": "Roses are red...",
                "expected_text": "Roses are red..."
            }
        }


class ExtractorVerifyResponse(BaseModel):
    """API 响应：验证结果"""
    similarity: float  # 0.0 - 1.0
    passed: bool       # >= 0.95 视为通过
    message: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "similarity": 0.973,
                "passed": True,
                "message": "验证通过"
            }
        }


class ExtractorAssignRequest(BaseModel):
    """API 请求：为站点分配提取器"""
    extractor_id: str
    
    class Config:
        json_schema_extra = {
            "example": {
                "extractor_id": "deep_mode_v1"
            }
        }


# ================= 工具函数 =================

def validate_workflow_step(step: Dict[str, Any]) -> bool:
    """验证工作流步骤是否有效"""
    required_keys = {"action", "target", "optional"}
    return all(key in step for key in required_keys)

def get_default_image_extraction_config() -> ImageExtractionConfig:
    """获取默认的多模态提取配置"""
    return {
        "enabled": False,
        "modalities": {
            "image": False,
            "audio": False,
            "video": False,
        },
        "selector": "img",
        "audio_selector": "audio, audio source",
        "video_selector": "video, video source",
        "container_selector": None,
        "debounce_seconds": 2.0,
        "wait_for_load": True,
        "load_timeout_seconds": 5.0,
        "download_blobs": True,
        "max_size_mb": 10,
        "mode": "all"
    }
def validate_site_config(config: Dict[str, Any]) -> bool:
    """验证站点配置是否有效"""
    if "selectors" not in config or "workflow" not in config:
        return False
    
    if not isinstance(config["selectors"], dict):
        return False
    
    if not isinstance(config["workflow"], list):
        return False
    
    for step in config["workflow"]:
        if not validate_workflow_step(step):
            return False
    
    if "stream_config" in config:
        if not isinstance(config["stream_config"], dict):
            return False
        
        stream_config = config["stream_config"]
        
        if "send_confirmation" in stream_config:
            if not isinstance(stream_config["send_confirmation"], dict):
                return False

            send_confirmation = stream_config["send_confirmation"]
            numeric_fields = [
                "post_click_observe_window",
                "pre_retry_probe_window",
                "retry_observe_window",
                "attachment_observe_window",
            ]
            bool_fields = [
                "trust_network_activity",
                "trust_generating_indicator",
                "trust_send_disabled_with_input_shrink",
            ]
            enum_fields = {
                "attachment_sensitivity": {"low", "medium", "high"},
            }

            for key in numeric_fields:
                if key in send_confirmation and not isinstance(send_confirmation[key], (int, float)):
                    return False

            for key in bool_fields:
                if key in send_confirmation and not isinstance(send_confirmation[key], bool):
                    return False

            for key, allowed_values in enum_fields.items():
                if key not in send_confirmation:
                    continue
                value = str(send_confirmation[key]).strip().lower()
                if value not in allowed_values:
                    return False
    
    return True


def get_default_send_confirmation_config() -> SendConfirmationConfig:
    """Get the default send confirmation strategy."""
    return {
        "attachment_sensitivity": "medium",
        "post_click_observe_window": 1.8,
        "pre_retry_probe_window": 0.12,
        "retry_observe_window": 0.9,
        "attachment_observe_window": 6.0,
        "trust_network_activity": True,
        "trust_generating_indicator": True,
        "trust_send_disabled_with_input_shrink": True,
    }


def get_default_stream_config() -> StreamConfig:
    """获取默认的流式监控配置"""
    return {
        "send_confirmation": get_default_send_confirmation_config(),
    }


def merge_stream_config(
    site_config: Optional[StreamConfig],
    defaults: Optional[StreamConfig] = None
) -> StreamConfig:
    """合并流式监控配置"""
    if defaults is None:
        defaults = get_default_stream_config()
    
    if site_config is None:
        return defaults.copy()
    
    result = defaults.copy()
    result.update(site_config)

    default_send_confirmation = defaults.get("send_confirmation")
    site_send_confirmation = site_config.get("send_confirmation")
    if isinstance(default_send_confirmation, dict):
        result["send_confirmation"] = default_send_confirmation.copy()
        if isinstance(site_send_confirmation, dict):
            result["send_confirmation"].update(site_send_confirmation)
    elif isinstance(site_send_confirmation, dict):
        result["send_confirmation"] = site_send_confirmation.copy()
    
    return result


# ================= 导出列表 =================

__all__ = [
    # 类型定义
    'ActionType',
    'WorkflowStep',
    'SelectorDefinition',
    'GlobalConfig',
    'StreamConfig',
    'SiteConfig',
    'SelectorValidationResult',
    'AIAnalysisResult',
    'HealthCheckResult',
    'PageStatusResult',
    'ChatMessage',
    'ChatCompletionRequest',
    'DeltaContent',
    'StreamChoice',
    'StreamResponse',
    'MessageContent',
    'NonStreamChoice',
    'UsageInfo',
    'NonStreamResponse',
    'ErrorDetail',
    'ErrorResponse',
    'ModelInfo',
    'ModelsResponse',
    
    # 提取器相关
    'ExtractorConfigDict',
    'ExtractorDefinition',
    'ExtractorListResponse',
    'ExtractorTestRequest',
    'ExtractorVerifyRequest',
    'ExtractorVerifyResponse',
    'ExtractorAssignRequest',
    
    # 常量
    'REQUIRED_SELECTOR_KEYS',
    'OPTIONAL_SELECTOR_KEYS',
    'ALL_SELECTOR_KEYS',
    'DEFAULT_SELECTOR_DEFINITIONS',
    
    # 工具函数
    'get_default_selector_definitions',
    'validate_workflow_step',
    'validate_site_config',
    'SendConfirmationConfig',
    'get_default_send_confirmation_config',
    'get_default_stream_config',
    'merge_stream_config',
    'ImageData',
    'ImageExtractionConfig',
    'get_default_image_extraction_config',
    'FilePasteConfig',
    'get_default_file_paste_config',
]


# ================= 测试 =================

if __name__ == "__main__":
    print("=" * 50)
    print("Schema 模型测试")
    print("=" * 50)
    
    # 测试 Pydantic 模型
    test_request = ExtractorTestRequest(
        site_id="chatgpt.com",
        extractor_id="deep_mode_v1",
        test_prompt="Hello!"
    )
    print(f"\n✅ ExtractorTestRequest: {test_request.model_dump()}")
    
    test_response = ExtractorVerifyResponse(
        similarity=0.98,
        passed=True,
        message="验证通过"
    )
    print(f"✅ ExtractorVerifyResponse: {test_response.model_dump()}")
    
    print("\n" + "=" * 50)
    print("所有测试通过!")
    print("=" * 50)
