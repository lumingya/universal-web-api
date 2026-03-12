"""
kimi_parser.py - Kimi Connect streaming response parser.

Observed stream traits:
- content-type: application/connect+json
- each message is framed as 1 flag byte + 4-byte big-endian payload length
- assistant text starts with op=set, mask=block.text
- later deltas use op=append, mask=block.text.content
- {"done": {}} or MESSAGE_STATUS_COMPLETED ends the stream
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Set, Tuple

from app.core.config import logger
from .base import ResponseParser


class KimiParser(ResponseParser):
    """Parse Kimi connect+json streaming responses."""

    def __init__(self) -> None:
        self._last_raw = b""
        self._pending = b""
        self._seen_offsets: Set[int] = set()

    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "content": "",
            "images": [],
            "done": False,
            "error": None,
        }

        try:
            raw_bytes = self._normalize_raw_bytes(raw_response)
            if not raw_bytes:
                return result

            if self._last_raw and raw_bytes == self._last_raw:
                return result

            if self._last_raw and raw_bytes.startswith(self._last_raw):
                new_data = raw_bytes[len(self._last_raw):]
            else:
                new_data = raw_bytes

            self._last_raw = raw_bytes

            delta_content, done = self._consume_new_data(new_data)
            if delta_content:
                result["content"] = delta_content
            result["done"] = done

        except Exception as e:
            logger.debug(f"[KimiParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self) -> None:
        self._last_raw = b""
        self._pending = b""
        self._seen_offsets.clear()

    @staticmethod
    def _normalize_raw_bytes(raw_response: Any) -> bytes:
        if isinstance(raw_response, bytes):
            return raw_response
        if isinstance(raw_response, bytearray):
            return bytes(raw_response)
        if isinstance(raw_response, str):
            if "\\u00" in raw_response and "\x00" not in raw_response:
                return KimiParser._decode_u00_escaped_bytes(raw_response)
            return raw_response.encode("utf-8", errors="ignore")
        return str(raw_response).encode("utf-8", errors="ignore")

    @staticmethod
    def _decode_u00_escaped_bytes(raw_response: str) -> bytes:
        output = bytearray()
        i = 0

        while i < len(raw_response):
            if (
                raw_response[i] == "\\"
                and i + 5 < len(raw_response)
                and raw_response[i + 1] == "u"
                and raw_response[i + 2:i + 4] == "00"
            ):
                hex_part = raw_response[i + 4:i + 6]
                if all(ch in "0123456789abcdefABCDEF" for ch in hex_part):
                    output.append(int(hex_part, 16))
                    i += 6
                    continue

            output.extend(raw_response[i].encode("utf-8", errors="ignore"))
            i += 1

        return bytes(output)

    def _consume_new_data(self, new_data: bytes) -> Tuple[str, bool]:
        self._pending += new_data

        content_parts: List[str] = []
        done = False

        while True:
            frame = self._next_frame()
            if frame is None:
                break

            payload_text, frame_done = frame
            if payload_text:
                content_parts.append(payload_text)
            if frame_done:
                done = True

        return "".join(content_parts), done

    def _next_frame(self) -> Tuple[str, bool] | None:
        if len(self._pending) < 5:
            return None

        length = int.from_bytes(self._pending[1:5], byteorder="big", signed=False)

        if length < 0:
            self._pending = self._pending[1:]
            return "", False

        total_length = 5 + length
        if len(self._pending) < total_length:
            return None

        payload = self._pending[5:total_length]
        self._pending = self._pending[total_length:]

        return self._parse_payload(payload)

    def _parse_payload(self, payload: bytes) -> Tuple[str, bool]:
        payload_text = payload.decode("utf-8", errors="ignore").strip()
        if not payload_text:
            return "", False

        try:
            data = json.loads(payload_text)
        except json.JSONDecodeError:
            return "", False

        if not isinstance(data, dict):
            return "", False

        if "done" in data:
            return "", True

        if "heartbeat" in data:
            return "", False

        event_offset = data.get("eventOffset")
        if isinstance(event_offset, int):
            if event_offset in self._seen_offsets:
                return "", False
            self._seen_offsets.add(event_offset)

        op = data.get("op")
        mask = data.get("mask", "")

        if op == "set" and mask == "block.text":
            return self._extract_block_text(data.get("block")), False

        if op == "append" and mask == "block.text.content":
            return self._extract_block_text(data.get("block")), False

        if op == "set" and mask == "message.status":
            message = data.get("message", {})
            if isinstance(message, dict) and message.get("status") == "MESSAGE_STATUS_COMPLETED":
                return "", True

        return "", False

    @staticmethod
    def _extract_block_text(block: Any) -> str:
        if not isinstance(block, dict):
            return ""

        text_data = block.get("text")
        if not isinstance(text_data, dict):
            return ""

        content = text_data.get("content", "")
        return content if isinstance(content, str) else ""

    @classmethod
    def get_id(cls) -> str:
        return "kimi"

    @classmethod
    def get_name(cls) -> str:
        return "Kimi"

    @classmethod
    def get_description(cls) -> str:
        return "Parse Kimi connect+json streams and keep assistant text deltas"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["/apiv2/kimi.gateway.chat.v1.ChatService/Chat", "/chat", "chat"]


__all__ = ["KimiParser"]
