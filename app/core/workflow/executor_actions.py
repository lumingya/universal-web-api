"""
app/core/workflow/executor_actions.py - 工作流执行器隐身动作 Mixin

主要功能：
- 提供拟人化鼠标移动、微小漂移和点击操作
- 隐身模式（Stealth Mode）下的页面预热与元素安全激活
- 物理坐标点击位置计算与防爬虫检测策略
"""

import math
import random
import re
import time
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from app.core.config import BrowserConstants, ElementNotFoundError, WorkflowError, logger
from app.utils.human_mouse import cdp_precise_click, human_scroll_path, idle_drift, smooth_move_mouse


class WorkflowExecutorActionMixin:
    _NEW_CHAT_EMPTY_ROUTE_SEGMENTS = {
        "app",
        "text",
        "direct",
        "none",
        "chat",
        "new",
        "new_chat",
        "new-chat",
        "prompts",
    }
    _NEW_CHAT_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")

    def _get_coord_click_settings(self) -> dict:
        return {
            "ready_timeout": self._coerce_float(
                BrowserConstants.get("COORD_CLICK_READY_TIMEOUT"),
                0.9,
                minimum=0.0,
            ),
            "stable_samples": self._coerce_int(
                BrowserConstants.get("COORD_CLICK_STABLE_SAMPLES"),
                2,
                minimum=1,
            ),
            "sample_interval": self._coerce_float(
                BrowserConstants.get("COORD_CLICK_SAMPLE_INTERVAL"),
                0.08,
                minimum=0.02,
            ),
            "rect_tolerance": self._coerce_int(
                BrowserConstants.get("COORD_CLICK_RECT_TOLERANCE"),
                3,
                minimum=0,
            ),
            "edge_inset": self._coerce_int(
                BrowserConstants.get("COORD_CLICK_EDGE_INSET"),
                4,
                minimum=0,
            ),
            "retry_offsets": BrowserConstants.get("COORD_CLICK_RETRY_OFFSETS")
            or [[0, 0], [4, 0], [-4, 0], [0, 4], [0, -4], [7, 3], [-7, 3]],
        }

    def _get_stealth_click_strategy(self) -> str:
        raw = str(BrowserConstants.get("STEALTH_CLICK_STRATEGY") or "auto").strip().lower()
        aliases = {
            "dom": "dom_safe",
            "js": "dom_safe",
            "native": "dom_safe",
            "background": "dom_safe",
            "background_safe": "dom_safe",
            "cdp": "cdp_mouse",
            "mouse": "cdp_mouse",
            "cdp_mouse": "cdp_mouse",
            "human": "cdp_mouse",
            "auto": "auto",
        }
        return aliases.get(raw, "auto")

    @staticmethod
    def _normalize_string_set(value: Any) -> set:
        if isinstance(value, (list, tuple, set)):
            return {
                str(item or "").strip()
                for item in value
                if str(item or "").strip()
            }
        if isinstance(value, str):
            return {
                item.strip()
                for item in value.replace(";", ",").split(",")
                if item.strip()
            }
        return set()

    def _get_stealth_dom_click_targets(self) -> set:
        targets = self._normalize_string_set(BrowserConstants.get("STEALTH_DOM_CLICK_TARGETS"))
        if not targets:
            targets = {"new_chat_btn", "input_box", "send_btn"}
        return targets

    def _should_use_stealth_dom_click(self, target_key: str = "") -> bool:
        if not self.stealth_mode:
            return False

        strategy = self._get_stealth_click_strategy()
        if strategy == "dom_safe":
            return True
        if strategy == "cdp_mouse":
            return False

        target = str(target_key or "").strip()
        return bool(target and target in self._get_stealth_dom_click_targets())

    def _resolve_input_length_selector(self, target_key: str) -> str:
        selector = ""
        if isinstance(getattr(self, "_selectors", None), dict):
            selector = str(self._selectors.get(target_key, "") or "").strip()
        if selector:
            return selector

        text_handler = getattr(self, "_text_handler", None)
        if text_handler is not None:
            selector = str(getattr(text_handler, "_active_input_selector", "") or "").strip()
        if selector:
            return selector

        return ""

    def _should_run_stealth_warmup(self, action: str = "", target_key: str = "") -> bool:
        if not self.stealth_mode:
            return False
        if not self._coerce_bool(BrowserConstants.get("STEALTH_MOUSE_WARMUP_ENABLED"), False):
            return False
        if str(action or "").strip().upper() == "CLICK" and self._should_use_stealth_dom_click(target_key):
            return False
        return True

    def _maybe_warmup_page_for_stealth(self, action: str = "", target_key: str = ""):
        if not self.stealth_mode or getattr(self, "_page_warmed_up", False):
            return

        if not self._should_run_stealth_warmup(action, target_key):
            self._page_warmed_up = True
            logger.debug(
                "[STEALTH] 跳过鼠标预热: "
                f"action={str(action or '-').upper()}, target={target_key or '-'}, "
                f"click_strategy={self._get_stealth_click_strategy()}"
            )
            return

        self._warmup_page_for_stealth()
        self._page_warmed_up = True

    def _stealth_dom_click_element(
        self,
        ele,
        target_key: str = "",
        selector: str = "",
        *,
        log_label: str = "STEALTH_CLICK",
    ) -> bool:
        """
        Background-safe low-entropy click path.

        CDP Input mouse events can stall when Chrome keeps a tab in the
        background input/compositor pipeline. For routine selector targets we
        can avoid stealing foreground focus by invoking the page-side click
        directly and preserving the rest of the low-entropy workflow.
        """
        if self._check_cancelled():
            return False

        started_at = time.perf_counter()
        target_label = target_key or "-"
        selector_label = self._compact_log_value(selector, 100)

        try:
            self._smart_delay(0.02, 0.06)
            result = ele.run_js(
                """
                try {
                    const el = this;
                    if (!el || !el.isConnected) {
                        return { ok: false, reason: 'not_connected' };
                    }

                    try {
                        el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
                    } catch (error) {}

                    try {
                        if (typeof el.focus === 'function') {
                            el.focus({ preventScroll: true });
                        }
                    } catch (error) {}

                    let clicked = false;
                    if (typeof el.click === 'function') {
                        el.click();
                        clicked = true;
                    } else {
                        const options = {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            button: 0,
                            buttons: 1
                        };
                        for (const type of ['mousedown', 'mouseup', 'click']) {
                            el.dispatchEvent(new MouseEvent(type, options));
                        }
                        clicked = true;
                    }

                    const active = document.activeElement === el
                        || (el.contains && el.contains(document.activeElement));
                    return {
                        ok: clicked,
                        active,
                        tag: (el.tagName || '').toLowerCase(),
                        href: el.getAttribute ? (el.getAttribute('href') || '') : ''
                    };
                } catch (error) {
                    return {
                        ok: false,
                        reason: String(error && error.message ? error.message : error || '')
                    };
                }
                """
            )
        except Exception as e:
            logger.warning(
                f"[{log_label}] 后台安全 DOM 点击异常: "
                f"target={target_label}, selector={selector_label}, error={self._compact_log_value(e, 180)}"
            )
            return False

        ok = bool(result.get("ok")) if isinstance(result, dict) else bool(result)
        elapsed = time.perf_counter() - started_at
        if ok:
            self._mouse_pos = None
            logger.debug(
                f"[{log_label}] 后台安全 DOM 点击完成: "
                f"target={target_label}, total={elapsed:.2f}s, "
                f"active={bool((result or {}).get('active')) if isinstance(result, dict) else '-'}, "
                f"strategy={self._get_stealth_click_strategy()}"
            )
            return True

        logger.warning(
            f"[{log_label}] 后台安全 DOM 点击失败: "
            f"target={target_label}, selector={selector_label}, result={self._compact_log_value(result, 180)}"
        )
        return False

    def _should_use_background_safe_dom_click(self, target_key: str = "") -> bool:
        target = str(target_key or "").strip()
        if not target:
            return False

        if self.stealth_mode:
            return self._should_use_stealth_dom_click(target)

        if getattr(self, "_workflow_scope_depth", 0) <= 0:
            return False
        if not self._should_keep_workflow_awake():
            return False
        return target in self._get_stealth_dom_click_targets()

    def _smart_delay(self, min_sec: float = None, max_sec: float = None):
        """
        隐身模式下的短延迟。

        目标是保留动作衔接的自然感，不再为了“像人”而故意放慢。
        """
        if not self.stealth_mode:
            return

        if min_sec is None:
            min_sec = BrowserConstants.get("STEALTH_DELAY_MIN")
        if max_sec is None:
            max_sec = BrowserConstants.get("STEALTH_DELAY_MAX")

        min_sec = max(0.0, float(min_sec or 0.0))
        max_sec = max(min_sec, float(max_sec or 0.0))
        if max_sec <= 0:
            return

        spread = max_sec - min_sec
        if spread <= 0:
            total_delay = min_sec
        else:
            # 反应时更接近右偏分布：短延迟常见，长延迟偶发
            median_guess = max(0.004, min_sec + spread * 0.32)
            sigma = 0.42
            sampled = random.lognormvariate(math.log(median_guess), sigma)
            total_delay = max(min_sec, min(sampled, max_sec))

        pause_prob = float(BrowserConstants.get("STEALTH_PAUSE_PROBABILITY") or 0.0)
        pause_max = max(0.0, float(BrowserConstants.get("STEALTH_PAUSE_EXTRA_MAX") or 0.0))
        if pause_prob > 0 and pause_max > 0 and random.random() < pause_prob:
            extra = random.uniform(min(0.03, pause_max), pause_max)
            total_delay = min(total_delay + extra, max_sec + pause_max)
            logger.debug(f"[STEALTH] 随机停顿 +{extra:.2f}s")

        elapsed = 0.0
        step = 0.02
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

    @staticmethod
    def _is_rect_stable(previous: dict, current: dict, tolerance: int) -> bool:
        if not previous or not current:
            return False
        for key in ("x", "y", "width", "height"):
            if abs(int(previous.get(key, 0)) - int(current.get(key, 0))) > tolerance:
                return False
        return True

    def _clamp_viewport_point(self, x: int, y: int) -> tuple[int, int]:
        vw, vh = self._get_viewport_size()
        safe_x = max(1, min(int(vw) - 1, int(x)))
        safe_y = max(1, min(int(vh) - 1, int(y)))
        return safe_x, safe_y

    def _sample_coord_click_target(self, x: int, y: int) -> Optional[dict]:
        try:
            state = self.tab.run_js(
                """
                try {
                    const x = Math.round(Number(arguments[0]) || 0);
                    const y = Math.round(Number(arguments[1]) || 0);
                    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
                    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
                    const insideViewport = x >= 0 && y >= 0 && x <= vw && y <= vh;
                    const top = insideViewport && document.elementFromPoint ? document.elementFromPoint(x, y) : null;
                    if (!top) {
                        return {
                            ok: false,
                            insideViewport,
                            viewport: { width: vw, height: vh }
                        };
                    }

                    const rect = top.getBoundingClientRect ? top.getBoundingClientRect() : null;
                    const style = window.getComputedStyle ? window.getComputedStyle(top) : null;
                    const rectData = rect ? {
                        x: Math.round(rect.x || 0),
                        y: Math.round(rect.y || 0),
                        width: Math.round(rect.width || 0),
                        height: Math.round(rect.height || 0)
                    } : null;
                    const classText = String(top.className || "");
                    const tag = String(top.tagName || "").toLowerCase();
                    return {
                        ok: true,
                        insideViewport,
                        viewport: { width: vw, height: vh },
                        tag,
                        className: classText,
                        id: String(top.id || ""),
                        text: String(top.innerText || top.textContent || "").slice(0, 120),
                        pointerEvents: style ? String(style.pointerEvents || "") : "",
                        disabled: !!top.disabled || top.getAttribute('aria-disabled') === 'true',
                        rect: rectData
                    };
                } catch (error) {
                    return {
                        ok: false,
                        error: String(error && error.message ? error.message : error || "")
                    };
                }
                """,
                x,
                y,
            )
        except Exception as e:
            logger.debug(f"[COORD_CLICK] 坐标探测失败（忽略）: {e}")
            return None
        return state if isinstance(state, dict) else None

    def _pick_coord_click_safe_point(self, x: int, y: int, sample: Optional[dict], edge_inset: int) -> tuple[int, int]:
        click_x, click_y = self._clamp_viewport_point(x, y)
        rect = (sample or {}).get("rect") if isinstance(sample, dict) else None
        if not isinstance(rect, dict):
            return click_x, click_y

        rect_x = int(rect.get("x", click_x))
        rect_y = int(rect.get("y", click_y))
        rect_w = max(0, int(rect.get("width", 0)))
        rect_h = max(0, int(rect.get("height", 0)))
        if rect_w <= 0 or rect_h <= 0:
            return click_x, click_y

        inner_left = rect_x + min(edge_inset, max(0, rect_w // 3))
        inner_top = rect_y + min(edge_inset, max(0, rect_h // 3))
        inner_right = rect_x + rect_w - min(edge_inset, max(0, rect_w // 3))
        inner_bottom = rect_y + rect_h - min(edge_inset, max(0, rect_h // 3))

        if inner_left > inner_right:
            inner_left = rect_x
            inner_right = rect_x + rect_w
        if inner_top > inner_bottom:
            inner_top = rect_y
            inner_bottom = rect_y + rect_h

        safe_x = min(max(click_x, inner_left), inner_right)
        safe_y = min(max(click_y, inner_top), inner_bottom)
        return self._clamp_viewport_point(safe_x, safe_y)

    def _wait_for_coord_click_target_ready(self, x: int, y: int) -> tuple[int, int, Optional[dict]]:
        settings = self._get_coord_click_settings()
        timeout = float(settings["ready_timeout"])
        if timeout <= 0:
            safe_x, safe_y = self._clamp_viewport_point(x, y)
            sample = self._sample_coord_click_target(safe_x, safe_y)
            tuned_x, tuned_y = self._pick_coord_click_safe_point(
                safe_x, safe_y, sample, int(settings["edge_inset"])
            )
            return tuned_x, tuned_y, sample

        stable_needed = max(1, int(settings["stable_samples"]))
        sample_interval = max(0.02, float(settings["sample_interval"]))
        rect_tolerance = max(0, int(settings["rect_tolerance"]))
        deadline = time.time() + timeout

        safe_x, safe_y = self._clamp_viewport_point(x, y)
        last_rect = None
        last_signature = None
        stable_count = 0
        latest_sample = None

        while time.time() < deadline:
            if self._check_cancelled():
                break

            latest_sample = self._sample_coord_click_target(safe_x, safe_y)
            rect = (latest_sample or {}).get("rect") if isinstance(latest_sample, dict) else None
            if latest_sample and latest_sample.get("ok") and rect:
                signature = (
                    str(latest_sample.get("tag") or ""),
                    str(latest_sample.get("id") or ""),
                    str(latest_sample.get("className") or "")[:120],
                )
                rect_ok = self._is_rect_stable(last_rect, rect, rect_tolerance) if last_rect else False
                if signature == last_signature and rect_ok:
                    stable_count += 1
                else:
                    stable_count = 1
                    last_signature = signature
                last_rect = rect
                if stable_count >= stable_needed:
                    break
            time.sleep(sample_interval)

        tuned_x, tuned_y = self._pick_coord_click_safe_point(
            safe_x, safe_y, latest_sample, int(settings["edge_inset"])
        )
        return tuned_x, tuned_y, latest_sample

    def _iter_coord_click_candidates(self, base_x: int, base_y: int, sample: Optional[dict]):
        settings = self._get_coord_click_settings()
        seen = set()
        offsets = settings.get("retry_offsets") or []

        for item in offsets:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                dx = int(item[0])
                dy = int(item[1])
            except Exception:
                continue

            cand_x, cand_y = self._clamp_viewport_point(base_x + dx, base_y + dy)
            cand_x, cand_y = self._pick_coord_click_safe_point(
                cand_x,
                cand_y,
                sample,
                int(settings["edge_inset"]),
            )
            key = (cand_x, cand_y)
            if key in seen:
                continue
            seen.add(key)
            yield cand_x, cand_y

    def _coord_dom_click_at(self, x: int, y: int, sample: Optional[dict] = None) -> bool:
        """Background-friendly coord click using page-side events before CDP fallback."""
        try:
            result = self.tab.run_js(
                """
                return (function() {
                    try {
                        const x = Math.round(Number(arguments[0]) || 0);
                        const y = Math.round(Number(arguments[1]) || 0);
                        const target = document.elementFromPoint ? document.elementFromPoint(x, y) : null;
                        if (!target) {
                            return { ok: false, reason: 'no_target' };
                        }

                        const clickable = target.closest(
                            'button, a, summary, label, option, [role="button"], [role="menuitem"], [role="tab"], [role="option"], [aria-haspopup], input[type="button"], input[type="submit"], input[type="checkbox"], input[type="radio"], [tabindex]'
                        ) || target;

                        try {
                            clickable.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
                        } catch (error) {}
                        try {
                            clickable.focus?.({ preventScroll: true });
                        } catch (error) {
                            try { clickable.focus?.(); } catch (error2) {}
                        }

                        const baseMouse = {
                            bubbles: true,
                            cancelable: true,
                            clientX: x,
                            clientY: y,
                            button: 0,
                            buttons: 1,
                            view: window
                        };
                        const basePointer = {
                            bubbles: true,
                            cancelable: true,
                            clientX: x,
                            clientY: y,
                            button: 0,
                            buttons: 1,
                            pointerId: 1,
                            pointerType: 'mouse',
                            isPrimary: true,
                            view: window
                        };

                        try {
                            clickable.dispatchEvent(new MouseEvent('mousemove', baseMouse));
                        } catch (error) {}
                        try {
                            if (typeof PointerEvent === 'function') {
                                clickable.dispatchEvent(new PointerEvent('pointerdown', basePointer));
                            }
                        } catch (error) {}
                        try {
                            clickable.dispatchEvent(new MouseEvent('mousedown', baseMouse));
                        } catch (error) {}
                        try {
                            if (typeof PointerEvent === 'function') {
                                clickable.dispatchEvent(new PointerEvent('pointerup', basePointer));
                            }
                        } catch (error) {}
                        try {
                            clickable.dispatchEvent(new MouseEvent('mouseup', baseMouse));
                        } catch (error) {}

                        let clickInvoked = false;
                        try {
                            if (typeof clickable.click === 'function') {
                                clickable.click();
                                clickInvoked = true;
                            }
                        } catch (error) {}

                        if (!clickInvoked) {
                            try {
                                clickable.dispatchEvent(new MouseEvent('click', {
                                    ...baseMouse,
                                    buttons: 0
                                }));
                                clickInvoked = true;
                            } catch (error) {}
                        }

                        return {
                            ok: !!clickInvoked,
                            tag: String(clickable.tagName || '').toLowerCase(),
                            id: String(clickable.id || ''),
                            className: String(clickable.className || '').slice(0, 120),
                            active: document.activeElement === clickable
                                || (clickable.contains && clickable.contains(document.activeElement))
                        };
                    } catch (error) {
                        return {
                            ok: false,
                            reason: String(error && error.message ? error.message : error || '')
                        };
                    }
                })();
                """,
                x,
                y,
            )
        except Exception as e:
            logger.debug(f"[COORD_CLICK] 页面内坐标点击异常（忽略）: {e}")
            return False

        ok = bool((result or {}).get("ok")) if isinstance(result, dict) else bool(result)
        if ok:
            logger.debug(
                "[COORD_CLICK] 页面内坐标点击完成: "
                f"point=({x}, {y}), active={bool((result or {}).get('active')) if isinstance(result, dict) else '-'}, "
                f"tag={self._compact_log_value((result or {}).get('tag') if isinstance(result, dict) else '', 40)}"
            )
            self._mouse_pos = None
            return True

        logger.debug(
            "[COORD_CLICK] 页面内坐标点击未确认，回退 CDP: "
            f"point=({x}, {y}), result={self._compact_log_value(result, 180)}"
        )
        return False
    
    # ================= 步骤执行 =================

    @staticmethod
    def _compact_log_value(value: Any, max_len: int = 120) -> str:
        text = str(value or "").replace("\r", "\\r").replace("\n", "\\n").strip()
        if not text:
            return "-"
        if len(text) > max_len:
            return f"{text[:max(0, max_len - 3)]}..."
        return text

    def _describe_element_for_log(self, ele) -> str:
        if ele is None:
            return "element=None"

        parts = []
        tag = str(getattr(ele, "tag", "") or "").strip()
        backend_id = getattr(ele, "_backend_id", None)
        if tag:
            parts.append(f"tag={tag}")
        if backend_id is not None:
            parts.append(f"backend={backend_id}")

        try:
            rect = getattr(ele, "rect", None)
            location = getattr(rect, "location", None)
            size = getattr(rect, "size", None)
            if location and size:
                parts.append(
                    f"rect=({int(location[0])},{int(location[1])},"
                    f"{int(size[0])},{int(size[1])})"
                )
        except Exception:
            pass

        return " ".join(parts) if parts else f"type={type(ele).__name__}"

    def _execute_click(self, selector: str, target_key: str, optional: bool):
        """执行点击操作（v5.7 隐身模式人类化点击）"""
        if self._check_cancelled():
            return

        last_error = None
        found_element = False
        pre_click_url = ""
        before_len = 0
        for attempt in range(2):
            try:
                with self._page_interaction_slot("CLICK", target_key) as acquired:
                    if not acquired or self._check_cancelled():
                        return

                    ele = self.finder.find_with_fallback(selector, target_key)
                    if not ele:
                        break
                    found_element = True

                    ele = self._wait_for_element_interactable(ele, selector, target_key)

                    if target_key in {"new_chat_btn", "new_chat", "new_conversation"}:
                        try:
                            pre_click_url = str(self.tab.url or "")
                            logger.debug(f"[CLICK_NEW_CHAT] 点击新建对话前 URL: {pre_click_url}")
                        except Exception:
                            pass

                    if target_key == "send_btn":
                        send_url = ""
                        try:
                            send_url = str(self.tab.url or "")
                        except Exception:
                            pass
                        before_len = self._safe_get_input_len_by_key("input_box")
                        logger.debug(f"[SEND_CLICK_START] 开始点击发送按钮, 当前 URL: {send_url}, 发送前输入框长度: {before_len}")
                        
                        expected_len = 0
                        if isinstance(getattr(self, "_context", None), dict):
                            expected_len = len(str(self._context.get("prompt", "") or ""))
                        if before_len <= 0 and expected_len > 0:
                            logger.error(
                                f"[SEND_CLICK_ERROR] 发送前检测到输入框为空，但预期输入长度为 {expected_len}。"
                                "这通常是由于超长文本粘贴时 React 状态未同步完成，失焦/点击触发了值覆写置空。"
                            )
                            settle_wait = min(
                                max(0.08, float(self._get_send_confirmation_check_timeout() or 0.0) * 0.25),
                                0.35,
                            )
                            deadline = time.time() + settle_wait
                            while time.time() < deadline and before_len <= 0:
                                time.sleep(0.05)
                                before_len = self._safe_get_input_len_by_key("input_box")
                            if before_len > 0:
                                logger.debug(
                                    f"[SEND_CLICK_RECOVER] 发送前输入框长度已恢复: "
                                    f"before_len={before_len}, expected_len={expected_len}"
                                )
                            else:
                                logger.warning(
                                    "[SEND_CLICK_SKIP] 发送前输入框仍为空，跳过硬失败并继续发送观察: "
                                    f"expected_len={expected_len}, settle_wait={settle_wait:.2f}s"
                                )

                    if self.stealth_mode:
                        if self._should_use_background_safe_dom_click(target_key):
                            if not self._stealth_dom_click_element(
                                ele,
                                target_key=target_key,
                                selector=selector,
                                log_label="STEALTH_CLICK",
                            ):
                                raise WorkflowError("stealth_dom_click_failed")
                        else:
                            self._stealth_click_element(ele, target_key=target_key, selector=selector)
                    else:
                        if self._check_cancelled():
                            return
                        if self._should_use_background_safe_dom_click(target_key):
                            if not self._stealth_dom_click_element(
                                ele,
                                target_key=target_key,
                                selector=selector,
                                log_label="INTERACT_CLICK",
                            ):
                                ele.click()
                        else:
                            ele.click()

                if target_key == "send_btn":
                    self._capture_dom_send_baseline("click")
                self._smart_delay(
                    BrowserConstants.ACTION_DELAY_MIN,
                    BrowserConstants.ACTION_DELAY_MAX
                )
                if target_key in {"new_chat_btn", "new_chat", "new_conversation"}:
                    self._input_stability_wait_pending = True
                    if pre_click_url:
                        self._last_new_chat_clicked_url = pre_click_url
                        self._last_new_chat_clicked_at = time.time()
                        self._last_new_chat_clicked_snapshot = {
                            "url": pre_click_url,
                            "session_id": str(getattr(self.session, "id", "") or ""),
                            "tab_id": str(getattr(self.tab, "tab_id", "") or ""),
                            "tab_ref_id": id(self.tab),
                            "clicked_at": self._last_new_chat_clicked_at,
                        }
                if target_key == "send_btn":
                    self._confirm_send_click_response_or_raise(before_len)
                return

            except Exception as click_err:
                if isinstance(click_err, WorkflowError) and str(click_err) in {"send_unconfirmed"}:
                    raise
                last_error = click_err
                logger.warning(
                    "[CLICK] 点击失败: "
                    f"target={target_key or '-'}, attempt={attempt + 1}/2, "
                    f"stealth={bool(self.stealth_mode)}, optional={bool(optional)}, "
                    f"will_retry={bool(attempt == 0 and target_key != 'send_btn')}, "
                    f"selector={self._compact_log_value(selector, 100)}, "
                    f"error={self._compact_log_value(click_err, 180)}"
                )
                if attempt == 0 and target_key != "send_btn":
                    time.sleep(0.12)
                    continue
                break

        if found_element:
            if target_key == "send_btn":
                logger.warning(f"[CLICK] 发送按钮点击失败，降级到 Enter 键: {last_error}")
                self._execute_keypress("Enter")
            elif self.stealth_mode and last_error is not None:
                raise last_error
        elif target_key == "send_btn":
            self._execute_keypress("Enter")
        
        elif not optional:
            raise ElementNotFoundError(f"点击目标未找到: {selector}")

    def _execute_coord_click(self, value: Any, optional: bool):
        """执行坐标点击动作。"""
        if self._check_cancelled():
            return

        if not isinstance(value, dict):
            if optional:
                logger.warning("[COORD_CLICK] 缺少坐标配置，已跳过")
                return
            raise WorkflowError("coord_click_missing_value")

        try:
            x = int(value.get("x"))
            y = int(value.get("y"))
        except Exception:
            if optional:
                logger.warning(f"[COORD_CLICK] 坐标无效，已跳过: {value}")
                return
            raise WorkflowError("coord_click_invalid_position")

        radius = max(0, int(value.get("random_radius", 0) or 0))
        click_x = x + random.randint(-radius, radius) if radius > 0 else x
        click_y = y + random.randint(-radius, radius) if radius > 0 else y

        try:
            with self._page_interaction_slot("COORD_CLICK", "coord_click") as acquired:
                if not acquired or self._check_cancelled():
                    return
                tuned_x, tuned_y, sample = self._wait_for_coord_click_target_ready(click_x, click_y)
                self._human_cdp_click_at(tuned_x, tuned_y, sample=sample)
            self._smart_delay(
                BrowserConstants.ACTION_DELAY_MIN,
                BrowserConstants.ACTION_DELAY_MAX
            )
        except Exception:
            if optional:
                logger.warning(f"[COORD_CLICK] 点击失败，已跳过: ({click_x}, {click_y})")
                return
            raise

    def _execute_coord_scroll(self, value: Any, optional: bool):
        """执行坐标滚轮滑动。"""
        if self._check_cancelled():
            return

        if not isinstance(value, dict):
            if optional:
                logger.warning("[COORD_SCROLL] 缺少滑动配置，已跳过")
                return
            raise WorkflowError("coord_scroll_missing_value")

        try:
            start_x = int(value.get("start_x"))
            start_y = int(value.get("start_y"))
            end_x = int(value.get("end_x"))
            end_y = int(value.get("end_y"))
        except Exception:
            if optional:
                logger.warning(f"[COORD_SCROLL] 坐标无效，已跳过: {value}")
                return
            raise WorkflowError("coord_scroll_invalid_position")

        try:
            with self._page_interaction_slot("COORD_SCROLL", "coord_scroll") as acquired:
                if not acquired or self._check_cancelled():
                    return
                if self.stealth_mode:
                    self._human_scroll_at(start_x, start_y, end_x, end_y)
                else:
                    self._direct_scroll_at(start_x, start_y, end_x, end_y)

            self._smart_delay(
                BrowserConstants.ACTION_DELAY_MIN,
                BrowserConstants.ACTION_DELAY_MAX
            )
        except Exception:
            if optional:
                logger.warning(
                    f"[COORD_SCROLL] 滑动失败，已跳过: "
                    f"({start_x}, {start_y}) -> ({end_x}, {end_y})"
                )
                return
            raise

    def _ensure_mouse_origin(self) -> tuple:
        """
        确保存在一个页面内鼠标起点。

        只使用 CDP mouseMoved 建立当前位置，不走 tab.actions / ele.click。
        """
        if self._mouse_pos is not None:
            return self._mouse_pos

        from app.utils.human_mouse import _dispatch_mouse_move

        vw, vh = self._get_viewport_size()
        origin_x = random.randint(max(40, int(vw * 0.18)), max(60, int(vw * 0.42)))
        origin_y = random.randint(max(40, int(vh * 0.16)), max(60, int(vh * 0.45)))

        _dispatch_mouse_move(self.tab, origin_x, origin_y)
        self._mouse_pos = (origin_x, origin_y)
        time.sleep(random.uniform(0.01, 0.04))
        return self._mouse_pos

    def _flash_click_marker(self, x: int, y: int):
        """在页面上短暂标记实际点击坐标，便于排查坐标系问题。"""
        try:
            self.tab.run_js(
                """
                const x = arguments[0];
                const y = arguments[1];
                const id = '__coord_click_debug_marker__';
                document.getElementById(id)?.remove();
                const dot = document.createElement('div');
                dot.id = id;
                Object.assign(dot.style, {
                    position: 'fixed',
                    left: `${x - 6}px`,
                    top: `${y - 6}px`,
                    width: '12px',
                    height: '12px',
                    borderRadius: '9999px',
                    background: 'rgba(255, 59, 48, 0.95)',
                    border: '2px solid #fff',
                    boxShadow: '0 0 0 2px rgba(255, 59, 48, 0.35)',
                    zIndex: '2147483647',
                    pointerEvents: 'none'
                });
                document.body.appendChild(dot);
                setTimeout(() => dot.remove(), 900);
                """,
                x,
                y
            )
        except Exception:
            pass

    def _human_cdp_click_at(self, x: int, y: int, sample: Optional[dict] = None):
        """
        使用 human_mouse 轨迹移动，并以 CDP 精确点击结束。

        链路固定为：
        页面内某处起点 -> smooth_move_mouse -> 短暂停顿/微漂移 -> cdp_precise_click
        """
        if self._check_cancelled():
            return

        candidates = list(self._iter_coord_click_candidates(x, y, sample))
        last_error = None

        for attempt_index, (cand_x, cand_y) in enumerate(candidates, start=1):
            attempt_started_at = time.perf_counter()
            self._flash_click_marker(cand_x, cand_y)
            logger.debug(
                f"[COORD_CLICK] viewport click at ({cand_x}, {cand_y}) "
                f"(attempt={attempt_index}/{max(1, len(candidates))})"
            )

            if not self.stealth_mode and self._coord_dom_click_at(cand_x, cand_y, sample=sample):
                logger.debug(
                    f"[COORD_CLICK] attempt={attempt_index} 使用页面内坐标点击成功 "
                    f"(elapsed={time.perf_counter() - attempt_started_at:.2f}s)"
                )
                return

            start_pos = self._ensure_mouse_origin()
            move_started_at = time.perf_counter()

            self._mouse_pos = smooth_move_mouse(
                tab=self.tab,
                from_pos=start_pos,
                to_pos=(cand_x, cand_y),
                check_cancelled=self._check_cancelled
            )
            logger.debug(
                f"[COORD_CLICK] attempt={attempt_index} 鼠标移动完成 "
                f"(elapsed={time.perf_counter() - move_started_at:.2f}s)"
            )

            if self._check_cancelled():
                return

            if random.random() < 0.65:
                self._mouse_pos = idle_drift(
                    tab=self.tab,
                    duration=random.uniform(0.02, 0.05),
                    center_pos=self._mouse_pos,
                    check_cancelled=self._check_cancelled,
                    drift_radius=random.uniform(0.8, 1.8),
                    freq_hz=random.uniform(7.0, 11.0)
                )
            else:
                time.sleep(random.uniform(0.015, 0.035))

            if self._check_cancelled():
                return

            click_started_at = time.perf_counter()
            success = cdp_precise_click(
                tab=self.tab,
                x=cand_x,
                y=cand_y,
                check_cancelled=self._check_cancelled
            )
            logger.debug(
                f"[COORD_CLICK] attempt={attempt_index} CDP 点击返回 "
                f"(success={bool(success)}, elapsed={time.perf_counter() - click_started_at:.2f}s)"
            )
            if success:
                self._mouse_pos = (cand_x, cand_y)
                return

            last_error = (cand_x, cand_y)
            if attempt_index < len(candidates):
                logger.warning(
                    f"[CDP_CLICK] 坐标点击失败，尝试附近回退点: "
                    f"({cand_x}, {cand_y}) -> next"
                )
                time.sleep(random.uniform(0.03, 0.08))

        raise WorkflowError(
            f"coord_click_failed:{last_error[0]},{last_error[1]}" if last_error else "coord_click_failed"
        )

    def _direct_scroll_at(self, start_x: int, start_y: int, end_x: int, end_y: int):
        """普通模式下执行坐标滚轮滑动。"""
        total_dx = end_x - start_x
        total_dy = end_y - start_y
        logger.debug(
            f"[COORD_SCROLL] normal wheel scroll: "
            f"({start_x}, {start_y}) -> ({end_x}, {end_y})"
        )

        steps = max(3, min(12, int(max(abs(total_dx), abs(total_dy)) / 90) + 1))
        prev_dx = 0
        prev_dy = 0

        for i in range(1, steps + 1):
            if self._check_cancelled():
                return

            t = i / steps
            anchor_x = int(round(start_x + total_dx * t))
            anchor_y = int(round(start_y + total_dy * t))
            scroll_dx = int(round(total_dx * t)) - prev_dx
            scroll_dy = int(round(total_dy * t)) - prev_dy

            self.tab.run_cdp(
                'Input.dispatchMouseEvent',
                type='mouseMoved',
                x=anchor_x,
                y=anchor_y,
                button='none',
                buttons=0,
                modifiers=0,
                pointerType='mouse'
            )
            self.tab.run_cdp(
                'Input.dispatchMouseEvent',
                type='mouseWheel',
                x=anchor_x,
                y=anchor_y,
                deltaX=scroll_dx,
                deltaY=scroll_dy,
                button='none',
                buttons=0,
                pointerType='mouse'
            )

            prev_dx += scroll_dx
            prev_dy += scroll_dy

            if i < steps:
                time.sleep(random.uniform(0.02, 0.06))

        self._mouse_pos = (end_x, end_y)

    def _human_scroll_at(self, start_x: int, start_y: int, end_x: int, end_y: int):
        """隐身模式下执行人类化坐标滚轮滑动。"""
        logger.debug(
            f"[COORD_SCROLL] stealth wheel scroll: "
            f"({start_x}, {start_y}) -> ({end_x}, {end_y})"
        )

        start_pos = self._ensure_mouse_origin()
        self._mouse_pos = smooth_move_mouse(
            tab=self.tab,
            from_pos=start_pos,
            to_pos=(start_x, start_y),
            check_cancelled=self._check_cancelled
        )

        if self._check_cancelled():
            return

        if random.random() < 0.6:
            self._mouse_pos = idle_drift(
                tab=self.tab,
                duration=random.uniform(0.02, 0.05),
                center_pos=self._mouse_pos,
                check_cancelled=self._check_cancelled,
                drift_radius=random.uniform(0.8, 1.8),
                freq_hz=random.uniform(7.0, 10.0)
            )
        else:
            time.sleep(random.uniform(0.015, 0.035))

        if self._check_cancelled():
            return

        self._mouse_pos = human_scroll_path(
            tab=self.tab,
            from_pos=(start_x, start_y),
            to_pos=(end_x, end_y),
            check_cancelled=self._check_cancelled
        )
    
    def _stealth_click_element(self, ele, target_key: str = "", selector: str = ""):
        """
        隐身模式人类化点击（v5.9 — 彻底消灭 ele.click() 降级路径）
        
        关键：
        - 所有路径均使用 cdp_precise_click（force=0.5），绝不降级到 ele.click()
        - 坐标仅走原生属性链路，失败即抛错，不执行页面 JS 坐标注入
        - 若坐标完全无法获取，抛出异常由上层处理（而非偷偷用 ele.click() 触发 CF）
        """
        if self._check_cancelled():
            return

        click_started_at = time.perf_counter()
        target_label = target_key or "-"
        selector_label = self._compact_log_value(selector, 100)
        element_label = self._describe_element_for_log(ele)
        
        # 1. 获取元素坐标（多重尝试）
        target = self._get_element_viewport_pos(ele)
        if target is None:
            logger.error(
                "[STEALTH_CLICK] 坐标获取失败: "
                f"target={target_label}, selector={selector_label}, "
                f"mouse={self._mouse_pos or '-'}, element={element_label}"
            )
            raise Exception("[STEALTH] 无法通过原生链路获取元素坐标，拒绝注入 JS 与 ele.click() 降级")
        target_ready_at = time.perf_counter()
        
        # 二维高斯落点：中心密集、边缘稀疏，更接近人类点击热力图
        sigma_x = 3.0
        sigma_y = 2.0
        click_x = target[0] + int(random.gauss(0, sigma_x))
        click_y = target[1] + int(random.gauss(0, sigma_y))
        click_x = max(target[0] - 8, min(target[0] + 8, click_x))
        click_y = max(target[1] - 6, min(target[1] + 6, click_y))
        
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
        move_finished_at = time.perf_counter()
        
        if self._check_cancelled():
            return
        
        # 3. 极短停顿/微漂移，让点击衔接自然但不拖节奏
        if random.random() < 0.6:
            self._mouse_pos = idle_drift(
                tab=self.tab,
                duration=random.uniform(0.02, 0.05),
                center_pos=self._mouse_pos,
                check_cancelled=self._check_cancelled,
                drift_radius=random.uniform(0.8, 1.6),
                freq_hz=random.uniform(7.0, 11.0)
            )
        else:
            time.sleep(random.uniform(0.015, 0.035))

        if self._check_cancelled():
            return

        # 点击前确认停顿：右偏分布，常见短停顿，偶发更长确认
        hesitation = random.lognormvariate(math.log(0.15), 0.4)
        hesitation = max(0.06, min(hesitation, 0.4))
        self._idle_wait(hesitation)
        
        # 4. 精确 CDP 点击（含 force=0.5 修复）
        success = cdp_precise_click(
            tab=self.tab,
            x=click_x,
            y=click_y,
            check_cancelled=self._check_cancelled
        )
        
        if not success:
            # 🔴 CDP 点击失败也不降级到 ele.click()，而是重试一次
            logger.warning(
                "[STEALTH_CLICK] CDP 点击失败，准备重试: "
                f"target={target_label}, click=({click_x},{click_y}), "
                f"target_center=({target[0]},{target[1]}), "
                f"element={element_label}"
            )
            time.sleep(random.uniform(0.04, 0.10))
            success = cdp_precise_click(
                tab=self.tab,
                x=click_x,
                y=click_y,
                check_cancelled=self._check_cancelled
            )
            if not success:
                failed_at = time.perf_counter()
                logger.error(
                    "[STEALTH_CLICK] CDP 点击两次失败: "
                    f"target={target_label}, selector={selector_label}, "
                    f"click=({click_x},{click_y}), target_center=({target[0]},{target[1]}), "
                    f"coord={target_ready_at - click_started_at:.2f}s, "
                    f"move={move_finished_at - target_ready_at:.2f}s, "
                    f"click={failed_at - move_finished_at:.2f}s, "
                    f"total={failed_at - click_started_at:.2f}s, "
                    f"element={element_label}"
                )
                raise Exception(
                    "[STEALTH] CDP 精确点击两次均失败 "
                    f"(target={target_label}, click=({click_x},{click_y}))"
                )
        
        # 更新鼠标位置
        self._mouse_pos = (click_x, click_y)
        click_finished_at = time.perf_counter()

        coord_elapsed = target_ready_at - click_started_at
        move_elapsed = move_finished_at - target_ready_at
        click_elapsed = click_finished_at - move_finished_at
        total_elapsed = click_finished_at - click_started_at

        if total_elapsed > 1.2 or coord_elapsed > 0.8 or move_elapsed > 0.8 or click_elapsed > 0.8:
            logger.warning(
                "[STEALTH] 人类化点击耗时异常 "
                f"(coord={coord_elapsed:.2f}s, move={move_elapsed:.2f}s, "
                f"click={click_elapsed:.2f}s, total={total_elapsed:.2f}s, "
                f"target=({target[0]}, {target[1]}), click=({click_x}, {click_y}))"
            )
        
        logger.debug(
            "[STEALTH_CLICK] 完成: "
            f"target={target_label}, click=({click_x},{click_y}), "
            f"target_center=({target[0]},{target[1]}), total={total_elapsed:.2f}s"
        )
    
    # ================= 可靠发送 =================

    def _safe_get_input_len_by_key(self, target_key: str) -> int:
        """读取输入框当前长度（防多标签干扰与后台 activeElement 漂移版）"""
        try:
            selector = self._resolve_input_length_selector(target_key)
            if not selector:
                return 0

            n = self.tab.run_js("""
                try {
                    const sel = String(arguments[0] || '').trim();
                    let root = sel ? document.querySelector(sel) : null;
                    if (!root) return 0;

                    const target = (() => {
                        const tag = (root.tagName || '').toLowerCase();
                        const isCE = root.isContentEditable || root.getAttribute('contenteditable') === 'true';
                        if (tag === 'textarea' || tag === 'input' || isCE) return root;
                        if (typeof root.querySelector !== 'function') return null;
                        return root.querySelector('textarea, input, [contenteditable="true"]');
                    })();
                    if (!target) return 0;

                    const tag = (target.tagName || '').toLowerCase();
                    if (tag === 'textarea' || tag === 'input') return (target.value || '').length;
                    if (target.isContentEditable || target.getAttribute('contenteditable') === 'true') return (target.innerText || '').length;
                    return 0;
                } catch(e){ return 0; }
            """, selector)

            if n is not None:
                return int(n)
            return 0
        except Exception:
            return 0
    
    def _is_send_success(self, before_len: int, after_len: int) -> bool:
        """判断是否发送成功"""
        try:
            before_len = int(before_len)
            after_len = int(after_len)
            if before_len <= 0:
                return False
            if after_len == 0:
                return True
            if after_len <= int(before_len * 0.4):
                return True
            return False
        except Exception:
            return False

    def _is_send_confirmation_check_enabled(self) -> bool:
        advanced = self._site_advanced_config if isinstance(self._site_advanced_config, dict) else {}
        return self._coerce_bool(
            advanced.get("send_confirmation_check_enabled"),
            False,
        )

    def _get_send_confirmation_check_timeout(self) -> float:
        advanced = self._site_advanced_config if isinstance(self._site_advanced_config, dict) else {}
        timeout = self._coerce_float(
            advanced.get("send_confirmation_check_timeout"),
            1.5,
            minimum=0.1,
        )
        return min(timeout, 10.0)

    def _confirm_send_click_response_or_raise(self, before_len: int) -> None:
        """Confirm the page physically reacted to send_btn by clearing/shrinking input."""
        timeout = self._get_send_confirmation_check_timeout()
        interval = 0.1
        started_at = time.perf_counter()
        latest_len = before_len

        if not self._is_send_confirmation_check_enabled():
            try:
                time.sleep(0.12)
                latest_len = self._safe_get_input_len_by_key("input_box")
                is_success = self._is_send_success(before_len, latest_len)
                logger.debug(
                    f"[SEND_CLICK_END] 发送按钮点击完成. 发送前长度: {before_len}, "
                    f"发送后长度: {latest_len}, 判定成功={is_success}, 当前 URL: {self.tab.url}"
                )
            except Exception:
                pass
            return

        if int(before_len or 0) <= 0:
            try:
                time.sleep(0.12)
                latest_len = self._safe_get_input_len_by_key("input_box")
                logger.debug(
                    f"[SEND_CLICK_END] 发送按钮点击完成. 发送前长度: {before_len}, "
                    f"发送后长度: {latest_len}, 判定成功=False, 当前 URL: {self.tab.url}"
                )
            except Exception:
                pass
            logger.warning(
                "[SEND_CONFIRM_SKIP] 发送前输入框为空，长度无法作为硬确认信号，交由后续发送观察: "
                f"before_len={before_len}, timeout={timeout:.1f}s"
            )
            return

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._check_cancelled():
                return

            try:
                latest_len = self._safe_get_input_len_by_key("input_box")
            except Exception:
                latest_len = before_len

            if self._is_send_success(before_len, latest_len):
                logger.info(
                    "[SEND_CONFIRM] 发送按钮物理响应已确认: "
                    f"before_len={before_len}, after_len={latest_len}, "
                    f"elapsed={time.perf_counter() - started_at:.2f}s"
                )
                return

            time.sleep(interval)

        try:
            current_url = str(self.tab.url or "")
        except Exception:
            current_url = ""
        logger.warning(
            "[SEND_CONFIRM_TIMEOUT] 发送确认超时，输入框仍未满足确认条件，触发工作流重试: "
            f"before_len={before_len}, after_len={latest_len}, "
            f"timeout={timeout:.1f}s, url={self._compact_log_value(current_url, 140)}"
        )
        raise WorkflowError("send_unconfirmed")

    # ================= 隐身模式页面预热 =================
    
    def _warmup_page_for_stealth(self):
        """
        页面预热（极速简化版）

        仅建立一个合理的鼠标起点，避免首个动作过于突兀，
        不再为了“拟人”加入明显的停顿和扫视。
        """
        warmup_started_at = time.perf_counter()

        try:
            from app.utils.human_mouse import _dispatch_mouse_move
            
            vw, vh = self._get_viewport_size()
            
            # 初始化鼠标位置（视口中上部，模拟"刚把鼠标放到页面"）
            init_x = vw // 2 + random.randint(-80, 80)
            init_y = int(vh * 0.3) + random.randint(-40, 40)
            self._mouse_pos = (init_x, init_y)
            _dispatch_mouse_move(self.tab, init_x, init_y)
            
            # 仅保留极短缓冲，避免首个动作过于生硬
            self._idle_wait(random.uniform(0.08, 0.18))
            
            if self._check_cancelled():
                return
            
            # 最多一次轻微修正，保持动作连贯
            move_count = 1 if random.random() < 0.45 else 0
            for i in range(move_count):
                if self._check_cancelled():
                    return
                
                # 小幅移动（仅做起手姿态修正）
                dx = random.randint(-int(vw * 0.08), int(vw * 0.08))
                dy = random.randint(-int(vh * 0.06), int(vh * 0.06))
                target_x = max(50, min(vw - 50, self._mouse_pos[0] + dx))
                target_y = max(50, min(vh - 50, self._mouse_pos[1] + dy))
                
                self._mouse_pos = smooth_move_mouse(
                    tab=self.tab,
                    from_pos=self._mouse_pos,
                    to_pos=(target_x, target_y),
                    check_cancelled=self._check_cancelled
                )
                
                self._idle_wait(random.uniform(0.04, 0.10))

            self._idle_wait(random.uniform(0.05, 0.12))
            
            logger.debug(
                "[STEALTH] 页面预热完成: "
                f"moves={move_count}, origin=({init_x},{init_y}), "
                f"elapsed={time.perf_counter() - warmup_started_at:.2f}s"
            )

        except Exception as e:
            logger.debug(f"[STEALTH] 页面预热异常（可忽略）: {e}")

    def _should_wait_for_new_chat_url_transition(self) -> bool:
        advanced = self._site_advanced_config if isinstance(self._site_advanced_config, dict) else {}
        return self._coerce_bool(
            advanced.get("url_transition_wait_on_new_chat"),
            False,
        )

    def _get_new_chat_url_transition_patterns(self) -> list[str]:
        advanced = self._site_advanced_config if isinstance(self._site_advanced_config, dict) else {}
        raw_patterns = advanced.get("url_transition_wait_patterns") or []
        if isinstance(raw_patterns, str):
            raw_patterns = raw_patterns.replace("\n", ",").replace(";", ",").split(",")
        if not isinstance(raw_patterns, (list, tuple, set)):
            return []
        return [
            str(pattern or "").strip()
            for pattern in raw_patterns
            if str(pattern or "").strip()
        ]

    @classmethod
    def _is_likely_chat_session_url(
        cls,
        url: str,
        patterns: Optional[list[str]] = None,
    ) -> bool:
        text = str(url or "").strip()
        if not text:
            return False

        for pattern in patterns or []:
            pattern_text = str(pattern or "").strip()
            if not pattern_text:
                continue
            if pattern_text in text:
                return True
            try:
                if re.search(pattern_text, text):
                    return True
            except re.error:
                continue

        try:
            parsed = urlparse(text)
        except Exception:
            return False

        path = parsed.path or ""
        if not path:
            return False

        segments = [
            unquote(segment).strip()
            for segment in path.split("/")
            if unquote(segment).strip()
        ]
        for segment in segments:
            lowered = segment.lower()
            if lowered in cls._NEW_CHAT_EMPTY_ROUTE_SEGMENTS:
                continue
            if cls._NEW_CHAT_SESSION_ID_RE.match(segment):
                return True
        return False

    def _wait_for_new_chat_url_transition_if_needed(
        self,
        *,
        fill_after_new_chat: bool,
        current_url: str = "",
    ) -> str:
        if (
            not fill_after_new_chat
            or not self._should_wait_for_new_chat_url_transition()
        ):
            return current_url

        snapshot = getattr(self, "_last_new_chat_clicked_snapshot", None)
        if not isinstance(snapshot, dict):
            snapshot = {}

        previous_url = str(snapshot.get("url") or getattr(self, "_last_new_chat_clicked_url", "") or "")
        if not previous_url:
            return current_url

        expected_session_id = str(snapshot.get("session_id") or "")
        current_session_id = str(getattr(self.session, "id", "") or "")
        if expected_session_id and current_session_id and expected_session_id != current_session_id:
            logger.warning(
                "[TRANSITION_SKIP] 新建对话 URL 快照不属于当前标签页会话，跳过等待: "
                f"snapshot_session={expected_session_id}, current_session={current_session_id}, "
                f"before={self._compact_log_value(previous_url, 140)}, "
                f"current={self._compact_log_value(current_url, 140)}"
            )
            return current_url

        expected_tab_id = str(snapshot.get("tab_id") or "")
        current_tab_id = str(getattr(self.tab, "tab_id", "") or "")
        if expected_tab_id and current_tab_id and expected_tab_id != current_tab_id:
            logger.warning(
                "[TRANSITION_SKIP] 新建对话 URL 快照不属于当前底层标签页，跳过等待: "
                f"snapshot_tab={expected_tab_id}, current_tab={current_tab_id}, "
                f"before={self._compact_log_value(previous_url, 140)}, "
                f"current={self._compact_log_value(current_url, 140)}"
            )
            return current_url

        expected_tab_ref_id = snapshot.get("tab_ref_id")
        if expected_tab_ref_id and expected_tab_ref_id != id(self.tab):
            logger.warning(
                "[TRANSITION_SKIP] 新建对话 URL 快照不属于当前 tab 对象，跳过等待: "
                f"before={self._compact_log_value(previous_url, 140)}, "
                f"current={self._compact_log_value(current_url, 140)}"
            )
            return current_url

        if not self._is_likely_chat_session_url(
            previous_url,
            self._get_new_chat_url_transition_patterns(),
        ):
            logger.debug(
                "[TRANSITION_SKIP] 新建对话前 URL 不像具体旧会话，跳过 URL 切换等待: "
                f"before={self._compact_log_value(previous_url, 140)}"
            )
            return current_url

        timeout = 5.0
        interval = 0.1
        started_at = time.perf_counter()
        deadline = time.time() + timeout
        latest_url = str(current_url or "")

        while time.time() < deadline:
            if self._check_cancelled():
                return latest_url

            try:
                latest_url = str(self.tab.url or "")
            except Exception as exc:
                logger.warning(
                    "[TRANSITION_ERROR] 等待新建对话 URL 切换时读取当前 URL 失败，触发工作流重试: "
                    f"before={self._compact_log_value(previous_url, 140)}, error={exc}"
                )
                raise WorkflowError("new_chat_transition_url_unavailable") from exc

            if latest_url and latest_url != previous_url:
                if self._is_likely_chat_session_url(
                    latest_url,
                    self._get_new_chat_url_transition_patterns(),
                ):
                    logger.info(
                        "[TRANSITION_OK] 新建对话后 URL 成功切换: "
                        f"before={self._compact_log_value(previous_url, 140)}, "
                        f"current={self._compact_log_value(latest_url, 140)}, "
                        f"elapsed={time.perf_counter() - started_at:.2f}s"
                    )
                else:
                    logger.info(
                        "[TRANSITION_EMPTY] 新建对话后已离开旧会话 URL，当前是空白/入口路由: "
                        f"before={self._compact_log_value(previous_url, 140)}, "
                        f"current={self._compact_log_value(latest_url, 140)}, "
                        f"elapsed={time.perf_counter() - started_at:.2f}s"
                    )
                return latest_url

            time.sleep(interval)

        logger.warning(
            "[TRANSITION_TIMEOUT] 新建对话后 URL 未在限定时间内切换，触发工作流重试: "
            f"before={self._compact_log_value(previous_url, 140)}, "
            f"current={self._compact_log_value(latest_url or current_url, 140)}, "
            f"timeout={timeout:.1f}s"
        )
        raise WorkflowError("new_chat_transition_timeout")
    
    # ================= 输入框填充 =================
    
    def _execute_fill(self, selector: str, text: str, target_key: str, optional: bool):
        """填充输入框（v5.7 隐身增强版）"""
        if self._check_cancelled():
            return

        with self._page_interaction_slot("FILL_INPUT", target_key) as acquired:
            if not acquired or self._check_cancelled():
                return

            fill_after_new_chat = bool(
                (target_key or "") == "input_box" and self._input_stability_wait_pending
            )

            current_url = ""
            try:
                current_url = str(self.tab.url or "")
            except Exception:
                pass

            if target_key == "input_box":
                logger.debug(f"[FILL_INPUT_START] 开始填充输入框, 当前 URL: {current_url}")
                if fill_after_new_chat:
                    last_url = getattr(self, "_last_new_chat_clicked_url", "")
                    if last_url and current_url == last_url:
                        logger.warning(
                            f"[WARNING] [FILL_INPUT] 新建对话后填充输入框，但 URL 未切换! "
                            f"当前 URL: {current_url}, 新建对话点击前 URL: {last_url}"
                        )
                current_url = self._wait_for_new_chat_url_transition_if_needed(
                    fill_after_new_chat=fill_after_new_chat,
                    current_url=current_url,
                )

            ele = self.finder.find_with_fallback(selector, target_key)
            if not ele:
                if not optional:
                    raise ElementNotFoundError("找不到输入框")
                return

            ele = self._wait_for_element_interactable(ele, selector, target_key)
            stabilized_ele = self._wait_for_fill_target_stability(selector, target_key)
            if stabilized_ele is not None:
                ele = stabilized_ele

            self._last_input_element = ele
            self._last_input_target_key = target_key or ""
            self._text_handler.set_active_input_context(selector=selector, target_key=target_key)

            if self.stealth_mode:
                if self._should_use_stealth_dom_click(target_key):
                    if not self._stealth_dom_click_element(ele, target_key=target_key, selector=selector):
                        raise WorkflowError("stealth_dom_click_failed")
                else:
                    self._stealth_click_element(ele, target_key=target_key, selector=selector)
                time.sleep(random.uniform(0.04, 0.10))
                active_input = self._resolve_active_text_input()
                if active_input is not None:
                    ele = active_input
                else:
                    refreshed_input = self._refresh_target_element(selector, target_key, timeout=0.25)
                    if refreshed_input is not None:
                        ele = refreshed_input
                self._last_input_element = ele
                self._text_handler.fill_via_clipboard_no_click(ele, text)
            else:
                self._text_handler.fill_via_js(ele, text)

            if hasattr(self, '_context') and self._context:
                images = self._context.get('images', [])
                if images:
                    if not self._image_handler.paste_images(images):
                        raise WorkflowError("image_paste_unconfirmed")

            self._last_input_element = self._resolve_active_text_input() or ele
            self._note_fill_completion(text, after_new_chat=fill_after_new_chat)
        
        # ===== 隐身模式：粘贴后仅保留极短缓冲，避免节奏被故意拖慢 =====
        if self.stealth_mode and len(text) > 0:
            base_delay = random.uniform(0.10, 0.22)
            extra_delay = min(0.22, (len(text) / 12000.0) * random.uniform(0.04, 0.08))
            total_review = min(base_delay + extra_delay, 0.45)

            self._idle_wait(total_review)


__all__ = ["WorkflowExecutorActionMixin"]
