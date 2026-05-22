"""
app/core/workflow/executor_actions.py - ?????? stealth mixin

???
- ???????????????
- stealth ??/??/??????
- ????????????
"""

import math
import random
import time
from typing import Any, Optional

from app.core.config import BrowserConstants, ElementNotFoundError, WorkflowError, logger
from app.utils.human_mouse import cdp_precise_click, human_scroll_path, idle_drift, smooth_move_mouse


class WorkflowExecutorActionMixin:
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

    def _stealth_dom_click_element(self, ele, target_key: str = "", selector: str = "") -> bool:
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
                "[STEALTH_CLICK] 后台安全 DOM 点击异常: "
                f"target={target_label}, selector={selector_label}, error={self._compact_log_value(e, 180)}"
            )
            return False

        ok = bool(result.get("ok")) if isinstance(result, dict) else bool(result)
        elapsed = time.perf_counter() - started_at
        if ok:
            self._mouse_pos = None
            logger.debug(
                "[STEALTH_CLICK] 后台安全 DOM 点击完成: "
                f"target={target_label}, total={elapsed:.2f}s, "
                f"active={bool((result or {}).get('active')) if isinstance(result, dict) else '-'}, "
                f"strategy={self._get_stealth_click_strategy()}"
            )
            return True

        logger.warning(
            "[STEALTH_CLICK] 后台安全 DOM 点击失败: "
            f"target={target_label}, selector={selector_label}, result={self._compact_log_value(result, 180)}"
        )
        return False

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

                    if self.stealth_mode:
                        if self._should_use_stealth_dom_click(target_key):
                            if not self._stealth_dom_click_element(ele, target_key=target_key, selector=selector):
                                raise WorkflowError("stealth_dom_click_failed")
                        else:
                            self._stealth_click_element(ele, target_key=target_key, selector=selector)
                    else:
                        if self._check_cancelled():
                            return
                        ele.click()

                self._smart_delay(
                    BrowserConstants.ACTION_DELAY_MIN,
                    BrowserConstants.ACTION_DELAY_MAX
                )
                if target_key in {"new_chat_btn", "new_chat", "new_conversation"}:
                    self._input_stability_wait_pending = True
                return

            except Exception as click_err:
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
                self._human_cdp_click_at(click_x, click_y)
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

    def _human_cdp_click_at(self, x: int, y: int):
        """
        使用 human_mouse 轨迹移动，并以 CDP 精确点击结束。

        链路固定为：
        页面内某处起点 -> smooth_move_mouse -> 短暂停顿/微漂移 -> cdp_precise_click
        """
        if self._check_cancelled():
            return

        self._flash_click_marker(x, y)
        logger.debug(f"[COORD_CLICK] viewport click at ({x}, {y})")

        start_pos = self._ensure_mouse_origin()

        self._mouse_pos = smooth_move_mouse(
            tab=self.tab,
            from_pos=start_pos,
            to_pos=(x, y),
            check_cancelled=self._check_cancelled
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

        success = cdp_precise_click(
            tab=self.tab,
            x=x,
            y=y,
            check_cancelled=self._check_cancelled
        )
        if not success:
            logger.warning(f"[CDP_CLICK] 首次坐标点击失败，重试一次: ({x}, {y})")
            time.sleep(random.uniform(0.03, 0.08))
            success = cdp_precise_click(
                tab=self.tab,
                x=x,
                y=y,
                check_cancelled=self._check_cancelled
            )

        if not success:
            raise WorkflowError("coord_click_failed")

        self._mouse_pos = (x, y)

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
        """读取输入框当前长度"""
        try:
            candidates = []

            if target_key and target_key == getattr(self, "_last_input_target_key", ""):
                last_ele = getattr(self, "_last_input_element", None)
                if last_ele:
                    candidates.append(last_ele)

            selector = ""
            if isinstance(self._selectors, dict):
                selector = str(self._selectors.get(target_key, "") or "").strip()

            if selector or target_key:
                try:
                    ele = self.finder.find_with_fallback(selector, target_key, timeout=0.2)
                except Exception:
                    ele = None
                if ele:
                    candidates.append(ele)

            try:
                active_ele = self.tab.run_js("return document.activeElement")
            except Exception:
                active_ele = None
            if active_ele:
                candidates.append(active_ele)

            for ele in candidates:
                try:
                    n = self.tab.run_js("""
                        try {
                            const el = arguments[0];
                            const tag = (el.tagName || '').toLowerCase();
                            if (tag === 'textarea' || tag === 'input') return (el.value || '').length;
                            if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') return (el.innerText || '').length;
                            return (el.textContent || '').length;
                        } catch(e){ return 0; }
                    """, ele)
                except Exception:
                    continue
                if n is not None:
                    return int(n)

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
