"""
app/core/stream_monitor.py - 流式监听核心（v5.5 图片支持版）

v5.5 修改：
- 添加图片检测（快照中包含 image_count）
- _detect_ai_start() 支持图片出现检测
- 最终阶段自动提取图片
- 新增 _image_config 配置支持
"""

import time
import threading
from typing import Generator, Optional, Callable, Tuple, Dict, List, Any

from app.core.config import logger, BrowserConstants, SSEFormatter
from app.core.elements import ElementFinder
from app.core.extractors.base import BaseExtractor
from app.core.extractors.deep_mode import DeepBrowserExtractor


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

    def reset_for_new_target(self):
        """切换到新目标节点时重置状态"""
        self.max_seen_text = ""
        self.sent_content_length = 0
        self.stable_text_count = 0
        self.last_stable_text = ""
        self.active_turn_baseline_len = 0
        self.content_ever_changed = False
        # v5.5: 不重置 images_detected，保持图片检测状态

    def calculate_diff(self, current_text: str) -> Tuple[str, bool, Optional[str]]:
        """v5 增强版 diff：支持前缀校验"""
        if not current_text:
            return "", False, None

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
            if len(current_active_text) < self.sent_content_length:
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
        self._generating_checker: Optional[GeneratingStatusCache] = None

    def monitor(self, selector: str, user_input: str = "",
                completion_id: Optional[str] = None) -> Generator[str, None, None]:
        logger.debug("流式监听启动")
        logger.debug(f"[MONITOR] selector_raw={selector!r}, image_enabled={self._image_extraction_enabled}")
        
        if completion_id is None:
            completion_id = SSEFormatter._generate_id()

        ctx = StreamContext()
        self._stream_ctx = ctx
        self._final_images = []
        self._generating_checker = GeneratingStatusCache(self.tab)

        # ===== 阶段 0：instant baseline =====
        ctx.instant_baseline = self._get_latest_message_snapshot(selector)
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
            'has_images': False    # 🆕
        }
        try:
            eles = self.finder.find_all(selector, timeout=0.5)
            if not eles:
                return result

            last_ele = eles[-1]
            text = self.extractor.extract_text(last_ele)

            result['groups_count'] = len(eles)
            result['text'] = text or ""
            result['text_len'] = len(result['text'])
            result['anchor'] = self.extractor.get_anchor(last_ele)

            # 🆕 图片检测（轻量级，只计数）
            try:
                img_count = last_ele.run_js("""
                    return (this.querySelectorAll('img') || []).length;
                """) or 0
                result['image_count'] = int(img_count)
                result['has_images'] = img_count > 0
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
            'has_images': False    # 🆕
        }
        try:
            eles = self.finder.find_all(selector, timeout=0.5)
            if not eles:
                return result

            result['groups_count'] = len(eles)

            target = None
            target_anchor = None

            if prefer_anchor:
                for ele in reversed(eles):
                    a = self.extractor.get_anchor(ele)
                    if a == prefer_anchor:
                        target = ele
                        target_anchor = a
                        break

            if target is None:
                target = eles[-1]
                target_anchor = self.extractor.get_anchor(target)
                
                last_text = self.extractor.extract_text(target)
                if (not last_text or not last_text.strip()) and len(eles) >= 2:
                    logger.debug(f"[Empty Last] 最后一个元素为空，共 {len(eles)} 个元素")

            text = self.extractor.extract_text(target) or ""

            result['anchor'] = target_anchor
            result['text'] = text
            result['text_len'] = len(text)

            # 🆕 图片检测
            try:
                img_count = target.run_js("return (this.querySelectorAll('img') || []).length;") or 0
                result['image_count'] = int(img_count)
                result['has_images'] = img_count > 0
            except Exception:
                pass

            if self._generating_checker is None:
                self._generating_checker = GeneratingStatusCache(self.tab)
            result['is_generating'] = self._generating_checker.is_generating()

        except Exception as e:
            logger.debug(f"Prefer-anchor Snapshot 异常: {e}")

        return result

    def _get_active_turn_text(self, selector: str) -> str:
        """回退：取最后一个元素的文本"""
        try:
            eles = self.finder.find_all(selector, timeout=1)
            if not eles:
                return ""
            
            last_text = self.extractor.extract_text(eles[-1])
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

        peak_text_len = 0
        content_shrink_count = 0

        while True:
            if time.time() - phase_start > self._hard_timeout:
                logger.error(f"[HardTimeout] 超过最大监听时间 {self._hard_timeout}s，强制退出")
                break
            
            if self._should_stop():
                logger.info("输出阶段被取消")
                break

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
                        ctx.reset_for_new_target()
                        peak_text_len = 0
                        silence_start = time.time()
                        has_output = False
                        last_text_len = 0
                        last_image_count = 0  # 🆕

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

            if len(current_text) > len(ctx.max_seen_text):
                ctx.max_seen_text = current_text

            diff, is_from_history, reason = ctx.calculate_diff(current_text)
            
            # 🆕 处理前缀不匹配（内容被重写）
            if reason == "prefix_mismatch":
                logger.info("[PREFIX_MISMATCH] 内容重写，发送完整当前内容")
                
                # 重置发送状态
                ctx.sent_content_length = 0
                ctx.max_seen_text = ""
                
                # 发送当前完整内容
                full_content = current_text[ctx.active_turn_baseline_len:]
                if full_content:
                    yield self.formatter.pack_chunk(full_content, completion_id=completion_id)
                    ctx.update_after_send(full_content, current_text)
                    silence_start = time.time()
                    has_output = True
                    ctx.content_ever_changed = True
                
                continue
            
            if diff:
                if self._should_stop():
                    break
                ctx.update_after_send(diff, current_text)
                silence_start = time.time()
                has_output = True
                current_interval = min_interval
                ctx.content_ever_changed = True

                yield self.formatter.pack_chunk(diff, completion_id=completion_id)
            else:
                if current_text == ctx.last_stable_text:
                    ctx.stable_text_count += 1
                else:
                    ctx.stable_text_count = 0
                    ctx.last_stable_text = current_text
                current_interval = min(current_interval * 1.5, max_interval)

            if current_text_len != last_text_len:
                ctx.content_ever_changed = True
                last_text_len = current_text_len

            silence_duration = time.time() - silence_start

            # 退出判定
            silence_threshold = BrowserConstants.STREAM_SILENCE_THRESHOLD
            silence_threshold_fallback = BrowserConstants.STREAM_SILENCE_THRESHOLD_FALLBACK
            stable_count_threshold = BrowserConstants.STREAM_STABLE_COUNT_THRESHOLD

            if ctx.content_ever_changed:
                if (ctx.stable_text_count >= stable_count_threshold and
                        silence_duration > silence_threshold):
                    logger.debug(f"生成结束 (稳定{ctx.stable_text_count}次, 静默{silence_duration:.1f}s)")
                    break
                elif silence_duration > silence_threshold_fallback * 3:
                    logger.info(f"[Exit] 生成结束（超长静默 {silence_duration:.1f}s）")
                    break
                elif ctx.images_detected and silence_duration > 3.0:
                    # 有图片且静默超过 3 秒，认为生成完成
                    logger.debug(f"[Exit] 图片生成完成（静默 {silence_duration:.1f}s）")
                    break
            else:
                if not still_generating and not has_output:
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

        # 文本补齐
        if final_text:
            final_effective_start = ctx.active_turn_baseline_len + ctx.sent_content_length
            if len(final_text) > final_effective_start:
                remaining = final_text[final_effective_start:]
                if remaining:
                    logger.debug(f"[Final] 发送剩余内容: {len(remaining)} 字符")
                    yield self.formatter.pack_chunk(remaining, completion_id=completion_id)
                    ctx.sent_content_length += len(remaining)

            self._final_complete_text = final_text[ctx.active_turn_baseline_len:]
        else:
            fallback_text = self._get_active_turn_text(selector)
            if fallback_text:
                final_effective_start = ctx.active_turn_baseline_len + ctx.sent_content_length
                if len(fallback_text) > final_effective_start:
                    remaining = fallback_text[final_effective_start:]
                    if remaining:
                        yield self.formatter.pack_chunk(remaining, completion_id=completion_id)
                        ctx.sent_content_length += len(remaining)

                self._final_complete_text = fallback_text[ctx.active_turn_baseline_len:]
            else:
                self._final_complete_text = ctx.max_seen_text[ctx.active_turn_baseline_len:] if ctx.max_seen_text else ""

        # 🆕 ===== 最终图片提取 =====
        if self._image_extraction_enabled and (ctx.images_detected or final_snap.get('has_images')):
            images = self._extract_final_images(selector, ctx)
            if images:
                self._final_images = images
                logger.debug(f"[Final] 提取到 {len(images)} 张图片")

                logger.debug("[Final] 已提取图片，但已禁用 StreamMonitor 图片 chunk 输出（由 BrowserCore 统一发送本地图片）")

        logger.debug(f"流式监听结束: {ctx.sent_content_length}字符, {len(self._final_images)}张图片")

    def _extract_final_images(self, selector: str, ctx: StreamContext) -> List[Dict]:
        """
        🆕 提取最终图片（带超时保护）
        """
        if not self._image_extraction_enabled:
            return []
        
        # 🆕 超时保护：默认 5 秒，可通过配置覆盖
        timeout = self._image_config.get("extraction_timeout", 5.0)
        

        
        result_container = {"images": []}
        extraction_error = {"error": None}
        
        def extract_with_timeout():
            """在独立线程中执行提取"""
            try:
                eles = self.finder.find_all(selector, timeout=1)
                if not eles:
                    return
                
                target = eles[-1]
                
                # 使用提取器的 extract_images 方法
                if hasattr(self.extractor, 'extract_images'):
                    images = self.extractor.extract_images(
                        target,
                        config=self._image_config,
                        container_selector_fallback=selector
                    )
                    result_container["images"] = images
            
            except Exception as e:
                extraction_error["error"] = e
        
        try:
            # 启动提取线程
            extraction_thread = threading.Thread(target=extract_with_timeout, daemon=True)
            extraction_thread.start()
            
            # 等待超时
            extraction_thread.join(timeout=timeout)
            
            # 检查是否超时
            if extraction_thread.is_alive():
                logger.warning(f"[Final] 图片提取超时（{timeout}s），跳过")
                return []
            
            # 检查是否有错误
            if extraction_error["error"]:
                raise extraction_error["error"]
            
            return result_container["images"]
        
        except Exception as e:
            logger.error(f"[Final] 图片提取失败: {e}")
            return []
    
    def get_final_images(self) -> List[Dict]:
        """获取最终提取的图片（供外部调用）"""
        return self._final_images


__all__ = ['StreamContext', 'GeneratingStatusCache', 'StreamMonitor']