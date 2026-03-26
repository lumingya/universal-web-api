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


# ================= 图片输入处理器 =================

class ImageInputHandler:
    """图片输入处理器"""
    
    def __init__(self, tab, stealth_mode: bool, smart_delay_fn, check_cancelled_fn,
                 attachment_monitor=None):
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
        self._recent_image_upload_at = 0.0

    def has_recent_attachment_upload(self, window: float = 45.0) -> bool:
        """Whether this request recently attached at least one image."""
        ts = float(getattr(self, "_recent_image_upload_at", 0.0) or 0.0)
        return ts > 0 and (time.time() - ts) <= window
    
    def paste_images(self, image_paths: List[str]):
        """
        粘贴多张图片到输入框
        
        策略：
        - 逐张复制到剪贴板 → Ctrl+V
        - 等待上传完成后再粘贴下一张
        """
        if not image_paths:
            return
        self._recent_image_upload_at = 0.0
        
        logger.debug(f"[IMAGE] 开始粘贴 {len(image_paths)} 张图片")
        
        for idx, img_path in enumerate(image_paths):
            if self._check_cancelled():
                logger.info("[IMAGE] 图片粘贴被取消")
                return
            
            logger.debug(f"[IMAGE] 粘贴第 {idx + 1}/{len(image_paths)} 张: {img_path}")
            
            success = self._paste_single_image(img_path, idx + 1)
            
            if not success:
                logger.warning(f"[IMAGE] 第 {idx + 1} 张粘贴失败，继续下一张")
            else:
                self._recent_image_upload_at = time.time()
            
            # 图片间隔（给网站时间处理上传）
            if idx < len(image_paths) - 1:
                self._smart_delay(0.5, 1.0)
        
        logger.debug(f"[IMAGE] 图片粘贴完成")
    
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
        
        try:
            # 等待输入框就绪
            self._smart_delay(0.1, 0.2)
            
            if self._check_cancelled():
                return False
            
            # 剪贴板操作加锁
            with clipboard_lock:
                # 复制图片到剪贴板
                if not copy_image_to_clipboard(image_path):
                    logger.error(f"[IMAGE] 复制到剪贴板失败: {image_path}")
                    return False
                
                # 等待剪贴板数据就绪
                time.sleep(0.1)
                
                if self._attachment_monitor is not None:
                    self._attachment_monitor.begin_tracking()

                # Ctrl+V 粘贴
                logger.debug(f"[IMAGE] 执行 Ctrl+V (图片 {index})")
                self.tab.actions.key_down('Control').key_down('V').key_up('V').key_up('Control')

                # 等待粘贴完成
                time.sleep(0.3)

            if self._attachment_monitor is not None:
                result = self._attachment_monitor.wait_until_ready(
                    require_observed=True,
                    require_send_enabled=False,
                    accept_existing=False,
                    start_new_tracking=False,
                    max_wait=getattr(BrowserConstants, "ATTACHMENT_READY_MAX_WAIT", 20.0),
                    poll_interval=getattr(BrowserConstants, "ATTACHMENT_READY_CHECK_INTERVAL", 0.25),
                    stable_window=getattr(BrowserConstants, "ATTACHMENT_READY_STABLE_WINDOW", 0.8),
                    label=f"image-paste-{index}",
                )
                if not result.get("success"):
                    logger.warning(f"[IMAGE] Image #{index} upload was not confirmed: {result}")
                    return False
            else:
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
        
        except Exception as e:
            logger.error(f"[IMAGE] 粘贴异常: {e}")
            return False


__all__ = ['ImageInputHandler']
