"""
app/utils/paste.py - 通用粘贴工具

解决问题：
- React/Vue 受控输入框直接改 value 不触发状态更新
- contenteditable 编辑器需要模拟用户输入
- 超长文本需要分块避免卡顿/被截断
"""

import time
import random
import json
from typing import Optional

# 通用 JS 函数（挂到 window 上避免作用域问题）
UNIVERSAL_INSERT_JS = r"""
window.universalAppend = function (element, text) {
    if (!element) return false;

    // 1) 聚焦（尽量不滚动）
    try { element.focus({preventScroll: true}); } catch(e) { element.focus(); }

    const isInput = (element.tagName === 'TEXTAREA' || element.tagName === 'INPUT');

    // 2) input/textarea：用 setRangeText 最稳
    if (isInput) {
        const len = element.value.length;
        element.setSelectionRange(len, len);

        // setRangeText 会更新 value，并能更像"真实编辑"
        if (typeof element.setRangeText === 'function') {
            element.setRangeText(text, len, len, 'end');
        } else {
            element.value += text;
        }

        // 触发事件（React 受控输入常吃这个）
        try {
            element.dispatchEvent(new InputEvent('input', {bubbles: true, data: text, inputType: 'insertText'}));
        } catch(e) {
            element.dispatchEvent(new Event('input', {bubbles: true}));
        }
        return true;
    }

    // 3) contenteditable：先把光标折到末尾
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(element);
    range.collapse(false);
    selection.removeAllRanges();
    selection.addRange(range);

    // 4) 优先 execCommand（很多富文本编辑器对它兼容最好）
    let ok = false;
    try { ok = document.execCommand('insertText', false, text); } catch(e) {}

    // 5) 兜底：直接插入 TextNode + 触发 input
    if (!ok) {
        range.insertNode(document.createTextNode(text));
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);

        try {
            element.dispatchEvent(new InputEvent('input', {bubbles: true, data: text, inputType: 'insertText'}));
        } catch(e) {
            element.dispatchEvent(new Event('input', {bubbles: true}));
        }
    }
    return true;
};

window.getElementTextLength = function(element) {
    if (!element) return 0;
    if (element.tagName === 'TEXTAREA' || element.tagName === 'INPUT') {
        return (element.value || '').length;
    }
    return (element.innerText || element.textContent || '').length;
};
"""


def safe_universal_paste(page, selector: str, text_content: str, 
                         chunk_size: int = 2000,
                         min_delay: float = 0.3,
                         max_delay: float = 0.8,
                         max_retries: int = 2) -> bool:
    """
    通用粘贴函数：无论目标是 textarea 还是 contenteditable div，都能正确追加文本
    
    :param page: DrissionPage 对象
    :param selector: 输入框的选择器
    :param text_content: 要发送的完整文本
    :param chunk_size: 分块大小（字符数）
    :param min_delay: 每块之间的最小延时（秒）
    :param max_delay: 每块之间的最大延时（秒）
    :param max_retries: 每块写入失败时的最大重试次数
    :return: 是否成功
    """
    if not text_content:
        return True
    
    # 注入通用 JS 函数
    try:
        page.run_js(UNIVERSAL_INSERT_JS)
    except Exception as e:
        print(f"[Paste] 注入 JS 失败: {e}")
        return False
    
    # 获取元素对象
    ele = page.ele(selector)
    if not ele:
        print(f"[Paste] 找不到元素: {selector}")
        return False

    # 分块逻辑
    total_length = len(text_content)
    chunks = [text_content[i:i+chunk_size] for i in range(0, total_length, chunk_size)]
    
    print(f"[Paste] 开始粘贴，共 {len(chunks)} 块，总长度 {total_length}")

    total_sent = 0
    
    for i, chunk in enumerate(chunks):
        success = False
        
        for retry in range(max_retries + 1):
            try:
                # 获取写入前的长度
                before_len = page.run_js("return window.getElementTextLength(arguments[0])", ele)
                before_len = before_len or 0
                
                # 安全转义文本，防止 JS 报错
                safe_chunk = json.dumps(chunk)
                
                # 调用 JS 写入
                result = page.run_js(f"return window.universalAppend(arguments[0], {safe_chunk})", ele)
                
                # 短暂等待让页面更新
                time.sleep(0.05)
                
                # 获取写入后的长度
                after_len = page.run_js("return window.getElementTextLength(arguments[0])", ele)
                after_len = after_len or 0
                
                # 验证是否成功写入
                expected_len = before_len + len(chunk)
                actual_increase = after_len - before_len
                
                # 允许小幅误差（可能有换行符转换等）
                if actual_increase >= len(chunk) * 0.95:
                    success = True
                    total_sent += len(chunk)
                    print(f"[Paste] 第 {i+1}/{len(chunks)} 块成功，+{actual_increase} 字符")
                    break
                else:
                    print(f"[Paste] 第 {i+1} 块校验失败 (预期+{len(chunk)}，实际+{actual_increase})，重试 {retry+1}/{max_retries}")
                    
            except Exception as e:
                print(f"[Paste] 第 {i+1} 块写入异常: {e}，重试 {retry+1}/{max_retries}")
        
        if not success:
            print(f"[Paste] 第 {i+1} 块最终失败，已写入 {total_sent}/{total_length} 字符")
            return False
        
        # 随机延时：模拟人类分段复制粘贴的思考时间
        if i < len(chunks) - 1:  # 最后一块不需要延时
            time.sleep(random.uniform(min_delay, max_delay))

    print(f"[Paste] 粘贴完成，共 {total_sent} 字符")
    return True


def clear_and_paste(page, selector: str, text_content: str, **kwargs) -> bool:
    """
    清空输入框后粘贴（用于替换内容而非追加）
    """
    ele = page.ele(selector)
    if not ele:
        print(f"[Paste] 找不到元素: {selector}")
        return False
    
    # 清空内容
    try:
        page.run_js("""
            const ele = arguments[0];
            if (ele.tagName === 'TEXTAREA' || ele.tagName === 'INPUT') {
                ele.value = '';
            } else {
                ele.innerHTML = '';
            }
            ele.dispatchEvent(new Event('input', {bubbles: true}));
        """, ele)
        time.sleep(0.1)
    except Exception as e:
        print(f"[Paste] 清空失败: {e}")
    
    return safe_universal_paste(page, selector, text_content, **kwargs)


__all__ = ['safe_universal_paste', 'clear_and_paste', 'UNIVERSAL_INSERT_JS']