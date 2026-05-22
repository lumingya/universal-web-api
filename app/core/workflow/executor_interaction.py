"""
app/core/workflow/executor_interaction.py - ??????? mixin

???
- ????????
- ??????????/?????
- ????????????
"""

import time
import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

from app.core.config import BrowserConstants, logger
from app.core.elements import ElementFinder
from app.core.page_lifecycle import install_visibility_emulation, restore_visibility_emulation


class _PageInteractionGate:
    """Throttle active page interactions across tabs to reduce renderer spikes."""

    def __init__(self):
        self._condition = threading.Condition()
        self._active_count = 0
        self._next_slot_at = 0.0

    @contextmanager
    def hold(
        self,
        *,
        label: str,
        session_id: str = "",
        max_concurrent: int = 1,
        timeout: float = 20.0,
        min_interval: float = 0.25,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ):
        acquired = self._acquire(
            label=label,
            session_id=session_id,
            max_concurrent=max_concurrent,
            timeout=timeout,
            cancel_checker=cancel_checker,
        )
        try:
            yield acquired
        finally:
            if acquired:
                self._release(min_interval=min_interval)

    def _acquire(
        self,
        *,
        label: str,
        session_id: str,
        max_concurrent: int,
        timeout: float,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ) -> bool:
        limit = max(1, int(max_concurrent or 1))
        wait_timeout = max(0.0, float(timeout or 0.0))
        deadline = time.time() + wait_timeout if wait_timeout > 0 else None

        while True:
            if cancel_checker and cancel_checker():
                return False

            with self._condition:
                now = time.time()
                if self._active_count < limit and now >= self._next_slot_at:
                    self._active_count += 1
                    return True

                if deadline is not None:
                    remaining = deadline - now
                    if remaining <= 0:
                        self._active_count += 1
                        logger.warning(
                            f"[INTERACT] throttle wait exceeded for {session_id or '-'}:{label}, "
                            "continuing fail-open"
                        )
                        return True
                else:
                    remaining = 0.25

                next_gap = max(0.0, self._next_slot_at - now)
                wait_for = min(max(0.05, next_gap), remaining) if deadline is not None else max(0.05, next_gap)
                self._condition.wait(timeout=max(0.05, min(wait_for, 0.25)))

    def _release(self, *, min_interval: float):
        with self._condition:
            if self._active_count > 0:
                self._active_count -= 1
            self._next_slot_at = max(self._next_slot_at, time.time() + max(0.0, float(min_interval or 0.0)))
            self._condition.notify_all()


_PAGE_INTERACTION_GATE = _PageInteractionGate()


# ================= 工作流执行器 =================


class WorkflowExecutorInteractionMixin:
    def _get_page_interaction_settings(self) -> Dict[str, Any]:
        return {
            "enabled": self._coerce_bool(
                BrowserConstants.get("PAGE_INTERACTION_THROTTLE_ENABLED"),
                True,
            ),
            "max_concurrent": self._coerce_int(
                BrowserConstants.get("PAGE_INTERACTION_MAX_CONCURRENT"),
                3,
                minimum=1,
            ),
            "max_wait": self._coerce_float(
                BrowserConstants.get("PAGE_INTERACTION_MAX_WAIT"),
                20.0,
                minimum=0.0,
            ),
            "min_interval": self._coerce_float(
                BrowserConstants.get("PAGE_INTERACTION_MIN_INTERVAL"),
                0.25,
                minimum=0.0,
            ),
            "ready_timeout": self._coerce_float(
                BrowserConstants.get("PAGE_INTERACTION_READY_TIMEOUT"),
                1.5,
                minimum=0.0,
            ),
            "stable_samples": self._coerce_int(
                BrowserConstants.get("PAGE_INTERACTION_STABLE_SAMPLES"),
                2,
                minimum=1,
            ),
            "sample_interval": self._coerce_float(
                BrowserConstants.get("PAGE_INTERACTION_SAMPLE_INTERVAL"),
                0.12,
                minimum=0.02,
            ),
            "rect_tolerance": self._coerce_int(
                BrowserConstants.get("PAGE_INTERACTION_RECT_TOLERANCE"),
                3,
                minimum=0,
            ),
        }

    def _get_input_stability_wait_settings(self) -> Dict[str, Any]:
        advanced = self._site_advanced_config if isinstance(self._site_advanced_config, dict) else {}
        timeout = self._coerce_float(
            advanced.get("input_box_stability_wait_timeout"),
            1.5,
            minimum=0.2,
        )
        return {
            "enabled": self._coerce_bool(
                advanced.get("input_box_stability_wait_enabled"),
                False,
            ),
            "after_new_chat_only": self._coerce_bool(
                advanced.get("input_box_stability_wait_after_new_chat_only"),
                True,
            ),
            "timeout": min(timeout, 10.0),
            "stable_samples": 2,
            "sample_interval": 0.18,
        }

    def _clear_target_element_cache(self, selector: str, target_key: str = "") -> None:
        if selector:
            self.finder.remove_from_cache(selector)

        for fallback_selector in ElementFinder.FALLBACK_SELECTORS.get(target_key or "", []):
            self.finder.remove_from_cache(fallback_selector)

    @staticmethod
    def _build_element_stability_signature(ele) -> Optional[tuple]:
        if ele is None:
            return None

        backend_id = getattr(ele, "_backend_id", None)
        tag = str(getattr(ele, "tag", "") or "")

        try:
            rect = getattr(ele, "rect", None)
            location = getattr(rect, "location", None) or (0, 0)
            size = getattr(rect, "size", None) or (0, 0)
            return (
                backend_id,
                tag,
                int(location[0]),
                int(location[1]),
                int(size[0]),
                int(size[1]),
            )
        except Exception:
            if backend_id is None and not tag:
                return None
            return (backend_id, tag)

    def _wait_for_fill_target_stability(self, selector: str, target_key: str):
        settings = self._get_input_stability_wait_settings()
        if not settings["enabled"] or (target_key or "") != "input_box":
            return None

        if settings["after_new_chat_only"] and not self._input_stability_wait_pending:
            return None

        self._input_stability_wait_pending = False
        stable_needed = max(1, int(settings["stable_samples"]))
        sample_interval = max(0.05, float(settings["sample_interval"]))
        deadline = time.time() + max(0.2, float(settings["timeout"]))
        stable_count = 0
        last_signature = None
        latest_element = None

        while time.time() < deadline:
            if self._check_cancelled():
                return latest_element

            self._clear_target_element_cache(selector, target_key)
            sample = self.finder.find_with_fallback(
                selector,
                target_key,
                timeout=min(sample_interval, 0.3),
            )
            signature = self._build_element_stability_signature(sample)
            latest_element = sample or latest_element

            if signature is not None and signature == last_signature:
                stable_count += 1
            else:
                stable_count = 1 if signature is not None else 0
                last_signature = signature

            if stable_count >= stable_needed and latest_element is not None:
                logger.debug(
                    "[FILL_STABLE] 输入框已稳定 "
                    f"(target={target_key}, samples={stable_count}, timeout={settings['timeout']:.2f}s)"
                )
                return latest_element

            time.sleep(sample_interval)

        logger.debug_throttled(
            f"fill.stability.{target_key or 'input'}",
            f"[FILL_STABLE] 输入框稳定等待超时，继续沿用原流程: target={target_key}, timeout={settings['timeout']:.2f}s",
            interval_sec=5.0,
        )
        return latest_element

    def _note_fill_completion(self, text: str, *, after_new_chat: bool = False) -> None:
        self._last_fill_completed_at = time.time()
        self._last_fill_text_length = max(0, len(text or ""))
        self._last_fill_after_new_chat = bool(after_new_chat)

    def _get_recent_fill_send_wait_timeout(self, target_key: str, default_timeout: float) -> float:
        if (target_key or "") != "send_btn":
            return default_timeout

        completed_at = float(getattr(self, "_last_fill_completed_at", 0.0) or 0.0)
        if completed_at <= 0:
            return default_timeout

        fill_age = time.time() - completed_at
        if fill_age < 0 or fill_age > 12.0:
            return default_timeout

        text_len = int(getattr(self, "_last_fill_text_length", 0) or 0)
        if text_len <= 0:
            return default_timeout

        extra_timeout = 0.0
        if bool(getattr(self, "_last_fill_after_new_chat", False)):
            extra_timeout += 1.2
        if text_len >= 20000:
            extra_timeout += min(2.8, text_len / 60000.0)

        if extra_timeout <= 0:
            return default_timeout
        return min(6.0, max(default_timeout, default_timeout + extra_timeout))

    def _refresh_target_element(self, selector: str, target_key: str, *, timeout: float = 0.3):
        if not selector and not target_key:
            return None

        self._clear_target_element_cache(selector, target_key)
        try:
            return self.finder.find_with_fallback(
                selector,
                target_key,
                timeout=timeout,
            )
        except Exception:
            return None

    def _element_accepts_text_input(self, ele) -> bool:
        if ele is None:
            return False

        try:
            result = self.tab.run_js(
                """
                try {
                    const el = arguments[0];
                    if (!el || !el.isConnected) return false;
                    const tag = (el.tagName || '').toLowerCase();
                    return tag === 'textarea'
                        || tag === 'input'
                        || !!el.isContentEditable
                        || el.getAttribute('contenteditable') === 'true';
                } catch (e) {
                    return false;
                }
                """,
                ele,
            )
        except Exception:
            return False
        return bool(result)

    def _resolve_active_text_input(self):
        try:
            active_ele = self.tab.run_js("return document.activeElement")
        except Exception:
            active_ele = None
        if self._element_accepts_text_input(active_ele):
            return active_ele
        return None

    @contextmanager
    def _page_interaction_slot(self, action: str, target_key: str = ""):
        settings = self._get_page_interaction_settings()
        label = f"{action}:{target_key}" if target_key else str(action or "interaction")

        if not settings["enabled"]:
            with self._wake_page_for_interaction(label):
                yield True
            return

        session_id = str(getattr(self.session, "id", "") or "")
        with _PAGE_INTERACTION_GATE.hold(
            label=label,
            session_id=session_id,
            max_concurrent=settings["max_concurrent"],
            timeout=settings["max_wait"],
            min_interval=settings["min_interval"],
            cancel_checker=self._check_cancelled,
        ) as acquired:
            if not acquired:
                yield False
                return
            with self._wake_page_for_interaction(label):
                yield True

    def _get_workflow_wake_settings(self) -> Dict[str, bool]:
        return {
            "wake_before_interaction": self._coerce_bool(
                BrowserConstants.get("WORKFLOW_WAKE_TAB_BEFORE_INTERACTION"),
                True,
            ),
            "focus_emulation": self._coerce_bool(
                BrowserConstants.get("WORKFLOW_FOCUS_EMULATION_ON_INTERACTION"),
                True,
            ),
        }

    @contextmanager
    def workflow_execution_scope(self):
        """Keep stealth focus emulation active for the whole workflow run."""
        if not self.stealth_mode:
            yield
            return

        self._workflow_scope_depth += 1
        started_here = self._workflow_scope_depth == 1
        if started_here:
            self._begin_stealth_workflow_scope()

        try:
            yield
        finally:
            self._workflow_scope_depth = max(0, self._workflow_scope_depth - 1)
            if started_here:
                self._end_stealth_workflow_scope()

    def _begin_stealth_workflow_scope(self):
        logger.debug("[STEALTH] 工作流开始前启用焦点模拟")

        try:
            self.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=True)
            self._workflow_focus_emulation_active = True
        except Exception as e:
            self._workflow_focus_emulation_active = False
            logger.debug_throttled(
                "workflow.stealth_focus_emulation.start",
                f"[STEALTH] 工作流级焦点模拟启用失败（忽略）: error={e}",
                interval_sec=10.0,
            )

        try:
            install_visibility_emulation(self.tab, owner=self.session, reason="workflow_start")
        except Exception as e:
            logger.debug_throttled(
                "workflow.stealth_visibility.start",
                f"[STEALTH] 工作流级可见性模拟启用失败（忽略）: error={e}",
                interval_sec=10.0,
            )

        try:
            self.tab.run_cdp("Page.setWebLifecycleState", state="active")
        except Exception as e:
            logger.debug_throttled(
                "workflow.stealth_lifecycle.start",
                f"[STEALTH] 工作流开始前页面唤醒失败（忽略）: error={e}",
                interval_sec=10.0,
            )

    def _end_stealth_workflow_scope(self):
        if not self._workflow_focus_emulation_active:
            restore_visibility_emulation(self.tab, owner=self.session, reason="workflow_end")
            return

        try:
            self.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=False)
        except Exception:
            pass
        finally:
            self._workflow_focus_emulation_active = False
            restore_visibility_emulation(self.tab, owner=self.session, reason="workflow_end")

    @contextmanager
    def _wake_page_for_interaction(self, label: str):
        settings = self._get_workflow_wake_settings()
        if self.stealth_mode:
            with self._wake_page_for_stealth_interaction(label):
                yield
            return

        if not settings["wake_before_interaction"]:
            yield
            return

        focus_emulation_enabled = False
        try:
            if settings["focus_emulation"]:
                try:
                    self.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=True)
                    focus_emulation_enabled = True
                except Exception as e:
                    logger.debug_throttled(
                        f"interaction.focus_emulation.{label}",
                        f"[INTERACT] 焦点模拟启用失败（忽略）: target={label}, error={e}",
                        interval_sec=10.0,
                    )
            try:
                self.tab.run_cdp("Page.setWebLifecycleState", state="active")
            except Exception as e:
                logger.debug_throttled(
                    f"interaction.lifecycle_wake.{label}",
                    f"[INTERACT] 页面唤醒失败（忽略）: target={label}, error={e}",
                    interval_sec=10.0,
                )
            try:
                self.tab.run_js(
                    "return {readyState: document.readyState || '', hidden: !!document.hidden, visibilityState: document.visibilityState || ''};"
                )
            except Exception:
                pass
            yield
        finally:
            if focus_emulation_enabled:
                try:
                    self.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=False)
                except Exception:
                    pass

    @contextmanager
    def _wake_page_for_stealth_interaction(self, label: str):
        """
        低熵模式下的最小唤醒。

        不强制激活标签页或切到前台，只使用不会抢焦点的 CDP 能力，
        尽量减少后台页被冻结、坐标读取或鼠标事件派发被拖延的概率。
        """
        focus_emulation_enabled = False

        try:
            if not self._workflow_focus_emulation_active:
                try:
                    self.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=True)
                    focus_emulation_enabled = True
                except Exception as e:
                    logger.debug_throttled(
                        f"interaction.stealth_focus_emulation.{label}",
                        f"[STEALTH] 焦点模拟启用失败（忽略）: target={label}, error={e}",
                        interval_sec=10.0,
                    )

            try:
                install_visibility_emulation(self.tab, owner=self.session, reason=f"interaction:{label}")
            except Exception as e:
                logger.debug_throttled(
                    f"interaction.stealth_visibility.{label}",
                    f"[STEALTH] 可见性模拟启用失败（忽略）: target={label}, error={e}",
                    interval_sec=10.0,
                )

            try:
                self.tab.run_cdp("Page.setWebLifecycleState", state="active")
            except Exception as e:
                logger.debug_throttled(
                    f"interaction.stealth_lifecycle.{label}",
                    f"[STEALTH] 页面唤醒失败（忽略）: target={label}, error={e}",
                    interval_sec=10.0,
                )

            yield
        finally:
            if focus_emulation_enabled:
                try:
                    self.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=False)
                except Exception:
                    pass

    @staticmethod
    def _is_rect_stable(previous: Dict[str, Any], current: Dict[str, Any], tolerance: int) -> bool:
        if not previous or not current:
            return False
        previous_rect = previous.get("rect") or {}
        current_rect = current.get("rect") or {}
        for key in ("x", "y", "width", "height"):
            if abs(int(previous_rect.get(key, 0)) - int(current_rect.get(key, 0))) > tolerance:
                return False
        return True

    def _sample_element_interactable_state(self, ele) -> Dict[str, Any]:
        try:
            state = ele.run_js(
                """
                try {
                    const el = this;
                    if (!el || !el.isConnected) {
                        return { interactable: false, connected: false };
                    }
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const signalText = [
                        el.getAttribute('aria-label'),
                        el.getAttribute('title'),
                        el.getAttribute('data-testid'),
                        el.innerText,
                        el.textContent
                    ].join(' ').toLowerCase();
                    const classText = String(el.className || '').toLowerCase();
                    const disabled = !!el.disabled || el.getAttribute('aria-disabled') === 'true';
                    const hidden = style.display === 'none'
                        || style.visibility === 'hidden'
                        || Number(style.opacity || '1') < 0.05;
                    const pointerEventsNone = style.pointerEvents === 'none';
                    const busy = el.getAttribute('aria-busy') === 'true'
                        || /loading|pending|sending|uploading/.test(signalText)
                        || /(^|[\\s:_-])(loading|pending|sending|uploading)(?=$|[\\s:_-])/.test(classText);
                    const sizeOk = rect.width >= 1 && rect.height >= 1;
                    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
                    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
                    const viewportOk = rect.bottom >= 0
                        && rect.right >= 0
                        && rect.top <= viewportHeight
                        && rect.left <= viewportWidth;
                    return {
                        interactable: !disabled && !hidden && !pointerEventsNone && !busy && sizeOk && viewportOk,
                        connected: true,
                        disabled,
                        hidden,
                        busy,
                        pointerEventsNone,
                        rect: {
                            x: Math.round(rect.x || 0),
                            y: Math.round(rect.y || 0),
                            width: Math.round(rect.width || 0),
                            height: Math.round(rect.height || 0)
                        }
                    };
                } catch (error) {
                    return {
                        interactable: false,
                        connected: false,
                        error: String(error && error.message ? error.message : error || '')
                    };
                }
                """
            )
        except Exception as e:
            return {
                "interactable": False,
                "connected": False,
                "error": str(e),
            }
        return state if isinstance(state, dict) else {"interactable": False, "connected": False}

    def _wait_for_element_interactable(self, ele, selector: str = "", target_key: str = ""):
        settings = self._get_page_interaction_settings()
        base_timeout = settings["ready_timeout"]
        timeout = self._get_recent_fill_send_wait_timeout(target_key, base_timeout)
        if timeout <= 0:
            return ele

        stable_needed = settings["stable_samples"]
        sample_interval = settings["sample_interval"]
        tolerance = settings["rect_tolerance"]
        stable_count = 0
        last_state: Optional[Dict[str, Any]] = None
        deadline = time.time() + timeout
        latest_element = ele

        if timeout > base_timeout + 0.01:
            logger.debug_throttled(
                f"interaction.wait.extend.{target_key or 'element'}",
                "[INTERACT] 检测到刚完成新会话/长文本填充，放宽发送按钮等待 "
                f"(target={target_key or '-'}, timeout={timeout:.2f}s)",
                interval_sec=5.0,
            )

        while time.time() < deadline:
            if self._check_cancelled():
                return latest_element

            if target_key in {"input_box", "send_btn"} and (selector or target_key):
                refreshed = self._refresh_target_element(
                    selector,
                    target_key,
                    timeout=min(sample_interval, 0.3),
                )
                if refreshed is not None:
                    latest_element = refreshed

            current_state = self._sample_element_interactable_state(latest_element)
            if current_state.get("interactable"):
                stable_count = stable_count + 1 if self._is_rect_stable(last_state, current_state, tolerance) else 1
                if stable_count >= stable_needed:
                    return latest_element
            else:
                stable_count = 0

            last_state = current_state
            time.sleep(sample_interval)

        logger.debug_throttled(
            f"interaction.wait.{target_key or 'element'}",
            f"[INTERACT] 元素稳定等待超时: target={target_key or '-'}, state={last_state}",
            interval_sec=5.0,
        )
        return latest_element
