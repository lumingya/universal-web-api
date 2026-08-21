"""
app/core/stream_monitor.py - 流式监听核心（v5.5 图片支持版）

v5.5 修改：
- 添加图片检测（快照中包含 image_count）
- _detect_ai_start() 支持图片出现检测
- 最终阶段自动提取图片
- 新增 _image_config 配置支持
"""

import re
import time
from typing import Generator, Optional, Callable, Tuple, Dict, List, Any
from urllib.parse import urlparse

from app.core.config import logger, BrowserConstants, SSEFormatter
from app.core.background_image_downloader import (
    background_image_downloader,
    build_image_download_request_context,
    get_image_download_partition,
    normalize_remote_image_url,
)
from app.core.elements import ElementFinder
from app.core.extractors.base import BaseExtractor
from app.core.extractors.deep_mode import DeepBrowserExtractor
from app.models.schemas import is_modality_enabled
from app.services.arena_image_generation import (
    ARENA_NATIVE_STOP_SELECTOR as ARENA_IMAGE_NATIVE_STOP_SELECTOR,
    ArenaImageGenerationError,
    ArenaImageGenerationGuard,
    is_arena_image_generation_request,
    is_arena_page_url,
    is_interrupted_stream_reason,
    is_visible_arena_stop,
)

_GEMINI_IMAGE_PLACEHOLDER_RE = re.compile(
    r"^\s*https?://(?:[\w.-]+\.)?googleusercontent\.com/image_generation_content/\d+\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_IMAGE_PENDING_STATUS_MARKERS = (
    "generating image",
    "generating images",
    "creating image",
    "creating your image",
    "image is being generated",
    "images are being generated",
    "正在生成图片",
    "正在生成图像",
    "正在创建您的图片",
    "图片正在生成",
    "图像正在生成",
)


def _is_pending_image_status_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if not normalized or len(normalized) > 160:
        return False
    return any(marker in normalized for marker in _IMAGE_PENDING_STATUS_MARKERS)


def _normalize_snapshot_image_urls(raw_urls: Any) -> List[str]:
    urls: List[str] = []
    seen = set()
    if not isinstance(raw_urls, list):
        return urls

    for raw_url in raw_urls:
        normalized = normalize_remote_image_url(raw_url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return urls


def _snapshot_image_reference_key(raw_reference: Any) -> str:
    reference = str(raw_reference or "").strip()
    if not reference:
        return ""
    if reference.lower().startswith(("blob:", "data:image/")):
        return reference

    normalized = normalize_remote_image_url(reference)
    if not normalized:
        return ""
    try:
        parsed = urlparse(normalized)
    except Exception:
        return normalized
    if parsed.scheme.lower() not in {"http", "https"}:
        return normalized

    host = str(parsed.hostname or "").strip().lower()
    query = str(parsed.query or "")
    if host.endswith(".r2.cloudflarestorage.com") or "x-amz-signature=" in query.lower():
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
    return normalized


def _snapshot_image_reference_keys(snapshot: Optional[Dict[str, Any]]) -> set[str]:
    snapshot = snapshot or {}
    raw_references: List[Any] = []
    for key in ("image_references", "page_image_references"):
        values = snapshot.get(key)
        if isinstance(values, list):
            raw_references.extend(values)
    if not raw_references:
        for key in ("image_urls", "page_image_urls"):
            values = snapshot.get(key)
            if isinstance(values, list):
                raw_references.extend(values)
    return {
        key
        for key in (_snapshot_image_reference_key(value) for value in raw_references)
        if key
    }


class StreamContext:
    """流式监控上下文（v5.5 增加图片追踪）"""

    # 修复#7：前缀一致性完整比对的降频周期（每 N 次 calculate_diff 执行一次）
    PREFIX_CHECK_INTERVAL = 5

    def __init__(self):
        self.max_seen_text = ""
        self.sent_content_length = 0
        # 修复#7：前缀一致性比对降频计数器
        self.prefix_check_counter = 0

        self.baseline_snapshot = None
        self.active_turn_started = False
        self.stable_text_count = 0
        self.last_stable_text = ""
        self.active_turn_baseline_len = 0

        # 两阶段 baseline
        self.instant_baseline = None
        self.user_baseline = None
        
        # v5.4：记录 instant 阶段最后一个节点的长度
        self.instant_last_node_len = 0
        
        # v5.5 新增：图片追踪
        self.baseline_image_count = 0
        self.baseline_image_references: set[str] = set()
        self.images_detected = False
        self.pending_image_status_seen = False

        # 状态标记
        self.content_ever_changed = False
        self.user_msg_confirmed = False

        # 输出目标锁定
        self.output_target_anchor = None
        self.output_target_count = 0
        self.pending_new_anchor = None
        self.pending_new_anchor_seen = 0
        self.network_sent_content_length = 0
        self.network_sent_offset_pending = False
        self.network_sent_offset_confirmed = False
        self.from_send_baseline = False

    def reset_for_new_target(self, preserve_network_sent_offset: bool = False):
        """切换到新目标节点时重置状态"""
        preserved_network_sent_length = (
            self.network_sent_content_length if preserve_network_sent_offset else 0
        )
        self.max_seen_text = ""
        self.sent_content_length = 0
        self.stable_text_count = 0
        self.last_stable_text = ""
        self.active_turn_baseline_len = 0
        self.content_ever_changed = False
        # 修复#7：切换目标后重置降频计数器
        self.prefix_check_counter = 0
        self.network_sent_content_length = preserved_network_sent_length
        self.network_sent_offset_pending = bool(preserved_network_sent_length > 0)
        self.network_sent_offset_confirmed = False
        # v5.5: 不重置 images_detected，保持图片检测状态

    def apply_network_sent_offset(self, sent_length: int, current_text: str = "") -> bool:
        try:
            sent_length = max(0, int(sent_length or 0))
        except Exception:
            sent_length = 0
        if sent_length <= 0:
            return False

        self.network_sent_content_length = max(self.network_sent_content_length, sent_length)
        if current_text:
            self.max_seen_text = current_text
            self.last_stable_text = current_text
        effective_start = int(self.active_turn_baseline_len or 0) + sent_length
        confirmed = len(current_text or "") >= effective_start
        self.network_sent_offset_pending = not confirmed
        self.network_sent_offset_confirmed = confirmed
        if confirmed:
            self.sent_content_length = max(self.sent_content_length, sent_length)
            self.content_ever_changed = True
        return confirmed

    def calculate_diff(self, current_text: str) -> Tuple[str, bool, Optional[str]]:
        """v5 增强版 diff：支持前缀校验"""
        if not current_text:
            return "", False, None

        effective_start = self.active_turn_baseline_len + self.sent_content_length

        if self.network_sent_offset_pending and len(current_text) < effective_start:
            return "", False, None
        if self.network_sent_offset_pending:
            pending_start = self.active_turn_baseline_len + self.network_sent_content_length
            if len(current_text) < pending_start:
                return "", False, None
            self.network_sent_offset_pending = False
            self.network_sent_offset_confirmed = True
            self.sent_content_length = max(self.sent_content_length, self.network_sent_content_length)
            self.content_ever_changed = True
            effective_start = self.active_turn_baseline_len + self.sent_content_length

        # 🆕 前缀一致性检查（如果已发送过内容）
        if self.sent_content_length > 0 and len(current_text) >= effective_start:
            # 修复#7：完整前缀比对为 O(已发送长度)/轮，长回复下开销大。
            # 降频为每 PREFIX_CHECK_INTERVAL 次调用执行一次完整比对；
            # 但当 current_text 比历史快照变短（疑似回退/重写）时立即执行完整比对。
            self.prefix_check_counter += 1
            suspect_rewrite = bool(self.max_seen_text) and len(current_text) < len(self.max_seen_text)
            if suspect_rewrite or self.prefix_check_counter >= self.PREFIX_CHECK_INTERVAL:
                self.prefix_check_counter = 0
                sent_prefix_end = self.active_turn_baseline_len + self.sent_content_length

                # 获取已发送部分对应的当前文本
                current_sent_part = current_text[self.active_turn_baseline_len:sent_prefix_end]

                # 与历史记录比对
                if self.max_seen_text and len(self.max_seen_text) >= sent_prefix_end:
                    expected_sent_part = self.max_seen_text[self.active_turn_baseline_len:sent_prefix_end]

                    # 检测前缀不匹配
                    if current_sent_part != expected_sent_part:
                        # 容错：只有差异超过 5% 才认为是真实不匹配（容忍微小变化）
                        mismatch_threshold = max(10, len(expected_sent_part) * 0.05)

                        mismatch_count = sum(
                            1 for i in range(min(len(current_sent_part), len(expected_sent_part)))
                            if i < len(current_sent_part) and i < len(expected_sent_part)
                            and current_sent_part[i] != expected_sent_part[i]
                        )

                        if mismatch_count > mismatch_threshold:
                            logger.warning(
                                f"[PREFIX_MISMATCH] 检测到内容重写 "
                                f"(mismatch={mismatch_count}/{len(expected_sent_part)})"
                            )
                            return "", False, "prefix_mismatch"

        # 原有逻辑：长度增长
        if len(current_text) > effective_start:
            diff = current_text[effective_start:]
            return diff, False, None

        # 原有逻辑：内容缩短检测
        if len(current_text) >= self.active_turn_baseline_len:
            current_active_text = current_text[self.active_turn_baseline_len:]
            if not self.network_sent_offset_pending and len(current_active_text) < self.sent_content_length:
                shrink_amount = self.sent_content_length - len(current_active_text)
                if shrink_amount <= BrowserConstants.STREAM_CONTENT_SHRINK_TOLERANCE:
                    return "", False, None
                return "", False, f"内容缩短 {shrink_amount} 字符"

        # 原有逻辑：历史快照回退
        if self.max_seen_text and len(self.max_seen_text) > effective_start:
            diff = self.max_seen_text[effective_start:]
            return diff, True, "使用历史快照"

        return "", False, None

    def update_after_send(self, diff: str, current_text: str):
        self.sent_content_length += len(diff)
        self.last_stable_text = current_text
        self.stable_text_count = 0

        if len(current_text) > len(self.max_seen_text):
            self.max_seen_text = current_text

    def remember_observed_text(self, current_text: str) -> None:
        """Remember DOM progress without marking it as sent to the client."""
        if not current_text:
            return
        self.last_stable_text = current_text
        self.stable_text_count = 0
        if len(current_text) > len(self.max_seen_text):
            self.max_seen_text = current_text

    def sync_to_current_dom_text(self, current_text: str) -> int:
        active_len = max(0, len(current_text or "") - int(self.active_turn_baseline_len or 0))
        self.sent_content_length = active_len
        self.max_seen_text = current_text or ""
        self.last_stable_text = current_text or ""
        self.stable_text_count = 0
        self.content_ever_changed = True
        return active_len


class GeneratingStatusCache:
    """生成状态缓存"""

    def __init__(self, tab):
        self.tab = tab
        self._last_check_time = 0.0
        self._last_result = False
        self._check_interval = 0.5
        self._found_selector = None

    def is_generating(self) -> bool:
        now = time.time()
        if now - self._last_check_time < self._check_interval:
            return self._last_result

        self._last_check_time = now

        if self._found_selector:
            try:
                ele = self.tab.ele(self._found_selector, timeout=0.1)
                if ele and ele.states.is_displayed:
                    self._last_result = True
                    return True
            except Exception:
                pass
            self._found_selector = None

        indicator_selectors = [
            'css:button[aria-label*="Stop"]',
            'css:button[aria-label*="stop"]',
            'css:[data-state="streaming"]',
            'css:.stop-generating',
        ]

        for selector in indicator_selectors:
            try:
                ele = self.tab.ele(selector, timeout=0.05)
                if ele and ele.states.is_displayed:
                    self._found_selector = selector
                    self._last_result = True
                    return True
            except Exception:
                pass

        self._last_result = False
        return False


class StreamMonitor:
    """流式监听器（v5.5 图片支持版 + 可配置超时）"""
    
    DEFAULT_HARD_TIMEOUT = 300  # 默认硬超时（秒）
    BASELINE_POLLUTION_THRESHOLD = 20
    FINAL_SETTLE_HARDCAP = 5.0  # 收尾 settle 观察的基础硬顶（秒）
    # 修复#6b：收尾期间相邻快照持续发生变化（内容仍在生成/渲染）时，
    # 允许延长观察窗口的总上限（秒），避免生成中被 5s 硬顶提前截断。
    FINAL_SETTLE_EXTENDED_HARDCAP = 15.0
    # Compatibility selector for the generic text-stream recovery tests. Arena
    # image generation itself uses the exact selector owned by its guard.
    ARENA_NATIVE_STOP_SELECTOR = (
        ARENA_IMAGE_NATIVE_STOP_SELECTOR
        + ':not([data-arena-hard-stop-overlay="true"])'
    )

    def __init__(self, tab, finder: ElementFinder, formatter: SSEFormatter,
                 stop_checker: Optional[Callable[[], bool]] = None,
                 extractor: Optional[BaseExtractor] = None,
                 image_config: Optional[Dict] = None,
                 stream_config: Optional[Dict] = None):  # 🆕 新增流式配置
        self.tab = tab
        self.finder = finder
        self.formatter = formatter
        self._should_stop = stop_checker or (lambda: False)
        self.extractor = extractor if extractor is not None else DeepBrowserExtractor()
        
        # 图片配置
        self._image_config = image_config or {}
        self._image_extraction_enabled = self._image_config.get("enabled", False)
        
        # 🆕 流式配置（支持站点级覆盖）
        self._stream_config = stream_config or {}
        self._hard_timeout = self._stream_config.get(
            "hard_timeout", 
            self.DEFAULT_HARD_TIMEOUT
        )

        self._stream_ctx: Optional[StreamContext] = None
        self._final_complete_text = ""
        self._final_images: List[Dict] = []
        self._final_image_urls: List[str] = []
        self._generating_checker: Optional[GeneratingStatusCache] = None
        self._expect_image_output = False
        self._prefetched_image_urls: set[str] = set()
        self._last_visual_reply_log_info = None
        self._pending_send_baseline: Optional[Dict[str, Any]] = None
        self._network_fallback_reason = ""
        self._recovery_mode = ""
        self._image_recovery_exhausted = False
        self._stream_recovery_exhausted = False
        self._stream_recovery_refresh_done = False
        self._stream_recovery_refresh_attempts = 0
        self._arena_image_guard: Optional[ArenaImageGenerationGuard] = None

    def _looks_like_expected_image_output(self, user_input: str = "") -> bool:
        marker = self._image_config.get("_arena_image_generation_active")
        if marker is None:
            marker = self._image_config.get("arena_image_generation", False)
        return bool(marker and self._image_extraction_enabled)

    def _should_probe_dom_images(self) -> bool:
        modalities = self._image_config.get("modalities") or {}
        return bool(
            self._image_extraction_enabled
            and is_modality_enabled(modalities, "image")
        )

    def _classify_image_wait_state(
        self,
        ctx: StreamContext,
        current_text: str,
        *,
        has_output: bool,
        current_has_new_image: bool,
    ) -> Tuple[bool, bool]:
        image_mode_enabled = bool(self._expect_image_output)
        pending_image_status = (
            image_mode_enabled
            and not current_has_new_image
            and _is_pending_image_status_text(current_text)
        )
        if pending_image_status:
            ctx.pending_image_status_seen = True
        pending_image_wait = pending_image_status or (
            image_mode_enabled
            and ctx.pending_image_status_seen
            and not str(current_text or "").strip()
        )
        no_visible_progress = (
            image_mode_enabled
            and not ctx.images_detected
            and not current_has_new_image
            and (
                pending_image_wait
                or (
                    not has_output
                    and ctx.sent_content_length <= 0
                    and len(current_text or "") <= int(ctx.active_turn_baseline_len or 0) + 2
                )
            )
        )
        return no_visible_progress, pending_image_status

    def _uses_interrupted_image_recovery(self) -> bool:
        if not self._is_arena_page():
            return False
        if not self._expect_image_output:
            return False
        if (
            str(getattr(self, "_recovery_mode", "") or "").strip().lower()
            == "workflow_dom_resume"
        ):
            return True
        reason = str(self._network_fallback_reason or "").strip().lower()
        return any(
            marker in reason
            for marker in (
                "未完整结束",
                "提前关闭",
                "连接已关闭",
                "未收到完成标志",
                "缺少明确结束事件",
                "未产出有效正文",
                "响应正文未就绪",
                "网络监听超过最大时间",
                "connection closed",
                "image_generation_retry",
            )
        )

    def _uses_interrupted_stream_recovery(self) -> bool:
        """Return whether the network fallback represents a partial stream.

        This is deliberately narrower than generic timeout handling. Normal
        first-response/empty-stream timeouts must keep their existing quick DOM
        behavior; only a stream that produced data but lost its completion event
        (or was explicitly closed) gets the refresh-and-reload recovery path.
        """
        if not self._is_arena_page():
            return False
        if (
            str(getattr(self, "_recovery_mode", "") or "").strip().lower()
            == "workflow_dom_resume"
        ):
            return True
        reason = str(self._network_fallback_reason or "").strip().lower()
        if not reason:
            return False
        return is_interrupted_stream_reason(reason)

    @staticmethod
    def _interrupted_recovery_confirmed(
        interrupted: bool,
        refresh_done: bool,
        still_generating: bool,
        has_output: bool,
        post_refresh_settled: bool = True,
    ) -> bool:
        """Completion gate for a stream recovered through a page reload."""
        return bool(
            not interrupted
            or (refresh_done and not still_generating and has_output and post_refresh_settled)
        )

    @staticmethod
    def _recovery_output_ready(
        recovery_output_seen: bool,
        expect_image_output: bool,
        page_ready: bool,
    ) -> bool:
        """Require observed output only for image recovery."""
        return bool(
            (recovery_output_seen or not expect_image_output)
            and page_ready
        )

    @staticmethod
    def _text_recovery_complete_before_refresh(
        interrupted: bool,
        expect_image_output: bool,
        still_generating: bool,
        recovery_output_seen: bool,
        refresh_done: bool = False,
    ) -> bool:
        """Accept a completed text reply without a redundant recovery reload."""
        return bool(
            interrupted
            and not refresh_done
            and not expect_image_output
            and not still_generating
            and recovery_output_seen
        )

    @staticmethod
    def _remember_recovery_output(
        previously_seen: bool,
        current_text_len: int,
        active_turn_baseline_len: int,
        current_image_count: int,
        baseline_image_count: int,
        current_has_new_image: bool,
        sent_content_length: int,
        network_sent_content_length: int,
    ) -> bool:
        """Keep evidence that this fallback already observed response output.

        A reload can briefly expose an empty placeholder or collapse the old
        reply node. That transient DOM shape must not erase output observed
        before the reload, otherwise recovery can loop forever after generation
        has already stopped.
        """
        return bool(
            previously_seen
            or int(current_text_len or 0) > int(active_turn_baseline_len or 0) + 5
            or int(current_image_count or 0) > int(baseline_image_count or 0)
            or current_has_new_image
            or int(sent_content_length or 0) > 0
            or int(network_sent_content_length or 0) > 0
        )

    def _sanitize_stream_text(self, text: str) -> str:
        if not text:
            return ""

        sanitized = _GEMINI_IMAGE_PLACEHOLDER_RE.sub("", text)
        if sanitized != text:
            sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
        return sanitized

    def _get_final_target_strategy(self) -> str:
        return str(
            self._image_config.get("final_target_strategy", "container") or "container"
        ).strip().lower()

    def _get_latest_visual_column(self) -> str:
        parser_id = str(self._image_config.get("_parser_id", "") or "").strip().lower()
        if "right" in parser_id:
            return "right"
        if "left" in parser_id:
            return "left"
        parser_side = str(self._image_config.get("_parser_target_side", "") or "").strip().lower()
        if parser_side in {"left", "right"}:
            return parser_side
        value = str(self._image_config.get("latest_visual_column", "left") or "left").strip().lower()
        return value if value in {"left", "right"} else "left"

    def _select_candidate_element(self, elements, prefer_anchor: Optional[str] = None):
        if not elements:
            return None, None

        strategy = self._get_final_target_strategy()

        if prefer_anchor:
            for ele in reversed(elements):
                try:
                    anchor = self.extractor.get_anchor(ele)
                except Exception:
                    anchor = ""
                if anchor == prefer_anchor:
                    return ele, anchor

        column = self._get_latest_visual_column()

        scored = []
        for index, ele in enumerate(elements):
            try:
                score = ele.run_js(
                    """
                    const rect = this.getBoundingClientRect();
                    const ol = this.closest('main ol, ol, [role="feed"], [data-testid*="conversation"]');
                    let isReverse = false;
                    let turnIndex = 0;
                    if (ol) {
                        const style = window.getComputedStyle(ol);
                        isReverse = style.flexDirection.includes('reverse') || (ol.className && ol.className.includes('reverse'));
                        const turnEl = this.closest('main ol > div, ol > li, [data-testid*="conversation-turn"], [class*="turn-container"]');
                        const turns = Array.from(ol.children).filter(c => c.children.length > 0 || c.offsetHeight > 0);
                        turnIndex = turnEl && turns.includes(turnEl) ? turns.indexOf(turnEl) : (ol.contains(this) ? Array.from(ol.children).indexOf(this.closest('ol > *')) : 0);
                    }

                    const col = this.closest('[class*="basis-"]');
                    let side = 'single';
                    if (col && col.parentElement && col.parentElement.children.length === 2) {
                        side = col.parentElement.children[0] === col ? 'left' : 'right';
                    }

                    const sc = document.querySelector('[data-radix-scroll-area-viewport]') || document.scrollingElement || document.documentElement;
                    const scScrollTop = sc ? (sc.scrollTop || 0) : 0;
                    const scRect = sc ? sc.getBoundingClientRect() : { top: 0, left: 0 };
                    const contentTop = Math.round(rect.top - scRect.top + scScrollTop);
                    const contentBottom = Math.round(rect.bottom - scRect.top + scScrollTop);

                    return {
                        top: Number(rect && rect.top || 0) + Number(window.scrollY || 0),
                        bottom: Number(rect && rect.bottom || 0) + Number(window.scrollY || 0),
                        contentTop,
                        contentBottom,
                        left: Number(rect && rect.left || 0) + Number(window.scrollX || 0),
                        width: Number(rect && rect.width || 0),
                        height: Number(rect && rect.height || 0),
                        turnIndex,
                        isReverse,
                        side
                    };
                    """
                ) or {}
                bottom = float(score.get("contentBottom") or score.get("bottom") or 0)
                left = float(score.get("left") or 0)
                area = float(score.get("width") or 0) * float(score.get("height") or 0)
                turn_index = int(score.get("turnIndex", 0) or 0)
                is_reverse = bool(score.get("isReverse", False))
                side = str(score.get("side", "single") or "single")
            except Exception:
                bottom = 0.0
                left = 0.0
                area = 0.0
                turn_index = 0
                is_reverse = False
                side = "single"

            horizontal_score = left if column == "right" else -left
            scored.append({
                "index": index,
                "bottom": bottom,
                "horizontal_score": horizontal_score,
                "area": area,
                "left": left,
                "turn_index": turn_index,
                "is_reverse": is_reverse,
                "side": side,
                "element": ele
            })

        # 1. 严格过滤历史轮次，锁定最新消息轮次（Latest Turn Boundary）
        if len(scored) > 1:
            turn_indexes = [item["turn_index"] for item in scored if item["turn_index"] >= 0]
            if turn_indexes:
                is_reverse_layout = any(item["is_reverse"] for item in scored)
                target_turn_index = min(turn_indexes) if is_reverse_layout else max(turn_indexes)
                latest_turn_candidates = [item for item in scored if item["turn_index"] == target_turn_index]
                if latest_turn_candidates:
                    scored = latest_turn_candidates

        # 2. 在最新轮次内根据目标侧（column: left/right）匹配容器
        if column in {"left", "right"}:
            exact_side_matches = [item for item in scored if item["side"] == column]
            if exact_side_matches:
                scored = exact_side_matches
            else:
                # 若为明确的双栏对战布局，而目标侧在本轮中不存在（如单侧报错500），绝不可跨侧或跨轮次误取相反侧
                opposite_side = "left" if column == "right" else "right"
                has_opposite = any(item["side"] == opposite_side for item in scored)
                if has_opposite:
                    logger.debug(
                        f"[latest_visual_reply] 本轮最新消息中未找到目标侧({column})容器，相反侧({opposite_side})存在但不可误取"
                    )
                    return None, None
                # 若为单栏布局（side == 'single'），按水平中线分界备选
                if len(scored) > 1:
                    left_edges = [item["left"] for item in scored]
                    horizontal_span = max(left_edges) - min(left_edges)
                    if horizontal_span >= 80.0:
                        midpoint = min(left_edges) + horizontal_span / 2.0
                        side_candidates = [
                            item for item in scored
                            if (item["left"] >= midpoint if column == "right" else item["left"] <= midpoint)
                        ]
                        if side_candidates:
                            scored = side_candidates

        scored.sort(
            key=lambda item: (item["bottom"], item["horizontal_score"], item["area"], -item["index"]),
            reverse=True
        )
        best = scored[0]

        current_log_info = (best["index"], column, f"{best['bottom']:.1f}", f"{best['left']:.1f}", len(elements))
        if self._last_visual_reply_log_info != current_log_info:
            self._last_visual_reply_log_info = current_log_info
            logger.debug(
                "[latest_visual_reply] 选中视觉最新回复容器: "
                f"index={best['index']}, column={column}, bottom={best['bottom']:.1f}, left={best['left']:.1f}, total={len(elements)}"
            )
        target = best["element"]
        return target, self.extractor.get_anchor(target)

    def capture_send_baseline(self, selector: str, user_input: str = "") -> Dict[str, Any]:
        """Capture the DOM reply baseline immediately after a submit action."""
        expect_image_output = self._looks_like_expected_image_output(user_input)
        if isinstance(self._pending_send_baseline, dict):
            captured_at = float(self._pending_send_baseline.get("_captured_at") or 0.0)
            if (
                self._pending_send_baseline.get("_captured_after_send")
                and time.time() - captured_at < 30.0
                and bool(self._pending_send_baseline.get("_expected_image_output", False))
                == expect_image_output
            ):
                logger.debug(
                    "[DOM_BASELINE] 保留已捕获的发送基线，避免重试动作覆盖 "
                    f"(age={time.time() - captured_at:.1f}s)"
                )
                return dict(self._pending_send_baseline)

        self._last_visual_reply_log_info = None
        previous_expect_image_output = bool(self._expect_image_output)
        self._expect_image_output = expect_image_output
        baseline = self._get_latest_message_snapshot(selector)
        self._expect_image_output = previous_expect_image_output
        self._pending_send_baseline = dict(baseline or {})
        self._pending_send_baseline["_captured_after_send"] = True
        self._pending_send_baseline["_captured_at"] = time.time()
        self._pending_send_baseline["_expected_image_output"] = expect_image_output
        logger.debug(
            "[DOM_BASELINE] 已捕获发送后 DOM 基线: "
            f"count={int(baseline.get('groups_count', 0) or 0)}, "
            f"text_len={int(baseline.get('text_len', 0) or 0)}, "
            f"images={int(baseline.get('image_count', 0) or 0)}"
        )
        return dict(self._pending_send_baseline or {})

    def consume_send_baseline(self) -> Optional[Dict[str, Any]]:
        baseline = self._pending_send_baseline
        self._pending_send_baseline = None
        return dict(baseline) if isinstance(baseline, dict) else None

    def clear_send_baseline(self) -> None:
        self._pending_send_baseline = None

    def monitor(
        self,
        selector: str,
        user_input: str = "",
        completion_id: Optional[str] = None,
        baseline_snapshot: Optional[Dict[str, Any]] = None,
        sent_content_length: int = 0,
        fallback_reason: str = "",
        resume_image_urls: Optional[List[str]] = None,
        recovery_mode: str = "",
    ) -> Generator[str, None, None]:
        self._last_visual_reply_log_info = None
        logger.debug("流式监听启动")
        logger.debug(f"[MONITOR] selector_raw={selector!r}, image_enabled={self._image_extraction_enabled}")
        
        if completion_id is None:
            completion_id = SSEFormatter._generate_id()

        ctx = StreamContext()
        self._stream_ctx = ctx
        self._final_complete_text = ""
        self._final_images = []
        self._final_image_urls = []
        self._generating_checker = GeneratingStatusCache(self.tab)
        self._prefetched_image_urls = set(
            _normalize_snapshot_image_urls(resume_image_urls or [])
        )
        self._expect_image_output = self._looks_like_expected_image_output(user_input)
        self._network_fallback_reason = str(fallback_reason or "").strip()
        self._recovery_mode = str(recovery_mode or "").strip().lower()
        self._image_recovery_exhausted = False
        self._stream_recovery_exhausted = False
        self._stream_recovery_refresh_done = False
        self._stream_recovery_refresh_attempts = 0
        self._arena_image_guard = None
        if self._expect_image_output and is_arena_image_generation_request(
            str(getattr(self.tab, "url", "") or ""),
            user_input,
            self._image_config,
            self._image_config.get("uploaded_image_paths") or [],
        ):
            self._arena_image_guard = ArenaImageGenerationGuard(
                self.tab,
                result_selector=selector,
                baseline_token=self._image_config.get("arena_result_baseline_token", ""),
                baseline_property=self._image_config.get("arena_result_baseline_property", ""),
            )
        logger.debug(
            f"[MONITOR] expect_image_output={self._expect_image_output}, "
            f"arena_image_guard={bool(self._arena_image_guard)}, "
            f"recovery_mode={self._recovery_mode or '-'}, "
            f"user_input_len={len(str(user_input or ''))}"
        )

        # ===== 阶段 0：instant baseline =====
        if baseline_snapshot is None:
            baseline_snapshot = self.consume_send_baseline()

        if isinstance(baseline_snapshot, dict) and baseline_snapshot:
            ctx.instant_baseline = dict(baseline_snapshot)
            ctx.from_send_baseline = bool(ctx.instant_baseline.get("_captured_after_send"))
            logger.debug(
                "[DOM_BASELINE] 使用预捕获发送基线: "
                f"count={int(ctx.instant_baseline.get('groups_count', 0) or 0)}, "
                f"text_len={int(ctx.instant_baseline.get('text_len', 0) or 0)}, "
                f"network_sent={max(0, int(sent_content_length or 0))}"
            )
        else:
            ctx.instant_baseline = self._get_latest_message_snapshot(selector)
        request_baseline_references = self._image_config.get("request_baseline_references") or []
        if (
            self._network_fallback_reason != "image_generation_retry"
            and isinstance(request_baseline_references, list)
            and request_baseline_references
        ):
            ctx.instant_baseline["image_references"] = [
                key
                for key in (
                    _snapshot_image_reference_key(value)
                    for value in request_baseline_references
                )
                if key
            ]
            ctx.instant_baseline["page_image_references"] = []
        ctx.baseline_snapshot = ctx.instant_baseline
        ctx.instant_last_node_len = ctx.instant_baseline.get('text_len', 0)
        ctx.baseline_image_count = ctx.instant_baseline.get('image_count', 0)  # 🆕
        ctx.baseline_image_references = _snapshot_image_reference_keys(ctx.instant_baseline)
        
        logger.debug(
            f"[Instant] count={ctx.instant_baseline['groups_count']}, "
            f"last_node_len={ctx.instant_last_node_len}, "
            f"images={ctx.baseline_image_count}"  # 🆕
        )

        # ===== 阶段 1：等待用户消息上屏 =====
        user_msg_wait_start = time.time()
        user_msg_wait_max = BrowserConstants.STREAM_USER_MSG_WAIT
        ctx.user_baseline = None

        while time.time() - user_msg_wait_start < user_msg_wait_max:
            if self._should_stop():
                logger.info("等待用户消息时被取消")
                return

            current_snapshot = self._get_latest_message_snapshot(selector)
            current_count = current_snapshot['groups_count']
            current_text_len = current_snapshot.get('text_len', 0)
            current_image_count = current_snapshot.get('image_count', 0)  # 🆕
            instant_count = ctx.instant_baseline['groups_count']

            if current_count == instant_count + 1:
                if ctx.from_send_baseline:
                    logger.debug(
                        "[DOM_BASELINE] 发送基线后检测到新输出节点，直接进入 DOM 接管"
                    )
                    ctx.user_msg_confirmed = True
                    ctx.user_baseline = current_snapshot
                    ctx.active_turn_started = True
                    ctx.active_turn_baseline_len = 0
                    break

                logger.debug(f"用户消息上屏 ({instant_count} -> {current_count})")
                ctx.user_msg_confirmed = True
                ctx.user_baseline = current_snapshot
                
                pollution_delta = current_text_len - ctx.instant_last_node_len
                if pollution_delta > self.BASELINE_POLLUTION_THRESHOLD:
                    logger.debug("AI 极速回复")
                    ctx.active_turn_started = True
                    ctx.active_turn_baseline_len = ctx.instant_last_node_len
                else:
                    if pollution_delta > 0:
                        logger.info(f"[Quick Start] 检测到快速回复（{pollution_delta} 字符），立即开始监控")
                        ctx.active_turn_started = True
                        ctx.active_turn_baseline_len = ctx.instant_last_node_len
                
                break

            elif current_count >= instant_count + 2:
                logger.info(f"[Fast AI] AI 秒回 (count: {instant_count} -> {current_count})")
                ctx.user_baseline = current_snapshot
                ctx.user_msg_confirmed = True
                ctx.active_turn_started = True
                ctx.active_turn_baseline_len = 0
                break

            elif current_count == instant_count:
                # 🆕 检测图片出现
                if self._snapshot_has_new_image(ctx.instant_baseline, current_snapshot):
                    logger.info(
                        "[Image Detected] 检测到新图片 "
                        f"(count={ctx.baseline_image_count}->{current_image_count})"
                    )
                    ctx.user_baseline = current_snapshot
                    ctx.user_msg_confirmed = True
                    ctx.active_turn_started = True
                    ctx.active_turn_baseline_len = ctx.instant_last_node_len
                    ctx.images_detected = True
                    self._prefetch_snapshot_image_urls(current_snapshot)
                    break
                
                if current_text_len > ctx.instant_last_node_len + 10:
                    logger.debug("[Same Node] 同节点文本增长，可能为 AI 回复")
                    ctx.user_baseline = current_snapshot
                    ctx.user_msg_confirmed = True
                    ctx.active_turn_started = True
                    ctx.active_turn_baseline_len = ctx.instant_last_node_len
                    break

            time.sleep(0.2)

        if ctx.user_baseline is None:
            logger.debug("[Timeout] 未检测到用户消息上屏，使用 instant baseline")
            ctx.user_baseline = ctx.instant_baseline

        # ===== 阶段 2：等待 AI 开始 =====
        if not ctx.active_turn_started:
            baseline = ctx.user_baseline
            start_time = time.time()

            while True:
                if self._should_stop():
                    logger.info("等待AI开始时被取消")
                    return

                elapsed = time.time() - start_time
                current = self._get_latest_message_snapshot(selector)

                is_started, reason = self._detect_ai_start(baseline, current, ctx)  # 🆕 传入 ctx
                if is_started:
                    logger.debug(f"AI 开始回复: {reason}")
                    ctx.active_turn_started = True

                    if current['groups_count'] > baseline['groups_count']:
                        ctx.active_turn_baseline_len = 0
                    else:
                        ctx.active_turn_baseline_len = baseline.get('text_len', 0)
                    
                    break

                if elapsed > BrowserConstants.STREAM_INITIAL_WAIT:
                    logger.warning(f"[Timeout] 等待 AI 开始超时（{elapsed:.1f}s）")
                    break

                time.sleep(0.3)

        # ===== 阶段 3：增量输出 =====
        if ctx.active_turn_started:
            if sent_content_length:
                current_snapshot = self._get_latest_message_snapshot(selector)
                current_text = current_snapshot.get("text", "") or ""
                seed_len = max(0, int(sent_content_length or 0))
                if ctx.apply_network_sent_offset(seed_len, current_text):
                    logger.debug(
                        "[DOM_FALLBACK] 已同步网络偏移: "
                        f"baseline_len={ctx.active_turn_baseline_len}, "
                        f"sent={ctx.sent_content_length}, current_len={len(current_text)}"
                    )
                else:
                    logger.debug(
                        "[DOM_FALLBACK] 网络偏移暂不适用，DOM 当前文本较短: "
                        f"baseline_len={ctx.active_turn_baseline_len}, "
                        f"sent={seed_len}, current_len={len(current_text)}"
                    )
            yield from self._stream_output_phase(selector, ctx, completion_id=completion_id)
        else:
            logger.warning("[Exit] 未检测到 AI 回复，退出监控")

    def _get_latest_message_snapshot(self, selector: str) -> dict:
        """取最后一个节点快照（v5.5：包含图片检测）"""
        result = {
            'groups_count': 0, 
            'anchor': None, 
            'text': '', 
            'text_len': 0, 
            'is_generating': False,
            'image_count': 0,      # 🆕
            'has_images': False,   # 🆕
            'image_urls': [],      # 🆕
            'image_references': [],
            'page_image_urls': [],
            'page_image_references': [],
        }
        try:
            eles = self.finder.find_all(selector, timeout=0.5)
            if not eles:
                return result

            last_ele, last_anchor = self._select_candidate_element(eles)
            if last_ele is None:
                return result
            text = self.extractor.extract_text(last_ele)

            result['groups_count'] = len(eles)
            result['text'] = text or ""
            result['text_len'] = len(result['text'])
            result['anchor'] = last_anchor

            if self._should_probe_dom_images():
                try:
                    image_info = self._extract_image_info(last_ele)
                    result['image_count'] = int(image_info.get('count', 0) or 0)
                    result['has_images'] = bool(result['image_count'] > 0)
                    result['image_urls'] = list(image_info.get('urls') or [])
                    result['image_references'] = list(image_info.get('references') or [])
                except Exception as e:
                    logger.debug(f"图片计数失败: {e}")

            if self._expect_image_output:
                page_image_info = self._extract_page_image_info()
                result['page_image_urls'] = list(page_image_info.get('urls') or [])
                result['page_image_references'] = list(page_image_info.get('references') or [])

            if self._generating_checker is None:
                self._generating_checker = GeneratingStatusCache(self.tab)
            result['is_generating'] = self._generating_checker.is_generating()

        except Exception as e:
            logger.debug(f"Snapshot 异常: {e}")
        return result

    def _get_snapshot_prefer_anchor(self, selector: str, prefer_anchor: Optional[str]) -> dict:
        """按锚点锁定读取目标元素（v5.5：包含图片检测）"""
        result = {
            'groups_count': 0, 
            'anchor': None, 
            'text': '', 
            'text_len': 0, 
            'is_generating': False,
            'image_count': 0,      # 🆕
            'has_images': False,   # 🆕
            'image_urls': [],      # 🆕
            'image_references': [],
            'page_image_urls': [],
            'page_image_references': [],
        }
        try:
            eles = self.finder.find_all(selector, timeout=0.5)
            if not eles:
                return result

            result['groups_count'] = len(eles)

            target, target_anchor = self._select_candidate_element(eles, prefer_anchor)

            if target is None:
                target, target_anchor = self._select_candidate_element(eles)

            if target is None:
                return result

            last_text = self.extractor.extract_text(target)
            if (not last_text or not last_text.strip()) and len(eles) >= 2:
                logger.debug(f"[Empty Last] 目标元素为空，共 {len(eles)} 个元素")

            text = self.extractor.extract_text(target) or ""

            result['anchor'] = target_anchor
            result['text'] = text
            result['text_len'] = len(text)

            if self._should_probe_dom_images():
                try:
                    image_info = self._extract_image_info(target)
                    result['image_count'] = int(image_info.get('count', 0) or 0)
                    result['has_images'] = bool(result['image_count'] > 0)
                    result['image_urls'] = list(image_info.get('urls') or [])
                    result['image_references'] = list(image_info.get('references') or [])
                except Exception:
                    pass

            if self._expect_image_output:
                page_image_info = self._extract_page_image_info()
                result['page_image_urls'] = list(page_image_info.get('urls') or [])
                result['page_image_references'] = list(page_image_info.get('references') or [])

            if self._generating_checker is None:
                self._generating_checker = GeneratingStatusCache(self.tab)
            result['is_generating'] = self._generating_checker.is_generating()

        except Exception as e:
            logger.debug(f"Prefer-anchor Snapshot 异常: {e}")

        return result

    def _extract_image_info(self, element) -> Dict[str, Any]:
        script = """
        const baselineToken = String(arguments[0] || '');
        const baselineProperty = String(arguments[1] || '');
        const excludeExistingNodes = Boolean(arguments[2]);
        const nodes = Array.from(this.querySelectorAll('img') || []);
        const sources = new Set();
        const urls = [];
        const references = [];
        for (const img of nodes) {
            try {
                const baseline = baselineProperty ? img[baselineProperty] : null;
                if (excludeExistingNodes && baselineToken && baseline
                    && String(baseline.token || '') === baselineToken) continue;
                const src = String(img.currentSrc || img.getAttribute('src') || img.src || '').trim();
                if (!src || sources.has(src)) continue;
                if (!/^(?:https?:\\/\\/|blob:|data:image\\/)/i.test(src)) continue;
                sources.add(src);
                if (src.length <= 8192) references.push(src);
                if (/^https?:\\/\\//i.test(src)) urls.push(src);
            } catch {}
        }
        return { count: sources.size, urls, references };
        """
        image_config = getattr(self, "_image_config", {}) or {}
        info = element.run_js(
            script,
            str(image_config.get("request_baseline_token") or ""),
            str(image_config.get("request_baseline_property") or ""),
            bool(image_config.get("request_baseline_exclude_existing_nodes")),
        ) or {}
        urls = _normalize_snapshot_image_urls(info.get("urls") or [])
        references = sorted(
            {
                key
                for key in (
                    _snapshot_image_reference_key(value)
                    for value in (info.get("references") or info.get("urls") or [])
                )
                if key
            }
        )
        return {
            "count": max(int(info.get("count", 0) or 0), len(urls)),
            "urls": urls,
            "references": references,
        }

    def _extract_page_image_info(self) -> Dict[str, Any]:
        selector = str(self._image_config.get("selector") or "img").strip() or "img"
        try:
            info = self.tab.run_js(
                """
                const selector = String(arguments[0] || "img");
                const baselineToken = String(arguments[1] || "");
                const baselineProperty = String(arguments[2] || "");
                const excludeExistingNodes = Boolean(arguments[3]);
                const root = document.querySelector("main") || document;
                let nodes = [];
                try { nodes = Array.from(root.querySelectorAll(selector)); } catch {}
                const sources = new Set();
                const urls = [];
                const references = [];
                for (const node of nodes) {
                    try {
                        const baseline = baselineProperty ? node[baselineProperty] : null;
                        if (excludeExistingNodes && baselineToken && baseline
                            && String(baseline.token || "") === baselineToken) continue;
                        const src = String(node.currentSrc || node.getAttribute("src") || node.src || "").trim();
                        if (!src || sources.has(src)) continue;
                        if (!/^(?:https?:\\/\\/|blob:|data:image\\/)/i.test(src)) continue;
                        sources.add(src);
                        if (src.length <= 8192) references.push(src);
                        if (/^https?:\\/\\//i.test(src)) urls.push(src);
                    } catch {}
                }
                return {
                    urls: Array.from(new Set(urls)).slice(-256),
                    references: Array.from(new Set(references)).slice(-256),
                };
                """,
                selector,
                str(self._image_config.get("request_baseline_token") or ""),
                str(self._image_config.get("request_baseline_property") or ""),
                bool(self._image_config.get("request_baseline_exclude_existing_nodes")),
            ) or {}
        except Exception:
            return {"urls": [], "references": []}

        return {
            "urls": _normalize_snapshot_image_urls(info.get("urls") or []),
            "references": sorted(
                {
                    key
                    for key in (
                        _snapshot_image_reference_key(value)
                        for value in (info.get("references") or info.get("urls") or [])
                    )
                    if key
                }
            ),
        }

    @staticmethod
    def _snapshot_has_new_image(
        baseline: Optional[Dict[str, Any]],
        current: Optional[Dict[str, Any]],
    ) -> bool:
        current = current or {}
        baseline = baseline or {}
        current_count = max(0, int(current.get("image_count", 0) or 0))
        baseline_count = max(0, int(baseline.get("image_count", 0) or 0))
        if current_count > baseline_count:
            return True

        current_references = _snapshot_image_reference_keys(current)
        baseline_references = _snapshot_image_reference_keys(baseline)
        return bool(current_references - baseline_references)

    @staticmethod
    def _snapshot_has_new_rendered_image(
        baseline: Optional[Dict[str, Any]],
        current: Optional[Dict[str, Any]],
    ) -> bool:
        """Detect a new image in the selected reply, excluding page-only refs."""
        baseline = baseline or {}
        current = current or {}
        current_count = max(0, int(current.get("image_count", 0) or 0))
        baseline_count = max(0, int(baseline.get("image_count", 0) or 0))
        if current_count > baseline_count:
            return True

        def selected_refs(snapshot: Dict[str, Any]) -> set[str]:
            values = snapshot.get("image_references") or snapshot.get("image_urls") or []
            return {
                key
                for key in (_snapshot_image_reference_key(value) for value in values)
                if key
            }

        return bool(selected_refs(current) - selected_refs(baseline))

    def _prefetch_snapshot_image_urls(self, snap: Dict[str, Any]) -> int:
        urls = [
            url
            for url in _normalize_snapshot_image_urls(snap.get("image_urls") or [])
            if url not in self._prefetched_image_urls
        ]

        if not urls:
            return 0

        cookies_dict, headers = build_image_download_request_context(self.tab)
        started = 0
        for url in urls:
            result = background_image_downloader.start_download(
                url,
                cookies=cookies_dict,
                headers=headers,
                partition_key=get_image_download_partition(url, cookies_dict, headers),
                max_bytes=max(
                    1,
                    int(self._image_config.get("max_size_mb") or 10),
                ) * 1024 * 1024,
            )
            if result:
                self._prefetched_image_urls.add(url)
                started += 1

        if started:
            logger.debug(f"[DOM Prefetch] 已提交后台图片下载: {started} 个")
        return started

    def _refresh_stalled_image_page(self) -> bool:
        try:
            self.tab.refresh(ignore_cache=True)
            logger.info("[Image Recovery] 图片结果长时间未渲染，已刷新页面重新同步服务端结果")
            return True
        except Exception as exc:
            logger.warning(f"[Image Recovery] 图片停滞恢复刷新失败（继续等待）: {exc}")
            return False

    def _refresh_interrupted_stream_page(self) -> bool:
        if not self._is_arena_page():
            logger.debug("[Stream Recovery] 非 Arena 页面跳过断流刷新")
            return False
        try:
            self.tab.refresh(ignore_cache=True)
            logger.info(
                "[Stream Recovery] 断流页面已刷新，重新建立 DOM 监听并同步服务端结果"
            )
            return True
        except Exception as exc:
            logger.warning(f"[Stream Recovery] 断流恢复刷新失败（继续等待）: {exc}")
            return False

    @staticmethod
    def _should_hold_interrupted_image_recovery(
        interrupted: bool,
        current_image_count: int,
        baseline_image_count: int,
        has_rendered_image: bool,
    ) -> bool:
        """Keep polling when only a server-side image reference is visible."""
        if not interrupted:
            return False
        if has_rendered_image:
            return False
        return int(current_image_count or 0) <= int(baseline_image_count or 0)

    def _is_arena_page(self) -> bool:
        return is_arena_page_url(str(getattr(self.tab, "url", "") or ""))

    def _arena_native_stop_present(self) -> bool:
        """Return whether Arena's native Stop button is currently visible."""
        if not self._is_arena_page():
            return False
        return is_visible_arena_stop(self.tab, self.ARENA_NATIVE_STOP_SELECTOR)

    def _uses_arena_image_recovery_defaults(self) -> bool:
        return self._is_arena_page()

    def _get_active_turn_text(self, selector: str) -> str:
        """回退：取最后一个元素的文本"""
        try:
            eles = self.finder.find_all(selector, timeout=1)
            if not eles:
                return ""
            
            target, _ = self._select_candidate_element(eles)
            if target is None:
                return ""

            last_text = self.extractor.extract_text(target)
            if last_text and last_text.strip():
                return last_text.strip()

            return ""
        except Exception:
            return ""

    def _detect_ai_start(self, baseline: dict, current: dict, ctx: StreamContext) -> Tuple[bool, str]:
        """检测 AI 是否开始回复（v5.5：支持图片检测）"""
        
        if current['groups_count'] > baseline['groups_count']:
            return True, f"节点数增加 {current['groups_count'] - baseline['groups_count']}"
        
        if current['is_generating']:
            return True, "生成指示器激活"
        
        if current['text_len'] > baseline['text_len'] + 10:
            return True, f"文本增长 {current['text_len'] - baseline['text_len']} 字符"
        
        # 🆕 图片检测：即使没有文本增长，有图片出现也认为开始回复
        current_img = current.get('image_count', 0)
        baseline_img = baseline.get('image_count', 0)
        if self._snapshot_has_new_image(baseline, current):
            ctx.images_detected = True
            self._prefetch_snapshot_image_urls(current)
            return True, f"检测到新图片 ({baseline_img} -> {current_img})"
        
        return False, ""

    def _stream_output_phase(self, selector: str, ctx: StreamContext,
                             completion_id: Optional[str] = None) -> Generator[str, None, None]:
        """流式输出阶段（v5.5：增加图片变化检测）"""
        silence_start = time.time()
        has_output = False

        current_interval = BrowserConstants.STREAM_CHECK_INTERVAL_DEFAULT
        min_interval = BrowserConstants.STREAM_CHECK_INTERVAL_MIN
        max_interval = BrowserConstants.STREAM_CHECK_INTERVAL_MAX

        element_missing_count = 0
        max_element_missing = 10

        last_text_len = 0
        last_image_count = ctx.baseline_image_count  # 🆕
        
        phase_start = time.time()

        initial_snap = self._get_snapshot_prefer_anchor(selector, None)
        ctx.output_target_count = initial_snap['groups_count']
        ctx.output_target_anchor = initial_snap['anchor']
        last_text_len = int(initial_snap.get('text_len', 0) or 0)
        last_image_count = int(initial_snap.get('image_count', ctx.baseline_image_count) or 0)
        last_image_references = _snapshot_image_reference_keys(initial_snap)

        peak_text_len = 0
        content_shrink_count = 0
        image_stall_refresh_attempted = False
        image_recovery_refresh_attempts = 0
        image_recovery_last_refresh_at = phase_start
        interrupted_image_recovery = self._uses_interrupted_image_recovery()
        interrupted_stream_recovery = self._uses_interrupted_stream_recovery()
        arena_image_guard = getattr(self, "_arena_image_guard", None)
        strict_arena_image_generation = isinstance(
            arena_image_guard, ArenaImageGenerationGuard
        )
        stream_recovery_refresh_done = bool(
            getattr(self, "_stream_recovery_refresh_done", False)
        )
        stream_recovery_refresh_attempts = int(
            getattr(self, "_stream_recovery_refresh_attempts", 0) or 0
        )
        stream_recovery_last_refresh_at = phase_start
        stream_recovery_max_refreshes = 0
        recovery_confirmed = not interrupted_stream_recovery
        recovery_output_seen = False
        recovery_output_held_logged = False
        stream_recovery_page_ready = False
        deferred_hard_timeout_logged = False
        stream_recovery_needs_post_refresh_check = False

        while True:
            if time.time() - phase_start > self._hard_timeout:
                max_refreshes = stream_recovery_max_refreshes
                if strict_arena_image_generation and not max_refreshes:
                    max_refreshes = ArenaImageGenerationGuard.max_refreshes(
                        self._image_config,
                        self._hard_timeout,
                    )
                if (
                    strict_arena_image_generation
                    and interrupted_stream_recovery
                    and (
                        stream_recovery_refresh_attempts < max_refreshes
                        or stream_recovery_needs_post_refresh_check
                    )
                ):
                    if not deferred_hard_timeout_logged:
                        deferred_hard_timeout_logged = True
                        logger.warning(
                            "[Arena Image] 达到常规监听硬上限，但严格断流恢复仍未达到最大刷新次数，继续等待"
                        )
                elif strict_arena_image_generation and not self._should_stop():
                    raise ArenaImageGenerationError(
                        "arena_image_generation_failed",
                        "Arena 图片生成失败：在刷新上限内未检测到新的生成图片或明确错误",
                    )
                elif interrupted_stream_recovery:
                    logger.error(f"[HardTimeout] 超过最大监听时间 {self._hard_timeout}s，强制退出")
                    self._stream_recovery_exhausted = True
                    logger.error(
                        "[Stream Recovery] 断流恢复达到监听硬上限，拒绝把不完整结果报告为成功"
                    )
                    break
                else:
                    logger.error(f"[HardTimeout] 超过最大监听时间 {self._hard_timeout}s，强制退出")
                    break
            
            if self._should_stop():
                logger.info("输出阶段被取消")
                break

            try:
                if hasattr(self.tab, "states") and not self.tab.states.is_alive:
                    logger.warning("[StreamMonitor] 检测到标签页已被关闭，强行退出 DOM 轮询")
                    return
            except Exception:
                pass

            snap = self._get_snapshot_prefer_anchor(selector, ctx.output_target_anchor)

            current_count = snap['groups_count']
            current_anchor = snap['anchor']
            current_text = snap['text'] or ""
            still_generating = snap['is_generating']
            if interrupted_stream_recovery:
                if not strict_arena_image_generation:
                    still_generating = self._arena_native_stop_present()
                if stream_recovery_refresh_done:
                    baseline_groups = int(ctx.baseline_snapshot.get('groups_count') or 0) if ctx.baseline_snapshot else 0
                    baseline_anchor = ctx.baseline_snapshot.get('anchor') if ctx.baseline_snapshot else None
                    baseline_text_len = int(ctx.baseline_snapshot.get('text_len') or 0) if ctx.baseline_snapshot else 0

                    if baseline_groups > 0:
                        has_new_target = bool(
                            current_count > baseline_groups
                            or (current_anchor and current_anchor != baseline_anchor)
                            or len(current_text) > baseline_text_len + 5
                        )
                    else:
                        has_new_target = bool(current_anchor or current_text)

                    stream_recovery_page_ready = bool(
                        stream_recovery_page_ready
                        or has_new_target
                        or still_generating
                    )
            current_text_len = len(current_text)
            current_image_count = snap.get('image_count', 0)  # 🆕
            current_image_references = _snapshot_image_reference_keys(snap)
            current_has_new_image = self._snapshot_has_new_image(ctx.baseline_snapshot, snap)
            current_has_new_rendered_image = self._snapshot_has_new_rendered_image(
                ctx.baseline_snapshot, snap
            )
            arena_observation = (
                arena_image_guard.observe(current_has_new_rendered_image)
                if strict_arena_image_generation
                else None
            )
            if arena_observation is not None:
                still_generating = arena_observation.stop_present
            recovery_output_seen = self._remember_recovery_output(
                recovery_output_seen,
                current_text_len,
                ctx.active_turn_baseline_len,
                current_image_count,
                ctx.baseline_image_count,
                (
                    current_has_new_rendered_image
                    if strict_arena_image_generation
                    else current_has_new_image
                ),
                ctx.sent_content_length,
                ctx.network_sent_content_length,
            )
            no_visible_progress, pending_image_status = self._classify_image_wait_state(
                ctx,
                current_text,
                has_output=has_output,
                current_has_new_image=(
                    current_has_new_rendered_image
                    if strict_arena_image_generation
                    else current_has_new_image
                ),
            )
            # A page-level signed URL can appear before the DOM mounts the actual
            # image. During interrupted Arena recovery this is not completion:
            # keep the Arena recovery window alive until a real image renders.
            hold_unrendered_image = self._should_hold_interrupted_image_recovery(
                interrupted_image_recovery,
                current_image_count,
                ctx.baseline_image_count,
                bool(snap.get("has_images")),
            )
            if hold_unrendered_image:
                no_visible_progress = True

            recovery_has_output = recovery_output_seen
            stream_cfg = getattr(self, "_stream_config", None) or {}
            settle_cfg = stream_cfg.get("stream_recovery_post_refresh_settle_seconds")
            post_refresh_settle_sec = float(settle_cfg) if settle_cfg is not None else 6.0
            post_refresh_settled = bool(
                not stream_recovery_refresh_done
                or (time.time() - stream_recovery_last_refresh_at >= post_refresh_settle_sec)
            )
            if strict_arena_image_generation and arena_observation is not None:
                recovery_has_output = bool(
                    arena_observation.has_new_image or arena_observation.terminal_error
                )
                recovery_confirmed = bool(
                    arena_observation.is_complete
                    and (
                        not interrupted_stream_recovery
                        or (stream_recovery_refresh_done and post_refresh_settled)
                    )
                )
            else:
                recovery_confirmed = self._interrupted_recovery_confirmed(
                    interrupted_stream_recovery,
                    stream_recovery_refresh_done,
                    still_generating,
                    self._recovery_output_ready(
                        recovery_output_seen,
                        self._expect_image_output,
                        stream_recovery_page_ready,
                    ),
                    post_refresh_settled=post_refresh_settled,
                )
                recovery_confirmed = bool(
                    recovery_confirmed
                    or self._text_recovery_complete_before_refresh(
                        interrupted_stream_recovery,
                        self._expect_image_output,
                        still_generating,
                        recovery_output_seen,
                        refresh_done=stream_recovery_refresh_done,
                    )
                )
            if interrupted_stream_recovery and not recovery_confirmed:
                # A disconnected stream is never complete before a reload has
                # reattached the page to the persisted conversation state.
                no_visible_progress = True

            # 🆕 检测图片变化
            image_snapshot_changed = (
                current_image_count != last_image_count
                or current_image_references != last_image_references
            )
            image_progress_signal = (
                current_has_new_rendered_image
                if strict_arena_image_generation
                else current_has_new_image
            )
            if current_has_new_image:
                self._prefetch_snapshot_image_urls(snap)
            if (
                strict_arena_image_generation
                and current_has_new_image
                and not current_has_new_rendered_image
                and image_snapshot_changed
            ):
                logger.debug(
                    "[Arena Image] 检测到页面图片引用，但回复图片尚未渲染，继续等待刷新"
                )
            if image_progress_signal and not ctx.images_detected:
                logger.debug(
                    "[Image Change] 检测到相对发送基线的新图片: "
                    f"count={ctx.baseline_image_count}->{current_image_count}, "
                    f"new_refs={len(current_image_references - ctx.baseline_image_references)}"
                )
                ctx.images_detected = True
                ctx.content_ever_changed = True
                silence_start = time.time()  # 重置静默计时
            elif image_progress_signal and image_snapshot_changed:
                silence_start = time.time()
            last_image_count = current_image_count
            last_image_references = current_image_references

            # 检测内容折叠
            if current_text_len > peak_text_len:
                peak_text_len = current_text_len
                content_shrink_count = 0
            elif peak_text_len > 100 and current_text_len < peak_text_len * 0.5:
                content_shrink_count += 1
                if content_shrink_count >= 2:
                    logger.info(f"[Collapse] 检测到内容折叠：{peak_text_len} -> {current_text_len}")
                    if not interrupted_stream_recovery:
                        ctx.reset_for_new_target()
                    else:
                        logger.debug(
                            "[Stream Recovery] 刷新折叠期间保留已发送偏移和最后完整 DOM 快照"
                        )
                    peak_text_len = current_text_len
                    content_shrink_count = 0
                    silence_start = time.time()
                    has_output = False
                    last_text_len = current_text_len
                    time.sleep(0.2)
                    continue
            else:
                content_shrink_count = 0

            # 检测新节点出现
            if current_count > ctx.output_target_count:
                if current_anchor != ctx.output_target_anchor:
                    if ctx.pending_new_anchor == current_anchor:
                        ctx.pending_new_anchor_seen += 1
                    else:
                        ctx.pending_new_anchor = current_anchor
                        ctx.pending_new_anchor_seen = 1

                    if ctx.pending_new_anchor_seen >= 2:
                        if not interrupted_stream_recovery:
                            ctx.reset_for_new_target(preserve_network_sent_offset=True)
                        ctx.output_target_anchor = current_anchor
                        ctx.output_target_count = current_count
                        ctx.pending_new_anchor = None
                        ctx.pending_new_anchor_seen = 0
                        peak_text_len = 0
                        silence_start = time.time()
                        has_output = False
                        last_text_len = current_text_len
                        last_image_count = current_image_count
                        last_image_references = current_image_references
                        if ctx.network_sent_content_length > 0:
                            if ctx.apply_network_sent_offset(
                                ctx.network_sent_content_length,
                                current_text,
                            ):
                                has_output = True
                                logger.debug(
                                    "[DOM_FALLBACK] 新输出节点已继承网络偏移: "
                                    f"sent={ctx.sent_content_length}, current_len={current_text_len}"
                                )

                        if not current_text:
                            time.sleep(0.2)
                            continue
            else:
                ctx.pending_new_anchor = None
                ctx.pending_new_anchor_seen = 0

            # 空文本处理
            if not current_text:
                # 🆕 如果有图片，标记内容变化并继续检查退出条件
                if snap.get('has_images') or current_has_new_image or ctx.images_detected:
                    if current_has_new_image or ctx.images_detected:
                        ctx.content_ever_changed = True
                    # 不 continue，继续执行后面的退出判定逻辑
                elif no_visible_progress or (
                    interrupted_stream_recovery and recovery_confirmed
                ):
                    # 图片请求在刷新后可能短暂变成完全空白，仍需进入恢复计时与刷新逻辑。
                    pass
                else:
                    if ctx.sent_content_length > 0:
                        element_missing_count += 1
                        if element_missing_count >= max_element_missing:
                            logger.warning("元素持续丢失，退出监控")
                            break
                    time.sleep(0.2)
                    continue
            else:
                element_missing_count = 0

            diff, is_from_history, reason = ctx.calculate_diff(current_text)

            # 🆕 处理前缀不匹配（内容被重写）
            if reason == "prefix_mismatch":
                if interrupted_stream_recovery:
                    ctx.remember_observed_text(current_text)
                    silence_start = time.time()
                    continue
                logger.warning(
                    "[PREFIX_MISMATCH] DOM 内容被重写；普通 delta 无法撤回已发送前缀，"
                    "跳过整段重发以避免重复污染"
                )
                active_len = max(0, len(current_text) - int(ctx.active_turn_baseline_len or 0))
                ctx.sent_content_length = max(ctx.sent_content_length, active_len)
                ctx.max_seen_text = current_text
                ctx.last_stable_text = current_text
                ctx.stable_text_count = 0
                ctx.content_ever_changed = True
                silence_start = time.time()
                continue

            if reason and str(reason).startswith("内容缩短"):
                if interrupted_stream_recovery:
                    ctx.remember_observed_text(current_text)
                    silence_start = time.time()
                    continue
                logger.warning(
                    f"[STREAM_SHRINK] {reason}；普通 delta 无法撤回已发送尾部，"
                    "同步 DOM 快照并停止重放历史内容"
                )
                ctx.sync_to_current_dom_text(current_text)
                silence_start = time.time()
                continue

            if diff:
                if self._should_stop():
                    break
                if interrupted_stream_recovery:
                    ctx.remember_observed_text(current_text)
                else:
                    ctx.update_after_send(diff, current_text)
                recovery_output_seen = self._remember_recovery_output(
                    recovery_output_seen,
                    current_text_len,
                    ctx.active_turn_baseline_len,
                    current_image_count,
                    ctx.baseline_image_count,
                    current_has_new_image,
                    ctx.sent_content_length,
                    ctx.network_sent_content_length,
                )
                current_interval = min_interval
                visible_diff = self._sanitize_stream_text(diff)
                if interrupted_stream_recovery:
                    if visible_diff.strip() and not recovery_output_held_logged:
                        recovery_output_held_logged = True
                        logger.info(
                            "[Stream Recovery] 已冻结 DOM 增量输出，等待刷新确认生成结束后一次性补发"
                        )
                elif pending_image_status:
                    logger.debug("[STREAM] 已抑制图片生成占位文本，继续等待最终图片")
                elif visible_diff.strip():
                    silence_start = time.time()
                    has_output = True
                    ctx.content_ever_changed = True
                    yield self.formatter.pack_chunk(visible_diff, completion_id=completion_id)
                else:
                    logger.debug("[STREAM] Suppressed Gemini placeholder-only chunk")
            else:
                if current_text == ctx.last_stable_text:
                    ctx.stable_text_count += 1
                else:
                    ctx.stable_text_count = 0
                    ctx.last_stable_text = current_text
                current_interval = min(current_interval * 1.5, max_interval)

            if current_text_len != last_text_len:
                # 基线文本（如用户 prompt 回显）不算“AI 有效变化”，避免图片任务被过早收尾。
                effective_baseline_len = int(ctx.active_turn_baseline_len or 0)
                if (
                    current_text_len > effective_baseline_len + 2
                    or last_text_len > effective_baseline_len + 2
                ):
                    ctx.content_ever_changed = True
                last_text_len = current_text_len

            if interrupted_stream_recovery:
                stream_cfg = getattr(self, "_stream_config", None) or {}
                settle_cfg = stream_cfg.get("stream_recovery_post_refresh_settle_seconds")
                post_refresh_settle_sec = float(settle_cfg) if settle_cfg is not None else 6.0
                post_refresh_settled = bool(
                    not stream_recovery_refresh_done
                    or (time.time() - stream_recovery_last_refresh_at >= post_refresh_settle_sec)
                )
                if strict_arena_image_generation and arena_observation is not None:
                    recovery_confirmed = bool(
                        arena_observation.is_complete and stream_recovery_refresh_done and post_refresh_settled
                    )
                else:
                    recovery_confirmed = self._interrupted_recovery_confirmed(
                        interrupted_stream_recovery,
                        stream_recovery_refresh_done,
                        still_generating,
                        self._recovery_output_ready(
                            recovery_output_seen,
                            self._expect_image_output,
                            stream_recovery_page_ready,
                        ),
                        post_refresh_settled=post_refresh_settled,
                    )
                    recovery_confirmed = bool(
                        recovery_confirmed
                        or self._text_recovery_complete_before_refresh(
                            interrupted_stream_recovery,
                            self._expect_image_output,
                            still_generating,
                            recovery_output_seen,
                            refresh_done=stream_recovery_refresh_done,
                        )
                    )

            silence_duration = time.time() - silence_start

            # 退出判定
            silence_threshold = BrowserConstants.STREAM_SILENCE_THRESHOLD
            silence_threshold_fallback = BrowserConstants.STREAM_SILENCE_THRESHOLD_FALLBACK
            stable_count_threshold = BrowserConstants.STREAM_STABLE_COUNT_THRESHOLD
            # 修复#6a：仅当生成指示器不再活跃（not still_generating）时才启用收紧后的
            # 1.2s 快速阈值；生成中的自然停顿仍使用原阈值，避免回复被提前截断。
            if ctx.network_sent_offset_confirmed and not still_generating:
                silence_threshold = min(float(silence_threshold), 1.2)
                silence_threshold_fallback = min(float(silence_threshold_fallback), 2.0)
                stable_count_threshold = min(int(stable_count_threshold), 2)
            image_mode_enabled = bool(self._expect_image_output)
            arena_image_recovery = image_mode_enabled and self._uses_arena_image_recovery_defaults()
            image_stall_refresh_seconds = float(
                self._image_config.get("dom_image_stall_refresh_seconds")
                or (90.0 if arena_image_recovery else 0.0)
            )
            if strict_arena_image_generation:
                interrupted_refresh_interval = ArenaImageGenerationGuard.refresh_interval_seconds(
                    self._image_config
                )
            else:
                interrupted_refresh_interval = float(
                    self._image_config.get("dom_image_interrupted_refresh_interval_seconds")
                    or 15.0
                )
            interrupted_recovery_timeout = float(
                self._image_config.get("dom_image_interrupted_recovery_timeout_seconds")
                or 240.0
            )
            interrupted_max_refreshes = max(
                0,
                int(
                    self._image_config.get("dom_image_interrupted_max_refreshes")
                    or max(12, int(max(1.0, float(self._hard_timeout)) / max(1.0, interrupted_refresh_interval)) + 1)
                ),
            )
            if strict_arena_image_generation:
                stream_recovery_max_refreshes = ArenaImageGenerationGuard.max_refreshes(
                    self._image_config,
                    self._hard_timeout,
                )
            else:
                stream_recovery_max_refreshes = max(
                    1,
                    int(
                        self._image_config.get("dom_interrupted_max_refreshes")
                        or max(
                            12,
                            int(max(1.0, float(self._hard_timeout)) / max(1.0, interrupted_refresh_interval)) + 1,
                        )
                    ),
                )
            no_progress_wait_limit = float(
                self._image_config.get("dom_image_no_output_timeout_seconds")
                or max(
                    180.0 if arena_image_recovery else 45.0,
                    float(silence_threshold_fallback) * 4.0,
                )
            )
            no_progress_hard_limit = float(
                self._image_config.get("dom_image_no_output_hard_timeout_seconds")
                or (240.0 if arena_image_recovery else 0.0)
            )
            elapsed_since_phase_start = time.time() - phase_start
            if strict_arena_image_generation and arena_observation is not None and recovery_confirmed:
                if arena_observation.terminal_error is not None:
                    raise arena_observation.terminal_error
                logger.info(
                    "[Arena Image] 原生停止按钮已消失且已确认新图片，生成完成"
                )
                break
            if strict_arena_image_generation and interrupted_stream_recovery:
                # Every refresh must be followed by one fresh DOM observation.
                # In particular, do not declare the last permitted refresh
                # exhausted before its reloaded page has been inspected.
                stream_recovery_needs_post_refresh_check = False
                if stream_recovery_refresh_attempts >= stream_recovery_max_refreshes:
                    self._stream_recovery_exhausted = True
                    logger.error(
                        "[Arena Image] 已达到最大刷新次数，仍未满足严格完成条件"
                    )
                    break
            if interrupted_stream_recovery and recovery_confirmed:
                if stream_recovery_refresh_done:
                    logger.info(
                        "[Stream Recovery] 刷新后停止按钮已消失，准备一次性补发未发送内容"
                    )
                else:
                    logger.info(
                        "[Stream Recovery] 停止按钮已消失且文本输出已确认，跳过刷新并一次性补发未发送内容"
                    )
                break
            if (
                interrupted_stream_recovery
                and not recovery_confirmed
                and interrupted_refresh_interval > 0
                and stream_recovery_refresh_attempts < stream_recovery_max_refreshes
                and time.time() - stream_recovery_last_refresh_at >= interrupted_refresh_interval
            ):
                stream_recovery_refresh_attempts += 1
                stream_recovery_last_refresh_at = time.time()
                logger.info(
                    "[Stream Recovery] 断流结果尚未确认，刷新页面重新建立监听 "
                    f"(attempt={stream_recovery_refresh_attempts}/{stream_recovery_max_refreshes}, "
                    f"native_stop={still_generating}, output={recovery_has_output}, "
                    f"page_ready={stream_recovery_page_ready})"
                )
                if self._refresh_interrupted_stream_page():
                    stream_recovery_refresh_done = True
                    self._stream_recovery_refresh_done = True
                    self._stream_recovery_refresh_attempts = stream_recovery_refresh_attempts
                    stream_recovery_page_ready = False
                    stream_recovery_last_refresh_at = time.time()
                    self._generating_checker = GeneratingStatusCache(self.tab)
                    ctx.output_target_anchor = None
                    ctx.output_target_count = 0
                    ctx.pending_new_anchor = None
                    ctx.pending_new_anchor_seen = 0
                    last_text_len = 0
                    last_image_count = max(int(ctx.baseline_image_count or 0), 0)
                    last_image_references = set(ctx.baseline_image_references)
                    peak_text_len = 0
                    silence_start = time.time()
                    stream_recovery_needs_post_refresh_check = True
                    time.sleep(2.0)
                    continue
                if (
                    strict_arena_image_generation
                    and stream_recovery_refresh_attempts >= stream_recovery_max_refreshes
                ):
                    self._stream_recovery_exhausted = True
                    logger.error(
                        "[Arena Image] 刷新失败且已达到最大刷新次数，终止本轮"
                    )
                    break
            if (
                interrupted_image_recovery
                and not interrupted_stream_recovery
                and no_visible_progress
                and elapsed_since_phase_start >= interrupted_recovery_timeout
            ):
                self._image_recovery_exhausted = True
                logger.warning(
                    "[Image Recovery] 断流后的图片恢复窗口已耗尽，准备中止并重试 "
                    f"(elapsed={elapsed_since_phase_start:.1f}s, "
                    f"refreshes={image_recovery_refresh_attempts})"
                )
                break
            if (
                interrupted_image_recovery
                and not interrupted_stream_recovery
                and no_visible_progress
                and interrupted_refresh_interval > 0
                and image_recovery_refresh_attempts < interrupted_max_refreshes
                and time.time() - image_recovery_last_refresh_at >= interrupted_refresh_interval
            ):
                image_recovery_refresh_attempts += 1
                image_recovery_last_refresh_at = time.time()
                logger.info(
                    "[Image Recovery] 断流后仍未同步到图片，刷新页面继续等待 "
                    f"(attempt={image_recovery_refresh_attempts}/{interrupted_max_refreshes})"
                )
                if self._refresh_stalled_image_page():
                    self._generating_checker = GeneratingStatusCache(self.tab)
                    ctx.output_target_anchor = None
                    ctx.output_target_count = 0
                    ctx.pending_new_anchor = None
                    ctx.pending_new_anchor_seen = 0
                    last_text_len = 0
                    last_image_count = max(int(ctx.baseline_image_count or 0), 0)
                    last_image_references = set(ctx.baseline_image_references)
                    silence_start = time.time()
                    time.sleep(1.0)
                    continue
            if (
                not interrupted_image_recovery
                and no_visible_progress
                and image_stall_refresh_seconds > 0
                and not image_stall_refresh_attempted
                and elapsed_since_phase_start >= image_stall_refresh_seconds
            ):
                image_stall_refresh_attempted = True
                if pending_image_status:
                    logger.info(
                        "[Image Recovery] 图片生成占位状态持续未产出图片，准备刷新页面"
                    )
                if self._refresh_stalled_image_page():
                    self._generating_checker = GeneratingStatusCache(self.tab)
                    ctx.output_target_anchor = None
                    ctx.output_target_count = 0
                    ctx.pending_new_anchor = None
                    ctx.pending_new_anchor_seen = 0
                    last_text_len = 0
                    last_image_count = max(int(ctx.baseline_image_count or 0), 0)
                    last_image_references = set(ctx.baseline_image_references)
                    silence_start = time.time()
                    time.sleep(1.0)
                    continue
            suppress_fast_exit = False
            if no_visible_progress and elapsed_since_phase_start < no_progress_wait_limit:
                suppress_fast_exit = True

            if strict_arena_image_generation and not recovery_confirmed:
                # Arena image generation is final only when its native Stop
                # control is gone and a new image or a terminal error exists.
                # Text stability and an empty DOM must never shortcut this gate.
                pass
            elif interrupted_stream_recovery and not recovery_confirmed:
                # Do not let stable DOM text from the pre-refresh page satisfy
                # any completion rule after the network stream was interrupted.
                pass
            elif hold_unrendered_image:
                # Do not let a URL/reference-only change satisfy the normal
                # completion rules while the page still has no rendered image.
                pass
            elif ctx.content_ever_changed:
                if (not suppress_fast_exit and ctx.stable_text_count >= stable_count_threshold and
                        silence_duration > silence_threshold):
                    logger.debug(f"生成结束 (稳定{ctx.stable_text_count}次, 静默{silence_duration:.1f}s)")
                    break
                elif (not suppress_fast_exit and silence_duration > silence_threshold_fallback * 3):
                    logger.info(f"[Exit] 生成结束（超长静默 {silence_duration:.1f}s）")
                    break
                elif (
                    ctx.images_detected
                    and not still_generating
                    and silence_duration > max(3.0, silence_threshold)
                ):
                    # 图片流式响应要等到生成指示器消失后，再按正常静默阈值退出
                    logger.debug(f"[Exit] 图片生成完成（静默 {silence_duration:.1f}s）")
                    break
            else:
                if (
                    image_mode_enabled
                    and no_visible_progress
                    and still_generating
                    and no_progress_hard_limit > 0
                    and elapsed_since_phase_start >= no_progress_hard_limit
                ):
                    logger.warning(
                        "[Exit] 图片模式无可见进展，达到硬等待上限后结束 "
                        f"(elapsed={elapsed_since_phase_start:.1f}s, "
                        f"hard_limit={no_progress_hard_limit:.1f}s)"
                    )
                    break
                if (
                    image_mode_enabled
                    and no_visible_progress
                    and not still_generating
                    and elapsed_since_phase_start >= no_progress_wait_limit
                ):
                    logger.info(
                        "[Exit] 图片模式无可见进展，达到最长等待后结束 "
                        f"(elapsed={elapsed_since_phase_start:.1f}s)"
                    )
                    break
                if not suppress_fast_exit and not still_generating and not has_output:
                    # 🆕 如果有图片但没文本，也认为是有效回复
                    if ctx.images_detected or current_text_len > ctx.active_turn_baseline_len + 5:
                        logger.info("[Exit] 检测到快速回复（无增量但有最终内容/图片）")
                        break

            sleep_elapsed = 0.0
            while sleep_elapsed < current_interval:
                if self._should_stop():
                    break
                step = min(0.1, current_interval - sleep_elapsed)
                time.sleep(step)
                sleep_elapsed += step

        if interrupted_stream_recovery and not self._should_stop() and not recovery_confirmed:
            self._stream_recovery_exhausted = True
            logger.error(
                "[Stream Recovery] 断流结果在恢复窗口内未确认，停止本轮而不释放为成功"
            )

        if not self._should_stop() and not self._stream_recovery_exhausted:
            yield from self._final_settle_and_output(selector, ctx, completion_id=completion_id)

    @staticmethod
    def _resolve_final_effective_start(ctx: StreamContext, final_text: str) -> int:
        """计算最终补齐阶段的起始偏移。

        网络监听半途回退 DOM 时会把"网络层已发送的原始流字符数"种入 ctx，
        但 DOM 渲染文本通常比原始 markdown 流短（语法字符被渲染吃掉），
        长度确认条件可能永远达不到：network_sent_offset_pending 一直为 True、
        sent_content_length 保持 0。此前这里直接用 baseline + sent_content_length
        作为起点，等于把网络层已经流出的内容整段重发一遍，客户端收到大段重复文本。
        偏移始终未确认时，保守地按网络已发字符数推进——宁可少补尾部，也不整段重发。
        """
        effective_start = ctx.active_turn_baseline_len + ctx.sent_content_length
        if (
            getattr(ctx, "network_sent_offset_pending", False)
            and ctx.network_sent_content_length > ctx.sent_content_length
        ):
            logger.warning(
                "[Final] 网络偏移始终未确认，按网络已发字符数收尾以避免重发 "
                f"(network_sent={ctx.network_sent_content_length}, "
                f"dom_sent={ctx.sent_content_length}, final_len={len(final_text)})"
            )
            effective_start = min(
                len(final_text),
                ctx.active_turn_baseline_len + ctx.network_sent_content_length,
            )
        return effective_start

    def _final_settle_and_output(self, selector: str, ctx: StreamContext,
                                 completion_id: Optional[str] = None) -> Generator[str, None, None]:
        """最终阶段（v5.5：包含图片提取）"""
        settle_time = 1.5
        # 修复#6b：基础硬顶保持 5s；当「本轮快照相比上轮有变化」持续发生
        # （内容仍在生成/渲染）时允许延长，总上限放宽到 FINAL_SETTLE_EXTENDED_HARDCAP。
        hardcap = float(self.FINAL_SETTLE_HARDCAP)
        extended_hardcap = float(self.FINAL_SETTLE_EXTENDED_HARDCAP)

        start = time.time()
        stable_start = time.time()
        # 修复#6b：记录上一轮快照对比是否发生变化，决定是否启用延长后的硬顶
        snapshot_changing = False

        last_snap = self._get_snapshot_prefer_anchor(selector, ctx.output_target_anchor)

        while True:
            if self._should_stop():
                break
            now = time.time()
            effective_hardcap = extended_hardcap if snapshot_changing else hardcap
            if now - start > effective_hardcap:
                break
            if now - stable_start >= settle_time:
                break

            time.sleep(0.15)
            snap = self._get_snapshot_prefer_anchor(selector, ctx.output_target_anchor)

            changed = False
            if snap['groups_count'] > last_snap['groups_count']:
                changed = True
                if snap['anchor'] != ctx.output_target_anchor:
                    ctx.output_target_anchor = snap['anchor']
                    ctx.output_target_count = snap['groups_count']
                    if not self._uses_interrupted_stream_recovery():
                        ctx.reset_for_new_target()
                    last_snap = snap
                    stable_start = time.time()
                    snapshot_changing = True  # 修复#6b：切换目标同样视为变化
                    continue

            if snap['text_len'] != last_snap['text_len']:
                changed = True
            if snap['anchor'] != last_snap['anchor']:
                changed = True
            # 🆕 图片变化也算 changed
            if snap.get('image_count', 0) != last_snap.get('image_count', 0):
                changed = True

            if changed:
                stable_start = time.time()
            snapshot_changing = changed
            last_snap = snap

        final_snap = self._get_snapshot_prefer_anchor(selector, ctx.output_target_anchor)
        final_text = final_snap.get('text', "") or ""
        final_image_urls = [
            url
            for url in _normalize_snapshot_image_urls(
                list(final_snap.get('image_urls') or [])
                + list(final_snap.get('page_image_urls') or [])
            )
            if _snapshot_image_reference_key(url) not in ctx.baseline_image_references
        ]
        final_has_new_image = self._snapshot_has_new_image(ctx.baseline_snapshot, final_snap)
        if final_has_new_image:
            self._image_recovery_exhausted = False
            ctx.images_detected = True
            self._prefetch_snapshot_image_urls(final_snap)
        self._final_image_urls = final_image_urls

        # 文本补齐
        if final_text:
            final_effective_start = self._resolve_final_effective_start(ctx, final_text)
            if len(final_text) > final_effective_start:
                remaining = final_text[final_effective_start:]
                if remaining:
                    ctx.sent_content_length += len(remaining)
                    visible_remaining = self._sanitize_stream_text(remaining)
                    if visible_remaining.strip():
                        logger.debug(f"[Final] 发送剩余内容: {len(remaining)} 字符")
                        yield self.formatter.pack_chunk(visible_remaining, completion_id=completion_id)
                    else:
                        logger.debug("[Final] Suppressed Gemini placeholder-only remainder")

            self._final_complete_text = self._sanitize_stream_text(
                final_text[ctx.active_turn_baseline_len:]
            )
        else:
            fallback_text = self._get_active_turn_text(selector) or ctx.max_seen_text
            if fallback_text:
                final_effective_start = self._resolve_final_effective_start(ctx, fallback_text)
                if len(fallback_text) > final_effective_start:
                    remaining = fallback_text[final_effective_start:]
                    if remaining:
                        ctx.sent_content_length += len(remaining)
                        visible_remaining = self._sanitize_stream_text(remaining)
                        if visible_remaining.strip():
                            yield self.formatter.pack_chunk(visible_remaining, completion_id=completion_id)
                        else:
                            logger.debug("[Final] Suppressed Gemini placeholder-only fallback remainder")

                self._final_complete_text = self._sanitize_stream_text(
                    fallback_text[ctx.active_turn_baseline_len:]
                )
            else:
                self._final_complete_text = self._sanitize_stream_text(
                    ctx.max_seen_text[ctx.active_turn_baseline_len:] if ctx.max_seen_text else ""
                )

        # 🆕 ===== 最终图片提取 =====
        if self._image_extraction_enabled and (ctx.images_detected or final_has_new_image):
            images = self._extract_final_images(selector, ctx)
            if images:
                self._final_images = images
                logger.debug(f"[Final] 提取到 {len(images)} 张图片")

                logger.debug("[Final] 已提取图片，但已禁用 StreamMonitor 图片 chunk 输出（由 BrowserCore 统一发送本地图片）")
            elif final_image_urls:
                column = self._get_latest_visual_column()
                # 在双栏对战场景下（column in {'left', 'right'}），若目标侧容器未提取到图片，绝不能使用全页级的 page_image_urls 跨侧误塞图片！
                if column not in {"left", "right"}:
                    self._final_images = [
                        {
                            "kind": "url",
                            "url": url,
                            "data_uri": None,
                            "media_type": "image",
                            "source": "stream_snapshot",
                        }
                        for url in final_image_urls
                    ]
                    logger.debug(f"[Final] 图片提取超时后回退到快照 URL: {len(self._final_images)} 张")

        logger.debug(f"流式监听结束: {ctx.sent_content_length}字符, {len(self._final_images)}张图片")

    def _extract_final_images(self, selector: str, ctx: StreamContext) -> List[Dict]:
        """
        🆕 提取最终图片（带超时保护）
        """
        if not self._image_extraction_enabled:
            return []
        
        # 🆕 超时保护：默认 5 秒，可通过配置覆盖
        timeout = self._image_config.get("extraction_timeout", 5.0)
        

        
        started_at = time.time()
        try:
            # DrissionPage/CDP 对象不能跨线程安全调用；最终图片提取保持在当前工作流线程串行执行。
            eles = self.finder.find_all(selector, timeout=min(1.0, max(0.1, float(timeout or 1.0))))
            if not eles:
                return []

            strategy = self._get_final_target_strategy()
            target = None

            if strategy == "latest_reply" and ctx.output_target_anchor:
                for ele in reversed(eles):
                    try:
                        anchor = self.extractor.get_anchor(ele)
                    except Exception:
                        anchor = ""
                    if anchor and anchor == ctx.output_target_anchor:
                        target = ele
                        break

            if target is None:
                target, _ = self._select_candidate_element(eles)
                if target is None:
                    return []

            if not hasattr(self.extractor, 'extract_images'):
                return []

            images = self.extractor.extract_images(
                target,
                config=self._image_config,
                container_selector_fallback=selector
            )
            elapsed = time.time() - started_at
            if elapsed > float(timeout or 0):
                logger.warning(f"[Final] 图片提取耗时超过配置窗口: {elapsed:.1f}s > {float(timeout or 0):.1f}s")
            return images

        except Exception as e:
            logger.error(f"[Final] 图片提取失败: {e}")
            return []
    
    def get_final_images(self) -> List[Dict]:
        """获取最终提取的图片（供外部调用）"""
        return self._final_images

    def has_detected_images(self) -> bool:
        """返回本轮流式监听期间是否曾观测到图片出现。"""
        return bool(getattr(self._stream_ctx, "images_detected", False))

    def get_final_image_urls(self) -> List[str]:
        """获取最终 settle 快照中识别到的远程图片 URL。"""
        return list(self._final_image_urls)

    def capture_interrupted_image_resume_state(self) -> Optional[Dict[str, Any]]:
        """Capture enough state to finish an interrupted image turn from DOM.

        Command-engine interruptions can arrive before or after the generated
        image renders. Restarting the network listener cannot observe the
        original response again, so preserve the original send baseline and
        continue through the DOM monitor instead. The DOM recovery path is also
        responsible for refreshing while the image is still generating.
        """
        ctx = self._stream_ctx
        if ctx is None:
            # A command interrupt can arrive while the network monitor owns
            # the request, before this DOM monitor has created StreamContext.
            # The send click still captured a DOM baseline, so image tasks can
            # safely resume from that baseline and wait/refresh in the DOM
            # path instead of rebuilding a network listener that cannot see
            # the already-open response again.
            if not self._looks_like_expected_image_output():
                return None
            baseline_snapshot = self._pending_send_baseline
            if not isinstance(baseline_snapshot, dict) or not baseline_snapshot:
                return None
            return {
                "baseline_snapshot": dict(baseline_snapshot),
                "sent_content_length": 0,
                "image_urls": [],
            }
        if not self._expect_image_output:
            return None

        known_image_urls = _normalize_snapshot_image_urls(
            list(self._prefetched_image_urls) + list(self._final_image_urls)
        )

        baseline_snapshot = ctx.baseline_snapshot or ctx.instant_baseline
        if not isinstance(baseline_snapshot, dict) or not baseline_snapshot:
            return None

        return {
            "baseline_snapshot": dict(baseline_snapshot),
            "sent_content_length": max(
                int(ctx.sent_content_length or 0),
                int(ctx.network_sent_content_length or 0),
            ),
            "image_urls": known_image_urls,
        }

    def image_recovery_exhausted(self) -> bool:
        return bool(self._image_recovery_exhausted)

    def stream_recovery_exhausted(self) -> bool:
        return bool(self._stream_recovery_exhausted)

    def cleanup(self) -> None:
        self._final_complete_text = ""
        self._final_images = []
        self._final_image_urls = []
        self._prefetched_image_urls = set()
        self._pending_send_baseline = None
        self._stream_ctx = None
        self._generating_checker = None
        self._expect_image_output = False
        self._last_visual_reply_log_info = None
        self._network_fallback_reason = ""
        self._recovery_mode = ""
        self._image_recovery_exhausted = False
        self._stream_recovery_exhausted = False
        self._stream_recovery_refresh_done = False
        self._stream_recovery_refresh_attempts = 0
        self._arena_image_guard = None
        logger.debug("[StreamMonitor] large cached stream results cleared")


__all__ = ['StreamContext', 'GeneratingStatusCache', 'StreamMonitor']
