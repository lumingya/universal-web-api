"""
app/core/workflow/text_input.py - 文本输入处理

职责：
- 输入框工具方法（读取、规范化、验证）
- JS 模式输入（原子写入、分块、验证修正）
- 剪贴板模式输入（隐身模式专用）
"""

import re
import os
import time
import json
import base64
import random
import mimetypes
from typing import Optional
import pyperclip
from app.core.config import logger, BrowserConstants, WorkflowError
from app.core.tab_pool import get_clipboard_lock
from app.utils.file_paste import create_temp_txt, copy_file_to_clipboard
from app.utils.human_mouse import smooth_move_mouse

# ================= 常量配置 =================

CHUNK_SIZE_THRESHOLD = 30000


# ================= 文本输入处理器 =================

class TextInputHandler:
    """文本输入处理器"""
    
    def __init__(self, tab, stealth_mode: bool, smart_delay_fn, check_cancelled_fn,
                 file_paste_config: dict = None,
                 selectors: dict = None):
        """
        Args:
            tab: 浏览器标签页
            stealth_mode: 是否隐身模式
            smart_delay_fn: 智能延迟函数
            check_cancelled_fn: 取消检查函数
            file_paste_config: 文件粘贴配置 {"enabled": bool, "threshold": int}
        """
        self.tab = tab
        self.stealth_mode = stealth_mode
        self._smart_delay = smart_delay_fn
        self._check_cancelled = check_cancelled_fn
        self._file_paste_config = file_paste_config or {}
        self._selectors = selectors or {}
    
    # ================= 工具方法 =================
    
    def normalize_for_compare(self, text: str) -> str:
        """规范化文本用于比对（处理富文本编辑器的换行差异）"""
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()
        return text
    
    def is_contenteditable(self, ele) -> bool:
        """检测元素是否为 contenteditable"""
        try:
            return bool(ele.run_js("""
                return !!(this.isContentEditable || this.getAttribute('contenteditable') === 'true')
            """))
        except Exception:
            return False
    
    def debug_read_input_sample(self, ele, head: int = 80, tail: int = 80) -> dict:
        """读取输入框内容的头尾采样（用于调试）"""
        try:
            return ele.run_js(f"""
                return (function(){{
                    try {{
                        const el = this;
                        const tag = (el.tagName || '').toLowerCase();
                        const isCE = el.isContentEditable || el.getAttribute('contenteditable') === 'true';
                        let s = '';
                        if (tag === 'textarea' || tag === 'input') s = el.value || '';
                        else if (isCE) s = el.innerText || '';
                        else s = el.textContent || '';

                        s = s.replace(/\\r\\n/g, '\\n');
                        const n = s.length;
                        return {{
                            len: n,
                            nl: (s.match(/\\n/g) || []).length,
                            head: s.slice(0, {head}),
                            tail: s.slice(Math.max(0, n - {tail}))
                        }};
                    }} catch(e) {{
                        return {{len: 0, nl: 0, head: '', tail: ''}};
                    }}
                }}).call(this);
            """)
        except Exception:
            return {"len": 0, "nl": 0, "head": "", "tail": ""}
    
    def get_input_len(self, ele) -> int:
        """读取当前输入框内容长度"""
        try:
            n = ele.run_js("""
                try {
                    const el = this;
                    if (!el) return 0;
                    const tag = (el.tagName || '').toLowerCase();
                    if (tag === 'textarea' || tag === 'input') return (el.value || '').length;
                    if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') {
                        return (el.innerText || '').length;
                    }
                    return (el.textContent || '').length;
                } catch (e) { return 0; }
            """)
            return int(n) if n is not None else 0
        except Exception:
            return 0
    
    def read_input_full_text(self, ele) -> str:
        """读取输入框完整内容"""
        try:
            s = ele.run_js("""
                try {
                    const el = this;
                    const tag = (el.tagName || '').toLowerCase();
                    if (tag === 'textarea' || tag === 'input') return (el.value || '');
                    if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') return (el.innerText || '');
                    return (el.textContent || '');
                } catch (e) { return ''; }
            """) or ""
            return str(s).replace('\r\n', '\n').replace('\r', '\n')
        except Exception:
            return ""
    
    def first_mismatch(self, a: str, b: str) -> int:
        """找到第一个不匹配的位置，完全相同返回 -1"""
        n = min(len(a), len(b))
        for i in range(n):
            if a[i] != b[i]:
                return i
        return n if len(a) != len(b) else -1
    
    def get_input_stats(self, ele) -> tuple:
        """获取输入框统计信息：(长度, 换行数)"""
        try:
            res = ele.run_js("""
                return (function(){
                    try {
                        const el = this;
                        const tag = (el.tagName || '').toLowerCase();
                        const isCE = el.isContentEditable || el.getAttribute('contenteditable') === 'true';

                        let s = '';
                        if (tag === 'textarea' || tag === 'input') {
                            s = el.value || '';
                        } else if (isCE) {
                            s = el.innerText || '';
                        } else {
                            s = el.textContent || '';
                        }

                        s = s.replace(/\\r\\n/g, '\\n');
                        const n = s.length;
                        const nl = (s.match(/\\n/g) || []).length;
                        return {len: n, nl: nl};
                    } catch(e) {
                        return {len: 0, nl: 0};
                    }
                }).call(this);
            """)
            if isinstance(res, dict):
                return int(res.get("len", 0)), int(res.get("nl", 0))
            return 0, 0
        except Exception:
            return 0, 0
    
    def clear_input_safely(self, ele):
        """安全清空输入框"""
        try:
            ele.clear()
        except Exception:
            pass

        try:
            ele.run_js("""
                (function(){
                    try {
                        const tag = (this.tagName || '').toLowerCase();
                        
                        if (tag === 'textarea' || tag === 'input') {
                            const proto = Object.getPrototypeOf(this);
                            const desc = Object.getOwnPropertyDescriptor(proto, 'value')
                                       || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')
                                       || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                            if (desc && desc.set) {
                                desc.set.call(this, '');
                            } else {
                                this.value = '';
                            }
                            try { this.dispatchEvent(new Event('input', {bubbles:true})); } catch(e) {}
                            try { this.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
                            return true;
                        }

                        if (this.isContentEditable || this.getAttribute('contenteditable') === 'true') {
                            this.innerHTML = '<p><br></p>';
                            try { this.dispatchEvent(new Event('input', {bubbles:true})); } catch(e) {}
                            try { this.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
                            return true;
                        }

                        return false;
                    } catch (e) {
                        return false;
                    }
                }).call(this);
            """)
        except Exception:
            pass
    
    def focus_to_end(self, ele):
        """把焦点放回输入框，并把光标移到末尾"""
        try:
            ele.run_js("""
                (function(){
                    try { this.focus && this.focus(); } catch(e){}
                    const tag = (this.tagName || '').toLowerCase();

                    if (tag === 'textarea' || tag === 'input') {
                        try {
                            const n = (this.value || '').length;
                            this.setSelectionRange(n, n);
                        } catch(e){}
                        return true;
                    }

                    if (this.isContentEditable || this.getAttribute('contenteditable') === 'true') {
                        try {
                            const range = document.createRange();
                            range.selectNodeContents(this);
                            range.collapse(false);
                            const sel = window.getSelection();
                            sel.removeAllRanges();
                            sel.addRange(range);
                        } catch(e){}
                        return true;
                    }

                    return false;
                }).call(this);
            """)
            return True
        except Exception:
            return False
    
    # ================= JS 模式输入 =================
    
    def set_input_atomic(self, ele, text: str, mode: str = "append") -> bool:
        """原子输入操作（仅普通模式使用）"""
        normalized_text = text.replace('\r\n', '\n')
    
        try:
            b64_text = base64.b64encode(normalized_text.encode('utf-8')).decode('utf-8')
        except Exception as e:
            logger.error(f"Base64 编码失败: {e}")
            return False

        is_append = "true" if mode == "append" else "false"

        js_code = f"""
        return (function() {{
          try {{
            const el = this;
            const b64 = "{b64_text}";
            const isAppend = {is_append};

            const bin = atob(b64);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            const newText = new TextDecoder('utf-8').decode(bytes);

            try {{ el.focus({{preventScroll: true}}); }} catch(e) {{ try{{el.focus();}}catch(e2){{}} }}

            const tag = (el.tagName || '').toLowerCase();
            const isContentEditable = el.isContentEditable || el.getAttribute('contenteditable') === 'true';

            function fireInputEvent(text) {{
              try {{
                el.dispatchEvent(new InputEvent('input', {{
                  bubbles: true,
                  cancelable: true,
                  inputType: 'insertText',
                  data: text
                }}));
              }} catch(e) {{
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
              }}
              try {{ el.dispatchEvent(new Event('change', {{ bubbles: true }})); }} catch(e) {{}}
            }}

            if (tag === 'textarea' || tag === 'input') {{
              if (isAppend) {{
                const len = (el.value || '').length;
                try {{ el.setSelectionRange(len, len); }} catch(e) {{}}
                if (typeof el.setRangeText === 'function') {{
                  el.setRangeText(newText, len, len, 'end');
                }} else {{
                  el.value = (el.value || '') + newText;
                }}
              }} else {{
                const proto = Object.getPrototypeOf(el);
                const nativeSetter =
                  Object.getOwnPropertyDescriptor(proto, 'value')?.set ||
                  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement?.prototype || {{}}, 'value')?.set ||
                  Object.getOwnPropertyDescriptor(window.HTMLInputElement?.prototype || {{}}, 'value')?.set;
                if (nativeSetter) nativeSetter.call(el, newText);
                else el.value = newText;
              }}
              fireInputEvent(newText);
              return true;
            }}

            if (isContentEditable) {{
              const sel = window.getSelection();
              if (!sel) return false;

              // ── 策略 0：尝试框架级 API（Quill / ProseMirror / Tiptap）──
              try {{
                // Quill（Gemini 的 rich-textarea 使用）
                const quillEl = el.closest('.ql-container') || el.parentElement?.closest('.ql-container');
                if (quillEl && quillEl.__quill) {{
                  const q = quillEl.__quill;
                  if (!isAppend) q.setText('\\n');
                  const idx = isAppend ? q.getLength() - 1 : 0;
                  q.insertText(idx, newText, 'user');
                  return true;
                }}
                // Quill 2.x（实例可能挂在不同位置）
                if (el.__quill) {{
                  const q = el.__quill;
                  if (!isAppend) q.setText('\\n');
                  const idx = isAppend ? q.getLength() - 1 : 0;
                  q.insertText(idx, newText, 'user');
                  return true;
                }}
              }} catch(qe) {{ /* Quill API 不可用，继续降级 */ }}

              try {{
                // ProseMirror / Tiptap（Grok 的 tiptap 编辑器）
                if (el.pmViewDesc && el.pmViewDesc.view) {{
                  const view = el.pmViewDesc.view;
                  const state = view.state;
                  let tr;
                  if (!isAppend) {{
                    tr = state.tr.replaceWith(0, state.doc.content.size, state.schema.text(newText));
                  }} else {{
                    tr = state.tr.insertText(newText, state.doc.content.size);
                  }}
                  view.dispatch(tr);
                  return true;
                }}
              }} catch(pe) {{ /* ProseMirror API 不可用，继续降级 */ }}

              // ── 策略 1：execCommand（传统 contenteditable）──
              sel.removeAllRanges();
              const range = document.createRange();
              range.selectNodeContents(el);

              if (!isAppend) {{
                el.innerHTML = '<p><br></p>';
                range.selectNodeContents(el);
                range.collapse(true);
              }} else {{
                range.collapse(false);
              }}
              sel.addRange(range);

              let success = false;
              try {{ success = document.execCommand('insertText', false, newText); }} catch(e) {{}}

              if (success) {{
                fireInputEvent(newText);
                return true;
              }}

              // ── 策略 2：直接 DOM 写入（最终降级）──
              if (!isAppend) {{
                el.innerText = newText;
              }} else {{
                el.innerText = (el.innerText || '') + newText;
              }}
              fireInputEvent(newText);
              return true;
            }}

            return false;
          }} catch (e) {{
            console.error("Atomic Input Error:", e);
            return false;
          }}
        }}).call(this);
        """    
        try:
            return bool(ele.run_js(js_code))
        except Exception as e:
            logger.error(f"原子输入执行错误: {e}")
            return False
    
    def append_chunk_via_js(self, ele, chunk: str) -> bool:
        """(备用) 简单追加模式"""
        try:
            escaped = json.dumps(chunk)
            ok = ele.run_js(f"""
            return (function() {{
                try {{
                    const chunk = {escaped};
                    const tag = (this.tagName || '').toLowerCase();
                    if (tag === 'textarea' || tag === 'input') {{
                        this.value = (this.value || '') + chunk;
                    }} else if (this.isContentEditable || this.getAttribute('contenteditable') === 'true') {{
                        this.innerText = (this.innerText || '') + chunk;
                    }} else {{
                        return false;
                    }}
                    this.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    this.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return true;
                }} catch (e) {{
                    return false;
                }}
            }}).call(this);
            """)
            return bool(ok)
        except Exception:
            return False
    
    def chunked_input(self, ele, text: str, chunk_size: int = CHUNK_SIZE_THRESHOLD) -> bool:
        """分块写入逻辑（普通模式用）"""
        total_len = len(text)
        
        # 情况1：短文本直接一次写入
        if total_len <= chunk_size:
            logger.debug(f"[CHUNKED_INPUT] 短文本模式: {total_len} 字符，直接写入")
            return self.set_input_atomic(ele, text, mode="overwrite")

        # 情况2：长文本分块写入
        logger.info(f"[CHUNKED_INPUT] 长文本模式: {total_len} 字符，分块大小 {chunk_size}")
        
        # 首块：覆盖写入
        first_chunk = text[:chunk_size]
        if not self.set_input_atomic(ele, first_chunk, mode="overwrite"):
            logger.warning("[CHUNKED_INPUT] 首块原子写入失败，尝试备用方案")
            # 降级：直接 JS 赋值
            if not self.fill_via_js_backup(ele, first_chunk):
                logger.error("[CHUNKED_INPUT] 首块写入彻底失败")
                return False
        
        logger.debug(f"[CHUNKED_INPUT] 首块完成: 0-{chunk_size}")
        time.sleep(0.1)
        
        # 后续块：追加写入
        current_pos = chunk_size
        chunk_index = 1
        
        while current_pos < total_len:
            if self._check_cancelled():
                logger.info("[CHUNKED_INPUT] 被取消")
                return False

            end_pos = min(current_pos + chunk_size, total_len)
            chunk = text[current_pos:end_pos]
            
            if not self.set_input_atomic(ele, chunk, mode="append"):
                logger.warning(f"[CHUNKED_INPUT] 第 {chunk_index} 块追加失败: {current_pos}-{end_pos}")
                if not self.append_chunk_via_js(ele, chunk):
                    logger.error(f"[CHUNKED_INPUT] 备用方案也失败")
                    return False
            
            logger.debug(f"[CHUNKED_INPUT] 第 {chunk_index} 块完成: {current_pos}-{end_pos}")
            current_pos = end_pos
            chunk_index += 1
            time.sleep(0.08)
        
        logger.info(f"[CHUNKED_INPUT] 全部完成: {chunk_index} 块，共 {total_len} 字符")
        return True
    
    def physical_activate(self, ele):
        """物理激活输入框（绕过 isTrusted 检测）"""
        try:
            ele.run_js("this.focus && this.focus()")
            
            is_ce = self.is_contenteditable(ele)
            
            if is_ce:
                self.tab.actions.key_down(' ').key_up(' ')
                time.sleep(0.03)
                self.tab.actions.key_down('Backspace').key_up('Backspace')
            else:
                ele.input(' ')
                time.sleep(0.03)
                self.tab.actions.key_down('Backspace').key_up('Backspace')
            
            time.sleep(0.1)
        except Exception as e:
            logger.debug(f"物理激活异常（可忽略）: {e}")
    
    def verify_and_fix(self, ele, original_text: str):
        """校验并修正输入内容（普通模式用）"""
        expected = original_text.replace('\r\n', '\n').replace('\r', '\n')
        expected_len = len(expected)
        expected_normalized = self.normalize_for_compare(expected)
        expected_core = re.sub(r'\s+', '', expected)
        
        is_rich_editor = self.is_contenteditable(ele)

        for attempt in range(3):
            actual = self.read_input_full_text(ele)

            # 检查1：精确匹配
            if actual == expected:
                logger.info(f"[VERIFY_OK] attempt={attempt} len={len(actual)} (exact match)")
                return

            # 检查2：规范化匹配
            actual_normalized = self.normalize_for_compare(actual)
            if actual_normalized == expected_normalized:
                diff = len(actual) - expected_len
                logger.debug(f"输入验证通过 (len={len(actual)}, diff={diff:+d})")
                return

            # 检查3：富文本宽松匹配
            if is_rich_editor:
                actual_core = re.sub(r'\s+', '', actual)
                if actual_core == expected_core:
                    diff = len(actual) - expected_len
                    logger.debug(
                        f"[VERIFY_OK] attempt={attempt} len={len(actual)} "
                        f"(rich editor core match, diff={diff:+d} chars)"
                    )
                    return

            # 校验失败，记录详细信息
            actual_len = len(actual)
            mismatch_pos = self.first_mismatch(actual_normalized, expected_normalized)
            
            window = 60
            if mismatch_pos >= 0:
                start = max(0, mismatch_pos - window)
                end = min(max(len(actual_normalized), len(expected_normalized)), mismatch_pos + window)
                actual_snippet = actual_normalized[start:end] if start < len(actual_normalized) else "(empty)"
                expected_snippet = expected_normalized[start:end] if start < len(expected_normalized) else "(empty)"
            else:
                actual_snippet = actual_normalized[-window:] if actual_normalized else "(empty)"
                expected_snippet = expected_normalized[-window:] if expected_normalized else "(empty)"
            
            logger.debug(
                f"[VERIFY_FAIL] attempt={attempt} "
                f"actual_len={actual_len} expected_len={expected_len} "
                f"mismatch_at={mismatch_pos} is_rich={is_rich_editor}\n"
                f"  ACTUAL(norm):   ...{repr(actual_snippet)}...\n"
                f"  EXPECTED(norm): ...{repr(expected_snippet)}..."
            )

            # 尝试修复
            self.clear_input_safely(ele)
            time.sleep(0.05)
            
            ok = self.set_input_atomic(ele, expected, mode="overwrite")
            if not ok:
                logger.debug(f"[VERIFY] attempt={attempt} 原子写入返回 False，尝试备用方案")
                self.fill_via_js_backup(ele, expected)
            
            time.sleep(0.15)

        # 最终检查
        final_actual = self.read_input_full_text(ele)
        final_normalized = self.normalize_for_compare(final_actual)
        
        if final_normalized == expected_normalized:
            logger.info("[VERIFY_OK] 最终检查通过 (normalized)")
            return
        
        if is_rich_editor:
            final_core = re.sub(r'\s+', '', final_actual)
            if final_core == expected_core:
                logger.info("[VERIFY_OK] 最终检查通过 (rich editor core match)")
                return

        final_len = len(final_actual)
        logger.error(
            f"[VERIFY_GIVEUP] 输入框内容仍不一致 "
            f"(actual={final_len}, expected={expected_len}, is_rich={is_rich_editor})"
        )
        raise WorkflowError("input_mismatch")
    
    def fill_via_js_backup(self, ele, text: str) -> bool:
        """使用 JavaScript 直接设置值（备用方案）"""
        try:
            escaped_text = json.dumps(text)

            ok = ele.run_js(f"""
                (function() {{
                    try {{
                        const v = {escaped_text};
                        try {{ this.focus && this.focus(); }} catch (e) {{}}

                        const tag = (this.tagName || '').toLowerCase();
                        if (tag === 'textarea' || tag === 'input') {{
                            this.value = v;
                        }} else if (this.isContentEditable || this.getAttribute('contenteditable') === 'true') {{
                            this.innerText = v;
                        }} else {{
                            return false;
                        }}

                        try {{ this.dispatchEvent(new Event('input', {{ bubbles: true }})); }} catch (e) {{}}
                        try {{ this.dispatchEvent(new Event('change', {{ bubbles: true }})); }} catch (e) {{}}

                        return true;
                    }} catch (e) {{
                        return false;
                    }}
                }}).call(this);
            """)

            if ok:
                logger.info(f"JS 备用方案完成 ({len(text)} 字符)")
                return True
            else:
                logger.debug("JS 备用方案返回 false")
                return False

        except Exception as e:
            logger.error(f"JS 备用方案失败: {e}")
            return False
    
    def fill_via_js(self, ele, text: str):
        """普通模式专用：JS 填充逻辑"""
        # 🆕 文件粘贴前置判断
        if self._should_use_file_paste(text):
            if self._fill_via_file_paste(ele, text):
                return
            logger.warning("[FILE_PASTE] 文件粘贴失败，降级到 JS 输入模式")
        
        self.clear_input_safely(ele)
        
        # 分块写入
        success = self.chunked_input(ele, text, chunk_size=CHUNK_SIZE_THRESHOLD)

        if not success:
            logger.debug("JS 分块输入遇到问题，准备进行后续修正...")

        self._smart_delay(0.2, 0.4)

        # 物理激活
        self.physical_activate(ele)

        # 校验并修正
        self.verify_and_fix(ele, text)
        
        # 调试日志
        sample = self.debug_read_input_sample(ele)
        logger.debug(
            f"[INPUT_SNAPSHOT] len={sample['len']} nl={sample['nl']} "
            f"head={repr(sample['head'][:40])}... tail=...{repr(sample['tail'][-40:])}"
        )
        # ================= 人类化按键辅助（隐身模式专用）=================
    
    def _human_key_combo(self, *keys):
        """
        人类化组合键：每个 key_down/key_up 之间加随机微延迟
        
        用法：
            self._human_key_combo('Control', 'A')   → Ctrl+A
            self._human_key_combo('Control', 'V')   → Ctrl+V
            self._human_key_combo('Delete')          → Delete
        """
        down_up_min = getattr(BrowserConstants, 'STEALTH_KEY_DOWN_UP_MIN', 0.03)
        down_up_max = getattr(BrowserConstants, 'STEALTH_KEY_DOWN_UP_MAX', 0.09)
        between_min = getattr(BrowserConstants, 'STEALTH_KEY_BETWEEN_MIN', 0.04)
        between_max = getattr(BrowserConstants, 'STEALTH_KEY_BETWEEN_MAX', 0.12)
        
        if len(keys) == 1:
            self.tab.actions.key_down(keys[0])
            time.sleep(random.uniform(down_up_min, down_up_max))
            self.tab.actions.key_up(keys[0])
            return
        
        modifier = keys[0]
        targets = keys[1:]
        
        self.tab.actions.key_down(modifier)
        time.sleep(random.uniform(between_min, between_max))
        
        for i, target in enumerate(targets):
            self.tab.actions.key_down(target)
            time.sleep(random.uniform(down_up_min, down_up_max))
            self.tab.actions.key_up(target)
            if i < len(targets) - 1:
                time.sleep(random.uniform(between_min, between_max))
        
        time.sleep(random.uniform(down_up_min, down_up_max))
        self.tab.actions.key_up(modifier)

    def _stealth_verify_paste_light(self, ele, expected_text: str):
        """
        轻量级粘贴验证（隐身模式专用）
        
        仅通过 DrissionPage 原生属性读取，不注入 JS。
        只做长度级别粗略检查，失败只记 warning 不重试。
        """
        try:
            actual_text = ""
            tag = ele.tag.lower() if hasattr(ele, 'tag') and ele.tag else ""
            
            if tag in ('textarea', 'input'):
                actual_text = ele.attr('value') or ""
            else:
                actual_text = ele.text or ""
            
            actual_len = len(actual_text)
            expected_len = len(expected_text)
            
            if expected_len == 0:
                return
            
            ratio = actual_len / expected_len if expected_len > 0 else 0
            
            if ratio < 0.5:
                logger.warning(
                    f"[STEALTH_VERIFY] 粘贴可能不完整: "
                    f"actual={actual_len}, expected={expected_len}, ratio={ratio:.2f}"
                )
            else:
                logger.debug(
                    f"[STEALTH_VERIFY] 粘贴检查通过: "
                    f"actual={actual_len}, expected={expected_len}, ratio={ratio:.2f}"
                )
        except Exception as e:
            logger.debug(f"[STEALTH_VERIFY] 检查跳过: {e}")

    # ================= 文件粘贴模式 =================

    def _get_selector_value(self, key: str) -> str:
        """读取当前站点配置里的选择器。"""
        value = self._selectors.get(key)
        return str(value).strip() if value else ""

    def _normalize_selector(self, selector: str) -> str:
        """统一补全选择器语法，默认按 CSS 处理。"""
        selector = (selector or "").strip()
        if not selector:
            return ""
        if selector.startswith(("tag:", "@", "xpath:", "css:")) or "@@" in selector:
            return selector
        return f"css:{selector}"

    def _find_elements(self, selector: str, timeout: float = 1.2) -> list:
        """查找多个元素，兼容裸 CSS 和 DrissionPage 语法。"""
        normalized = self._normalize_selector(selector)
        if not normalized:
            return []

        try:
            return list(self.tab.eles(normalized, timeout=timeout) or [])
        except Exception as e:
            logger.debug(f"[FILE_PASTE] 查找元素失败 {selector!r}: {e}")
            return []

    def _find_first_element(self, selector: str, timeout: float = 1.2):
        """查找单个元素。"""
        elements = self._find_elements(selector, timeout=timeout)
        return elements[0] if elements else None

    def _guess_mime_type(self, filepath: str) -> str:
        """推断文件 MIME。"""
        mime_type, _ = mimetypes.guess_type(filepath)
        return mime_type or "application/octet-stream"

    def _get_element_file_count(self, ele) -> int:
        """Read the selected file count from a file input element."""
        try:
            count = ele.run_js("return (this.files && this.files.length) || 0;")
            return int(count or 0)
        except Exception:
            return 0

    def _wait_for_upload_signal(self, filepath: str, timeout: float = 2.5) -> bool:
        """
        Wait for page-level evidence that a file was actually attached.

        Without this check, silent upload failures can be misclassified as success
        and only the hint text gets submitted to the model.
        """
        filename = os.path.basename(filepath or "").strip()
        stem = os.path.splitext(filename)[0].strip()
        needles = [item.lower() for item in (filename, stem) if item]
        deadline = time.time() + max(0.2, timeout)
        expected_names_js = json.dumps(needles, ensure_ascii=False)

        js = """
        return (function() {
            try {
                const expectedNames = __EXPECTED_NAMES__;
                const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
                const fileCount = inputs.reduce((sum, input) => {
                    return sum + ((input.files && input.files.length) || 0);
                }, 0);

                const text = (document.body && document.body.innerText)
                    ? String(document.body.innerText).toLowerCase()
                    : '';
                const matchedName = Array.isArray(expectedNames)
                    && expectedNames.some(name => name && text.includes(name));

                const fileText = Array.from(
                    document.querySelectorAll(
                        '.file-card-list, .fileitem-file-name, .fileitem-file-name-text, .message-input-column-file'
                    )
                )
                    .map(el => String(el.textContent || '').toLowerCase())
                    .join('\\n');
                const matchedFileNode = Array.isArray(expectedNames)
                    && expectedNames.some(name => name && fileText.includes(name));

                return { ok: true, fileCount, matchedName, matchedFileNode };
            } catch (error) {
                return {
                    ok: false,
                    fileCount: 0,
                    matchedName: false,
                    matchedFileNode: false,
                    error: String(error && error.message ? error.message : error)
                };
            }
        })();
        """.replace("__EXPECTED_NAMES__", expected_names_js)

        while time.time() < deadline:
            if self._check_cancelled():
                return False

            try:
                result = self.tab.run_js(js) or {}
            except Exception as e:
                logger.debug(f"[FILE_PASTE] 检查文件上传信号失败: {e}")
                result = {}

            file_count = int(result.get("fileCount", 0) or 0)
            if file_count > 0 or bool(result.get("matchedName")) or bool(result.get("matchedFileNode")):
                logger.debug(
                    f"[FILE_PASTE] 检测到文件上传信号 "
                    f"(file_count={file_count}, matched_name={bool(result.get('matchedName'))}, "
                    f"matched_file_node={bool(result.get('matchedFileNode'))})"
                )
                return True

            time.sleep(0.2)

        logger.warning(f"[FILE_PASTE] 未检测到文件上传信号: {filename or filepath}")
        return False

    def _click_upload_button_if_configured(self) -> bool:
        """点击站点配置里的上传按钮，常用于唤起动态 file input。"""
        selector = self._get_selector_value("upload_btn")
        if not selector:
            return False

        button = self._find_first_element(selector, timeout=1.5)
        if not button:
            logger.debug("[FILE_PASTE] 已配置 upload_btn，但当前页面未找到")
            return False

        try:
            button.click()
            self._smart_delay(0.15, 0.35)
            logger.info("[FILE_PASTE] 已点击上传按钮")
            return True
        except Exception as e:
            logger.debug(f"[FILE_PASTE] 点击上传按钮失败: {e}")
            return False

    def _list_file_inputs(self, selector: str = "") -> list:
        """列出 file input 候选元素。"""
        if selector:
            return self._find_elements(selector, timeout=1.5)

        try:
            return list(self.tab.eles('css:input[type="file"]') or [])
        except Exception as e:
            logger.debug(f"[FILE_PASTE] 查找通用 file input 失败: {e}")
            return []

    def _upload_file_via_input(self, filepath: str, selector: str = "") -> bool:
        """使用 file input 直接上传文件。"""
        candidates = self._list_file_inputs(selector)
        if not candidates:
            logger.debug("[FILE_PASTE] 当前没有可用的 file input")
            return False

        for index, file_input in enumerate(candidates, 1):
            try:
                if file_input.attr("disabled") is not None:
                    continue

                file_input.input(filepath)
                try:
                    file_input.run_js(
                        """
                        this.dispatchEvent(new Event('input', { bubbles: true }));
                        this.dispatchEvent(new Event('change', { bubbles: true }));
                        """
                    )
                except Exception:
                    pass

                selected_count = self._get_element_file_count(file_input)
                if selected_count <= 0:
                    logger.debug(
                        f"[FILE_PASTE] file input #{index} 未真正挂载文件 "
                        f"(selector={selector or 'input[type=file]'})"
                    )
                    continue

                logger.debug(
                    f"[FILE_PASTE] 已通过 file input 上传文件 "
                    f"(candidate={index}, files={selected_count})"
                )
                return True
            except Exception as e:
                logger.debug(f"[FILE_PASTE] file input #{index} 上传失败: {e}")

        return False

    def _dispatch_native_file_drag(self, zone, filepath: str) -> bool:
        """
        Use CDP drag events to simulate a browser-level file drop.

        This is closer to a real OS drag-and-drop than page-injected DragEvent,
        and works better on sites like Qwen that register file drops at the browser layer.
        """
        try:
            point = zone.run_js(
                """
                return (function() {
                    try {
                        this.scrollIntoView({ block: 'center', inline: 'center' });
                    } catch (e) {}
                    const rect = this.getBoundingClientRect();
                    const minX = rect.left + Math.min(40, Math.max(8, rect.width * 0.15));
                    const maxX = rect.right - Math.min(40, Math.max(8, rect.width * 0.15));
                    const minY = rect.top + Math.min(24, Math.max(6, rect.height * 0.2));
                    const maxY = rect.bottom - Math.min(24, Math.max(6, rect.height * 0.2));
                    const x = Math.round((minX + maxX) / 2);
                    const y = Math.round((minY + maxY) / 2);
                    return {
                        x,
                        y,
                        width: Math.round(window.innerWidth || 1280),
                        height: Math.round(window.innerHeight || 720)
                    };
                }).call(this);
                """
            ) or {}
        except Exception as e:
            logger.debug(f"[FILE_PASTE] 读取 drop zone 坐标失败: {e}")
            return False

        target_x = int(point.get("x", 0) or 0)
        target_y = int(point.get("y", 0) or 0)
        viewport_w = int(point.get("width", 1280) or 1280)
        viewport_h = int(point.get("height", 720) or 720)

        if target_x <= 0 or target_y <= 0:
            logger.debug("[FILE_PASTE] drop zone 坐标无效，跳过原生拖拽")
            return False

        start_x = max(8, min(viewport_w - 8, target_x - random.randint(160, 280)))
        start_y = max(8, min(viewport_h - 8, target_y - random.randint(100, 180)))
        mid_x = int((start_x + target_x) / 2)
        mid_y = int((start_y + target_y) / 2)

        drag_data = {
            "items": [],
            "files": [filepath],
            "dragOperationsMask": 1,
        }

        try:
            smooth_move_mouse(
                self.tab,
                from_pos=(start_x, start_y),
                to_pos=(target_x, target_y),
                duration=random.uniform(0.18, 0.42),
                check_cancelled=self._check_cancelled,
            )

            self.tab.run_cdp(
                "Input.dispatchDragEvent",
                type="dragEnter",
                x=start_x,
                y=start_y,
                data=drag_data,
                modifiers=0,
            )
            time.sleep(random.uniform(0.03, 0.08))

            self.tab.run_cdp(
                "Input.dispatchDragEvent",
                type="dragOver",
                x=mid_x,
                y=mid_y,
                data=drag_data,
                modifiers=0,
            )
            time.sleep(random.uniform(0.04, 0.1))

            self.tab.run_cdp(
                "Input.dispatchDragEvent",
                type="dragOver",
                x=target_x,
                y=target_y,
                data=drag_data,
                modifiers=0,
            )
            time.sleep(random.uniform(0.04, 0.1))

            self.tab.run_cdp(
                "Input.dispatchDragEvent",
                type="drop",
                x=target_x,
                y=target_y,
                data=drag_data,
                modifiers=0,
            )
            logger.debug("[FILE_PASTE] 已通过 CDP 原生拖拽投递文件")
            return True
        except Exception as e:
            logger.debug(f"[FILE_PASTE] CDP 原生拖拽失败: {e}")
            return False

    def _upload_file_via_drop_zone(self, filepath: str, selector: str) -> bool:
        """通过拖拽事件把文件投递到配置的 drop zone。"""
        zone = self._find_first_element(selector, timeout=1.5)
        if not zone:
            logger.debug("[FILE_PASTE] 已配置 drop_zone，但当前页面未找到")
            return False

        if self._dispatch_native_file_drag(zone, filepath):
            return True

        try:
            with open(filepath, "rb") as f:
                raw = f.read()
        except Exception as e:
            logger.error(f"[FILE_PASTE] 读取临时文件失败: {e}")
            return False

        filename = os.path.basename(filepath)
        mime_type = self._guess_mime_type(filepath)
        b64_data = base64.b64encode(raw).decode("ascii")
        escaped_name = json.dumps(filename)
        escaped_mime = json.dumps(mime_type)
        escaped_data = json.dumps(b64_data)

        js = f"""
        return (async function() {{
            try {{
                const fileName = {escaped_name};
                const mimeType = {escaped_mime};
                const b64 = {escaped_data};
                const binary = atob(b64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {{
                    bytes[i] = binary.charCodeAt(i);
                }}

                const file = new File([bytes], fileName, {{
                    type: mimeType,
                    lastModified: Date.now()
                }});

                const dt = new DataTransfer();
                dt.items.add(file);

                const target = this;
                try {{
                    target.scrollIntoView({{ block: 'center', inline: 'center' }});
                }} catch (e) {{}}

                for (const eventName of ['dragenter', 'dragover', 'drop']) {{
                    const event = new DragEvent(eventName, {{
                        bubbles: true,
                        cancelable: true,
                        dataTransfer: dt
                    }});
                    target.dispatchEvent(event);
                }}

                return true;
            }} catch (error) {{
                console.error('drop upload failed', error);
                return false;
            }}
        }}).call(this);
        """

        try:
            ok = bool(zone.run_js(js))
            if ok:
                logger.info("[FILE_PASTE] 已通过拖拽区域上传文件")
            return ok
        except Exception as e:
            logger.debug(f"[FILE_PASTE] drop zone 上传失败: {e}")
            return False

    def _upload_file_via_site_targets(self, filepath: str) -> bool:
        """
        站点感知的上传顺序：
        1. 配置的 file_input
        2. 点击 upload_btn 后再次尝试 file_input / 通用 file input
        3. 配置的 drop_zone 拖拽
        4. 通用 input[type=file]
        """
        configured_file_input = self._get_selector_value("file_input")
        configured_drop_zone = self._get_selector_value("drop_zone")

        if configured_file_input and self._upload_file_via_input(filepath, configured_file_input):
            return True

        if self._upload_file_via_input(filepath):
            return True

        if configured_drop_zone and self._upload_file_via_drop_zone(filepath, configured_drop_zone):
            return True

        clicked_upload_button = self._click_upload_button_if_configured()
        if clicked_upload_button:
            time.sleep(0.35)
            if configured_file_input and self._upload_file_via_input(filepath, configured_file_input):
                return True
            if self._upload_file_via_input(filepath):
                return True
            if configured_drop_zone and self._upload_file_via_drop_zone(filepath, configured_drop_zone):
                return True

        if self._upload_file_via_input(filepath):
            return True

        return False
    
    def _should_use_file_paste(self, text: str) -> bool:
        """判断是否应该使用文件粘贴模式"""
        if not self._file_paste_config.get("enabled", False):
            return False
        
        threshold = self._file_paste_config.get("threshold", 50000)
        return len(text) > threshold
    
    def _fill_via_file_paste(self, ele, text: str) -> bool:
        """
        通过临时 txt 文件上传内容

        流程：
        1. 创建临时 txt 文件并写入文本
        2. 优先尝试页面中的 input[type=file] 直接上传
        3. 若无可用 file input，再回退到 Win32 CF_HDROP + Ctrl+V

        Args:
            ele: 输入框元素
            text: 文本内容
        
        Returns:
            是否成功
        """
        from app.core.tab_pool import get_clipboard_lock
        
        threshold = self._file_paste_config.get("threshold", 50000)
        logger.info(
            f"[FILE_PASTE] 文本长度 {len(text)} 超过阈值 {threshold}，"
            f"使用文件粘贴模式"
        )
        
        clipboard_lock = get_clipboard_lock()
        
        try:
            # 1. 聚焦输入框
            ele.click()
            self._smart_delay(0.15, 0.35)
            
            if self._check_cancelled():
                return False
            
            # 2. 全选现有内容（准备覆盖）
            if self.stealth_mode:
                self._human_key_combo('Control', 'A')
                self._smart_delay(0.08, 0.18)
            else:
                self.tab.actions.key_down('Control').key_down('A').key_up('A').key_up('Control')
                time.sleep(0.1)
            
            if self._check_cancelled():
                return False
            
            # 3. 创建临时文件
            filepath = create_temp_txt(text)
            if not filepath:
                logger.error("[FILE_PASTE] 创建临时文件失败")
                return False

            logger.debug(f"[FILE_PASTE] 临时文件: {filepath}")

            # 4. 优先尝试站点专配上传入口，再回退到通用 file input
            uploaded = self._upload_file_via_site_targets(filepath)

            # 5. 若站点没有可用上传入口，再退回剪贴板文件粘贴
            if not uploaded:
                with clipboard_lock:
                    if not copy_file_to_clipboard(filepath):
                        logger.error("[FILE_PASTE] 复制文件到剪贴板失败")
                        return False

                    time.sleep(random.uniform(0.08, 0.15))

                    if self.stealth_mode:
                        self._human_key_combo('Control', 'V')
                    else:
                        self.tab.actions.key_down('Control').key_down('V').key_up('V').key_up('Control')
            
            # 6. 等待文件处理完成
            time.sleep(random.uniform(0.5, 1.0))
            self._smart_delay(0.3, 0.6)
            
            if self._check_cancelled():
                return True

            if not self._wait_for_upload_signal(filepath):
                logger.warning("[FILE_PASTE] 文件上传未生效，放弃文件粘贴模式")
                return False
            
            # 7. 追加引导文本（确保输入框有文字内容，否则某些网站无法发送）
            hint_text = self._file_paste_config.get("hint_text", "完全专注于文件内容")
            if hint_text:
                logger.debug(f"[FILE_PASTE] 追加引导文本: {hint_text}")
                
                clipboard_lock_inner = get_clipboard_lock()
                with clipboard_lock_inner:
                    original_cb = ""
                    try:
                        original_cb = pyperclip.paste()
                    except Exception:
                        pass
                        
                    pyperclip.copy(hint_text)
                    time.sleep(random.uniform(0.06, 0.12))
                    
                    if self.stealth_mode:
                        self._human_key_combo('Control', 'V')
                    else:
                        self.tab.actions.key_down('Control').key_down('V').key_up('V').key_up('Control')
                    
                    time.sleep(random.uniform(0.2, 0.4))
                    
                    try:
                        pyperclip.copy(original_cb)
                    except Exception:
                        pass
                
                self._smart_delay(0.2, 0.4)
            
            logger.info(f"[FILE_PASTE] 文件粘贴完成 ({len(text)} 字符)")
            return True
        
        except Exception as e:
            logger.error(f"[FILE_PASTE] 文件粘贴失败: {e}")
            return False

    def fill_via_clipboard_no_click(self, ele, text: str):
        """
        隐身模式专用：跳过 ele.click() 的剪贴板粘贴
        
        假设调用方已经通过人类化点击聚焦了输入框。
        """
        # 🆕 文件粘贴前置判断
        if self._should_use_file_paste(text):
            if self._fill_via_file_paste(ele, text):
                return
            logger.warning("[FILE_PASTE] 文件粘贴失败，降级到剪贴板文本粘贴")
        
        logger.debug(f"[STEALTH] 使用剪贴板粘贴（无click），长度 {len(text)}")
        
        clipboard_lock = get_clipboard_lock()
        
        settle_min = getattr(BrowserConstants, 'STEALTH_PASTE_SETTLE_MIN', 0.4)
        settle_max = getattr(BrowserConstants, 'STEALTH_PASTE_SETTLE_MAX', 0.8)
        skip_verify = getattr(BrowserConstants, 'STEALTH_SKIP_PASTE_VERIFY', True)
        
        try:
            if self._check_cancelled():
                return
            
            # 全选（人类化时序）
            self._human_key_combo('Control', 'A')
            self._smart_delay(0.08, 0.18)
            
            if self._check_cancelled():
                return
            
            # 剪贴板操作（加锁）
            with clipboard_lock:
                original_clipboard = ""
                try:
                    original_clipboard = pyperclip.paste()
                except Exception:
                    pass
                
                pyperclip.copy(text)
                time.sleep(random.uniform(0.06, 0.15))
                
                # Ctrl+V 粘贴
                self._human_key_combo('Control', 'V')
                
                # 等待粘贴完成
                time.sleep(random.uniform(settle_min, settle_max))
                
                # 恢复剪贴板
                try:
                    pyperclip.copy(original_clipboard)
                except Exception:
                    pass
            
            # 额外等待框架响应
            self._smart_delay(0.2, 0.5)
            
            if self._check_cancelled():
                return
            
            if not skip_verify:
                self._stealth_verify_paste_light(ele, text)
            else:
                logger.debug("[STEALTH] 跳过粘贴验证")
        
        except Exception as e:
            logger.error(f"[STEALTH] 剪贴板粘贴失败: {e}，降级到 JS 方式")
            self.fill_via_js(ele, text)
    # ================= 剪贴板模式输入 =================
    
    def fill_via_clipboard(self, ele, text: str):
        """
        隐身模式专用：剪贴板 + Ctrl+V 输入（v5.6 反检测增强版）
        
        改进：
        - 人类化按键时序（_human_key_combo）
        - Ctrl+A → Ctrl+V（跳过 Delete，人类习惯：选中直接粘贴覆盖）
        - 默认跳过 JS 注入验证（STEALTH_SKIP_PASTE_VERIFY）
        - 验证降级为原生属性读取
        - 🆕 文件粘贴模式：超长文本自动切换为文件粘贴
        """
        # 🆕 文件粘贴前置判断
        if self._should_use_file_paste(text):
            if self._fill_via_file_paste(ele, text):
                return
            logger.warning("[FILE_PASTE] 文件粘贴失败，降级到剪贴板文本粘贴")
        
        logger.debug(f"[STEALTH] 使用剪贴板粘贴，长度 {len(text)}")
    
        clipboard_lock = get_clipboard_lock()
        
        settle_min = getattr(BrowserConstants, 'STEALTH_PASTE_SETTLE_MIN', 0.4)
        settle_max = getattr(BrowserConstants, 'STEALTH_PASTE_SETTLE_MAX', 0.8)
        skip_verify = getattr(BrowserConstants, 'STEALTH_SKIP_PASTE_VERIFY', True)
    
        try:
            # 1. 聚焦输入框（原生点击）
            ele.click()
            self._smart_delay(0.15, 0.35)
        
            if self._check_cancelled():
                return
        
            # 2. 全选（人类化时序）—— 粘贴会自动覆盖选中内容，无需 Delete
            self._human_key_combo('Control', 'A')
            self._smart_delay(0.08, 0.18)
        
            if self._check_cancelled():
                return
        
            # 3. 剪贴板操作（加锁）
            with clipboard_lock:
                original_clipboard = ""
                try:
                    original_clipboard = pyperclip.paste()
                except Exception:
                    pass
            
                pyperclip.copy(text)
                time.sleep(random.uniform(0.06, 0.15))
            
                # Ctrl+V 粘贴（人类化时序）
                self._human_key_combo('Control', 'V')
            
                # 等待粘贴完成 + DOM 更新
                time.sleep(random.uniform(settle_min, settle_max))
            
                # 恢复剪贴板
                try:
                    pyperclip.copy(original_clipboard)
                except Exception:
                    pass
        
            # 4. 额外等待框架响应
            self._smart_delay(0.2, 0.5)
        
            if self._check_cancelled():
                return
        
            # 5. 验证（可配置跳过，默认跳过以避免 JS 注入）
            if not skip_verify:
                self._stealth_verify_paste_light(ele, text)
            else:
                logger.debug("[STEALTH] 跳过粘贴验证（STEALTH_SKIP_PASTE_VERIFY=true）")
    
        except Exception as e:
            logger.error(f"[STEALTH] 剪贴板粘贴失败: {e}，降级到 JS 方式")
            self.fill_via_js(ele, text)
    
    def verify_clipboard_result(self, ele, expected_text: str):
        """验证剪贴板粘贴结果"""
        expected_normalized = self.normalize_for_compare(expected_text)
        expected_core = re.sub(r'\s+', '', expected_text)
        is_rich_editor = self.is_contenteditable(ele)
        
        # 第一次检查
        actual = self.read_input_full_text(ele)
        actual_normalized = self.normalize_for_compare(actual)
        
        # 精确匹配
        if actual_normalized == expected_normalized:
            logger.info(f"[CLIPBOARD_OK] 粘贴成功，长度 {len(actual)}")
            return
        
        # 富文本编辑器宽松匹配
        if is_rich_editor:
            actual_core = re.sub(r'\s+', '', actual)
            if actual_core == expected_core:
                diff = len(actual) - len(expected_text)                
                return
        
        # 失败：尝试重试
        logger.warning(
            f"[CLIPBOARD_RETRY] 粘贴不完整 "
            f"(actual={len(actual)}, expected={len(expected_text)})"
        )
        
        try:

        
            clipboard_lock = get_clipboard_lock()
        
            # 清空
            ele.click()
            time.sleep(0.05)
            self.tab.actions.key_down('Control').key_down('A').key_up('A').key_up('Control')
            time.sleep(0.05)
            self.tab.actions.key_down('Delete').key_up('Delete')
            time.sleep(0.1)
        
            # 重试粘贴（完整的 copy→paste→restore 原子操作，加锁保护）
            with clipboard_lock:
                # 备份当前剪贴板
                backup_clipboard = ""
                try:
                    backup_clipboard = pyperclip.paste()
                except Exception:
                    pass
            
                # 粘贴操作
                pyperclip.copy(expected_text)
                time.sleep(0.05)
                self.tab.actions.key_down('Control').key_down('V').key_up('V').key_up('Control')
                time.sleep(0.5)
            
                # 恢复剪贴板
                try:
                    pyperclip.copy(backup_clipboard)
                except Exception:
                    pass
        
            # 最终验证
            actual = self.read_input_full_text(ele)
            actual_normalized = self.normalize_for_compare(actual)
        
            if actual_normalized == expected_normalized:
                logger.info("[CLIPBOARD_OK] 重试成功")
                return
        
            if is_rich_editor:
                actual_core = re.sub(r'\s+', '', actual)
                if actual_core == expected_core:
                    logger.info("[CLIPBOARD_OK] 重试成功（富文本匹配）")
                    return
        
            # 彻底失败
            logger.error(
                f"[CLIPBOARD_FAIL] 重试后仍失败 "
                f"(actual={len(actual)}, expected={len(expected_text)})"
            )
            raise WorkflowError("clipboard_paste_failed")
    
        except Exception as e:
            logger.error(f"[CLIPBOARD_FAIL] 重试异常: {e}")
            raise WorkflowError("clipboard_paste_failed")

__all__ = ['TextInputHandler']
