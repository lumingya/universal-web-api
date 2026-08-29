"""Arena-only guards for browser image generation.

This module deliberately owns Arena's DOM error handling and image-to-image
validation.  Callers outside Arena should never enable these rules.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from app.core.config import logger
from app.utils.image_validation import (
    extract_media_item_bytes as _common_extract_media_item_bytes,
    get_current_page_url as current_page_url,
    image_signatures,
    read_image_bytes,
    read_local_image_bytes,
    read_uploaded_image_bytes as _common_read_uploaded_image_bytes,
    same_image,
    validate_generated_images as _common_validate_generated_images,
)


ARENA_NATIVE_STOP_SELECTOR = 'css:button[aria-label="Stop generation"]'
ARENA_PROMPT_REJECTED_CODE = "arena_prompt_rejected"
ARENA_IMAGE_GENERATION_FAILED_CODE = "arena_image_generation_failed"
ARENA_IMAGE_UNCHANGED_CODE = "arena_image_unchanged"
ARENA_RESULT_BASELINE_PROPERTY = "__universalProxyArenaResultBaseline"
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
    has_stop: Optional[bool] = None
    has_generating_text: bool = False
    has_spin_canvas: bool = False
    still_generating: Optional[bool] = None

    def __post_init__(self):
        if self.has_stop is None:
            object.__setattr__(self, "has_stop", bool(self.stop_present))
        if self.still_generating is None:
            object.__setattr__(
                self,
                "still_generating",
                bool(self.stop_present or (self.has_generating_text and self.has_spin_canvas)),
            )

    @property
    def is_complete(self) -> bool:
        if self.terminal_error is not None:
            return True
        return bool(not self.still_generating and self.has_new_image)


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
    """Enable Arena image-generation rules only for marked templates."""
    config = image_config or {}
    if not is_arena_page_url(page_url) or not _image_modality_enabled(config):
        return False
    marker = config.get("_arena_image_generation_active")
    if marker is None:
        marker = config.get("arena_image_generation", False)
    return bool(marker)


def is_interrupted_stream_reason(reason: str) -> bool:
    normalized = str(reason or "").strip().lower()
    return bool(normalized) and any(marker in normalized for marker in _INTERRUPTED_STREAM_MARKERS)


def auto_skip_arena_direct_comparison(tab: Any) -> bool:
    """Detect and click the Skip button in Arena Direct mode's occasional image comparison prompt with debouncing."""
    script = r"""
        try {
            const evalBars = Array.from(document.querySelectorAll('div')).filter(d => {
                const text = (d.innerText || '');
                return (text.includes('继续使用 A') || text.includes('Continue with A') || text.includes('Use A'))
                    && (text.includes('跳过') || text.includes('Skip'));
            });
            const searchRoots = evalBars.length > 0 ? evalBars : [document.body];
            for (const root of searchRoots) {
                const buttons = Array.from(root.querySelectorAll('button:not([data-skip-triggered="true"])'));
                const skipBtn = buttons.find(b => {
                    const text = (b.innerText || '').trim();
                    const hasSkipSvg = !!b.querySelector('svg path[d*="M18 7V17"]') || !!b.querySelector('svg path[d*="M18 6V18"]');
                    const isVisible = b.offsetWidth > 0 && b.offsetHeight > 0;
                    return isVisible && (text === '跳过' || text === 'Skip' || hasSkipSvg);
                });
                if (skipBtn) {
                    skipBtn.setAttribute('data-skip-triggered', 'true');
                    skipBtn.click();
                    return true;
                }
            }
        } catch (_) {}
        return false;
    """
    try:
        return bool(tab.run_js(script))
    except Exception:
        return False


def detect_arena_render_crash(tab: Any) -> bool:
    """Detect unstyled / FOUC layout crash where Tailwind styles failed to load."""
    script = r"""
        try {
            const ol = document.querySelector('ol');
            if (ol) {
                const display = window.getComputedStyle(ol)?.display;
                if (display === 'block') return true;
            }
            if (document.styleSheets.length === 0) return true;
        } catch (_) {}
        return false;
    """
    try:
        return bool(tab.run_js(script))
    except Exception:
        return False


def evaluate_arena_direct_generation_state(
    tab: Any,
    baseline_depth: int = 0,
    current_prompt: str = "",
    stop_selector: str = ARENA_NATIVE_STOP_SELECTOR,
) -> Optional[dict[str, Any]]:
    """Execute node-scoped five-state generation evaluation for Arena Direct image requests."""
    script = r"""
        return ((baselineDepth, currentPrompt, stopSelector) => {
            const result = {
                status: 'IDLE',
                still_generating: false,
                is_complete: false,
                image_urls: [],
                error_code: null,
                error_msg: '',
                skipped_comparison: false
            };

            // 0. Auto-skip occasional comparison bar if present (scoped & debounced)
            try {
                const evalBars = Array.from(document.querySelectorAll('div')).filter(d => {
                    const text = (d.innerText || '');
                    return (text.includes('继续使用 A') || text.includes('Continue with A') || text.includes('Use A'))
                        && (text.includes('跳过') || text.includes('Skip'));
                });
                const searchRoots = evalBars.length > 0 ? evalBars : [document.body];
                for (const root of searchRoots) {
                    const buttons = Array.from(root.querySelectorAll('button:not([data-skip-triggered="true"])'));
                    const skipBtn = buttons.find(b => {
                        const text = (b.innerText || '').trim();
                        const hasSkipSvg = !!b.querySelector('svg path[d*="M18 7V17"]') || !!b.querySelector('svg path[d*="M18 6V18"]');
                        return b.offsetWidth > 0 && (text === '跳过' || text === 'Skip' || hasSkipSvg);
                    });
                    if (skipBtn) {
                        skipBtn.setAttribute('data-skip-triggered', 'true');
                        skipBtn.click();
                        result.skipped_comparison = true;
                        break;
                    }
                }
            } catch (_) {}

            const ol = document.querySelector('ol.flex-col-reverse') || document.querySelector('ol');
            const textarea = document.querySelector('textarea, [contenteditable="true"]');
            if (!ol) {
                result.status = 'WAITING_HYDRATION';
                result.still_generating = true;
                return result;
            }

            const rawChildren = Array.from(ol.children);
            const validChildren = rawChildren.filter(c => c.tagName === 'DIV' && !c.classList.contains('h-0'));

            if (validChildren.length === 0) {
                result.status = 'WAITING_HYDRATION';
                result.still_generating = true;
                return result;
            }

            // 1. Blocker Fix: Assistant-Only Depth Guard
            const assistantNodes = validChildren.filter(c => !c.classList.contains('justify-end') && !c.querySelector('[data-role="user"]'));
            if (baselineDepth > 0 && assistantNodes.length < baselineDepth + 1) {
                result.status = 'WAITING_HYDRATION';
                result.still_generating = true;
                return result;
            }

            // 2. Blocker Fix: Physical Top Child Isolation
            // In flex-col-reverse, index 0 is visually at the very bottom.
            // If index 0 is a User node, the new Assistant node has NOT mounted yet!
            const firstChild = validChildren[0];
            const isFirstChildUser = firstChild.classList.contains('justify-end') || !!firstChild.querySelector('[data-role="user"]');
            if (isFirstChildUser) {
                result.status = 'WAITING_HYDRATION';
                result.still_generating = true;
                return result;
            }
            const latestAssistant = firstChild;

            // 3. Prompt Echo Guard on the latest User node.  Do not return
            // early: active generation and a newly rendered image are stronger
            // evidence than a UI-normalized prompt prefix mismatch.
            let promptEchoMismatch = false;
            if (currentPrompt && String(currentPrompt).trim()) {
                const latestUser = validChildren.find(c => c.classList.contains('justify-end') || !!c.querySelector('[data-role="user"]'));
                if (latestUser) {
                    const userText = (latestUser.innerText || '').replace(/\s+/g, ' ').trim();
                    const promptSnippet = String(currentPrompt).trim().slice(0, 15);
                    if (promptSnippet && !userText.includes(promptSnippet)) {
                        promptEchoMismatch = true;
                    }
                }
            }

            // 4. Stop button & active generating status
            let hasStop = false;
            try {
                if (window.__arenaHardStop && typeof window.__arenaHardStop.status === 'function') {
                    const st = window.__arenaHardStop.status();
                    if (st && (st.hasNativeStopButton || st.hasOverlayStopButton)) hasStop = true;
                }
            } catch (_) {}

            if (!hasStop && stopSelector) {
                try {
                    const sel = String(stopSelector).trim().replace(/^css:/i, '');
                    const stopEl = document.querySelector(sel);
                    if (stopEl && stopEl.offsetWidth > 0 && stopEl.offsetHeight > 0) hasStop = true;
                } catch (_) {}
            }

            if (!hasStop) {
                const stopOverlays = ['button[aria-label="Stop generation"]', '[data-arena-hard-stop-overlay="true"]'];
                for (const s of stopOverlays) {
                    const el = document.querySelector(s);
                    if (el && el.offsetWidth > 0 && el.offsetHeight > 0) {
                        hasStop = true;
                        break;
                    }
                }
            }

            const hasSpinner = !!latestAssistant.querySelector('.lucide-loader.animate-spin, .animate-spin, [class*="animate-spin"]');
            const isTextareaBusy = textarea ? (textarea.disabled || textarea.getAttribute('aria-busy') === 'true') : false;

            // Priority 1: Still generating
            if (hasStop || hasSpinner || isTextareaBusy) {
                result.status = 'GENERATING';
                result.still_generating = true;
                return result;
            }

            // Priority 2: Error detection inside latest Assistant container
            const assistantText = (latestAssistant.innerText || '').toLowerCase();
            if (assistantText.includes('something went wrong with this response, please try again') ||
                assistantText.includes('an error occurred while generating')) {
                result.status = 'ERROR';
                result.error_code = 'arena_image_generation_failed';
                result.error_msg = 'Arena 图片生成失败：Something went wrong with this response, please try again.';
                result.is_complete = true;
                return result;
            }

            // Priority 3: Safety / Terms of Use violation inside latest Assistant container
            if (assistantText.includes('this content violates our terms of use')) {
                result.status = 'SAFETY_VIOLATION';
                result.error_code = 'arena_prompt_rejected';
                result.error_msg = 'Arena 拒绝了该图片请求：内容违反 Terms of Use';
                result.is_complete = true;
                return result;
            }

            // Priority 4: Success - image URLs resolution inside latest Assistant container
            const imgs = Array.from(latestAssistant.querySelectorAll('img')).filter(img => {
                const src = String(img.src || img.currentSrc || '').trim();
                const isAvatar = img.closest('[class*="avatar"], .size-6, .rounded-full') || (img.naturalWidth > 0 && img.naturalWidth <= 96);
                return src && !isAvatar && (src.includes('r2.cloudflarestorage.com') || src.startsWith('blob:') || src.startsWith('http'));
            });

            if (imgs.length > 0) {
                result.status = 'SUCCESS';
                result.is_complete = true;
                result.still_generating = false;
                result.image_urls = imgs.map(img => img.src || img.currentSrc);
                return result;
            }

            if (promptEchoMismatch) {
                result.status = 'WAITING_HYDRATION';
                result.still_generating = true;
                return result;
            }

            // Priority 5: Empty abort fallback (Stop button gone, action bar mounted, but no image or error)
            const hasActionBar = !!latestAssistant.querySelector('button[aria-label*="Like" i], button[aria-label*="Dislike" i]') ||
                                 Array.from(latestAssistant.querySelectorAll('button')).some(b => b.innerText.includes('Edit'));
            if (hasActionBar) {
                result.status = 'EMPTY_ABORT';
                result.error_code = 'arena_image_empty_response';
                result.error_msg = 'Arena 图片生成结束但未产出有效图片或错误';
                result.is_complete = true;
                return result;
            }

            result.status = 'GENERATING';
            result.still_generating = true;
            return result;
        })(arguments[0], arguments[1], arguments[2]);
    """
    try:
        data = tab.run_js(script, baseline_depth, current_prompt, stop_selector)
        if isinstance(data, dict) and data.get("status"):
            return data
    except Exception as exc:
        logger.debug(f"evaluate_arena_direct_generation_state 异常: {exc}")
    return None


def get_arena_generation_status(
    tab: Any,
    selector: str = ARENA_NATIVE_STOP_SELECTOR,
) -> dict[str, bool]:
    """Return structured multi-modal generation indicators and fault-tolerant generation state."""
    script = r"""
        const selector = String(arguments[0] || '').trim().replace(/^css:/i, '');
        const visible = (element) => {
            if (!(element instanceof Element) || !element.isConnected) return false;
            if (element.disabled || element.getAttribute('aria-disabled') === 'true' || element.getAttribute('data-disabled') === 'true') {
                return false;
            }
            if (element.hidden || element.closest('[hidden], [inert], [aria-hidden="true"]')) {
                return false;
            }
            if (element.closest('[data-message-author="user"], [data-role="user"]')) {
                return false;
            }
            const style = getComputedStyle(element);
            if (style.display === 'none' || style.visibility === 'hidden'
                || style.visibility === 'collapse' || Number(style.opacity) === 0) {
                return false;
            }
            const rect = element.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        };

        // 1. Indicator 1: Stop button & __arenaHardStop
        let has_stop = false;
        try {
            if (window.__arenaHardStop && typeof window.__arenaHardStop.status === 'function') {
                const st = window.__arenaHardStop.status();
                if (st) {
                    if (st.hasNativeStopButton || st.hasOverlayStopButton) has_stop = true;
                    else if (Array.isArray(st.active) && st.active.some(record => record && !record.done && record.ageMs < 180000)) has_stop = true;
                }
            }
        } catch (_) {}

        if (!has_stop && selector) {
            try {
                if (Array.from(document.querySelectorAll(selector)).some(visible)) {
                    has_stop = true;
                }
            } catch (_) {}
        }

        if (!has_stop) {
            try {
                const overlaySelectors = [
                    'button[aria-label="Stop generation"]',
                    '[data-arena-hard-stop-overlay="true"]',
                    'button[aria-label="Hard stop Arena stream"]',
                ];
                for (const sel of overlaySelectors) {
                    if (Array.from(document.querySelectorAll(sel)).some(visible)) {
                        has_stop = true;
                        break;
                    }
                }
            } catch (_) {}
        }

        // 2. Indicator 2: "Generating image..." text placeholder
        let has_generating_text = false;
        try {
            const textPlaceholders = [
                'generating image',
                'generating images',
                'creating image',
                'creating your image',
                'image is being generated',
                'images are being generated',
                '正在生成图片',
                '正在生成图像',
                '正在创建您的图片',
                '图片正在生成',
                '图像正在生成',
            ];
            const textCandidates = Array.from(document.querySelectorAll(
                '.text-shimmer, [class*="text-shimmer"], [class*="shimmer"], [data-testid*="generating"], div[role="status"]'
            ));
            for (const el of textCandidates) {
                if (el.closest('[data-message-author="user"], [data-role="user"]')) {
                    continue;
                }
                const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
                if (text && text.length <= 160) {
                    if (textPlaceholders.some(p => text.includes(p))) {
                        if (visible(el)) {
                            has_generating_text = true;
                            break;
                        }
                    }
                }
            }
        } catch (_) {}

        // 3. Indicator 3: animate-spin canvas loading animation
        let has_spin_canvas = false;
        try {
            const spinCanvases = Array.from(document.querySelectorAll(
                '.animate-spin canvas, [class*="animate-spin"] canvas, canvas.animate-spin, canvas[class*="animate-spin"]'
            ));
            for (const el of spinCanvases) {
                if (visible(el)) {
                    has_spin_canvas = true;
                    break;
                }
            }
        } catch (_) {}

        const still_generating = Boolean(has_stop || (has_generating_text && has_spin_canvas));

        return {
            has_stop: Boolean(has_stop),
            has_generating_text: Boolean(has_generating_text),
            has_spin_canvas: Boolean(has_spin_canvas),
            still_generating: Boolean(still_generating),
        };
    """
    try:
        result = tab.run_js(script, selector)
    except Exception:
        return {
            "has_stop": False,
            "has_generating_text": False,
            "has_spin_canvas": False,
            "still_generating": False,
        }

    if isinstance(result, dict):
        has_stop = bool(result.get("has_stop", False))
        has_generating_text = bool(result.get("has_generating_text", False))
        has_spin_canvas = bool(result.get("has_spin_canvas", False))
        still_generating = bool(
            result.get(
                "still_generating",
                has_stop or (has_generating_text and has_spin_canvas),
            )
        )
        return {
            "has_stop": has_stop,
            "has_generating_text": has_generating_text,
            "has_spin_canvas": has_spin_canvas,
            "still_generating": still_generating,
        }
    elif isinstance(result, bool):
        return {
            "has_stop": bool(result),
            "has_generating_text": False,
            "has_spin_canvas": False,
            "still_generating": bool(result),
        }
    return {
        "has_stop": False,
        "has_generating_text": False,
        "has_spin_canvas": False,
        "still_generating": False,
    }


def is_visible_arena_stop(tab: Any, selector: str = ARENA_NATIVE_STOP_SELECTOR) -> bool:
    """Return whether the Arena stop button / overlay is visible."""
    status = get_arena_generation_status(tab, selector)
    return bool(status.get("has_stop", False))


def capture_arena_result_baseline(
    tab: Any,
    result_selector: str,
) -> Optional[dict[str, Any]]:
    """Mark response nodes that existed immediately before request submission."""
    token = uuid.uuid4().hex
    script = r"""
        return ((resultSelector, baselineToken, baselineProperty) => {
            const selector = String(resultSelector || '').trim().replace(/^css:/i, '');
            const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
            let candidates = [];
            if (selector) {
                try {
                    candidates = Array.from(document.querySelectorAll(selector));
                } catch (_) {
                    candidates = [];
                }
            }
            if (!candidates.length) {
                candidates = Array.from(document.querySelectorAll(
                    '[role="alert"], [role="status"]'
                ));
            }
            let markedCount = 0;
            for (const element of candidates) {
                const marker = {
                    token: String(baselineToken || ''),
                    text: normalize(element.innerText || element.textContent || ''),
                };
                try {
                    Object.defineProperty(element, baselineProperty, {
                        configurable: true,
                        writable: true,
                        value: marker,
                    });
                    markedCount += 1;
                } catch (_) {
                    try {
                        element[baselineProperty] = marker;
                        markedCount += 1;
                    } catch (_) {}
                }
            }
            return {ok: true, node_count: candidates.length, marked_count: markedCount};
        })(arguments[0], arguments[1], arguments[2]);
    """
    try:
        result = tab.run_js(
            script,
            result_selector,
            token,
            ARENA_RESULT_BASELINE_PROPERTY,
        ) or {}
    except Exception:
        return None
    if not isinstance(result, dict) or not bool(result.get("ok")):
        return None
    return {
        "token": token,
        "property": ARENA_RESULT_BASELINE_PROPERTY,
        "node_count": int(result.get("node_count") or 0),
        "marked_count": int(result.get("marked_count") or 0),
    }


class ArenaImageGenerationGuard:
    """Arena-specific terminal-state and interrupted-stream recovery policy."""

    DEFAULT_REFRESH_INTERVAL_SECONDS = 20.0
    DEFAULT_MAX_REFRESHES = 16

    def __init__(
        self,
        tab: Any,
        *,
        result_selector: str = "",
        stop_selector: str = ARENA_NATIVE_STOP_SELECTOR,
        baseline_token: str = "",
        baseline_property: str = "",
    ):
        self.tab = tab
        self.result_selector = str(result_selector or "")
        self.stop_selector = str(stop_selector or ARENA_NATIVE_STOP_SELECTOR)
        self.baseline_token = str(baseline_token or "")
        self.baseline_property = str(baseline_property or "")

    def generation_status(self) -> dict[str, bool]:
        return get_arena_generation_status(
            self.tab,
            self.stop_selector,
        )

    def native_stop_present(self) -> bool:
        return bool(self.generation_status().get("has_stop", False))

    def detect_terminal_error(self) -> Optional[ArenaImageGenerationError]:
        """Map the current Arena result/error node to a terminal failure.

        Do not scan the whole conversation: an older turn can legitimately
        contain the same error text and must not poison the current request.
        """
        script = r"""
            return ((resultSelector, baselineToken, baselineProperty) => {
                const normalize = (value) => String(value || '')
                    .replace(/\s+/g, ' ').trim();
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
                if (baselineToken && baselineProperty) {
                    candidates = candidates.filter((element) => {
                        const marker = element[baselineProperty];
                        if (!marker || marker.token !== baselineToken) return true;
                        return normalize(element.innerText || element.textContent || '')
                            !== normalize(marker.text);
                    });
                }
                // Arena renders its conversation with flex-col-reverse, so the
                // newest response is first in DOM order. Select by visual
                // position instead: the current response is closest to the
                // composer at the bottom of the conversation.
                const current = candidates.reduce((lowest, element) => {
                    if (!lowest) return element;
                    return element.getBoundingClientRect().bottom
                        > lowest.getBoundingClientRect().bottom
                        ? element : lowest;
                }, null);
                const text = current
                    ? (current.innerText || current.textContent || '')
                        .replace(/\s+/g, ' ').trim()
                    : '';
                const normalized = text.toLowerCase();
                if (normalized.includes('this content violates our terms of use')) {
                    return {code: 'arena_prompt_rejected', text};
                }
                if (normalized.includes('something went wrong with this response, please try again')) {
                    return {code: 'arena_image_generation_failed', text};
                }
                return null;
            })(arguments[0], arguments[1], arguments[2]);
        """
        try:
            if self.result_selector or self.baseline_token or self.baseline_property:
                result = self.tab.run_js(
                    script,
                    self.result_selector,
                    self.baseline_token,
                    self.baseline_property,
                )
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
        status = self.generation_status()
        obs = ArenaImageGenerationObservation(
            stop_present=bool(status.get("has_stop", False)),
            has_new_image=bool(has_new_image),
            terminal_error=self.detect_terminal_error(),
            has_stop=bool(status.get("has_stop", False)),
            has_generating_text=bool(status.get("has_generating_text", False)),
            has_spin_canvas=bool(status.get("has_spin_canvas", False)),
            still_generating=bool(status.get("still_generating", False)),
        )
        current_state = (
            obs.still_generating,
            obs.has_new_image,
            obs.is_complete,
            bool(obs.terminal_error),
        )
        if getattr(self, "_last_logged_state", None) != current_state:
            self._last_logged_state = current_state
            logger.debug(
                f"[Arena Guard] 生图状态变化: still_generating={obs.still_generating}, "
                f"has_new_image={obs.has_new_image}, complete={obs.is_complete}, "
                f"stop={obs.has_stop}, text={obs.has_generating_text}, spin={obs.has_spin_canvas}, "
                f"terminal_error={bool(obs.terminal_error)}"
            )
        return obs

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
        return min(
            cls.DEFAULT_MAX_REFRESHES,
            max(12, int(max(1.0, float(hard_timeout_seconds)) / interval) + 1),
        )


_read_local_image = read_local_image_bytes


def _read_uploaded_image(value: Any) -> bytes:
    return _common_read_uploaded_image_bytes(value, local_reader=_read_local_image)


def _media_item_image_bytes(tab: Any, item: dict[str, Any]) -> bytes:
    return _common_extract_media_item_bytes(tab, item, local_reader=_read_local_image)


def validate_generated_images(
    uploaded_images: Iterable[Any],
    generated_media_items: Iterable[dict[str, Any]],
    *,
    tab: Any = None,
) -> None:
    """Reject Arena image output that is the uploaded reference image itself."""
    _common_validate_generated_images(
        uploaded_images,
        generated_media_items,
        tab=tab,
        max_dhash_distance=None,
        local_reader=_read_local_image,
        error_builder=lambda msg: ArenaImageGenerationError(
            ARENA_IMAGE_UNCHANGED_CODE,
            f"Arena 图片生成失败：{msg}",
        ),
    )


from app.services.arena_image_stream_observer import ArenaImageStreamObserver


__all__ = [
    "ARENA_IMAGE_GENERATION_FAILED_CODE",
    "ARENA_IMAGE_UNCHANGED_CODE",
    "ARENA_NATIVE_STOP_SELECTOR",
    "ARENA_NON_RETRYABLE_CODES",
    "ARENA_PROMPT_REJECTED_CODE",
    "ARENA_RESULT_BASELINE_PROPERTY",
    "ArenaImageGenerationError",
    "ArenaImageGenerationGuard",
    "ArenaImageGenerationObservation",
    "ArenaImageStreamObserver",
    "auto_skip_arena_direct_comparison",
    "detect_arena_render_crash",
    "evaluate_arena_direct_generation_state",
    "current_page_url",
    "capture_arena_result_baseline",
    "get_arena_generation_status",
    "image_signatures",
    "is_arena_image_generation_request",
    "is_arena_page_url",
    "is_interrupted_stream_reason",
    "is_visible_arena_stop",
    "looks_like_image_generation_request",
    "read_image_bytes",
    "same_image",
    "validate_generated_images",
]

