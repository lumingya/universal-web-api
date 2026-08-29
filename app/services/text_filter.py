"""
app/services/text_filter.py - 通用响应文本清理与过滤器管道

职责：
- 集中管理响应文本中的多媒体占位符、临时链接与垃圾标记的清理
- 提供通用的 sanitize_response_text 处理流（占位符剔除 -> 空行规范化 -> Stop 序列截断）
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Pattern, Sequence, Union


# 默认的媒体生成占位符正则列表（如 Gemini / Google 等平台在生成媒体时的临时 URL 占位行）
DEFAULT_MEDIA_PLACEHOLDER_PATTERNS: List[Pattern[str]] = [
    re.compile(
        r"^\s*https?://(?:[\w.-]+\.)?googleusercontent\.com/(?:image_generation_content|generated_music_content)/\d+\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
]


def sanitize_response_text(
    text: str,
    *,
    stop_sequences: Optional[Union[str, Sequence[str]]] = None,
    extra_patterns: Optional[Sequence[Pattern[str]]] = None,
    strip_consecutive_newlines: bool = True,
) -> str:
    """Clean and normalize full assistant response text.
    
    1. Removes known platform temporary/placeholder URL lines.
    2. Compresses 3+ consecutive newlines into 2.
    3. Truncates text based on requested stop sequences.
    """
    if not text:
        return ""

    content = str(text)

    # 1. 过滤占位符正则
    patterns: List[Pattern[str]] = list(DEFAULT_MEDIA_PLACEHOLDER_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)

    for pattern in patterns:
        content = pattern.sub("", content)

    # 2. 规范化连续换行
    if strip_consecutive_newlines:
        content = re.sub(r"\n{3,}", "\n\n", content).strip()

    # 3. 截断 stop sequences (延迟导入避免 app.api.__init__ 循环引用)
    if stop_sequences:
        from app.api.openai_stop import apply_stop_sequences_to_text

        content = apply_stop_sequences_to_text(content, stop_sequences)

    return content
