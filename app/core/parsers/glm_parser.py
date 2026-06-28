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
import re
from typing import Any, Dict, List

from app.core.config import logger
from .base import ResponseParser


class GLMParser(ResponseParser):
    """Parse ChatGLM SSE streams while ignoring think/tool-call noise."""

    _TERMINAL_STATUSES = {"finish", "finished", "intervene", "intervened"}
    _HIDDEN_REPAIR_TEXT_PATTERNS = (
        "xml-style tool call",
        "could not be parsed into a valid declared tool",
        "could not be parsed into valid tool_calls",
        "validation errors",
        "the rejected assistant reply was",
    )
    _THINK_REASONING_PREFIX_RE = re.compile(
        r"^\s*(let me\b|looking at\b|the user has\b|让我|我来|先分析|仔细分析)",
        re.IGNORECASE,
    )
    _EMBEDDED_REPLY_LABEL_RE = re.compile(
        r"(?:the rejected assistant reply was|the rejected response was|被拒绝的响应是|被拒绝的助手回复是)\s*[:：]?\s*```",
        re.IGNORECASE,
    )
    _VISIBLE_MARKERS = ("<render", "<segment", "<context_summary", "<q>", "<act>", "<inner>", "<aside>", "<meme")

    def __init__(self) -> None:
        self._last_raw_length = 0
        self._pending = ""
        self._rendered_text = ""
        self._think_text = ""
        self._has_seen_visible_text = False
        self._saw_hidden_repair_flow = False
        self._saw_only_think_payloads = False

    def reset(self) -> None:
        self._last_raw_length = 0
        self._last_raw_response = ""
        self._pending = ""
        self._rendered_text = ""
        self._think_text = ""
        self._has_seen_visible_text = False
        self._saw_hidden_repair_flow = False
        self._saw_only_think_payloads = False

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

            new_data = self._prepare_incremental_raw_response(raw_response)
            if not new_data:
                return result

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
        if intervene_text and self._looks_like_hidden_repair_text(intervene_text):
            intervene_text = ""
        parts = data.get("parts")
        if not isinstance(parts, list) or not parts:
            if intervene_text and top_status in self._TERMINAL_STATUSES:
                delta = self._compute_delta(self._rendered_text, intervene_text)
                self._rendered_text = intervene_text
                if intervene_text:
                    self._has_seen_visible_text = True
                return delta, True
            return "", bool(self._has_seen_visible_text and top_status in self._TERMINAL_STATUSES)

        visible_snapshot = self._rendered_text
        saw_visible_text_in_payload = False
        saw_think_in_payload = False
        done = bool(self._has_seen_visible_text and top_status in self._TERMINAL_STATUSES)

        for part in parts:
            if not isinstance(part, dict):
                continue

            part_status = str(part.get("status") or "").strip().lower()

            content_items = part.get("content")
            if not isinstance(content_items, list):
                continue

            for item in content_items:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "think":
                    think_text = str(item.get("think") or self._think_text)
                    self._think_text = think_text
                    saw_think_in_payload = True
                    embedded_snapshot = self._extract_embedded_visible_snapshot(think_text)
                    if embedded_snapshot:
                        visible_snapshot = embedded_snapshot
                        saw_visible_text_in_payload = True
                        self._has_seen_visible_text = True
                    if self._looks_like_hidden_repair_text(think_text):
                        self._saw_hidden_repair_flow = True
                    continue
                if item_type == "tool_calls":
                    continue
                if item_type != "text":
                    continue

                snapshot = str(item.get("text") or "")
                if snapshot:
                    visible_snapshot = snapshot
                    saw_visible_text_in_payload = True
                    self._has_seen_visible_text = True
                if part_status in self._TERMINAL_STATUSES and (snapshot or self._has_seen_visible_text):
                    done = True

        if saw_think_in_payload and not saw_visible_text_in_payload:
            self._saw_only_think_payloads = True

        if intervene_text and done and not visible_snapshot:
            visible_snapshot = intervene_text

        delta = self._compute_delta(self._rendered_text, visible_snapshot)
        self._rendered_text = visible_snapshot
        return delta, done

    def should_fallback_to_dom_when_no_visible_content(self) -> bool:
        return bool(
            not self._has_seen_visible_text
            and (self._saw_hidden_repair_flow or self._saw_only_think_payloads)
        )

    @classmethod
    def _looks_like_hidden_repair_text(cls, text: Any) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False

        lowered = normalized.lower()
        if any(marker in lowered for marker in cls._HIDDEN_REPAIR_TEXT_PATTERNS):
            return True

        if cls._THINK_REASONING_PREFIX_RE.search(normalized):
            if "<segment>" in normalized or "<context_summary>" in normalized:
                return True
            if "\\u003csegment\\u003e" in normalized or "\\u003ccontext_summary\\u003e" in normalized:
                return True

        return False

    def export_debug_data(self, raw_response: str = "") -> Dict[str, Any]:
        return {
            "has_seen_visible_text": bool(self._has_seen_visible_text),
            "rendered_text_len": len(self._rendered_text or ""),
            "think_text_len": len(self._think_text or ""),
            "saw_hidden_repair_flow": bool(self._saw_hidden_repair_flow),
            "saw_only_think_payloads": bool(self._saw_only_think_payloads),
        }

    @classmethod
    def _extract_embedded_visible_snapshot(cls, text: Any) -> str:
        normalized = str(text or "")
        if not normalized:
            return ""

        match = cls._EMBEDDED_REPLY_LABEL_RE.search(normalized)
        if not match:
            return ""

        body = normalized[match.end():]
        if not body:
            return ""

        fence_end = body.find("```")
        candidate = body[:fence_end] if fence_end >= 0 else body
        candidate = candidate.strip()
        if not candidate:
            return ""

        lowered = candidate.lower()
        if lowered.startswith("<think"):
            return ""
        if any(marker in lowered for marker in cls._HIDDEN_REPAIR_TEXT_PATTERNS):
            return ""
        if not any(marker in lowered for marker in cls._VISIBLE_MARKERS):
            return ""
        return candidate

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
