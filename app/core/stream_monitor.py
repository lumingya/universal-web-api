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

from app.core.config import logger, BrowserConstants, SSEFormatter
from app.core.background_image_downloader import (
    background_image_downloader,
    build_image_download_request_context,
    normalize_remote_image_url,
)
from app.core.elements import ElementFinder
from app.core.extractors.base import BaseExtractor
from app.core.extractors.deep_mode import DeepBrowserExtractor
from app.models.schemas import get_modality_run_policy, is_modality_enabled

_GEMINI_IMAGE_PLACEHOLDER_RE = re.compile(
    r"^\s*https?://(?:[\w.-]+\.)?googleusercontent\.com/image_generation_content/\d+\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _looks_like_image_generation_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False

    direct_markers = (
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
    if any(marker in lowered for marker in direct_markers):
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
    if any(action in lowered for action in english_actions) and any(obj in lowered for obj in english_objects):
        return True

    chinese_actions = ("画", "绘制", "生成", "创作", "设计")
    chinese_objects = ("图片", "图像", "照片", "插画", "海报", "logo", "图标", "头像", "封面", "壁纸")
    return any(action in lowered for action in chinese_actions) and any(
        obj in lowered for obj in chinese_objects
    )


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


class StreamContext:
    """流式监控上下文（v5.5 增加图片追踪）"""
    def __init__(self):
        self.max_seen_text = ""
        self.sent_content_length = 0

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
        self.images_detected = False

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

    def _looks_like_expected_image_output(self, user_input: str = "") -> bool:
        modalities = self._image_config.get("modalities") or {}
        if not is_modality_enabled(modalities, "image"):
            return False
        image_run_policy = get_modality_run_policy(modalities, "image")
        if image_run_policy == "always_probe":
            return bool(self._image_extraction_enabled)
        return bool(
            self._image_extraction_enabled
            and _looks_like_image_generation_request(user_input)
        )

    def _should_probe_dom_images(self) -> bool:
        return bool(self._expect_image_output)

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
        value = str(self._image_config.get("latest_visual_column", "left") or "left").strip().lower()
        return value if value in {"left", "right"} else "left"

    def _select_candidate_element(self, elements, prefer_anchor: Optional[str] = None):
        if not elements:
            return None, None

        strategy = self._get_final_target_strategy()

        if prefer_anchor and strategy != "latest_visual_reply":
            for ele in reversed(elements):
                try:
                    anchor = self.extractor.get_anchor(ele)
                except Exception:
                    anchor = ""
                if anchor == prefer_anchor:
                    return ele, anchor

        if strategy != "latest_visual_reply":
            target = elements[-1]
            return target, self.extractor.get_anchor(target)

        scored = []
        column = self._get_latest_visual_column()
        for index, ele in enumerate(elements):
            try:
                score = ele.run_js(
                    """
                    const rect = this.getBoundingClientRect();
                    return {
                        top: Number(rect && rect.top || 0) + Number(window.scrollY || 0),
                        bottom: Number(rect && rect.bottom || 0) + Number(window.scrollY || 0),
                        left: Number(rect && rect.left || 0) + Number(window.scrollX || 0),
                        width: Number(rect && rect.width || 0),
                        height: Number(rect && rect.height || 0),
                    };
                    """
                ) or {}
                bottom = float(score.get("bottom") or 0)
                left = float(score.get("left") or 0)
                area = float(score.get("width") or 0) * float(score.get("height") or 0)
            except Exception:
                bottom = 0.0
                left = 0.0
                area = 0.0
            horizontal_score = left if column == "right" else -left
            scored.append((bottom, horizontal_score, area, -index, index, left, ele))

        scored.sort(key=lambda item: item[:4], reverse=True)
        best = scored[0]
        
        current_log_info = (best[4], column, f"{best[0]:.1f}", f"{best[5]:.1f}", len(elements))
        if self._last_visual_reply_log_info != current_log_info:
            self._last_visual_reply_log_info = current_log_info
            logger.debug(
                "[latest_visual_reply] 选中视觉最新回复容器: "
                f"index={best[4]}, column={column}, bottom={best[0]:.1f}, left={best[5]:.1f}, total={len(elements)}"
            )
        target = best[6]
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
        self._prefetched_image_urls = set()
        self._expect_image_output = self._looks_like_expected_image_output(user_input)
        logger.debug(
            f"[MONITOR] expect_image_output={self._expect_image_output}, "
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
        ctx.baseline_snapshot = ctx.instant_baseline
        ctx.instant_last_node_len = ctx.instant_baseline.get('text_len', 0)
        ctx.baseline_image_count = ctx.instant_baseline.get('image_count', 0)  # 🆕
        
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
                if current_image_count > ctx.baseline_image_count:
                    logger.info(f"[Image Detected] 检测到新图片 ({ctx.baseline_image_count} -> {current_image_count})")
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
                except Exception as e:
                    logger.debug(f"图片计数失败: {e}")

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
        }
        try:
            eles = self.finder.find_all(selector, timeout=0.5)
            if not eles:
                return result

            result['groups_count'] = len(eles)

            target, target_anchor = self._select_candidate_element(eles, prefer_anchor)

            if target is None:
                target, target_anchor = self._select_candidate_element(eles)
                
                last_text = self.extractor.extract_text(target)
                if (not last_text or not last_text.strip()) and len(eles) >= 2:
                    logger.debug(f"[Empty Last] 最后一个元素为空，共 {len(eles)} 个元素")

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
                except Exception:
                    pass

            if self._generating_checker is None:
                self._generating_checker = GeneratingStatusCache(self.tab)
            result['is_generating'] = self._generating_checker.is_generating()

        except Exception as e:
            logger.debug(f"Prefer-anchor Snapshot 异常: {e}")

        return result

    def _extract_image_info(self, element) -> Dict[str, Any]:
        script = """
        const nodes = Array.from(this.querySelectorAll('img') || []);
        const urls = [];
        const seen = new Set();
        for (const img of nodes) {
            try {
                const src = String(img.currentSrc || img.getAttribute('src') || img.src || '').trim();
                if (!src || seen.has(src)) continue;
                if (!/^https?:\\/\\//i.test(src)) continue;
                seen.add(src);
                urls.push(src);
            } catch {}
        }
        return { count: urls.length, urls };
        """
        info = element.run_js(script) or {}
        urls = _normalize_snapshot_image_urls(info.get("urls") or [])
        return {
            "count": len(urls),
            "urls": urls,
        }

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
            )
            if result:
                self._prefetched_image_urls.add(url)
                started += 1

        if started:
            logger.debug(f"[DOM Prefetch] 已提交后台图片下载: {started} 个")
        return started

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
            
            for i in range(len(eles) - 2, -1, -1):
                t = self.extractor.extract_text(eles[i])
                if t and t.strip():
                    return t.strip()
            
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
        if current_img > baseline_img:
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

        peak_text_len = 0
        content_shrink_count = 0

        while True:
            if time.time() - phase_start > self._hard_timeout:
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
            current_text_len = len(current_text)
            current_image_count = snap.get('image_count', 0)  # 🆕
            
            # 🆕 检测图片变化
            if current_image_count > last_image_count:
                logger.debug(f"[Image Change] 图片数量变化: {last_image_count} -> {current_image_count}")
                ctx.images_detected = True
                ctx.content_ever_changed = True
                self._prefetch_snapshot_image_urls(snap)
                silence_start = time.time()  # 重置静默计时
                last_image_count = current_image_count

            # 检测内容折叠
            if current_text_len > peak_text_len:
                peak_text_len = current_text_len
                content_shrink_count = 0
            elif peak_text_len > 100 and current_text_len < peak_text_len * 0.5:
                content_shrink_count += 1
                if content_shrink_count >= 2:
                    logger.info(f"[Collapse] 检测到内容折叠：{peak_text_len} -> {current_text_len}")
                    ctx.reset_for_new_target()
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
                if snap.get('has_images'):
                    ctx.content_ever_changed = True
                    # 不 continue，继续执行后面的退出判定逻辑
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
                ctx.update_after_send(diff, current_text)
                current_interval = min_interval
                visible_diff = self._sanitize_stream_text(diff)
                if visible_diff.strip():
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

            silence_duration = time.time() - silence_start

            # 退出判定
            silence_threshold = BrowserConstants.STREAM_SILENCE_THRESHOLD
            silence_threshold_fallback = BrowserConstants.STREAM_SILENCE_THRESHOLD_FALLBACK
            stable_count_threshold = BrowserConstants.STREAM_STABLE_COUNT_THRESHOLD
            if ctx.network_sent_offset_confirmed:
                silence_threshold = min(float(silence_threshold), 1.2)
                silence_threshold_fallback = min(float(silence_threshold_fallback), 2.0)
                stable_count_threshold = min(int(stable_count_threshold), 2)
            image_mode_enabled = self._expect_image_output
            no_visible_progress = (
                image_mode_enabled
                and not ctx.images_detected
                and not has_output
                and ctx.sent_content_length <= 0
                and current_image_count <= max(int(ctx.baseline_image_count or 0), 0)
                and current_text_len <= int(ctx.active_turn_baseline_len or 0) + 2
            )
            no_progress_wait_limit = float(
                self._image_config.get("dom_image_no_output_timeout_seconds")
                or max(45.0, float(silence_threshold_fallback) * 4.0)
            )
            no_progress_hard_limit = float(
                self._image_config.get("dom_image_no_output_hard_timeout_seconds") or 0.0
            )
            elapsed_since_phase_start = time.time() - phase_start
            suppress_fast_exit = False
            if no_visible_progress and elapsed_since_phase_start < no_progress_wait_limit:
                suppress_fast_exit = True

            if ctx.content_ever_changed:
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

        if not self._should_stop():
            yield from self._final_settle_and_output(selector, ctx, completion_id=completion_id)

    def _final_settle_and_output(self, selector: str, ctx: StreamContext,
                                 completion_id: Optional[str] = None) -> Generator[str, None, None]:
        """最终阶段（v5.5：包含图片提取）"""
        settle_time = 1.5
        hardcap = 5.0

        start = time.time()
        stable_start = time.time()

        last_snap = self._get_snapshot_prefer_anchor(selector, ctx.output_target_anchor)

        while True:
            if self._should_stop():
                break
            now = time.time()
            if now - start > hardcap:
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
                    ctx.reset_for_new_target()
                    last_snap = snap
                    stable_start = time.time()
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
            last_snap = snap

        final_snap = self._get_snapshot_prefer_anchor(selector, ctx.output_target_anchor)
        final_text = final_snap.get('text', "") or ""
        final_image_urls = _normalize_snapshot_image_urls(final_snap.get('image_urls') or [])
        if final_snap.get('has_images'):
            ctx.images_detected = True
            self._prefetch_snapshot_image_urls(final_snap)
        self._final_image_urls = final_image_urls

        # 文本补齐
        if final_text:
            final_effective_start = ctx.active_turn_baseline_len + ctx.sent_content_length
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
            fallback_text = self._get_active_turn_text(selector)
            if fallback_text:
                final_effective_start = ctx.active_turn_baseline_len + ctx.sent_content_length
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
        if self._image_extraction_enabled and (ctx.images_detected or final_snap.get('has_images')):
            images = self._extract_final_images(selector, ctx)
            if images:
                self._final_images = images
                logger.debug(f"[Final] 提取到 {len(images)} 张图片")

                logger.debug("[Final] 已提取图片，但已禁用 StreamMonitor 图片 chunk 输出（由 BrowserCore 统一发送本地图片）")
            elif final_image_urls:
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
        logger.debug("[StreamMonitor] large cached stream results cleared")


__all__ = ['StreamContext', 'GeneratingStatusCache', 'StreamMonitor']
