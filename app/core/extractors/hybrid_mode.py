"""
app/core/extractors/hybrid_mode.py - 混合智能提取器

结合 DOM 直接提取和深度 JS 注入的优点：
- 先快速检测页面特征（是否有代码块、公式等）
- 简单内容 → 轻量提取（性能优先）
- 复杂内容 → 深度提取（准确优先）
"""

from typing import Any

from app.core.extractors.base import BaseExtractor


class HybridExtractor(BaseExtractor):
    """
    混合智能提取器
    
    策略：
    1. 快速检测元素是否包含复杂内容（代码块、LaTeX、Shadow DOM）
    2. 简单内容：直接读取 textContent（快速，低开销）
    3. 复杂内容：JS 深度遍历提取（准确，支持格式化）
    
    优势：
    - 兼顾性能与准确性
    - 自动适应不同类型的页面
    - 失败时自动降级
    """
    
    # ============ 元数据 ============
    
    @classmethod
    def get_id(cls) -> str:
        return "hybrid_mode"
    
    @classmethod
    def get_name(cls) -> str:
        return "混合智能模式"
    
    @classmethod
    def get_description(cls) -> str:
        return "自动检测内容复杂度，智能选择最优提取策略（推荐）"
    
    # ============ 检测脚本 ============
    
    # 轻量级复杂度检测 JS
    DETECT_COMPLEXITY_JS = """
    return (function() {
        var result = {
            hasCode: false,
            hasLatex: false,
            hasShadow: false,
            hasTable: false,
            textLength: 0
        };
        
        try {
            // 检测代码块
            result.hasCode = !!(
                this.querySelector('pre') || 
                this.querySelector('code') || 
                this.querySelector('ms-code-block')
            );
            
            // 检测 LaTeX 公式
            result.hasLatex = !!(
                this.querySelector('.katex') || 
                this.querySelector('ms-katex') || 
                this.querySelector('.MathJax') ||
                this.querySelector('math')
            );
            
            // 检测表格
            result.hasTable = !!(this.querySelector('table'));
            
            // 检测 Shadow DOM（需要深度遍历）
            var checkShadow = function(el, depth) {
                if (depth > 5) return false;
                if (el.shadowRoot) return true;
                var children = el.children || [];
                for (var i = 0; i < Math.min(children.length, 20); i++) {
                    if (checkShadow(children[i], depth + 1)) return true;
                }
                return false;
            };
            result.hasShadow = checkShadow(this, 0);
            
            // 获取文本长度（用于判断是否值得深度提取）
            result.textLength = (this.textContent || '').length;
            
        } catch(e) {
            result.error = e.message;
        }
        
        // 综合判断
        result.isComplex = result.hasCode || result.hasLatex || result.hasShadow || result.hasTable;
        result.needsDeep = result.isComplex && result.textLength > 50;
        
        return result;
    }).call(this);
    """
    
    # 深度提取 JS（来自 deep_mode.py，此处简化展示关键部分）
    DEEP_EXTRACT_JS = r"""
    return (function () {
      function normNewline(s){ return (s||'').replace(/\r\n/g,'\n').replace(/\r/g,'\n'); }
      function isEl(n){ return n && n.nodeType === 1; }

      function hasAncestorTag(el, tagNameLower){
        var p = el;
        while (p) {
          if (p.nodeType === 1 && (p.tagName||'').toLowerCase() === tagNameLower) return true;
          p = p.parentElement;
        }
        return false;
      }

      function rstripNewlines(s){
        s = normNewline(s);
        return s.replace(/\n+$/g, '');
      }

      function ignorableEl(el){
        if (!isEl(el)) return false;
        var tag = (el.tagName||'').toLowerCase();
        if (tag === 'button' || tag === 'svg') return true;
        if (tag === 'mat-expansion-panel-header') return true;

        var cls = (el.className && typeof el.className === 'string') ? el.className : '';
        if (cls.indexOf('actions-container') >= 0) return true;
        if (cls.indexOf('turn-footer') >= 0) return true;
        if (cls.indexOf('material-symbols') >= 0) return true;
        if (cls.indexOf('material-icons') >= 0) return true;
        if (cls.indexOf('mat-icon') >= 0) return true;

        var aria = (el.getAttribute && (el.getAttribute('aria-label')||'')) || '';
        if (/download|copy|expand|collapse|edit/i.test(aria)) return true;

        return false;
      }

      function extractKatex(el){
        try {
          var ann = el.querySelector && el.querySelector('annotation[encoding="application/x-tex"]');
          if (ann && ann.textContent) return ann.textContent;
        } catch(e) {}
        return '';
      }

      function walk(node, out){
        if (!node) return;
        if (isEl(node) && node.shadowRoot) walk(node.shadowRoot, out);

        if (node.nodeType === 11) {
          var kidsF = node.childNodes ? Array.prototype.slice.call(node.childNodes) : [];
          for (var iF = 0; iF < kidsF.length; iF++) walk(kidsF[iF], out);
          return;
        }

        if (node.nodeType === 3) {
          out.push(node.nodeValue || '');
          return;
        }

        if (!isEl(node)) return;
        var el = node;
        if (ignorableEl(el)) return;

        var tag = (el.tagName||'').toLowerCase();
        var cls = (el.className && typeof el.className === 'string') ? el.className : '';

        // KaTeX 处理
        if (tag === 'ms-katex' || cls.indexOf('katex') >= 0) {
          var tex = extractKatex(el);
          if (tex) {
            var inline = (cls.indexOf('inline') >= 0);
            out.push(inline ? (' $' + tex + '$ ') : ('\n$$\n' + tex + '\n$$\n'));
          }
          return;
        }

        // 代码块处理
        if ((tag === 'pre' || tag === 'code') && !hasAncestorTag(el, 'ms-katex')) {
          var tcode = normNewline(el.textContent || '');
          if (tcode && tcode.replace(/\s+/g,'').length > 0) {
            out.push('\n```\n' + rstripNewlines(tcode) + '\n```\n');
          }
          return;
        }

        // 递归子节点
        var kids = el.childNodes ? Array.prototype.slice.call(el.childNodes) : [];
        if (kids.length) {
          var isBlock = 'p div section li ul ol table tr h1 h2 h3 h4 h5 h6 br'.indexOf(tag) >= 0;
          if (isBlock) out.push('\n');
          for (var k = 0; k < kids.length; k++) walk(kids[k], out);
          if (isBlock) out.push('\n');
        }
      }

      try {
        var out = [];
        walk(this, out);
        var s = normNewline(out.join(''));
        s = s.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n');
        return s.trim();
      } catch (e) {
        return normNewline(this.textContent || '').trim();
      }
    }).call(this);
    """
    
    # 内容子节点选择器
    CONTENT_SELECTORS = [
        'css:.markdown',
        'css:.prose',
        'css:.response-content-markdown',
        'css:.ds-markdown',
        'css:.message-content',
        'css:[class*="markdown"]',
        'css:[class*="content"]',
    ]
    
    # ============ 核心方法 ============
    
    def extract_text(self, element) -> str:
        """
        智能提取文本
        
        流程：
        1. 定位内容节点
        2. 检测复杂度
        3. 选择提取策略
        4. 失败时自动降级
        """
        if not element:
            return ""
        
        # 1. 定位内容子节点
        target = self.find_content_node(element)
        
        # 2. 检测复杂度
        complexity = self._detect_complexity(target)
        
        # 3. 根据复杂度选择策略
        if complexity.get('needsDeep', False):
            # 复杂内容：使用深度提取
            text = self._deep_extract(target)
            if text:
                return text
        
        # 简单内容或深度提取失败：使用轻量提取
        return self._simple_extract(target)
    
    def get_anchor(self, element) -> str:
        """
        获取元素锚点
        
        复用 deep_mode 的稳定锚点策略
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
            
            # 2. 回退：tag + class + DOM 位置
            tag = element.tag if hasattr(element, 'tag') else 'unknown'
            
            cls = ""
            try:
                cls = element.attr('class') or ""
            except Exception:
                pass
            
            classes = cls.split()[:3]
            class_part = f"|cls={'.'.join(classes)}" if classes else ""
            
            # 3. DOM 位置
            index_part = ""
            try:
                index = element.run_js("""
                    const parent = this.parentElement;
                    if (!parent) return -1;
                    return Array.from(parent.children).indexOf(this);
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
        
        尝试找到实际包含内容的子元素，避免读取杂项
        """
        if not element:
            return element
        
        for selector in self.CONTENT_SELECTORS:
            try:
                child = element.ele(selector, timeout=0.05)
                if child:
                    # 验证有实际内容
                    text = child.run_js("return (this.textContent || '').trim()")
                    if text and len(str(text)) > 10:
                        return child
            except Exception:
                pass
        
        return element
    
    # ============ 私有方法 ============
    
    def _detect_complexity(self, element) -> dict:
        """
        检测元素复杂度
        
        Returns:
            {
                'hasCode': bool,
                'hasLatex': bool, 
                'hasShadow': bool,
                'hasTable': bool,
                'isComplex': bool,
                'needsDeep': bool
            }
        """
        try:
            result = element.run_js(self.DETECT_COMPLEXITY_JS)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        
        # 检测失败时默认简单模式
        return {'isComplex': False, 'needsDeep': False}
    
    def _simple_extract(self, element) -> str:
        """
        简单提取（高性能）
        
        直接读取 textContent，不做复杂处理
        """
        try:
            # 优先使用 .text 属性
            if hasattr(element, 'text') and element.text:
                return self._normalize(element.text)
            
            # 回退：JS 读取
            text = element.run_js("return this.textContent || this.innerText || ''")
            return self._normalize(str(text)) if text else ""
        
        except Exception:
            return ""
    
    def _deep_extract(self, element) -> str:
        """
        深度提取（高准确度）
        
        使用 JS 注入遍历 DOM 树
        """
        try:
            text = element.run_js(self.DEEP_EXTRACT_JS)
            if text and str(text).strip():
                return self._normalize(str(text))
        except Exception:
            pass
        
        return ""
    
    def _normalize(self, text: str) -> str:
        """标准化文本"""
        if not text:
            return ""
        
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        return text.strip()


__all__ = ['HybridExtractor']