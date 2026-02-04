"""
app/core/extractors/dom_mode.py - DOM 直接提取器

轻量级提取器，直接读取元素的 textContent/innerText。
适用于结构简单、无复杂格式的页面。
"""

from typing import Any

from app.core.extractors.base import BaseExtractor


class DOMDirectExtractor(BaseExtractor):
    """
    DOM 直接提取器
    
    特点：
    - 直接读取 textContent/innerText
    - 无 JavaScript 注入（性能高）
    - 不处理 LaTeX/代码块
    - 适合简单页面
    """
    
    # ============ 元数据 ============
    
    @classmethod
    def get_id(cls) -> str:
        return "dom_direct"
    
    @classmethod
    def get_name(cls) -> str:
        return "DOM 直接提取"
    
    @classmethod
    def get_description(cls) -> str:
        return "直接读取 DOM textContent（轻量级，不支持复杂格式）"
    
    # ============ 提取逻辑 ============
    
    def extract_text(self, element) -> str:
        """
        直接提取元素的文本内容
        
        Args:
            element: 页面元素对象
        
        Returns:
            提取的文本
        """
        if not element:
            return ""
        
        try:
            # 优先使用 .text 属性
            if hasattr(element, 'text'):
                text = element.text
                if text:
                    return self._normalize_text(text)
            
            # 回退：使用 textContent
            text = element.run_js("return this.textContent || this.innerText || ''")
            return self._normalize_text(str(text)) if text else ""
        
        except Exception:
            return ""
    
    def get_anchor(self, element) -> str:
        """
        获取元素锚点（复用 deep_mode 的逻辑）
        
        Args:
            element: 页面元素对象
        
        Returns:
            锚点字符串
        """
        if not element:
            return ""
        
        try:
            # 1. 优先使用稳定的 ID 类属性
            stable_attrs = ['data-message-id', 'data-turn-id', 'data-testid', 'id']
            for attr in stable_attrs:
                try:
                    val = element.attr(attr)
                    if val:
                        return f"{attr}={val}"
                except Exception:
                    pass
            
            # 2. 回退：tag + class
            tag = element.tag if hasattr(element, 'tag') else 'unknown'
            
            cls = ""
            try:
                cls = element.attr('class') or ""
            except Exception:
                pass
            
            classes = cls.split()[:3]
            class_part = f"|cls={'.'.join(classes)}" if classes else ""
            
            # 3. 加入 DOM 位置
            index_part = ""
            try:
                index = element.run_js("""
                    const parent = this.parentElement;
                    if (!parent) return -1;
                    const siblings = Array.from(parent.children);
                    return siblings.indexOf(this);
                """)
                if index is not None and index >= 0:
                    index_part = f"|idx={index}"
            except Exception:
                pass
            
            return f"tag:{tag}{class_part}{index_part}"
        
        except Exception:
            return ""
    
    def find_content_node(self, element) -> Any:
        """
        定位内容子节点
        
        简化版：直接使用常见选择器
        
        Args:
            element: 父元素
        
        Returns:
            内容子节点或原元素
        """
        if not element:
            return element
        
        # 常见的内容容器选择器
        content_selectors = [
            'css:.markdown',
            'css:.prose',
            'css:[class*="content"]',
        ]
        
        for selector in content_selectors:
            try:
                child = element.ele(selector, timeout=0.05)
                if child:
                    # 检查是否有内容
                    try:
                        text = child.run_js("return this.textContent || ''")
                        if text and len(str(text).strip()) > 0:
                            return child
                    except Exception:
                        pass
            except Exception:
                pass
        
        return element
    
    # ============ 辅助方法 ============
    
    def _normalize_text(self, text: str) -> str:
        """
        标准化文本
        
        - 统一换行符
        - 去除首尾空白
        
        Args:
            text: 原始文本
        
        Returns:
            标准化后的文本
        """
        if not text:
            return ""
        
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return text.strip()


__all__ = ['DOMDirectExtractor']