"""
lmarena_side_left_parser.py - Arena.ai side-by-side left-stream parser.

Protocol:
- Vercel AI SDK data stream lines: "{prefix}:{payload}"
- left(modelA): a0/ad/ae
- right(modelB): b0/bd/be (ignored here)
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.core.config import logger
from .base import ResponseParser
from .lmarena_parser import _CP1252_TO_LATIN1


class LmarenaSideLeftParser(ResponseParser):
    """
    Parse Arena.ai side-by-side stream and keep only left/modelA channel.

    This parser is intended for side mode where the left model is the target
    (for example gemini-3.1-pro-preview in your setup).
    """

    def __init__(self) -> None:
        self._accumulated = ""

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

                # left/modelA text delta
                if prefix == "a0":
                    text = self._parse_text_chunk(payload)
                    if text is not None:
                        content_parts.append(text)
                # left/modelA done
                elif prefix == "ad":
                    if self._is_finish_signal(payload):
                        done = True
                # left/modelA error frames (observed formats: ae / a3)
                elif prefix in {"ae", "a3"}:
                    error_msg = self._parse_error(payload)
                    if error_msg:
                        result["error"] = error_msg
                        done = True

            new_content = "".join(content_parts)
            if new_content:
                if self._accumulated and new_content == self._accumulated:
                    logger.debug("[LmarenaSideLeftParser] duplicate full response ignored")
                elif self._accumulated and new_content.startswith(self._accumulated):
                    result["content"] = new_content[len(self._accumulated):]
                    self._accumulated = new_content
                else:
                    result["content"] = new_content
                    self._accumulated = new_content

            result["done"] = done

        except Exception as e:
            logger.debug(f"[LmarenaSideLeftParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self):
        self._accumulated = ""

    def should_abort_on_error(self) -> bool:
        # Side-by-side left mode only trusts the left stream. If the left channel
        # itself reports an error, we should fail fast instead of silently
        # falling back to whole-page DOM heuristics.
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
        return "lmarena_side_left"

    @classmethod
    def get_name(cls) -> str:
        return "Arena.ai Side Left"

    @classmethod
    def get_description(cls) -> str:
        return "Parse Arena.ai side-by-side stream, keep left(modelA) only"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["nextjs-api/stream/create-evaluation"]


__all__ = ["LmarenaSideLeftParser"]
