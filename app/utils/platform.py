"""
app/utils/platform.py - 轻量平台工具

职责：
- 统一判断当前运行平台
- 提供跨平台快捷键抽象
"""

from __future__ import annotations

import sys


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def get_primary_modifier_key() -> str:
    """
    返回系统主修饰键。

    - Windows / Linux: Control
    - macOS: Meta（对应 Command）
    """
    return "Meta" if is_macos() else "Control"


__all__ = [
    "get_primary_modifier_key",
    "is_linux",
    "is_macos",
    "is_windows",
]
