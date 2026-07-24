"""
lmarena_battle_side_parser.py - Arena.ai battle mode parsers.

The side parsers stream one selected side and finish when both battle sides are
terminal. The winner parser buffers both sides and emits only the first
completed side while still waiting for the battle response to finish normally.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.core.config import logger
from .base import ResponseParser
from .lmarena_parser import LmarenaParser, _CP1252_TO_LATIN1, _extract_arena_image_items


class _LmarenaBattleSideParser(ResponseParser):
    """Shared implementation for left/right Arena battle text streams."""

    SIDE_PREFIX = "a"
    SIDE_LABEL = "left"

    def __init__(self) -> None:
        self._accumulated = ""
        self._accumulated_reasoning = ""
        self._seen_image_refs: set[str] = set()
        self._completed_sides: set[str] = set()
        self._completed = False
        self._completion_side = ""

    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "content": "",
            "reasoning_content": "",
            "images": [],
            "done": False,
            "error": None,
            "selected_side": self.SIDE_LABEL,
            "completion_side": self._completion_side,
        }

        if self._completed:
            result["done"] = True
            return result

        if isinstance(raw_response, (bytes, bytearray)):
            raw_response = raw_response.decode("utf-8", errors="ignore")

        if not raw_response or not isinstance(raw_response, str):
            return result

        raw_response = self._fix_mojibake(raw_response)

        try:
            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            images: List[Dict[str, Any]] = []

            for line in raw_response.split("\n"):
                line = line.strip()
                if not line:
                    continue

                colon_idx = line.find(":")
                if colon_idx < 1:
                    continue

                prefix = line[:colon_idx]
                payload = line[colon_idx + 1:]

                if prefix == f"{self.SIDE_PREFIX}0":
                    text = self._parse_text_chunk(payload)
                    if text is not None:
                        content_parts.append(text)
                elif prefix == f"{self.SIDE_PREFIX}g":
                    text = self._parse_text_chunk(payload)
                    if text is not None:
                        reasoning_parts.append(text)
                elif prefix == f"{self.SIDE_PREFIX}2":
                    images.extend(
                        _extract_arena_image_items(
                            payload,
                            self._seen_image_refs,
                            source=f"lmarena_battle_side_{self.SIDE_LABEL}_stream",
                        )
                    )
                elif prefix in {"ad", "bd"}:
                    if self._is_finish_signal(payload):
                        self._mark_terminal(prefix)
                elif prefix in {"ae", "be", "a3", "b3"}:
                    error_msg = self._parse_error(payload)
                    if error_msg:
                        completion_side = self._mark_terminal(prefix)
                        logger.debug(
                            "[LmarenaBattleSideParser] stream error frame treated as completion "
                            f"(selected={self.SIDE_LABEL}, side={completion_side}, error={error_msg[:160]})"
                        )

            new_content = "".join(content_parts)
            new_reasoning = "".join(reasoning_parts)

            if new_content:
                delta, next_accumulated = LmarenaParser._content_delta(
                    self._accumulated,
                    new_content,
                    append_disjoint=True,
                )
                if self._accumulated and not delta:
                    logger.debug(
                        f"[LmarenaBattleSideParser] duplicate {self.SIDE_LABEL} response ignored"
                    )
                else:
                    result["content"] = delta
                self._accumulated = next_accumulated

            if new_reasoning:
                delta_r, next_acc_r = LmarenaParser._content_delta(
                    self._accumulated_reasoning,
                    new_reasoning,
                    append_disjoint=True,
                )
                if self._accumulated_reasoning and not delta_r:
                    logger.debug(
                        f"[LmarenaBattleSideParser] duplicate {self.SIDE_LABEL} reasoning response ignored"
                    )
                else:
                    result["reasoning_content"] = delta_r
                self._accumulated_reasoning = next_acc_r

            if images:
                result["images"] = images

            result["completion_side"] = self._completion_side

            if len(self._completed_sides) >= 2:
                self._completed = True
                result["done"] = True
                result["completion_side"] = self._completion_side

        except Exception as e:
            logger.debug(f"[LmarenaBattleSideParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self):
        self._accumulated = ""
        self._accumulated_reasoning = ""
        self._seen_image_refs.clear()
        self._completed_sides.clear()
        self._completed = False
        self._completion_side = ""

    def _mark_terminal(self, prefix: str) -> str:
        side = "left" if prefix.startswith("a") else "right"
        self._completed_sides.add(side)
        if not self._completion_side:
            self._completion_side = side
        return side

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
    def get_supported_patterns(cls) -> List[str]:
        return ["nextjs-api/stream/create-evaluation"]


class LmarenaBattleWinnerParser(ResponseParser):
    """Emit only the first completed Arena battle side, without streaming."""

    def __init__(self) -> None:
        self._buffers = {"left": "", "right": ""}
        self._reasoning_buffers = {"left": "", "right": ""}
        self._seen_image_refs = {"left": set(), "right": set()}
        self._images = {"left": [], "right": []}
        self._completed_sides: set[str] = set()
        self._completed = False
        self._winner_side = ""
        self._emitted_winner = False

    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "content": "",
            "reasoning_content": "",
            "images": [],
            "done": False,
            "error": None,
            "selected_side": "winner",
            "winner_side": self._winner_side,
        }

        if self._completed:
            result["done"] = True
            return result

        if isinstance(raw_response, (bytes, bytearray)):
            raw_response = raw_response.decode("utf-8", errors="ignore")

        if not raw_response or not isinstance(raw_response, str):
            return result

        raw_response = _LmarenaBattleSideParser._fix_mojibake(raw_response)

        try:
            content_parts = {"left": [], "right": []}
            reasoning_parts = {"left": [], "right": []}

            for line in raw_response.split("\n"):
                line = line.strip()
                if not line:
                    continue

                colon_idx = line.find(":")
                if colon_idx < 1:
                    continue

                prefix = line[:colon_idx]
                payload = line[colon_idx + 1:]

                if prefix == "a0":
                    text = _LmarenaBattleSideParser._parse_text_chunk(payload)
                    if text is not None:
                        content_parts["left"].append(text)
                elif prefix == "b0":
                    text = _LmarenaBattleSideParser._parse_text_chunk(payload)
                    if text is not None:
                        content_parts["right"].append(text)
                elif prefix == "ag":
                    text = _LmarenaBattleSideParser._parse_text_chunk(payload)
                    if text is not None:
                        reasoning_parts["left"].append(text)
                elif prefix == "bg":
                    text = _LmarenaBattleSideParser._parse_text_chunk(payload)
                    if text is not None:
                        reasoning_parts["right"].append(text)
                elif prefix == "a2":
                    self._images["left"].extend(
                        _extract_arena_image_items(
                            payload,
                            self._seen_image_refs["left"],
                            source="lmarena_battle_winner_left_stream",
                        )
                    )
                elif prefix == "b2":
                    self._images["right"].extend(
                        _extract_arena_image_items(
                            payload,
                            self._seen_image_refs["right"],
                            source="lmarena_battle_winner_right_stream",
                        )
                    )
                elif prefix in {"ad", "bd"}:
                    if _LmarenaBattleSideParser._is_finish_signal(payload):
                        self._mark_terminal(prefix)
                elif prefix in {"ae", "be", "a3", "b3"}:
                    error_msg = _LmarenaBattleSideParser._parse_error(payload)
                    if error_msg:
                        winner_side = self._mark_terminal(prefix)
                        logger.debug(
                            "[LmarenaBattleWinnerParser] stream error frame treated as completion "
                            f"(winner={winner_side}, error={error_msg[:160]})"
                        )

            for side, parts in content_parts.items():
                self._merge_text(side, "".join(parts))

            for side, parts in reasoning_parts.items():
                self._merge_reasoning(side, "".join(parts))

            if self._winner_side and not self._emitted_winner:
                self._emitted_winner = True
                result["content"] = self._buffers[self._winner_side]
                result["reasoning_content"] = self._reasoning_buffers[self._winner_side]
                result["images"] = self._images[self._winner_side]
                result["winner_side"] = self._winner_side

            if len(self._completed_sides) >= 2:
                self._completed = True
                result["done"] = True
                result["winner_side"] = self._winner_side

        except Exception as e:
            logger.debug(f"[LmarenaBattleWinnerParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def _merge_text(self, side: str, text: str) -> None:
        if not text:
            return

        current = self._buffers[side]
        _delta, next_accumulated = LmarenaParser._content_delta(
            current,
            text,
            append_disjoint=True,
        )
        self._buffers[side] = next_accumulated

    def _merge_reasoning(self, side: str, text: str) -> None:
        if not text:
            return

        current = self._reasoning_buffers[side]
        _delta, next_accumulated = LmarenaParser._content_delta(
            current,
            text,
            append_disjoint=True,
        )
        self._reasoning_buffers[side] = next_accumulated

    def reset(self):
        self._buffers = {"left": "", "right": ""}
        self._reasoning_buffers = {"left": "", "right": ""}
        self._seen_image_refs = {"left": set(), "right": set()}
        self._images = {"left": [], "right": []}
        self._completed_sides.clear()
        self._completed = False
        self._winner_side = ""
        self._emitted_winner = False
        self._completed_sides.clear()
        self._completed = False
        self._winner_side = ""
        self._emitted_winner = False

    def _mark_terminal(self, prefix: str) -> str:
        side = "left" if prefix.startswith("a") else "right"
        self._completed_sides.add(side)
        if not self._winner_side:
            self._winner_side = side
        return side

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["nextjs-api/stream/create-evaluation"]

    @classmethod
    def get_id(cls) -> str:
        return "lmarena_battle_winner"

    @classmethod
    def get_name(cls) -> str:
        return "Arena.ai Battle Winner"

    @classmethod
    def get_description(cls) -> str:
        return "Buffer both Arena.ai battle sides and emit only the first completed response"


class LmarenaBattleSideLeftParser(_LmarenaBattleSideParser):
    """Stream left/modelA text and finish when both battle sides finish."""

    SIDE_PREFIX = "a"
    SIDE_LABEL = "left"

    @classmethod
    def get_id(cls) -> str:
        return "lmarena_battle_side_left"

    @classmethod
    def get_name(cls) -> str:
        return "Arena.ai Battle Side Left"

    @classmethod
    def get_description(cls) -> str:
        return "Stream Arena.ai battle left/modelA text; finish when both sides complete"


class LmarenaBattleSideRightParser(_LmarenaBattleSideParser):
    """Stream right/modelB text and finish when both battle sides finish."""

    SIDE_PREFIX = "b"
    SIDE_LABEL = "right"

    @classmethod
    def get_id(cls) -> str:
        return "lmarena_battle_side_right"

    @classmethod
    def get_name(cls) -> str:
        return "Arena.ai Battle Side Right"

    @classmethod
    def get_description(cls) -> str:
        return "Stream Arena.ai battle right/modelB text; finish when both sides complete"


__all__ = [
    "LmarenaBattleWinnerParser",
    "LmarenaBattleSideLeftParser",
    "LmarenaBattleSideRightParser",
]
