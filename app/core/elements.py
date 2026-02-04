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
            'css:button[type="submit"]',
            'tag:button@@type=submit',
            'css:form button[type="submit"]',
            'css:[role="button"][type="submit"]',
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
        
        # 查找元素
        ele = self._find_with_syntax(selector, timeout)
        
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
        
        # 先尝试主选择器
        if primary_selector:
            ele = self.find(primary_selector, timeout)
            if ele:
                return ele

        # 回退选择器
        fallback_list = self.FALLBACK_SELECTORS.get(target_key, [])
        if not fallback_list:
            return None

        logger.debug(f"主选择器失败，尝试回退: {target_key}")

        fallback_timeout = BrowserConstants.FALLBACK_ELEMENT_TIMEOUT
        for fb_selector in fallback_list:
            ele = self.find(fb_selector, fallback_timeout)
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