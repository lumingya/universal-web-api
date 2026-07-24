"""
claude_parser.py - Claude SSE response parser.

Observed stream traits:
- event: content_block_delta with delta.type=text_delta carries answer text
- event: content_block_delta with delta.type=thinking_delta carries Extended Thinking CoT text
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
            "reasoning_content": "",
            "images": [],
            "done": False,
            "error": None,
        }

        try:
            if isinstance(raw_response, (bytes, bytearray)):
                raw_response = raw_response.decode("utf-8", errors="ignore")
            elif not isinstance(raw_response, str):
                raw_response = str(raw_response)

            new_data = self._prepare_incremental_raw_response(raw_response)
            if not new_data:
                return result

            delta_content, delta_reasoning, done = self._consume_new_data(new_data)
            if delta_content:
                result["content"] = delta_content
            if delta_reasoning:
                result["reasoning_content"] = delta_reasoning
            result["done"] = done

        except Exception as e:
            logger.debug(f"[ClaudeParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self) -> None:
        self._last_raw_length = 0
        self._last_raw_response = ""
        self._pending = ""

    def _consume_new_data(self, new_data: str) -> Tuple[str, str, bool]:
        # Browser stream snapshots can use either CRLF or lone CR line endings.
        normalized = (self._pending + new_data).replace("\r\n", "\n").replace("\r", "\n")
        if not normalized:
            return "", "", False

        blocks = normalized.split("\n\n")
        if normalized.endswith("\n\n"):
            self._pending = ""
            complete_blocks = [block for block in blocks if block.strip()]
        else:
            self._pending = blocks.pop() if blocks else normalized
            complete_blocks = [block for block in blocks if block.strip()]

        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        done = False

        for block in complete_blocks:
            block_content, block_reasoning, block_done = self._parse_event_block(block)
            if block_content:
                content_parts.append(block_content)
            if block_reasoning:
                reasoning_parts.append(block_reasoning)
            if block_done:
                done = True

        return "".join(content_parts), "".join(reasoning_parts), done

    def _parse_event_block(self, block: str) -> Tuple[str, str, bool]:
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
            return "", "", True

        payload = "\n".join(data_lines).strip()
        if not payload:
            return "", "", False

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "", "", False

        # Some response wrappers omit the SSE event line but preserve the
        # protocol event type in the JSON payload.
        if not event_name:
            event_name = str(data.get("type") or "").strip()

        if event_name == "message_stop":
            return "", "", True

        if event_name == "content_block_start":
            cb = data.get("content_block", {})
            if isinstance(cb, dict):
                cb_type = cb.get("type")
                if cb_type == "text" and isinstance(cb.get("text"), str):
                    return cb.get("text", ""), "", False
                elif cb_type == "thinking" and isinstance(cb.get("thinking"), str):
                    return "", cb.get("thinking", ""), False

        if event_name == "content_block_delta":
            delta = data.get("delta", {})
            if isinstance(delta, dict):
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    return (text, "", False) if isinstance(text, str) else ("", "", False)
                elif delta_type == "thinking_delta":
                    thinking_text = delta.get("thinking", "")
                    return ("", thinking_text, False) if isinstance(thinking_text, str) else ("", "", False)

                elif delta_type == "thinking_summary_delta":
                    summary_text = delta.get("summary", delta.get("thinking", ""))
                    return ("", summary_text, False) if isinstance(summary_text, str) else ("", "", False)

        return "", "", False

    @classmethod
    def get_id(cls) -> str:
        return "claude"

    @classmethod
    def get_name(cls) -> str:
        return "Claude"

    @classmethod
    def get_description(cls) -> str:
        return "Parse Claude SSE streams including Extended Thinking blocks"

    def should_require_explicit_done(self) -> bool:
        # Claude's stream object may report capture completion after an
        # intermediate snapshot; message_stop is the reliable protocol end.
        return True

    def should_wait_for_replacement_stream_on_incomplete_capture(self) -> bool:
        return True

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        # The conversation path is also used by /title and other JSON requests.
        # Match the actual SSE completion endpoint so auxiliary requests cannot
        # replace the active stream.
        return ["/completion"]


__all__ = ["ClaudeParser"]
