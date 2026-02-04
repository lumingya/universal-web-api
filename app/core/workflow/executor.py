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
from typing import Generator, Dict, Any, Callable

from app.core.config import (
    logger,
    BrowserConstants,
    SSEFormatter,
    ElementNotFoundError,
    WorkflowError,
)
from app.core.elements import ElementFinder
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
                 stream_config: Dict = None):
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
        
        # 初始化输入处理器
        self._text_handler = TextInputHandler(
            tab=tab,
            stealth_mode=stealth_mode,
            smart_delay_fn=self._smart_delay,
            check_cancelled_fn=self._check_cancelled
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
                self._execute_keypress(target_key or value)
            
            elif action == "CLICK":
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
        """执行按键操作"""
        if self._check_cancelled():
            return
        self.tab.actions.key_down(key).key_up(key)
        self._smart_delay(0.1, 0.2)
    
    def _execute_click(self, selector: str, target_key: str, optional: bool):
        """执行点击操作（v5.5 增强版）"""
        if self._check_cancelled():
            return
        
        ele = self.finder.find_with_fallback(selector, target_key)
        
        if ele:
            try:
                if self.stealth_mode:
                    # 发送按钮前额外犹豫（40% 概率）
                    if target_key == "send_btn" and random.random() < 0.4:
                        hesitate = random.uniform(0.4, 0.9)
                        logger.debug(f"[STEALTH] 发送前犹豫 {hesitate:.2f}s")
                        elapsed = 0
                        while elapsed < hesitate:
                            if self._check_cancelled():
                                return
                            time.sleep(0.05)
                            elapsed += 0.05
                    
                    # 鼠标移动
                    try:
                        self.tab.actions.move_to(ele)
                        self._smart_delay(0.1, 0.25)
                    except Exception:
                        pass
                
                if self._check_cancelled():
                    return
                
                # 原生点击
                ele.click()
                self._smart_delay(
                    BrowserConstants.ACTION_DELAY_MIN,
                    BrowserConstants.ACTION_DELAY_MAX
                )
            
            except Exception as click_err:
                logger.debug(f"点击异常: {click_err}")
                if target_key == "send_btn":
                    self._execute_keypress("Enter")
        
        elif target_key == "send_btn":
            self._execute_keypress("Enter")
        
        elif not optional:
            raise ElementNotFoundError(f"点击目标未找到: {selector}")
    
    # ================= 可靠发送 =================
    
    def _execute_click_send_reliably(self, selector: str, target_key: str, optional: bool):
        """可靠发送：专门解决图片上传导致消息未发出的问题"""
        if self._check_cancelled():
            return

        max_wait = getattr(BrowserConstants, "IMAGE_SEND_MAX_WAIT", 12.0)
        retry_interval = getattr(BrowserConstants, "IMAGE_SEND_RETRY_INTERVAL", 0.6)

        # 点击前：记录输入框长度
        before_len = self._safe_get_input_len_by_key("input_box")

        # 第一次点击
        self._execute_click(selector, target_key, optional)

        # 快速判断一次
        time.sleep(0.25)
        after_len = self._safe_get_input_len_by_key("input_box")

        if self._is_send_success(before_len, after_len):
            logger.info("发送成功")
            return

        # 没成功：进入重试窗口
        logger.warning(f"[SEND] 发送未成功，进入重试窗口 max_wait={max_wait}s")

        elapsed = 0.0
        while elapsed < max_wait:
            if self._check_cancelled():
                return

            step = min(retry_interval, max_wait - elapsed)
            time.sleep(step)
            elapsed += step

            # 重试点击
            self._execute_click(selector, target_key, optional)

            time.sleep(0.25)
            new_len = self._safe_get_input_len_by_key("input_box")

            if self._is_send_success(after_len, new_len) or self._is_send_success(before_len, new_len):
                logger.info(f"发送成功 (重试{elapsed:.1f}s)")
                return

            after_len = new_len

        # 超时仍未成功
        logger.error("[SEND] 发送重试超时")
        if not optional:
            raise WorkflowError("send_btn_click_failed_due_to_uploading")
    
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
    
    # ================= 输入框填充 =================
    
    def _execute_fill(self, selector: str, text: str, target_key: str, optional: bool):
        """填充输入框（模式分离版）"""
        if self._check_cancelled():
            return

        ele = self.finder.find_with_fallback(selector, target_key)
        if not ele:
            if not optional:
                raise ElementNotFoundError("找不到输入框")
            return

        # 填充文本
        if self.stealth_mode:
            self._text_handler.fill_via_clipboard(ele, text)
        else:
            self._text_handler.fill_via_js(ele, text)
        
        # 粘贴图片
        if hasattr(self, '_context') and self._context:
            images = self._context.get('images', [])
            if images:
                self._image_handler.paste_images(images)


__all__ = ['WorkflowExecutor']