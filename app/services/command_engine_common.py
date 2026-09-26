"""CommandEngine 及其 mixin 共用的模块级对象（R2-3 拆分时从 command_engine.py 迁出，原处仍然重新导出）。"""

import re

from app.core.config import get_logger

try:
    import requests  # noqa: F401
    HAS_REQUESTS = True
except ImportError:  # pragma: no cover - requests 为必装依赖
    requests = None  # type: ignore[assignment]
    HAS_REQUESTS = False

logger = get_logger("CMD_ENG")
FOLLOW_DEFAULT_PRESET = "__DEFAULT__"

# 页面文本匹配相关的正则预编译：这些会在 page_check 高频路径上
# 对整页快照（可能几十上百 KB）反复求值，逐次 re.* 的模式缓存查找是不必要的开销。
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")
_WORD_LIKE_KEYWORD_RE = re.compile(r"[a-z0-9 _-]+")
