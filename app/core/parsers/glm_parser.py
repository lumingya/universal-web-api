"""
glm_parser.py - ChatGLM SSE response parser.

Observed stream traits:
- content-type: text/event-stream
- each SSE block is carried in a data: line with a JSON payload
- think/tool_calls/text are all emitted through parts[*].content[*]
- text payloads are full rendered text snapshots, not pure deltas
- stream ends when a text part status becomes finish or top-level status becomes finish
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.core.config import logger
from .base import ResponseParser


class GLMParser(ResponseParser):
    """Parse ChatGLM SSE streams while ignoring think/tool-call noise."""

    _TERMINAL_STATUSES = {"finish", "finished", "intervene", "intervened"}

    def __init__(self) -> None:
        self._last_raw_length = 0
        self._pending = ""
        self._rendered_text = ""
        self._think_text = ""

    def reset(self) -> None:
        self._last_raw_length = 0
        self._pending = ""
        self._rendered_text = ""
        self._think_text = ""

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

            new_data = raw_response[self._last_raw_length:]
            self._last_raw_length = current_len

            content, done = self._consume_new_data(new_data)
            if content:
                result["content"] = content
            result["done"] = done
        except Exception as e:
            logger.debug(f"[GLMParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def _consume_new_data(self, new_data: str) -> tuple[str, bool]:
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

    def _parse_event_block(self, block: str) -> tuple[str, bool]:
        data_lines: List[str] = []

        for raw_line in block.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())

        payload = "\n".join(data_lines).strip()
        if not payload:
            return "", False

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "", False

        if not isinstance(data, dict):
            return "", False

        return self._extract_payload(data)

    def _extract_payload(self, data: Dict[str, Any]) -> tuple[str, bool]:
        top_status = str(data.get("status") or "").strip().lower()
        last_error = data.get("last_error")
        intervene_text = self._extract_intervene_text(last_error)
        parts = data.get("parts")
        if not isinstance(parts, list) or not parts:
            if intervene_text and top_status in self._TERMINAL_STATUSES:
                delta = self._compute_delta(self._rendered_text, intervene_text)
                self._rendered_text = intervene_text
                return delta, True
            return "", top_status in self._TERMINAL_STATUSES

        visible_snapshot = self._rendered_text
        done = top_status in self._TERMINAL_STATUSES

        for part in parts:
            if not isinstance(part, dict):
                continue

            part_status = str(part.get("status") or "").strip().lower()
            if part_status in self._TERMINAL_STATUSES:
                done = True

            content_items = part.get("content")
            if not isinstance(content_items, list):
                continue

            for item in content_items:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "think":
                    self._think_text = str(item.get("think") or self._think_text)
                    continue
                if item_type == "tool_calls":
                    tool_calls = item.get("tool_calls") or {}
                    tool_name = ""
                    if isinstance(tool_calls, dict):
                        tool_name = str(tool_calls.get("name") or "").strip().lower()
                    if tool_name == "finish":
                        done = True
                    continue
                if item_type != "text":
                    continue

                snapshot = str(item.get("text") or "")
                if snapshot:
                    visible_snapshot = snapshot
                if part_status in self._TERMINAL_STATUSES:
                    done = True

        if intervene_text and done and not visible_snapshot:
            visible_snapshot = intervene_text

        delta = self._compute_delta(self._rendered_text, visible_snapshot)
        self._rendered_text = visible_snapshot
        return delta, done

    @staticmethod
    def _extract_intervene_text(last_error: Any) -> str:
        if not isinstance(last_error, dict):
            return ""
        text = last_error.get("intervene_text")
        if not isinstance(text, str):
            return ""
        return text.strip()

    @staticmethod
    def _compute_delta(previous: str, current: str) -> str:
        if current == previous:
            return ""
        if previous and current.startswith(previous):
            return current[len(previous):]
        return current

    @classmethod
    def get_id(cls) -> str:
        return "glm"

    @classmethod
    def get_name(cls) -> str:
        return "GLM"

    @classmethod
    def get_description(cls) -> str:
        return "Parse ChatGLM SSE streams and emit assistant text deltas while ignoring think/tool-call events"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["/chatglm/backend-api/assistant/stream", "assistant/stream"]


__all__ = ["GLMParser"]
