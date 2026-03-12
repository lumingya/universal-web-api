"""
claude_parser.py - Claude SSE response parser.

Observed stream traits:
- event: content_block_delta with delta.type=text_delta carries answer text
- thinking blocks use thinking_delta / thinking_summary_delta and should be ignored
- event: message_stop marks stream completion
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from app.core.config import logger
from .base import ResponseParser


class ClaudeParser(ResponseParser):
    """Parse Claude web SSE responses."""

    def __init__(self) -> None:
        self._last_raw_length = 0
        self._pending = ""

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
            logger.debug(f"[ClaudeParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self) -> None:
        self._last_raw_length = 0
        self._pending = ""

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

        if event_name == "message_stop":
            return "", True

        payload = "\n".join(data_lines).strip()
        if event_name != "content_block_delta" or not payload:
            return "", False

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "", False

        delta = data.get("delta", {})
        if not isinstance(delta, dict):
            return "", False

        if delta.get("type") != "text_delta":
            return "", False

        text = delta.get("text", "")
        return (text, False) if isinstance(text, str) else ("", False)

    @classmethod
    def get_id(cls) -> str:
        return "claude"

    @classmethod
    def get_name(cls) -> str:
        return "Claude"

    @classmethod
    def get_description(cls) -> str:
        return "Parse Claude SSE streams and ignore thinking blocks"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["chat_conversations", "/completion"]


__all__ = ["ClaudeParser"]
