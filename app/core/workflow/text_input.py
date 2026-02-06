"""
app/core/workflow/text_input.py - 文本输入处理

职责：
- 输入框工具方法（读取、规范化、验证）
- JS 模式输入（原子写入、分块、验证修正）
- 剪贴板模式输入（隐身模式专用）
"""

import re
import time
import json
import base64
import random
from typing import Optional
import pyperclip
from app.core.config import logger, BrowserConstants, WorkflowError
from app.core.tab_pool import get_clipboard_lock


# ================= 常量配置 =================

CHUNK_SIZE_THRESHOLD = 30000


# ================= 文本输入处理器 =================

class TextInputHandler:
    """文本输入处理器"""
    
    def __init__(self, tab, stealth_mode: bool, smart_delay_fn, check_cancelled_fn):
        """
        Args:
            tab: 浏览器标签页
            stealth_mode: 是否隐身模式
            smart_delay_fn: 智能延迟函数
            check_cancelled_fn: 取消检查函数
        """
        self.tab = tab
        self.stealth_mode = stealth_mode
        self._smart_delay = smart_delay_fn
        self._check_cancelled = check_cancelled_fn
    
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

              if (!success) {{
                const tn = document.createTextNode(newText);
                range.insertNode(tn);
                range.setStartAfter(tn);
                range.collapse(true);
                sel.removeAllRanges();
                sel.addRange(range);
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
            logger.error("[CHUNKED_INPUT] 首块写入失败")
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
    # ================= 剪贴板模式输入 =================
    
    def fill_via_clipboard(self, ele, text: str):
        """
        隐身模式专用：剪贴板 + Ctrl+V 输入（v5.6 反检测增强版）
        
        改进：
        - 人类化按键时序（_human_key_combo）
        - Ctrl+A → Ctrl+V（跳过 Delete，人类习惯：选中直接粘贴覆盖）
        - 默认跳过 JS 注入验证（STEALTH_SKIP_PASTE_VERIFY）
        - 验证降级为原生属性读取
        """
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