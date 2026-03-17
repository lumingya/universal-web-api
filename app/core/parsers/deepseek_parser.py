"""
deepseek_parser.py - DeepSeek SSE response parser.

Observed stream traits:
- data: {"v": {"response": {"fragments": [...]}}} initializes fragment state
- thinking mode sends THINK fragments first and answer text later in RESPONSE fragments
- path-less data: {"v": "..."} continues the current fragment's content
- response/status or quasi_status=FINISHED ends the stream
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from app.core.config import logger
from .base import ResponseParser


class DeepSeekParser(ResponseParser):
    """Parse DeepSeek web SSE streams and ignore THINK fragments."""

    def __init__(self) -> None:
        self._last_raw_length = 0
        self._pending = ""
        self._fragment_types: List[str] = []
        self._current_fragment_type = ""

    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "content": "",
            "images": [],
            "done": False,
            "error": None,
        }

        try:
            if isinstance(raw_response, (bytes, bytearray)):
                raw_response = raw_response.decode("utf-8", errors="ignore")
            elif not isinstance(raw_response, str):
                raw_response = str(raw_response)

            current_len = len(raw_response)
            if current_len <= self._last_raw_length:
                return result

            new_data = raw_response[self._last_raw_length :]
            self._last_raw_length = current_len

            delta_content, done = self._consume_new_data(new_data)
            if delta_content:
                result["content"] = delta_content
            result["done"] = done

        except Exception as e:
            logger.debug(f"[DeepSeekParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self) -> None:
        self._last_raw_length = 0
        self._pending = ""
        self._fragment_types = []
        self._current_fragment_type = ""

    def _consume_new_data(self, new_data: str) -> Tuple[str, bool]:
        normalized = (self._pending + new_data).replace("\r\n", "\n")
        if not normalized:
            return "", False

        blocks = normalized.split("\n\n")
        if normalized.endswith("\n\n"):
            self._pending = ""
            complete_blocks = [block for block in blocks if block.strip()]
        else:
            self._pending = blocks.pop() if blocks else normalized
            complete_blocks = [block for block in blocks if block.strip()]

        content_parts: List[str] = []
        done = False

        for block in complete_blocks:
            block_content, block_done = self._parse_event_block(block)
            if block_content:
                content_parts.append(block_content)
            if block_done:
                done = True

        return "".join(content_parts), done

    def _parse_event_block(self, block: str) -> Tuple[str, bool]:
        event_name = ""
        data_lines: List[str] = []

        for raw_line in block.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())

        if event_name in {"ready", "update_session", "title"}:
            return "", False
        if event_name in {"finish", "close"}:
            return "", True

        payload = "\n".join(data_lines).strip()
        if not payload or payload == "{}":
            return "", False

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "", False

        return self._extract_content(data)

    def _extract_content(self, data: Dict[str, Any]) -> Tuple[str, bool]:
        path = str(data.get("p", "") or "")
        operation = str(data.get("o", "") or "")
        value = data.get("v")

        if operation == "BATCH" and isinstance(value, list):
            return self._extract_batch_content(value)

        if isinstance(value, dict):
            response = value.get("response")
            if isinstance(response, dict):
                return self._extract_response_object(response)

        if self._is_done_signal(path, value):
            return "", True

        if self._is_fragment_append(path, operation, value):
            return self._register_fragments(value), False

        if self._is_fragment_content_patch(path, value):
            fragment_type = self._resolve_fragment_type(path)
            if self._should_emit_fragment_text(fragment_type):
                return value, False
            return "", False

        if not path and not operation and isinstance(value, str):
            if self._should_emit_fragment_text(self._current_fragment_type):
                return value, False
            return "", False

        return "", False

    def _extract_batch_content(self, operations: List[Dict[str, Any]]) -> Tuple[str, bool]:
        content_parts: List[str] = []
        done = False

        for op in operations:
            if not isinstance(op, dict):
                continue

            path = str(op.get("p", "") or "")
            operation = str(op.get("o", "") or "")
            value = op.get("v")

            if self._is_done_signal(path, value):
                done = True
                continue

            if self._is_fragment_append(path, operation, value):
                fragment_content = self._register_fragments(value)
                if fragment_content:
                    content_parts.append(fragment_content)
                continue

            if self._is_fragment_content_patch(path, value):
                fragment_type = self._resolve_fragment_type(path)
                if self._should_emit_fragment_text(fragment_type):
                    content_parts.append(value)

        return "".join(content_parts), done

    def _extract_response_object(self, response: Dict[str, Any]) -> Tuple[str, bool]:
        fragments = response.get("fragments")
        content = self._register_fragments(fragments) if isinstance(fragments, list) else ""

        done = False
        if self._is_done_signal("response/status", response.get("status")):
            done = True
        if self._is_done_signal("response/quasi_status", response.get("quasi_status")):
            done = True

        return content, done

    def _register_fragments(self, fragments: List[Dict[str, Any]]) -> str:
        content_parts: List[str] = []

        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue

            fragment_type = str(fragment.get("type", "") or "").upper()
            self._fragment_types.append(fragment_type)
            if fragment_type:
                self._current_fragment_type = fragment_type

            text = fragment.get("content", "")
            if isinstance(text, str) and text and self._should_emit_fragment_text(fragment_type):
                content_parts.append(text)

        return "".join(content_parts)

    def _resolve_fragment_type(self, path: str) -> str:
        marker = "response/fragments/"
        if marker not in path or not self._fragment_types:
            return self._current_fragment_type

        index_part = path.split(marker, 1)[1].split("/", 1)[0]
        if index_part == "-1":
            fragment_type = self._fragment_types[-1]
            self._current_fragment_type = fragment_type
            return fragment_type

        try:
            index = int(index_part)
        except ValueError:
            return self._current_fragment_type

        if index < 0:
            index += len(self._fragment_types)
        if 0 <= index < len(self._fragment_types):
            fragment_type = self._fragment_types[index]
            self._current_fragment_type = fragment_type
            return fragment_type

        return self._current_fragment_type

    @staticmethod
    def _is_fragment_append(path: str, operation: str, value: Any) -> bool:
        if not isinstance(value, list):
            return False
        return operation == "APPEND" and path in {"response/fragments", "fragments"}

    @staticmethod
    def _is_fragment_content_patch(path: str, value: Any) -> bool:
        return isinstance(value, str) and "fragments/" in path and path.endswith("/content")

    @staticmethod
    def _is_done_signal(path: str, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        if value.upper() != "FINISHED":
            return False
        return path.endswith("status") or path.endswith("quasi_status")

    def _should_emit_fragment_text(self, fragment_type: str) -> bool:
        if not self._fragment_types:
            return True
        return fragment_type == "RESPONSE"

    @classmethod
    def get_id(cls) -> str:
        return "deepseek"

    @classmethod
    def get_name(cls) -> str:
        return "DeepSeek API"

    @classmethod
    def get_description(cls) -> str:
        return "Parse DeepSeek SSE streams and ignore THINK fragments"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["**/api/v0/chat/completion**"]


__all__ = ["DeepSeekParser"]
