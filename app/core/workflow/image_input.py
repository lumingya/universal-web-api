"""
app/core/workflow/image_input.py - 图片粘贴处理

职责：
- 图片复制到剪贴板
- Ctrl+V 粘贴图片
- 等待上传完成
"""

import time
from typing import List

from app.core.config import logger, BrowserConstants
from app.core.tab_pool import get_clipboard_lock
from .attachment_monitor import AttachmentMonitor


# ================= 图片输入处理器 =================

class ImageInputHandler:
    """图片输入处理器"""
    
    def __init__(self, tab, stealth_mode: bool, smart_delay_fn, check_cancelled_fn,
                 attachment_monitor=None,
                 focus_input_fn=None):
        """
        Args:
            tab: 浏览器标签页
            stealth_mode: 是否隐身模式
            smart_delay_fn: 智能延迟函数
            check_cancelled_fn: 取消检查函数
        """
        self.tab = tab
        self.stealth_mode = stealth_mode
        self._smart_delay = smart_delay_fn
        self._check_cancelled = check_cancelled_fn
        self._attachment_monitor = attachment_monitor
        self._focus_input = focus_input_fn
        self._recent_image_upload_at = 0.0

    def has_recent_attachment_upload(self, window: float = 45.0) -> bool:
        """Whether this request recently attached at least one image."""
        ts = float(getattr(self, "_recent_image_upload_at", 0.0) or 0.0)
        return ts > 0 and (time.time() - ts) <= window
    
    def paste_images(self, image_paths: List[str]) -> bool:
        """
        粘贴多张图片到输入框
        
        策略：
        - 逐张复制到剪贴板 → Ctrl+V
        - 等待上传完成后再粘贴下一张
        """
        if not image_paths:
            return True
        self._recent_image_upload_at = 0.0
        
        logger.debug(f"[IMAGE] 开始粘贴 {len(image_paths)} 张图片")
        
        for idx, img_path in enumerate(image_paths):
            if self._check_cancelled():
                logger.info("[IMAGE] 图片粘贴被取消")
                return False
            
            logger.debug(f"[IMAGE] 粘贴第 {idx + 1}/{len(image_paths)} 张: {img_path}")
            
            success = self._paste_single_image(img_path, idx + 1)
            
            if not success:
                logger.warning(f"[IMAGE] 第 {idx + 1} 张粘贴失败，停止后续图片发送")
                return False

            self._recent_image_upload_at = time.time()
            
            # 图片间隔（给网站时间处理上传）
            if idx < len(image_paths) - 1:
                self._smart_delay(0.5, 1.0)
        
        logger.debug(f"[IMAGE] 图片粘贴完成")
        return True
    
    def _paste_single_image(self, image_path: str, index: int) -> bool:
        """
        粘贴单张图片
        
        流程：
        1. 复制图片到剪贴板
        2. 聚焦输入框
        3. Ctrl+V 粘贴
        4. 等待上传完成
        """
        from app.utils.image_handler import copy_image_to_clipboard
        
        clipboard_lock = get_clipboard_lock()
        quick_probe_wait = 3.0
        quick_probe_idle_timeout = 2.5
        quick_probe_hard_timeout = 3.0
        max_attempts = 2
        
        try:
            # 等待输入框就绪
            self._smart_delay(0.1, 0.2)
            
            if self._check_cancelled():
                return False

            for attempt in range(1, max_attempts + 1):
                # 剪贴板操作加锁
                with clipboard_lock:
                    # 复制图片到剪贴板
                    if not copy_image_to_clipboard(image_path):
                        logger.error(f"[IMAGE] 复制到剪贴板失败: {image_path}")
                        return False
                    
                    # 等待剪贴板数据就绪
                    time.sleep(0.1)

                    if callable(self._focus_input):
                        try:
                            focused = bool(self._focus_input())
                        except Exception as e:
                            focused = False
                            logger.debug(f"[IMAGE] 粘贴前聚焦输入框异常: {e}")
                        if not focused:
                            logger.warning(
                                f"[IMAGE] 粘贴前未能确认输入框焦点，继续尝试 Ctrl+V (图片 {index}, attempt {attempt})"
                            )
                    
                    if self._attachment_monitor is not None:
                        self._attachment_monitor.begin_tracking()

                    logger.debug(f"[IMAGE] 执行 Ctrl+V (图片 {index}, attempt {attempt}/{max_attempts})")
                    self.tab.actions.key_down('Control').key_down('V').key_up('V').key_up('Control')

                    # 给粘贴事件一个极短的进入时间
                    time.sleep(0.3)

                if self._attachment_monitor is not None:
                    poll_interval = getattr(BrowserConstants, "ATTACHMENT_READY_CHECK_INTERVAL", 0.25)
                    stable_window = getattr(BrowserConstants, "ATTACHMENT_READY_STABLE_WINDOW", 0.8)

                    probe = self._attachment_monitor.wait_until_ready(
                        require_observed=True,
                        require_send_enabled=False,
                        accept_existing=False,
                        start_new_tracking=False,
                        max_wait=quick_probe_wait,
                        poll_interval=poll_interval,
                        stable_window=stable_window,
                        idle_timeout=quick_probe_idle_timeout,
                        hard_max_wait=quick_probe_hard_timeout,
                        label=f"image-paste-{index}-probe",
                    )
                    if probe.get("success"):
                        logger.debug(f"[IMAGE] 第 {index} 张粘贴完成")
                        return True

                    saw_activity = (
                        bool(probe.get("activitySeen"))
                        or bool(probe.get("attachmentObserved"))
                        or AttachmentMonitor._attachment_present(probe)
                    )
                    if saw_activity:
                        result = self._attachment_monitor.wait_until_ready(
                            require_observed=True,
                            require_send_enabled=False,
                            accept_existing=False,
                            start_new_tracking=False,
                            max_wait=getattr(BrowserConstants, "ATTACHMENT_READY_MAX_WAIT", 20.0),
                            poll_interval=poll_interval,
                            stable_window=stable_window,
                            label=f"image-paste-{index}",
                        )
                        if result.get("success"):
                            logger.debug(f"[IMAGE] 第 {index} 张粘贴完成")
                            return True
                        logger.warning(
                            f"[IMAGE] Image #{index} upload was not confirmed: "
                            f"{AttachmentMonitor.summarize(result)}"
                        )
                        return False

                    if attempt < max_attempts:
                        logger.warning(
                            f"[IMAGE] 图片 {index} 粘贴后未观察到任何附件活动，准备重试 "
                            f"({AttachmentMonitor.summarize(probe)})"
                        )
                        self._smart_delay(0.2, 0.4)
                        continue

                    logger.warning(
                        f"[IMAGE] 图片 {index} 粘贴后仍未观察到任何附件活动: "
                        f"{AttachmentMonitor.summarize(probe)}"
                    )
                    return False

                # Legacy fallback when the shared monitor is unavailable.
                upload_wait = 0.8 if self.stealth_mode else 0.5
                elapsed = 0
                step = 0.1
                while elapsed < upload_wait:
                    if self._check_cancelled():
                        return False
                    time.sleep(step)
                    elapsed += step
                logger.debug(f"[IMAGE] 第 {index} 张粘贴完成")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"[IMAGE] 粘贴异常: {e}")
            return False


__all__ = ['ImageInputHandler']
