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
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.config import BrowserConstants, logger

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
            if not (hasattr(ele, 'states') and ele.states.is_displayed):
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
                ele = self.tab.ele(selector, timeout=timeout)
            else:
                ele = self.tab.ele(f'css:{selector}', timeout=timeout)
            
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
            if str(ele.attr("disabled") or "").strip():
                return False
            if str(ele.attr("aria-disabled") or "").strip().lower() == "true":
                return False
        except Exception:
            pass

        return True

    @staticmethod
    def _split_css_selector_groups(selector: str) -> List[str]:
        groups: List[str] = []
        current: List[str] = []
        quote = ""
        bracket_depth = 0
        paren_depth = 0
        escape = False

        for ch in selector:
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
        if not selector or selector.startswith(('tag:', '@', 'xpath:', 'css:')) or '@@' in selector:
            return None

        groups = self._split_css_selector_groups(selector)
        if len(groups) <= 1:
            return None

        per_group_timeout = max(0.02, min(float(timeout or 0), 0.25))
        for group in groups:
            started_at = time.perf_counter()
            ele = self._find_with_syntax(group, per_group_timeout)
            self._log_find_timing(
                target_key="input_box" if "textarea" in group or "contenteditable" in group else "",
                stage="primary_group",
                selector=group,
                elapsed=time.perf_counter() - started_at,
                found=bool(ele and self._is_visible_enabled(ele)),
            )
            if ele and self._is_visible_enabled(ele):
                return ele
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
        for prop in ("text", "html"):
            try:
                value = getattr(ele, prop, "")
            except Exception:
                value = ""
            if value:
                parts.append(str(value))
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

    @classmethod
    def _looks_like_send_button(cls, ele: Any) -> bool:
        signature = cls._element_text_signature(ele)
        if not signature:
            return False
        return any(
            token in signature
            for token in (
                "send message",
                "send",
                "submit",
                "发送",
                "提交",
            )
        )

    def _find_send_button_safely(self, selector: str, timeout: float) -> Optional[Any]:
        self._last_send_btn_blocked_by_stop = False
        candidates: List[Any] = []

        groups = self._split_css_selector_groups(selector) if selector else []
        if not groups and selector:
            groups = [selector]

        per_group_timeout = max(0.02, min(float(timeout or 0), 0.25))
        for group in groups:
            ele = self._find_with_syntax(group, per_group_timeout)
            if not ele or not self._is_visible_enabled(ele):
                continue
            candidates.append(ele)
            if not self._looks_like_stop_button(ele):
                return ele

        for fb_selector in self.FALLBACK_SELECTORS.get("send_btn", []):
            ele = self._find_with_syntax(fb_selector, BrowserConstants.FALLBACK_ELEMENT_TIMEOUT)
            if not ele or not self._is_visible_enabled(ele):
                continue
            candidates.append(ele)
            if not self._looks_like_stop_button(ele):
                return ele

        for ele in candidates:
            if not self._looks_like_stop_button(ele):
                return ele

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
        
        grouped_selector = (
            bool(selector)
            and not selector.startswith(('tag:', '@', 'xpath:', 'css:'))
            and '@@' not in selector
            and len(self._split_css_selector_groups(selector)) > 1
        )

        # 查找元素
        ele = self._find_css_groups_in_order(selector, timeout)
        if not ele and not grouped_selector:
            ele = self._find_with_syntax(selector, timeout)
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
                eles = self.tab.eles(selector, timeout=timeout)
            else:
                eles = self.tab.eles(f'css:{selector}', timeout=timeout)
            return list(eles) if eles else []
        except Exception:
            return []

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
