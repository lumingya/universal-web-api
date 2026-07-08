"""
schemas.py - 数据模型和 API Schema 定义

职责：
- 定义所有数据结构的类型（TypedDict）
- 定义 API 请求/响应模型（Pydantic）
- 提供类型检查支持
"""

import copy
from typing import TypedDict, List, Optional, Literal, Dict, Any, Union
from pydantic import BaseModel, ConfigDict

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
    "JS_EXEC",
    "READONLY_HINT",
    "PAGE_FETCH",
]

# ================= 选择器字段名称 =================

REQUIRED_SELECTOR_KEYS = [
    "input_box",
    "send_btn",
    "result_container",
]

OPTIONAL_SELECTOR_KEYS = [
    "new_chat_btn",
    "retry_send_btn",
    "stop_btn",
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
        "description": "发送消息的按钮（通常是 type=submit 或带有发送图标 of 按钮）",
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
        "key": "retry_send_btn",
        "description": "重新运行/重试当前回复的按钮（通常是回复卡片顶部的圆形箭头）",
        "enabled": False,
        "required": False
    },
    {
        "key": "stop_btn",
        "description": "停止/打断大模型输出的按钮（通常在生成过程中显示）",
        "enabled": False,
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

    model_config = ConfigDict(
        json_schema_extra={
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
    )

class FilePasteConfig(TypedDict, total=False):
    """
    文件粘贴配置

    当文本长度超过阈值时，将文本写入临时 txt/pdf 文件并上传，
    或按 error 类型直接返回自定义错误信息。
    上传文件后，自动在输入框中追加一句引导文本，确保能正常发送。
    附件发送确认规则也优先挂在这里，避免和网络监听配置混在一起。

    用于 sites.json 中的 file_paste 字段
    """
    enabled: bool       # 是否启用文件粘贴模式，默认 False
    threshold: int      # 字符数阈值，超过此值时使用文件粘贴，默认 50000
    temp_file_type: Literal["txt", "pdf", "error"]  # 临时文件类型/超长处理策略，默认 txt
    hint_text: str      # 上传后引导文本；error 类型时作为自定义错误信息
    txt_hint_text: str  # TXT 临时文件引导文本
    pdf_hint_text: str  # PDF 临时文件引导文本
    error_hint_text: str  # ERROR 类型错误信息
    reacquire_input_after_upload: bool  # 上传完成后是否重新定位输入框
    post_upload_input_selector: str     # 上传后专用输入框选择器
    post_upload_settle: float           # 上传完成后额外稳定等待
    upload_signal_timeout: float        # 上传信号确认超时
    upload_signal_grace: float          # 弱信号额外宽限
    send_confirmation: Dict[str, Any]   # 附件发送后的成功判定策略
    attachment_monitor: Dict[str, Any]  # 附件预览 / pending / busy 规则
    state_probe: Dict[str, Any]         # 自定义 JS 状态探针


def get_default_file_paste_config() -> 'FilePasteConfig':
    """获取默认的文件粘贴配置"""
    return {
        "enabled": False,
        "threshold": 50000,
        "temp_file_type": "txt",
        "hint_text": "完全专注于文件内容",
        "txt_hint_text": "完全专注于文件内容",
        "pdf_hint_text": "完全专注于文件内容",
        "error_hint_text": "输入文本长度超过限制，已中止发送",
        "reacquire_input_after_upload": False,
        "post_upload_input_selector": "",
        "post_upload_settle": 0.0,
        "upload_signal_timeout": 2.5,
        "upload_signal_grace": 3.0,
        "state_probe": {
            "enabled": False,
            "code": "",
        },
    }


class PromptPaddingConfig(TypedDict, total=False):
    """
    提示词首尾填充配置

    用于 sites.json 中的 prompt_padding 字段
    """
    enabled: bool
    marker_text: str
    segments_per_side: int


def get_default_prompt_padding_config() -> 'PromptPaddingConfig':
    """获取默认的提示词首尾填充配置"""
    return {
        "enabled": False,
        "marker_text": "测试号，无实际意义",
        "segments_per_side": 12,
    }


ModalityRunPolicy = Literal[
    "disabled",
    "generic_only",
    "on_signal",
    "probe_if_trigger_found",
    "always_probe",
]


class ModalityPolicyConfig(TypedDict, total=False):
    """单个模态的运行策略。"""
    enabled: bool
    run_policy: ModalityRunPolicy
    selector: str
    container_selector: Optional[str]
    quick_probe_timeout_seconds: float
    capture_timeout_seconds: float
    late_wait_timeout_seconds: float
    blind_wait_timeout_seconds: float


class ExtractionModalitiesConfig(TypedDict, total=False):
    """多模态提取开关与运行策略。旧版 bool 配置仍兼容。"""
    image: Union[bool, ModalityPolicyConfig]
    audio: Union[bool, ModalityPolicyConfig]
    video: Union[bool, ModalityPolicyConfig]


class AudioNetworkCaptureConfig(TypedDict, total=False):
    """页面内网络音频捕获配置"""
    enabled: bool
    timeout_seconds: float
    transport: Literal["page_websocket_probe"]
    url_patterns: List[str]
    extractor: Literal["voicegenie_ogg_pages", "voicegenie_binary_stream"]
    settle_seconds: float
    max_payload_bytes: int


class AudioBrowserTtsFallbackConfig(TypedDict, total=False):
    """浏览器上下文内直接拉取 TTS 的兜底配置。"""
    enabled: bool
    provider: Literal["doubao_samantha"]
    speaker: str
    speech_rate: int
    pitch: int
    format: Literal["aac"]
    timeout_seconds: float
    pc_version: str
    aid: str
    real_aid: str
    language: str
    device_platform: str
    pkg_type: str
    region: str
    sys_region: str
    use_olympus_account: str
    samantha_web: str


MODALITY_TYPES = ("image", "audio", "video")
MODALITY_RUN_POLICY_VALUES = {
    "disabled",
    "generic_only",
    "on_signal",
    "probe_if_trigger_found",
    "always_probe",
}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def get_default_modality_policy(media_type: str, enabled: bool = False) -> ModalityPolicyConfig:
    """获取单模态默认运行策略。"""
    media_type = str(media_type or "").strip().lower()
    policy: ModalityPolicyConfig = {
        "enabled": bool(enabled),
        "run_policy": "disabled" if not enabled else "on_signal",
        "quick_probe_timeout_seconds": 1.0,
    }
    if media_type == "audio":
        policy.update({
            "run_policy": "probe_if_trigger_found" if enabled else "disabled",
            "capture_timeout_seconds": 12.0,
        })
    elif media_type == "video":
        policy.update({
            "run_policy": "on_signal" if enabled else "disabled",
            "late_wait_timeout_seconds": 90.0,
        })
    elif media_type == "image":
        policy.update({
            "run_policy": "on_signal" if enabled else "disabled",
            "late_wait_timeout_seconds": 45.0,
            "blind_wait_timeout_seconds": 1.0,
        })
    return policy


def normalize_modality_policy(media_type: str, raw_value: Any) -> ModalityPolicyConfig:
    """把旧 bool 或新对象配置规范化成策略对象。"""
    if isinstance(raw_value, dict):
        enabled = _coerce_bool(raw_value.get("enabled"), False)
        result = get_default_modality_policy(media_type, enabled=enabled)
        run_policy = str(raw_value.get("run_policy") or result.get("run_policy") or "").strip().lower()
        if run_policy not in MODALITY_RUN_POLICY_VALUES:
            run_policy = str(result.get("run_policy") or "disabled")
        if not enabled:
            run_policy = "disabled"
        result["run_policy"] = run_policy  # type: ignore[typeddict-item]

        if "quick_probe_timeout_seconds" in raw_value:
            try:
                result["quick_probe_timeout_seconds"] = max(0.1, min(float(raw_value["quick_probe_timeout_seconds"]), 10.0))
            except (TypeError, ValueError):
                pass
        if "capture_timeout_seconds" in raw_value:
            try:
                result["capture_timeout_seconds"] = max(0.2, min(float(raw_value["capture_timeout_seconds"]), 180.0))
            except (TypeError, ValueError):
                pass
        if "late_wait_timeout_seconds" in raw_value:
            try:
                result["late_wait_timeout_seconds"] = max(0.2, min(float(raw_value["late_wait_timeout_seconds"]), 300.0))
            except (TypeError, ValueError):
                pass
        if "blind_wait_timeout_seconds" in raw_value:
            try:
                result["blind_wait_timeout_seconds"] = max(0.0, min(float(raw_value["blind_wait_timeout_seconds"]), 300.0))
            except (TypeError, ValueError):
                pass
        if "selector" in raw_value:
            selector = str(raw_value.get("selector") or "").strip()
            if selector:
                result["selector"] = selector
        if "container_selector" in raw_value:
            container_selector = str(raw_value.get("container_selector") or "").strip()
            result["container_selector"] = container_selector or None
        return result

    return get_default_modality_policy(media_type, enabled=_coerce_bool(raw_value, False))


def normalize_modalities_config(raw_modalities: Any) -> ExtractionModalitiesConfig:
    """规范化 image/audio/video 三个模态配置。"""
    raw = raw_modalities if isinstance(raw_modalities, dict) else {}
    return {
        media_type: normalize_modality_policy(media_type, raw.get(media_type, False))
        for media_type in MODALITY_TYPES
    }


def get_modality_policy(modalities: Any, media_type: str) -> ModalityPolicyConfig:
    """读取单模态策略，兼容旧 bool。"""
    raw = modalities.get(media_type) if isinstance(modalities, dict) else False
    return normalize_modality_policy(media_type, raw)


def is_modality_enabled(modalities: Any, media_type: str) -> bool:
    """判断模态是否启用，避免 bool(dict) 误判。"""
    return bool(get_modality_policy(modalities, media_type).get("enabled", False))


def get_modality_run_policy(modalities: Any, media_type: str) -> str:
    """读取模态运行策略。"""
    return str(get_modality_policy(modalities, media_type).get("run_policy") or "disabled")


def get_enabled_modalities(modalities: Any) -> List[str]:
    """返回已启用模态列表。"""
    return [
        media_type for media_type in MODALITY_TYPES
        if is_modality_enabled(modalities, media_type)
    ]


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
    final_target_strategy: Literal["container", "latest_reply", "latest_visual_reply"] # 最终提取时锁定整容器或当前回复节点
    latest_visual_column: Literal["left", "right"] # latest_visual_reply 同一行内优先左栏或右栏
    allow_container_fallback: bool   # 当前回复内无媒体时是否回退到容器/整页
    force_postprocess: bool          # 是否强制执行收尾多模态后处理（由配置显式声明）
    direct_postprocess_modalities: List[Literal["image", "audio", "video"]] # 允许无额外信号直接执行 DOM 收尾提取的模态
    debounce_seconds: float          # 文本稳定后等待时间
    wait_for_load: bool              # 是否等待媒体加载完成
    load_timeout_seconds: float      # 等待加载的超时时间
    download_blobs: bool             # 是否下载 blob 转 data_uri
    max_size_mb: int                 # blob 最大允许大小(MB)
    src_allow_patterns: List[str]    # 可选：按 src 正则白名单过滤
    mode: Literal["all", "first", "last"]  # 每种模态的提取模式
    audio_capture_enabled: bool      # 是否启用通用页面播放音频捕获回退
    audio_capture_mute_playback: bool # 捕获时是否静音页面播放
    audio_capture_preload_enabled: bool # 是否在页面加载前预注入捕获脚本
    audio_capture_reload_before_workflow: bool # 捕获前是否刷新页面以接管早期音频图
    audio_capture_preserve_graph: bool # 重置捕获时是否保留已接管的音频图
    audio_capture_terminal_settle_seconds: float # 播放结束后额外等待一小段时间，避免截断尾音
    audio_trigger_selector: str      # 可选：朗读/播放按钮选择器
    audio_trigger_labels: List[str]  # 可选：朗读/播放按钮文本候选
    audio_capture_max_wait_seconds: float  # 最长等待音频播放完成
    audio_capture_min_wait_seconds: float  # 按文本估算等待时的最短窗口
    audio_capture_hard_max_wait_seconds: float # 按文本估算等待时的硬上限
    audio_capture_estimated_chars_per_second: float # 按文本估算朗读时长的字符/秒
    audio_capture_wait_padding_seconds: float # 按文本估算等待时额外冗余
    audio_network_capture: AudioNetworkCaptureConfig # 页面内网络音频捕获配置
    audio_browser_tts_fallback: AudioBrowserTtsFallbackConfig # 浏览器页内直接拉 TTS 的兜底
    audio_capture_poll_seconds: float      # 捕获状态轮询间隔
    audio_capture_silence_seconds: float   # 检测到静默后结束捕获
    audio_capture_activity_threshold: float # 音量活动检测阈值
    audio_capture_activity_silence_seconds: float # 音量静默多久后结束捕获
# ================= 流式监控配置 =================

class SendConfirmationConfig(TypedDict, total=False):
    """Post-click send confirmation strategy."""
    attachment_sensitivity: Literal["low", "medium", "high"]
    post_click_observe_window: float
    pre_retry_probe_window: float
    retry_observe_window: float
    attachment_observe_window: float
    max_retry_count: int
    retry_interval: float
    retry_cooldown_window: float
    retry_action: Literal["click_send_btn", "key_press"]
    retry_key_combo: str
    retry_on_unconfirmed_send: bool
    accept_attachment_change: bool
    accept_attachment_disappear: bool
    accept_probe_confirmation: bool
    retry_block_on_stop_button: bool
    retry_block_if_generating: bool
    trust_network_activity: bool
    trust_generating_indicator: bool
    trust_send_disabled_with_input_shrink: bool


class AttachmentMonitorConfig(TypedDict, total=False):
    """Per-site attachment readiness heuristics."""
    root_selectors: List[str]
    attachment_selectors: List[str]
    pending_selectors: List[str]
    busy_text_markers: List[str]
    ignored_busy_text_markers: List[str]
    send_button_disabled_markers: List[str]
    require_attachment_present: bool
    require_upload_signal_before_ready: bool
    continue_once_on_unconfirmed_send: bool
    idle_timeout: float
    hard_max_wait: float


class NetworkConfig(TypedDict, total=False):
    """Network stream capture configuration."""
    listen_pattern: str
    stream_match_mode: Literal["keyword", "regex"]
    stream_match_pattern: str
    parser: str
    silence_threshold: float
    response_interval: float


class StreamConfig(TypedDict, total=False):
    """流式监控配置（可选字段）"""
    mode: Literal["dom", "network"]
    request_transport: Dict[str, Any]
    hard_timeout: float
    network: NetworkConfig
    send_confirmation: SendConfirmationConfig
    attachment_monitor: AttachmentMonitorConfig


# ================= 站点高级配置 =================

class SiteAdvancedConfig(TypedDict, total=False):
    """站点/预设高级功能配置。"""
    independent_cookies: bool
    independent_cookies_auto_takeover: bool
    input_box_stability_wait_enabled: bool
    input_box_stability_wait_after_new_chat_only: bool
    input_box_stability_wait_timeout: float
    url_transition_wait_on_new_chat: bool
    url_transition_wait_patterns: List[str]
    send_confirmation_check_enabled: bool
    send_confirmation_check_timeout: float
    skip_new_chat_on_retry: bool


SITE_ADVANCED_FIELDS = {
    "independent_cookies",
    "independent_cookies_auto_takeover",
}

PRESET_ADVANCED_FIELDS = {
    "input_box_stability_wait_enabled",
    "input_box_stability_wait_after_new_chat_only",
    "input_box_stability_wait_timeout",
    "url_transition_wait_on_new_chat",
    "url_transition_wait_patterns",
    "send_confirmation_check_enabled",
    "send_confirmation_check_timeout",
    "skip_new_chat_on_retry",
}

ADVANCED_FIELDS = SITE_ADVANCED_FIELDS | PRESET_ADVANCED_FIELDS


def get_default_site_advanced_config() -> 'SiteAdvancedConfig':
    """获取默认站点高级配置"""
    return {
        "independent_cookies": False,
        "independent_cookies_auto_takeover": False,
        "input_box_stability_wait_enabled": False,
        "input_box_stability_wait_after_new_chat_only": True,
        "input_box_stability_wait_timeout": 1.5,
        "url_transition_wait_on_new_chat": False,
        "url_transition_wait_patterns": [],
        "send_confirmation_check_enabled": False,
        "send_confirmation_check_timeout": 1.5,
        "skip_new_chat_on_retry": False,
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
    prompt_padding: PromptPaddingConfig
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
    retry_send_btn: Optional[str]
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

    model_config = ConfigDict(
        json_schema_extra={
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
    )


class ExtractorTestRequest(BaseModel):
    """API 请求：测试提取器"""
    site_id: str
    extractor_id: str
    test_prompt: str = "Hello, test."

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "site_id": "chatgpt.com",
                "extractor_id": "deep_mode_v1",
                "test_prompt": "Write a short poem."
            }
        }
    )


class ExtractorVerifyRequest(BaseModel):
    """API 请求：验证提取结果"""
    extracted_text: str
    expected_text: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "extracted_text": "Roses are red...",
                "expected_text": "Roses are red..."
            }
        }
    )


class ExtractorVerifyResponse(BaseModel):
    """API 响应：验证结果"""
    similarity: float  # 0.0 - 1.0
    passed: bool       # >= 0.95 视为通过
    message: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "similarity": 0.973,
                "passed": True,
                "message": "验证通过"
            }
        }
    )


class ExtractorAssignRequest(BaseModel):
    """API 请求：为站点分配提取器"""
    extractor_id: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "extractor_id": "deep_mode_v1"
            }
        }
    )


# ================= 工具函数 =================

def validate_workflow_step(step: Dict[str, Any]) -> bool:
    """验证工作流步骤是否有效"""
    required_keys = {"action", "target", "optional"}
    return all(key in step for key in required_keys)

def get_default_image_extraction_config() -> ImageExtractionConfig:
    """获取默认的多模态提取配置"""
    return {
        "enabled": False,
        "modalities": normalize_modalities_config({}),
        "selector": "img",
        "audio_selector": "audio, audio source",
        "video_selector": "video, video source",
        "container_selector": None,
        "force_postprocess": False,
        "debounce_seconds": 2.0,
        "wait_for_load": True,
        "load_timeout_seconds": 5.0,
        "download_blobs": True,
        "max_size_mb": 10,
        "canvas_export_mime": "image/jpeg",
        "canvas_export_quality": 0.88,
        "src_allow_patterns": [],
        "mode": "all",
        "audio_capture_enabled": True,
        "audio_capture_mute_playback": True,
        "audio_capture_preload_enabled": True,
        "audio_capture_reload_before_workflow": False,
        "audio_capture_preserve_graph": True,
        "audio_capture_terminal_settle_seconds": 0.35,
        "audio_trigger_selector": "",
        "audio_trigger_labels": ["朗读", "语音朗读", "收听", "read aloud", "listen", "tts", "voice"],
        "audio_capture_max_wait_seconds": 12.0,
        "audio_capture_min_wait_seconds": 2.0,
        "audio_capture_hard_max_wait_seconds": 45.0,
        "audio_capture_estimated_chars_per_second": 4.8,
        "audio_capture_wait_padding_seconds": 1.2,
        "audio_network_capture": {
            "enabled": False,
            "timeout_seconds": 2.5,
            "transport": "page_websocket_probe",
            "url_patterns": ["voicegenie", "speech", "audio", "tts"],
            "extractor": "voicegenie_binary_stream",
            "settle_seconds": 0.35,
            "max_payload_bytes": 10 * 1024 * 1024,
        },
        "audio_browser_tts_fallback": {
            "enabled": False,
            "provider": "doubao_samantha",
            "speaker": "2",
            "speech_rate": 0,
            "pitch": 0,
            "format": "aac",
            "timeout_seconds": 30.0,
            "pc_version": "3.20.2",
            "aid": "497858",
            "real_aid": "497858",
            "language": "zh",
            "device_platform": "web",
            "pkg_type": "release_version",
            "region": "CN",
            "sys_region": "CN",
            "use_olympus_account": "1",
            "samantha_web": "1",
        },
        "audio_capture_poll_seconds": 0.25,
        "audio_capture_silence_seconds": 1.2,
        "audio_capture_activity_threshold": 0.006,
        "audio_capture_activity_silence_seconds": 0.65,
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

        if "mode" in stream_config:
            mode = str(stream_config["mode"]).strip().lower()
            if mode not in {"dom", "network"}:
                return False

        if "hard_timeout" in stream_config and not isinstance(stream_config["hard_timeout"], (int, float)):
            return False

        if "request_transport" in stream_config:
            request_transport = stream_config["request_transport"]
            if not isinstance(request_transport, dict):
                return False
            if "mode" in request_transport and not isinstance(request_transport["mode"], str):
                return False
            if "profile" in request_transport and not isinstance(request_transport["profile"], str):
                return False
            if "options" in request_transport and not isinstance(request_transport["options"], dict):
                return False

        if "network" in stream_config:
            if not isinstance(stream_config["network"], dict):
                return False

            network_config = stream_config["network"]
            string_fields = [
                "listen_pattern",
                "stream_match_pattern",
                "parser",
            ]
            numeric_fields = [
                "silence_threshold",
                "response_interval",
            ]

            for key in string_fields:
                if key in network_config and not isinstance(network_config[key], str):
                    return False

            for key in numeric_fields:
                if key in network_config and not isinstance(network_config[key], (int, float)):
                    return False

            if "stream_match_mode" in network_config:
                mode = str(network_config["stream_match_mode"]).strip().lower()
                if mode not in {"keyword", "regex"}:
                    return False

        if "send_confirmation" in stream_config:
            if not isinstance(stream_config["send_confirmation"], dict):
                return False

            send_confirmation = stream_config["send_confirmation"]
            numeric_fields = [
                "post_click_observe_window",
                "pre_retry_probe_window",
                "retry_observe_window",
                "attachment_observe_window",
                "retry_interval",
                "retry_cooldown_window",
            ]
            int_fields = [
                "max_retry_count",
            ]
            string_fields = [
                "retry_key_combo",
            ]
            bool_fields = [
                "retry_on_unconfirmed_send",
                "accept_attachment_change",
                "accept_attachment_disappear",
                "accept_probe_confirmation",
                "retry_block_on_stop_button",
                "retry_block_if_generating",
                "trust_network_activity",
                "trust_generating_indicator",
                "trust_send_disabled_with_input_shrink",
            ]
            enum_fields = {
                "attachment_sensitivity": {"low", "medium", "high"},
                "retry_action": {"click_send_btn", "key_press"},
            }

            for key in numeric_fields:
                if key in send_confirmation and not isinstance(send_confirmation[key], (int, float)):
                    return False

            for key in int_fields:
                if key in send_confirmation and not isinstance(send_confirmation[key], int):
                    return False

            for key in string_fields:
                if key in send_confirmation and not isinstance(send_confirmation[key], str):
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

        if "attachment_monitor" in stream_config:
            if not isinstance(stream_config["attachment_monitor"], dict):
                return False

            attachment_monitor = stream_config["attachment_monitor"]
            numeric_fields = [
                "idle_timeout",
                "hard_max_wait",
            ]
            bool_fields = [
                "require_attachment_present",
                "require_upload_signal_before_ready",
                "continue_once_on_unconfirmed_send",
            ]
            list_fields = [
                "root_selectors",
                "attachment_selectors",
                "pending_selectors",
                "busy_text_markers",
                "ignored_busy_text_markers",
                "send_button_disabled_markers",
            ]

            for key in numeric_fields:
                if key in attachment_monitor and not isinstance(attachment_monitor[key], (int, float)):
                    return False

            for key in bool_fields:
                if key in attachment_monitor and not isinstance(attachment_monitor[key], bool):
                    return False

            for key in list_fields:
                if key not in attachment_monitor:
                    continue
                value = attachment_monitor[key]
                if not isinstance(value, list):
                    return False
                if any(not isinstance(item, str) for item in value):
                    return False

    if "file_paste" in config:
        if not isinstance(config["file_paste"], dict):
            return False

        file_paste = config["file_paste"]
        if "enabled" in file_paste and not isinstance(file_paste["enabled"], bool):
            return False
        if "threshold" in file_paste and not isinstance(file_paste["threshold"], int):
            return False
        if "temp_file_type" in file_paste:
            temp_file_type = str(file_paste["temp_file_type"]).strip().lower()
            if temp_file_type not in {"txt", "pdf", "error"}:
                return False
        if "hint_text" in file_paste and not isinstance(file_paste["hint_text"], str):
            return False
        if "txt_hint_text" in file_paste and not isinstance(file_paste["txt_hint_text"], str):
            return False
        if "pdf_hint_text" in file_paste and not isinstance(file_paste["pdf_hint_text"], str):
            return False
        if "error_hint_text" in file_paste and not isinstance(file_paste["error_hint_text"], str):
            return False
        if "reacquire_input_after_upload" in file_paste and not isinstance(file_paste["reacquire_input_after_upload"], bool):
            return False
        if "post_upload_input_selector" in file_paste and not isinstance(file_paste["post_upload_input_selector"], str):
            return False
        if "post_upload_settle" in file_paste and not isinstance(file_paste["post_upload_settle"], (int, float)):
            return False
        if "upload_signal_timeout" in file_paste and not isinstance(file_paste["upload_signal_timeout"], (int, float)):
            return False
        if "upload_signal_grace" in file_paste and not isinstance(file_paste["upload_signal_grace"], (int, float)):
            return False
        if "state_probe" in file_paste:
            state_probe = file_paste["state_probe"]
            if not isinstance(state_probe, dict):
                return False
            if "enabled" in state_probe and not isinstance(state_probe["enabled"], bool):
                return False
            if "code" in state_probe and not isinstance(state_probe["code"], str):
                return False

        if "send_confirmation" in file_paste:
            send_confirmation = file_paste["send_confirmation"]
            if not isinstance(send_confirmation, dict):
                return False

            numeric_fields = [
                "post_click_observe_window",
                "pre_retry_probe_window",
                "retry_observe_window",
                "attachment_observe_window",
                "retry_interval",
                "retry_cooldown_window",
            ]
            int_fields = [
                "max_retry_count",
            ]
            string_fields = [
                "retry_key_combo",
            ]
            bool_fields = [
                "retry_on_unconfirmed_send",
                "accept_attachment_change",
                "accept_attachment_disappear",
                "accept_probe_confirmation",
                "retry_block_on_stop_button",
                "retry_block_if_generating",
                "trust_network_activity",
                "trust_generating_indicator",
                "trust_send_disabled_with_input_shrink",
            ]
            enum_fields = {
                "attachment_sensitivity": {"low", "medium", "high"},
                "retry_action": {"click_send_btn", "key_press"},
            }

            for key in numeric_fields:
                if key in send_confirmation and not isinstance(send_confirmation[key], (int, float)):
                    return False

            for key in int_fields:
                if key in send_confirmation and not isinstance(send_confirmation[key], int):
                    return False

            for key in string_fields:
                if key in send_confirmation and not isinstance(send_confirmation[key], str):
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

        if "attachment_monitor" in file_paste:
            attachment_monitor = file_paste["attachment_monitor"]
            if not isinstance(attachment_monitor, dict):
                return False

            numeric_fields = [
                "idle_timeout",
                "hard_max_wait",
            ]
            bool_fields = [
                "require_attachment_present",
                "require_upload_signal_before_ready",
                "continue_once_on_unconfirmed_send",
            ]
            list_fields = [
                "root_selectors",
                "attachment_selectors",
                "pending_selectors",
                "busy_text_markers",
                "ignored_busy_text_markers",
                "send_button_disabled_markers",
            ]

            for key in numeric_fields:
                if key in attachment_monitor and not isinstance(attachment_monitor[key], (int, float)):
                    return False

            for key in bool_fields:
                if key in attachment_monitor and not isinstance(attachment_monitor[key], bool):
                    return False

            for key in list_fields:
                if key not in attachment_monitor:
                    continue
                value = attachment_monitor[key]
                if not isinstance(value, list):
                    return False
                if any(not isinstance(item, str) for item in value):
                    return False

    if "prompt_padding" in config:
        if not isinstance(config["prompt_padding"], dict):
            return False

        prompt_padding = config["prompt_padding"]
        if "enabled" in prompt_padding and not isinstance(prompt_padding["enabled"], bool):
            return False
        if "marker_text" in prompt_padding and not isinstance(prompt_padding["marker_text"], str):
            return False
        if "segments_per_side" in prompt_padding and not isinstance(prompt_padding["segments_per_side"], int):
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
        "max_retry_count": 2,
        "retry_interval": 0.6,
        "retry_cooldown_window": 1.5,
        "retry_action": "click_send_btn",
        "retry_key_combo": "Enter",
        "retry_on_unconfirmed_send": True,
        "accept_attachment_change": False,
        "accept_attachment_disappear": False,
        "accept_probe_confirmation": True,
        "retry_block_on_stop_button": True,
        "retry_block_if_generating": True,
        "trust_network_activity": True,
        "trust_generating_indicator": True,
        "trust_send_disabled_with_input_shrink": True,
    }


def get_default_attachment_monitor_config() -> AttachmentMonitorConfig:
    """Get default per-site attachment monitor rules."""
    return {
        "root_selectors": [],
        "attachment_selectors": [],
        "pending_selectors": [],
        "busy_text_markers": [],
        "send_button_disabled_markers": [],
        "require_attachment_present": False,
        "require_upload_signal_before_ready": False,
        "continue_once_on_unconfirmed_send": True,
        "idle_timeout": 8.0,
        "hard_max_wait": 90.0,
    }


def get_default_stream_config() -> StreamConfig:
    """获取默认的流式监控配置"""
    return {
        "request_transport": {
            "mode": "workflow",
            "profile": "",
            "options": {},
        },
        "send_confirmation": get_default_send_confirmation_config(),
        "attachment_monitor": get_default_attachment_monitor_config(),
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

    default_request_transport = defaults.get("request_transport")
    site_request_transport = site_config.get("request_transport")
    if isinstance(default_request_transport, dict):
        result["request_transport"] = copy.deepcopy(default_request_transport)
        if isinstance(site_request_transport, dict):
            result["request_transport"].update(site_request_transport)
            if isinstance(default_request_transport.get("options"), dict):
                result["request_transport"]["options"] = copy.deepcopy(default_request_transport.get("options") or {})
                if isinstance(site_request_transport.get("options"), dict):
                    result["request_transport"]["options"].update(site_request_transport["options"])
    elif isinstance(site_request_transport, dict):
        result["request_transport"] = copy.deepcopy(site_request_transport)

    default_send_confirmation = defaults.get("send_confirmation")
    site_send_confirmation = site_config.get("send_confirmation")
    if isinstance(default_send_confirmation, dict):
        result["send_confirmation"] = default_send_confirmation.copy()
        if isinstance(site_send_confirmation, dict):
            result["send_confirmation"].update(site_send_confirmation)
    elif isinstance(site_send_confirmation, dict):
        result["send_confirmation"] = site_send_confirmation.copy()

    default_attachment_monitor = defaults.get("attachment_monitor")
    site_attachment_monitor = site_config.get("attachment_monitor")
    if isinstance(default_attachment_monitor, dict):
        result["attachment_monitor"] = default_attachment_monitor.copy()
        if isinstance(site_attachment_monitor, dict):
            result["attachment_monitor"].update(site_attachment_monitor)
    elif isinstance(site_attachment_monitor, dict):
        result["attachment_monitor"] = site_attachment_monitor.copy()

    return result


# ================= 导出列表 =================

__all__ = [
    # 类型定义
    'ActionType',
    'WorkflowStep',
    'SelectorDefinition',
    'GlobalConfig',
    'StreamConfig',
    'NetworkConfig',
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
    'SITE_ADVANCED_FIELDS',
    'PRESET_ADVANCED_FIELDS',
    'ADVANCED_FIELDS',

    # 工具函数
    'get_default_selector_definitions',
    'validate_workflow_step',
    'validate_site_config',
    'SendConfirmationConfig',
    'AttachmentMonitorConfig',
    'get_default_send_confirmation_config',
    'get_default_attachment_monitor_config',
    'get_default_stream_config',
    'merge_stream_config',
    'ImageData',
    'ImageExtractionConfig',
    'ModalityPolicyConfig',
    'ModalityRunPolicy',
    'normalize_modalities_config',
    'normalize_modality_policy',
    'get_modality_policy',
    'is_modality_enabled',
    'get_modality_run_policy',
    'get_enabled_modalities',
    'get_default_image_extraction_config',
    'FilePasteConfig',
    'get_default_file_paste_config',
    'PromptPaddingConfig',
    'get_default_prompt_padding_config',
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
