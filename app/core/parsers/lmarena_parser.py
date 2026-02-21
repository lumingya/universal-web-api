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


class LmarenaParser(ResponseParser):
    """
    Arena.ai 响应解析器

    URL 特征: /nextjs-api/stream/create-evaluation
    响应格式: Vercel AI SDK Data Stream Protocol (行分隔)
    """

    def __init__(self) -> None:
        self._accumulated = ""

    # ============ 对外接口 ============

    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        """
        解析完整的 HTTP 响应体（一次性包含所有 SSE 行）
        """
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

        # 修复双重 UTF-8 编码（mojibake）
        raw_response = self._fix_mojibake(raw_response)

        try:
            content_parts: list[str] = []
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

                if prefix == "a0":
                    text = self._parse_text_chunk(payload)
                    if text is not None:
                        content_parts.append(text)

                elif prefix == "ad":
                    if self._is_finish_signal(payload):
                        done = True

                elif prefix == "ae":
                    error_msg = self._parse_error(payload)
                    if error_msg:
                        result["error"] = error_msg
                        done = True

            new_content = "".join(content_parts)

            if new_content:
                if self._accumulated and new_content == self._accumulated:
                    logger.debug("[LmarenaParser] 检测到重复响应，跳过")
                elif self._accumulated and new_content.startswith(self._accumulated):
                    result["content"] = new_content[len(self._accumulated):]
                    self._accumulated = new_content
                else:
                    result["content"] = new_content
                    self._accumulated = new_content

            result["done"] = done

        except Exception as e:
            logger.debug(f"[LmarenaParser] 解析异常: {e}")
            result["error"] = str(e)

        return result

    def reset(self) -> None:
        """重置状态"""
        self._accumulated = ""

    # ============ 内部方法 ============

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


__all__ = ["LmarenaParser"]