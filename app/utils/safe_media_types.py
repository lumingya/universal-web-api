"""统一的落盘媒体类型白名单与活动内容拦截（修复 S8 / S9）。

背景
----
上游站点返回的媒体会被保存到 `download_images/`，再通过与控制面板**同源**的
`/media/{filename}` 与 `/download_images/{filename}` 提供出去。

原实现有两条口子：

- S8：后台下载器拒绝 SVG，但前台回退分支的 `ext_map` 里仍有
  `"image/svg+xml": ".svg"`，于是 SVG 会原样落盘并以 `image/svg+xml` 内联返回。
  SVG 是可携带脚本的活动文档，同源打开等于把上游内容当作本站脚本执行。
- S9：音视频分支只检查响应头里含不含 `video` / `audio` 子串，扩展名却直接取自
  远端 URL 路径。攻击者用 `Content-Type: video/custom` + `https://.../clip.html`
  就能把任意 HTML 写进公开目录，并以 `text/html` 内联取回。

本模块把「什么扩展名可以落盘」「什么 MIME 可以内联返回」收敛到一处，
前台/后台/HTTP 出口共用同一套判断。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

# ---------------------------------------------------------------- 扩展名白名单

SAFE_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".ico", ".tif", ".tiff",
})

SAFE_AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".weba",
})

SAFE_VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".webm", ".ogv", ".mov", ".m4v", ".mkv",
})

SAFE_MEDIA_EXTENSIONS = SAFE_IMAGE_EXTENSIONS | SAFE_AUDIO_EXTENSIONS | SAFE_VIDEO_EXTENSIONS

# 会在浏览器里被当作文档解析、可执行脚本或引用外部资源的扩展名。
# 这些内容**永远不允许**以同源可内联的方式落盘/提供。
ACTIVE_CONTENT_EXTENSIONS = frozenset({
    ".svg", ".svgz", ".html", ".htm", ".xhtml", ".xht", ".shtml", ".mhtml", ".mht",
    ".xml", ".xsl", ".xslt", ".js", ".mjs", ".cjs", ".css", ".json", ".jsonp",
    ".htc", ".hta", ".vbs", ".swf", ".pdf", ".eml",
})

ACTIVE_CONTENT_MIME_TYPES = frozenset({
    "image/svg+xml", "image/svg",
    "text/html", "application/xhtml+xml", "text/xml", "application/xml",
    "text/xsl", "application/xslt+xml",
    "text/javascript", "application/javascript", "application/x-javascript",
    "text/css", "application/json", "application/pdf",
    "text/vbscript", "application/x-shockwave-flash", "message/rfc822",
})

# MIME → 扩展名，仅包含确定安全的位图/音频/视频容器
_IMAGE_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/apng": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/x-ms-bmp": ".bmp",
    "image/avif": ".avif",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/tiff": ".tif",
}

_AUDIO_MIME_EXTENSIONS = {
    "audio/aac": ".aac",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/vnd.wave": ".wav",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/webm": ".weba",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
}

_VIDEO_MIME_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
    "video/quicktime": ".mov",
    "video/x-m4v": ".m4v",
    "video/x-matroska": ".mkv",
}

SAFE_MEDIA_MIME_EXTENSIONS = {
    **_IMAGE_MIME_EXTENSIONS,
    **_AUDIO_MIME_EXTENSIONS,
    **_VIDEO_MIME_EXTENSIONS,
}

_DEFAULT_EXTENSION_BY_KIND = {
    "image": ".png",
    "audio": ".mp3",
    "video": ".mp4",
}

_ALLOWED_EXTENSIONS_BY_KIND = {
    "image": SAFE_IMAGE_EXTENSIONS,
    "audio": SAFE_AUDIO_EXTENSIONS,
    "video": SAFE_VIDEO_EXTENSIONS,
}


def normalize_content_type(content_type: Optional[str]) -> str:
    """去掉 charset 等参数，返回小写的裸 MIME。"""
    return str(content_type or "").split(";", 1)[0].strip().lower()


def is_active_content_type(content_type: Optional[str]) -> bool:
    mime = normalize_content_type(content_type)
    if not mime:
        return False
    if mime in ACTIVE_CONTENT_MIME_TYPES:
        return True
    # 兜底：任何 */*+xml 与 text/html 变体都按活动内容处理
    return mime.endswith("+xml") or mime.startswith("text/html")


def is_active_content_extension(suffix_or_name: Optional[str]) -> bool:
    raw = str(suffix_or_name or "").strip().lower()
    if not raw:
        return False
    suffix = raw if raw.startswith(".") and "/" not in raw and "." not in raw[1:] else Path(raw).suffix.lower()
    return suffix in ACTIVE_CONTENT_EXTENSIONS


def url_path_extension(url: Optional[str]) -> str:
    """取 URL 路径部分的扩展名（忽略 query / fragment）。"""
    try:
        return Path(urlsplit(str(url or "")).path).suffix.lower()
    except Exception:
        return ""


def resolve_safe_media_extension(
    kind: str,
    content_type: Optional[str] = None,
    url: Optional[str] = None,
) -> Optional[str]:
    """为即将落盘的媒体决定一个安全扩展名。

    参数
    ----
    kind: ``"image"`` / ``"audio"`` / ``"video"``
    content_type: 上游响应的 Content-Type
    url: 上游 URL，仅在 MIME 无法识别时作为次要线索

    返回
    ----
    安全的扩展名（含点号），或 ``None`` 表示**拒绝落盘**。

    判定顺序：
    1. Content-Type 命中活动内容 → 直接拒绝（修复 S8 的 `image/svg+xml`）；
    2. Content-Type 命中安全白名单 → 用白名单扩展名，**忽略** URL 里的后缀；
    3. Content-Type 不认识、且 URL 后缀是活动内容（`.html` / `.svg` …）→ 整体拒绝。
       这是 S9 的核心场景（`video/custom` + `clip.html`）：既然两个线索都不可信，
       就不该把内容存进可同源访问的公开目录；
    4. Content-Type 不认识、URL 后缀在**同类**白名单里 → 采用该后缀；
    5. 其余情况回落到该类别的默认扩展名（`.png` / `.mp3` / `.mp4`），
       绝不使用来自 URL 的任意后缀。
    """
    category = str(kind or "").strip().lower()
    allowed = _ALLOWED_EXTENSIONS_BY_KIND.get(category)
    if allowed is None:
        return None

    mime = normalize_content_type(content_type)
    if is_active_content_type(mime):
        return None

    mapped = SAFE_MEDIA_MIME_EXTENSIONS.get(mime)
    if mapped:
        # MIME 类别必须与请求的媒体类别一致，防止把 HTML 伪装成 video 的场景
        # 换成「MIME 说自己是图片、却走了视频分支」的新绕过路径。
        return mapped if mapped in allowed else None

    path_ext = url_path_extension(url)
    if path_ext:
        if path_ext in ACTIVE_CONTENT_EXTENSIONS:
            return None
        if path_ext in allowed:
            return path_ext

    return _DEFAULT_EXTENSION_BY_KIND.get(category)


# ---------------------------------------------------------------- 内容嗅探

_ACTIVE_CONTENT_MARKERS = (
    b"<!doctype html",
    b"<html",
    b"<svg",
    b"<?xml",
    b"<script",
    b"<!entity",
    b"<base ",
    b"<iframe",
)


def looks_like_active_content(head: Optional[bytes]) -> bool:
    """按内容开头判断是否是 HTML/SVG/XML 之类的活动文档。

    这是扩展名与 MIME 白名单之外的最后一道防线：上游完全可以用
    `Content-Type: video/mp4` 返回一段 HTML。只看前若干字节，成本可忽略。
    """
    if not head:
        return False
    sample = bytes(head[:1024])
    # 跳过 BOM 与前导空白，避免用 "\n\n<svg" 这种写法绕过
    for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
        if sample.startswith(bom):
            sample = sample[len(bom):]
            break
    stripped = sample.lstrip(b" \t\r\n\x00")
    if not stripped.startswith(b"<"):
        return False
    lowered = stripped[:512].lower()
    return any(marker in lowered for marker in _ACTIVE_CONTENT_MARKERS)


# ---------------------------------------------------------------- HTTP 出口

#: 允许以 inline 方式返回的 MIME。其余一律强制下载。
INLINE_SAFE_MIME_PREFIXES = ("image/", "audio/", "video/")


def is_inline_safe_mime(content_type: Optional[str]) -> bool:
    mime = normalize_content_type(content_type)
    if not mime or is_active_content_type(mime):
        return False
    return mime.startswith(INLINE_SAFE_MIME_PREFIXES)


def safe_media_response_headers() -> dict:
    """静态媒体出口的统一加固响应头。

    - `nosniff` 阻止浏览器把 `application/octet-stream` 猜成 HTML；
    - `sandbox` + `default-src 'none'` 让万一漏网的活动文档失去脚本与同源能力；
    - `Cross-Origin-Resource-Policy` 限制被第三方页面直接引用。
    """
    return {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox; base-uri 'none'; form-action 'none'",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Referrer-Policy": "no-referrer",
    }


def resolve_media_delivery(path_or_name, content_type: Optional[str] = None) -> tuple:
    """决定静态媒体的 (media_type, content_disposition_type)。

    未在白名单内的扩展名会被降级成 `application/octet-stream` + `attachment`，
    即使文件因为历史原因已经躺在目录里，也不会再以活动文档形式打开。
    """
    suffix = Path(str(path_or_name or "")).suffix.lower()
    mime = normalize_content_type(content_type)

    if suffix in ACTIVE_CONTENT_EXTENSIONS or suffix not in SAFE_MEDIA_EXTENSIONS:
        return "application/octet-stream", "attachment"

    if mime and not is_inline_safe_mime(mime):
        return "application/octet-stream", "attachment"

    return (mime or "application/octet-stream"), "inline"
