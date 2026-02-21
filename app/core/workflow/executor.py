"""
app/core/workflow/executor.py - 工作流执行器

职责：
- 工作流步骤编排
- 点击、等待等基础操作
- 可靠发送（图片上传场景）
- 与 StreamMonitor 协同
"""

import time
import random
from typing import Generator, Dict, Any, Callable, Optional

from app.core.config import (
    logger,
    BrowserConstants,
    SSEFormatter,
    ElementNotFoundError,
    WorkflowError,
)
from app.core.elements import ElementFinder
from app.utils.human_mouse import smooth_move_mouse, idle_drift, human_scroll, cdp_precise_click
from app.core.stream_monitor import StreamMonitor
from app.core.network_monitor import (
    create_network_monitor,
    NetworkMonitorTimeout,
    NetworkMonitorError
)

from .text_input import TextInputHandler
from .image_input import ImageInputHandler


# ================= 工作流执行器 =================

class WorkflowExecutor:
    """工作流执行器"""
    
    def __init__(self, tab, stealth_mode: bool = False, 
                 should_stop_checker: Callable[[], bool] = None,
                 extractor = None,
                 image_config: Dict = None,
                 stream_config: Dict = None,
                 file_paste_config: Dict = None):
        self.tab = tab
        self.stealth_mode = stealth_mode
        self.finder = ElementFinder(tab)
        self.formatter = SSEFormatter()
        
        self._should_stop = should_stop_checker or (lambda: False)
        self._extractor = extractor
        self._image_config = image_config or {}  
        self._stream_config = stream_config or {}
        
        # 🆕 初始化双 Monitor（优先网络，回退 DOM）
        self._network_monitor = None
        self._stream_monitor = None
        
        # 检查是否启用网络监听模式
        stream_mode = stream_config.get("mode", "dom") if stream_config else "dom"
        network_config = stream_config.get("network", {}) if stream_config else {}
        
        # 只有当 mode="network" 且配置了 parser 时才启用网络监听
        if stream_mode == "network" and network_config and network_config.get("parser"):
            # 创建网络监听器
            try:
                self._network_monitor = create_network_monitor(
                    tab=tab,
                    formatter=self.formatter,
                    stream_config=stream_config,
                    stop_checker=should_stop_checker
                )
                logger.debug(
                    f"[Executor] 网络监听器已启用 "
                    f"(parser={network_config.get('parser')})"
                )
            except Exception as e:
                logger.warning(f"[Executor] 网络监听器创建失败: {e}")
        
        # 始终创建 DOM 监听器（作为回退）
        self._stream_monitor = StreamMonitor(
            tab=tab,
            finder=self.finder,
            formatter=self.formatter,
            stop_checker=should_stop_checker,
            extractor=extractor,
            image_config=image_config,
            stream_config=stream_config
        )
        
        self._completion_id = SSEFormatter._generate_id()
                
        # 🆕 隐身模式鼠标位置追踪（CDP 绝对坐标）
        self._mouse_pos = None
        # 初始化输入处理器
        self._text_handler = TextInputHandler(
            tab=tab,
            stealth_mode=stealth_mode,
            smart_delay_fn=self._smart_delay,
            check_cancelled_fn=self._check_cancelled,
            file_paste_config=file_paste_config
        )
        
        self._image_handler = ImageInputHandler(
            tab=tab,
            stealth_mode=stealth_mode,
            smart_delay_fn=self._smart_delay,
            check_cancelled_fn=self._check_cancelled
        )
        
        if extractor:
            logger.debug(f"WorkflowExecutor 使用提取器: {extractor.get_id()}")
        
        if self._image_config.get("enabled"):
            logger.debug(f"[IMAGE] 图片提取已启用")
        
        if self.stealth_mode:
            logger.debug("[STEALTH] 隐身模式已启用")
    
    # ================= 控制方法 =================
    
    def _check_cancelled(self) -> bool:
        """检查是否被取消"""
        return self._should_stop()
    
    def _smart_delay(self, min_sec: float = None, max_sec: float = None):
        """
        智能延迟（v5.5 增强版）
        
        改进：
        - 正态分布（更像人类）
        - 10% 概率额外停顿（模拟走神）
        - 可被取消中断
        """
        if not self.stealth_mode:
            return
        
        min_sec = min_sec or BrowserConstants.STEALTH_DELAY_MIN
        max_sec = max_sec or BrowserConstants.STEALTH_DELAY_MAX
        
        # 正态分布参数
        mean = (min_sec + max_sec) / 2
        std = (max_sec - min_sec) / 4
        
        # 生成延迟时间
        total_delay = random.gauss(mean, std)
        
        # 限制范围
        total_delay = max(min_sec, min(total_delay, max_sec))
        
        # 10% 概率"走神"（额外停顿）
        pause_prob = getattr(BrowserConstants, 'STEALTH_PAUSE_PROBABILITY', 0.1)
        pause_max = getattr(BrowserConstants, 'STEALTH_PAUSE_EXTRA_MAX', 0.8)
        
        if random.random() < pause_prob:
            extra = random.uniform(0.2, pause_max)
            total_delay = min(total_delay + extra, 1.0)  # 不超过 1s
            logger.debug(f"[STEALTH] 随机停顿 +{extra:.2f}s")
        
        # 可中断的等待
        elapsed = 0
        step = 0.05
        
        while elapsed < total_delay:
            if self._check_cancelled():
                return
            time.sleep(min(step, total_delay - elapsed))
            elapsed += step
    
    # ================= 隐身模式辅助方法 =================
    
    def _idle_wait(self, duration: float):
        """
        带微漂移的空闲等待（隐身模式专用）
        
        如果有已知鼠标位置，等待期间产生微小漂移事件；
        否则退化为纯 sleep（仍可中断）。
        """
        if self._mouse_pos is not None:
            self._mouse_pos = idle_drift(
                tab=self.tab,
                duration=duration,
                center_pos=self._mouse_pos,
                check_cancelled=self._check_cancelled
            )
        else:
            elapsed = 0
            step = 0.1
            while elapsed < duration:
                if self._check_cancelled():
                    return
                time.sleep(min(step, duration - elapsed))
                elapsed += step
    
    def _stealth_move_to_element(self, ele):
        """
        隐身模式下平滑移动鼠标到元素附近
        
        通过 DrissionPage 原生属性获取坐标，不注入 JS。
        如果坐标获取失败，跳过移动（后续 click 自带定位）。
        """
        if self._mouse_pos is None:
            return
        
        target = self._get_element_viewport_pos(ele)
        if target is None:
            return
        
        # 随机偏移（不精确命中中心）
        tx = target[0] + random.randint(-8, 8)
        ty = target[1] + random.randint(-5, 5)
        
        try:
            self._mouse_pos = smooth_move_mouse(
                tab=self.tab,
                from_pos=self._mouse_pos,
                to_pos=(tx, ty),
                check_cancelled=self._check_cancelled
            )
        except Exception as e:
            logger.debug(f"[STEALTH] 平滑移动异常（可忽略）: {e}")
    
    def _get_element_viewport_pos(self, ele) -> Optional[tuple]:
        """
        获取元素视口坐标（不注入 JS）
        
        依次尝试多种 DrissionPage 原生属性。
        对于可见的固定位置元素（如聊天输入框），
        页面坐标近似等于视口坐标。
        """
        try:
            r = ele.rect
            
            # 尝试 viewport 相关属性
            for attr in ('viewport_midpoint', 'viewport_click_point'):
                pos = getattr(r, attr, None)
                if pos and len(pos) >= 2:
                    return (int(pos[0]), int(pos[1]))
            
            # midpoint（页面坐标，对可见元素近似视口坐标）
            pos = getattr(r, 'midpoint', None)
            if pos and len(pos) >= 2:
                return (int(pos[0]), int(pos[1]))
            
            # click_point
            pos = getattr(r, 'click_point', None)
            if pos and len(pos) >= 2:
                return (int(pos[0]), int(pos[1]))
            
            # location + size 计算中心
            loc = getattr(r, 'location', None)
            size = getattr(r, 'size', None)
            if loc and size and len(loc) >= 2 and len(size) >= 2:
                return (int(loc[0] + size[0] / 2), int(loc[1] + size[1] / 2))
        except Exception:
            pass
        
        return None
    
    def _get_viewport_size(self) -> tuple:
        """获取视口尺寸（不注入 JS）"""
        try:
            r = self.tab.rect
            for attr in ('viewport_size', 'size'):
                s = getattr(r, attr, None)
                if s and len(s) >= 2 and s[0] > 100:
                    return (int(s[0]), int(s[1]))
        except Exception:
            pass
        return (1200, 800)
    
    # ================= 步骤执行 =================
    
    def execute_step(self, action: str, selector: str,
                     target_key: str, value: str = None,
                     optional: bool = False,
                     context: Dict = None) -> Generator[str, None, None]:
        """执行单个步骤"""
        
        if self._check_cancelled():
            logger.debug(f"步骤 {action} 跳过（已取消）")
            return
        
        logger.debug(f"执行: {action} -> {target_key}")
        self._context = context
        
        try:
            if action == "WAIT":
                wait_time = float(value or 0.5)
                elapsed = 0
                while elapsed < wait_time:
                    if self._check_cancelled():
                        return
                    time.sleep(min(0.1, wait_time - elapsed))
                    elapsed += 0.1
            
            elif action == "KEY_PRESS":
                key = target_key or value
                # 包含 Enter 的按键（Enter、Ctrl+Enter 等）可能触发提交
                if key and "Enter" in key and self._network_monitor is not None:
                    self._network_monitor.pre_start()
                self._execute_keypress(key)
            
            elif action == "CLICK":
                # ===== 隐身模式：首次交互前执行人类行为预热 =====
                if self.stealth_mode and not getattr(self, '_page_warmed_up', False):
                    self._warmup_page_for_stealth()
                    self._page_warmed_up = True
                
                if target_key == "send_btn":
                    # 🆕 发送前启动网络监听（如果已配置）
                    if self._network_monitor is not None:
                        self._network_monitor.pre_start()
                    
                    self._execute_click_send_reliably(
                        selector=selector,
                        target_key=target_key,
                        optional=optional,
                    )
                else:
                    self._execute_click(selector, target_key, optional)
            
            elif action == "FILL_INPUT":
                
                prompt = context.get("prompt", "") if context else ""
                # v5.12：复用模式下可以提前启动（无额外 CDP session 风险）
                if self._network_monitor is not None:
                    self._network_monitor.pre_start()
                
                self._execute_fill(selector, prompt, target_key, optional)
            
            elif action in ("STREAM_WAIT", "STREAM_OUTPUT"):
                user_input = context.get("prompt", "") if context else ""
                
                # 🆕 优先尝试网络监听，失败则回退到 DOM 监听
                monitor_used = None
                
                if self._network_monitor is not None:
                    try:
                        logger.debug("[Executor] 尝试网络监听模式")
                        yield from self._network_monitor.monitor(
                            selector=selector,
                            user_input=user_input,
                            completion_id=self._completion_id
                        )
                        monitor_used = "network"
                    
                    except NetworkMonitorTimeout as e:
                        logger.warning(
                            f"[Executor] 网络监听超时，回退到 DOM 模式: {e}"
                        )
                        # 回退到 DOM 监听
                        yield from self._stream_monitor.monitor(
                            selector=selector,
                            user_input=user_input,
                            completion_id=self._completion_id
                        )
                        monitor_used = "dom_fallback"
                    
                    except NetworkMonitorError as e:
                        logger.error(
                            f"[Executor] 网络监听错误，回退到 DOM 模式: {e}"
                        )
                        # 回退到 DOM 监听
                        yield from self._stream_monitor.monitor(
                            selector=selector,
                            user_input=user_input,
                            completion_id=self._completion_id
                        )
                        monitor_used = "dom_fallback"
                
                else:
                    # 未配置网络监听，直接使用 DOM 监听
                    yield from self._stream_monitor.monitor(
                        selector=selector,
                        user_input=user_input,
                        completion_id=self._completion_id
                    )
                    monitor_used = "dom"
                
                if monitor_used:
                    logger.debug(f"[Executor] 监听完成 (mode={monitor_used})")
            
            else:
                logger.debug(f"未知动作: {action}")
        
        except ElementNotFoundError as e:
            if not optional:
                yield self.formatter.pack_error(f"元素未找到: {str(e)}")
                raise
        
        except Exception as e:
            logger.error(f"步骤执行失败 [{action}]: {e}")
            if not optional:
                yield self.formatter.pack_error(f"执行失败: {str(e)}")
                raise
    
    def _execute_keypress(self, key: str):
        """执行按键操作（隐身模式人类化时序）"""
        if self._check_cancelled():
            return
       
        
        if self.stealth_mode:
            self.tab.actions.key_down(key)
            time.sleep(random.uniform(0.05, 0.13))
            self.tab.actions.key_up(key)
        else:
            self.tab.actions.key_down(key).key_up(key)
        
        self._smart_delay(0.1, 0.2)
    
    def _execute_click(self, selector: str, target_key: str, optional: bool):
        """执行点击操作（v5.7 隐身模式人类化点击）"""
        if self._check_cancelled():
            return
        
        ele = self.finder.find_with_fallback(selector, target_key)
        
        if ele:
            try:
                if self.stealth_mode:
                    # 发送按钮前额外犹豫（50% 概率，带微漂移）
                    if target_key == "send_btn" and random.random() < 0.5:
                        hesitate = random.uniform(0.5, 1.2)
                        logger.debug(f"[STEALTH] 发送前犹豫 {hesitate:.2f}s")
                        self._idle_wait(hesitate)
                    
                    # 🆕 人类化点击（平滑移动 + CDP mousedown/mouseup 带间隔）
                    self._stealth_click_element(ele)
                else:
                    if self._check_cancelled():
                        return
                    ele.click()
                
                self._smart_delay(
                    BrowserConstants.ACTION_DELAY_MIN,
                    BrowserConstants.ACTION_DELAY_MAX
                )
            
            except Exception as click_err:
                logger.debug(f"点击异常: {click_err}")
                if target_key == "send_btn":
                    logger.warning(f"[CLICK] 发送按钮点击失败，降级到 Enter 键: {click_err}")
                    self._execute_keypress("Enter")
                elif self.stealth_mode:
                    # 隐身模式下非发送按钮点击失败，向上抛出（不偷偷用 ele.click）
                    raise
        
        elif target_key == "send_btn":
            self._execute_keypress("Enter")
        
        elif not optional:
            raise ElementNotFoundError(f"点击目标未找到: {selector}")
    
    def _stealth_click_element(self, ele):
        """
        隐身模式人类化点击（v5.9 — 彻底消灭 ele.click() 降级路径）
        
        关键：
        - 所有路径均使用 cdp_precise_click（force=0.5），绝不降级到 ele.click()
        - 坐标获取失败时，尝试 JS 获取 getBoundingClientRect 作为最后手段
        - 若坐标完全无法获取，抛出异常由上层处理（而非偷偷用 ele.click() 触发 CF）
        """
        if self._check_cancelled():
            return
        
        # 1. 获取元素坐标（多重尝试）
        target = self._get_element_viewport_pos(ele)
        
        if target is None:
            # 最后手段：通过 JS 获取坐标（仅在原生属性全部失败时）
            try:
                rect = ele.run_js(
                    "const r = this.getBoundingClientRect();"
                    "return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}"
                )
                if rect and rect.get('x') and rect.get('y'):
                    target = (int(rect['x']), int(rect['y']))
                    logger.debug(f"[STEALTH] 原生属性获取坐标失败，JS getBoundingClientRect 获取: {target}")
            except Exception as e:
                logger.debug(f"[STEALTH] JS 坐标获取也失败: {e}")
        
        if target is None:
            # 🔴 绝不降级到 ele.click()，抛出异常
            raise Exception("[STEALTH] 无法获取元素坐标，拒绝使用 ele.click()（会触发 CF）")
        
        # 随机偏移（不精确命中中心）
        click_x = target[0] + random.randint(-6, 6)
        click_y = target[1] + random.randint(-4, 4)
        
        # 2. 平滑移动鼠标到目标
        if self._mouse_pos is not None:
            self._mouse_pos = smooth_move_mouse(
                tab=self.tab,
                from_pos=self._mouse_pos,
                to_pos=(click_x, click_y),
                check_cancelled=self._check_cancelled
            )
        else:
            from app.utils.human_mouse import _dispatch_mouse_move
            _dispatch_mouse_move(self.tab, click_x, click_y)
            self._mouse_pos = (click_x, click_y)
        
        if self._check_cancelled():
            return
        
        # 3. 短暂停顿（模拟"确认要点击"）
        time.sleep(random.uniform(0.05, 0.15))
        
        # 4. 精确 CDP 点击（含 force=0.5 修复）
        success = cdp_precise_click(
            tab=self.tab,
            x=click_x,
            y=click_y,
            check_cancelled=self._check_cancelled
        )
        
        if not success:
            # 🔴 CDP 点击失败也不降级到 ele.click()，而是重试一次
            logger.warning("[STEALTH] CDP 精确点击失败，重试一次...")
            time.sleep(random.uniform(0.1, 0.3))
            success = cdp_precise_click(
                tab=self.tab,
                x=click_x,
                y=click_y,
                check_cancelled=self._check_cancelled
            )
            if not success:
                raise Exception("[STEALTH] CDP 精确点击两次均失败，拒绝降级到 ele.click()")
        
        # 更新鼠标位置
        self._mouse_pos = (click_x, click_y)
        
        logger.debug(f"[STEALTH] 人类化点击完成: ({click_x}, {click_y})")
    
    # ================= 可靠发送 =================
    
    def _execute_click_send_reliably(self, selector: str, target_key: str, optional: bool):
        """
        可靠发送（v5.6 隐身模式增强版）
        
        - 隐身模式：零 JS 注入，盲等待+重试
        - 普通模式：保持 JS 检查逻辑
        """
        if self._check_cancelled():
            return

        # ===== 隐身模式：无 JS 注入路径 =====
        if self.stealth_mode:
            self._execute_click_send_stealth(selector, target_key, optional)
            return

        # ===== 普通模式：原有逻辑 =====
        max_wait = getattr(BrowserConstants, "IMAGE_SEND_MAX_WAIT", 12.0)
        retry_interval = getattr(BrowserConstants, "IMAGE_SEND_RETRY_INTERVAL", 0.6)

        before_len = self._safe_get_input_len_by_key("input_box")
        self._execute_click(selector, target_key, optional)

        time.sleep(0.25)
        after_len = self._safe_get_input_len_by_key("input_box")

        if self._is_send_success(before_len, after_len):
            logger.info("发送成功")
            return

        logger.warning(f"[SEND] 发送未成功，进入重试窗口 max_wait={max_wait}s")

        elapsed = 0.0
        while elapsed < max_wait:
            if self._check_cancelled():
                return

            step = min(retry_interval, max_wait - elapsed)
            time.sleep(step)
            elapsed += step

            self._execute_click(selector, target_key, optional)

            time.sleep(0.25)
            new_len = self._safe_get_input_len_by_key("input_box")

            if self._is_send_success(after_len, new_len) or self._is_send_success(before_len, new_len):
                logger.info(f"发送成功 (重试{elapsed:.1f}s)")
                return

            after_len = new_len

        logger.error("[SEND] 发送重试超时")
        if not optional:
            raise WorkflowError("send_btn_click_failed_due_to_uploading")

    def _execute_click_send_stealth(self, selector: str, target_key: str, optional: bool):
        """
        隐身模式发送（零 JS 注入）
        
        - 无图片：直接点击
        - 有图片：盲等待+重试
        """
        has_images = False
        if hasattr(self, '_context') and self._context:
            has_images = bool(self._context.get('images'))
        
        if not has_images:
            self._execute_click(selector, target_key, optional)
            logger.info("[STEALTH] 发送完成（无图片）")
            return
        
        max_wait = getattr(BrowserConstants, 'STEALTH_SEND_IMAGE_WAIT', 8.0)
        retry_interval = getattr(BrowserConstants, 'STEALTH_SEND_IMAGE_RETRY_INTERVAL', 1.5)
        
        logger.info(f"[STEALTH] 有图片，发送后等待上传 (max_wait={max_wait}s)")
        
        self._execute_click(selector, target_key, optional)
        
        elapsed = 0.0
        retry_count = 0
        while elapsed < max_wait:
            if self._check_cancelled():
                return
            
            wait_step = min(retry_interval, max_wait - elapsed)
            wait_step = wait_step * random.uniform(0.8, 1.2)
            time.sleep(wait_step)
            elapsed += wait_step
            
            retry_count += 1
            try:
                self._execute_click(selector, target_key, True)
                logger.debug(f"[STEALTH] 发送重试 #{retry_count} (elapsed={elapsed:.1f}s)")
            except Exception:
                pass
        
        logger.info(f"[STEALTH] 发送完成（图片模式，重试 {retry_count} 次）")
    
    def _safe_get_input_len_by_key(self, target_key: str) -> int:
        """读取输入框当前长度"""
        try:
            ele = None
            try:
                ele = self.tab.run_js("return document.activeElement")
            except Exception:
                ele = None

            if ele:
                n = self.tab.run_js("""
                    try {
                        const el = arguments[0];
                        const tag = (el.tagName || '').toLowerCase();
                        if (tag === 'textarea' || tag === 'input') return (el.value || '').length;
                        if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') return (el.innerText || '').length;
                        return (el.textContent || '').length;
                    } catch(e){ return 0; }
                """, ele)
                return int(n) if n is not None else 0

            return 0
        except Exception:
            return 0
    
    def _is_send_success(self, before_len: int, after_len: int) -> bool:
        """判断是否发送成功"""
        try:
            if after_len == 0 and before_len > 0:
                return True
            if before_len <= 0:
                return False
            if after_len <= int(before_len * 0.4):
                return True
            return False
        except Exception:
            return False
            # ================= 隐身模式页面预热 =================
    
    def _warmup_page_for_stealth(self):
        """
        页面预热（v5.8 — 简化版，降低行为指纹风险）
        
        改进：
        - 修复死代码（_dispatch_mouse_move = None 覆盖导入）
        - 减少随机扫视次数（1-2 次，真实用户打开熟悉页面不会大量扫视）
        - 移除随机滚动（在已登录的对话页面滚动不自然）
        - 保留微漂移（等待期间的手部抖动仍有价值）
        """
        logger.debug("[STEALTH] 执行页面预热")
        
        try:
            from app.utils.human_mouse import _dispatch_mouse_move
            
            vw, vh = self._get_viewport_size()
            
            # 初始化鼠标位置（视口中上部，模拟"刚把鼠标放到页面"）
            init_x = vw // 2 + random.randint(-80, 80)
            init_y = int(vh * 0.3) + random.randint(-40, 40)
            self._mouse_pos = (init_x, init_y)
            _dispatch_mouse_move(self.tab, init_x, init_y)
            
            # 短暂停顿（模拟"看到页面内容"）
            self._idle_wait(random.uniform(0.4, 0.9))
            
            if self._check_cancelled():
                return
            
            # 1-2 次轻微移动（模拟目光扫过，不是大幅扫视）
            move_count = random.randint(1, 2)
            for i in range(move_count):
                if self._check_cancelled():
                    return
                
                # 小幅移动（不超过视口 30%）
                dx = random.randint(-int(vw * 0.15), int(vw * 0.15))
                dy = random.randint(-int(vh * 0.12), int(vh * 0.12))
                target_x = max(50, min(vw - 50, self._mouse_pos[0] + dx))
                target_y = max(50, min(vh - 50, self._mouse_pos[1] + dy))
                
                self._mouse_pos = smooth_move_mouse(
                    tab=self.tab,
                    from_pos=self._mouse_pos,
                    to_pos=(target_x, target_y),
                    check_cancelled=self._check_cancelled
                )
                
                # 微漂移停留
                self._idle_wait(random.uniform(0.3, 0.6))
            
            # 最后停顿
            self._idle_wait(random.uniform(0.3, 0.7))
            
            logger.debug(f"[STEALTH] 页面预热完成（{move_count} 次移动）")
            
        except Exception as e:
            logger.debug(f"[STEALTH] 页面预热异常（可忽略）: {e}")
    
    # ================= 输入框填充 =================
    
    def _execute_fill(self, selector: str, text: str, target_key: str, optional: bool):
        """填充输入框（v5.7 隐身增强版）"""
        if self._check_cancelled():
            return

        ele = self.finder.find_with_fallback(selector, target_key)
        if not ele:
            if not optional:
                raise ElementNotFoundError("找不到输入框")
            return

        # 🆕 隐身模式：人类化点击聚焦输入框 + 剪贴板粘贴
        if self.stealth_mode:
            self._stealth_click_element(ele)
            time.sleep(random.uniform(0.1, 0.25))
            self._text_handler.fill_via_clipboard_no_click(ele, text)
        else:
            self._text_handler.fill_via_js(ele, text)   
        
        # 粘贴图片
        if hasattr(self, '_context') and self._context:
            images = self._context.get('images', [])
            if images:
                self._image_handler.paste_images(images)
        
        # ===== 隐身模式：粘贴后模拟"人类阅读/检查"延迟（带微漂移）=====
        if self.stealth_mode and len(text) > 0:
            base_delay = random.uniform(1.0, 2.0)
            extra_per_chunk = len(text) / 5000.0
            extra_delay = extra_per_chunk * random.uniform(0.3, 0.6)
            total_review = min(base_delay + extra_delay, 3.0)
            
            logger.debug(f"[STEALTH] 粘贴后阅读延迟 {total_review:.1f}s (文本长度={len(text)})")
            
            # 🆕 等待期间保持微漂移（消灭"事件沙漠"）
            self._idle_wait(total_review)


__all__ = ['WorkflowExecutor']