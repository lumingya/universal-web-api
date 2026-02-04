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
from .lmarena_parser import LmarenaParser

# 自动注册内置解析器
ParserRegistry.register_class(GeminiParser)
ParserRegistry.register_class(ChatGPTParser)
ParserRegistry.register_class(DeepSeekParser)
ParserRegistry.register_class(AIStudioParser)
ParserRegistry.register_class(LmarenaParser)

__all__ = [
    'ResponseParser',
    'ParserRegistry',
    'GeminiParser',
    'ChatGPTParser',
    'DeepSeekParser',
    'AIStudioParser',
    'LmarenaParser',
]