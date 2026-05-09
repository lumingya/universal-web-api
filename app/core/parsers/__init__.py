"""
app/core/parsers - 网络响应解析器

职责：
- 定义响应解析的标准接口
- 提供注册机制支持多站点适配
- 解析增量响应数据
"""

from .base import ResponseParser
from .registry import ParserRegistry
from .gemini_parser import GeminiParser
from .chatgpt_parser import ChatGPTParser
from .deepseek_parser import DeepSeekParser
from .aistudio_parser import AIStudioParser
from .doubao_parser import DoubaoParser
from .claude_parser import ClaudeParser
from .kimi_parser import KimiParser
from .qwen_parser import QwenParser
from .mimo_parser import MimoParser
from .lmarena_parser import LmarenaParser
from .lmarena_side_left_parser import LmarenaSideLeftParser

# 自动注册内置解析器
ParserRegistry.register_class(GeminiParser)
ParserRegistry.register_class(ChatGPTParser)
ParserRegistry.register_class(DeepSeekParser)
ParserRegistry.register_class(AIStudioParser)
ParserRegistry.register_class(DoubaoParser)
ParserRegistry.register_class(ClaudeParser)
ParserRegistry.register_class(KimiParser)
ParserRegistry.register_class(QwenParser)
ParserRegistry.register_class(MimoParser)
ParserRegistry.register_class(LmarenaParser)
ParserRegistry.register_class(LmarenaSideLeftParser)

__all__ = [
    'ResponseParser',
    'ParserRegistry',
    'GeminiParser',
    'ChatGPTParser',
    'DeepSeekParser',
    'AIStudioParser',
    'DoubaoParser',
    'ClaudeParser',
    'KimiParser',
    'QwenParser',
    'MimoParser',
    'LmarenaParser',
    'LmarenaSideLeftParser',
]
