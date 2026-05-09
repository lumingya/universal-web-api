"""
mimo_parser.py - Xiaomi MiMo SSE response parser.

Observed stream traits:
- POST /open-apis/bot/chat?... returns text/event-stream
- answer chunks arrive as event:message with JSON data {"type":"text","content":"..."}
- event:finish with {"content":"[DONE]"} ends the stream
- reasoning text is wrapped inside <think>...</think> and should be ignored
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from app.core.config import logger
from .base import ResponseParser


class MimoParser(ResponseParser):
    """Parse Xiaomi MiMo SSE streams and ignore think-phase content."""

    _SUSPICIOUS_CHARS = set(
        "ÃÂâæåçèéêëìíîïðñòóôõöùúûüýþÿ"
        "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"
    )
    _REVERSE_CP1252_BYTES = {
        0x20AC: 0x80,
        0x201A: 0x82,
        0x0192: 0x83,
        0x201E: 0x84,
        0x2026: 0x85,
        0x2020: 0x86,
        0x2021: 0x87,
        0x02C6: 0x88,
        0x2030: 0x89,
        0x0160: 0x8A,
        0x2039: 0x8B,
        0x0152: 0x8C,
        0x017D: 0x8E,
        0x2018: 0x91,
        0x2019: 0x92,
        0x201C: 0x93,
        0x201D: 0x94,
        0x2022: 0x95,
        0x2013: 0x96,
        0x2014: 0x97,
        0x02DC: 0x98,
        0x2122: 0x99,
        0x0161: 0x9A,
        0x203A: 0x9B,
        0x0153: 0x9C,
        0x017E: 0x9E,
        0x0178: 0x9F,
    }

    def __init__(self) -> None:
        self._last_raw_length = 0
        self._pending = ""
        self._inside_think = False

    def reset(self) -> None:
        self._last_raw_length = 0
        self._pending = ""
        self._inside_think = False

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
            logger.debug(f"[MimoParser] parse exception: {e}")
            result["error"] = str(e)

        return result

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

        if event_name == "finish":
            return "", True
        if event_name != "message":
            return "", False

        payload = "\n".join(data_lines).strip()
        if not payload:
            return "", False

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "", False

        if not isinstance(data, dict):
            return "", False
        if str(data.get("type") or "").strip().lower() != "text":
            return "", False

        text = data.get("content", "")
        if not isinstance(text, str) or not text:
            return "", False

        visible_text = self._strip_think_content(text)
        visible_text = self._repair_mojibake_text(visible_text)
        return visible_text, False

    def _strip_think_content(self, text: str) -> str:
        if not text:
            return ""

        normalized = text.replace("\x00", "")
        visible_parts: List[str] = []
        cursor = 0

        while cursor < len(normalized):
            if self._inside_think:
                end_idx = normalized.find("</think>", cursor)
                if end_idx == -1:
                    return "".join(visible_parts)
                self._inside_think = False
                cursor = end_idx + len("</think>")
                continue

            start_idx = normalized.find("<think>", cursor)
            if start_idx == -1:
                visible_parts.append(normalized[cursor:])
                break

            if start_idx > cursor:
                visible_parts.append(normalized[cursor:start_idx])
            self._inside_think = True
            cursor = start_idx + len("<think>")

        return "".join(visible_parts)

    @classmethod
    def _count_cjk(cls, text: str) -> int:
        total = 0
        for ch in text or "":
            code = ord(ch)
            if (
                0x4E00 <= code <= 0x9FFF
                or 0x3400 <= code <= 0x4DBF
                or 0xF900 <= code <= 0xFAFF
            ):
                total += 1
        return total

    @classmethod
    def _count_suspicious(cls, text: str) -> int:
        return sum(1 for ch in (text or "") if ch in cls._SUSPICIOUS_CHARS)

    @classmethod
    def _is_byte_like_char(cls, ch: str) -> bool:
        code = ord(ch)
        return code in cls._REVERSE_CP1252_BYTES or 0 <= code <= 0xFF

    @classmethod
    def _has_high_byte_signal(cls, text: str) -> bool:
        for ch in text or "":
            code = ord(ch)
            if code in cls._REVERSE_CP1252_BYTES or 0x80 <= code <= 0xFF:
                return True
        return False

    @classmethod
    def _looks_like_utf8_mojibake(cls, text: str) -> bool:
        if not text or len(text) < 2:
            return False

        cjk = cls._count_cjk(text)
        if cjk == 0:
            return cls._has_high_byte_signal(text)

        suspicious = cls._count_suspicious(text)
        return suspicious >= 1 and cls._has_high_byte_signal(text)

    @classmethod
    def _quality_score(cls, text: str) -> tuple[int, int, int]:
        return (
            cls._count_cjk(text),
            -cls._count_suspicious(text),
            -(text.count("\ufffd") if text else 0),
        )

    @classmethod
    def _try_redecode(cls, text: str) -> str:
        try:
            payload = bytearray()
            for ch in text:
                code = ord(ch)
                if code in cls._REVERSE_CP1252_BYTES:
                    payload.append(cls._REVERSE_CP1252_BYTES[code])
                    continue
                if 0 <= code <= 0xFF:
                    payload.append(code)
                    continue
                return text
            return bytes(payload).decode("utf-8")
        except Exception:
            return text

    @classmethod
    def _repair_segmentwise(cls, text: str) -> str:
        if not text:
            return text

        pieces: List[str] = []
        buffer: List[str] = []

        def _flush_buffer() -> None:
            if not buffer:
                return
            segment = "".join(buffer)
            buffer.clear()

            if (
                len(segment) >= 2
                and cls._count_cjk(segment) == 0
                and cls._has_high_byte_signal(segment)
            ):
                candidate = cls._try_redecode(segment)
                if cls._quality_score(candidate) > cls._quality_score(segment):
                    pieces.append(candidate)
                    return
            pieces.append(segment)

        for ch in text:
            if cls._is_byte_like_char(ch):
                buffer.append(ch)
            else:
                _flush_buffer()
                pieces.append(ch)

        _flush_buffer()
        return "".join(pieces)

    @classmethod
    def _repair_mojibake_text(cls, text: str) -> str:
        if not text or not cls._looks_like_utf8_mojibake(text):
            return text

        best = text
        best_score = cls._quality_score(text)

        candidate = cls._try_redecode(text)
        if candidate != best:
            candidate_score = cls._quality_score(candidate)
            if candidate_score > best_score:
                best = candidate
                best_score = candidate_score

        candidate = cls._repair_segmentwise(best)
        if candidate != best:
            candidate_score = cls._quality_score(candidate)
            if candidate_score > best_score:
                best = candidate
                best_score = candidate_score

        return best

    @classmethod
    def get_id(cls) -> str:
        return "mimo"

    @classmethod
    def get_name(cls) -> str:
        return "Xiaomi MiMo"

    @classmethod
    def get_description(cls) -> str:
        return "Parse Xiaomi MiMo SSE streams and ignore think-phase text"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["**/open-apis/bot/chat**"]


__all__ = ["MimoParser"]
