"""
gemini_parser.py - Gemini StreamGenerate 响应解析器

响应格式特征
1. 安全前缀：        )]}'\n
2. 多块格式：        <len>\n[[JSON]]
3. 文本路径：        outer[0][2] ➜ json.loads ➜ inner[4][0][1][0]
4. 文本是“累积”模式：每块都返回从开头到当前的完整文本
5. 结束标志：        [["di",...]] / [["e",...]] / [["af.httprm",...]]
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import logger
from .base import ResponseParser


# ----------------------------------------------------------
# 私有工具
# ----------------------------------------------------------
_ESCAPE_FIXER = re.compile(r"\\([<>`#*_\[\]()])")  # 常见转义符号：HTML/CSS/Markdown
_EOL_FIXER = re.compile(r"\\\\n")                 # \\n  →  \n
_STANDALONE_BS = re.compile(r"\\\n")              # backslash + real LF
_REASONING_HINTS = (
    "i'm now ",
    "i am now ",
    "i've ",
    "i have ",
    "i must ",
    "i clarified",
    "i've clarified",
    "i have clarified",
    "user's request",
    "assistant persona",
    "brainstorm",
    "analyzing",
    "analysis",
    "focusing on",
    "clarifying",
    "refining",
    "crafting",
    "delivering",
    "defining",
    "prioritizing",
    "interpr",
)
_REASONING_PROCESS_HINTS = (
    "define",
    "defined",
    "defining",
    "develop",
    "developing",
    "draft",
    "drafting",
    "craft",
    "crafted",
    "crafting",
    "focus",
    "focusing",
    "clarif",
    "refin",
    "break",
    "breaking",
    "apply",
    "applying",
    "confirm",
    "confirming",
)
_REASONING_META_HINTS = (
    "task",
    "request",
    "user",
    "intent",
    "persona",
    "story",
    "parameter",
    "format",
    "constraint",
    "component",
    "narrative",
    "title",
    "scene",
    "style",
    "protagonist",
    "plot",
    "character",
    "dialogue",
    "ending",
    "climax",
    "structure",
)


def _clean_escaped(text: str) -> str:
    """
    Gemini 返回的字符串仍可能残留多余的反斜杠：
      1. \\<ctx\\>      →  <ctx>
      2. \\\\n          →  \n  →  换行
      3. \\`code\\`     →  `code`
      4. 反斜杠 + 真 \n  →  真 \n
    """
    if "\\" not in text:
        return text

    # 1) 处理 \n / \\n
    text = _STANDALONE_BS.sub("\n", text)   #  \ + 真换行
    text = _EOL_FIXER.sub(r"\n", text)      #  \\n → \n

    # 2) 去除转义符号前缀（只对 < > ` 三个常见 HTML 符号）
    text = _ESCAPE_FIXER.sub(r"\1", text)

    return text


# ----------------------------------------------------------
# 解析器主体
# ----------------------------------------------------------
class GeminiParser(ResponseParser):
    """
    Google Gemini StreamGenerate 响应解析器
    """

    def __init__(self) -> None:
        self._last_len = 0      # 已发送给上层的字符数
        self._full_cache = ""   # 最新完整文本

    # ---------- 对外接口 ---------- #
    def parse_chunk(self, raw: str | bytes) -> Dict[str, Any]:
        """
        解析 *单个* HTTP chunk，返回增量内容
        """
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")

        try:
            full_txt, done = self._parse(raw)
        except Exception as exc:  # pragma: no cover
            logger.debug(f"[GeminiParser] 解析异常: {exc}")
            return {"content": "", "images": [], "done": False, "error": str(exc)}

        delta = ""
        if full_txt is not None and len(full_txt) > self._last_len:
            delta = full_txt[self._last_len :]
            self._last_len = len(full_txt)
            self._full_cache = full_txt

        return {"content": delta, "images": [], "done": done, "error": None}

    def reset(self) -> None:
        self._last_len = 0
        self._full_cache = ""

    @staticmethod
    def _list_get(value: Any, index: int) -> Any:
        if isinstance(value, list) and 0 <= index < len(value):
            return value[index]
        return None

    @classmethod
    def _nested_list_get(cls, value: Any, *path: int) -> Any:
        current = value
        for index in path:
            current = cls._list_get(current, index)
            if current is None:
                return None
        return current

    @staticmethod
    def _looks_like_content(value: Any) -> bool:
        if not isinstance(value, str):
            return False

        text = value.strip()
        if not text:
            return False

        lowered = text.lower()
        if lowered.startswith(("r_", "c_", "rc_")) and len(text) <= 80:
            return False

        if text.startswith(("http://", "https://", "data:image", "blob:")):
            return True
        if "\n" in text or "\r" in text:
            return True
        if "<" in text and ">" in text:
            return True
        if any("\u4e00" <= ch <= "\u9fff" for ch in text):
            return True

        alpha_count = sum(ch.isalpha() for ch in text)
        return len(text) >= 16 and alpha_count >= 6

    @classmethod
    def _iter_content_candidates(cls, value: Any):
        if isinstance(value, str):
            if cls._looks_like_content(value):
                yield value
            return

        if isinstance(value, list):
            for item in value:
                yield from cls._iter_content_candidates(item)
            return

        if isinstance(value, dict):
            for item in value.values():
                yield from cls._iter_content_candidates(item)

    @staticmethod
    def _decode_content_text(content: str) -> str:
        try:
            decoded = json.loads(f'"{content}"')
            if isinstance(decoded, str):
                content = decoded
        except json.JSONDecodeError:
            content = _clean_escaped(content)
        return _clean_escaped(content)

    @staticmethod
    def _looks_like_reasoning(value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return False

        lowered = " ".join(text.lower().split())
        if not lowered:
            return False

        looks_like_structured_answer = text.startswith(
            ("{", "```json", "```", "<render", "<segment", "<kaitou", "<scene")
        ) or any(token in text for token in ('"role"', '"content"', '"tool_calls"', '"decision"'))

        if text.startswith("**") and any(hint in lowered for hint in _REASONING_HINTS):
            return True

        if (
            not looks_like_structured_answer
            and lowered.startswith(("i'm ", "i am ", "i've ", "i have "))
            and any(hint in lowered for hint in _REASONING_PROCESS_HINTS)
            and any(hint in lowered for hint in _REASONING_META_HINTS)
        ):
            return True

        return any(
            marker in lowered
            for marker in (
                "i'm now carefully",
                "i am now carefully",
                "i'm now focused",
                "i am now focused",
                "i've established",
                "i have established",
                "the user's compositional request",
                "the user's intent",
                "helpful assistant persona",
            )
        )

    @classmethod
    def _score_candidate(cls, value: str) -> int:
        text = str(value or "").strip()
        if not text:
            return -10_000

        score = 0
        lowered = text.lower()

        if text.startswith(("{", "```json", "```", "<render", "<segment", "<kaitou", "<scene")):
            score += 120
        if any(token in text for token in ('"role"', '"content"', '"tool_calls"', '"decision"', '"name"', '"description"')):
            score += 80
        if any("\u4e00" <= ch <= "\u9fff" for ch in text):
            score += 60
        if text.startswith(("http://", "https://", "data:image", "blob:")):
            score += 40
        if "\n" in text or "\r" in text:
            score += 10
        if len(text) >= 80:
            score += 10
        elif len(text) >= 24:
            score += 5

        if cls._looks_like_reasoning(text):
            score -= 400
        elif text.startswith("**"):
            score -= 30

        if lowered.startswith(("r_", "c_", "rc_")) and len(text) <= 80:
            score -= 200

        return score

    @classmethod
    def _select_best_candidate(cls, value: Any) -> Optional[str]:
        seen: set[str] = set()
        best_text: Optional[str] = None
        best_score: Optional[int] = None

        for candidate in cls._iter_content_candidates(value):
            decoded = cls._decode_content_text(candidate)
            normalized = decoded.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)

            score = cls._score_candidate(decoded)
            if (
                best_text is None
                or best_score is None
                or score > best_score
                or (score == best_score and len(decoded) > len(best_text))
            ):
                best_text = decoded
                best_score = score

        if best_text is None or best_score is None or best_score <= 0:
            return None

        return best_text

    @classmethod
    def _extract_direct_answer(cls, inner: Any) -> Optional[str]:
        content_block = cls._list_get(inner, 4)
        if not isinstance(content_block, list):
            return None

        best_text: Optional[str] = None
        best_score: Optional[int] = None

        for item in content_block:
            direct_payload = cls._list_get(item, 1)
            if direct_payload is None:
                continue

            candidate = cls._select_best_candidate(direct_payload)
            if not candidate:
                continue

            score = cls._score_candidate(candidate)
            if (
                best_text is None
                or best_score is None
                or score > best_score
                or (score == best_score and len(candidate) > len(best_text))
            ):
                best_text = candidate
                best_score = score

        return best_text

    # ---------- 内部逻辑 ---------- #
    def _parse(self, raw_text: str) -> Tuple[Optional[str], bool]:
        """
        解析 Gemini 的 *整段* HTTP body，返回 (完整文本, 是否结束)
        """
        clean = raw_text.lstrip(")]}'\n")
        lines = clean.split("\n")

        full_content: Optional[str] = None
        done = False
        i = 0
        while i < len(lines):
            meta = lines[i].strip()
            if not meta:               # 空行
                i += 1
                continue

            if meta.isdigit():         # 长度行
                if i + 1 >= len(lines):
                    break
                json_block = lines[i + 1]

                try:
                    outer = json.loads(json_block)
                except json.JSONDecodeError:
                    i += 2
                    continue

                if self._is_end_signal(outer):
                    done = True
                else:
                    content = self._extract_content(outer)
                    if content:
                        full_content = content
                i += 2
            else:
                i += 1

        return full_content, done

    @staticmethod
    def _is_end_signal(data: list) -> bool:
        """
        结束块格式：
          [["di", 123]]  /  [["e",...]]  /  [["af.httprm",...]]
        """
        if (
            isinstance(data, list)
            and data
            and isinstance(data[0], list)
            and data[0]
            and data[0][0] in ("di", "e", "af.httprm")
        ):
            return True
        return False

    # -------- 核心：提取 & 转义修复 -------- #
    def _extract_content(self, outer: list) -> Optional[str]:
        """
        outer → inner → content
        outer[0][2] 是 *字符串*，再 json.loads 得 inner
        inner[4][0][1][0] 是转义后的文本
        """
        try:
            first = outer[0]
            if not (isinstance(first, list) and len(first) >= 3 and first[0] == "wrb.fr"):
                return None

            inner_raw: str = first[2]
            if not isinstance(inner_raw, str):
                return None
            inner = json.loads(inner_raw)  # type: ignore[arg-type]

            return self._extract_direct_answer(inner)

        except Exception as exc:  # pragma: no cover
            logger.debug(f"[GeminiParser] 提取失败: {exc}")
            return None

    def should_fallback_to_dom_when_no_visible_content(self) -> bool:
        return True

    # ---------- 元数据 ---------- #
    @classmethod
    def get_id(cls) -> str:
        return "gemini"

    @classmethod
    def get_name(cls) -> str:
        return "Gemini StreamGenerate"

    @classmethod
    def get_description(cls) -> str:
        return "解析 Google Gemini 的 StreamGenerate 响应"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["StreamGenerate"]


__all__ = ["GeminiParser"]
