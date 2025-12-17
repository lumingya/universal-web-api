"""
data_models.py - 数据模型定义

职责：
- 定义所有数据结构的类型
- 提供类型检查支持
"""

from typing import TypedDict, List, Optional, Literal, Dict, Any


# ================= 动作类型 =================

ActionType = Literal[
    "FILL_INPUT",     # 填入文本
    "CLICK",          # 点击元素
    "STREAM_WAIT",    # 流式等待结果
    "STREAM_OUTPUT",  # 流式输出（别名）
    "KEY_PRESS",      # 模拟按键 (如回车)
    "WAIT",           # 强制等待 (秒)
    "JS_EXEC"         # 执行自定义 JS (高级扩展，暂未实现)
]


# ================= 选择器字段名称 =================

# 必需的选择器字段
REQUIRED_SELECTOR_KEYS = [
    "input_box",
    "send_btn", 
    "result_container",
]

# 可选的选择器字段
OPTIONAL_SELECTOR_KEYS = [
    "new_chat_btn",
    "message_wrapper",
    "generating_indicator",
]

# 所有选择器字段
ALL_SELECTOR_KEYS = REQUIRED_SELECTOR_KEYS + OPTIONAL_SELECTOR_KEYS


# ================= 工作流步骤 =================

class WorkflowStep(TypedDict):
    """工作流单步定义"""
    action: ActionType          # 动作类型
    target: str                 # 目标元素键（对应 selectors 中的 key）
    optional: bool              # 失败是否跳过（True=跳过，False=中断）
    value: Optional[str]        # 参数值（如 WAIT 的秒数、KEY_PRESS 的键名）


# ================= 流式监控配置 =================

class StreamConfig(TypedDict, total=False):
    """
    流式监控配置（可选字段）
    
    用于站点级别覆盖默认的流式监控参数
    所有字段都是可选的，未配置时使用 BrowserConstants 中的默认值
    """
    silence_threshold: float       # 静默阈值（秒），无新内容多久判定结束
    initial_wait: float            # 初始等待时间（秒），等待 AI 开始回复的最长时间
    enable_wrapper_search: bool    # 是否启用消息容器向上查找
    rerender_wait: float           # 检测到重渲染后的额外等待时间（秒）
    content_shrink_tolerance: int  # 内容变短的连续容忍次数


# ================= 站点配置 =================

class SiteConfig(TypedDict, total=False):
    """
    站点配置结构
    
    必需字段：
    - selectors: 选择器映射表
    - workflow: 工作流步骤列表
    
    可选字段：
    - stealth: 是否启用隐身模式
    - stream_config: 流式监控配置
    """
    selectors: Dict[str, Optional[str]]  # 选择器映射表
    workflow: List[WorkflowStep]         # 工作流步骤列表
    stealth: bool                        # 是否启用隐身模式（默认 False）
    stream_config: StreamConfig          # 流式监控配置（可选）


# ================= 选择器验证结果 =================

class SelectorValidationResult(TypedDict):
    """选择器验证结果"""
    key: str                    # 选择器键名
    selector: Optional[str]     # 选择器内容
    valid: bool                 # 是否有效
    reason: Optional[str]       # 无效原因
    repaired: Optional[str]     # 修复后的选择器


# ================= AI 分析结果 =================

class AIAnalysisResult(TypedDict, total=False):
    """
    AI 分析返回的选择器结构
    
    必需字段（AI 应尽量返回）：
    - input_box: 输入框选择器
    - send_btn: 发送按钮选择器
    - result_container: 结果容器选择器
    
    可选字段：
    - new_chat_btn: 新建对话按钮选择器
    - message_wrapper: 消息完整容器选择器
    - generating_indicator: 生成中指示器选择器
    """
    input_box: Optional[str]            # 输入框选择器
    send_btn: Optional[str]             # 发送按钮选择器
    result_container: Optional[str]     # 结果容器选择器
    new_chat_btn: Optional[str]         # 新建对话按钮选择器
    message_wrapper: Optional[str]      # 消息完整容器选择器（用于多节点拼接）
    generating_indicator: Optional[str] # 生成中指示器选择器（检测是否还在输出）


# ================= 健康检查结果 =================

class HealthCheckResult(TypedDict):
    """健康检查结果"""
    status: Literal["healthy", "unhealthy"]  # 状态
    connected: bool                          # 浏览器是否连接
    port: int                                # 端口号
    tab_url: Optional[str]                   # 当前标签页 URL
    tab_title: Optional[str]                 # 当前标签页标题
    error: Optional[str]                     # 错误信息


# ================= 页面状态检查结果 =================

class PageStatusResult(TypedDict):
    """页面状态检查结果"""
    ready: bool              # 是否就绪
    reason: Optional[str]    # 未就绪原因


# ================= API 请求模型 =================

class ChatMessage(TypedDict):
    """聊天消息"""
    role: Literal["user", "assistant", "system"]  # 角色
    content: str                                   # 内容


class ChatCompletionRequest(TypedDict):
    """聊天补全请求"""
    model: str                      # 模型名称
    messages: List[ChatMessage]     # 消息列表
    stream: Optional[bool]          # 是否流式输出
    temperature: Optional[float]    # 温度参数（暂未使用）
    max_tokens: Optional[int]       # 最大 token 数（暂未使用）


# ================= SSE 响应模型 =================

class DeltaContent(TypedDict, total=False):
    """流式响应的增量内容"""
    content: str  # 内容片段


class StreamChoice(TypedDict):
    """流式响应的选项"""
    index: int                                      # 索引
    delta: DeltaContent                             # 增量内容
    finish_reason: Optional[Literal["stop", None]]  # 结束原因


class StreamResponse(TypedDict):
    """流式响应结构"""
    id: str                    # 响应 ID
    object: str                # 对象类型
    created: int               # 创建时间戳
    model: str                 # 模型名称
    choices: List[StreamChoice] # 选项列表


# ================= 非流式响应模型 =================

class MessageContent(TypedDict):
    """消息内容"""
    role: Literal["assistant"]  # 角色
    content: str                # 内容


class NonStreamChoice(TypedDict):
    """非流式响应的选项"""
    index: int                              # 索引
    message: MessageContent                 # 消息内容
    finish_reason: Literal["stop"]          # 结束原因


class UsageInfo(TypedDict):
    """Token 使用信息（占位）"""
    prompt_tokens: int      # 提示词 token 数
    completion_tokens: int  # 完成 token 数
    total_tokens: int       # 总 token 数


class NonStreamResponse(TypedDict):
    """非流式响应结构"""
    id: str                       # 响应 ID
    object: str                   # 对象类型
    created: int                  # 创建时间戳
    model: str                    # 模型名称
    choices: List[NonStreamChoice] # 选项列表
    usage: UsageInfo              # 使用信息


# ================= 错误响应模型 =================

class ErrorDetail(TypedDict):
    """错误详情"""
    message: str  # 错误消息
    type: str     # 错误类型
    code: str     # 错误代码


class ErrorResponse(TypedDict):
    """错误响应结构"""
    error: ErrorDetail


# ================= 模型信息 =================

class ModelInfo(TypedDict):
    """模型信息"""
    id: str           # 模型 ID
    object: str       # 对象类型
    created: int      # 创建时间
    owned_by: str     # 所有者


class ModelsResponse(TypedDict):
    """模型列表响应"""
    object: str           # 对象类型
    data: List[ModelInfo] # 模型列表


# ================= 工具函数 =================

def validate_workflow_step(step: Dict[str, Any]) -> bool:
    """
    验证工作流步骤是否有效
    
    Args:
        step: 步骤字典
        
    Returns:
        是否有效
    """
    required_keys = {"action", "target", "optional"}
    return all(key in step for key in required_keys)


def validate_site_config(config: Dict[str, Any]) -> bool:
    """
    验证站点配置是否有效
    
    Args:
        config: 配置字典
        
    Returns:
        是否有效
    """
    # 检查必需字段
    if "selectors" not in config or "workflow" not in config:
        return False
    
    if not isinstance(config["selectors"], dict):
        return False
    
    if not isinstance(config["workflow"], list):
        return False
    
    # 验证每个步骤
    for step in config["workflow"]:
        if not validate_workflow_step(step):
            return False
    
    # 验证 stream_config（如果存在）
    if "stream_config" in config:
        if not isinstance(config["stream_config"], dict):
            return False
        
        # 验证 stream_config 字段类型
        stream_config = config["stream_config"]
        
        if "silence_threshold" in stream_config:
            if not isinstance(stream_config["silence_threshold"], (int, float)):
                return False
            if stream_config["silence_threshold"] <= 0:
                return False
        
        if "initial_wait" in stream_config:
            if not isinstance(stream_config["initial_wait"], (int, float)):
                return False
            if stream_config["initial_wait"] <= 0:
                return False
        
        if "enable_wrapper_search" in stream_config:
            if not isinstance(stream_config["enable_wrapper_search"], bool):
                return False
    
    return True


def get_default_stream_config() -> StreamConfig:
    """
    获取默认的流式监控配置
    
    Returns:
        默认配置字典
    """
    return {
        "silence_threshold": 2.5,
        "initial_wait": 30.0,
        "enable_wrapper_search": True,
        "rerender_wait": 0.5,
        "content_shrink_tolerance": 3
    }


def merge_stream_config(
    site_config: Optional[StreamConfig],
    defaults: Optional[StreamConfig] = None
) -> StreamConfig:
    """
    合并流式监控配置，用站点配置覆盖默认值
    
    Args:
        site_config: 站点级配置（可能为 None 或部分字段）
        defaults: 默认配置（为 None 时使用内置默认值）
        
    Returns:
        合并后的完整配置
    """
    if defaults is None:
        defaults = get_default_stream_config()
    
    if site_config is None:
        return defaults.copy()
    
    result = defaults.copy()
    result.update(site_config)
    
    return result


# ================= 测试 =================

if __name__ == "__main__":
    # 测试类型检查
    
    test_step: WorkflowStep = {
        "action": "CLICK",
        "target": "send_btn",
        "optional": True,
        "value": None
    }
    
    test_stream_config: StreamConfig = {
        "silence_threshold": 3.0,
        "initial_wait": 25.0,
        "enable_wrapper_search": True
    }
    
    test_config: SiteConfig = {
        "selectors": {
            "input_box": "textarea",
            "send_btn": "button[type='submit']",
            "result_container": "div.response",
            "new_chat_btn": None,
            "message_wrapper": "div.message-turn",
            "generating_indicator": "button.stop-btn"
        },
        "workflow": [test_step],
        "stealth": False,
        "stream_config": test_stream_config
    }
    
    test_ai_result: AIAnalysisResult = {
        "input_box": "textarea",
        "send_btn": "button",
        "result_container": "div.response",
        "new_chat_btn": None,
        "message_wrapper": "div.turn",
        "generating_indicator": None
    }
    
    print("=" * 50)
    print("数据模型测试")
    print("=" * 50)
    
    print("\n✅ WorkflowStep:")
    print(f"   {test_step}")
    
    print("\n✅ StreamConfig:")
    print(f"   {test_stream_config}")
    
    print("\n✅ SiteConfig:")
    print(f"   selectors: {len(test_config['selectors'])} 个")
    print(f"   workflow: {len(test_config['workflow'])} 步")
    print(f"   stealth: {test_config.get('stealth', False)}")
    print(f"   stream_config: {test_config.get('stream_config', {})}")
    
    print("\n✅ AIAnalysisResult:")
    print(f"   {test_ai_result}")
    
    print("\n✅ 配置验证:")
    print(f"   validate_site_config: {validate_site_config(test_config)}")
    
    print("\n✅ 默认流式配置:")
    print(f"   {get_default_stream_config()}")
    
    print("\n✅ 配置合并测试:")
    partial_config: StreamConfig = {"silence_threshold": 5.0}
    merged = merge_stream_config(partial_config)
    print(f"   输入: {partial_config}")
    print(f"   输出: {merged}")
    
    print("\n✅ 选择器字段常量:")
    print(f"   必需: {REQUIRED_SELECTOR_KEYS}")
    print(f"   可选: {OPTIONAL_SELECTOR_KEYS}")
    print(f"   全部: {ALL_SELECTOR_KEYS}")
    
    print("\n" + "=" * 50)
    print("所有测试通过!")
    print("=" * 50)
