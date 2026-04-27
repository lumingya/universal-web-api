"""
chatgpt_parser.py - ChatGPT 响应解析器

响应格式特征：
- SSE (Server-Sent Events) 流式响应
- 使用 v1 delta 编码
- 增量文本通过 event: delta 事件传递
- 结束标志: data: [DONE]
"""

import json
import re
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
        self._rendered_content = ""
        self._message_parts: Dict[int, str] = {}
        self._content_references: List[Dict[str, Any]] = []
        self._active_part_index: Optional[int] = None
        self._ref_start = "\ue200"
        self._ref_end = "\ue201"
        self._ref_sep = "\ue202"
        self._inline_ref_pattern = re.compile(
            rf"{re.escape(self._ref_start)}([^{re.escape(self._ref_sep)}{re.escape(self._ref_end)}]+)"
            rf"{re.escape(self._ref_sep)}(.*?){re.escape(self._ref_end)}",
            re.DOTALL,
        )
        self._dangling_ref_pattern = re.compile(
            rf"{re.escape(self._ref_start)}[^{re.escape(self._ref_end)}]*$",
            re.DOTALL,
        )
    
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
        previous_content = self._rendered_content

        for current_event, data in self._iter_sse_json_events(chunk):
            if not isinstance(data, dict):
                continue
            self._consume_event(current_event, data)

        current_content = self._render_visible_text()
        self._rendered_content = current_content
        return self._compute_delta(previous_content, current_content)

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
    
    def _consume_event(self, event_name: Optional[str], data: Dict[str, Any]) -> None:
        if event_name != "delta":
            return

        operations: List[Dict[str, Any]] = []
        if data.get("o") == "patch" and isinstance(data.get("v"), list):
            operations = [item for item in data.get("v", []) if isinstance(item, dict)]
        elif isinstance(data.get("v"), list):
            operations = [item for item in data.get("v", []) if isinstance(item, dict)]
        elif isinstance(data.get("p"), str) and data.get("o") is not None:
            operations = [data]

        if operations:
            for operation in operations:
                self._apply_operation(operation)
            return

        value = data.get("v")
        if isinstance(value, str) and self._active_part_index is not None:
            self._message_parts[self._active_part_index] = (
                self._message_parts.get(self._active_part_index, "") + value
            )
            return

        self._sync_message_snapshot_from_payload(data)

    def _sync_message_snapshot_from_payload(self, payload: Dict[str, Any]) -> None:
        message = None
        value = payload.get("v")
        if isinstance(value, dict) and isinstance(value.get("message"), dict):
            message = value.get("message") or {}
        elif isinstance(payload.get("message"), dict):
            message = payload.get("message") or {}

        if not isinstance(message, dict):
            return

        author = message.get("author") or {}
        if str(author.get("role") or "").strip().lower() != "assistant":
            return

        content = message.get("content") or {}
        if not isinstance(content, dict):
            return

        if str(content.get("content_type") or "").strip().lower() != "text":
            return

        parts = content.get("parts")
        if isinstance(parts, list):
            self._message_parts = {
                index: str(part)
                for index, part in enumerate(parts)
                if isinstance(part, str)
            }
            self._active_part_index = max(self._message_parts.keys(), default=0)

        metadata = message.get("metadata") or {}
        refs = metadata.get("content_references")
        if isinstance(refs, list):
            self._content_references = [
                dict(item) for item in refs if isinstance(item, dict)
            ]

    def _apply_operation(self, operation: Dict[str, Any]) -> None:
        path = str(operation.get("p") or "")
        action = str(operation.get("o") or "").strip().lower()
        value = operation.get("v")

        if self._apply_content_part_operation(path, action, value):
            return

        if self._apply_content_reference_operation(path, action, value):
            return

    def _apply_content_part_operation(self, path: str, action: str, value: Any) -> bool:
        if not path.startswith("/message/content/parts"):
            return False

        if path == "/message/content/parts":
            if action in {"add", "replace"} and isinstance(value, list):
                self._message_parts = {
                    index: str(part)
                    for index, part in enumerate(value)
                    if isinstance(part, str)
                }
                self._active_part_index = max(self._message_parts.keys(), default=0)
            elif action == "append" and isinstance(value, list):
                start_index = max(self._message_parts.keys(), default=-1) + 1
                for offset, part in enumerate(value):
                    if isinstance(part, str):
                        self._message_parts[start_index + offset] = part
                self._active_part_index = max(self._message_parts.keys(), default=0)
            elif action == "remove":
                self._message_parts = {}
                self._active_part_index = None
            return True

        match = re.match(r"^/message/content/parts/(\d+)$", path)
        if not match:
            return True

        index = int(match.group(1))
        if action == "append" and isinstance(value, str):
            self._message_parts[index] = self._message_parts.get(index, "") + value
            self._active_part_index = index
        elif action in {"add", "replace"}:
            if isinstance(value, str):
                self._message_parts[index] = value
                self._active_part_index = index
            elif value is None:
                self._message_parts[index] = ""
                self._active_part_index = index
        elif action == "remove":
            self._message_parts.pop(index, None)
            if self._active_part_index == index:
                self._active_part_index = max(self._message_parts.keys(), default=None)
        return True

    def _apply_content_reference_operation(self, path: str, action: str, value: Any) -> bool:
        if not path.startswith("/message/metadata/content_references"):
            return False

        match = re.match(r"^/message/metadata/content_references(?:/(\d+)(?:/(.+))?)?$", path)
        if not match:
            return True

        index_text, subpath = match.groups()
        if index_text is None:
            if action in {"add", "replace"} and isinstance(value, list):
                self._content_references = [
                    dict(item) for item in value if isinstance(item, dict)
                ]
            elif action == "append":
                items = value if isinstance(value, list) else [value]
                for item in items:
                    if isinstance(item, dict):
                        self._content_references.append(dict(item))
            elif action == "remove":
                self._content_references = []
            return True

        index = int(index_text)
        self._ensure_reference_index(index)

        if not subpath:
            if action == "remove":
                if 0 <= index < len(self._content_references):
                    self._content_references.pop(index)
            elif action in {"add", "replace"} and isinstance(value, dict):
                self._content_references[index] = dict(value)
            elif action == "append" and isinstance(value, dict):
                self._content_references[index].update(value)
            return True

        target = self._content_references[index]
        self._apply_nested_patch(target, subpath.split("/"), action, value)
        return True

    def _ensure_reference_index(self, index: int) -> None:
        while len(self._content_references) <= index:
            self._content_references.append({})

    def _apply_nested_patch(
        self,
        container: Dict[str, Any],
        path_segments: List[str],
        action: str,
        value: Any,
    ) -> None:
        current = container
        for segment in path_segments[:-1]:
            next_value = current.get(segment)
            if not isinstance(next_value, dict):
                next_value = {}
                current[segment] = next_value
            current = next_value

        key = path_segments[-1]
        existing = current.get(key)
        if action == "append":
            if isinstance(existing, str) and isinstance(value, str):
                current[key] = existing + value
            elif isinstance(existing, list) and isinstance(value, list):
                current[key] = existing + value
            elif isinstance(existing, dict) and isinstance(value, dict):
                merged = dict(existing)
                merged.update(value)
                current[key] = merged
            elif existing is None:
                current[key] = value
            else:
                current[key] = value
        elif action in {"add", "replace"}:
            current[key] = value
        elif action == "remove":
            current.pop(key, None)

    def _render_visible_text(self) -> str:
        if not self._message_parts:
            return self._rendered_content

        text = "".join(self._message_parts[index] for index in sorted(self._message_parts))
        text = self._replace_content_references(text)
        text = self._strip_private_markup(text)
        return text

    def _replace_content_references(self, text: str) -> str:
        rendered = text
        references = sorted(
            (
                ref for ref in self._content_references
                if isinstance(ref, dict) and str(ref.get("matched_text") or "")
            ),
            key=lambda ref: len(str(ref.get("matched_text") or "")),
            reverse=True,
        )

        for ref in references:
            matched_text = str(ref.get("matched_text") or "")
            if not matched_text or matched_text not in rendered:
                continue
            rendered = rendered.replace(
                matched_text,
                self._build_reference_replacement(ref),
            )

        rendered = self._inline_ref_pattern.sub(
            lambda match: self._decode_inline_reference(match.group(1), match.group(2)),
            rendered,
        )
        return rendered

    def _build_reference_replacement(self, ref: Dict[str, Any]) -> str:
        ref_type = str(ref.get("type") or "").strip().lower()
        if ref_type == "hidden":
            return ""

        label = ""
        for key in ("alt", "prompt_text", "name"):
            value = str(ref.get(key) or "").strip()
            if value:
                label = value
                break

        if not label:
            matched_text = str(ref.get("matched_text") or "")
            if matched_text:
                label = self._decode_inline_reference_from_text(matched_text)

        safe_urls = ref.get("safe_urls")
        first_url = ""
        if isinstance(safe_urls, list):
            for item in safe_urls:
                candidate = str(item or "").strip()
                if candidate:
                    first_url = candidate
                    break

        if first_url and label:
            if first_url in label or "](" in label or label.startswith("http://") or label.startswith("https://"):
                return label
            return f"{label} ({first_url})"
        if label:
            return label
        if first_url:
            return first_url
        return ""

    def _decode_inline_reference_from_text(self, text: str) -> str:
        match = self._inline_ref_pattern.search(str(text or ""))
        if not match:
            return ""
        return self._decode_inline_reference(match.group(1), match.group(2))

    def _decode_inline_reference(self, ref_kind: str, payload: str) -> str:
        kind = str(ref_kind or "").strip().lower()
        payload_text = str(payload or "")

        if kind == "entity":
            try:
                decoded = json.loads(payload_text)
                if isinstance(decoded, list):
                    for index in (1, 0, 2):
                        if index < len(decoded):
                            value = str(decoded[index] or "").strip()
                            if value:
                                return value
            except Exception:
                return ""

        return ""

    def _strip_private_markup(self, text: str) -> str:
        cleaned = self._dangling_ref_pattern.sub("", text)
        cleaned = cleaned.replace(self._ref_start, "")
        cleaned = cleaned.replace(self._ref_sep, "")
        cleaned = cleaned.replace(self._ref_end, "")
        return cleaned

    @staticmethod
    def _compute_delta(previous: str, current: str) -> str:
        if not current or current == previous:
            return ""
        if not previous:
            return current
        if current.startswith(previous):
            return current[len(previous):]

        prefix_len = 0
        max_prefix = min(len(previous), len(current))
        while prefix_len < max_prefix and previous[prefix_len] == current[prefix_len]:
            prefix_len += 1

        return current[prefix_len:]
    
    def reset(self):
        """重置状态"""
        self._accumulated_content = ""
        self._last_raw_length = 0
        self._rendered_content = ""
        self._message_parts = {}
        self._content_references = []
        self._active_part_index = None

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
