"""
lmarena_parser.py - Arena.ai 响应解析器

响应格式：Vercel AI SDK Data Stream Protocol
- 每行格式: {prefix}:{json_data}
- a0: 文本增量（JSON 编码的字符串）
- a2: 心跳/元数据（JSON 数组）
- ad: 结束元数据（含 finishReason）
- ae: 错误信息

编码问题：
  DrissionPage 通过 CDP 获取响应体时，UTF-8 字节被混合解码为
  cp1252 映射字符（如 0x92→' U+2019）和 latin-1 直通字符
  （如 0x90→U+0090），导致双重编码（mojibake）。
  需要两步修复：translate 统一到 latin-1 范围 → encode('latin-1') 还原字节

调用方式：
  NetworkMonitor 每次调用 parse_chunk 传入一个完整的 HTTP 响应体
  （DrissionPage listen.wait 是非流式的，一次性拿到整个 SSE body）
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core.config import logger
from .base import ResponseParser


# ─────────────────────────────────────────────────────
# cp1252 → latin-1 逆映射表（模块级常量，只构建一次）
# ─────────────────────────────────────────────────────
# cp1252 在 0x80-0x9F 区间将部分字节映射到 U+0100 以上的 Unicode 字符，
# 而 latin-1 将相同字节直通映射到 U+0080-U+009F。
# 此表将 cp1252 的特殊映射还原为 latin-1 范围的等价字符，
# 使后续 encode('latin-1') 能正确还原原始字节。
#
# 5 个未定义位置 (0x81, 0x8D, 0x8F, 0x90, 0x9D) 已经是
# latin-1 直通映射 (U+0081 等)，无需处理。
_CP1252_TO_LATIN1 = str.maketrans({
    '\u20ac': '\x80',  # €
    '\u201a': '\x82',  # ‚
    '\u0192': '\x83',  # ƒ
    '\u201e': '\x84',  # „
    '\u2026': '\x85',  # …
    '\u2020': '\x86',  # †
    '\u2021': '\x87',  # ‡
    '\u02c6': '\x88',  # ˆ
    '\u2030': '\x89',  # ‰
    '\u0160': '\x8a',  # Š
    '\u2039': '\x8b',  # ‹
    '\u0152': '\x8c',  # Œ
    '\u017d': '\x8e',  # Ž
    '\u2018': '\x91',  # '
    '\u2019': '\x92',  # '
    '\u201c': '\x93',  # "
    '\u201d': '\x94',  # "
    '\u2022': '\x95',  # •
    '\u2013': '\x96',  # –
    '\u2014': '\x97',  # —
    '\u02dc': '\x98',  # ˜
    '\u2122': '\x99',  # ™
    '\u0161': '\x9a',  # š
    '\u203a': '\x9b',  # ›
    '\u0153': '\x9c',  # œ
    '\u017e': '\x9e',  # ž
    '\u0178': '\x9f',  # Ÿ
})


def _looks_like_image_ref(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith(("http://", "https://", "data:image/", "blob:")):
        return True
    return any(token in lowered for token in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".avif"))


def _extract_arena_image_items(
    payload: str,
    seen_refs: set[str],
    *,
    source: str,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return items

    entries = data if isinstance(data, list) else [data]
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        entry_type = str(entry.get("type") or "").strip().lower()
        if entry_type and entry_type != "image":
            continue

        image_ref = ""
        for key in ("image", "url", "src"):
            candidate = str(entry.get(key) or "").strip()
            if _looks_like_image_ref(candidate):
                image_ref = candidate
                break

        if not image_ref:
            continue

        dedupe_key = f"image:{image_ref}"
        if dedupe_key in seen_refs:
            continue
        seen_refs.add(dedupe_key)

        mime = str(entry.get("mimeType") or entry.get("mime") or "").strip() or None
        kind = "data_uri" if image_ref.startswith("data:image/") else "url"
        items.append(
            {
                "media_type": "image",
                "kind": kind,
                "url": image_ref if kind == "url" else None,
                "data_uri": image_ref if kind == "data_uri" else None,
                "mime": mime,
                "byte_size": None,
                "alt": str(entry.get("alt") or "").strip(),
                "width": entry.get("width"),
                "height": entry.get("height"),
                "detected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "source": source,
            }
        )

    return items


class LmarenaParser(ResponseParser):
    """
    Arena.ai 响应解析器

    URL 特征: /nextjs-api/stream/create-evaluation
    响应格式: Vercel AI SDK Data Stream Protocol (行分隔)
    """

    _PROTOCOL_PREFIXES = {"a0", "a2", "ad", "ae", "a3", "ag", "bg"}

    def __init__(self) -> None:
        self._accumulated = ""
        self._accumulated_reasoning = ""
        self._seen_image_refs: set[str] = set()
        self._last_debug_summary: Dict[str, Any] = {}
        self._last_debug_raw_signature = ""

    @staticmethod
    def _content_delta(
        accumulated: str,
        candidate: str,
        *,
        append_disjoint: bool = False,
    ) -> tuple[str, str]:
        """Return unseen text and the new accumulated value for repeated SSE bodies."""
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

        if append_disjoint:
            return candidate, accumulated + candidate

        # A parser instance belongs to one response stream. A body that neither
        # extends nor overlaps the emitted text is a stale/non-monotonic replay;
        # emitting it wholesale would duplicate the response already sent.
        return "", accumulated

    # ============ 对外接口 ============

    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        """
        解析完整的 HTTP 响应体（一次性包含所有 SSE 行）
        """
        result: Dict[str, Any] = {
            "content": "",
            "reasoning_content": "",
            "images": [],
            "done": False,
            "error": None,
        }

        if isinstance(raw_response, (bytes, bytearray)):
            raw_response = raw_response.decode("utf-8", errors="ignore")

        if not raw_response or not isinstance(raw_response, str):
            return result

        debug_raw_signature = self._debug_body_signature(raw_response)

        # 修复双重 UTF-8 编码（mojibake）
        raw_response = self._fix_mojibake(raw_response)

        try:
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            images: list[Dict[str, Any]] = []
            done = False
            line_count = 0
            prefix_counts: Dict[str, int] = {}
            unknown_prefix_count = 0
            non_protocol_line_count = 0
            parse_errors: list[Dict[str, Any]] = []

            for line in raw_response.split("\n"):
                line = line.strip()
                if not line:
                    continue
                line_count += 1

                colon_idx = line.find(":")
                if colon_idx < 1:
                    non_protocol_line_count += 1
                    continue

                prefix = line[:colon_idx]
                payload = line[colon_idx + 1:]
                if prefix in self._PROTOCOL_PREFIXES:
                    prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
                else:
                    unknown_prefix_count += 1

                if prefix == "a0":
                    text = self._parse_text_chunk(payload)
                    if text is not None:
                        content_parts.append(text)
                    else:
                        parse_errors.append({
                            "prefix": prefix,
                            "payload_preview": payload[:160],
                        })

                elif prefix in {"ag", "bg"}:
                    text = self._parse_text_chunk(payload)
                    if text is not None:
                        reasoning_parts.append(text)
                    else:
                        parse_errors.append({
                            "prefix": prefix,
                            "payload_preview": payload[:160],
                        })

                elif prefix == "a2":
                    images.extend(
                        _extract_arena_image_items(
                            payload,
                            self._seen_image_refs,
                            source="lmarena_stream",
                        )
                    )

                elif prefix == "ad":
                    if self._is_finish_signal(payload):
                        done = True

                elif prefix in {"ae", "a3"}:
                    error_msg = self._parse_error(payload)
                    if error_msg:
                        result["error"] = error_msg
                        done = True

            new_content = "".join(content_parts)
            new_reasoning = "".join(reasoning_parts)

            if new_content:
                delta, next_accumulated = self._content_delta(self._accumulated, new_content)
                if self._accumulated and not delta:
                    logger.debug("[LmarenaParser] 检测到重复正文响应，跳过")
                else:
                    result["content"] = delta
                self._accumulated = next_accumulated

            if new_reasoning:
                delta_r, next_acc_r = self._content_delta(self._accumulated_reasoning, new_reasoning)
                if self._accumulated_reasoning and not delta_r:
                    logger.debug("[LmarenaParser] 检测到重复思考响应，跳过")
                else:
                    result["reasoning_content"] = delta_r
                self._accumulated_reasoning = next_acc_r

            if images:
                result["images"] = images
            result["done"] = done
            self._last_debug_summary = {
                "raw_body_len": len(raw_response),
                "line_count": line_count,
                "prefix_counts": prefix_counts,
                "unknown_prefix_count": unknown_prefix_count,
                "non_protocol_line_count": non_protocol_line_count,
                "content_candidate_len": len(new_content),
                "emitted_content_len": len(str(result.get("content") or "")),
                "accumulated_len": len(self._accumulated),
                "reasoning_candidate_len": len(new_reasoning),
                "emitted_reasoning_len": len(str(result.get("reasoning_content") or "")),
                "accumulated_reasoning_len": len(self._accumulated_reasoning),
                "done": bool(done),
                "error": str(result.get("error") or ""),
                "image_count": len(images),
                "parse_errors": parse_errors[-8:],
            }
            self._last_debug_raw_signature = debug_raw_signature

        except Exception as e:
            logger.debug(f"[LmarenaParser] 解析异常: {e}")
            result["error"] = str(e)
            self._last_debug_summary = {
                "raw_body_len": len(raw_response),
                "exception": str(e),
            }
            self._last_debug_raw_signature = debug_raw_signature

        return result

    def reset(self) -> None:
        """重置状态"""
        self._accumulated = ""
        self._accumulated_reasoning = ""
        self._seen_image_refs.clear()
        self._last_debug_summary = {}
        self._last_debug_raw_signature = ""

    def export_debug_data(self, raw_response: str = "") -> Dict[str, Any]:
        """Return a compact parser-side protocol summary for network_parser_debug."""
        summary = dict(self._last_debug_summary or {})
        if raw_response:
            text = self._normalize_debug_text(raw_response)
            raw_summary = self._summarize_protocol_lines(text)
            if self._last_debug_raw_signature == self._debug_body_signature(text):
                summary.update(raw_summary)
            else:
                summary = raw_summary
        summary.update({
            "accumulated_len": len(self._accumulated),
            "seen_image_refs": len(self._seen_image_refs),
        })
        return summary

    # ============ 内部方法 ============

    @staticmethod
    def _normalize_debug_text(raw_response: Any) -> str:
        if isinstance(raw_response, (bytes, bytearray)):
            return raw_response.decode("utf-8", errors="ignore")
        return str(raw_response or "")

    @classmethod
    def _debug_body_signature(cls, raw_response: Any) -> str:
        text = cls._normalize_debug_text(raw_response)
        return f"{len(text)}:{text[:80]}:{text[-80:]}"

    @classmethod
    def _summarize_protocol_lines(cls, raw_response: Any) -> Dict[str, Any]:
        text = cls._normalize_debug_text(raw_response)
        prefix_counts: Dict[str, int] = {}
        line_count = 0
        unknown_prefix_count = 0
        non_protocol_line_count = 0
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            line_count += 1
            colon_idx = line.find(":")
            if colon_idx < 1:
                non_protocol_line_count += 1
                continue
            prefix = line[:colon_idx]
            if prefix in cls._PROTOCOL_PREFIXES:
                prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
            else:
                unknown_prefix_count += 1
        return {
            "raw_body_len": len(text),
            "line_count": line_count,
            "prefix_counts": prefix_counts,
            "unknown_prefix_count": unknown_prefix_count,
            "non_protocol_line_count": non_protocol_line_count,
        }

    @staticmethod
    def _fix_mojibake(text: str) -> str:
        """
        修复双重 UTF-8 编码（mojibake）

        DrissionPage 通过 CDP 获取的响应体存在编码错误：
        UTF-8 字节被混合解码为 cp1252 + latin-1，导致：

          原始 UTF-8:     f0 9f 92 9e  (💞)
          被 cp1252 解码:  ð(F0) Ÿ(9F→U+0178) '(92→U+2019) ž(9E→U+017E)
          但 0x90 → U+0090（latin-1 直通，因 cp1252 未定义此位置）

        cp1252 有 5 个未定义位置 (0x81,0x8D,0x8F,0x90,0x9D)
        走 latin-1 直通映射，导致 encode('cp1252') 和 encode('latin-1')
        都无法单独处理全部字符。

        修复方案（两步）：
        1. translate: 将 cp1252 特有字符 (U+2019等) 映射回 latin-1 范围 (U+0092等)
        2. encode('latin-1'): 所有字符现在都在 U+0000-U+00FF，可一一还原原始字节
        3. decode('utf-8'): 原始字节 → 正确文本
        """
        try:
            mapped = text.translate(_CP1252_TO_LATIN1)
            return mapped.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            # 不是 mojibake（或只有部分是），返回原样
            return text

    @staticmethod
    def _parse_text_chunk(payload: str) -> str | None:
        """解析 a0 行的 payload"""
        try:
            value = json.loads(payload)
            if isinstance(value, str):
                return value
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    @staticmethod
    def _is_finish_signal(payload: str) -> bool:
        """检查 ad 行是否表示结束"""
        try:
            data = json.loads(payload)
            if isinstance(data, dict) and data.get("finishReason"):
                return True
        except (json.JSONDecodeError, ValueError):
            pass
        return False

    @staticmethod
    def _parse_error(payload: str) -> str | None:
        """解析 ae 行的错误信息"""
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                return data.get("message", str(data))
            return str(data)
        except (json.JSONDecodeError, ValueError):
            return payload.strip() if payload.strip() else None

    # ============ 元数据 ============

    @classmethod
    def get_id(cls) -> str:
        return "lmarena"

    @classmethod
    def get_name(cls) -> str:
        return "Arena.ai"

    @classmethod
    def get_description(cls) -> str:
        return "解析 Arena.ai 的流式响应 (Vercel AI SDK Data Stream Protocol)"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["nextjs-api/stream/create-evaluation"]


__all__ = [
    "LmarenaParser",
    "_CP1252_TO_LATIN1",
    "_extract_arena_image_items",
]
