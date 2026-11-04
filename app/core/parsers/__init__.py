"""
app/core/parsers - 网络响应解析器

职责：
- 定义响应解析的标准接口
- 提供注册机制支持多站点适配
- 解析增量响应数据
"""

from .aistudio_parser import AIStudioParser
from .base import ResponseParser
from .chatgpt_parser import ChatGPTParser
from .claude_parser import ClaudeParser
from .deepseek_parser import DeepSeekParser
from .doubao_parser import DoubaoParser
from .gemini_parser import GeminiParser
from .glm_parser import GLMParser
from .grok_parser import GrokParser
from .kimi_parser import KimiParser
from .lmarena_battle_side_parser import (
    LmarenaBattleSideLeftParser,
    LmarenaBattleSideRightParser,
    LmarenaBattleWinnerParser,
)
from .lmarena_image_side_left_parser import LmarenaImageSideLeftParser
from .lmarena_image_side_right_parser import LmarenaImageSideRightParser
from .lmarena_parser import LmarenaParser
from .lmarena_side_left_parser import LmarenaSideLeftParser
from .mimo_runtime_parser import MimoParser
from .qwen_parser import QwenParser
from .registry import ParserRegistry

# 自动注册内置解析器
ParserRegistry.register_class(GeminiParser)
ParserRegistry.register_class(ChatGPTParser)
ParserRegistry.register_class(DeepSeekParser)
ParserRegistry.register_class(AIStudioParser)
ParserRegistry.register_class(DoubaoParser)
ParserRegistry.register_class(ClaudeParser)
ParserRegistry.register_class(KimiParser)
ParserRegistry.register_class(GLMParser)
ParserRegistry.register_class(QwenParser)
ParserRegistry.register_class(MimoParser)
ParserRegistry.register_class(LmarenaParser)
ParserRegistry.register_class(LmarenaSideLeftParser)
ParserRegistry.register_class(LmarenaBattleWinnerParser)
ParserRegistry.register_class(LmarenaBattleSideLeftParser)
ParserRegistry.register_class(LmarenaBattleSideRightParser)
ParserRegistry.register_class(LmarenaImageSideLeftParser)
ParserRegistry.register_class(LmarenaImageSideRightParser)
ParserRegistry.register_class(GrokParser)

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
    'GLMParser',
    'QwenParser',
    'MimoParser',
    'LmarenaParser',
    'LmarenaSideLeftParser',
    'LmarenaBattleWinnerParser',
    'LmarenaBattleSideLeftParser',
    'LmarenaBattleSideRightParser',
    'LmarenaImageSideLeftParser',
    'LmarenaImageSideRightParser',
    'GrokParser',
]
