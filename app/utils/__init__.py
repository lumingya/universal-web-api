"""
app/utils/__init__.py - 工具模块
"""

from app.utils.paste import (
    UNIVERSAL_INSERT_JS,
    clear_and_paste,
    safe_universal_paste,
)

__all__ = [
    'safe_universal_paste',
    'clear_and_paste',
    'UNIVERSAL_INSERT_JS',
]
