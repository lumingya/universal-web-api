"""
app/utils/media_safety.py - 媒体落盘与同源提供的安全策略（审查条目 S8 / S9）

背景：下载的图片/音视频保存在 ``download_images/`` 并通过应用**同源**的
``/download_images/*``、``/media/*`` 提供。若把上游返回的 SVG、HTML 等「活动文档」
原样落盘，受害者直接打开该链接时脚本会在控制面板同源执行。

策略（单一事实源）：

1. 写入端：扩展名只能来自白名单；Content-Type 与 URL 后缀都不能把 ``.svg``/``.html``
   之类带进来；再对文件头做嗅探，标记语言（``<svg``、``<html``、``<?xml`` …）一律拒绝。
2. 出口端：非白名单扩展名（含历史遗留的 .svg/.html）一律 ``application/octet-stream``
   + ``attachment``；所有媒体响应附带 ``nosniff`` 与 ``sandbox`` CSP，即使内容被误判也不能执行脚本。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

IMAGE_EXT_BY_MIME: Dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
}

AUDIO_EXT_BY_MIME: Dict[str, str] = {
    "audio/aac": ".aac",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
}

VIDEO_EXT_BY_MIME: Dict[str, str] = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
    "video/quicktime": ".mov",
}

SAFE_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"})
SAFE_AUDIO_EXTENSIONS = frozenset({".aac", ".mp3", ".wav", ".ogg", ".oga", ".opus", ".webm", ".m4a", ".flac"})
SAFE_VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".webm", ".ogv", ".mov"})
SAFE_MEDIA_EXTENSIONS = SAFE_IMAGE_EXTENSIONS | SAFE_AUDIO_EXTENSIONS | SAFE_VIDEO_EXTENSIONS

_SAFE_EXTS_BY_KIND = {
    "image": SAFE_IMAGE_EXTENSIONS,
    "audio": SAFE_AUDIO_EXTENSIONS,
    "video": SAFE_VIDEO_EXTENSIONS,
}
_MIME_MAP_BY_KIND = {
    "image": IMAGE_EXT_BY_MIME,
    "audio": AUDIO_EXT_BY_MIME,
    "video": VIDEO_EXT_BY_MIME,
}
_DEFAULT_EXT_BY_KIND = {"image": ".png", "audio": ".mp3", "video": ".mp4"}

# 出口端 MIME：只为白名单扩展名给出可内联的媒体类型
_SERVE_MIME_BY_EXT: Dict[str, str] = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".bmp": "image/bmp", ".avif": "image/avif",
    ".aac": "audio/aac", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".opus": "audio/ogg", ".m4a": "audio/mp4", ".flac": "audio/flac",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg",
    ".mov": "video/quicktime",
}

# sandbox（不含 allow-scripts）禁止脚本执行；media-src/img-src 'self' 保证浏览器
# 直接打开图片/音视频时的内置查看器仍能加载本身。
MEDIA_CSP = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"


def normalize_mime(content_type: Optional[str]) -> str:
    return str(content_type or "").split(";", 1)[0].strip().lower()


def is_active_content_mime(content_type: Optional[str]) -> bool:
    mime = normalize_mime(content_type)
    if not mime:
        return False
    if mime in {"text/html", "application/xhtml+xml", "image/svg+xml", "text/xml",
                "application/xml", "text/javascript", "application/javascript",
                "application/x-javascript", "text/xsl", "application/pdf", "text/plain"}:
        return True
    return mime.endswith("+xml")


def choose_media_extension(kind: str, content_type: Optional[str], url: Optional[str] = None) -> Optional[str]:
    """返回安全的落盘扩展名；``None`` 表示拒绝落盘（保留远程链接/走其它回退）。

    顺序：活动内容 MIME → 拒绝；MIME 与 kind 匹配且在白名单 → 用映射；
    否则仅当 URL 后缀在同类白名单时采用；再否则用类别默认扩展名。
    URL 后缀是 .html/.svg 等时**绝不**采用（S9 根因）。
    """
    kind = str(kind or "").strip().lower()
    safe_exts = _SAFE_EXTS_BY_KIND.get(kind)
    if safe_exts is None:
        return None
    mime = normalize_mime(content_type)
    if is_active_content_mime(mime):
        return None

    mapped = _MIME_MAP_BY_KIND[kind].get(mime)
    if mapped:
        return mapped

    # MIME 属于别的媒体大类（如 image/png 走 video 分支）→ 不信任
    if mime and "/" in mime and mime.split("/", 1)[0] in _SAFE_EXTS_BY_KIND and mime.split("/", 1)[0] != kind:
        return None

    if url:
        try:
            suffix = Path(urlparse(str(url)).path).suffix.lower()
        except Exception:
            suffix = ""
        if suffix in safe_exts:
            return ".jpg" if suffix == ".jpeg" else suffix
    return _DEFAULT_EXT_BY_KIND[kind]


_MARKUP_PREFIXES = (b"<!doctype", b"<html", b"<svg", b"<?xml", b"<script", b"<head", b"<body",
                    b"<iframe", b"<!--", b"<xsl", b"<meta", b"<a ", b"<div", b"<img", b"<object",
                    b"<embed", b"<style", b"<link", b"<form", b"<title", b"<p>", b"<?php")


def looks_like_active_content(head: bytes) -> bool:
    """嗅探文件头：以 HTML/SVG/XML 标记开头（允许 BOM 与前导空白）即判为活动内容。"""
    data = bytes(head or b"")[:1024]
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        # UTF-16 文本：真实媒体文件不会以 UTF-16 BOM 开头
        return True
    stripped = data.lstrip(b" \t\r\n\x0c").lower()
    if stripped.startswith(b"<"):
        if stripped.startswith(_MARKUP_PREFIXES):
            return True
        # 其它以 < 开头的也不是任何受支持的二进制媒体格式
        return True
    return False


def detect_image_extension(head: bytes) -> Optional[str]:
    data = bytes(head or b"")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if len(data) >= 16 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        return ".avif"
    return None


def resolve_media_delivery(path: Path, preferred_mime: Optional[str] = None) -> Tuple[str, str]:
    """出口端：返回 (media_type, content_disposition_type)。

    白名单扩展名 → 对应媒体 MIME + inline；其余（含历史遗留 .svg/.html）→ octet-stream + attachment。
    ``preferred_mime`` 仅在它本身是安全媒体类型时采用（例如转码输出）。
    """
    suffix = Path(path).suffix.lower()
    if suffix not in SAFE_MEDIA_EXTENSIONS:
        return "application/octet-stream", "attachment"
    mime = normalize_mime(preferred_mime)
    if mime and not is_active_content_mime(mime) and mime.split("/", 1)[0] in ("image", "audio", "video"):
        return mime, "inline"
    return _SERVE_MIME_BY_EXT.get(suffix, "application/octet-stream"), "inline"


def safe_media_response_headers() -> Dict[str, str]:
    # 注意：不设置 Cross-Origin-Resource-Policy —— 聊天客户端（如运行在 localhost:8000 的前端）
    # 需要跨站 <img>/<audio> 引用这些链接，CORP 会把它们一并拦掉。
    return {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": MEDIA_CSP,
        "Referrer-Policy": "no-referrer",
    }


__all__ = [
    "AUDIO_EXT_BY_MIME",
    "IMAGE_EXT_BY_MIME",
    "MEDIA_CSP",
    "SAFE_MEDIA_EXTENSIONS",
    "VIDEO_EXT_BY_MIME",
    "choose_media_extension",
    "detect_image_extension",
    "is_active_content_mime",
    "looks_like_active_content",
    "normalize_mime",
    "resolve_media_delivery",
    "safe_media_response_headers",
]
