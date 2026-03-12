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


def _clean_escaped(text: str) -> str:
    """
    Gemini 返回的字符串仍可能残留多余的反斜杠：
      1. \<ctx\>        →  <ctx>
      2. \\n            →  \n  →  换行
      3. \`code\`       →  `code`
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
            inner = json.loads(inner_raw)  # type: ignore[arg-type]

            # inner[4][0][1][0]
            content = inner[4][0][1][0]  # type: ignore[index]
            if not isinstance(content, str):
                return None

            # ---------- 反转义 ----------
            try:
                # content 仍是 *裸* 的转义字符串（无外围引号）
                # 用 json.loads 再解一次
                content = json.loads(f'"{content}"')
            except json.JSONDecodeError:
                # 若仍失败，再做一次“人工”清洗
                content = _clean_escaped(content)

            # 最后再跑一次清洗，确保无漏网
            return _clean_escaped(content)

        except Exception as exc:  # pragma: no cover
            logger.debug(f"[GeminiParser] 提取失败: {exc}")
            return None

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