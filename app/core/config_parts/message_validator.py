"""
app/core/config_parts/message_validator.py - 消息验证器模块
"""
import ast
import json
from typing import Any, Tuple, Optional, List
from .browser_constants import BrowserConstants


class MessageValidator:
    """消息验证器"""
    
    VALID_ROLES = {'user', 'assistant', 'system'}
    ROLE_ALIASES = {
        'developer': 'system',
    }
    _IMAGE_PLACEHOLDER = "[图片]"

    @classmethod
    def _parse_multimodal_string(cls, content: str):
        """尝试把字符串形式的多模态 content 还原成列表。"""
        text = str(content or "")
        stripped = text.strip()
        if not stripped.startswith('[') or not stripped.endswith(']'):
            return text, False

        parsed = None
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = None

        if parsed is None:
            try:
                parsed = ast.literal_eval(stripped)
            except Exception:
                parsed = None

        if isinstance(parsed, list):
            return parsed, True
        return text, False

    @classmethod
    def _normalize_content(cls, content: Any) -> Any:
        """保留多模态结构，避免把图片/base64 粗暴 str() 化。"""
        if content is None:
            return ""
        if isinstance(content, list):
            return content
        if isinstance(content, tuple):
            return list(content)
        if isinstance(content, str):
            parsed, ok = cls._parse_multimodal_string(content)
            return parsed if ok else content
        return str(content)

    @classmethod
    def _effective_content_length(cls, content: Any) -> int:
        """按网页执行真实会使用的语义估算 content 长度。"""
        normalized = cls._normalize_content(content)

        if isinstance(normalized, str):
            text = normalized
            if text.startswith("data:image") and "base64," in text and len(text) > 1000:
                return len("[图片内容]")
            return len(text)

        if isinstance(normalized, list):
            total = 0
            for item in normalized:
                if item is None:
                    continue
                if not isinstance(item, dict):
                    total += len(str(item))
                    continue

                item_type = str(item.get("type", "") or "").strip()
                if item_type == "text":
                    total += len(str(item.get("text", "") or ""))
                elif item_type == "image_url":
                    total += len(cls._IMAGE_PLACEHOLDER)
                else:
                    total += len(str(item))
            return total

        return len(str(normalized))
    
    @classmethod
    def validate(cls, messages: Any) -> tuple:
        if messages is None:
            return False, "messages 不能为空", None
        
        if not isinstance(messages, list):
            return False, "messages 应该是列表", None
        
        if len(messages) == 0:
            return False, "messages 不能为空列表", None
        
        message_count = len(messages)
        max_messages = int(BrowserConstants.MAX_MESSAGES_COUNT)
        if message_count > max_messages:
            return False, (
                f"消息数量超过限制（当前 {message_count} 条，最大允许 {max_messages} 条）"
            ), None
        
        sanitized = []
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                return False, f"messages[{i}] 不是字典类型", None
            
            role = str(msg.get('role', 'user') or 'user').strip().lower()
            role = cls.ROLE_ALIASES.get(role, role)
            if role not in cls.VALID_ROLES:
                role = 'user'
            
            content = cls._normalize_content(msg.get('content', ''))
            
            content_length = cls._effective_content_length(content)
            max_length = int(BrowserConstants.MAX_MESSAGE_LENGTH)
            if content_length > max_length:
                return False, (
                    f"messages[{i}].content 超过长度限制"
                    f"（当前 {content_length} 字符，最大允许 {max_length} 字符）"
                ), None
            
            sanitized.append({'role': role, 'content': content})
        
        return True, None, sanitized
