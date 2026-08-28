"""
lmarena_image_side_right_parser.py - Arena.ai image battle right-side parser.

Purpose:
- Keep the Arena image battle right/modelB stream channel.
- Fall back to DOM/image extraction quickly when the stream has no visible text.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.core.config import logger
from .base import ResponseParser
from .lmarena_parser import LmarenaParser, _CP1252_TO_LATIN1, _extract_arena_image_items


class LmarenaImageSideRightParser(ResponseParser):
    """Arena.ai image battle parser for the right/modelB channel."""

    def __init__(self) -> None:
        self._accumulated = ""
        self._seen_image_refs: set[str] = set()

    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "content": "",
            "images": [],
            "done": False,
            "error": None,
        }

        if isinstance(raw_response, (bytes, bytearray)):
            raw_response = raw_response.decode("utf-8", errors="ignore")

        if not raw_response or not isinstance(raw_response, str):
            return result

        raw_response = self._fix_mojibake(raw_response)

        try:
            content_parts: List[str] = []
            images: List[Dict[str, Any]] = []
            done = False

            for line in raw_response.split("\n"):
                line = line.strip()
                if not line:
                    continue

                colon_idx = line.find(":")
                if colon_idx < 1:
                    continue

                prefix = line[:colon_idx]
                payload = line[colon_idx + 1:]

                if prefix == "b0":
                    text = self._parse_text_chunk(payload)
                    if text is not None:
                        content_parts.append(text)
                elif prefix == "b2":
                    images.extend(
                        _extract_arena_image_items(
                            payload,
                            self._seen_image_refs,
                            source="lmarena_image_side_right_stream",
                        )
                    )
                elif prefix == "bd":
                    if self._is_finish_signal(payload):
                        done = True
                elif prefix in {"be", "b3"}:
                    error_msg = self._parse_error(payload)
                    if error_msg:
                        result["error"] = error_msg
                        done = True

            new_content = "".join(content_parts)
            if new_content:
                delta, next_accumulated = LmarenaParser._content_delta(
                    self._accumulated,
                    new_content,
                    append_disjoint=True,
                )
                if delta:
                    result["content"] = delta
                self._accumulated = next_accumulated

            if images:
                result["images"] = images
            result["done"] = done

        except Exception as e:
            logger.debug(f"[LmarenaImageSideRightParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self):
        self._accumulated = ""
        self._seen_image_refs.clear()

    def should_abort_on_error(self) -> bool:
        return True

    def should_fallback_to_dom_when_no_visible_content(self) -> bool:
        return True

    @staticmethod
    def _fix_mojibake(text: str) -> str:
        try:
            mapped = text.translate(_CP1252_TO_LATIN1)
            return mapped.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text

    @staticmethod
    def _parse_text_chunk(payload: str) -> str | None:
        try:
            value = json.loads(payload)
            if isinstance(value, str):
                return value
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    @staticmethod
    def _is_finish_signal(payload: str) -> bool:
        try:
            data = json.loads(payload)
            return isinstance(data, dict) and bool(data.get("finishReason"))
        except (json.JSONDecodeError, ValueError):
            return False

    @staticmethod
    def _parse_error(payload: str) -> str | None:
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                return data.get("message", str(data))
            return str(data)
        except (json.JSONDecodeError, ValueError):
            return payload.strip() if payload.strip() else None

    @classmethod
    def get_id(cls) -> str:
        return "lmarena_image_side_right"

    @classmethod
    def get_name(cls) -> str:
        return "Arena.ai Image Side Right"

    @classmethod
    def get_description(cls) -> str:
        return "Parse Arena.ai image battle stream, keep right(modelB) and fall back to DOM when text is absent"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return [
            "nextjs-api/stream/post-to-evaluation",
            "nextjs-api/stream/create-evaluation",
        ]


__all__ = ["LmarenaImageSideRightParser"]
