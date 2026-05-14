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
import pyperclip
from app.core.config import logger, BrowserConstants, WorkflowError
from app.core.tab_pool import get_clipboard_lock
from app.utils.file_paste import create_temp_txt, copy_file_to_clipboard
from app.utils.human_mouse import smooth_move_mouse

# ================= 常量配置 =================

TEXT_INPUT_CHUNK_SIZE_DEFAULT = 30000
TEXT_INPUT_CHUNK_SIZE_MIN = 1000
TEXT_INPUT_CHUNK_SIZE_MAX = 1000000


# ================= 文本输入处理器 =================

class TextInputHandler:
    """文本输入处理器"""
    
    def __init__(self, tab, stealth_mode: bool, smart_delay_fn, check_cancelled_fn,
                 file_paste_config: dict = None,
                 selectors: dict = None,
                 attachment_monitor = None,
                 attachment_monitor_config: dict = None):
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
        self._attachment_monitor = attachment_monitor
        self._attachment_monitor_config = attachment_monitor_config or {}
        self._recent_file_upload_at = 0.0
        self._last_file_upload_path = ""
        self._last_upload_signal_wait = {
            "confirmed": False,
            "weak_signal_seen": False,
            "last_state": {},
        }
        self._active_input_selector = ""
        self._active_input_target_key = ""

    def has_recent_attachment_upload(self, window: float = 45.0) -> bool:
        """Whether this request recently attached a file via file-paste/upload."""
        ts = float(getattr(self, "_recent_file_upload_at", 0.0) or 0.0)
        return ts > 0 and (time.time() - ts) <= window

    def get_post_upload_settle_seconds(self, default: float = 0.0) -> float:
        """Per-site extra settle wait after a file upload finishes."""
        try:
            value = float(self._file_paste_config.get("post_upload_settle", default))
        except Exception:
            value = float(default or 0.0)
        return max(0.0, value)

    def get_upload_signal_timeout(self, default: float = 2.5) -> float:
        """Maximum time to wait for a strong upload signal."""
        try:
            value = float(self._file_paste_config.get("upload_signal_timeout", default))
        except Exception:
            value = float(default or 0.0)
        return max(0.5, value)

    def get_upload_signal_grace(self, default: float = 3.0) -> float:
        """Extra grace window when the page shows weak/pending upload hints."""
        try:
            value = float(self._file_paste_config.get("upload_signal_grace", default))
        except Exception:
            value = float(default or 0.0)
        return max(0.0, value)

    def set_active_input_context(self, selector: str = "", target_key: str = ""):
        """Remember the current workflow input locator for post-upload reacquire."""
        self._active_input_selector = str(selector or "").strip()
        self._active_input_target_key = str(target_key or "").strip()

    def _sanitize_text_chunk_size(self, raw_value, default: int = TEXT_INPUT_CHUNK_SIZE_DEFAULT) -> int:
        """Normalize long-text chunk size from config/user input."""
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = int(default or TEXT_INPUT_CHUNK_SIZE_DEFAULT)
        return max(TEXT_INPUT_CHUNK_SIZE_MIN, min(value, TEXT_INPUT_CHUNK_SIZE_MAX))

    def get_text_input_chunk_size(self, default: int = TEXT_INPUT_CHUNK_SIZE_DEFAULT) -> int:
        """Long-text JS input chunk size from browser constants."""
        return self._sanitize_text_chunk_size(
            BrowserConstants.get("TEXT_INPUT_CHUNK_SIZE"),
            default=default,
        )

    def probe_recent_upload_signal(self) -> dict:
        """Probe the latest file-upload signal for the most recent temp file."""
        filepath = str(getattr(self, "_last_file_upload_path", "") or "").strip()
        if not filepath:
            return {}
        return self._probe_upload_signal(filepath)

    def has_strong_upload_signal(self, state: dict = None) -> bool:
        """Whether the current upload state is strong enough to trust as attached."""
        signal_state = state or self.probe_recent_upload_signal()
        return self._has_upload_signal(signal_state)

    def has_confirmed_upload_signal(self, state: dict = None) -> bool:
        """Whether the latest file upload was already confirmed by page/file-input probes."""
        return self._has_confirmed_upload_signal(state)

    def _attachment_monitor_list(self, key: str) -> list:
        raw_value = self._attachment_monitor_config.get(key) if isinstance(self._attachment_monitor_config, dict) else None
        if not isinstance(raw_value, list):
            return []
        cleaned = []
        for item in raw_value:
            value = str(item or "").strip()
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned

    def _should_reacquire_input_after_upload(self) -> bool:
        return bool(self._file_paste_config.get("reacquire_input_after_upload", False))

    def _has_confirmed_upload_signal(self, state: dict = None) -> bool:
        """Whether upload has already been strongly confirmed by file-input/page probes."""
        signal_state = state if isinstance(state, dict) else self._last_upload_signal_wait
        if bool((signal_state or {}).get("confirmed")):
            return True
        return self.has_strong_upload_signal((signal_state or {}).get("last_state"))
    
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
    
    def _probe_focus_state(self, ele) -> dict:
        """检查当前焦点/选区是否仍落在目标输入元素内。"""
        try:
            state = ele.run_js("""
                return (function(){
                    try {
                        const el = this;
                        const active = document.activeElement;
                        const activeWithin = !!active && (
                            active === el
                            || (el.contains && el.contains(active))
                            || (active.contains && active.contains(el))
                        );

                        const sel = window.getSelection ? window.getSelection() : null;
                        const anchor = sel ? sel.anchorNode : null;
                        const focus = sel ? sel.focusNode : null;
                        const selectionWithin = !!(
                            anchor && (anchor === el || (el.contains && el.contains(anchor)))
                        ) || !!(
                            focus && (focus === el || (el.contains && el.contains(focus)))
                        );

                        return {
                            activeWithin,
                            selectionWithin,
                            activeTag: active ? String(active.tagName || '').toLowerCase() : '',
                            selectionTextLen: sel ? String(sel).length : 0
                        };
                    } catch (e) {
                        return {
                            activeWithin: false,
                            selectionWithin: false,
                            activeTag: '',
                            selectionTextLen: 0,
                            error: String(e && e.message ? e.message : e)
                        };
                    }
                }).call(this);
            """) or {}
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}

    def ensure_input_focus(self, ele, attempts: int = 2, log_failure: bool = True) -> bool:
        """尽量确保后续 Ctrl+A / Ctrl+V 作用在目标输入框上。"""
        for attempt in range(max(1, attempts) + 1):
            state = self._probe_focus_state(ele)
            active_within = bool(state.get("activeWithin"))
            selection_within = bool(state.get("selectionWithin"))
            if active_within or selection_within:
                return True

            self.focus_to_end(ele)
            time.sleep(0.05)

            state = self._probe_focus_state(ele)
            active_within = bool(state.get("activeWithin"))
            selection_within = bool(state.get("selectionWithin"))
            if active_within or selection_within:
                return True

            if attempt < attempts:
                self.physical_activate(ele)
                time.sleep(0.08)

        final_state = self._probe_focus_state(ele)
        if log_failure:
            logger.warning(
                "[INPUT_FOCUS] 输入框焦点校验失败 "
                f"(active_within={bool(final_state.get('activeWithin'))}, "
                f"selection_within={bool(final_state.get('selectionWithin'))}, "
                f"active_tag={final_state.get('activeTag')!r}, "
                f"selection_len={final_state.get('selectionTextLen', 0)})"
            )
        return False

    def verify_paste_result_minimal(self, ele, expected_text: str) -> bool:
        """最小化校验粘贴结果，避免整页全选后脚本误判成功。"""
        expected = self.normalize_for_compare(expected_text)
        if not expected:
            return True

        actual = self.read_input_full_text(ele)
        actual_normalized = self.normalize_for_compare(actual)
        if actual_normalized == expected:
            return True

        expected_core = re.sub(r'\s+', '', expected_text)
        actual_core = re.sub(r'\s+', '', actual)
        if actual_core == expected_core:
            return True

        if expected_core and expected_core in actual_core:
            return True

        expected_len = len(expected_core)
        actual_len = len(actual_core)
        ratio = (actual_len / expected_len) if expected_len > 0 else 1.0

        prefix_len = min(64, expected_len, actual_len)
        suffix_len = min(64, expected_len, actual_len)
        prefix_match = (
            prefix_len == 0
            or actual_core[:prefix_len] == expected_core[:prefix_len]
        )
        suffix_match = (
            suffix_len == 0
            or actual_core[-suffix_len:] == expected_core[-suffix_len:]
        )
        length_gap = abs(expected_len - actual_len)
        max_gap = max(4, expected_len // 100)

        if (
            actual_len > 0
            and ratio >= 0.98
            and length_gap <= max_gap
            and prefix_match
            and suffix_match
        ):
            logger.debug(
                "[PASTE_VERIFY] 接受近似匹配结果 "
                f"(actual={actual_len}, expected={expected_len}, ratio={ratio:.2f}, gap={length_gap})"
            )
            return True

        if actual_len == 0 or ratio < 0.98 or not prefix_match or not suffix_match:
            logger.warning(
                "[PASTE_VERIFY] 检测到异常粘贴结果 "
                f"(actual={actual_len}, expected={expected_len}, ratio={ratio:.2f}, "
                f"prefix_match={prefix_match}, suffix_match={suffix_match})"
            )
            return False

        logger.warning(
            "[PASTE_VERIFY] 粘贴结果长度偏差过大 "
            f"(actual={actual_len}, expected={expected_len}, gap={length_gap})"
        )
        return False

    def _input_contains_text_loose(self, ele, expected_text: str) -> bool:
        """宽松检查输入框中是否已包含指定文本。"""
        expected_core = re.sub(r'\s+', '', expected_text or '')
        if not expected_core:
            return True

        actual = self.read_input_full_text(ele)
        actual_core = re.sub(r'\s+', '', actual or '')
        return expected_core in actual_core

    def _append_file_paste_hint(self, ele, hint_text: str) -> bool:
        """文件上传后追加提示词，并尽量确认提示词真的进入输入框。"""
        hint_text = str(hint_text or "")
        if not hint_text.strip():
            return True

        # 上传控件经常会抢走焦点，先显式把焦点拉回真实输入框末尾。
        self.focus_to_end(ele)
        time.sleep(0.06)

        if not self.ensure_input_focus(ele):
            logger.warning("[FILE_PASTE] 追加引导文本前重新聚焦失败，尝试直接回退追加")
        else:
            clipboard_lock = get_clipboard_lock()
            with clipboard_lock:
                original_cb = ""
                try:
                    original_cb = pyperclip.paste()
                except Exception:
                    pass

                try:
                    pyperclip.copy(hint_text)
                    time.sleep(random.uniform(0.06, 0.12))

                    if self.stealth_mode:
                        self._human_key_combo('Control', 'V')
                    else:
                        self.tab.actions.key_down('Control').key_down('V').key_up('V').key_up('Control')

                    time.sleep(random.uniform(0.2, 0.4))
                finally:
                    try:
                        pyperclip.copy(original_cb)
                    except Exception:
                        pass

            self._smart_delay(0.15, 0.3)
            if self._input_contains_text_loose(ele, hint_text):
                return True

            logger.warning("[FILE_PASTE] 剪贴板追加引导文本未生效，回退到原子追加")

        if self.stealth_mode:
            logger.warning("[FILE_PASTE] 隐身模式下跳过 JS 回退追加，避免增加风控风险")
            return False

        if self.set_input_atomic(ele, hint_text, mode="append"):
            self._smart_delay(0.12, 0.25)
            if self._input_contains_text_loose(ele, hint_text):
                return True

        logger.warning("[FILE_PASTE] 引导文本追加失败，发送内容可能缺少附带说明")
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
    
    def chunked_input(self, ele, text: str, chunk_size: int = None) -> bool:
        """分块写入逻辑（普通模式用）"""
        chunk_size = (
            self.get_text_input_chunk_size()
            if chunk_size is None
            else self._sanitize_text_chunk_size(chunk_size)
        )
        total_len = len(text)
        total_chunks = max(1, (total_len + max(1, chunk_size) - 1) // max(1, chunk_size))
        
        # 情况1：短文本直接一次写入
        if total_len <= chunk_size:
            logger.debug(f"[CHUNKED_INPUT] 短文本模式: {total_len} 字符，直接写入")
            return self.set_input_atomic(ele, text, mode="overwrite")

        # 情况2：长文本分块写入
        logger.info(
            f"[CHUNKED_INPUT] 长文本模式: {total_len} 字符，分块大小 {chunk_size}，"
            f"预计 {total_chunks} 块"
        )
        
        # 首块：覆盖写入
        first_chunk = text[:chunk_size]
        if not self.set_input_atomic(ele, first_chunk, mode="overwrite"):
            logger.warning("[CHUNKED_INPUT] 首块原子写入失败，尝试备用方案")
            # 降级：直接 JS 赋值
            if not self.fill_via_js_backup(ele, first_chunk):
                logger.error("[CHUNKED_INPUT] 首块写入彻底失败")
                return False
        
        logger.debug(
            f"[CHUNKED_INPUT] 首块完成: 1/{total_chunks} "
            f"(chars=0-{len(first_chunk)})"
        )
        time.sleep(0.1)
        
        # 后续块：追加写入
        current_pos = chunk_size
        chunk_index = 1
        next_progress_pct = 25
        
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
            
            completed_chunks = chunk_index + 1
            progress_pct = int(end_pos * 100 / max(1, total_len))
            if (
                completed_chunks <= 3
                or completed_chunks == total_chunks
                or progress_pct >= next_progress_pct
            ):
                logger.debug(
                    f"[CHUNKED_INPUT] 进度 {completed_chunks}/{total_chunks} "
                    f"({progress_pct}%, chars={current_pos}-{end_pos})"
                )
                while progress_pct >= next_progress_pct:
                    next_progress_pct += 25
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
        chunk_size = self.get_text_input_chunk_size()
        success = self.chunked_input(ele, text, chunk_size=chunk_size)

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
        人类化组合键：保留轻微随机感，但不刻意拖慢节奏
        
        用法：
            self._human_key_combo('Control', 'A')   → Ctrl+A
            self._human_key_combo('Control', 'V')   → Ctrl+V
            self._human_key_combo('Delete')          → Delete
        """
        down_up_min = float(BrowserConstants.get('STEALTH_KEY_DOWN_UP_MIN') or 0.015)
        down_up_max = float(BrowserConstants.get('STEALTH_KEY_DOWN_UP_MAX') or 0.04)
        between_min = float(BrowserConstants.get('STEALTH_KEY_BETWEEN_MIN') or 0.02)
        between_max = float(BrowserConstants.get('STEALTH_KEY_BETWEEN_MAX') or 0.06)
        
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

    def _reacquire_input_after_upload(self, fallback_ele=None):
        """
        Re-locate the active input after upload finishes.

        Some sites rebuild the composer after attaching a file, so the old
        element reference stops receiving subsequent hint text.
        """
        if not self._should_reacquire_input_after_upload():
            return fallback_ele

        candidates = []
        configured = str(self._file_paste_config.get("post_upload_input_selector") or "").strip()
        if configured:
            candidates.append(("post_upload_input_selector", configured))
        if self._active_input_selector:
            candidates.append(("active_input_selector", self._active_input_selector))
        if self._active_input_target_key:
            selector = self._get_selector_value(self._active_input_target_key)
            if selector:
                candidates.append((f"target_key:{self._active_input_target_key}", selector))

        seen = set()
        for source, selector in candidates:
            normalized = self._normalize_selector(selector)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            try:
                ele = self.tab.ele(normalized, timeout=1.5)
            except Exception as e:
                logger.debug(f"[FILE_PASTE] 上传后重定位输入框失败 ({source}={selector!r}): {e}")
                continue
            if ele and getattr(ele, "tag", None):
                logger.debug(f"[FILE_PASTE] 上传后重定位输入框成功 ({source}={selector!r})")
                return ele

        logger.warning("[FILE_PASTE] 上传后未能重新定位输入框，回退到原元素")
        return fallback_ele

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

    def _probe_upload_signal(self, filepath: str) -> dict:
        """Read page-level evidence for whether a file attachment appeared."""
        filename = os.path.basename(filepath or "").strip()
        stem = os.path.splitext(filename)[0].strip()
        needles = [item.lower() for item in (filename, stem) if item]
        expected_names_js = json.dumps(needles, ensure_ascii=False)
        send_selector_js = json.dumps(self._get_selector_value("send_btn"), ensure_ascii=False)
        configured_upload_signal_selectors = self._file_paste_config.get("upload_signal_selectors") or []
        if not isinstance(configured_upload_signal_selectors, list):
            configured_upload_signal_selectors = [configured_upload_signal_selectors]
        attachment_monitor_selectors = self._attachment_monitor_list("attachment_selectors")

        upload_signal_selectors = [
            ".file-card-list",
            ".fileitem-btn",
            ".fileitem-file-name",
            ".fileitem-file-name-text",
            ".message-input-column-file",
            "[class*='attachment']",
            "[class*='upload-preview']",
            "[class*='uploaded-file']",
            "[class*='file-preview']",
            "[class*='preview-file']",
            "[data-testid*='attachment']",
            "[data-testid*='file']",
            "[data-test-id*='attachment']",
            "[data-test-id*='file']",
        ]
        for selector in configured_upload_signal_selectors:
            selector = str(selector or "").strip()
            if selector and selector not in upload_signal_selectors:
                upload_signal_selectors.append(selector)
        for selector in attachment_monitor_selectors:
            if selector and selector not in upload_signal_selectors:
                upload_signal_selectors.append(selector)

        upload_selectors_js = json.dumps(upload_signal_selectors, ensure_ascii=False)
        pending_selectors = [
            "progress",
            '[role="progressbar"]',
            '[aria-busy="true"]',
            '[class*="uploading"]',
            '[class*="pending"]',
            '[class*="loading"]',
            '[class*="progress"]',
            '[class*="processing"]',
        ]
        for selector in self._attachment_monitor_list("pending_selectors"):
            if selector and selector not in pending_selectors:
                pending_selectors.append(selector)
        pending_selectors_js = json.dumps(pending_selectors, ensure_ascii=False)

        busy_markers = [
            "上传中",
            "处理中",
            "解析中",
            "分析中",
            "loading",
            "uploading",
            "processing",
            "preparing",
            "analyzing",
            "reading",
        ]
        for marker in self._attachment_monitor_list("busy_text_markers"):
            if marker and marker not in busy_markers:
                busy_markers.append(marker)
        busy_markers_js = json.dumps(busy_markers, ensure_ascii=False)

        send_disabled_markers = [
            "disabled",
            "loading",
            "uploading",
            "sending",
            "processing",
        ]
        for marker in self._attachment_monitor_list("send_button_disabled_markers"):
            if marker and marker not in send_disabled_markers:
                send_disabled_markers.append(marker)
        send_disabled_markers_js = json.dumps(send_disabled_markers, ensure_ascii=False)

        js = """
        return (function() {
            try {
                const expectedNames = __EXPECTED_NAMES__;
                const uploadSelectors = __UPLOAD_SIGNAL_SELECTORS__;
                const pendingSelectors = __PENDING_SELECTORS__;
                const busyMarkers = __BUSY_MARKERS__;
                const sendDisabledMarkers = __SEND_DISABLED_MARKERS__;
                const sendSelector = __SEND_SELECTOR__;
                const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
                const fileCount = inputs.reduce((sum, input) => {
                    return sum + ((input.files && input.files.length) || 0);
                }, 0);

                const root = document.querySelector(
                    '.message-input-wrapper, .message-input-container, .chat-layout-input-container, '
                    + '#dropzone-container, form:has(button[type=\"submit\"]), rich-textarea, '
                    + '[class*=\"message-input\"], [class*=\"input-container\"], [class*=\"input-wrapper\"]'
                ) || document.body;

                const includesAny = (text, markers) => {
                    const haystack = String(text || '').toLowerCase();
                    return Array.isArray(markers) && markers.some(marker => {
                        const needle = String(marker || '').toLowerCase().trim();
                        return needle && haystack.includes(needle);
                    });
                };

                const text = (root && root.innerText)
                    ? String(root.innerText).toLowerCase()
                    : '';
                const matchedName = Array.isArray(expectedNames)
                    && expectedNames.some(name => name && text.includes(name));

                const uploadNodes = Array.from(
                    root.querySelectorAll(Array.isArray(uploadSelectors) ? uploadSelectors.join(',') : '')
                );
                const fileText = uploadNodes
                    .map(el => String(el.textContent || '').toLowerCase())
                    .join('\\n');
                const matchedFileNode = Array.isArray(expectedNames)
                    && expectedNames.some(name => name && fileText.includes(name));
                const fileNodeCount = uploadNodes.length;
                const pendingSelectorText = Array.isArray(pendingSelectors) ? pendingSelectors.join(',') : '';
                const pendingCount = pendingSelectorText ? root.querySelectorAll(pendingSelectorText).length : 0;
                const pendingText = includesAny(text, busyMarkers);

                let sendBtn = null;
                if (sendSelector) {
                    try {
                        sendBtn = document.querySelector(sendSelector);
                    } catch (e) {}
                }
                const sendDisabled = !!sendBtn && (
                    !!sendBtn.disabled
                    || sendBtn.getAttribute('aria-disabled') === 'true'
                    || includesAny(
                        [
                            sendBtn.className,
                            sendBtn.innerText,
                            sendBtn.textContent,
                            sendBtn.getAttribute('title'),
                            sendBtn.getAttribute('aria-label'),
                            sendBtn.getAttribute('data-testid')
                        ].join(' '),
                        sendDisabledMarkers
                    )
                );

                return { ok: true, fileCount, matchedName, matchedFileNode, fileNodeCount, pendingCount, pendingText, sendDisabled };
            } catch (error) {
                return {
                    ok: false,
                    fileCount: 0,
                    matchedName: false,
                    matchedFileNode: false,
                    fileNodeCount: 0,
                    pendingCount: 0,
                    pendingText: false,
                    sendDisabled: false,
                    error: String(error && error.message ? error.message : error)
                };
            }
        })();
        """.replace("__EXPECTED_NAMES__", expected_names_js)\
            .replace("__UPLOAD_SIGNAL_SELECTORS__", upload_selectors_js)\
            .replace("__PENDING_SELECTORS__", pending_selectors_js)\
            .replace("__BUSY_MARKERS__", busy_markers_js)\
            .replace("__SEND_DISABLED_MARKERS__", send_disabled_markers_js)\
            .replace("__SEND_SELECTOR__", send_selector_js)

        try:
            result = self.tab.run_js(js) or {}
        except Exception as e:
            logger.debug(f"[FILE_PASTE] 检查文件上传信号失败: {e}")
            result = {}

        return {
            "fileCount": int(result.get("fileCount", 0) or 0),
            "matchedName": bool(result.get("matchedName")),
            "matchedFileNode": bool(result.get("matchedFileNode")),
            "fileNodeCount": int(result.get("fileNodeCount", 0) or 0),
            "pendingCount": int(result.get("pendingCount", 0) or 0),
            "pendingText": bool(result.get("pendingText")),
            "sendDisabled": bool(result.get("sendDisabled")),
            "filename": filename or filepath,
        }

    def _has_upload_signal(self, state: dict, baseline: dict = None) -> bool:
        """Check whether the current page state shows a new attachment signal."""
        baseline = baseline or {}
        file_count = int(state.get("fileCount", 0) or 0)
        baseline_file_count = int(baseline.get("fileCount", 0) or 0)
        matched_name = bool(state.get("matchedName"))
        matched_file_node = bool(state.get("matchedFileNode"))
        file_node_count = int(state.get("fileNodeCount", 0) or 0)
        baseline_file_node_count = int(baseline.get("fileNodeCount", 0) or 0)
        allow_name_only = bool(self._file_paste_config.get("allow_name_only_signal", False))

        if not baseline:
            return (
                file_count > 0
                or matched_file_node
                or file_node_count > 0
                or (allow_name_only and matched_name)
            )

        return (
            file_count > baseline_file_count
            or (matched_file_node and not bool(baseline.get("matchedFileNode")))
            or file_node_count > baseline_file_node_count
            or (allow_name_only and matched_name and not bool(baseline.get("matchedName")))
        )

    def _wait_for_upload_signal(self, filepath: str, timeout: float = None, baseline: dict = None) -> dict:
        """
        Wait for page-level evidence that a file was actually attached.

        Returns structured state so callers can tell whether the page showed
        a confirmed upload, only weak activity, or no signal at all.
        """
        timeout = self.get_upload_signal_timeout(2.5) if timeout is None else timeout
        grace_timeout = self.get_upload_signal_grace(3.0)
        deadline = time.time() + max(0.2, timeout)
        extended_deadline = deadline
        last_state = baseline or self._probe_upload_signal(filepath)
        saw_weak_signal = False
        wait_result = {
            "confirmed": False,
            "weak_signal_seen": False,
            "last_state": dict(last_state or {}),
        }

        while time.time() < extended_deadline:
            if self._check_cancelled():
                wait_result["last_state"] = dict(last_state or {})
                self._last_upload_signal_wait = wait_result
                return wait_result

            state = self._probe_upload_signal(filepath)
            last_state = state
            wait_result["last_state"] = dict(last_state or {})
            if self._has_upload_signal(state, baseline):
                wait_result["confirmed"] = True
                wait_result["weak_signal_seen"] = saw_weak_signal
                self._last_upload_signal_wait = wait_result
                logger.debug(
                    "[FILE_PASTE] detected upload signal "
                    f"(file_count={state.get('fileCount', 0)}, "
                    f"matched_name={bool(state.get('matchedName'))}, "
                    f"matched_file_node={bool(state.get('matchedFileNode'))}, "
                    f"file_node_count={state.get('fileNodeCount', 0)})"
                )
                return wait_result

            weak_signal = (
                bool(state.get("matchedName"))
                or int(state.get("fileNodeCount", 0) or 0) > 0
                or int(state.get("pendingCount", 0) or 0) > 0
                or bool(state.get("pendingText"))
            )
            if weak_signal and not saw_weak_signal:
                saw_weak_signal = True
                wait_result["weak_signal_seen"] = True
                extended_deadline = max(extended_deadline, time.time() + max(0.5, grace_timeout))
                logger.debug(
                    "[FILE_PASTE] detected weak upload signal, waiting for stronger confirmation "
                    f"(matched_name={bool(state.get('matchedName'))}, "
                    f"file_node_count={state.get('fileNodeCount', 0)}, "
                    f"pending={state.get('pendingCount', 0)}, "
                    f"pending_text={bool(state.get('pendingText'))})"
                )

            time.sleep(0.2)

        logger.warning(
            "[FILE_PASTE] no confirmed upload signal detected: "
            f"{last_state.get('filename', filepath)} "
            f"(matched_name={bool(last_state.get('matchedName'))}, "
            f"matched_file_node={bool(last_state.get('matchedFileNode'))}, "
            f"file_node_count={last_state.get('fileNodeCount', 0)}, "
            f"pending={last_state.get('pendingCount', 0)}, "
            f"pending_text={bool(last_state.get('pendingText'))})"
        )
        wait_result["weak_signal_seen"] = saw_weak_signal
        wait_result["last_state"] = dict(last_state or {})
        self._last_upload_signal_wait = wait_result
        return wait_result

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
            self._smart_delay(0.06, 0.12)
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
            return list(self.tab.eles('css:input[type="file"]', timeout=0.8) or [])
        except Exception as e:
            logger.debug(f"[FILE_PASTE] 查找通用 file input 失败: {e}")
            return []

    def _upload_file_via_input(self, filepath: str, selector: str = "") -> bool:
        """Use file input elements to attach the temporary file directly."""
        candidates = self._list_file_inputs(selector)
        if not candidates:
            logger.debug("[FILE_PASTE] no usable file input is available on this page")
            return False

        for index, file_input in enumerate(candidates, 1):
            try:
                if file_input.attr("disabled") is not None:
                    continue

                baseline = self._probe_upload_signal(filepath)
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
                    wait_state = self._wait_for_upload_signal(filepath, timeout=1.2, baseline=baseline)
                    if wait_state.get("confirmed"):
                        logger.debug(
                            f"[FILE_PASTE] file input #{index} triggered a confirmed attachment signal "
                            f"(selector={selector or 'input[type=file]'})"
                        )
                        return True

                    if wait_state.get("weak_signal_seen"):
                        logger.warning(
                            f"[FILE_PASTE] file input #{index} produced only a weak signal; "
                            "clipboard fallback will be skipped for this attempt"
                        )

                    logger.debug(
                        f"[FILE_PASTE] file input #{index} did not keep a selected file "
                        f"(selector={selector or 'input[type=file]'})"
                    )
                    continue

                logger.debug(
                    f"[FILE_PASTE] uploaded file via file input "
                    f"(candidate={index}, files={selected_count})"
                )
                return True
            except Exception as e:
                logger.debug(f"[FILE_PASTE] file input #{index} upload failed: {e}")

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

        return False
    
    def _should_use_file_paste(self, text: str) -> bool:
        """判断是否应该使用文件粘贴模式"""
        if not self._file_paste_config.get("enabled", False):
            return False
        
        threshold = self._file_paste_config.get("threshold", 50000)
        return len(text) > threshold
    
    def _fill_via_file_paste(self, ele, text: str) -> bool:
        """
        Upload large text by turning it into a temporary txt attachment.

        Flow:
        1. Create a temporary txt file with the prompt content.
        2. Prefer site-native upload entry points and file inputs.
        3. Only fall back to clipboard file paste when there was no ambiguous upload activity.
        """
        from app.core.tab_pool import get_clipboard_lock

        threshold = self._file_paste_config.get("threshold", 50000)
        logger.info(
            f"[FILE_PASTE] text length {len(text)} exceeds threshold {threshold}, using file-paste mode"
        )

        clipboard_lock = get_clipboard_lock()
        self._recent_file_upload_at = 0.0
        self._last_upload_signal_wait = {
            "confirmed": False,
            "weak_signal_seen": False,
            "last_state": {},
        }

        try:
            ele.click()
            self._smart_delay(0.15, 0.35)

            if not self.ensure_input_focus(ele):
                raise WorkflowError("input_focus_failed")

            if self._check_cancelled():
                return False

            if self.stealth_mode:
                self._human_key_combo('Control', 'A')
                self._smart_delay(0.03, 0.08)
            else:
                self.tab.actions.key_down('Control').key_down('A').key_up('A').key_up('Control')
                time.sleep(0.1)

            if self._check_cancelled():
                return False

            filepath = create_temp_txt(text)
            if not filepath:
                logger.error("[FILE_PASTE] failed to create temp file")
                return False

            logger.debug(f"[FILE_PASTE] temp file: {filepath}")
            self._last_file_upload_path = filepath
            expected_names = [
                os.path.basename(filepath),
                os.path.splitext(os.path.basename(filepath))[0],
            ]
            if self._attachment_monitor is not None:
                self._attachment_monitor.begin_tracking(expected_names=expected_names)
            upload_baseline = self._probe_upload_signal(filepath)

            uploaded = self._upload_file_via_site_targets(filepath)

            if not uploaded:
                ambiguous_input_signal = (
                    bool(self._last_upload_signal_wait.get("weak_signal_seen"))
                    and not bool(self._last_upload_signal_wait.get("confirmed"))
                )
                if ambiguous_input_signal:
                    logger.warning(
                        "[FILE_PASTE] ambiguous file-input signal detected; skip clipboard fallback to avoid duplicate attachments"
                    )
                else:
                    with clipboard_lock:
                        if not copy_file_to_clipboard(filepath):
                            logger.error("[FILE_PASTE] copy file to clipboard failed")
                            return False

                        time.sleep(random.uniform(0.08, 0.15))

                        if self.stealth_mode:
                            self._human_key_combo('Control', 'V')
                        else:
                            self.tab.actions.key_down('Control').key_down('V').key_up('V').key_up('Control')

            if self._check_cancelled():
                return True

            if self._attachment_monitor is not None:
                if self._has_confirmed_upload_signal():
                    logger.debug(
                        "[FILE_PASTE] 已拿到强上传信号，跳过 attachment_monitor 的长等待，直接进入补文本阶段"
                    )
                else:
                    upload_timeout = self.get_upload_signal_timeout(
                        getattr(BrowserConstants, "ATTACHMENT_READY_MAX_WAIT", 20.0)
                    )
                    upload_grace = self.get_upload_signal_grace(4.0)
                    check_interval = getattr(BrowserConstants, "ATTACHMENT_READY_CHECK_INTERVAL", 0.25)
                    stable_window = getattr(BrowserConstants, "ATTACHMENT_READY_STABLE_WINDOW", 0.8)
                    attachment_state = self._attachment_monitor.wait_until_ready(
                        expected_names=expected_names,
                        require_observed=True,
                        require_send_enabled=False,
                        accept_existing=False,
                        start_new_tracking=False,
                        max_wait=max(upload_timeout, 0.5) + max(upload_grace, 0.0),
                        poll_interval=check_interval,
                        stable_window=stable_window,
                        label="file-paste",
                    )
                    if not attachment_state.get("success"):
                        if attachment_state.get("activitySeen") or attachment_state.get("attachmentObserved"):
                            logger.error(
                                "[FILE_PASTE] Attachment activity was observed but never confirmed; aborting text fallback to avoid duplicate send"
                            )
                            raise WorkflowError("file_paste_upload_unconfirmed")
                        logger.warning("[FILE_PASTE] file upload did not take effect; giving up file-paste mode")
                        return False
            else:
                time.sleep(random.uniform(0.5, 1.0))
                self._smart_delay(0.3, 0.6)
                wait_state = self._wait_for_upload_signal(filepath, baseline=upload_baseline)
                if not wait_state.get("confirmed"):
                    logger.warning("[FILE_PASTE] file upload did not take effect; giving up file-paste mode")
                    return False
            self._recent_file_upload_at = time.time()

            settle_seconds = self.get_post_upload_settle_seconds(0.0)
            if settle_seconds > 0:
                time.sleep(settle_seconds)

            hint_ele = self._reacquire_input_after_upload(fallback_ele=ele)

            hint_text = self._file_paste_config.get("hint_text", "完全专注于文件内容")
            if hint_text:
                logger.debug(
                    f"[FILE_PASTE] 进入补文本阶段: reacquire={self._should_reacquire_input_after_upload()}, "
                    f"hint_target_tag={getattr(hint_ele, 'tag', '') or 'unknown'}"
                )
                logger.debug(f"[FILE_PASTE] appending hint text: {hint_text}")
                self._append_file_paste_hint(hint_ele, hint_text)

            logger.info(f"[FILE_PASTE] file paste completed ({len(text)} chars)")
            return True

        except Exception as e:
            logger.error(f"[FILE_PASTE] file paste failed: {e}")
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
        
        settle_min = float(BrowserConstants.get('STEALTH_PASTE_SETTLE_MIN') or 0.12)
        settle_max = float(BrowserConstants.get('STEALTH_PASTE_SETTLE_MAX') or 0.25)
        skip_verify = bool(BrowserConstants.get('STEALTH_SKIP_PASTE_VERIFY'))
        
        try:
            if self._check_cancelled():
                return

            if not self.ensure_input_focus(ele, log_failure=False):
                self.physical_activate(ele)
                time.sleep(0.08)
                self.focus_to_end(ele)
                time.sleep(0.05)
                if not self.ensure_input_focus(ele):
                    raise WorkflowError("clipboard_focus_failed")
            
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
                time.sleep(random.uniform(0.02, 0.06))
                
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
            self._smart_delay(0.06, 0.14)
            
            if self._check_cancelled():
                return
            
            if not self.verify_paste_result_minimal(ele, text):
                raise WorkflowError("clipboard_paste_failed")

            if not skip_verify:
                self._stealth_verify_paste_light(ele, text)
            else:
                logger.debug_throttled(
                    "stealth.skip_paste_verify",
                    "[STEALTH] 跳过粘贴验证",
                    interval_sec=15.0,
                )
        
        except WorkflowError:
            raise
        except Exception as e:
            logger.error(f"[STEALTH] 剪贴板粘贴失败: {e}，停止本次隐身输入")
            raise WorkflowError("clipboard_paste_failed") from e
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
        
        settle_min = float(BrowserConstants.get('STEALTH_PASTE_SETTLE_MIN') or 0.12)
        settle_max = float(BrowserConstants.get('STEALTH_PASTE_SETTLE_MAX') or 0.25)
        skip_verify = bool(BrowserConstants.get('STEALTH_SKIP_PASTE_VERIFY'))
    
        try:
            # 1. 聚焦输入框（原生点击）
            ele.click()
            self._smart_delay(0.06, 0.12)

            if not self.ensure_input_focus(ele, log_failure=False):
                self.physical_activate(ele)
                time.sleep(0.08)
                self.focus_to_end(ele)
                time.sleep(0.05)
                if not self.ensure_input_focus(ele):
                    raise WorkflowError("clipboard_focus_failed")
        
            if self._check_cancelled():
                return
        
            # 2. 全选（人类化时序）—— 粘贴会自动覆盖选中内容，无需 Delete
            self._human_key_combo('Control', 'A')
            self._smart_delay(0.03, 0.08)
        
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
                time.sleep(random.uniform(0.02, 0.06))
            
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
            self._smart_delay(0.06, 0.14)
        
            if self._check_cancelled():
                return
        
            if not self.verify_paste_result_minimal(ele, text):
                raise WorkflowError("clipboard_paste_failed")

            # 5. 验证（可配置跳过，默认跳过以避免 JS 注入）
            if not skip_verify:
                self._stealth_verify_paste_light(ele, text)
            else:
                logger.debug_throttled(
                    "stealth.skip_paste_verify",
                    "[STEALTH] 跳过粘贴验证（STEALTH_SKIP_PASTE_VERIFY=true）",
                    interval_sec=15.0,
                )
    
        except WorkflowError:
            raise
        except Exception as e:
            logger.error(f"[STEALTH] 剪贴板粘贴失败: {e}，停止本次隐身输入")
            raise WorkflowError("clipboard_paste_failed") from e
    
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
