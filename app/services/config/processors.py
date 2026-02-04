"""
app/services/config/processors.py - 配置处理器

职责：
- HTML 清理
- 选择器验证
- AI 页面分析
"""

import json
import os
import re
import time
import logging
from typing import Dict, Optional
from urllib import request, error

import bs4
from bs4 import BeautifulSoup

from app.core.config import AppConfig

from .managers import GlobalConfigManager


logger = logging.getLogger('config_engine')


# ================= 常量 =================

# HTML 清理配置
MAX_HTML_CHARS = int(os.getenv("MAX_HTML_CHARS", "120000"))
TEXT_TRUNCATE_LENGTH = 80

# AI 请求配置
AI_MAX_RETRIES = 3
AI_RETRY_BASE_DELAY = 1.0
AI_RETRY_MAX_DELAY = 10.0
AI_REQUEST_TIMEOUT = 120

# 无效选择器语法模式
INVALID_SYNTAX_PATTERNS = [
    (r'~\s*\.\.', '~ .. 无效语法'),
    (r'\.\.\s*$', '结尾 .. 无效'),
    (r'>>\s', '>> 无效语法'),
    (r':has\(', ':has() 兼容性差'),
    (r'\s~\s*$', '结尾 ~ 无效'),
]


# ================= HTML 清理器 =================

class HTMLCleaner:
    """HTML 清理器"""
    
    TAGS_TO_REMOVE = [
        'script', 'style', 'meta', 'link', 'noscript',
        'img', 'video', 'audio', 'iframe', 'canvas',
        'path', 'rect', 'circle', 'polygon', 'defs', 'clipPath',
        'header', 'footer', 'nav', 'aside',
    ]
    
    ALLOWED_ATTRS = [
        'id', 'class', 'name', 'placeholder', 'aria-label', 'role',
        'data-testid', 'type', 'disabled', 'value', 'title', 'tabindex',
        'contenteditable', 'href'
    ]
    
    INTERACTIVE_TAGS = ['input', 'textarea', 'button', 'form', 'a']
    
    CORE_AREA_SELECTORS = [
        '[role="main"]',
        'main',
        '#app',
        '#root',
        '.chat',
        '.conversation',
        '.message',
    ]
    
    def __init__(self, max_chars: int = None, text_truncate: int = None):
        self.max_chars = max_chars or MAX_HTML_CHARS
        self.text_truncate = text_truncate or TEXT_TRUNCATE_LENGTH
    
    def clean(self, html: str) -> str:
        """深度清理 HTML"""
        logger.debug("开始 HTML 清理...")
        original_length = len(html)
        
        soup = BeautifulSoup(html, 'html.parser')
        
        interactive_elements = self._extract_interactive_elements(soup)
        
        for tag in soup(self.TAGS_TO_REMOVE):
            tag.decompose()
        
        for element in soup(text=lambda t: isinstance(t, bs4.element.Comment)):
            element.extract()
        
        for tag in soup.find_all(True):
            if tag.string and len(tag.string) > self.text_truncate:
                tag.string = tag.string[:self.text_truncate] + "..."
            
            attrs = dict(tag.attrs)
            for attr in attrs:
                if attr not in self.ALLOWED_ATTRS:
                    del tag.attrs[attr]
            
            if 'class' in tag.attrs and isinstance(tag.attrs['class'], list):
                tag.attrs['class'] = " ".join(tag.attrs['class'])
        
        clean_html = str(soup.body) if soup.body else str(soup)
        clean_html = re.sub(r'\s+', ' ', clean_html).strip()
        
        if len(clean_html) > self.max_chars:
            logger.warning(f"HTML 过长 ({len(clean_html)})，执行智能截断...")
            clean_html = self._smart_truncate(clean_html, interactive_elements)
        
        final_length = len(clean_html)
        reduction = 100 - (final_length / original_length * 100) if original_length > 0 else 0
        logger.info(f"HTML 清理完成: {original_length} → {final_length} 字符 (减少 {reduction:.1f}%)")
        
        return clean_html
    
    def _extract_interactive_elements(self, soup: BeautifulSoup) -> str:
        elements = []
        
        for tag_name in self.INTERACTIVE_TAGS:
            for element in soup.find_all(tag_name):
                context = self._get_element_with_context(element, levels=2)
                if context:
                    elements.append(context)
        
        unique_elements = list(dict.fromkeys(elements))
        return "\n".join(unique_elements)
    
    def _get_element_with_context(self, element, levels: int = 2) -> str:
        try:
            current = element
            for _ in range(levels):
                if current.parent and current.parent.name not in ['body', 'html', '[document]']:
                    current = current.parent
                else:
                    break
            
            html_str = str(current)
            if len(html_str) > 2000:
                html_str = html_str[:2000] + "..."
            return html_str
        except Exception:
            return str(element)
    
    def _smart_truncate(self, html: str, interactive_html: str) -> str:
        interactive_budget = int(self.max_chars * 0.3)
        if len(interactive_html) > interactive_budget:
            interactive_html = interactive_html[:interactive_budget]
        
        remaining_budget = self.max_chars - len(interactive_html) - 100
        
        if remaining_budget <= 0:
            logger.warning("交互元素已占满预算")
            return interactive_html
        
        core_html = self._extract_core_area(html)
        if core_html and len(core_html) <= remaining_budget:
            result = core_html + "\n<!-- INTERACTIVE ELEMENTS -->\n" + interactive_html
            return result[:self.max_chars]
        
        head_budget = remaining_budget // 3
        tail_budget = remaining_budget // 3
        
        head_part = self._truncate_at_tag_boundary(html[:head_budget * 2], head_budget, from_end=False)
        tail_part = self._truncate_at_tag_boundary(html[-tail_budget * 2:], tail_budget, from_end=True)
        
        result = (
            head_part +
            "\n<!-- TRUNCATED: MIDDLE SECTION -->\n" +
            "<!-- INTERACTIVE ELEMENTS START -->\n" +
            interactive_html +
            "\n<!-- INTERACTIVE ELEMENTS END -->\n" +
            tail_part
        )
        
        if len(result) > self.max_chars:
            result = result[:self.max_chars]
        
        return result
    
    def _extract_core_area(self, html: str) -> Optional[str]:
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            for selector in self.CORE_AREA_SELECTORS:
                try:
                    element = soup.select_one(selector)
                    if element:
                        core_html = str(element)
                        logger.debug(f"找到核心区域: {selector} ({len(core_html)} chars)")
                        return core_html
                except Exception:
                    continue
            
            return None
        except Exception as e:
            logger.debug(f"提取核心区域失败: {e}")
            return None
    
    def _truncate_at_tag_boundary(self, html: str, max_len: int, from_end: bool = False) -> str:
        if len(html) <= max_len:
            return html
        
        if from_end:
            start_pos = len(html) - max_len
            tag_start = html.find('<', start_pos)
            if tag_start != -1 and tag_start < len(html) - 100:
                return html[tag_start:]
            return html[-max_len:]
        else:
            tag_end = html.rfind('>', 0, max_len)
            if tag_end != -1 and tag_end > 100:
                return html[:tag_end + 1]
            return html[:max_len]


# ================= 选择器验证器 =================

class SelectorValidator:
    """选择器验证器"""
    
    def __init__(self, fallback_selectors: Dict[str, Optional[str]] = None):
        self.fallback_selectors = fallback_selectors or {}
    
    def validate(self, selectors: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
        """验证并修复选择器"""
        fixed = {}
        
        for key, selector in selectors.items():
            if selector is None:
                fixed[key] = self.fallback_selectors.get(key)
                continue
            
            is_invalid = False
            invalid_reason = ""
            
            for pattern, reason in INVALID_SYNTAX_PATTERNS:
                if re.search(pattern, selector):
                    is_invalid = True
                    invalid_reason = reason
                    break
            
            if is_invalid:
                logger.warning(f"❌ 无效选择器 [{key}]: {selector}")
                logger.warning(f"   原因: {invalid_reason}")
                
                repaired = self._try_repair(selector)
                if repaired:
                    logger.info(f"   ✅ 修复为: {repaired}")
                    fixed[key] = repaired
                else:
                    fallback = self.fallback_selectors.get(key)
                    logger.info(f"   🔄 回退为: {fallback}")
                    fixed[key] = fallback
            else:
                if re.search(r'\._[a-f0-9]{5,}|^\.[a-f0-9]{6,}', selector):
                    logger.info(f"ℹ️  哈希类名 [{key}]: {selector} (可能不稳定，但保留)")
                
                fixed[key] = selector
        
        return fixed
    
    def _try_repair(self, selector: str) -> Optional[str]:
        tag_match = re.match(r'^(\w+)', selector)
        if not tag_match:
            return None
        
        tag = tag_match.group(1)
        
        attr_patterns = [
            r'(\[name=["\']?\w+["\']?\])',
            r'(\[type=["\']?\w+["\']?\])',
            r'(\[role=["\']?\w+["\']?\])',
            r'(#[\w-]+)',
        ]
        
        for pattern in attr_patterns:
            match = re.search(pattern, selector)
            if match:
                return tag + match.group(1)
        
        return tag


# ================= AI 分析器 =================

class AIAnalyzer:
    """AI 页面分析器"""
    
    def __init__(self, global_config: GlobalConfigManager = None):
        self.api_key = AppConfig.get_helper_api_key()
        
        base_url = AppConfig.get_helper_base_url()
        self.base_url = base_url.rstrip('/') if base_url else "http://127.0.0.1:5104/v1"
        
        self.model = AppConfig.get_helper_model()
        self.global_config = global_config
        
        if not self.api_key:
            logger.warning("⚠️  未配置 HELPER_API_KEY，AI 分析功能将不可用")
        else:
            logger.debug(f"AI 分析器已配置: URL={self.base_url}, Model={self.model}")
    
    def analyze(self, html: str) -> Optional[Dict[str, str]]:
        """分析 HTML 并返回选择器"""
        if not self.api_key:
            logger.error("API Key 未配置")
            return None
        
        prompt = self._build_prompt(html)
        
        for attempt in range(AI_MAX_RETRIES):
            try:
                logger.info(f"正在请求 AI 分析（尝试 {attempt + 1}/{AI_MAX_RETRIES}）...")
                
                response = self._request_ai(prompt)
                if response:
                    selectors = self._extract_json(response)
                    if selectors:
                        logger.info("✅ AI 分析成功")
                        return selectors
                
                logger.warning(f"第 {attempt + 1} 次分析失败")
            
            except Exception as e:
                logger.error(f"AI 请求异常: {e}")
            
            if attempt < AI_MAX_RETRIES - 1:
                delay = min(
                    AI_RETRY_BASE_DELAY * (2 ** attempt),
                    AI_RETRY_MAX_DELAY
                )
                jitter = delay * 0.1 * (0.5 - os.urandom(1)[0] / 255)
                sleep_time = delay + jitter
                
                logger.info(f"等待 {sleep_time:.2f}s 后重试...")
                time.sleep(sleep_time)
        
        logger.error("❌ AI 分析失败（已达最大重试次数）")
        return None
    
    def _request_ai(self, prompt: str) -> Optional[str]:
        """向 AI API 发送请求"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }
        
        try:
            req = request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers=headers
            )
            
            with request.urlopen(req, timeout=AI_REQUEST_TIMEOUT) as response:
                response_text = response.read().decode('utf-8')
            
            try:
                json_resp = json.loads(response_text)
                if "choices" in json_resp and len(json_resp['choices']) > 0:
                    return json_resp['choices'][0]['message']['content']
            except json.JSONDecodeError:
                logger.error("AI 响应解析失败")
            
            return None
        
        except error.HTTPError as e:
            logger.error(f"HTTP 错误 {e.code}: {e.reason}")
            return None
        except error.URLError as e:
            logger.error(f"网络错误: {e.reason}")
            return None
        except TimeoutError:
            logger.error("请求超时")
            return None
    
    def _build_prompt(self, clean_html: str) -> str:
        """构建 AI 提示词（动态版）"""
        if self.global_config:
            selector_list = self.global_config.build_prompt_selector_list()
            json_keys = self.global_config.build_prompt_json_keys()
        else:
            selector_list = self._build_default_selector_list()
            json_keys = self._build_default_json_keys()
        
        lines = [
            "You are a web scraping expert. Analyze this AI chat interface HTML to identify critical elements.",
            "",
            "## CRITICAL RULES:",
            "1. **Uniqueness is Key**: Ensure selectors matches ONLY the intended element.",
            "2. **Distinguish AI vs User**: For `result_container`, specify the selector to target the **AI's response text** only. It MUST exclude user prompts, sidebars, or chat history.",
            "3. **Use Hierarchy**: If a class like `.prose` or `.markdown` is used for both User and AI, you MUST find a unique parent class to differentiate (e.g., `.bot-msg .prose`).",
            "4. **Syntax**: Use standard CSS selectors. Spaces for descendants (e.g., `div.bot p`) are encouraged for precision.",
            "5. **No Invalid Syntax**: Do NOT use `xpath`, `~`, `:has()`, or `text()`.",
            "",
            "## PREFERENCE ORDER:",
            "1. `id`, `name`, `data-testid` (Most preferred)",
            "2. `button[type=\"submit\"]`",
            "3. Unique parent class + target class (e.g., `.response-area .content`)",
            "4. Hashed classes (only if no other option exists)",
            "",
            "## ELEMENTS TO FIND:",
            selector_list,
            "",
            "## REQUIRED OUTPUT (JSON ONLY):",
            "Return a JSON object with these keys:",
            json_keys,
            "",
            "## HTML:",
            clean_html
        ]
        return "\n".join(lines)
    
    def _build_default_selector_list(self) -> str:
        """默认的元素列表（回退用）"""
        return """- `input_box`: The text input area (textarea/input). [REQUIRED]
- `send_btn`: The button that sends the message. [REQUIRED]
- `result_container`: The container for the AI's generated text response. [REQUIRED]
- `new_chat_btn`: Button to start a fresh conversation. [OPTIONAL, return null if not found]"""
    
    def _build_default_json_keys(self) -> str:
        """默认的 JSON 键说明（回退用）"""
        return """- `input_box`: The text input area
- `send_btn`: The send button
- `result_container`: AI response container
- `new_chat_btn`: New chat button (or null)"""
    
    def _extract_json(self, text: str) -> Optional[Dict]:
        """从 AI 响应中提取 JSON"""
        try:
            match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
            if match:
                return json.loads(match.group(1))
            
            match = re.search(r'(\{[\s\S]*\})', text)
            if match:
                return json.loads(match.group(1))
            
            return json.loads(text)
        
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            return None


__all__ = ['HTMLCleaner', 'SelectorValidator', 'AIAnalyzer']