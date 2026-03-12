"""
qwen_parser.py - Qwen SSE response parser.

Observed stream traits:
- data: {"response.created": ...} announces the response id
- answer tokens are in choices[*].delta.content with phase=answer
- thinking_summary chunks should be ignored
- choices[*].delta.status=finished ends the answer stream
"""

from __future__ import annotations

import json
from typing import Dict, Any, List, Tuple

from app.core.config import logger
from .base import ResponseParser


class QwenParser(ResponseParser):
    """Parse Qwen web SSE responses."""

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
            logger.debug(f"[QwenParser] parse exception: {e}")
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
        data_lines: List[str] = []

        for raw_line in block.split("\n"):
            line = raw_line.strip()
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())

        payload = "\n".join(data_lines).strip()
        if not payload:
            return "", False

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "", False

        choices = data.get("choices")
        if not isinstance(choices, list):
            return "", False

        content_parts: List[str] = []
        done = False

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            if delta.get("phase") != "answer":
                continue

            text = delta.get("content", "")
            if isinstance(text, str) and text:
                content_parts.append(text)

            if delta.get("status") == "finished":
                done = True

        return "".join(content_parts), done

    @classmethod
    def get_id(cls) -> str:
        return "qwen"

    @classmethod
    def get_name(cls) -> str:
        return "Qwen"

    @classmethod
    def get_description(cls) -> str:
        return "Parse Qwen SSE streams and keep only answer-phase text"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["**/api/v2/chat/completions**"]


__all__ = ["QwenParser"]
