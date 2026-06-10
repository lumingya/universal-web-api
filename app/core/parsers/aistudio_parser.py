"""
aistudio_parser.py - Google AI Studio 响应解析器

响应格式特征：
- Content-Type: application/json+protobuf
- 嵌套数组结构
- 增量文本模式
- 包含 thinking 内容（需过滤）
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from app.core.config import logger
from .base import ResponseParser


class AIStudioParser(ResponseParser):
    """
    Google AI Studio (MakerSuite) 响应解析器
    
    URL 特征: MakerSuiteService/GenerateContent
    响应格式: JSON+Protobuf (嵌套数组)
    """

    _VISIBLE_TEXT_BLOCK_RE = re.compile(
        r'null,"((?:\\.|[^"\\])*)"\]\],"model"'
    )
    _VISIBLE_TEXT_BLOCK_PREFIX = 'null,"'
    _OBFUSCATED_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")
    _CJK_RE = re.compile(r"[\u3400-\u9fff]")
    
    def __init__(self):
        self._accumulated_content = ""
        self._is_done = False
        self._last_raw_length = 0
        self._pending_raw = ""
        self._visible_text_scan_pending = ""
        self._visible_text_fallback_content = ""
    
    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        """
        解析响应（返回完整内容的增量）
        """
        result = {
            "content": "",
            "images": [],
            "done": False,
            "error": None
        }

        try:
            if isinstance(raw_response, bytes):
                raw_response = raw_response.decode("utf-8", errors="ignore")
            elif not isinstance(raw_response, str):
                raw_response = str(raw_response)

            new_data = self._prepare_incremental_raw_response(raw_response)
            if not new_data:
                return result

            payloads = self._decode_payloads(new_data, incremental=True)
            content = ""
            is_done = False

            for data in payloads:
                current_content, current_done = self._extract_content(data)
                if current_content:
                    content = current_content
                if current_done:
                    is_done = True

            if not content:
                content = self._extract_visible_text_from_increment(new_data)

            if content:
                if content.startswith(self._accumulated_content):
                    delta = content[len(self._accumulated_content):]
                    self._accumulated_content = content
                else:
                    delta = content
                    self._accumulated_content += content
                if delta:
                    result["content"] = delta

            result["done"] = is_done

        except json.JSONDecodeError as e:
            logger.debug(f"[AIStudioParser] JSON 解析失败: {e}")
            result["error"] = str(e)
        except Exception as e:
            logger.debug(f"[AIStudioParser] 解析异常: {e}")
            result["error"] = str(e)

        return result

    def reset(self):
        """重置状态"""
        self._accumulated_content = ""
        self._is_done = False
        self._last_raw_length = 0
        self._last_raw_response = ""
        self._pending_raw = ""
        self._visible_text_scan_pending = ""
        self._visible_text_fallback_content = ""

    @staticmethod
    def _normalize_raw_text(raw_response: Any) -> str:
        if isinstance(raw_response, bytes):
            raw_response = raw_response.decode("utf-8", errors="ignore")
        elif not isinstance(raw_response, str):
            raw_response = str(raw_response)

        text = raw_response.lstrip("\ufeff")
        stripped = text.lstrip()
        if stripped.startswith(")]}'"):
            stripped = stripped[4:].lstrip("\r\n")
        return stripped

    def _decode_payloads(self, raw_response: Any, incremental: bool = False) -> List[Any]:
        if isinstance(raw_response, (list, dict)):
            return [raw_response]

        normalized = self._normalize_raw_text(raw_response)
        if not normalized:
            return []

        if incremental:
            normalized = f"{self._pending_raw}{normalized}"

        try:
            payload = json.loads(normalized)
            if incremental:
                self._pending_raw = ""
            return [payload]
        except json.JSONDecodeError as exc:
            payloads, remainder = self._decode_json_prefix_values(normalized)
            if incremental:
                self._pending_raw = remainder
            if payloads:
                return payloads
            logger.debug_throttled(
                f"aistudio.incomplete_json.{id(self)}",
                "[AIStudioParser] JSON 未完整，继续等待流增长 "
                f"(body_len={len(normalized)}, error={exc})",
                interval_sec=3.0,
            )
            return []

    @classmethod
    def _extract_visible_text_from_raw_body(cls, raw_response: Any) -> str:
        raw_text = cls._normalize_raw_text(raw_response)
        if not raw_text:
            return ""

        pieces, _last_match_end = cls._scan_visible_text_matches(raw_text)
        return "".join(pieces)

    @classmethod
    def _scan_visible_text_matches(cls, raw_text: str) -> Tuple[List[str], int]:
        pieces: List[str] = []
        last_match_end = 0
        for match in cls._VISIBLE_TEXT_BLOCK_RE.finditer(raw_text):
            last_match_end = match.end()
            piece = cls._decode_json_fragment(match.group(1))
            if piece and cls._looks_like_visible_text(piece):
                pieces.append(piece)

        return pieces, last_match_end

    @classmethod
    def _build_visible_text_scan_pending(cls, scan_text: str, last_match_end: int) -> str:
        remainder = scan_text[last_match_end:] if last_match_end > 0 else scan_text
        marker_index = remainder.rfind(cls._VISIBLE_TEXT_BLOCK_PREFIX)
        if marker_index >= 0:
            return remainder[marker_index:]

        keep_chars = max(0, len(cls._VISIBLE_TEXT_BLOCK_PREFIX) - 1)
        return remainder[-keep_chars:] if keep_chars else ""

    def _extract_visible_text_from_increment(self, new_data: str) -> str:
        if not new_data and not self._visible_text_scan_pending:
            return self._visible_text_fallback_content

        normalized_new_data = self._normalize_raw_text(new_data)
        scan_text = f"{self._visible_text_scan_pending}{normalized_new_data}"
        if not scan_text:
            return self._visible_text_fallback_content

        pieces, last_match_end = self._scan_visible_text_matches(scan_text)
        if pieces:
            self._visible_text_fallback_content += "".join(pieces)
        self._visible_text_scan_pending = self._build_visible_text_scan_pending(
            scan_text,
            last_match_end,
        )
        return self._visible_text_fallback_content

    @classmethod
    def _looks_like_obfuscated_payload(cls, value: Any) -> bool:
        text = str(value or "").strip()
        if len(text) < 96:
            return False
        if any(ch.isspace() for ch in text):
            return False
        if cls._CJK_RE.search(text):
            return False
        if not cls._OBFUSCATED_PAYLOAD_RE.fullmatch(text):
            return False
        if not any(ch in "+/=_-" for ch in text):
            return False

        distinct_chars = len(set(text))
        if distinct_chars < 20:
            return False

        alpha_num_ratio = sum(ch.isalnum() for ch in text) / len(text)
        return alpha_num_ratio >= 0.85

    @classmethod
    def _looks_like_visible_text(cls, value: Any) -> bool:
        text = str(value or "")
        stripped = text.strip()
        if not stripped:
            return False
        if cls._looks_like_obfuscated_payload(stripped):
            return False
        if cls._CJK_RE.search(stripped):
            return True
        if any(ch.isspace() for ch in stripped):
            return True
        if stripped.startswith(("{", "[", "<", "```", "http://", "https://")):
            return True

        punctuation_count = sum(
            ch in '.,!?;:()[]{}<>"\'`~@#$%^&*\\|'
            for ch in stripped
        )
        if punctuation_count > 0:
            return True

        return len(stripped) <= 80

    @classmethod
    def _pick_visible_text_candidate(cls, values: List[Any]) -> str:
        for item in values:
            if not isinstance(item, str):
                continue
            if cls._looks_like_visible_text(item):
                return item
        return ""

    @staticmethod
    def _decode_json_fragment(value: str) -> str:
        text = str(value or "")
        if not text:
            return ""

        try:
            decoded = json.loads(f'"{text}"')
            return decoded if isinstance(decoded, str) else ""
        except json.JSONDecodeError:
            return (
                text.replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )

    @staticmethod
    def _decode_json_prefix_values(raw_text: str) -> Tuple[List[Any], str]:
        decoder = json.JSONDecoder()
        values: List[Any] = []
        index = 0
        length = len(raw_text)

        while index < length:
            while index < length and raw_text[index].isspace():
                index += 1
            if index >= length:
                break

            try:
                value, next_index = decoder.raw_decode(raw_text, index)
            except json.JSONDecodeError:
                return values, raw_text[index:]

            values.append(value)
            index = next_index

        return values, ""

    @staticmethod
    def _normalize_outer_blocks(data: Any) -> List[Any]:
        if not isinstance(data, list) or not data:
            return []

        if any(isinstance(item, list) for item in data):
            if (
                len(data) == 1
                and isinstance(data[0], list)
                and any(isinstance(item, list) for item in data[0])
            ):
                return data[0]
            return data

        return []

    def _extract_content(self, data: Any) -> Tuple[str, bool]:
        """
        从响应数据中提取文本内容

        Returns:
            (accumulated_text, is_done)
        """
        try:
            outer = self._normalize_outer_blocks(data)
            if not outer:
                return "", False

            accumulated = ""
            is_done = False

            for block in outer:
                if not isinstance(block, list):
                    continue

                # 检查是否是统计块（结束标志之一）
                if self._is_stats_block(block):
                    is_done = True
                    continue

                # 提取文本
                text, block_done, is_thinking = self._extract_block_content(block)

                # 跳过 thinking 内容
                if is_thinking:
                    continue

                if text:
                    accumulated += text

                if block_done:
                    is_done = True

            return accumulated, is_done

        except Exception as e:
            logger.debug(f"[AIStudioParser] _extract_content 异常: {e}")
            return "", False

    def _is_stats_block(self, block: list) -> bool:
        """检查是否是统计块（响应结束）"""
        try:
            # 格式: [null, null, null, [timestamp, ...]]
            if len(block) >= 4:
                if block[0] is None and block[1] is None and block[2] is None:
                    if isinstance(block[3], list):
                        return True
            return False
        except:
            return False

    def _extract_block_content(self, block: list) -> tuple:
        """
        从单个块提取内容

        Returns:
            (text, is_done, is_thinking)
        """
        try:
            # 路径: block[0][0][0][0][0]
            # thinking: [13 items, None, "**Thinking...", ..., 1]
            # 正常内容: [2 items, None, "文本内容"]
            
            if not isinstance(block, list) or len(block) == 0:
                return "", False, False
            
            level1 = block[0]
            if not isinstance(level1, list) or len(level1) == 0:
                return "", False, False
            
            level2 = level1[0]
            if not isinstance(level2, list) or len(level2) == 0:
                return "", False, False
            
            level3 = level2[0]
            if not isinstance(level3, list) or len(level3) == 0:
                return "", False, False
            
            level4 = level3[0]
            if not isinstance(level4, list) or len(level4) == 0:
                return "", False, False
            
            # 这里就是 content_arr
            content_arr = level4[0]
            if not isinstance(content_arr, list):
                return "", False, False
            
            # 提取文本：只接受像“可见正文”的字符串，避免把内部高熵 payload 误当回复。
            text = ""
            primary_text = content_arr[1] if len(content_arr) > 1 else ""
            if self._looks_like_visible_text(primary_text):
                text = primary_text
            else:
                text = self._pick_visible_text_candidate(content_arr[2:])
                if not text:
                    suppressed_payload = next(
                        (
                            item for item in content_arr[2:]
                            if isinstance(item, str)
                            and self._looks_like_obfuscated_payload(item)
                        ),
                        "",
                    )
                    if suppressed_payload:
                        logger.debug_throttled(
                            f"aistudio.hidden_payload.{id(self)}",
                            "[AIStudioParser] 忽略疑似内部高熵 payload "
                            f"(len={len(str(suppressed_payload).strip())})",
                            interval_sec=3.0,
                        )
            
            # 判断是否是 thinking
            # thinking 块特征：len >= 13 且索引 12 == 1
            is_thinking = False
            if len(content_arr) >= 13:
                if len(content_arr) > 12 and content_arr[12] == 1:
                    is_thinking = True
            
            # 检查是否结束
            # 1. level2 的第二个元素是 1
            is_done = False
            if len(level2) > 1 and level2[1] == 1:
                is_done = True
            
            # 2. 有长 token 字符串（索引 13 或 14）
            if len(content_arr) > 13 and isinstance(content_arr[13], str):
                if len(content_arr[13]) > 50:
                    is_done = True
            
            return text, is_done, is_thinking

        except Exception as e:
            logger.debug(f"[AIStudioParser] _extract_block_content 异常: {e}")
            return "", False, False

    # ============ 元数据 ============

    @classmethod
    def get_id(cls) -> str:
        return "aistudio"

    @classmethod
    def get_name(cls) -> str:
        return "Google AI Studio"

    @classmethod
    def get_description(cls) -> str:
        return "解析 Google AI Studio (MakerSuite) 的 GenerateContent 响应"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["MakerSuiteService/GenerateContent", "GenerateContent"]

    def should_fallback_to_dom_when_no_visible_content(self) -> bool:
        return True


__all__ = ['AIStudioParser']
