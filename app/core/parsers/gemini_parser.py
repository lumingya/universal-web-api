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
_GEMINI_GENERATED_MEDIA_URL_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?(?:googleusercontent\.com|lh3\.googleusercontent\.com)/[^\s\"'<>\]\)\}\\]+",
    re.IGNORECASE,
)
_GEMINI_PLACEHOLDER_MEDIA_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?googleusercontent\.com/(?:image_generation_content|generated_music_content)/\d+",
    re.IGNORECASE,
)
_RENDER_START_RE = re.compile(r"<\s*render\b", re.IGNORECASE)
_RENDER_CLOSE_RE = re.compile(r"<\s*/\s*render\s*>", re.IGNORECASE)
_MEDIA_SCAN_TAIL_CHARS = 4096
_MEDIA_SCAN_DELIMITERS = (" ", "\n", "\r", "\t", '"', "'", "<", ">", "\\")


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
        self._last_raw_length = 0
        self._pending_raw = ""
        self._seen_media_refs: set[str] = set()
        self._media_scan_tail = ""

    # ---------- 对外接口 ---------- #
    def parse_chunk(self, raw: str | bytes) -> Dict[str, Any]:
        """
        解析 *单个* HTTP chunk，返回增量内容
        """
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        elif not isinstance(raw, str):
            raw = str(raw)

        try:
            previous_len = int(getattr(self, "_last_raw_length", 0) or 0)
            previous_raw = getattr(self, "_last_raw_response", "")
            will_reset = bool(previous_len and previous_raw and not raw.startswith(previous_raw))
            is_stream_start = (previous_len == 0 and not self._pending_raw) or will_reset
            new_data = self._prepare_incremental_raw_response(raw)
            if not new_data:
                return {
                    "content": "",
                    "images": [],
                    "done": False,
                    "error": None,
                    "unclosed_render_output": self._has_unclosed_render_output(
                        self._full_cache
                    ),
                }

            full_txt, done = self._parse_incremental(
                new_data,
                is_stream_start=is_stream_start,
            )
            images = self._extract_generated_images_from_increment(new_data)
        except Exception as exc:  # pragma: no cover
            logger.debug(f"[GeminiParser] 解析异常: {exc}")
            return {"content": "", "images": [], "done": False, "error": str(exc)}

        delta = ""
        if full_txt is not None and len(full_txt) > self._last_len:
            delta = full_txt[self._last_len :]
            self._last_len = len(full_txt)
            self._full_cache = full_txt

        return {
            "content": delta,
            "images": images,
            "done": done,
            "error": None,
            "unclosed_render_output": self._has_unclosed_render_output(
                self._full_cache
            ),
        }

    def reset(self) -> None:
        self._last_len = 0
        self._full_cache = ""
        self._last_raw_length = 0
        self._last_raw_response = ""
        self._pending_raw = ""
        self._seen_media_refs.clear()
        self._media_scan_tail = ""

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
    def _has_unclosed_render_output(text: str) -> bool:
        text = str(text or "")
        if not text:
            return False

        stripped = text.lstrip()
        if not _RENDER_START_RE.match(stripped):
            return False

        render_starts = list(_RENDER_START_RE.finditer(text))
        if not render_starts:
            return False

        render_closes = list(_RENDER_CLOSE_RE.finditer(text))
        last_open_at = render_starts[-1].start()
        last_close_at = render_closes[-1].start() if render_closes else -1
        return last_open_at > last_close_at

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
    def _parse_incremental(
        self,
        raw_text: str,
        *,
        is_stream_start: bool = False,
    ) -> Tuple[Optional[str], bool]:
        """
        解析 Gemini 的 *增量* HTTP body 片段，返回 (完整文本, 是否结束)
        """
        combined = f"{self._pending_raw}{str(raw_text or '')}"
        if not combined:
            return None, False

        # 仅在全新流的首个片段上清理安全前缀。
        if is_stream_start:
            combined = combined.lstrip(")]}'\n")

        lines = combined.splitlines(keepends=True)
        full_content: Optional[str] = None
        done = False
        pending_start = len(combined)
        i = 0
        cursor = 0
        while i < len(lines):
            line = lines[i]
            line_start = cursor
            line_end = line_start + len(line)
            meta = line.strip()
            if not meta:               # 空行
                cursor = line_end
                i += 1
                continue

            if meta.isdigit():         # 长度行
                if i + 1 >= len(lines):
                    pending_start = line_start
                    break
                json_block = lines[i + 1].rstrip("\r\n")

                try:
                    outer = json.loads(json_block)
                except json.JSONDecodeError:
                    pending_start = line_start
                    break

                if self._is_end_signal(outer):
                    done = True
                else:
                    content = self._extract_content(outer)
                    if content:
                        full_content = content
                cursor = line_end + len(lines[i + 1])
                i += 2
            else:
                cursor = line_end
                i += 1

        self._pending_raw = combined[pending_start:] if pending_start < len(combined) else ""
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

    def get_media_generation_state(
        self,
        raw_response: str = "",
        parse_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raw_text = str(raw_response or "")
        if not raw_text:
            return {}

        parse_images = parse_result.get("images") if isinstance(parse_result, dict) else None
        if parse_images or self._seen_media_refs or self._has_generated_image(raw_text):
            return {}

        hint_parts: List[str] = []
        lowered = raw_text.lower()
        wait_timeout_seconds = None

        if "nano banana" in lowered:
            hint_parts.append("正在加载 Nano Banana 2...")
            wait_timeout_seconds = max(float(wait_timeout_seconds or 0), 90.0)

        if "image_generation_content/" in lowered:
            hint_parts.append("image_generation_content")
            wait_timeout_seconds = max(float(wait_timeout_seconds or 0), 90.0)

        if not hint_parts:
            return {}

        deduped_hint_parts: List[str] = []
        seen = set()
        for item in hint_parts:
            if item in seen:
                continue
            seen.add(item)
            deduped_hint_parts.append(item)

        return {
            "pending": True,
            "media_type": "image",
            "hint_text": "\n\n".join(deduped_hint_parts[:3]),
            "wait_timeout_seconds": wait_timeout_seconds,
        }

    @staticmethod
    def _build_media_scan_tail(raw_text: str) -> str:
        text = str(raw_text or "")
        if not text:
            return ""

        last_delimiter = -1
        for delimiter in _MEDIA_SCAN_DELIMITERS:
            last_delimiter = max(last_delimiter, text.rfind(delimiter))

        tail = text[last_delimiter + 1:] if last_delimiter >= 0 else text
        if len(tail) > _MEDIA_SCAN_TAIL_CHARS:
            tail = tail[-_MEDIA_SCAN_TAIL_CHARS:]
        return tail

    @classmethod
    def _iter_generated_image_urls(cls, raw_response: str):
        raw_text = str(raw_response or "")
        if not raw_text:
            return

        for match in _GEMINI_GENERATED_MEDIA_URL_RE.finditer(raw_text):
            url = str(match.group(0) or "").strip().rstrip(".,;:!?")
            if not url:
                continue
            if _GEMINI_PLACEHOLDER_MEDIA_RE.fullmatch(url):
                continue
            lowered = url.lower()
            if "/gg/" not in lowered and "/gg-dl/" not in lowered:
                continue
            if not any(ext in lowered for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif")):
                continue
            yield url

    @classmethod
    def _has_generated_image(cls, raw_response: str) -> bool:
        return any(cls._iter_generated_image_urls(raw_response))

    def _extract_generated_images_from_increment(self, new_data: str) -> List[Dict[str, Any]]:
        scan_text = f"{self._media_scan_tail}{str(new_data or '')}"
        images = self._extract_generated_images(scan_text)
        self._media_scan_tail = self._build_media_scan_tail(scan_text)
        return images

    def _extract_generated_images(self, raw_response: str, *, mark_seen: bool = True) -> List[Dict[str, Any]]:
        local_seen: set[str] = set()

        images: List[Dict[str, Any]] = []
        for url in self._iter_generated_image_urls(raw_response):
            if url in local_seen:
                continue
            local_seen.add(url)
            if mark_seen:
                if url in self._seen_media_refs:
                    continue
                self._seen_media_refs.add(url)
            images.append(
                {
                    "media_type": "image",
                    "kind": "url",
                    "url": url,
                    "data_uri": None,
                    "mime": None,
                    "byte_size": None,
                    "source": "gemini_stream",
                }
            )

        return images

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
