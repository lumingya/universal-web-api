"""
app/utils/system_clipboard.py - 平台原生剪贴板适配

职责：
- 保留 Windows 上的原生文件/图片剪贴板能力
- 为非 Windows 平台提供显式不可用信号，让上层走网页原生上传回退
"""

from __future__ import annotations

import io
import os
import struct

from app.utils.platform import is_windows


class ClipboardUnsupportedError(RuntimeError):
    """当前平台不支持该原生剪贴板能力。"""


class ClipboardDependencyError(RuntimeError):
    """缺少平台剪贴板依赖。"""


def supports_native_file_clipboard() -> bool:
    return is_windows()


def supports_native_image_clipboard() -> bool:
    return is_windows()


def copy_file_to_native_clipboard(filepath: str) -> None:
    """
    使用系统原生文件剪贴板复制文件。

    目前仅支持 Windows 的 CF_HDROP。
    """
    if not supports_native_file_clipboard():
        raise ClipboardUnsupportedError("native file clipboard is only supported on Windows")

    try:
        import win32clipboard
    except ImportError as exc:
        raise ClipboardDependencyError("pywin32 is required for the Windows file clipboard backend") from exc

    abs_path = os.path.abspath(filepath)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(abs_path)

    file_list = abs_path + "\0"
    file_list += "\0"
    file_list_bytes = file_list.encode("utf-16-le")
    header = struct.pack("IIIii", 20, 0, 0, 0, 1)
    data = header + file_list_bytes
    cf_hdrop = 15

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(cf_hdrop, data)
    finally:
        win32clipboard.CloseClipboard()


def copy_image_to_native_clipboard(image_path: str) -> None:
    """
    使用系统原生图片剪贴板复制图片。

    目前仅支持 Windows 的 CF_DIB。
    """
    if not supports_native_image_clipboard():
        raise ClipboardUnsupportedError("native image clipboard is only supported on Windows")

    try:
        import win32clipboard
    except ImportError as exc:
        raise ClipboardDependencyError("pywin32 is required for the Windows image clipboard backend") from exc

    from PIL import Image

    image = Image.open(image_path)
    if image.mode != "RGB":
        image = image.convert("RGB")

    output = io.BytesIO()
    image.save(output, "BMP")
    data = output.getvalue()[14:]

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    finally:
        win32clipboard.CloseClipboard()


__all__ = [
    "ClipboardDependencyError",
    "ClipboardUnsupportedError",
    "copy_file_to_native_clipboard",
    "copy_image_to_native_clipboard",
    "supports_native_file_clipboard",
    "supports_native_image_clipboard",
]
