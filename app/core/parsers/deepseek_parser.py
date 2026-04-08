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
        self._fragment_contents: List[str] = []
        self._current_fragment_type = ""
        self._current_fragment_index = -1
        self._debug_records: List[Dict[str, Any]] = []
        self._emit_buffer = ""

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
            safe_content = self._drain_safe_output(delta_content, force=done)
            if safe_content:
                result["content"] = safe_content
            result["done"] = done

        except Exception as e:
            logger.debug(f"[DeepSeekParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self) -> None:
        self._last_raw_length = 0
        self._pending = ""
        self._fragment_types = []
        self._fragment_contents = []
        self._current_fragment_type = ""
        self._current_fragment_index = -1
        self._debug_records = []
        self._emit_buffer = ""

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

        content, done = self._extract_content(data)
        self._record_debug_event(event_name, data, content, done)
        return content, done

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
            fragment_index = self._resolve_fragment_index(path)
            fragment_type = self._resolve_fragment_type(path)
            delta_text = self._apply_fragment_content_update(fragment_index, value)
            if self._should_emit_fragment_text(fragment_type):
                return delta_text, False
            return "", False

        if not path and not operation and isinstance(value, str):
            self._append_to_current_fragment(value)
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
                fragment_index = self._resolve_fragment_index(path)
                fragment_type = self._resolve_fragment_type(path)
                delta_text = self._apply_fragment_content_update(fragment_index, value)
                if self._should_emit_fragment_text(fragment_type):
                    content_parts.append(delta_text)

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
            self._fragment_contents.append("")
            self._current_fragment_index = len(self._fragment_types) - 1
            if fragment_type:
                self._current_fragment_type = fragment_type

            text = fragment.get("content", "")
            if isinstance(text, str):
                self._fragment_contents[self._current_fragment_index] = text
                if text and self._should_emit_fragment_text(fragment_type):
                    content_parts.append(text)

        return "".join(content_parts)

    def _resolve_fragment_index(self, path: str) -> int:
        marker = "response/fragments/"
        if marker not in path or not self._fragment_types:
            return self._current_fragment_index

        index_part = path.split(marker, 1)[1].split("/", 1)[0]
        if index_part == "-1":
            return len(self._fragment_types) - 1

        try:
            index = int(index_part)
        except ValueError:
            return self._current_fragment_index

        if index < 0:
            index += len(self._fragment_types)
        if 0 <= index < len(self._fragment_types):
            return index

        return self._current_fragment_index

    def _resolve_fragment_type(self, path: str) -> str:
        fragment_index = self._resolve_fragment_index(path)
        if 0 <= fragment_index < len(self._fragment_types):
            fragment_type = self._fragment_types[fragment_index]
            self._current_fragment_index = fragment_index
            self._current_fragment_type = fragment_type
            return fragment_type
        return self._current_fragment_type

    def _append_to_current_fragment(self, delta_text: str) -> None:
        if not isinstance(delta_text, str) or not delta_text:
            return
        if 0 <= self._current_fragment_index < len(self._fragment_contents):
            self._fragment_contents[self._current_fragment_index] += delta_text

    def _apply_fragment_content_update(self, fragment_index: int, incoming_text: str) -> str:
        if not isinstance(incoming_text, str) or not incoming_text:
            return ""

        if not (0 <= fragment_index < len(self._fragment_contents)):
            return incoming_text

        previous_text = self._fragment_contents[fragment_index]
        new_text, delta_text = self._merge_fragment_text(previous_text, incoming_text)
        self._fragment_contents[fragment_index] = new_text
        self._current_fragment_index = fragment_index
        return delta_text

    @staticmethod
    def _merge_fragment_text(previous_text: str, incoming_text: str) -> Tuple[str, str]:
        previous = previous_text if isinstance(previous_text, str) else ""
        incoming = incoming_text if isinstance(incoming_text, str) else ""

        if not previous:
            return incoming, incoming

        if not incoming:
            return previous, ""

        if incoming.startswith(previous):
            return incoming, incoming[len(previous) :]

        if previous.endswith(incoming):
            return previous, ""

        common_prefix_len = 0
        max_prefix_len = min(len(previous), len(incoming))
        while common_prefix_len < max_prefix_len and previous[common_prefix_len] == incoming[common_prefix_len]:
            common_prefix_len += 1

        if common_prefix_len > 0:
            return incoming, incoming[common_prefix_len:]

        return previous + incoming, incoming

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

    def _drain_safe_output(self, delta_text: str, force: bool = False) -> str:
        if delta_text:
            self._emit_buffer += delta_text

        if not self._emit_buffer:
            return ""

        if force:
            flushed = self._emit_buffer
            self._emit_buffer = ""
            return flushed

        safe_index = self._find_last_safe_emit_index(self._emit_buffer)
        if safe_index <= 0:
            return ""

        flushed = self._emit_buffer[:safe_index]
        self._emit_buffer = self._emit_buffer[safe_index:]
        return flushed

    @staticmethod
    def _find_last_safe_emit_index(text: str) -> int:
        if not text:
            return 0

        inside_tag = False
        quote_char = ""
        last_safe_index = 0

        for index, char in enumerate(text):
            if inside_tag:
                if quote_char:
                    if char == quote_char:
                        quote_char = ""
                    continue

                if char in {'"', "'"}:
                    quote_char = char
                    continue

                if char == ">":
                    inside_tag = False
                    last_safe_index = index + 1
                continue

            if char == "<":
                inside_tag = True
                continue

            last_safe_index = index + 1

        if inside_tag:
            last_open = text.rfind("<")
            if last_open != -1:
                return last_open

        return last_safe_index

    def export_debug_data(self, raw_response: str = "") -> Dict[str, Any]:
        return {
            "parser": self.get_id(),
            "fragment_types": list(self._fragment_types),
            "fragment_lengths": [len(item) for item in self._fragment_contents],
            "current_fragment_type": self._current_fragment_type,
            "current_fragment_index": self._current_fragment_index,
            "debug_records": self._debug_records[-120:],
            "raw_response_preview": str(raw_response or "")[:12000],
        }

    def export_debug_snapshot(self, raw_response: str = "") -> str:
        import time
        from pathlib import Path

        payload = {
            **self.export_debug_data(raw_response),
            "exported_at": int(time.time()),
        }
        path = Path("logs") / "deepseek_parser_debug.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return str(path)

    def _record_debug_event(self, event_name: str, data: Dict[str, Any], content: str, done: bool) -> None:
        value = data.get("v")
        path = str(data.get("p", "") or "")
        operation = str(data.get("o", "") or "")

        record: Dict[str, Any] = {
            "event": str(event_name or ""),
            "path": path,
            "op": operation,
            "done": bool(done),
            "content_len": len(content or ""),
            "content_preview": str(content or "")[:240],
            "value_type": type(value).__name__,
            "fragment_count": len(self._fragment_types),
            "current_fragment_type": self._current_fragment_type,
            "current_fragment_index": self._current_fragment_index,
        }

        if isinstance(value, str):
            record["value_len"] = len(value)
            record["value_preview"] = value[:240]
        elif isinstance(value, list):
            record["value_len"] = len(value)
        elif isinstance(value, dict):
            response = value.get("response") if isinstance(value.get("response"), dict) else {}
            fragments = response.get("fragments") if isinstance(response.get("fragments"), list) else []
            record["response_keys"] = list(response.keys())[:12]
            record["response_fragment_count"] = len(fragments)
            if fragments:
                record["response_fragment_types"] = [
                    str(fragment.get("type", "") or "").upper()
                    for fragment in fragments[:8]
                    if isinstance(fragment, dict)
                ]

        self._debug_records.append(record)
        if len(self._debug_records) > 200:
            self._debug_records = self._debug_records[-200:]

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
