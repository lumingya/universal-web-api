"""
app/core/workflow/image_input.py - 图片输入处理

职责：
- Windows 上保留原生图片剪贴板 + 主修饰键粘贴
- 非 Windows 平台优先走网页原生上传入口
- 等待上传完成
"""

import base64
import json
import mimetypes
import os
import time
from typing import List

from app.core.config import logger, BrowserConstants
from app.core.tab_pool import get_clipboard_lock
from app.utils.image_handler import copy_image_to_clipboard
from app.utils.platform import get_primary_modifier_key
from app.utils.system_clipboard import supports_native_image_clipboard
from .attachment_monitor import AttachmentMonitor


class ImageInputHandler:
    """图片输入处理器"""

    def __init__(
        self,
        tab,
        stealth_mode: bool,
        smart_delay_fn,
        check_cancelled_fn,
        attachment_monitor=None,
        focus_input_fn=None,
        selectors=None,
    ):
        self.tab = tab
        self.stealth_mode = stealth_mode
        self._smart_delay = smart_delay_fn
        self._check_cancelled = check_cancelled_fn
        self._attachment_monitor = attachment_monitor
        self._focus_input = focus_input_fn
        self._selectors = selectors or {}
        self._primary_modifier = get_primary_modifier_key()
        self._recent_image_upload_at = 0.0

    def has_recent_attachment_upload(self, window: float = 45.0) -> bool:
        """Whether this request recently attached at least one image."""
        ts = float(getattr(self, "_recent_image_upload_at", 0.0) or 0.0)
        return ts > 0 and (time.time() - ts) <= window

    def _press_primary_combo(self, key: str):
        self.tab.actions.key_down(self._primary_modifier).key_down(key).key_up(key).key_up(self._primary_modifier)

    def _get_selector_value(self, key: str) -> str:
        value = self._selectors.get(key, "")
        return str(value or "").strip()

    def _find_first_element(self, selector: str, timeout: float = 1.5):
        if not selector:
            return None
        try:
            return self.tab.ele(selector, timeout=timeout)
        except Exception as e:
            logger.debug(f"[IMAGE] 查找元素失败 {selector}: {e}")
            return None

    def _find_elements(self, selector: str, timeout: float = 1.5) -> list:
        if not selector:
            return []
        try:
            return list(self.tab.eles(selector, timeout=timeout) or [])
        except Exception as e:
            logger.debug(f"[IMAGE] 查找元素列表失败 {selector}: {e}")
            return []

    def _list_file_inputs(self, selector: str = "") -> list:
        if selector:
            return self._find_elements(selector, timeout=1.5)

        try:
            return list(self.tab.eles('css:input[type="file"]', timeout=0.8) or [])
        except Exception as e:
            logger.debug(f"[IMAGE] 查找通用 file input 失败: {e}")
            return []

    def _get_element_file_count(self, file_input) -> int:
        try:
            count = file_input.run_js("return this.files ? this.files.length : 0;")
            return max(0, int(count or 0))
        except Exception:
            return 0

    def _upload_file_via_input(self, filepath: str, selector: str = "") -> bool:
        candidates = self._list_file_inputs(selector)
        if not candidates:
            return False

        for index, file_input in enumerate(candidates, 1):
            try:
                if file_input.attr("disabled") is not None:
                    continue

                file_input.input(filepath)
                try:
                    file_input.run_js(
                        """
                        this.dispatchEvent(new Event('input', { bubbles: true }));
                        this.dispatchEvent(new Event('change', { bubbles: true }));
                        """
                    )
                except Exception:
                    pass

                selected_count = self._get_element_file_count(file_input)
                if selected_count > 0:
                    logger.debug(
                        f"[IMAGE] 已通过 file input 上传图片 "
                        f"(candidate={index}, files={selected_count})"
                    )
                    return True
            except Exception as e:
                logger.debug(f"[IMAGE] file input #{index} 上传失败: {e}")

        return False

    def _upload_file_via_drop_zone(self, filepath: str, selector: str) -> bool:
        zone = self._find_first_element(selector, timeout=1.5)
        if not zone:
            return False

        try:
            with open(filepath, "rb") as f:
                raw = f.read()
        except Exception as e:
            logger.error(f"[IMAGE] 读取图片失败: {e}")
            return False

        filename = os.path.basename(filepath)
        mime_type = mimetypes.guess_type(filepath)[0] or "image/png"
        b64_data = base64.b64encode(raw).decode("ascii")
        escaped_name = json.dumps(filename)
        escaped_mime = json.dumps(mime_type)
        escaped_data = json.dumps(b64_data)

        js = f"""
        return (async function() {{
            try {{
                const fileName = {escaped_name};
                const mimeType = {escaped_mime};
                const b64 = {escaped_data};
                const binary = atob(b64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {{
                    bytes[i] = binary.charCodeAt(i);
                }}

                const file = new File([bytes], fileName, {{
                    type: mimeType,
                    lastModified: Date.now()
                }});

                const dt = new DataTransfer();
                dt.items.add(file);

                const target = this;
                try {{
                    target.scrollIntoView({{ block: 'center', inline: 'center' }});
                }} catch (e) {{}}

                for (const eventName of ['dragenter', 'dragover', 'drop']) {{
                    const event = new DragEvent(eventName, {{
                        bubbles: true,
                        cancelable: true,
                        dataTransfer: dt
                    }});
                    target.dispatchEvent(event);
                }}

                return true;
            }} catch (error) {{
                console.error('image drop upload failed', error);
                return false;
            }}
        }}).call(this);
        """

        try:
            ok = bool(zone.run_js(js))
            if ok:
                logger.info("[IMAGE] 已通过拖拽区域上传图片")
            return ok
        except Exception as e:
            logger.debug(f"[IMAGE] drop zone 上传失败: {e}")
            return False

    def _click_upload_button_if_configured(self) -> bool:
        selector = self._get_selector_value("upload_btn")
        if not selector:
            return False

        button = self._find_first_element(selector, timeout=1.5)
        if not button:
            logger.debug("[IMAGE] 已配置 upload_btn，但当前页面未找到")
            return False

        try:
            button.click()
            self._smart_delay(0.06, 0.12)
            logger.info("[IMAGE] 已点击上传按钮")
            return True
        except Exception as e:
            logger.debug(f"[IMAGE] 点击上传按钮失败: {e}")
            return False

    def _upload_image_via_site_targets(self, filepath: str) -> bool:
        configured_file_input = self._get_selector_value("file_input")
        configured_drop_zone = self._get_selector_value("drop_zone")

        if configured_file_input and self._upload_file_via_input(filepath, configured_file_input):
            return True

        if self._upload_file_via_input(filepath):
            return True

        if configured_drop_zone and self._upload_file_via_drop_zone(filepath, configured_drop_zone):
            return True

        clicked_upload_button = self._click_upload_button_if_configured()
        if clicked_upload_button:
            time.sleep(0.35)
            if configured_file_input and self._upload_file_via_input(filepath, configured_file_input):
                return True
            if self._upload_file_via_input(filepath):
                return True
            if configured_drop_zone and self._upload_file_via_drop_zone(filepath, configured_drop_zone):
                return True

        return False

    def _wait_for_attachment_ready(
        self,
        index: int,
        *,
        quick_probe_wait: float,
        quick_probe_idle_timeout: float,
        quick_probe_hard_timeout: float,
    ) -> bool:
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
                logger.debug(f"[IMAGE] 第 {index} 张上传完成")
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
                    logger.debug(f"[IMAGE] 第 {index} 张上传完成")
                    return True
                logger.warning(
                    f"[IMAGE] Image #{index} upload was not confirmed: "
                    f"{AttachmentMonitor.summarize(result)}"
                )
                return False

            logger.warning(
                f"[IMAGE] 图片 {index} 上传后未观察到任何附件活动: "
                f"{AttachmentMonitor.summarize(probe)}"
            )
            return False

        upload_wait = 0.8 if self.stealth_mode else 0.5
        elapsed = 0.0
        step = 0.1
        while elapsed < upload_wait:
            if self._check_cancelled():
                return False
            time.sleep(step)
            elapsed += step
        logger.debug(f"[IMAGE] 第 {index} 张上传完成")
        return True

    def paste_images(self, image_paths: List[str]) -> bool:
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
            if idx < len(image_paths) - 1:
                self._smart_delay(0.5, 1.0)

        logger.debug("[IMAGE] 图片粘贴完成")
        return True

    def _paste_single_image(self, image_path: str, index: int) -> bool:

        quick_probe_wait = 3.0
        quick_probe_idle_timeout = 2.5
        quick_probe_hard_timeout = 3.0
        max_attempts = 2

        try:
            self._smart_delay(0.1, 0.2)
            if self._check_cancelled():
                return False

            if not supports_native_image_clipboard():
                if callable(self._focus_input):
                    try:
                        self._focus_input()
                    except Exception as e:
                        logger.debug(f"[IMAGE] 网页原生上传前聚焦输入框异常: {e}")

                if self._attachment_monitor is not None:
                    self._attachment_monitor.begin_tracking()

                uploaded = self._upload_image_via_site_targets(image_path)
                if not uploaded:
                    logger.error(
                        f"[IMAGE] 当前平台不支持原生图片剪贴板，且未找到可用的 file input / drop_zone: {image_path}"
                    )
                    return False

                return self._wait_for_attachment_ready(
                    index,
                    quick_probe_wait=quick_probe_wait,
                    quick_probe_idle_timeout=quick_probe_idle_timeout,
                    quick_probe_hard_timeout=quick_probe_hard_timeout,
                )

            clipboard_lock = get_clipboard_lock()

            for attempt in range(1, max_attempts + 1):
                with clipboard_lock:
                    if not copy_image_to_clipboard(image_path):
                        logger.warning(f"[IMAGE] 复制到剪贴板失败，回退网页原生上传: {image_path}")
                        break

                    time.sleep(0.1)

                    if callable(self._focus_input):
                        try:
                            focused = bool(self._focus_input())
                        except Exception as e:
                            focused = False
                            logger.debug(f"[IMAGE] 粘贴前聚焦输入框异常: {e}")
                        if not focused:
                            logger.warning(
                                f"[IMAGE] 粘贴前未能确认输入框焦点，继续尝试 {self._primary_modifier}+V "
                                f"(图片 {index}, attempt {attempt})"
                            )

                    if self._attachment_monitor is not None:
                        self._attachment_monitor.begin_tracking()

                    logger.debug(
                        f"[IMAGE] 执行 {self._primary_modifier}+V "
                        f"(图片 {index}, attempt {attempt}/{max_attempts})"
                    )
                    self._press_primary_combo("V")
                    time.sleep(0.3)

                if self._wait_for_attachment_ready(
                    index,
                    quick_probe_wait=quick_probe_wait,
                    quick_probe_idle_timeout=quick_probe_idle_timeout,
                    quick_probe_hard_timeout=quick_probe_hard_timeout,
                ):
                    return True

                if attempt < max_attempts:
                    self._smart_delay(0.2, 0.4)
                    continue

                break

            if self._attachment_monitor is not None:
                self._attachment_monitor.begin_tracking()

            if self._upload_image_via_site_targets(image_path):
                logger.info(f"[IMAGE] 第 {index} 张改用网页原生上传入口")
                return self._wait_for_attachment_ready(
                    index,
                    quick_probe_wait=quick_probe_wait,
                    quick_probe_idle_timeout=quick_probe_idle_timeout,
                    quick_probe_hard_timeout=quick_probe_hard_timeout,
                )

            return False

        except Exception as e:
            logger.error(f"[IMAGE] 粘贴异常: {e}")
            return False


__all__ = ["ImageInputHandler"]
