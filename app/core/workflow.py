"""
app/core/workflow.py - 工作流执行和输入处理（v5.5 隐身模式增强版）

职责：
- 工作流步骤执行
- 输入框填充（隐身模式使用剪贴板，普通模式使用JS）
- 点击、等待等基础操作
- 与 StreamMonitor 协同

修改说明（v5.5）：
- 【隐身模式增强】剪贴板 + Ctrl+V 输入（isTrusted=true）
- 【行为随机化】正态分布延迟 + 偶发停顿
- 【模式隔离】隐身/普通模式完全独立逻辑
- 保持原有校验和分块逻辑

依赖：
- app.core.config
- app.core.elements
- app.core.stream_monitor
"""

import os
import re
import time
import json
import random
import base64
from typing import Generator, Optional, Dict, Any, Callable, List
from app.core.tab_pool import get_clipboard_lock

from app.core.config import (
    logger,
    BrowserConstants,
    SSEFormatter,
    ElementNotFoundError,
    WorkflowError,
)
from app.core.elements import ElementFinder
from app.core.stream_monitor import StreamMonitor


# ================= 常量配置 =================

# 分块阈值：30000字符以下直接一次写入，超过则分块
CHUNK_SIZE_THRESHOLD = 30000


# ================= 工作流执行器 =================

class WorkflowExecutor:
    """工作流执行器"""
    
    def __init__(self, tab, stealth_mode: bool = False, 
                 should_stop_checker: Callable[[], bool] = None,
                 extractor = None,
                 image_config: Dict = None,
                 stream_config: Dict = None):  # 🆕 新增
        self.tab = tab
        self.stealth_mode = stealth_mode
        self.finder = ElementFinder(tab)
        self.formatter = SSEFormatter()
        
        self._should_stop = should_stop_checker or (lambda: False)
        self._extractor = extractor
        self._image_config = image_config or {}  
        self._stream_config = stream_config or {}        
        # 传递 image_config 和 stream_config 到 StreamMonitor
        self._stream_monitor = StreamMonitor(
            tab=tab,
            finder=self.finder,
            formatter=self.formatter,
            stop_checker=should_stop_checker,
            extractor=extractor,
            image_config=image_config,
            stream_config=stream_config  # 🆕 新增
        )
        
        self._completion_id = SSEFormatter._generate_id()
        
        if extractor:
            logger.debug(f"WorkflowExecutor 使用提取器: {extractor.get_id()}")
        
        if self._image_config.get("enabled"):  # 🆕
            logger.info(f"[IMAGE] 图片提取已启用")
        
        if self.stealth_mode:
            logger.info("[STEALTH] 隐身模式已启用")

    # ================= 规范化方法 =================

    def _normalize_for_compare(self, text: str) -> str:
        """规范化文本用于比对（处理富文本编辑器的换行差异）"""
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()
        return text

    def _is_contenteditable(self, ele) -> bool:
        """检测元素是否为 contenteditable"""
        try:
            return bool(ele.run_js("""
                return !!(this.isContentEditable || this.getAttribute('contenteditable') === 'true')
            """))
        except Exception:
            return False

    # ================= 调试辅助方法 =================

    def _debug_read_input_sample(self, ele, head: int = 80, tail: int = 80) -> dict:
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

    def _get_input_len(self, ele) -> int:
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

    def _read_input_full_text(self, ele) -> str:
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

    def _first_mismatch(self, a: str, b: str) -> int:
        """找到第一个不匹配的位置，完全相同返回 -1"""
        n = min(len(a), len(b))
        for i in range(n):
            if a[i] != b[i]:
                return i
        return n if len(a) != len(b) else -1

    def _get_input_stats(self, ele) -> tuple:
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

    # ================= 原子输入方法（普通模式用）=================

    def _set_input_atomic(self, ele, text: str, mode: str = "append") -> bool:
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

    def _append_chunk_via_js(self, ele, chunk: str) -> bool:
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

    # ================= 控制方法 =================

    def _check_cancelled(self) -> bool:
        """检查是否被取消"""
        return self._should_stop()
    
    def _smart_delay(self, min_sec: float = None, max_sec: float = None):
        """
        智能延迟（v5.5 增强版）
        
        改进：
        - 正态分布（更像人类）
        - 10% 概率额外停顿（模拟走神）
        - 可被取消中断
        """
        if not self.stealth_mode:
            return
        
        min_sec = min_sec or BrowserConstants.STEALTH_DELAY_MIN
        max_sec = max_sec or BrowserConstants.STEALTH_DELAY_MAX
        
        # 正态分布参数
        mean = (min_sec + max_sec) / 2
        std = (max_sec - min_sec) / 4
        
        # 生成延迟时间
        total_delay = random.gauss(mean, std)
        
        # 限制范围
        total_delay = max(min_sec, min(total_delay, max_sec))
        
        # 10% 概率"走神"（额外停顿）
        pause_prob = getattr(BrowserConstants, 'STEALTH_PAUSE_PROBABILITY', 0.1)
        pause_max = getattr(BrowserConstants, 'STEALTH_PAUSE_EXTRA_MAX', 0.8)
        
        if random.random() < pause_prob:
            extra = random.uniform(0.2, pause_max)
            total_delay = min(total_delay + extra, 1.0)  # 不超过 1s
            logger.debug(f"[STEALTH] 随机停顿 +{extra:.2f}s")
        
        # 可中断的等待
        elapsed = 0
        step = 0.05
        
        while elapsed < total_delay:
            if self._check_cancelled():
                return
            time.sleep(min(step, total_delay - elapsed))
            elapsed += step

    # ================= 步骤执行 =================

    def execute_step(self, action: str, selector: str,
                     target_key: str, value: str = None,
                     optional: bool = False,
                     context: Dict = None) -> Generator[str, None, None]:
        """执行单个步骤"""
        
        if self._check_cancelled():
            logger.debug(f"步骤 {action} 跳过（已取消）")
            return
        
        logger.debug(f"执行: {action} -> {target_key}")
        # 🆕 保存 context 供 _execute_fill 使用
        self._context = context
        
        try:
            if action == "WAIT":
                wait_time = float(value or 0.5)
                elapsed = 0
                while elapsed < wait_time:
                    if self._check_cancelled():
                        return
                    time.sleep(min(0.1, wait_time - elapsed))
                    elapsed += 0.1
            
            elif action == "KEY_PRESS":
                self._execute_keypress(target_key or value)
            
            elif action == "CLICK":
                # 🆕 send_btn 使用“可靠发送”：等图片上传完成后确保真的发出去
                if target_key == "send_btn":
                    self._execute_click_send_reliably(
                        selector=selector,
                        target_key=target_key,
                        optional=optional,
                    )
                else:
                    self._execute_click(selector, target_key, optional)
            
            elif action == "FILL_INPUT":
                prompt = context.get("prompt", "") if context else ""
                self._execute_fill(selector, prompt, target_key, optional)
            
            elif action in ("STREAM_WAIT", "STREAM_OUTPUT"):
                user_input = context.get("prompt", "") if context else ""
                yield from self._stream_monitor.monitor(
                    selector=selector,
                    user_input=user_input,
                    completion_id=self._completion_id
                )
            
            else:
                logger.debug(f"未知动作: {action}")
        
        except ElementNotFoundError as e:
            if not optional:
                yield self.formatter.pack_error(f"元素未找到: {str(e)}")
                raise
        
        except Exception as e:
            logger.error(f"步骤执行失败 [{action}]: {e}")
            if not optional:
                yield self.formatter.pack_error(f"执行失败: {str(e)}")
                raise
    
    def _execute_keypress(self, key: str):
        """执行按键操作"""
        if self._check_cancelled():
            return
        self.tab.actions.key_down(key).key_up(key)
        self._smart_delay(0.1, 0.2)
    
    def _execute_click(self, selector: str, target_key: str, optional: bool):
        """
        执行点击操作（v5.5 增强版）
        
        改进：
        - 发送按钮前额外犹豫
        - 保持原有鼠标移动逻辑
        """
        if self._check_cancelled():
            return
        
        ele = self.finder.find_with_fallback(selector, target_key)
        
        if ele:
            try:
                # ===== 隐身模式增强 =====
                if self.stealth_mode:
                    # 发送按钮前额外犹豫（40% 概率）
                    if target_key == "send_btn" and random.random() < 0.4:
                        hesitate = random.uniform(0.4, 0.9)
                        logger.debug(f"[STEALTH] 发送前犹豫 {hesitate:.2f}s")
                        elapsed = 0
                        while elapsed < hesitate:
                            if self._check_cancelled():
                                return
                            time.sleep(0.05)
                            elapsed += 0.05
                    
                    # 鼠标移动（保留原有逻辑）
                    try:
                        self.tab.actions.move_to(ele)
                        self._smart_delay(0.1, 0.25)
                    except Exception:
                        pass
                
                if self._check_cancelled():
                    return
                
                # 原生点击（isTrusted=true）
                ele.click()
                self._smart_delay(
                    BrowserConstants.ACTION_DELAY_MIN,
                    BrowserConstants.ACTION_DELAY_MAX
                )
            
            except Exception as click_err:
                logger.debug(f"点击异常: {click_err}")
                if target_key == "send_btn":
                    self._execute_keypress("Enter")
        
        elif target_key == "send_btn":
            self._execute_keypress("Enter")
        
        elif not optional:
            raise ElementNotFoundError(f"点击目标未找到: {selector}")
    # ================= 可靠发送（图片上传场景）=================

    def _execute_click_send_reliably(self, selector: str, target_key: str, optional: bool):
        """
        可靠发送：专门解决“图片上传中导致 send 按钮点不了，消息没发出去”的问题。

        策略（不依赖站点定制选择器）：
        1) 先尝试点击一次
        2) 如果点击后输入框内容没有明显变化（仍然很长/仍然包含用户文本），判定未发送
        3) 在超时时间内循环：等待 -> 再点击
        """
        if self._check_cancelled():
            return

        # 发送重试窗口（秒）
        max_wait = getattr(BrowserConstants, "IMAGE_SEND_MAX_WAIT", 12.0)
        retry_interval = getattr(BrowserConstants, "IMAGE_SEND_RETRY_INTERVAL", 0.6)

        # 输入框 selector 在 sites.json 里叫 input_box
        input_selector = None
        try:
            # 尽量复用 finder 的 fallback 体系
            input_selector = None
        except Exception:
            pass

        # 点击前：记录输入框长度（用于判断是否真的发出去了）
        before_len = self._safe_get_input_len_by_key("input_box")

        # 第一次点击
        self._execute_click(selector, target_key, optional)

        # 快速判断一次（给页面反应时间）
        time.sleep(0.25)
        after_len = self._safe_get_input_len_by_key("input_box")

        if self._is_send_success(before_len, after_len):
            logger.info("发送成功")
            return

        # 没成功：进入重试窗口
        logger.warning(f"[SEND] 发送未成功，进入重试窗口 max_wait={max_wait}s (before_len={before_len}, after_len={after_len})")

        elapsed = 0.0
        while elapsed < max_wait:
            if self._check_cancelled():
                return

            # 等一会儿让图片上传完成
            step = min(retry_interval, max_wait - elapsed)
            time.sleep(step)
            elapsed += step

            # 重试点击
            self._execute_click(selector, target_key, optional)

            time.sleep(0.25)
            new_len = self._safe_get_input_len_by_key("input_box")

            if self._is_send_success(after_len, new_len) or self._is_send_success(before_len, new_len):
                logger.info(f"发送成功 (重试{elapsed:.1f}s)")
                return

            after_len = new_len

        # 超时仍未成功
        logger.error("[SEND] 发送重试超时：图片可能一直处于上传中或按钮被禁用")
        if not optional:
            raise WorkflowError("send_btn_click_failed_due_to_uploading")

    def _safe_get_input_len_by_key(self, target_key: str) -> int:
        """
        读取输入框当前长度（用配置 key 查找 selector）。
        这里不直接依赖外部 context，走 ElementFinder 的 fallback。
        """
        try:
            # ElementFinder.find_with_fallback 接收的是 selector 和 target_key
            # 但这里我们没有 selector，需要从页面上用 key 找——做不到。
            # 解决：用 tab 上常见方式尝试查找已缓存的输入框：优先用 finder 的最后一次命中缓存（如果 ElementFinder 有的话）。
            # 由于你没提供 ElementFinder 的实现，我们只能用一个“尽量稳”的办法：从当前页面按常用 key 再找一次需要 selector。
            #
            # 为了不猜测 ElementFinder 内部，我们退回到：直接用 tab.ele 查找当前 activeElement 的文本长度（通用）。
            ele = None
            try:
                ele = self.tab.run_js("return document.activeElement")
            except Exception:
                ele = None

            if ele:
                # 通过 JS 读取 activeElement 长度
                n = self.tab.run_js("""
                    try {
                        const el = arguments[0];
                        const tag = (el.tagName || '').toLowerCase();
                        if (tag === 'textarea' || tag === 'input') return (el.value || '').length;
                        if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') return (el.innerText || '').length;
                        return (el.textContent || '').length;
                    } catch(e){ return 0; }
                """, ele)
                return int(n) if n is not None else 0

            return 0
        except Exception:
            return 0

    def _is_send_success(self, before_len: int, after_len: int) -> bool:
        """
        判断是否发送成功：
        - 输入框明显变短/清空（典型表现：发送后输入框被清空）
        - 这里用“减少 60% 或清空”作为判定，避免富文本编辑器插入额外换行导致误判
        """
        try:
            if after_len == 0 and before_len > 0:
                return True
            if before_len <= 0:
                return False
            # 明显变短：减少 60%
            if after_len <= int(before_len * 0.4):
                return True
            return False
        except Exception:
            return False
    # ================= 输入框填充（核心修改区域）=================

    def _execute_fill(self, selector: str, text: str, target_key: str, optional: bool):
        """
        填充输入框（v5.5 模式分离版）
        
        流程：
        - stealth_mode=True  → 使用剪贴板 + Ctrl+V（isTrusted=true）
        - stealth_mode=False → 使用 JS 方式（保持原有逻辑）
        """
        if self._check_cancelled():
            return

        if self.stealth_mode:
            # ===== 隐身模式：剪贴板粘贴 =====
            self._fill_via_clipboard(selector, text, target_key, optional)
        else:
            # ===== 普通模式：JS 方式 =====
            self._fill_via_js(selector, text, target_key, optional)
        # 🆕 填充完文本后，粘贴图片
        if hasattr(self, '_context') and self._context:
            images = self._context.get('images', [])
            if images:
                self._paste_images(selector, images, target_key, optional)

    def _fill_via_clipboard(self, selector: str, text: str, target_key: str, optional: bool):
        """隐身模式专用：剪贴板 + Ctrl+V 输入"""
        logger.debug(f"[STEALTH] 使用剪贴板粘贴，长度 {len(text)}")
    
        ele = self.finder.find_with_fallback(selector, target_key)
        if not ele:
            if not optional:
                raise ElementNotFoundError("找不到输入框")
            return
    
        try:
            import pyperclip
        except ImportError:
            logger.error("[STEALTH] pyperclip 未安装，降级到 JS 方式")
            self._fill_via_js(selector, text, target_key, optional)
            return
    
        # 🔒 获取剪贴板锁，确保多任务并发时不会互相干扰
        clipboard_lock = get_clipboard_lock()
    
        try:
            # 聚焦输入框（在锁外执行，减少锁持有时间）
            ele.click()
            self._smart_delay(0.1, 0.3)
        
            if self._check_cancelled():
                return
        
            # 全选 + 删除
            self.tab.actions.key_down('Control').key_down('A').key_up('A').key_up('Control')
            time.sleep(0.05)
            self.tab.actions.key_down('Delete').key_up('Delete')
            self._smart_delay(0.1, 0.2)
        
            if self._check_cancelled():
                return
        
            # 🔒 剪贴板操作加锁
            with clipboard_lock:
                # 备份原剪贴板
                original_clipboard = ""
                try:
                    original_clipboard = pyperclip.paste()
                except Exception:
                    pass
            
                # 写入剪贴板
                pyperclip.copy(text)
                time.sleep(0.05)
            
                # Ctrl+V 粘贴
                logger.debug("[STEALTH] 执行 Ctrl+V (锁内)")
                self.tab.actions.key_down('Control').key_down('V').key_up('V').key_up('Control')
            
                # 等待粘贴完成
                time.sleep(0.3)
            
                # 恢复原剪贴板
                try:
                    pyperclip.copy(original_clipboard)
                except Exception:
                    pass
            # 🔓 锁释放
        
            # 额外等待 DOM 更新
            self._smart_delay(0.2, 0.4)
        
            if self._check_cancelled():
                return
        
            # 验证粘贴结果
            self._verify_clipboard_result(ele, text)
        
            # 调试日志
            sample = self._debug_read_input_sample(ele)
            logger.debug(
                f"[CLIPBOARD_OK] len={sample['len']} nl={sample['nl']} "
                f"head={repr(sample['head'][:40])}... tail=...{repr(sample['tail'][-40:])}"
            )
    
        except Exception as e:
            logger.error(f"[STEALTH] 剪贴板粘贴失败: {e}，降级到 JS 方式")
            self._fill_via_js(selector, text, target_key, optional)

    def _verify_clipboard_result(self, ele, expected_text: str):
        """
        验证剪贴板粘贴结果
        
        策略：
        1. 规范化比对（容忍换行差异）
        2. 失败则重试一次
        3. 再失败则抛出异常
        """
        expected_normalized = self._normalize_for_compare(expected_text)
        expected_core = re.sub(r'\s+', '', expected_text)
        is_rich_editor = self._is_contenteditable(ele)
        
        # 第一次检查
        actual = self._read_input_full_text(ele)
        actual_normalized = self._normalize_for_compare(actual)
        
        # 精确匹配
        if actual_normalized == expected_normalized:
            return
        
        # 富文本编辑器宽松匹配
        if is_rich_editor:
            actual_core = re.sub(r'\s+', '', actual)
            if actual_core == expected_core:
                diff = len(actual) - len(expected_text)
                logger.info(f"[CLIPBOARD_OK] 富文本匹配成功 (diff={diff:+d})")
                return
        
        # 失败：尝试重试
        logger.warning(
            f"[CLIPBOARD_RETRY] 粘贴不完整 "
            f"(actual={len(actual)}, expected={len(expected_text)})"
        )
        
        try:
            import pyperclip
            
            # 清空
            ele.click()
            time.sleep(0.05)
            self.tab.actions.key_down('Control').key_down('A').key_up('A').key_up('Control')
            time.sleep(0.05)
            self.tab.actions.key_down('Delete').key_up('Delete')
            time.sleep(0.1)
            
            # 再次粘贴
            pyperclip.copy(expected_text)
            time.sleep(0.05)
            self.tab.actions.key_down('Control').key_down('V').key_up('V').key_up('Control')
            time.sleep(0.5)
            
            # 最终验证
            actual = self._read_input_full_text(ele)
            actual_normalized = self._normalize_for_compare(actual)
            
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

    def _fill_via_js(self, selector: str, text: str, target_key: str, optional: bool):
        """
        普通模式专用：原有 JS 填充逻辑（完全不修改）
        """
        ele = self.finder.find_with_fallback(selector, target_key)
        if not ele:
            if not optional:
                raise ElementNotFoundError("找不到输入框")
            return

        # === 以下是原有逻辑，完全保持不变 ===
        self._clear_input_safely(ele)
        
        # 分块写入（使用统一的阈值常量）
        success = self._chunked_input(ele, text, chunk_size=CHUNK_SIZE_THRESHOLD)

        if not success:
            logger.debug("JS 分块输入遇到问题，准备进行后续修正...")

        self._smart_delay(0.2, 0.4)

        # 物理激活（绕过 isTrusted 检测）
        self._physical_activate(ele)

        # 校验并修正
        self._verify_and_fix(ele, text)
        
        # 调试日志
        sample = self._debug_read_input_sample(ele)
        logger.debug(
            f"[INPUT_SNAPSHOT] len={sample['len']} nl={sample['nl']} "
            f"head={repr(sample['head'][:40])}... tail=...{repr(sample['tail'][-40:])}"
        )

    def _chunked_input(self, ele, text: str, chunk_size: int = CHUNK_SIZE_THRESHOLD) -> bool:
        """分块写入逻辑（普通模式用）"""
        total_len = len(text)
        
        # 情况1：短文本直接一次写入
        if total_len <= chunk_size:
            logger.debug(f"[CHUNKED_INPUT] 短文本模式: {total_len} 字符，直接写入")
            return self._set_input_atomic(ele, text, mode="overwrite")

        # 情况2：长文本分块写入
        logger.debug(f"[CHUNKED_INPUT] 长文本模式: {total_len} 字符，分块大小 {chunk_size}")
        
        # 首块：覆盖写入
        first_chunk = text[:chunk_size]
        if not self._set_input_atomic(ele, first_chunk, mode="overwrite"):
            logger.debug("[CHUNKED_INPUT] 首块写入失败")
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
            
            if not self._set_input_atomic(ele, chunk, mode="append"):
                logger.warning(f"[CHUNKED_INPUT] 第 {chunk_index} 块追加失败: {current_pos}-{end_pos}")
                if not self._append_chunk_via_js(ele, chunk):
                    logger.error(f"[CHUNKED_INPUT] 备用方案也失败")
                    return False
            
            logger.debug(f"[CHUNKED_INPUT] 第 {chunk_index} 块完成: {current_pos}-{end_pos}")
            current_pos = end_pos
            chunk_index += 1
            time.sleep(0.08)
        
        logger.info(f"[CHUNKED_INPUT] 全部完成: {chunk_index} 块，共 {total_len} 字符")
        return True

    def _physical_activate(self, ele):
        """物理激活输入框（绕过 isTrusted 检测）"""
        try:
            ele.run_js("this.focus && this.focus()")
            
            is_ce = self._is_contenteditable(ele)
            
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

    def _verify_and_fix(self, ele, original_text: str):
        """校验并修正输入内容（普通模式用）"""
        expected = original_text.replace('\r\n', '\n').replace('\r', '\n')
        expected_len = len(expected)
        expected_normalized = self._normalize_for_compare(expected)
        expected_core = re.sub(r'\s+', '', expected)
        
        is_rich_editor = self._is_contenteditable(ele)

        for attempt in range(3):
            actual = self._read_input_full_text(ele)

            # 检查1：精确匹配
            if actual == expected:
                logger.info(f"[VERIFY_OK] attempt={attempt} len={len(actual)} (exact match)")
                return

            # 检查2：规范化匹配
            actual_normalized = self._normalize_for_compare(actual)
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
            mismatch_pos = self._first_mismatch(actual_normalized, expected_normalized)
            
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
            self._clear_input_safely(ele)
            time.sleep(0.05)
            
            ok = self._set_input_atomic(ele, expected, mode="overwrite")
            if not ok:
                logger.debug(f"[VERIFY] attempt={attempt} 原子写入返回 False，尝试备用方案")
                self._fill_via_js_backup(ele, expected)
            
            time.sleep(0.15)

        # 最终检查
        final_actual = self._read_input_full_text(ele)
        final_normalized = self._normalize_for_compare(final_actual)
        
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

    def _fill_via_js_backup(self, ele, text: str) -> bool:
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

    def _clear_input_safely(self, ele):
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

    def _focus_to_end(self, ele):
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
    # ================= 图片粘贴（v6.0 新增）=================

    def _paste_images(self, selector: str, image_paths: List[str], 
                      target_key: str, optional: bool):
        """
        粘贴多张图片到输入框
        
        策略：
        - 逐张复制到剪贴板 → Ctrl+V
        - 等待上传完成后再粘贴下一张
        """
        if not image_paths:
            return
        
        logger.info(f"[IMAGE] 开始粘贴 {len(image_paths)} 张图片")
        
        ele = self.finder.find_with_fallback(selector, target_key)
        if not ele:
            if not optional:
                raise ElementNotFoundError("找不到输入框，无法粘贴图片")
            logger.warning("[IMAGE] 输入框未找到，跳过图片粘贴")
            return
        
        for idx, img_path in enumerate(image_paths):
            if self._check_cancelled():
                logger.info("[IMAGE] 图片粘贴被取消")
                return
            
            logger.debug(f"[IMAGE] 粘贴第 {idx + 1}/{len(image_paths)} 张: {img_path}")
            
            success = self._paste_single_image(ele, img_path, idx + 1)
            
            if not success:
                logger.warning(f"[IMAGE] 第 {idx + 1} 张粘贴失败，继续下一张")
            
            # 图片间隔（给网站时间处理上传）
            if idx < len(image_paths) - 1:
                self._smart_delay(0.5, 1.0)
        
        logger.info(f"[IMAGE] 图片粘贴完成")

    def _paste_single_image(self, ele, image_path: str, index: int) -> bool:
        """
        粘贴单张图片
        
        流程：
        1. 复制图片到剪贴板
        2. 聚焦输入框
        3. Ctrl+V 粘贴
        4. 等待上传完成
        """
        from app.utils.image_handler import copy_image_to_clipboard
        from app.core.tab_pool import get_clipboard_lock
        
        # 🔒 获取剪贴板锁（防止并发任务冲突）
        clipboard_lock = get_clipboard_lock()
        
        try:
            # 聚焦输入框
            ele.click()
            self._smart_delay(0.1, 0.2)
            
            if self._check_cancelled():
                return False
            
            # 🔒 剪贴板操作加锁
            with clipboard_lock:
                # 复制图片到剪贴板
                if not copy_image_to_clipboard(image_path):
                    logger.error(f"[IMAGE] 复制到剪贴板失败: {image_path}")
                    return False
                
                # 等待剪贴板数据就绪
                time.sleep(0.1)
                
                # Ctrl+V 粘贴
                logger.debug(f"[IMAGE] 执行 Ctrl+V (图片 {index})")
                self.tab.actions.key_down('Control').key_down('V').key_up('V').key_up('Control')
                
                # 等待粘贴完成
                time.sleep(0.3)
            # 🔓 锁释放
            
            # 额外等待网站处理上传（观察网络请求/DOM 变化）
            upload_wait = 0.8 if self.stealth_mode else 0.5
            elapsed = 0
            step = 0.1
            while elapsed < upload_wait:
                if self._check_cancelled():
                    return False
                time.sleep(step)
                elapsed += step
            
            logger.debug(f"[IMAGE] 第 {index} 张粘贴完成")
            return True
        
        except Exception as e:
            logger.error(f"[IMAGE] 粘贴异常: {e}")
            return False

__all__ = [
    'WorkflowExecutor',
]