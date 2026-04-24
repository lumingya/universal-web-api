"""
chatgpt_parser.py - ChatGPT 响应解析器

响应格式特征：
- SSE (Server-Sent Events) 流式响应
- 使用 v1 delta 编码
- 增量文本通过 event: delta 事件传递
- 结束标志: data: [DONE]
"""

import json
from typing import Dict, Any, List, Optional

from app.core.config import logger
from .base import ResponseParser


class ChatGPTParser(ResponseParser):
    """
    ChatGPT API 响应解析器
    
    URL 特征: /backend-api/f/conversation
    响应格式: SSE (text/event-stream) with v1 delta encoding
    """
    
    def __init__(self):
        self._accumulated_content = ""
        self._last_raw_length = 0
        self._is_appending_text = False
    
    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        """
        解析SSE响应流（返回增量）
        """
        result = {
            "content": "",
            "images": [],
            "done": False,
            "error": None
        }
        
        try:
            # 确保是字符串
            if isinstance(raw_response, bytes):
                raw_response = raw_response.decode('utf-8', errors='ignore')
            
            if not isinstance(raw_response, str):
                raw_response = str(raw_response)
            
            # 只处理新增部分
            current_len = len(raw_response)
            if current_len <= self._last_raw_length:
                return result
            
            new_data = raw_response[self._last_raw_length:]
            self._last_raw_length = current_len
            
            # 解析新增的SSE事件
            delta_content = self._parse_sse_chunk(new_data)
            
            if delta_content:
                result["content"] = delta_content
                self._accumulated_content += delta_content
            
            # 检测结束标志
            if "data: [DONE]" in new_data:
                result["done"] = True
            
        except Exception as e:
            logger.debug(f"[ChatGPTParser] 解析异常: {e}")
            result["error"] = str(e)
        
        return result
    
    def _parse_sse_chunk(self, chunk: str) -> str:
        """解析SSE数据块，提取文本增量"""
        content = ""
        
        for current_event, data in self._iter_sse_json_events(chunk):
            if current_event != "delta" or not isinstance(data, dict):
                continue
            extracted = self._extract_delta_content(data)
            if extracted:
                content += extracted
        
        return content

    def _iter_sse_json_events(self, chunk: str):
        """遍历 SSE 中可解析为 JSON 的事件。"""
        current_event = None

        for raw_line in str(chunk or "").split('\n'):
            line = raw_line.strip()

            if not line:
                current_event = None
                continue

            if line.startswith('event:'):
                current_event = line[6:].strip()
                continue

            if not line.startswith('data:'):
                continue

            data_str = line[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue

            try:
                payload = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            yield current_event, payload
    
    def _extract_delta_content(self, data: Dict[str, Any]) -> str:
        """从delta事件中提取文本内容"""
        
        # 1. 优先处理 patch 操作（批量更新，包含结尾内容）
        if data.get("o") == "patch" and isinstance(data.get("v"), list):
            content = ""
            for op in data["v"]:
                if isinstance(op, dict):
                    # 递归提取每个子操作
                    sub_path = op.get("p", "")
                    sub_op = op.get("o", "")
                    sub_v = op.get("v", "")
                    if "/message/content/parts" in sub_path and sub_op == "append":
                        if isinstance(sub_v, str):
                            content += sub_v
            return content
        
        path = data.get("p", "")
        op = data.get("o", "")
        
        # 2. 明确的 append 操作：开启追加模式
        if "/message/content/parts" in path and op == "append":
            self._is_appending_text = True
            v = data.get("v", "")
            if isinstance(v, str):
                return v
            return ""
        
        # 3. 明确的 replace 操作：关闭追加模式
        if "/message/content/parts" in path and op == "replace":
            self._is_appending_text = False
            return ""
        
        # 4. 追加模式下，处理纯增量事件（只有 v，无 p/o）
        if self._is_appending_text:
            v = data.get("v")
            if isinstance(v, str):
                return v
        
        return ""
    
    def reset(self):
        """重置状态"""
        self._accumulated_content = ""
        self._last_raw_length = 0
        self._is_appending_text = False

    def get_media_generation_state(
        self,
        raw_response: str = "",
        parse_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        pending = False
        media_type = ""
        hint_parts: List[str] = []
        wait_timeout_seconds = None

        try:
            for event_name, payload in self._iter_sse_json_events(raw_response):
                if not isinstance(payload, dict):
                    continue

                payload_type = str(payload.get("type") or "").strip().lower()
                if payload_type == "server_ste_metadata":
                    metadata = payload.get("metadata") or {}
                    if str(metadata.get("turn_use_case") or "").strip().lower() == "image gen":
                        pending = True
                        media_type = media_type or "image"
                        wait_timeout_seconds = max(float(wait_timeout_seconds or 0), 90.0)

                message = None
                if event_name == "delta":
                    value = payload.get("v")
                    if isinstance(value, dict) and isinstance(value.get("message"), dict):
                        message = value.get("message") or {}
                elif isinstance(payload.get("message"), dict):
                    message = payload.get("message") or {}

                if not isinstance(message, dict):
                    continue

                metadata = message.get("metadata") or {}
                content = message.get("content") or {}

                if str(metadata.get("image_gen_task_id") or "").strip():
                    pending = True
                    media_type = media_type or "image"
                    wait_timeout_seconds = max(float(wait_timeout_seconds or 0), 90.0)

                if bool(metadata.get("image_gen_multi_stream")):
                    pending = True
                    media_type = media_type or "image"
                    wait_timeout_seconds = max(float(wait_timeout_seconds or 0), 90.0)

                for field in ("ui_card_title", "ui_card_description"):
                    value = str(metadata.get(field) or "").strip()
                    if value:
                        hint_parts.append(value)

                if isinstance(content, dict):
                    parts = content.get("parts")
                    if isinstance(parts, list):
                        for item in parts:
                            if isinstance(item, str) and item.strip():
                                hint_parts.append(item.strip())

        except Exception as e:
            logger.debug(f"[ChatGPTParser] 媒体状态解析异常: {e}")

        if not pending or not media_type:
            return {}

        deduped_hint_parts = []
        seen = set()
        for item in hint_parts:
            if item in seen:
                continue
            seen.add(item)
            deduped_hint_parts.append(item)

        return {
            "pending": True,
            "media_type": media_type,
            "hint_text": "\n\n".join(deduped_hint_parts[:3]),
            "wait_timeout_seconds": wait_timeout_seconds,
        }
    
    @classmethod
    def get_id(cls) -> str:
        return "chatgpt"
    
    @classmethod
    def get_name(cls) -> str:
        return "ChatGPT API"
    
    @classmethod
    def get_description(cls) -> str:
        return "解析 ChatGPT 的 API 响应（SSE流式，v1 delta编码）"
    
    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["**/backend-api/f/conversation**"]


__all__ = ['ChatGPTParser']
