"""
app/core/extractors/deep_mode.py - 深度内容提取器 (v2.2)

v2.2 修复：
- find_content_node 正确识别容器元素，不再误匹配段落
- 支持 DeepSeek ds-markdown 结构
"""

from typing import Any, Optional, Dict, List

from app.core.extractors.base import BaseExtractor
from app.core.extractors.image_extractor import image_extractor


class DeepBrowserExtractor(BaseExtractor):
    """深度浏览器内容提取器"""
    
    @classmethod
    def get_id(cls) -> str:
        return "deep_mode_v1"
    
    @classmethod
    def get_name(cls) -> str:
        return "深度模式 (JS注入)"
    
    @classmethod
    def get_description(cls) -> str:
        return "通过 JavaScript 注入深度提取内容，支持 LaTeX、代码块、Shadow DOM"
    
    DEEP_EXTRACT_JS = r"""
    return (function () {
      function normNewline(s){ return (s||'').replace(/\r\n/g,'\n').replace(/\r/g,'\n'); }
      function isEl(n){ return n && n.nodeType === 1; }
      function rstripNewlines(s){ return normNewline(s).replace(/\n+$/g, ''); }

      function hasAncestorTag(el, tagNameLower){
        var p = el;
        while (p) {
          if (p.nodeType === 1 && (p.tagName||'').toLowerCase() === tagNameLower) return true;
          p = p.parentElement;
        }
        return false;
      }
      
      function hasAncestorClass(el, classPattern){
        var p = el;
        while (p) {
          if (p.nodeType === 1) {
            var cls = (p.className && typeof p.className === 'string') ? p.className : '';
            if (cls.indexOf(classPattern) >= 0) return true;
          }
          p = p.parentElement;
        }
        return false;
      }

      function ignorableEl(el){
        if (!isEl(el)) return false;
        var tag = (el.tagName||'').toLowerCase();

        if (tag === 'button' || tag === 'svg') return true;
        if (tag === 'mat-expansion-panel-header') return true;

        var cls = (el.className && typeof el.className === 'string') ? el.className : '';

        // DeepSeek: 代码块横幅区域
        if (cls.indexOf('md-code-block-banner-wrap') >= 0) return true;
        if (cls.indexOf('code-info-button-text') >= 0) return true;
        
        // GPT: 代码块工具栏
        if (cls.indexOf('sticky') >= 0 && el.querySelector && el.querySelector('button')) return true;
        if (cls.indexOf('bg-token-sidebar-surface-primary') >= 0 && el.querySelector && el.querySelector('button')) return true;
        
        // 通用 UI 元素
        if (cls.indexOf('actions-container') >= 0) return true;
        if (cls.indexOf('projected-actions-wrapper') >= 0) return true;
        if (cls.indexOf('turn-footer') >= 0) return true;
        if (cls.indexOf('mat-expansion-indicator') >= 0) return true;
        if (cls.indexOf('material-symbols') >= 0) return true;
        if (cls.indexOf('material-icons') >= 0) return true;
        if (cls.indexOf('mat-icon') >= 0) return true;
        
        // Gemini: 代码块按钮区
        if (cls.indexOf('buttons') >= 0 && el.closest && el.closest('code-block')) return true;

        var aria = (el.getAttribute && (el.getAttribute('aria-label')||'')) || '';
        if (/download|copy|expand|collapse|edit|rerun|open options|good response|bad response|复制/i.test(aria)) return true;

        return false;
      }

      // ========== LaTeX 提取 ==========
      
      function extractKatexAnnotation(el){
        try {
          var ann = el.querySelector && el.querySelector('annotation[encoding="application/x-tex"]');
          if (ann && ann.textContent) return ann.textContent;
        } catch(e) {}
        return '';
      }
      
      function extractDataMath(el){
        try {
          var dm = el.getAttribute && el.getAttribute('data-math');
          if (dm) return dm;
        } catch(e) {}
        return '';
      }

      // ========== 代码块提取 ==========
      
      function extractDeepSeekCodeBlock(el){
        var lang = '';
        var code = '';
        try {
          var langSpan = el.querySelector('.d813de27');
          if (langSpan) lang = (langSpan.textContent || '').trim();
          var pre = el.querySelector('pre');
          if (pre) code = normNewline(pre.textContent || '');
        } catch(e) {}
        return { lang: lang, code: code };
      }
      
      function extractGeminiCodeBlock(el){
        var lang = '';
        var code = '';
        try {
          var langEl = el.querySelector('.code-block-decoration span');
          if (langEl) lang = (langEl.textContent || '').trim();
          var codeEl = el.querySelector('code[data-test-id="code-content"]');
          if (codeEl) code = normNewline(codeEl.textContent || '');
        } catch(e) {}
        return { lang: lang, code: code };
      }
      
      function extractGPTCodeBlock(el){
        var lang = '';
        var code = '';
        try {
          var codeEl = el.querySelector('code[class*="language-"]');
          if (codeEl) {
            var clsStr = codeEl.className || '';
            var match = clsStr.match(/language-(\S+)/);
            if (match) lang = match[1];
            code = normNewline(codeEl.textContent || '');
          } else {
            codeEl = el.querySelector('code');
            if (codeEl) code = normNewline(codeEl.textContent || '');
          }
          if (!lang) {
            var headerDiv = el.previousElementSibling;
            if (headerDiv && headerDiv.textContent) {
              var t = (headerDiv.textContent || '').trim();
              if (t && t.length < 30 && !/复制|copy/i.test(t)) lang = t;
            }
          }
        } catch(e) {}
        return { lang: lang, code: code };
      }
      
      function extractMsCodeBlock(el){
        var best = '';
        try {
          var nodes = el.querySelectorAll ? el.querySelectorAll('pre code') : [];
          for (var i = 0; i < nodes.length; i++) {
            var c = nodes[i];
            if (!c) continue;
            if (hasAncestorTag(c, 'ms-katex')) continue;
            var ccls = (c.className && typeof c.className === 'string') ? c.className : '';
            if (ccls.indexOf('rendered') >= 0) continue;
            try {
              if (c.querySelector && c.querySelector('.katex')) continue;
            } catch(e2){}
            var t = normNewline(c.textContent || '');
            if (t && t.replace(/\s+/g,'').length > best.replace(/\s+/g,'').length) best = t;
          }
        } catch(e) {}
        return best;
      }

      // ========== 主遍历逻辑 ==========
      
      function walk(node, out){
        if (!node) return;

        if (isEl(node) && node.shadowRoot) {
          walk(node.shadowRoot, out);
        }

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

        // ===== DeepSeek 代码块 =====
        if (cls.indexOf('md-code-block') >= 0 && tag === 'div') {
          var ds = extractDeepSeekCodeBlock(el);
          if (ds.code && ds.code.replace(/\s+/g,'').length > 0) {
            out.push('\n```' + (ds.lang || '') + '\n');
            out.push(rstripNewlines(ds.code));
            out.push('\n```\n');
          }
          return;
        }
        
        // ===== Gemini 代码块 =====
        if (tag === 'code-block') {
          var gm = extractGeminiCodeBlock(el);
          if (gm.code && gm.code.replace(/\s+/g,'').length > 0) {
            out.push('\n```' + (gm.lang ? gm.lang.toLowerCase() : '') + '\n');
            out.push(rstripNewlines(gm.code));
            out.push('\n```\n');
          }
          return;
        }
        
        // ===== GPT 代码块 =====
        if (tag === 'pre' && el.querySelector && el.querySelector('code[class*="language-"]')) {
          var gpt = extractGPTCodeBlock(el);
          if (gpt.code && gpt.code.replace(/\s+/g,'').length > 0) {
            out.push('\n```' + (gpt.lang || '') + '\n');
            out.push(rstripNewlines(gpt.code));
            out.push('\n```\n');
          }
          return;
        }
        
        // ===== ms-katex (LaTeX) =====
        if (tag === 'ms-katex') {
          var tex = extractKatexAnnotation(el);
          if (tex) {
            var inline = (cls.indexOf('inline') >= 0);
            out.push(inline ? (' $' + tex + '$ ') : ('\n$$\n' + tex + '\n$$\n'));
          }
          return;
        }
        
        // ===== Gemini LaTeX (span[data-math]) =====
        var dataMath = extractDataMath(el);
        if (dataMath) {
          var isBlockMath = cls.indexOf('math-block') >= 0 || cls.indexOf('math-display') >= 0;
          out.push(isBlockMath ? ('\n$$\n' + dataMath + '\n$$\n') : (' $' + dataMath + '$ '));
          return;
        }

        // ===== GPT/通用 .katex =====
        if (cls.indexOf('katex') >= 0) {
          var tex2 = extractKatexAnnotation(el);
          if (tex2) out.push(' $' + tex2 + '$ ');
          return;
        }

        // ===== ms-code-block =====
        if (tag === 'ms-code-block') {
          var msCode = extractMsCodeBlock(el);
          if (msCode && msCode.replace(/\s+/g,'').length > 0) {
            out.push('\n```\n');
            out.push(rstripNewlines(msCode));
            out.push('\n```\n');
          }
          return;
        }

        // ===== 通用 pre/code 回退 =====
        if (tag === 'pre' && !hasAncestorTag(el, 'ms-katex') && !hasAncestorClass(el, 'md-code-block')) {
          var tcode = normNewline(el.textContent || '');
          if (tcode && tcode.replace(/\s+/g,'').length > 0) {
            out.push('\n```\n');
            out.push(rstripNewlines(tcode));
            out.push('\n```\n');
          }
          return;
        }

        // ===== Markdown 标题 h1-h6 =====
        if (/^h[1-6]$/.test(tag)) {
          var level = parseInt(tag.charAt(1), 10);
          var hashes = '';
          for (var h = 0; h < level; h++) hashes += '#';
          out.push('\n' + hashes + ' ');
          var hKids = el.childNodes ? Array.prototype.slice.call(el.childNodes) : [];
          for (var hk = 0; hk < hKids.length; hk++) walk(hKids[hk], out);
          out.push('\n');
          return;
        }
        
        // ===== Markdown 加粗 strong/b =====
        if (tag === 'strong' || tag === 'b') {
          out.push('**');
          var bKids = el.childNodes ? Array.prototype.slice.call(el.childNodes) : [];
          for (var bk = 0; bk < bKids.length; bk++) walk(bKids[bk], out);
          out.push('**');
          return;
        }
        
        // ===== Markdown 斜体 em/i =====
        if (tag === 'em' || tag === 'i') {
          out.push('*');
          var iKids = el.childNodes ? Array.prototype.slice.call(el.childNodes) : [];
          for (var ik = 0; ik < iKids.length; ik++) walk(iKids[ik], out);
          out.push('*');
          return;
        }
        
        // ===== Markdown 水平线 hr =====
        if (tag === 'hr') {
          out.push('\n---\n');
          return;
        }

        // ===== 通用块级元素 =====
        var kids = el.childNodes ? Array.prototype.slice.call(el.childNodes) : [];
        if (kids.length) {
          var isBlock = ('p div section article li ul ol table tr'.indexOf(tag) >= 0);
          if (isBlock) out.push('\n');
          for (var k = 0; k < kids.length; k++) walk(kids[k], out);
          if (isBlock) out.push('\n');
        }
      }

      // ========== 入口 ==========
      try {
        var out = [];
        walk(this, out);

        var s = normNewline(out.join(''));
        s = s.replace(/[ \t]+\n/g, '\n');
        s = s.replace(/\n{3,}/g, '\n\n');
        return s.trim();
      } catch (e) {
        try {
          return normNewline(this.textContent || this.innerText || '').trim();
        } catch (e2) {
          return '';
        }
      }
    }).call(this);
    """
    
    # 注意：移除了 'css:[class*="markdown"]' 这种容易误匹配的选择器
    CONTENT_CHILD_SELECTORS = [
        'css:.markdown-body',      # GitHub style
        'css:.prose',              # Tailwind
        'css:.turn-content',       # Gemini
        'css:.response-content-markdown',
        'css:.message-content',
    ]

    def find_content_node(self, element) -> Any:
        """
        定位内容子节点
        
        v2.2 修复：正确识别容器元素，不再误匹配段落
        """
        if not element:
            return element
        
        # ===== 关键修复：检查元素本身是否已经是目标容器 =====
        try:
            ele_class = element.attr('class') or ""
            ele_tag = element.tag.lower() if hasattr(element, 'tag') else ""
            
            # 已知的内容容器类名（直接返回，不再向下查找）
            container_patterns = [
                'ds-markdown',      # DeepSeek
                'markdown-body',    # GitHub style  
                'prose',            # Tailwind
                'message-content',  # 通用
                'response-content', # 通用
            ]
            
            for pattern in container_patterns:
                if pattern in ele_class:
                    # 确保不是段落子元素
                    if 'paragraph' not in ele_class and 'html' not in ele_class:
                        return element
            
            # 如果是 div/article 且类名包含 markdown/prose，直接返回
            if ele_tag in ('div', 'article', 'section'):
                if ('markdown' in ele_class or 'prose' in ele_class) and 'paragraph' not in ele_class:
                    return element
                    
        except Exception:
            pass
        
        # ===== 向下查找（只在必要时执行）=====
        for child_selector in self.CONTENT_CHILD_SELECTORS:
            try:
                child = element.ele(child_selector, timeout=0.05)
                if child:
                    # 额外检查：不要选中段落元素
                    try:
                        child_class = child.attr('class') or ""
                        if 'paragraph' in child_class:
                            continue
                    except Exception:
                        pass
                    
                    child_text = child.run_js("return this.textContent || this.innerText || ''")
                    if child_text and len(str(child_text).strip()) > 0:
                        return child
            except Exception:
                pass
        
        return element

    def extract_text(self, element) -> str:
        """从元素中提取纯文本"""
        if not element:
            return ""
        
        target_ele = self.find_content_node(element)
        
        try:
            text = target_ele.run_js(self.DEEP_EXTRACT_JS)
            if text and str(text).strip():
                return str(text).replace('\r\n', '\n').replace('\r', '\n')
        except Exception:
            pass
        
        try:
            t = target_ele.text if hasattr(target_ele, 'text') else ""
            return (t or "").replace('\r\n', '\n').replace('\r', '\n')
        except Exception:
            return ""

    def get_anchor(self, element) -> str:
        """获取元素锚点"""
        if not element:
            return ""
        try:
            stable_attrs = ['data-message-id', 'data-turn-id', 'data-testid', 'id']
            for attr in stable_attrs:
                try:
                    val = element.attr(attr)
                    if val:
                        return f"{attr}={val}"
                except Exception:
                    pass

            tag = element.tag if hasattr(element, 'tag') else 'unknown'
            
            cls = ""
            try:
                cls = element.attr('class') or ""
            except Exception:
                pass
            
            classes = cls.split()[:3]
            class_part = f"|cls={'.'.join(classes)}" if classes else ""

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
    def extract_images(
        self,
        element,
        config: Optional[Dict] = None,
        container_selector_fallback: Optional[str] = None
    ) -> List[Dict]:
        """
        从元素中提取图片（Phase A 新增）
        
        Args:
            element: 页面元素对象
            config: 图片提取配置（ImageExtractionConfig）
            container_selector_fallback: 容器选择器回退值
        
        Returns:
            图片数据列表，每项符合 ImageData 格式
        """
        if not element:
            return []
        
        
        # 复用 find_content_node 定位内容区域
        target_ele = self.find_content_node(element)
        
        return image_extractor.extract(
            target_ele,
            config=config,
            container_selector_fallback=container_selector_fallback
        )

__all__ = ['DeepBrowserExtractor']