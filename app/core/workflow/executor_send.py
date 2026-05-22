"""
Send confirmation and attachment flow mixin for WorkflowExecutor.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from app.core.config import BrowserConstants, WorkflowError, logger
from .attachment_monitor import AttachmentMonitor


class WorkflowExecutorSendMixin:
    def _probe_attachment_readiness(self, send_selector: str = "") -> Dict[str, Any]:
        """Inspect whether attachments are still uploading and whether send looks available."""
        if self._attachment_monitor is not None:
            try:
                state = self._attachment_monitor.snapshot()
                if not isinstance(state, dict):
                    state = {}
                state = dict(state)
                phase_flags = AttachmentMonitor.derive_phase_flags(
                    state,
                    require_send_enabled=True,
                    require_attachment_present=self._get_attachment_monitor_flag(
                        "require_attachment_present",
                        False,
                    ),
                    require_upload_signal_before_ready=self._get_attachment_monitor_flag(
                        "require_upload_signal_before_ready",
                        False,
                    ),
                )
                state.update(phase_flags)
                state["ready"] = bool(phase_flags.get("upload_ready"))
                return state
            except Exception as e:
                logger.debug(f"[SEND] 附件状态探测失败: {e}")
                return {
                    "ok": False,
                    "attachmentCount": 0,
                    "pendingCount": 0,
                    "pendingText": False,
                    "sendFound": False,
                    "sendDisabled": False,
                    "sendBusy": False,
                    "upload_started": False,
                    "uploading": False,
                    "attachment_present": False,
                    "ready": True,
                }
        selector_json = json.dumps((send_selector or "").strip(), ensure_ascii=False)
        js = f"""
        return (function() {{
            try {{
                const sendSelector = {selector_json};
                const root = document.querySelector(
                    '.message-input-wrapper, .message-input-container, .chat-layout-input-container, '
                    + '#dropzone-container, form:has(button[type="submit"]), '
                    + '[class*="message-input"], [class*="input-container"], [class*="input-wrapper"]'
                );
                if (!root) {{
                    return {{
                        ok: true,
                        attachmentCount: 0,
                        pendingCount: 0,
                        pendingText: false,
                        sendFound: false,
                        sendDisabled: false,
                        ready: true,
                        skipped: 'no_input_root'
                    }};
                }}

                const attachmentSelectors = [
                    '.file-card-list',
                    '.fileitem-btn',
                    '.fileitem-file-name',
                    '.fileitem-file-name-text',
                    '.message-input-column-file',
                    '[class*="fileitem"]',
                    '[class*="image-preview"]',
                    '[data-testid*="attachment"]',
                    '[data-testid*="preview"]',
                    'img[src^="blob:"]',
                    'img[src^="data:image"]'
                ].join(',');

                const pendingSelectors = [
                    'progress',
                    '[role="progressbar"]',
                    '[aria-busy="true"]',
                    '[class*="uploading"]',
                    '[class*="pending"]'
                ].join(',');

                const attachmentCount = root.querySelectorAll(attachmentSelectors).length;
                const pendingCount = root.querySelectorAll(pendingSelectors).length;
                const rootText = String(root.innerText || '').toLowerCase();
                const pendingText = /上传中|处理中|loading|uploading|processing|preparing/.test(rootText);

                let sendBtn = null;
                if (sendSelector) {{
                    try {{
                        sendBtn = document.querySelector(sendSelector);
                    }} catch (e) {{}}
                }}

                const sendDisabled = !!sendBtn && (
                    !!sendBtn.disabled
                    || sendBtn.getAttribute('aria-disabled') === 'true'
                    || /disable(?:d)?|loading|uploading|sending/.test(String(sendBtn.className || '').toLowerCase())
                );

                return {{
                    ok: true,
                    attachmentCount,
                    pendingCount,
                    pendingText,
                    sendFound: !!sendBtn,
                    sendDisabled,
                    ready: pendingCount === 0 && !pendingText && (!sendBtn || !sendDisabled)
                }};
            }} catch (error) {{
                return {{
                    ok: false,
                    attachmentCount: 0,
                    pendingCount: 0,
                    pendingText: false,
                    sendFound: false,
                    sendDisabled: false,
                    ready: true,
                    error: String(error && error.message ? error.message : error)
                }};
            }}
        }})();
        """

        try:
            return self.tab.run_js(js) or {}
        except Exception as e:
            logger.debug(f"[SEND] 附件状态探测失败: {e}")
            return {
                "ok": False,
                "attachmentCount": 0,
                "pendingCount": 0,
                "pendingText": False,
                "sendFound": False,
                "sendDisabled": False,
                "upload_started": False,
                "uploading": False,
                "attachment_present": False,
                "ready": True,
            }

    def _recent_attachment_age_seconds(self) -> Optional[float]:
        """Seconds since the newest attachment upload completed, if known."""
        timestamps = []

        for handler, attr in (
            (getattr(self, "_text_handler", None), "_recent_file_upload_at"),
            (getattr(self, "_image_handler", None), "_recent_image_upload_at"),
        ):
            try:
                ts = float(getattr(handler, attr, 0.0) or 0.0)
            except Exception:
                ts = 0.0
            if ts > 0:
                timestamps.append(ts)

        if not timestamps:
            return None
        return max(0.0, time.time() - max(timestamps))

    def _wait_for_attachments_ready_before_send(self, send_selector: str = ""):
        """Wait for file/image uploads to settle before attempting submit."""
        if not self._should_wait_for_attachments_before_send():
            return

        if self._attachment_monitor is not None:
            max_wait = getattr(BrowserConstants, "ATTACHMENT_READY_MAX_WAIT", 20.0)
            check_interval = getattr(BrowserConstants, "ATTACHMENT_READY_CHECK_INTERVAL", 0.35)
            stable_window = getattr(BrowserConstants, "ATTACHMENT_READY_STABLE_WINDOW", 0.8)
            recent_attachment_age = self._recent_attachment_age_seconds()
            recent_file_upload = False
            recent_image_upload = False
            confirmed_file_upload = False
            try:
                recent_file_upload = bool(self._text_handler.has_recent_attachment_upload())
            except Exception:
                recent_file_upload = False
            try:
                recent_image_upload = bool(self._image_handler.has_recent_attachment_upload())
            except Exception:
                recent_image_upload = False
            if not recent_image_upload:
                context = getattr(self, "_context", None) or {}
                recent_image_upload = bool(context.get("images"))
            if recent_file_upload:
                try:
                    confirmed_file_upload = bool(self._text_handler.has_confirmed_upload_signal())
                except Exception:
                    confirmed_file_upload = False

            reuse_existing_tracking = recent_attachment_age is not None
            require_attachment_confirmation = recent_image_upload or (
                recent_file_upload and not confirmed_file_upload
            )
            require_send_enabled = True
            if recent_image_upload:
                # Arena 等站点在图片预览已就绪后仍可能短暂维持 disabled，
                # 这里放宽 gate，后续交给发送确认阶段判定是否真正发出。
                require_send_enabled = False
            if reuse_existing_tracking:
                logger.debug(
                    "[SEND] Recent attachment upload detected before submit; "
                    f"reusing existing attachment tracking (age={recent_attachment_age:.1f}s)"
                )
            if recent_file_upload and confirmed_file_upload and not recent_image_upload:
                logger.debug(
                    "[SEND] Recent file-paste upload was strongly confirmed; "
                    "send gate will only wait for pending/busy signals"
                )
            result = self._attachment_monitor.wait_until_ready(
                require_observed=require_attachment_confirmation,
                require_send_enabled=require_send_enabled,
                accept_existing=not require_attachment_confirmation,
                start_new_tracking=not reuse_existing_tracking,
                max_wait=max_wait,
                poll_interval=check_interval,
                stable_window=stable_window,
                require_attachment_present=require_attachment_confirmation,
                label="send-gate",
            )
            if result.get("success"):
                return

            continue_once = self._get_attachment_monitor_flag(
                "continue_once_on_unconfirmed_send",
                True,
            )
            if not continue_once:
                logger.warning(
                    "[SEND] Attachment readiness was not confirmed before submit; blocking send "
                    f"({AttachmentMonitor.summarize(result)})"
                )
                raise WorkflowError("attachment_ready_unconfirmed_before_send")
            logger.warning(
                "[SEND] Attachment readiness was not confirmed before submit; continuing once "
                f"({AttachmentMonitor.summarize(result)})"
            )
            return

        max_wait = getattr(BrowserConstants, "ATTACHMENT_READY_MAX_WAIT", 20.0)
        check_interval = getattr(BrowserConstants, "ATTACHMENT_READY_CHECK_INTERVAL", 0.35)
        settle_floor = getattr(BrowserConstants, "ATTACHMENT_POST_UPLOAD_SETTLE", 1.8)
        try:
            settle_floor = max(
                settle_floor,
                self._text_handler.get_post_upload_settle_seconds(settle_floor)
            )
        except Exception:
            pass

        upload_age = self._recent_attachment_age_seconds()
        if upload_age is not None and upload_age < settle_floor:
            remaining = settle_floor - upload_age
            logger.debug(f"[SEND] 附件刚上传完成，额外等待解析稳定 {remaining:.1f}s")
            elapsed = 0.0
            while elapsed < remaining:
                if self._check_cancelled():
                    return
                step = min(check_interval, remaining - elapsed)
                time.sleep(step)
                elapsed += step

        state = self._probe_attachment_readiness(send_selector)
        if state.get("ready", True):
            return

        logger.debug(
            "[SEND] 检测到附件仍在处理，发送前等待 "
            f"(attachments={state.get('attachmentCount', 0)}, "
            f"pending={state.get('pendingCount', 0)}, "
            f"send_disabled={state.get('sendDisabled', False)})"
        )

        elapsed = 0.0
        while elapsed < max_wait:
            if self._check_cancelled():
                return

            sleep_for = min(check_interval, max_wait - elapsed)
            time.sleep(sleep_for)
            elapsed += sleep_for

            state = self._probe_attachment_readiness(send_selector)
            if state.get("ready", True):
                logger.debug(
                    "[SEND] 附件已就绪，继续发送 "
                    f"(waited={elapsed:.1f}s, attachments={state.get('attachmentCount', 0)})"
                )
                return

        logger.warning(
            "[SEND] 等待附件就绪超时，继续尝试发送 "
            f"(attachments={state.get('attachmentCount', 0)}, "
            f"pending={state.get('pendingCount', 0)}, "
            f"send_disabled={state.get('sendDisabled', False)})"
        )

    def _should_wait_for_attachments_before_send(self) -> bool:
        """Only wait when this request actually attached files or images."""
        try:
            if self._text_handler.has_recent_attachment_upload():
                return True
        except Exception:
            pass

        try:
            if self._image_handler.has_recent_attachment_upload():
                return True
        except Exception:
            pass

        context = getattr(self, "_context", None) or {}
        return bool(context.get("images"))

    def _has_recent_attachment_upload(self) -> bool:
        """Whether the current turn recently attached files/images before sending."""
        try:
            if self._text_handler.has_recent_attachment_upload():
                return True
        except Exception:
            pass

        try:
            if self._image_handler.has_recent_attachment_upload():
                return True
        except Exception:
            pass

        context = getattr(self, "_context", None) or {}
        return bool(context.get("images"))

    def _get_send_confirmation_config(self) -> Dict[str, Any]:
        """Return the merged send confirmation strategy for the current site."""
        config = {
            "attachment_sensitivity": "medium",
            "post_click_observe_window": float(
                getattr(BrowserConstants, "SEND_POST_CLICK_OBSERVE_WINDOW", 1.8)
            ),
            "pre_retry_probe_window": 0.12,
            "retry_observe_window": float(
                getattr(BrowserConstants, "SEND_RETRY_OBSERVE_WINDOW", 0.9)
            ),
            "attachment_observe_window": float(
                getattr(BrowserConstants, "ATTACHMENT_SEND_OBSERVE_WINDOW", 6.0)
            ),
            "retry_action": "click_send_btn",
            "retry_key_combo": "Enter",
            "trust_network_activity": True,
            "trust_generating_indicator": True,
            "trust_send_disabled_with_input_shrink": True,
        }

        raw_config = {}
        if isinstance(self._stream_config, dict):
            raw_config = self._stream_config.get("send_confirmation", {}) or {}
        file_paste_config = self._get_file_paste_send_confirmation_config()
        if isinstance(file_paste_config, dict):
            raw_config = {
                **(raw_config if isinstance(raw_config, dict) else {}),
                **file_paste_config,
            }

        if isinstance(raw_config, dict):
            config.update(raw_config)

        return config

    def _get_raw_send_confirmation_config(self) -> Dict[str, Any]:
        """Return only the site-provided send confirmation overrides."""
        raw_config: Dict[str, Any] = {}
        if isinstance(self._stream_config, dict):
            legacy_config = self._stream_config.get("send_confirmation", {}) or {}
            if isinstance(legacy_config, dict):
                raw_config.update(legacy_config)
        file_paste_config = self._get_file_paste_send_confirmation_config()
        if isinstance(file_paste_config, dict):
            raw_config.update(file_paste_config)
        return raw_config

    def _get_send_confirmation_window(
        self,
        key: str,
        fallback: float,
        *,
        min_value: float = 0.0,
        max_value: Optional[float] = None,
        raw_only: bool = False,
    ) -> float:
        """Read a numeric send confirmation option with clamping."""
        config = self._get_raw_send_confirmation_config() if raw_only else self._get_send_confirmation_config()
        try:
            value = float(config.get(key, fallback))
        except (TypeError, ValueError):
            value = float(fallback)

        value = max(min_value, value)
        if max_value is not None:
            value = min(value, max_value)
        return value

    def _get_send_confirmation_flag(
        self,
        key: str,
        fallback: bool = True,
        *,
        raw_only: bool = False,
    ) -> bool:
        """Read a boolean send confirmation option."""
        config = self._get_raw_send_confirmation_config() if raw_only else self._get_send_confirmation_config()
        raw_value = config.get(key, fallback)

        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            lowered = raw_value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
        return bool(raw_value)

    def _get_send_confirmation_int(
        self,
        key: str,
        fallback: int,
        *,
        min_value: int = 0,
        max_value: Optional[int] = None,
        raw_only: bool = False,
    ) -> int:
        config = self._get_raw_send_confirmation_config() if raw_only else self._get_send_confirmation_config()
        try:
            value = int(config.get(key, fallback))
        except (TypeError, ValueError):
            value = int(fallback)
        value = max(min_value, value)
        if max_value is not None:
            value = min(value, max_value)
        return value

    def _get_send_retry_action_config(self) -> Dict[str, str]:
        """Resolve the action used when automatic send retry is triggered."""
        config = self._get_send_confirmation_config()
        retry_action = str(config.get("retry_action") or "click_send_btn").strip().lower()
        if retry_action not in {"click_send_btn", "key_press"}:
            retry_action = "click_send_btn"

        retry_key_combo = str(config.get("retry_key_combo") or "Enter").strip() or "Enter"
        return {
            "retry_action": retry_action,
            "retry_key_combo": retry_key_combo,
        }

    def _format_send_retry_action(self, retry_action_config: Optional[Dict[str, str]] = None) -> str:
        config = retry_action_config or self._get_send_retry_action_config()
        retry_action = str(config.get("retry_action") or "click_send_btn").strip().lower()
        if retry_action == "key_press":
            return f"KEY_PRESS({config.get('retry_key_combo') or 'Enter'})"
        return "CLICK(send_btn)"

    def _execute_send_retry_action(
        self,
        selector: str,
        target_key: str,
        optional: bool,
        *,
        retry_action_config: Optional[Dict[str, str]] = None,
    ) -> None:
        config = retry_action_config or self._get_send_retry_action_config()
        retry_action = str(config.get("retry_action") or "click_send_btn").strip().lower()
        if self._network_monitor is not None:
            self._network_monitor.mark_send_attempt()

        if retry_action == "key_press":
            key_combo = str(config.get("retry_key_combo") or "Enter").strip() or "Enter"
            self._execute_keypress_combo(key_combo)
            return

        self._execute_click(selector, target_key, optional)

    def _get_attachment_send_confirmation_profile(self) -> Dict[str, Any]:
        """Resolve the 3-level attachment send sensitivity profile."""
        raw_value = str(
            self._get_raw_send_confirmation_config().get("attachment_sensitivity")
            or self._get_send_confirmation_config().get("attachment_sensitivity")
            or "medium"
        ).strip().lower()
        level = raw_value if raw_value in {"low", "medium", "high"} else "medium"

        profiles = {
            "low": {
                "attachment_observe_window": 4.0,
                "trust_network_activity": True,
                "trust_generating_indicator": True,
                "trust_send_disabled_with_input_shrink": False,
            },
            "medium": {
                "attachment_observe_window": 6.0,
                "trust_network_activity": True,
                "trust_generating_indicator": True,
                "trust_send_disabled_with_input_shrink": True,
            },
            "high": {
                "attachment_observe_window": 8.0,
                "trust_network_activity": True,
                "trust_generating_indicator": True,
                "trust_send_disabled_with_input_shrink": True,
            },
        }
        return {
            "level": level,
            **profiles[level],
        }

    @staticmethod
    def _to_query_selector(selector: Any) -> str:
        """Convert a configured selector into querySelector-compatible CSS when possible."""
        value = str(selector or "").strip()
        if not value:
            return ""

        lowered = value.lower()
        if lowered.startswith("css:"):
            return value[4:].strip()

        if lowered.startswith(("xpath:", "tag:")) or value.startswith("@") or "@@" in value:
            return ""

        return value

    def _probe_send_post_click_state(self, send_selector: str = "") -> Dict[str, Any]:
        """Passively inspect whether the page has transitioned into generating state."""
        selector_json = json.dumps(self._to_query_selector(send_selector), ensure_ascii=False)
        generating_selector = ""
        if isinstance(self._selectors, dict):
            generating_selector = self._to_query_selector(
                self._selectors.get("generating_indicator", "")
            )
        generating_selector_json = json.dumps(generating_selector, ensure_ascii=False)
        js = f"""
        return (function() {{
            try {{
                const sendSelector = {selector_json};
                const configuredGeneratingSelector = {generating_selector_json};
                const indicators = [
                    configuredGeneratingSelector,
                    'button[aria-label*="Stop"]',
                    'button[aria-label*="stop"]',
                    'button[aria-label*="停止"]',
                    '[data-state="streaming"]',
                    '.stop-generating'
                ].filter(Boolean);

                function lowered(value) {{
                    return String(value || '').toLowerCase();
                }}

                function isVisible(node) {{
                    if (!node) return false;
                    const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                    if (style && (style.display === 'none' || style.visibility === 'hidden')) {{
                        return false;
                    }}
                    const rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                    return !rect || (rect.width > 0 && rect.height > 0);
                }}

                let sendBtn = null;
                if (sendSelector) {{
                    try {{
                        sendBtn = document.querySelector(sendSelector);
                    }} catch (e) {{}}
                }}

                const sendMeta = sendBtn ? [
                    sendBtn.getAttribute('aria-label'),
                    sendBtn.getAttribute('title'),
                    sendBtn.getAttribute('data-testid'),
                    sendBtn.className,
                    sendBtn.innerText,
                    sendBtn.textContent
                ].map(lowered).join(' ') : '';

                const generatingIndicator = indicators.some(selector => {{
                    try {{
                        const node = document.querySelector(selector);
                        return isVisible(node);
                    }} catch (e) {{
                        return false;
                    }}
                }});

                const sendLooksLikeStop = !!sendMeta && (
                    /\\bstop\\b|\\bstopping\\b|\\bcancel\\b|\\babort\\b/.test(sendMeta)
                    || /停止|中止|取消/.test(sendMeta)
                );

                const sendDisabled = !!sendBtn && (
                    !!sendBtn.disabled
                    || sendBtn.getAttribute('aria-disabled') === 'true'
                    || /disable(?:d)?|loading|uploading|sending/.test(sendMeta)
                );

                return {{
                    ok: true,
                    sendFound: !!sendBtn,
                    sendDisabled,
                    sendLooksLikeStop,
                    generating: generatingIndicator || sendLooksLikeStop
                }};
            }} catch (error) {{
                return {{
                    ok: false,
                    sendFound: false,
                    sendDisabled: false,
                    sendLooksLikeStop: false,
                    generating: false,
                    error: String(error && error.message ? error.message : error)
                }};
            }}
        }})();
        """

        try:
            return self.tab.run_js(js) or {}
        except Exception as e:
            logger.debug(f"[SEND] 发送后状态探测失败: {e}")
            return {
                "ok": False,
                "sendFound": False,
                "sendDisabled": False,
                "sendLooksLikeStop": False,
                "generating": False,
            }

    def _observe_send_without_retry(
        self,
        send_selector: str,
        before_len: int,
        *,
        max_wait: Optional[float] = None,
        trust_network_activity: Optional[bool] = None,
        trust_generating_indicator: Optional[bool] = None,
        trust_send_disabled_with_input_shrink: Optional[bool] = None,
    ) -> bool:
        """Observe post-click send signals without issuing another click."""
        observe_window = self._get_send_confirmation_window(
            "attachment_observe_window",
            getattr(BrowserConstants, "ATTACHMENT_SEND_OBSERVE_WINDOW", 6.0),
            min_value=0.0,
            max_value=60.0,
        ) if max_wait is None else float(max_wait)
        if trust_network_activity is None:
            trust_network_activity = self._get_send_confirmation_flag(
                "trust_network_activity",
                True,
            )
        if trust_generating_indicator is None:
            trust_generating_indicator = self._get_send_confirmation_flag(
                "trust_generating_indicator",
                True,
            )
        if trust_send_disabled_with_input_shrink is None:
            trust_send_disabled_with_input_shrink = self._get_send_confirmation_flag(
                "trust_send_disabled_with_input_shrink",
                True,
            )
        if observe_window <= 0:
            return False
        poll_interval = 0.25
        elapsed = 0.0
        last_len = before_len

        while elapsed < observe_window:
            if self._check_cancelled():
                return True

            step = min(poll_interval, observe_window - elapsed)
            network_state = {"matched": False}
            if self._network_monitor is not None:
                try:
                    network_state = self._network_monitor.poll_send_activity(timeout=step) or {"matched": False}
                except Exception as e:
                    logger.debug_throttled(
                        "send.network_pre_read_failed",
                        f"[SEND] 网络活动预读失败: {e}",
                        interval_sec=5.0,
                    )
                    time.sleep(step)
            else:
                time.sleep(step)
            elapsed += step

            if trust_network_activity and network_state.get("matched"):
                logger.debug(
                    "[SEND] 已通过网络监听捕获到发送后的目标流事件 "
                    f"(source={network_state.get('source') or '-'}, "
                    f"targets={network_state.get('running_targets', 0)}, "
                    f"requests={network_state.get('running_requests', 0)})"
                )
                return True

            current_len = self._safe_get_input_len_by_key("input_box")
            if self._is_send_success(before_len, current_len) or self._is_send_success(last_len, current_len):
                return True

            state = self._probe_send_post_click_state(send_selector)
            if trust_generating_indicator and state.get("generating"):
                return True

            if (
                trust_send_disabled_with_input_shrink
                and state.get("sendDisabled")
                and current_len < before_len
            ):
                return True

            last_len = current_len

        return False

    def _probe_attachment_state_probe(self, state: Optional[Dict[str, Any]], stage: str) -> Dict[str, Any]:
        if self._attachment_monitor is None:
            return {
                "enabled": False,
                "ok": False,
                "hit": False,
                "result": {},
                "summary": "",
            }
        try:
            return self._attachment_monitor.run_state_probe(state=state, stage=stage)
        except Exception as e:
            logger.debug(f"[SEND] 附件 state probe 执行失败 ({stage}): {e}")
            return {
                "enabled": True,
                "ok": False,
                "hit": False,
                "result": {},
                "summary": str(e)[:240],
            }

    def _build_send_attempt_state(
        self,
        *,
        send_selector: str,
        before_len: int,
        after_len: int,
        baseline_attachment_state: Optional[Dict[str, Any]] = None,
        attachment_state: Optional[Dict[str, Any]] = None,
        network_state: Optional[Dict[str, Any]] = None,
        post_click_state: Optional[Dict[str, Any]] = None,
        probe_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base_attachment_state = dict(baseline_attachment_state or {})
        current_attachment_state = dict(attachment_state or {})
        attachment_delta = {
            "attachmentCount": int(current_attachment_state.get("attachmentCount", 0) or 0)
            - int(base_attachment_state.get("attachmentCount", 0) or 0),
            "previewCount": int(current_attachment_state.get("previewCount", 0) or 0)
            - int(base_attachment_state.get("previewCount", 0) or 0),
            "fileInputCount": int(current_attachment_state.get("fileInputCount", 0) or 0)
            - int(base_attachment_state.get("fileInputCount", 0) or 0),
        }
        attachment_changed = bool(
            current_attachment_state.get("attachmentFingerprint")
            and current_attachment_state.get("attachmentFingerprint") != base_attachment_state.get("attachmentFingerprint")
        ) or any(delta != 0 for delta in attachment_delta.values())
        attachment_disappeared = bool(
            AttachmentMonitor._attachment_present(base_attachment_state)
            and not AttachmentMonitor._attachment_present(current_attachment_state)
        )

        state = {
            "before_input_length": int(before_len or 0),
            "after_input_length": int(after_len or 0),
            "input_shrunk": int(after_len or 0) < int(before_len or 0),
            "attachment_before": base_attachment_state,
            "attachment_after": current_attachment_state,
            "attachment_delta": attachment_delta,
            "attachment_changed_after_send": attachment_changed,
            "attachment_disappeared_after_send": attachment_disappeared,
            "network": dict(network_state or {}),
            "post_click": dict(post_click_state or {}),
            "probe": dict(probe_info or {}),
        }

        accepted_signals = []
        if state["input_shrunk"]:
            accepted_signals.append("input_shrunk")
        if bool((state["network"] or {}).get("matched")):
            accepted_signals.append("network_activity")
        if bool((state["post_click"] or {}).get("generating")):
            accepted_signals.append("generating")
        if bool((state["post_click"] or {}).get("sendLooksLikeStop")):
            accepted_signals.append("send_became_stop")
        if bool((state["post_click"] or {}).get("sendDisabled")) and state["input_shrunk"]:
            accepted_signals.append("send_disabled_with_input_shrink")
        if attachment_changed:
            accepted_signals.append("attachment_changed")
        if attachment_disappeared:
            accepted_signals.append("attachment_disappeared")
        probe_result = (probe_info or {}).get("result") if isinstance(probe_info, dict) else {}
        if isinstance(probe_result, dict) and bool(probe_result.get("accepted")):
            accepted_signals.append("probe_accepted")
        if isinstance(probe_result, dict) and bool(probe_result.get("confirmed")):
            accepted_signals.append("probe_confirmed")
        if isinstance(probe_result, dict) and bool(probe_result.get("retry")):
            state["probe_retry"] = True
        if isinstance(probe_result, dict) and bool(probe_result.get("uploading")):
            state["probe_uploading"] = True
        if isinstance(probe_result, dict) and bool(probe_result.get("ready")):
            state["probe_ready"] = True

        state["accepted_signals"] = accepted_signals
        state["accepted"] = bool(accepted_signals)
        return state

    @staticmethod
    def _format_send_attempt_state(attempt_state: Optional[Dict[str, Any]]) -> str:
        if not isinstance(attempt_state, dict) or not attempt_state:
            return "state=-"

        accepted_signals = [
            str(item).strip()
            for item in (attempt_state.get("accepted_signals") or [])
            if str(item).strip()
        ]
        signals_text = "|".join(accepted_signals) if accepted_signals else "-"
        post_click = attempt_state.get("post_click") or {}
        network = attempt_state.get("network") or {}
        attachment_delta = attempt_state.get("attachment_delta") or {}
        probe = attempt_state.get("probe") or {}
        probe_result = probe.get("result") if isinstance(probe, dict) else {}

        probe_flags = []
        if isinstance(probe_result, dict):
            for key, label in (
                ("retry", "retry"),
                ("accepted", "accepted"),
                ("confirmed", "confirmed"),
                ("uploading", "uploading"),
                ("ready", "ready"),
            ):
                if probe_result.get(key):
                    probe_flags.append(label)

        parts = [
            f"signals={signals_text}",
            f"input={int(attempt_state.get('before_input_length') or 0)}->{int(attempt_state.get('after_input_length') or 0)}",
            f"network={bool(network.get('matched'))}",
            f"network_source={str(network.get('source') or '-')}",
            f"generating={bool(post_click.get('generating'))}",
            f"stop={bool(post_click.get('sendLooksLikeStop'))}",
            f"disabled={bool(post_click.get('sendDisabled'))}",
            "attachment_delta="
            f"a{int(attachment_delta.get('attachmentCount', 0) or 0)}/"
            f"p{int(attachment_delta.get('previewCount', 0) or 0)}/"
            f"f{int(attachment_delta.get('fileInputCount', 0) or 0)}",
        ]

        if probe_flags:
            parts.append(f"probe={'+'.join(probe_flags)}")

        probe_summary = str(probe.get("summary") or "").strip() if isinstance(probe, dict) else ""
        if probe_summary:
            parts.append(f"probe_summary={probe_summary[:80]}")

        return ", ".join(parts)

    def _evaluate_attachment_retry_decision(
        self,
        attempt_state: Dict[str, Any],
        *,
        retry_index: int,
        max_retry_count: int,
    ) -> Dict[str, Any]:
        decision = {
            "should_retry": False,
            "reason": "unknown",
        }

        if retry_index >= max_retry_count:
            decision["reason"] = f"max_retry_count_reached({retry_index}/{max_retry_count})"
            return decision

        retry_on_unconfirmed = self._get_send_confirmation_flag(
            "retry_on_unconfirmed_send",
            True,
            raw_only=True,
        )
        if not retry_on_unconfirmed:
            decision["reason"] = "retry_disabled"
            return decision

        if self._get_send_confirmation_flag("retry_block_if_generating", True, raw_only=True):
            if bool((attempt_state.get("post_click") or {}).get("generating")):
                decision["reason"] = "page_generating"
                return decision

        if self._get_send_confirmation_flag("retry_block_on_stop_button", True, raw_only=True):
            if bool((attempt_state.get("post_click") or {}).get("sendLooksLikeStop")):
                decision["reason"] = "send_button_became_stop"
                return decision

        probe_result = (attempt_state.get("probe") or {}).get("result") if isinstance(attempt_state.get("probe"), dict) else {}
        if isinstance(probe_result, dict):
            if probe_result.get("shouldRetry") is False:
                decision["reason"] = "probe_blocked_retry"
                return decision
            if probe_result.get("retry") is True:
                decision["should_retry"] = True
                decision["reason"] = "probe_requested_retry"
                return decision
            if probe_result.get("accepted") is True or probe_result.get("confirmed") is True:
                decision["reason"] = "probe_confirmed_send"
                return decision

        if bool((attempt_state.get("network") or {}).get("matched")):
            decision["reason"] = "network_activity_seen"
            return decision

        if bool(attempt_state.get("accepted")):
            accepted_signals = attempt_state.get("accepted_signals") or []
            if self._get_send_confirmation_flag("accept_attachment_change", False, raw_only=True):
                if "attachment_changed" in accepted_signals:
                    decision["reason"] = "attachment_changed_accepted"
                    return decision
            if self._get_send_confirmation_flag("accept_attachment_disappear", False, raw_only=True):
                if "attachment_disappeared" in accepted_signals:
                    decision["reason"] = "attachment_disappeared_accepted"
                    return decision
            if self._get_send_confirmation_flag("accept_probe_confirmation", True, raw_only=True):
                if any(
                    signal in accepted_signals
                    for signal in ("probe_accepted", "probe_confirmed")
                ):
                    decision["reason"] = "probe_confirmed_send"
                    return decision
            if "input_shrunk" in accepted_signals:
                decision["reason"] = "input_shrunk"
                return decision
            if "generating" in accepted_signals:
                decision["reason"] = "page_generating"
                return decision
            if "send_became_stop" in accepted_signals:
                decision["reason"] = "send_button_became_stop"
                return decision
            if "network_activity" in accepted_signals:
                decision["reason"] = "network_activity_seen"
                return decision
            if "send_disabled_with_input_shrink" in accepted_signals:
                decision["reason"] = "send_disabled_with_input_shrink"
                return decision
            decision["reason"] = f"accepted_signal:{'|'.join(str(item) for item in accepted_signals)}"
            return decision

        decision["should_retry"] = True
        decision["reason"] = "unconfirmed_no_success_signal"
        return decision

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
        avoid_repeat_click = self._has_recent_attachment_upload()
        attachment_profile = self._get_attachment_send_confirmation_profile()
        max_retry_count = self._get_send_confirmation_int(
            "max_retry_count",
            2,
            min_value=0,
            max_value=10,
            raw_only=True,
        )
        retry_interval = self._get_send_confirmation_window(
            "retry_interval",
            getattr(BrowserConstants, "IMAGE_SEND_RETRY_INTERVAL", 0.6),
            min_value=0.0,
            max_value=max_wait,
            raw_only=True,
        )
        send_observe_window = self._get_send_confirmation_window(
            "post_click_observe_window",
            getattr(BrowserConstants, "SEND_POST_CLICK_OBSERVE_WINDOW", 1.8),
            min_value=0.0,
            max_value=max_wait,
        )
        retry_probe_window = self._get_send_confirmation_window(
            "pre_retry_probe_window",
            0.12,
            min_value=0.0,
            max_value=max_wait,
        )
        retry_observe_window = self._get_send_confirmation_window(
            "retry_observe_window",
            getattr(BrowserConstants, "SEND_RETRY_OBSERVE_WINDOW", 0.9),
            min_value=0.0,
            max_value=max_wait,
        )
        attachment_observe_window = self._get_send_confirmation_window(
            "attachment_observe_window",
            attachment_profile["attachment_observe_window"],
            min_value=0.0,
            max_value=max_wait,
            raw_only=True,
        )
        attachment_trust_network_activity = self._get_send_confirmation_flag(
            "trust_network_activity",
            attachment_profile["trust_network_activity"],
            raw_only=True,
        )
        attachment_trust_generating_indicator = self._get_send_confirmation_flag(
            "trust_generating_indicator",
            attachment_profile["trust_generating_indicator"],
            raw_only=True,
        )
        attachment_trust_send_disabled_with_input_shrink = self._get_send_confirmation_flag(
            "trust_send_disabled_with_input_shrink",
            attachment_profile["trust_send_disabled_with_input_shrink"],
            raw_only=True,
        )
        retry_action_config = self._get_send_retry_action_config()
        retry_action_desc = self._format_send_retry_action(retry_action_config)

        before_len = self._safe_get_input_len_by_key("input_box")
        baseline_attachment_state = self._probe_attachment_readiness(selector) if avoid_repeat_click else {}
        if self._network_monitor is not None:
            self._network_monitor.mark_send_attempt()
        self._execute_click(selector, target_key, optional)

        time.sleep(0.25)
        after_len = self._safe_get_input_len_by_key("input_box")

        if self._is_send_success(before_len, after_len):
            logger.info("发送成功")
            return

        if avoid_repeat_click:
            network_probe = {"matched": False}
            if attachment_trust_network_activity and self._network_monitor is not None:
                try:
                    network_probe = self._network_monitor.poll_send_activity(timeout=min(0.3, attachment_observe_window)) or {"matched": False}
                except Exception as e:
                    logger.debug_throttled(
                        "send.attachment_network_probe_failed",
                        f"[SEND] 附件发送网络探测失败: {e}",
                        interval_sec=5.0,
                    )
            post_click_state = self._probe_send_post_click_state(selector)
            attachment_state_after_send = self._probe_attachment_readiness(selector)
            probe_info = self._probe_attachment_state_probe(attachment_state_after_send, "after_send")
            attempt_state = self._build_send_attempt_state(
                send_selector=selector,
                before_len=before_len,
                after_len=after_len,
                baseline_attachment_state=baseline_attachment_state,
                attachment_state=attachment_state_after_send,
                network_state=network_probe,
                post_click_state=post_click_state,
                probe_info=probe_info,
            )
            self._last_send_attempt_state = attempt_state

            accept_unconfirmed = False
            if self._get_send_confirmation_flag("accept_attachment_change", False, raw_only=True):
                accept_unconfirmed = accept_unconfirmed or bool(attempt_state.get("attachment_changed_after_send"))
            if self._get_send_confirmation_flag("accept_attachment_disappear", False, raw_only=True):
                accept_unconfirmed = accept_unconfirmed or bool(attempt_state.get("attachment_disappeared_after_send"))
            if self._get_send_confirmation_flag("accept_probe_confirmation", True, raw_only=True):
                accept_unconfirmed = accept_unconfirmed or any(
                    signal in (attempt_state.get("accepted_signals") or [])
                    for signal in ("probe_accepted", "probe_confirmed")
                )

            if self._observe_send_without_retry(
                selector,
                before_len,
                max_wait=attachment_observe_window,
                trust_network_activity=attachment_trust_network_activity,
                trust_generating_indicator=attachment_trust_generating_indicator,
                trust_send_disabled_with_input_shrink=attachment_trust_send_disabled_with_input_shrink,
            ):
                logger.info(
                    f"发送成功（附件场景，已避免重复点击发送按钮，sensitivity={attachment_profile['level']}）"
                )
            elif accept_unconfirmed:
                logger.info(
                    "发送成功（附件场景，通过补充信号确认，"
                    f"{self._format_send_attempt_state(attempt_state)}）"
                )
            else:
                retry_decision = self._evaluate_attachment_retry_decision(
                    attempt_state,
                    retry_index=0,
                    max_retry_count=max_retry_count,
                )
                if max_retry_count > 0 and retry_decision["should_retry"]:
                    logger.warning(
                        "[SEND] 附件发送首轮未确认，准备自动重试 "
                        f"(next_retry=1/{max_retry_count}, action={retry_action_desc}, "
                        f"reason={retry_decision['reason']}, "
                        f"sensitivity={attachment_profile['level']}, "
                        f"{self._format_send_attempt_state(attempt_state)})"
                    )

                    for retry_index in range(1, max_retry_count + 1):
                        if self._check_cancelled():
                            return

                        wait_for = max(0.0, min(retry_interval, max_wait))
                        if wait_for > 0:
                            time.sleep(wait_for)

                        pre_retry_probe_window = min(
                            max(0.0, retry_probe_window),
                            max(0.0, max_wait),
                        )
                        if pre_retry_probe_window > 0 and self._observe_send_without_retry(
                            selector,
                            before_len,
                            max_wait=pre_retry_probe_window,
                            trust_network_activity=attachment_trust_network_activity,
                            trust_generating_indicator=attachment_trust_generating_indicator,
                            trust_send_disabled_with_input_shrink=attachment_trust_send_disabled_with_input_shrink,
                        ):
                            logger.info(
                                f"发送成功（附件重试前观察确认，第 {retry_index} 轮，无需执行重试动作，action={retry_action_desc}）"
                            )
                            return

                        logger.warning(
                            "[SEND] 执行附件重试动作 "
                            f"(retry={retry_index}/{max_retry_count}, action={retry_action_desc})"
                        )
                        self._execute_send_retry_action(
                            selector,
                            target_key,
                            optional,
                            retry_action_config=retry_action_config,
                        )
                        time.sleep(0.25)
                        retry_after_len = self._safe_get_input_len_by_key("input_box")
                        retry_network_probe = {"matched": False}
                        if attachment_trust_network_activity and self._network_monitor is not None:
                            try:
                                retry_network_probe = self._network_monitor.poll_send_activity(
                                    timeout=min(0.3, attachment_observe_window)
                                ) or {"matched": False}
                            except Exception as e:
                                logger.debug_throttled(
                                    "send.attachment_network_retry_probe_failed",
                                    f"[SEND] 附件重试网络探测失败: {e}",
                                    interval_sec=5.0,
                                )
                        retry_post_click_state = self._probe_send_post_click_state(selector)
                        retry_attachment_state = self._probe_attachment_readiness(selector)
                        retry_probe_info = self._probe_attachment_state_probe(
                            retry_attachment_state,
                            f"retry_{retry_index}",
                        )
                        retry_attempt_state = self._build_send_attempt_state(
                            send_selector=selector,
                            before_len=before_len,
                            after_len=retry_after_len,
                            baseline_attachment_state=baseline_attachment_state,
                            attachment_state=retry_attachment_state,
                            network_state=retry_network_probe,
                            post_click_state=retry_post_click_state,
                            probe_info=retry_probe_info,
                        )
                        self._last_send_attempt_state = retry_attempt_state

                        if self._observe_send_without_retry(
                            selector,
                            before_len,
                            max_wait=min(retry_observe_window, max_wait),
                            trust_network_activity=attachment_trust_network_activity,
                            trust_generating_indicator=attachment_trust_generating_indicator,
                            trust_send_disabled_with_input_shrink=attachment_trust_send_disabled_with_input_shrink,
                        ):
                            logger.info(
                                f"发送成功（附件重试后观察确认，第 {retry_index} 轮，action={retry_action_desc}）"
                            )
                            return

                        retry_decision = self._evaluate_attachment_retry_decision(
                            retry_attempt_state,
                            retry_index=retry_index,
                            max_retry_count=max_retry_count,
                        )
                        if not retry_decision["should_retry"]:
                            logger.warning(
                                "[SEND] 附件重试停止 "
                                f"(retry={retry_index}/{max_retry_count}, action={retry_action_desc}, "
                                f"reason={retry_decision['reason']}, "
                                f"{self._format_send_attempt_state(retry_attempt_state)})"
                            )
                            return

                    logger.warning(
                        "[SEND] 附件发送重试已达到上限，停止自动补发 "
                        f"(max_retry_count={max_retry_count}, action={retry_action_desc})"
                    )
                else:
                    logger.warning(
                        "[SEND] 附件发送未确认，但当前不执行自动重试 "
                        f"(action={retry_action_desc}, reason={retry_decision['reason']}, "
                        f"sensitivity={attachment_profile['level']}, "
                        f"{self._format_send_attempt_state(attempt_state)})"
                    )
            return

        if self._observe_send_without_retry(selector, before_len, max_wait=send_observe_window):
            logger.info("发送成功（首次点击后观察确认）")
            return

        retry_action_config = self._get_send_retry_action_config()
        retry_action_desc = self._format_send_retry_action(retry_action_config)
        logger.warning(
            f"[SEND] 发送未成功，进入重试窗口 (max_wait={max_wait}s, action={retry_action_desc})"
        )

        deadline = time.time() + max_wait
        while time.time() < deadline:
            if self._check_cancelled():
                return

            remaining = max(0.0, deadline - time.time())
            step = min(retry_interval, remaining)
            if step <= 0:
                break
            time.sleep(step)

            remaining = max(0.0, deadline - time.time())
            if remaining > 0 and self._observe_send_without_retry(
                selector,
                before_len,
                max_wait=min(retry_probe_window, remaining),
            ):
                elapsed = max_wait - max(0.0, deadline - time.time())
                logger.info(
                    f"发送成功（重试前观察确认，elapsed={elapsed:.1f}s, action={retry_action_desc} 未执行）"
                )
                return

            logger.warning(
                f"[SEND] 执行发送重试动作 (elapsed={max_wait - remaining:.1f}s, action={retry_action_desc})"
            )
            self._execute_send_retry_action(
                selector,
                target_key,
                optional,
                retry_action_config=retry_action_config,
            )

            if time.time() < deadline:
                time.sleep(min(0.25, max(0.0, deadline - time.time())))
            new_len = self._safe_get_input_len_by_key("input_box")

            if self._is_send_success(after_len, new_len) or self._is_send_success(before_len, new_len):
                elapsed = max_wait - max(0.0, deadline - time.time())
                logger.info(f"发送成功（重试动作后确认，elapsed={elapsed:.1f}s, action={retry_action_desc}）")
                return

            remaining = max(0.0, deadline - time.time())
            if remaining > 0 and self._observe_send_without_retry(
                selector,
                before_len,
                max_wait=min(retry_observe_window, remaining),
            ):
                elapsed = max_wait - max(0.0, deadline - time.time())
                logger.info(
                    f"发送成功（重试后观察确认，elapsed={elapsed:.1f}s, action={retry_action_desc}）"
                )
                return

            after_len = new_len

        logger.error(f"[SEND] 发送重试超时 (action={retry_action_desc})")
        if not optional:
            raise WorkflowError("send_btn_click_failed_due_to_uploading")

    def _execute_click_send_stealth(self, selector: str, target_key: str, optional: bool):
        """
        隐身模式发送（零 JS 注入）
        
        - 无图片：直接点击
        - 有图片：先单击并观察发送信号，仅在未确认时做少量重试
        """
        has_images = False
        if hasattr(self, '_context') and self._context:
            has_images = bool(self._context.get('images'))
        
        if not has_images:
            self._execute_click(selector, target_key, optional)
            logger.info("[STEALTH] 发送完成（无图片）")
            return
        
        default_wait = float(BrowserConstants.get('STEALTH_SEND_IMAGE_WAIT') or 8.0)
        observe_window = self._get_send_confirmation_window(
            "attachment_observe_window",
            default_wait,
            min_value=0.0,
            max_value=60.0,
            raw_only=True,
        )
        retry_interval = self._get_send_confirmation_window(
            "retry_interval",
            float(BrowserConstants.get('STEALTH_SEND_IMAGE_RETRY_INTERVAL') or 1.2),
            min_value=0.0,
            max_value=30.0,
            raw_only=True,
        )
        pre_retry_probe_window = self._get_send_confirmation_window(
            "pre_retry_probe_window",
            0.12,
            min_value=0.0,
            max_value=5.0,
            raw_only=True,
        )
        retry_observe_window = self._get_send_confirmation_window(
            "retry_observe_window",
            float(getattr(BrowserConstants, "SEND_RETRY_OBSERVE_WINDOW", 0.9)),
            min_value=0.0,
            max_value=15.0,
            raw_only=True,
        )
        max_retry_count = self._get_send_confirmation_int(
            "max_retry_count",
            1,
            min_value=0,
            max_value=3,
            raw_only=True,
        )
        trust_network_activity = self._get_send_confirmation_flag(
            "trust_network_activity",
            True,
            raw_only=True,
        )
        trust_generating_indicator = self._get_send_confirmation_flag(
            "trust_generating_indicator",
            True,
            raw_only=True,
        )
        trust_send_disabled_with_input_shrink = self._get_send_confirmation_flag(
            "trust_send_disabled_with_input_shrink",
            True,
            raw_only=True,
        )
        before_len = self._safe_get_input_len_by_key("input_box")
        if self._network_monitor is not None:
            self._network_monitor.mark_send_attempt()
        
        logger.info(
            "[STEALTH] 有图片，发送后观察确认 "
            f"(observe={observe_window:.1f}s, max_retry={max_retry_count})"
        )
        
        self._execute_click(selector, target_key, optional)
        time.sleep(0.25)
        after_len = self._safe_get_input_len_by_key("input_box")
        if self._is_send_success(before_len, after_len):
            logger.info("[STEALTH] 发送成功（输入框已缩短）")
            return
        if self._observe_send_without_retry(
            selector,
            before_len,
            max_wait=observe_window,
            trust_network_activity=trust_network_activity,
            trust_generating_indicator=trust_generating_indicator,
            trust_send_disabled_with_input_shrink=trust_send_disabled_with_input_shrink,
        ):
            logger.info("[STEALTH] 发送成功（首击后信号确认）")
            return
        
        for retry_count in range(1, max_retry_count + 1):
            if self._check_cancelled():
                return

            if retry_interval > 0:
                time.sleep(retry_interval)

            if pre_retry_probe_window > 0 and self._observe_send_without_retry(
                selector,
                before_len,
                max_wait=pre_retry_probe_window,
                trust_network_activity=trust_network_activity,
                trust_generating_indicator=trust_generating_indicator,
                trust_send_disabled_with_input_shrink=trust_send_disabled_with_input_shrink,
            ):
                logger.info(f"[STEALTH] 发送成功（重试前观察确认，第 {retry_count} 轮）")
                return

            post_state = self._probe_send_post_click_state(selector)
            if bool(post_state.get("generating")) or bool(post_state.get("sendLooksLikeStop")):
                logger.info(
                    f"[STEALTH] 检测到页面进入生成态，停止重试 (retry={retry_count}/{max_retry_count})"
                )
                return

            try:
                self._execute_click(selector, target_key, True)
                logger.debug(f"[STEALTH] 发送重试 #{retry_count}")
            except Exception:
                logger.debug(f"[STEALTH] 发送重试 #{retry_count} 执行失败，继续观察")

            time.sleep(0.25)
            retry_after_len = self._safe_get_input_len_by_key("input_box")
            if self._is_send_success(before_len, retry_after_len):
                logger.info(f"[STEALTH] 发送成功（第 {retry_count} 次重试后输入框缩短）")
                return

            if self._observe_send_without_retry(
                selector,
                before_len,
                max_wait=retry_observe_window,
                trust_network_activity=trust_network_activity,
                trust_generating_indicator=trust_generating_indicator,
                trust_send_disabled_with_input_shrink=trust_send_disabled_with_input_shrink,
            ):
                logger.info(f"[STEALTH] 发送成功（第 {retry_count} 次重试后信号确认）")
                return

        logger.warning(
            "[STEALTH] 图片发送未拿到确认信号，结束重试并交由后续监听 "
            f"(max_retry={max_retry_count}, observe={observe_window:.1f}s)"
        )
    
