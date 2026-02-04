"""
app/utils/__init__.py - 工具模块
"""

from app.utils.paste import (
    safe_universal_paste,
    clear_and_paste,
    UNIVERSAL_INSERT_JS,
)

__all__ = [
    'safe_universal_paste',
    'clear_and_paste', 
    'UNIVERSAL_INSERT_JS',
]