"""
app/core/elements.py - 元素查找和缓存

职责：
- 元素查找和验证
- 元素缓存管理
- Fallback 选择器逻辑
- 元素稳定性检查

依赖：
- app.core.config（BrowserConstants, logger）
"""

import time
import json
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.config import BrowserConstants, get_tutorial_selectors_url, logger
from app.core.driver import driver_for_tab

_ELEMENT_TIMING_LOG_THRESHOLD = 0.5


@dataclass
class CachedElement:
    element: Any
    selector: str
    cached_at: float
    content_hash: str

    def is_stale(self, max_age: float = None) -> bool:
        if max_age is None:
            max_age = BrowserConstants.ELEMENT_CACHE_MAX_AGE
        return time.time() - self.cached_at > max_age


class ElementFinder:
    """元素查找器，支持缓存和回退逻辑"""

    FALLBACK_SELECTORS: Dict[str, List[str]] = {
        "input_box": [
            'tag:textarea',
            'css:textarea',
            'css:textarea[name="message"]',
            'css:textarea[placeholder]',
            # 优先命中 Quill 的真正输入区
            'css:rich-textarea .ql-editor[contenteditable="true"]',
            # 最后才用"任意 contenteditable"兜底
            'tag:div@@contenteditable=true',
            'css:[contenteditable="true"]',
        ],
        "send_btn": [
            'css:button[aria-label="Send message"][type="submit"]:not(:disabled):not([aria-disabled="true"])',
            'css:form button[type="submit"]:not(:disabled):not([aria-disabled="true"])',
            'css:button[type="submit"]:not(:disabled):not([aria-disabled="true"])',
            'css:[role="button"][type="submit"]:not(:disabled):not([aria-disabled="true"])',
            'css:.enter-icon-container:not(:disabled):not([aria-disabled="true"])',
            'css:[class*="enter-icon"]:not(:disabled):not([aria-disabled="true"])',
            'css:[class*="send-icon"]:not(:disabled):not([aria-disabled="true"])',
            'css:[class*="send-btn"]:not(:disabled):not([aria-disabled="true"])',
        ],
        "result_container": [
            'css:div[class*="message"]',
            'css:div[class*="response"]',
            'css:div[class*="answer"]',
        ],
    }

    def __init__(self, tab):
        """
        初始化查找器
        :param tab: DrissionPage 的 Tab 或 Page 对象
        """
        self.tab = tab
        self._cache: Dict[str, CachedElement] = {}

    @staticmethod
    def _compact_selector(selector: str, max_len: int = 120) -> str:
        text = str(selector or "").replace("\r", "\\r").replace("\n", "\\n").strip()
        if len(text) > max_len:
            return f"{text[:max(0, max_len - 3)]}..."
        return text or "-"

    def _log_find_timing(
        self,
        *,
        target_key: str,
        stage: str,
        selector: str,
        elapsed: float,
        found: bool,
    ) -> None:
        if elapsed < _ELEMENT_TIMING_LOG_THRESHOLD:
            return
        compact_selector = self._compact_selector(selector, 100)
        logger.debug_throttled(
            f"element.find_timing.{target_key or 'generic'}.{stage}.{compact_selector}",
            "[ELEMENT_TIMING] "
            f"target={target_key or '-'}, stage={stage}, elapsed={elapsed:.2f}s, "
            f"found={bool(found)}, selector={compact_selector}",
            interval_sec=5.0,
        )

    def _compute_element_hash(self, ele) -> str:
        try:
            identity_parts = []
            stable_attrs = ['id', 'data-testid', 'data-message-id', 'data-turn-id']
            for attr in stable_attrs:
                try:
                    val = ele.attr(attr)
                    if val:
                        identity_parts.append(f"{attr}={val}")
                except Exception:
                    pass

            try:
                tag = ele.tag if hasattr(ele, 'tag') else 'unknown'
                identity_parts.append(f"tag={tag}")
            except Exception:
                pass

            try:
                cls = (ele.attr('class') or '').split()[:2]
                if cls:
                    identity_parts.append("cls=" + ".".join(cls))
            except Exception:
                pass

            if not identity_parts:
                return ""

            identity_str = "|".join(identity_parts)
            return hashlib.md5(identity_str.encode()).hexdigest()[:8]
        except Exception:
            return ""

    def _validate_cached_element(self, cached: CachedElement) -> bool:
        if cached.is_stale():
            return False

        ele = cached.element
        try:
            if not self._is_visible_enabled(ele):
                return False

            current_hash = self._compute_element_hash(ele)
            if cached.content_hash and current_hash != cached.content_hash:
                return False

            return True
        except Exception:
            return False

    def _find_with_syntax(self, selector: str, timeout: float) -> Optional[Any]:
        """内部方法：使用 DrissionPage 语法查找元素"""
        try:
            if selector.startswith(('tag:', '@', 'xpath:', 'css:')) or '@@' in selector:
                ele = driver_for_tab(self.tab).find(selector, timeout=timeout)
            else:
                ele = driver_for_tab(self.tab).find(f'css:{selector}', timeout=timeout)
            
            # 更可靠的检查
            if ele and hasattr(ele, 'tag') and ele.tag:
                return ele
            return None
        except Exception:
            return None

    @staticmethod
    def _is_visible_enabled(ele: Any) -> bool:
        try:
            states = getattr(ele, "states", None)
            if states is not None:
                if hasattr(states, "is_displayed") and not states.is_displayed:
                    return False
                if hasattr(states, "is_enabled") and not states.is_enabled:
                    return False
        except Exception:
            return False

        try:
            disabled_val = ele.attr("disabled")
            if disabled_val is not None and str(disabled_val).strip().lower() != "false":
                return False
            aria_disabled = ele.attr("aria-disabled")
            if aria_disabled is not None and str(aria_disabled).strip().lower() == "true":
                return False
        except Exception:
            pass

        return True

    @staticmethod
    def _split_css_selector_groups(selector: str) -> List[str]:
        if not selector or not isinstance(selector, str):
            return []
        raw = selector.strip()
        if raw.startswith("css:"):
            raw = raw[4:].strip()
        if raw.startswith(('tag:', '@', 'xpath:')) or '@@' in raw:
            return [raw] if raw else []

        groups: List[str] = []
        current: List[str] = []
        quote = ""
        bracket_depth = 0
        paren_depth = 0
        escape = False

        for ch in raw:
            current.append(ch)

            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if quote:
                if ch == quote:
                    quote = ""
                continue
            if ch in {"'", '"'}:
                quote = ch
                continue
            if ch == "[":
                bracket_depth += 1
                continue
            if ch == "]" and bracket_depth > 0:
                bracket_depth -= 1
                continue
            if ch == "(":
                paren_depth += 1
                continue
            if ch == ")" and paren_depth > 0:
                paren_depth -= 1
                continue
            if ch == "," and bracket_depth == 0 and paren_depth == 0:
                current.pop()
                group = "".join(current).strip()
                if group:
                    groups.append(group)
                current = []

        tail = "".join(current).strip()
        if tail:
            groups.append(tail)
        return groups

    def _find_css_groups_in_order(self, selector: str, timeout: float) -> Optional[Any]:
        if not selector:
            return None
        raw = str(selector).strip()
        if raw.startswith("css:"):
            raw = raw[4:].strip()
        if not raw or raw.startswith(('tag:', '@', 'xpath:')) or '@@' in raw:
            return None

        groups = self._split_css_selector_groups(raw)
        if len(groups) <= 1:
            return None

        total_timeout = max(0.02, float(timeout or 0.0))

        # 优先尝试完整复合选择器（浏览器原生支持逗号复合选择器，单次直接查询避免拆分超时）
        direct_timeout = max(0.4, min(total_timeout, 1.2))
        ele = self._find_with_syntax(raw, direct_timeout)
        if ele and self._is_visible_enabled(ele):
            return ele

        deadline = time.perf_counter() + total_timeout
        is_first_round = True

        while True:
            for group in groups:
                if time.perf_counter() >= deadline:
                    break
                rem = max(0.01, deadline - time.perf_counter())
                per_group_timeout = max(0.3, min(rem / max(len(groups), 1), 0.6))

                started_at = time.perf_counter()
                ele = self._find_with_syntax(group, per_group_timeout)
                is_valid = bool(ele and self._is_visible_enabled(ele))
                self._log_find_timing(
                    target_key="input_box" if "textarea" in group or "contenteditable" in group else "",
                    stage="primary_group",
                    selector=group,
                    elapsed=time.perf_counter() - started_at,
                    found=is_valid,
                )
                if is_valid:
                    return ele

            is_first_round = False
            if time.perf_counter() >= deadline:
                break
            time.sleep(0.05)

        return None

    @staticmethod
    def _element_text_signature(ele: Any) -> str:
        parts: List[str] = []
        for attr in ("aria-label", "title", "data-testid", "class"):
            try:
                value = ele.attr(attr)
            except Exception:
                value = ""
            if value:
                parts.append(str(value))
        try:
            value = getattr(ele, "text", "")
            if value:
                parts.append(str(value))
        except Exception:
            pass
        return " ".join(parts).lower()

    @classmethod
    def _looks_like_stop_button(cls, ele: Any) -> bool:
        signature = cls._element_text_signature(ele)
        if not signature:
            return False
        return any(
            token in signature
            for token in (
                "stop generation",
                "stop generating",
                "stop",
                "cancel",
                "abort",
                "停止",
                "中止",
                "取消",
            )
        )

    def _find_send_button_safely(self, selector: str, timeout: float) -> Optional[Any]:
        self._last_send_btn_blocked_by_stop = False
        candidates: List[Any] = []

        # 优先直接使用完整选择器进行单次查询（避免复合选择器拆分后单组超时过小导致假失败）
        if selector:
            direct_timeout = max(0.4, min(float(timeout or 0.0), 1.2))
            direct_ele = self._find_with_syntax(selector, direct_timeout)
            if direct_ele and self._is_visible_enabled(direct_ele):
                if not self._looks_like_stop_button(direct_ele):
                    return direct_ele
                candidates.append(direct_ele)

        groups = self._split_css_selector_groups(selector) if selector else []
        if not groups and selector:
            groups = [selector]

        total_timeout = max(0.02, float(timeout or 0.0))
        deadline = time.perf_counter() + total_timeout
        is_first_round = True

        while True:
            for group in groups:
                if time.perf_counter() >= deadline:
                    break
                rem = max(0.01, deadline - time.perf_counter())
                per_group_timeout = max(0.3, min(rem / max(len(groups), 1), 0.6))
                ele = self._find_with_syntax(group, per_group_timeout)
                if not ele or not self._is_visible_enabled(ele):
                    continue
                if not self._looks_like_stop_button(ele):
                    return ele
                candidates.append(ele)

            is_first_round = False
            if candidates or time.perf_counter() >= deadline:
                break
            time.sleep(0.05)

        for fb_selector in self.FALLBACK_SELECTORS.get("send_btn", []):
            ele = self._find_with_syntax(fb_selector, BrowserConstants.FALLBACK_ELEMENT_TIMEOUT)
            if not ele or not self._is_visible_enabled(ele):
                continue
            if not self._looks_like_stop_button(ele):
                return ele
            candidates.append(ele)

        if candidates:
            self._last_send_btn_blocked_by_stop = True
            logger.warning("[ELEMENT] send_btn 只匹配到停止/取消态按钮，已跳过点击以避免中断生成")
        return None

    def find(self, selector: str, timeout: float = None) -> Optional[Any]:
        """
        查找单个元素（公开方法）
        
        参数:
            selector: 选择器（支持 CSS、XPath、DrissionPage 语法）
            timeout: 超时时间（秒），默认使用配置值
        
        返回:
            找到的元素，或 None
        """
        if timeout is None:
            timeout = BrowserConstants.DEFAULT_ELEMENT_TIMEOUT
        
        # 检查缓存
        cache_key = selector
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if self._validate_cached_element(cached):
                return cached.element
            else:
                # 缓存失效，删除
                del self._cache[cache_key]
        
        raw_selector = selector.strip() if isinstance(selector, str) else ""
        if raw_selector.startswith("css:"):
            raw_selector = raw_selector[4:].strip()
        grouped_selector = (
            bool(raw_selector)
            and not raw_selector.startswith(('tag:', '@', 'xpath:'))
            and '@@' not in raw_selector
            and len(self._split_css_selector_groups(raw_selector)) > 1
        )

        # 查找元素
        ele = self._find_css_groups_in_order(selector, timeout)
        if not ele and not grouped_selector:
            ele = self._find_with_syntax(selector, timeout)
        elif not ele and grouped_selector:
            ele = self._find_with_syntax(selector, 0.05)
        if ele and not self._is_visible_enabled(ele):
            ele = None
        
        # 缓存有效元素
        if ele:
            content_hash = self._compute_element_hash(ele)
            self._cache[cache_key] = CachedElement(
                element=ele,
                selector=selector,
                cached_at=time.time(),
                content_hash=content_hash
            )
        
        return ele

    def find_all(self, selector: str, timeout: float = None) -> List[Any]:
        """
        查找所有匹配的元素
        
        参数:
            selector: 选择器
            timeout: 超时时间（秒）
        
        返回:
            元素列表（可能为空）
        """
        if timeout is None:
            timeout = BrowserConstants.DEFAULT_ELEMENT_TIMEOUT
        
        return self._find_all_with_syntax(selector, timeout)

    def _find_all_with_syntax(self, selector: str, timeout: float) -> List[Any]:
        """支持 DrissionPage 语法或默认 CSS 语法的批量查找"""
        try:
            if selector.startswith(('tag:', '@', 'xpath:', 'css:')) or '@@' in selector:
                eles = driver_for_tab(self.tab).find_all(selector, timeout=timeout)
            else:
                eles = driver_for_tab(self.tab).find_all(f'css:{selector}', timeout=timeout)
            return list(eles) if eles else []
        except Exception:
            return []

    # 输入框候选的打分依据：ChatGPT 的消息正文若含内联代码块，代码块的 CodeMirror
    # 编辑器也会命中 `[role="textbox"][contenteditable="true"]`，且文档顺序在前。
    _CODE_EDITOR_SELECTOR = (
        '.cm-editor, .cm-scroller, .cm-content, '
        '[class*="InlineCodePane"], pre, code'
    )
    # 注意：不要在单个元素上逐个 run_js 做判定。DrissionPage 的元素级 run_js 里
    # `this` 不是可靠的 DOM 元素（实测 matches() 恒返回 '0'、closest() 与
    # getBoundingClientRect() 直接抛错），必须一次页面级查询拿到全部判定。
    _INPUT_CANDIDATE_RANK_JS = """
    (function(){
      var sel = %s;
      var CODE = %s;
      var els = document.querySelectorAll(sel);
      var out = [];
      for (var i = 0; i < els.length; i++) {
        var e = els[i];
        var r = e.getBoundingClientRect();
        var isCode = false;
        try { isCode = !!(e.matches(CODE) || e.closest(CODE)); } catch (err) {}
        var inForm = false;
        try { inForm = !!e.closest('form'); } catch (err) {}
        var hasBox = (r.width > 0 && r.height > 0);
        var inVp = (r.top < window.innerHeight && r.bottom > 0 && r.width > 0);
        // 打分：非代码编辑器 > 有盒子 > 在视口内或属于 composer 表单
        var score = (isCode ? 0 : 4) + (hasBox ? 2 : 0) + ((inVp || inForm) ? 1 : 0);
        out.push(score);
      }
      return out.join(',');
    })()
    """

    def _rank_input_candidates(self, selector: str, count: int) -> List[int]:
        """一次页面级查询拿到每个候选的得分（顺序与 find_all 的文档顺序一致）。"""
        if not selector or count <= 0:
            return []
        try:
            raw = driver_for_tab(self.tab).run_js(
                self._INPUT_CANDIDATE_RANK_JS
                % (
                    json.dumps(selector, ensure_ascii=False),
                    json.dumps(self._CODE_EDITOR_SELECTOR, ensure_ascii=False),
                ),
                as_expr=True,
                timeout=5,
            )
        except Exception as exc:
            logger.debug(f"[ELEMENT] input_box 候选打分失败（忽略）: {exc}")
            return []
        scores: List[int] = []
        for part in str(raw or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                scores.append(int(float(part)))
            except ValueError:
                scores.append(0)
        return scores

    def _pick_best_input_candidate(self, elements: List[Any], selector: str = "") -> Optional[Any]:
        """在多个 input_box 候选里挑真正的聊天输入框。

        ChatGPT 的消息正文如果含内联代码块，代码块的 CodeMirror 也会命中
        `[role="textbox"][contenteditable="true"]`，而且文档顺序在前。直接取第一个
        就会把内容填进代码块，聊天框始终为空、发送按钮因此不存在。
        """
        if not elements:
            return None
        if len(elements) == 1:
            return elements[0]
        scores = self._rank_input_candidates(selector, len(elements))
        if len(scores) != len(elements):
            logger.debug(
                "[ELEMENT] input_box 候选打分不可用，退回文档顺序 "
                f"(total={len(elements)}, scores={len(scores)})"
            )
            return elements[0]
        best_index = 0
        best_score = scores[0]
        for index in range(1, len(scores)):
            # 同分时保留文档顺序，避免行为漂移
            if scores[index] > best_score:
                best_index, best_score = index, scores[index]
        if best_score <= 0:
            logger.debug(
                "[ELEMENT] input_box 候选全部得分为 0，退回文档顺序 "
                f"(total={len(elements)}, scores={scores})"
            )
            return elements[0]
        logger.debug(
            "[ELEMENT] input_box 择优 "
            f"(total={len(elements)}, scores={scores}, picked={best_index})"
        )
        return elements[best_index]

    def find_with_fallback(self, primary_selector: str,
                           target_key: str,
                           timeout: float = None) -> Optional[Any]:
        """
        带回退机制的元素查找
        
        参数:
            primary_selector: 主选择器
            target_key: 目标键名（用于回退选择器）
            timeout: 超时时间
        
        返回:
            找到的元素，或 None
        """
        if timeout is None:
            timeout = BrowserConstants.DEFAULT_ELEMENT_TIMEOUT

        if target_key == "input_box":
            # 输入框可能命中多个元素（消息正文里的代码块编辑器也会命中），
            # 必须择优而不是取第一个，否则会把正文填进代码块。
            candidates = []
            try:
                candidates = self.find_all(primary_selector, timeout=timeout) or []
            except Exception:
                candidates = []
            candidates = [c for c in candidates if self._is_visible_enabled(c)]
            if candidates:
                ele = self._pick_best_input_candidate(candidates, primary_selector)
            else:
                ele = None
            if ele:
                return ele
            # 都不可用才继续走回退选择器

        if target_key == "send_btn":
            ele = self._find_send_button_safely(primary_selector, timeout)
            if ele:
                cache_key = primary_selector or target_key
                self._cache[cache_key] = CachedElement(
                    element=ele,
                    selector=primary_selector or target_key,
                    cached_at=time.time(),
                    content_hash=self._compute_element_hash(ele),
                )
            return ele
        
        # 先尝试主选择器
        if primary_selector:
            started_at = time.perf_counter()
            ele = self.find(primary_selector, timeout)
            self._log_find_timing(
                target_key=target_key,
                stage="primary",
                selector=primary_selector,
                elapsed=time.perf_counter() - started_at,
                found=bool(ele),
            )
            if ele:
                return ele

        # 回退选择器
        fallback_list = self.FALLBACK_SELECTORS.get(target_key, [])
        if not fallback_list:
            return None

        logger.debug(f"主选择器失败，尝试回退: {target_key}")

        fallback_timeout = BrowserConstants.FALLBACK_ELEMENT_TIMEOUT
        for fb_selector in fallback_list:
            started_at = time.perf_counter()
            ele = self.find(fb_selector, fallback_timeout)
            self._log_find_timing(
                target_key=target_key,
                stage="fallback",
                selector=fb_selector,
                elapsed=time.perf_counter() - started_at,
                found=bool(ele),
            )
            if ele:
                logger.debug(f"回退选择器成功: {fb_selector}")
                return ele

        return None

    def clear_cache(self):
        """清空元素缓存"""
        self._cache.clear()

    def remove_from_cache(self, selector: str):
        """从缓存中移除指定选择器"""
        if selector in self._cache:
            del self._cache[selector]


__all__ = [
    'CachedElement',
    'ElementFinder',
]