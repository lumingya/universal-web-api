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
        self._has_seen_answer_text = False
        self._accumulated_reasoning = ""

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
            logger.debug(f"[QwenParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self) -> None:
        self._last_raw_length = 0
        self._last_raw_response = ""
        self._pending = ""
        self._has_seen_answer_text = False
        self._accumulated_reasoning = ""

    def _consume_new_data(self, new_data: str) -> Tuple[str, str, bool]:
        normalized = (self._pending + new_data).replace("\r\n", "\n")
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

        new_reasoning = "\n\n".join(reasoning_parts)
        delta_reasoning = ""
        if new_reasoning:
            delta_reasoning, self._accumulated_reasoning = self._reasoning_delta(
                self._accumulated_reasoning, new_reasoning
            )

        return "".join(content_parts), delta_reasoning, done

    @staticmethod
    def _reasoning_delta(accumulated: str, candidate: str) -> Tuple[str, str]:
        if not candidate:
            return "", accumulated
        if not accumulated:
            return candidate, candidate
        if candidate == accumulated or accumulated.startswith(candidate):
            return "", accumulated
        if candidate.startswith(accumulated):
            return candidate[len(accumulated):], candidate

        max_overlap = min(len(accumulated), len(candidate))
        for overlap in range(max_overlap, 0, -1):
            if accumulated.endswith(candidate[:overlap]):
                delta = candidate[overlap:]
                return delta, accumulated + delta

        return candidate, accumulated + candidate

    def _parse_event_block(self, block: str) -> Tuple[str, str, bool]:
        data_lines: List[str] = []

        for raw_line in block.split("\n"):
            line = raw_line.strip()
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())

        payload = "\n".join(data_lines).strip()
        if not payload:
            return "", "", False

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "", "", False

        choices = data.get("choices")
        if not isinstance(choices, list):
            return "", "", False

        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        done = False

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            phase = delta.get("phase")

            if phase == "thinking_summary":
                extra = delta.get("extra")
                if isinstance(extra, dict):
                    summary_thought = extra.get("summary_thought")
                    if isinstance(summary_thought, dict):
                        thought_list = summary_thought.get("content")
                        if isinstance(thought_list, list):
                            reasoning_parts.append("\n\n".join([str(x) for x in thought_list if x]))

            elif phase == "answer":
                text = delta.get("content", "")
                if isinstance(text, str) and text:
                    content_parts.append(text)
                    self._has_seen_answer_text = True

                if delta.get("status") == "finished" and self._has_seen_answer_text:
                    done = True

        return "".join(content_parts), "\n\n".join(reasoning_parts), done

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
