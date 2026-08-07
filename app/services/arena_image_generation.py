"""Arena-only guards for browser image generation.

This module deliberately owns Arena's DOM error handling and image-to-image
validation.  Callers outside Arena should never enable these rules.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps


ARENA_NATIVE_STOP_SELECTOR = 'css:button[aria-label="Stop generation"]'
ARENA_PROMPT_REJECTED_CODE = "arena_prompt_rejected"
ARENA_IMAGE_GENERATION_FAILED_CODE = "arena_image_generation_failed"
ARENA_IMAGE_UNCHANGED_CODE = "arena_image_unchanged"
ARENA_NON_RETRYABLE_CODES = frozenset(
    {
        ARENA_PROMPT_REJECTED_CODE,
        ARENA_IMAGE_GENERATION_FAILED_CODE,
        ARENA_IMAGE_UNCHANGED_CODE,
    }
)

_IMAGE_PROMPT_MARKERS = (
    "生成图片",
    "生成图像",
    "生成一张图",
    "生成一张图片",
    "画一张",
    "画一幅",
    "帮我画",
    "请画",
    "出图",
    "做图",
    "文生图",
    "以图生图",
    "image generation",
    "generate image",
    "generate an image",
    "create image",
    "create an image",
    "draw an image",
    "draw me",
    "make an image",
    "render an image",
    "render image",
)

_INTERRUPTED_STREAM_MARKERS = (
    "未完整结束",
    "提前关闭",
    "连接已关闭",
    "未收到完成标志",
    "缺少明确结束事件",
    "未产出有效正文",
    "响应正文未就绪",
    "network listener timeout",
    "stream ended without completion",
    "connection closed",
)


class ArenaImageGenerationError(RuntimeError):
    """A terminal Arena image-generation error that must not be retried."""

    status_code = 422
    retryable = False

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class ArenaImageGenerationObservation:
    """The state used to decide whether an Arena image response is final."""

    stop_present: bool
    has_new_image: bool
    terminal_error: Optional[ArenaImageGenerationError] = None

    @property
    def is_complete(self) -> bool:
        return bool(
            not self.stop_present and (self.has_new_image or self.terminal_error)
        )


def is_arena_page_url(page_url: str) -> bool:
    """Return true only for Arena hosts, not lookalike host names."""
    try:
        hostname = str(urlsplit(str(page_url or "")).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return hostname in {"arena.ai", "lmarena.ai"} or hostname.endswith(
        (".arena.ai", ".lmarena.ai")
    )


def looks_like_image_generation_request(text: str) -> bool:
    """Recognize an image generation request without binding it to a site."""
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if any(marker in lowered for marker in _IMAGE_PROMPT_MARKERS):
        return True

    english_actions = ("generate", "create", "draw", "make", "render", "design", "produce")
    english_objects = (
        "image",
        "images",
        "picture",
        "pictures",
        "photo",
        "photos",
        "illustration",
        "artwork",
        "poster",
        "logo",
        "icon",
        "banner",
        "wallpaper",
        "portrait",
    )
    if any(action in lowered for action in english_actions) and any(
        item in lowered for item in english_objects
    ):
        return True

    chinese_actions = ("画", "绘制", "生成", "创作", "设计", "修改", "改成", "编辑")
    chinese_objects = ("图片", "图像", "照片", "插画", "海报", "logo", "图标", "头像", "封面", "壁纸")
    return any(action in lowered for action in chinese_actions) and any(
        item in lowered for item in chinese_objects
    )


def _image_modality_enabled(image_config: Optional[dict[str, Any]]) -> bool:
    config = image_config or {}
    if bool(config.get("enabled", False)):
        return True
    image_setting = (config.get("modalities") or {}).get("image")
    if isinstance(image_setting, dict):
        return bool(image_setting.get("enabled", False))
    return bool(image_setting)


def is_arena_image_generation_request(
    page_url: str,
    prompt: str,
    image_config: Optional[dict[str, Any]] = None,
    uploaded_images: Optional[Iterable[Any]] = None,
    *,
    preset_name: str = "",
) -> bool:
    """Enable the special rules only for Arena image-generation requests."""
    config = image_config or {}
    if not is_arena_page_url(page_url) or not _image_modality_enabled(config):
        return False
    explicit = config.get("_arena_image_generation_active")
    if explicit is not None:
        return bool(explicit)
    if bool(config.get("arena_image_generation", False)):
        return True
    normalized_preset = str(preset_name or "").strip().lower()
    if "图片模式" in normalized_preset or "image mode" in normalized_preset:
        return True
    try:
        page_path = str(urlsplit(str(page_url or "")).path or "").lower()
    except Exception:
        page_path = ""
    # Arena's dedicated image route makes a terse instruction unambiguous. On
    # ordinary chat routes, keep uploaded-image understanding requests outside
    # this specialized generation state machine.
    if any(uploaded_images or []) and (
        page_path == "/image" or page_path.startswith("/image/")
    ):
        return True
    return looks_like_image_generation_request(prompt)


def is_interrupted_stream_reason(reason: str) -> bool:
    normalized = str(reason or "").strip().lower()
    return bool(normalized) and any(marker in normalized for marker in _INTERRUPTED_STREAM_MARKERS)


class ArenaImageGenerationGuard:
    """Arena-specific terminal-state and interrupted-stream recovery policy."""

    DEFAULT_REFRESH_INTERVAL_SECONDS = 10.0

    def __init__(self, tab: Any, *, result_selector: str = ""):
        self.tab = tab
        self.result_selector = str(result_selector or "")

    def native_stop_present(self) -> bool:
        try:
            return bool(self.tab.ele(ARENA_NATIVE_STOP_SELECTOR, timeout=0.1))
        except Exception:
            return False

    def detect_terminal_error(self) -> Optional[ArenaImageGenerationError]:
        """Map the current Arena result/error node to a terminal failure.

        Do not scan the whole conversation: an older turn can legitimately
        contain the same error text and must not poison the current request.
        """
        script = r"""
            return ((resultSelector) => {
                const visible = (element) => {
                    if (!(element instanceof Element) || !element.isConnected) return false;
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    return rect.width > 0 && rect.height > 0
                        && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const selector = String(resultSelector || '').trim().replace(/^css:/i, '');
                let candidates = [];
                if (selector) {
                    try {
                        candidates = Array.from(document.querySelectorAll(selector)).filter(visible);
                    } catch (_) {
                        candidates = [];
                    }
                }
                if (!candidates.length) {
                    candidates = Array.from(document.querySelectorAll(
                        '[role="alert"], [role="status"]'
                    )).filter(visible);
                }
                const current = candidates.length ? [candidates[candidates.length - 1]] : [];
                const text = current.map((element) => element.innerText || element.textContent || '')
                    .join(' ').replace(/\s+/g, ' ').trim();
                const normalized = text.toLowerCase();
                if (normalized.includes('this content violates our terms of use')) {
                    return {code: 'arena_prompt_rejected', text};
                }
                if (normalized.includes('something went wrong with this response, please try again')) {
                    return {code: 'arena_image_generation_failed', text};
                }
                return null;
            })(arguments[0]);
        """
        try:
            if self.result_selector:
                result = self.tab.run_js(script, self.result_selector)
            else:
                # Preserve compatibility with lightweight tab doubles and
                # older wrappers whose run_js accepts only the script.
                result = self.tab.run_js(script)
        except Exception:
            return None
        if isinstance(result, dict):
            code = str(result.get("code") or "").strip()
            text = str(result.get("text") or "").strip()
        else:
            code = ""
            text = str(result or "").strip()
        normalized = text.lower()
        if code == ARENA_PROMPT_REJECTED_CODE or "this content violates our terms of use" in normalized:
            return ArenaImageGenerationError(
                ARENA_PROMPT_REJECTED_CODE,
                "Arena 拒绝了该图片请求：内容违反 Terms of Use",
            )
        if code == ARENA_IMAGE_GENERATION_FAILED_CODE or (
            "something went wrong with this response, please try again" in normalized
        ):
            return ArenaImageGenerationError(
                ARENA_IMAGE_GENERATION_FAILED_CODE,
                "Arena 图片生成失败：Something went wrong with this response, please try again.",
            )
        return None

    def observe(self, has_new_image: bool) -> ArenaImageGenerationObservation:
        return ArenaImageGenerationObservation(
            stop_present=self.native_stop_present(),
            has_new_image=bool(has_new_image),
            terminal_error=self.detect_terminal_error(),
        )

    @classmethod
    def refresh_interval_seconds(cls, image_config: Optional[dict[str, Any]]) -> float:
        config = image_config or {}
        value = config.get("arena_image_refresh_interval_seconds")
        # Do not inherit the old generic DOM recovery interval here. Arena image
        # recovery has a separate contract: absent an explicit Arena override,
        # its first and subsequent reloads happen every ten seconds.
        try:
            return max(1.0, float(value)) if value is not None else cls.DEFAULT_REFRESH_INTERVAL_SECONDS
        except (TypeError, ValueError):
            return cls.DEFAULT_REFRESH_INTERVAL_SECONDS

    @classmethod
    def max_refreshes(
        cls,
        image_config: Optional[dict[str, Any]],
        hard_timeout_seconds: float,
    ) -> int:
        config = image_config or {}
        value = config.get("arena_image_max_refreshes")
        if value is None:
            value = config.get("dom_interrupted_max_refreshes")
        if value is not None:
            try:
                return max(1, int(value))
            except (TypeError, ValueError):
                pass
        interval = cls.refresh_interval_seconds(config)
        return max(12, int(max(1.0, float(hard_timeout_seconds)) / interval) + 1)


def current_page_url(tab: Any) -> str:
    try:
        return str(tab.run_js("return location.href") or "").strip()
    except Exception:
        return str(getattr(tab, "url", "") or "").strip()


def read_image_bytes(tab: Any, url: str, timeout: float = 20.0) -> bytes:
    """Read an image through the browser session when direct HTTP is blocked."""
    source = str(url or "")
    if source.startswith("data:"):
        try:
            return base64.b64decode(source.split(",", 1)[1])
        except Exception:
            return b""

    if source.startswith(("http://", "https://")):
        try:
            response = requests.get(
                source,
                headers={"Referer": current_page_url(tab), "User-Agent": "Mozilla/5.0"},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.content
        except Exception:
            pass

    try:
        result = tab.run_js(
            """
            return fetch(arguments[0], { credentials: 'include' })
                .then((response) => response.arrayBuffer())
                .then((buffer) => {
                    const bytes = new Uint8Array(buffer);
                    let binary = '';
                    const step = 0x8000;
                    for (let index = 0; index < bytes.length; index += step) {
                        binary += String.fromCharCode(...bytes.subarray(index, index + step));
                    }
                    return btoa(binary);
                });
            """,
            source,
        )
        return base64.b64decode(str(result or ""))
    except Exception:
        return b""


def image_signatures(payload: bytes) -> dict[str, Any]:
    """Create byte and decoded-pixel fingerprints for equality checks."""
    data = bytes(payload or b"")
    signatures: dict[str, Any] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "pixel_sha256": None,
        "size": None,
        "dhash": None,
    }
    try:
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            signatures["size"] = image.size
            signatures["pixel_sha256"] = hashlib.sha256(image.tobytes()).hexdigest()
            grayscale = image.convert("L").resize((17, 16), Image.Resampling.LANCZOS)
            flattened_data = getattr(grayscale, "get_flattened_data", grayscale.getdata)
            pixels = list(flattened_data())
        bits = 0
        for row in range(16):
            offset = row * 17
            for column in range(16):
                bits = (bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
        signatures["dhash"] = bits
    except Exception:
        pass
    return signatures


def same_image(
    candidate: dict[str, Any],
    reference: Optional[dict[str, Any]],
    *,
    max_dhash_distance: Optional[int] = 4,
) -> bool:
    """Compare images exactly first, with optional perceptual fallback."""
    if not reference:
        return False
    if candidate.get("sha256") == reference.get("sha256"):
        return True
    if (
        candidate.get("size") == reference.get("size")
        and candidate.get("pixel_sha256")
        and candidate.get("pixel_sha256") == reference.get("pixel_sha256")
    ):
        return True
    candidate_dhash = candidate.get("dhash")
    reference_dhash = reference.get("dhash")
    return bool(
        max_dhash_distance is not None
        and isinstance(candidate_dhash, int)
        and isinstance(reference_dhash, int)
        and (candidate_dhash ^ reference_dhash).bit_count() <= max_dhash_distance
    )


def _read_local_image(path_value: Any) -> bytes:
    try:
        path = Path(str(path_value or "")).expanduser()
        return path.read_bytes() if path.is_file() else b""
    except Exception:
        return b""


def _read_uploaded_image(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    source = str(value or "")
    if source.startswith("data:"):
        try:
            return base64.b64decode(source.split(",", 1)[1])
        except Exception:
            return b""
    return _read_local_image(source)


def _media_item_image_bytes(tab: Any, item: dict[str, Any]) -> bytes:
    for key in ("local_path", "path", "file_path"):
        payload = _read_local_image(item.get(key))
        if payload:
            return payload
    for key in ("data_uri", "url", "src"):
        value = str(item.get(key) or "")
        if value:
            payload = read_image_bytes(tab, value) if tab is not None else b""
            if payload:
                return payload
    return b""


def validate_generated_images(
    uploaded_images: Iterable[Any],
    generated_media_items: Iterable[dict[str, Any]],
    *,
    tab: Any = None,
) -> None:
    """Reject Arena image output that is the uploaded reference image itself."""
    references = [
        image_signatures(payload)
        for payload in (_read_uploaded_image(path) for path in uploaded_images or [])
        if payload
    ]
    if not references:
        return

    for item in generated_media_items or []:
        if not isinstance(item, dict):
            continue
        media_type = str(item.get("media_type") or item.get("type") or "image").lower()
        if media_type not in {"image", "image_url", "input_image"}:
            continue
        payload = _media_item_image_bytes(tab, item)
        if not payload:
            continue
        candidate = image_signatures(payload)
        # The API contract is strict equality. Pixel equality handles a server
        # re-encode; dHash is intentionally disabled here to avoid rejecting a
        # legitimate edit that merely looks similar to its reference.
        if any(same_image(candidate, reference, max_dhash_distance=None) for reference in references):
            raise ArenaImageGenerationError(
                ARENA_IMAGE_UNCHANGED_CODE,
                "Arena 图片生成失败：生成结果与上传图片一致",
            )


__all__ = [
    "ARENA_IMAGE_GENERATION_FAILED_CODE",
    "ARENA_IMAGE_UNCHANGED_CODE",
    "ARENA_NATIVE_STOP_SELECTOR",
    "ARENA_NON_RETRYABLE_CODES",
    "ARENA_PROMPT_REJECTED_CODE",
    "ArenaImageGenerationError",
    "ArenaImageGenerationGuard",
    "ArenaImageGenerationObservation",
    "current_page_url",
    "image_signatures",
    "is_arena_image_generation_request",
    "is_arena_page_url",
    "is_interrupted_stream_reason",
    "looks_like_image_generation_request",
    "read_image_bytes",
    "same_image",
    "validate_generated_images",
]
