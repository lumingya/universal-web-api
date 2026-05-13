"""
doubao_parser.py - Doubao SSE response parser.

Observed stream traits:
- event: STREAM_MSG_NOTIFY carries the first assistant block
- event: STREAM_CHUNK may carry extra content_block patches
- event: CHUNK_DELTA carries plain text deltas
- event: SSE_REPLY_END marks the answer as finished
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from app.core.config import logger
from .base import ResponseParser


class DoubaoParser(ResponseParser):
    """Parse Doubao's SSE response stream."""

    def __init__(self) -> None:
        self._last_raw = ""
        self._pending = ""
        self._block_texts: Dict[str, str] = {}
        self._last_full_message = ""
        self._assembled_text = ""

    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "content": "",
            "images": [],
            "done": False,
            "error": None,
        }

        try:
            if isinstance(raw_response, (bytes, bytearray)):
                raw_response = raw_response.decode("utf-8", errors="ignore")
            elif not isinstance(raw_response, str):
                raw_response = str(raw_response)

            if not raw_response:
                return result

            if self._last_raw and raw_response == self._last_raw:
                return result

            if self._last_raw and raw_response.startswith(self._last_raw):
                new_data = raw_response[len(self._last_raw):]
            else:
                new_data = raw_response

            self._last_raw = raw_response

            direct_content, direct_done = self._parse_direct_payload(new_data)
            if direct_content or direct_done:
                repaired = self._repair_text(direct_content)
                result["content"] = repaired
                if repaired:
                    self._assembled_text += repaired
                result["done"] = direct_done
                return result

            delta_content, done = self._consume_new_data(new_data)
            if delta_content:
                repaired = self._repair_text(delta_content)
                if repaired != delta_content and self._assembled_text.endswith(delta_content):
                    self._assembled_text = self._assembled_text[:-len(delta_content)] + repaired
                result["content"] = repaired
            result["done"] = done

        except Exception as e:
            logger.debug(f"[DoubaoParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self) -> None:
        self._last_raw = ""
        self._pending = ""
        self._block_texts.clear()
        self._last_full_message = ""
        self._assembled_text = ""

    def _consume_new_data(self, new_data: str) -> Tuple[str, bool]:
        normalized = (self._pending + new_data).replace("\r\n", "\n")
        if not normalized:
            return "", False

        blocks = normalized.split("\n\n")
        if normalized.endswith("\n\n"):
            self._pending = ""
            complete_blocks = [block for block in blocks if block.strip()]
        else:
            self._pending = blocks.pop() if blocks else normalized
            complete_blocks = [block for block in blocks if block.strip()]

        content_parts: List[str] = []
        done = False

        for block in complete_blocks:
            block_content, block_done = self._parse_event_block(block)
            if block_content:
                content_parts.append(block_content)
                self._assembled_text += block_content
            if block_done:
                done = True

        return "".join(content_parts), done

    def _parse_direct_payload(self, payload: str) -> Tuple[str, bool]:
        stripped = (payload or "").strip()
        if not stripped or not stripped.startswith("{"):
            return "", False

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            return "", False

        logger.debug(f"[DoubaoParser][DirectJSON] {self._describe_direct_json(data)}")

        embedded_stream = self._extract_embedded_stream_payload(data)
        if embedded_stream:
            logger.debug(
                "[DoubaoParser][DirectJSON] branch=embedded_stream "
                f"payload_len={len(embedded_stream)}"
            )
            return self._consume_new_data(embedded_stream)

        logger.debug("[DoubaoParser][DirectJSON] branch=direct_content")
        return self._extract_direct_json_content(data)

    @staticmethod
    def _extract_embedded_stream_payload(data: Any) -> str:
        if not isinstance(data, dict):
            return ""

        containers: List[Dict[str, Any]] = [data]
        body = data.get("body")
        if isinstance(body, dict):
            containers.append(body)

        for container in containers:
            for stream_key in ("_stream", "stream"):
                stream_data = container.get(stream_key)
                if not isinstance(stream_data, dict):
                    continue

                full_text = stream_data.get("fullText")
                if isinstance(full_text, str) and full_text:
                    return full_text

                chunks = stream_data.get("chunks")
                if not isinstance(chunks, list):
                    continue

                parts: List[str] = []
                for chunk in chunks:
                    if not isinstance(chunk, dict):
                        continue
                    chunk_data = chunk.get("data")
                    if isinstance(chunk_data, str) and chunk_data:
                        parts.append(chunk_data)
                if parts:
                    return "".join(parts)

        return ""

    def _parse_event_block(self, block: str) -> Tuple[str, bool]:
        event_name = ""
        data_lines: List[str] = []

        for raw_line in block.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())

        payload = "\n".join(data_lines).strip()
        if not event_name:
            return "", False

        if not payload:
            return "", False

        if event_name in {"STREAM_FINISH", "STREAM_END", "DONE", "COMPLETION"}:
            logger.debug(f"[DoubaoParser][SSE] event={event_name} done_only=1")
            return "", True

        if event_name == "CHUNK_DELTA":
            text = self._extract_chunk_delta(payload)
            text = self._repair_text(text)
            if text:
                logger.debug(
                    f"[DoubaoParser][SSE] event=CHUNK_DELTA text_len={len(text)} "
                    f"preview={text[:60]!r}"
                )
            return text, False

        if event_name == "STREAM_MSG_NOTIFY":
            text = self._extract_stream_msg_notify(payload)
            text = self._repair_text(text)
            if text:
                logger.debug(
                    f"[DoubaoParser][SSE] event=STREAM_MSG_NOTIFY text_len={len(text)} "
                    f"preview={text[:60]!r}"
                )
            return text, False

        if event_name == "STREAM_CHUNK":
            text, done = self._extract_stream_chunk(payload)
            text = self._repair_text(text)
            if text or done:
                logger.debug(
                    f"[DoubaoParser][SSE] event=STREAM_CHUNK text_len={len(text)} "
                    f"done={done} preview={text[:60]!r}"
                )
            return text, done

        if event_name == "FULL_MSG_NOTIFY":
            text = self._extract_full_msg_notify(payload)
            text = self._repair_text(text)
            if text:
                logger.debug(
                    f"[DoubaoParser][SSE] event=FULL_MSG_NOTIFY text_len={len(text)} "
                    f"preview={text[:60]!r}"
                )
            return text, False

        if event_name == "SSE_REPLY_END":
            text, done = self._extract_reply_end(payload)
            text = self._repair_text(text)
            logger.debug(
                f"[DoubaoParser][SSE] event=SSE_REPLY_END text_len={len(text)} "
                f"done={done} preview={text[:60]!r}"
            )
            return text, done

        return "", False

    def _extract_direct_json_content(self, data: Any) -> Tuple[str, bool]:
        if not isinstance(data, dict):
            return "", False

        role = data.get("role")
        content = data.get("content")
        if role == "assistant" and isinstance(content, str) and content:
            logger.debug(
                "[DoubaoParser][DirectJSON] content_source=role.content "
                f"len={len(content)}"
            )
            return self._extract_full_message_delta(content), True

        message = data.get("message")
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
            if role == "assistant" and isinstance(content, str) and content:
                logger.debug(
                    "[DoubaoParser][DirectJSON] content_source=message.content "
                    f"len={len(content)}"
                )
                return self._extract_full_message_delta(content), True

        choices = data.get("choices")
        if isinstance(choices, list):
            parts: List[str] = []
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if isinstance(delta, dict):
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        parts.append(text)
                message_obj = choice.get("message")
                if isinstance(message_obj, dict):
                    text = message_obj.get("content")
                    if isinstance(text, str) and text:
                        parts.append(text)
            if parts:
                logger.debug(
                    "[DoubaoParser][DirectJSON] content_source=choices "
                    f"parts={len(parts)} total_len={len(''.join(parts))}"
                )
                return self._extract_full_message_delta("".join(parts)), bool(data.get("done", True))

        return "", False

    @staticmethod
    def _describe_direct_json(data: Any) -> str:
        if not isinstance(data, dict):
            return f"type={type(data).__name__}"

        def _keys_of(obj: Any) -> list[str]:
            if isinstance(obj, dict):
                return [str(k) for k in list(obj.keys())[:8]]
            return []

        message = data.get("message")
        body = data.get("body")
        message_content = message.get("content") if isinstance(message, dict) else None
        message_content_len = len(message_content) if isinstance(message_content, str) else 0
        choices = data.get("choices")
        choices_len = len(choices) if isinstance(choices, list) else 0

        return (
            f"keys={_keys_of(data)}, "
            f"stream_keys={_keys_of(data.get('stream'))}, "
            f"_stream_keys={_keys_of(data.get('_stream'))}, "
            f"body_keys={_keys_of(body)}, "
            f"message_keys={_keys_of(message)}, "
            f"message_content_len={message_content_len}, "
            f"choices_len={choices_len}"
        )

    @staticmethod
    def _extract_chunk_delta(payload: str) -> str:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return ""

        text = data.get("text", "")
        return text if isinstance(text, str) else ""

    def _extract_stream_msg_notify(self, payload: str) -> str:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return ""

        content = data.get("content", {})
        return self._extract_content_blocks(content.get("content_block"))

    def _extract_full_msg_notify(self, payload: str) -> str:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return ""

        message = data.get("message")
        if not isinstance(message, dict):
            return ""

        if message.get("user_type") != 2:
            return ""

        content_block_text = self._extract_content_blocks(message.get("content_block"))
        if content_block_text:
            return content_block_text

        raw_content = message.get("content")
        if not isinstance(raw_content, str) or not raw_content.strip():
            return ""

        try:
            parsed_content = json.loads(raw_content)
        except json.JSONDecodeError:
            return ""

        return self._extract_content_blocks(parsed_content)

    def _extract_stream_chunk(self, payload: str) -> Tuple[str, bool]:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "", False

        content_parts: List[str] = []
        done = False

        for patch in data.get("patch_op", []) or []:
            if not isinstance(patch, dict):
                continue

            patch_value = patch.get("patch_value")
            if not isinstance(patch_value, dict):
                continue

            text = self._extract_content_blocks(patch_value.get("content_block"))
            if text:
                content_parts.append(text)

            ext = patch_value.get("ext")
            if isinstance(ext, dict) and str(ext.get("is_finish", "")) == "1":
                done = True

        return "".join(content_parts), done

    def _extract_reply_end(self, payload: str) -> Tuple[str, bool]:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "", True

        if self._assembled_text:
            return "", True

        finish_attr = data.get("msg_finish_attr")
        if not isinstance(finish_attr, dict):
            return "", True

        brief = finish_attr.get("brief")
        if not isinstance(brief, str) or not brief:
            return "", True

        return self._extract_full_message_delta(brief), True

    @staticmethod
    def _extract_text_from_block(block: Any) -> Tuple[str, str]:
        if not isinstance(block, dict):
            return "", ""

        block_id = block.get("block_id") or block.get("id") or ""
        if not isinstance(block_id, str):
            block_id = str(block_id)

        content = block.get("content")
        if not isinstance(content, dict):
            return block_id, ""

        text_block = content.get("text_block")
        if not isinstance(text_block, dict):
            return block_id, ""

        text = text_block.get("text", "")
        return block_id, text if isinstance(text, str) else ""

    def _extract_content_blocks(self, blocks: Any) -> str:
        if not isinstance(blocks, list):
            return ""

        parts: List[str] = []
        for block in blocks:
            block_id, text = self._extract_text_from_block(block)
            if not text:
                continue

            previous = self._block_texts.get(block_id, "")
            if previous and text.startswith(previous):
                delta = text[len(previous):]
            elif previous == text:
                delta = ""
            else:
                delta = text

            self._block_texts[block_id] = text

            if delta:
                parts.append(delta)

        return "".join(parts)

    def _extract_full_message_delta(self, text: str) -> str:
        if not text:
            return ""

        previous = self._last_full_message
        self._last_full_message = text

        if self._assembled_text and text.startswith(self._assembled_text):
            return text[len(self._assembled_text):]
        if previous and text.startswith(previous):
            return text[len(previous):]
        if previous == text:
            return ""
        return text

    @staticmethod
    def _repair_text(text: str) -> str:
        if not text:
            return text

        repaired_whole = DoubaoParser._repair_text_candidate(text)
        if repaired_whole != text:
            return repaired_whole

        repaired_segments: List[str] = []
        changed = False
        buffer = ""
        buffer_is_suspicious: bool | None = None

        def flush() -> None:
            nonlocal buffer, buffer_is_suspicious, changed
            if not buffer:
                return
            if buffer_is_suspicious:
                repaired = DoubaoParser._repair_text_candidate(buffer)
                if repaired != buffer:
                    changed = True
                repaired_segments.append(repaired)
            else:
                repaired_segments.append(buffer)
            buffer = ""
            buffer_is_suspicious = None

        for ch in text:
            is_suspicious = DoubaoParser._is_suspicious_char(ch)
            if buffer_is_suspicious is None:
                buffer = ch
                buffer_is_suspicious = is_suspicious
                continue
            if is_suspicious == buffer_is_suspicious:
                buffer += ch
                continue
            flush()
            buffer = ch
            buffer_is_suspicious = is_suspicious

        flush()
        if changed:
            repaired_text = "".join(repaired_segments)
            if repaired_text != text:
                logger.debug(
                    "[DoubaoParser][Repair] repaired mixed mojibake "
                    f"(before_len={len(text)}, after_len={len(repaired_text)})"
                )
                return repaired_text
        return text

    @staticmethod
    def _repair_text_candidate(text: str) -> str:
        if not text or not DoubaoParser._should_try_repair_text(text):
            return text

        for source_encoding in ("latin1", "cp1252"):
            repaired = DoubaoParser._try_redecode_text(text, source_encoding)
            if not repaired:
                continue

            if DoubaoParser._looks_more_readable(repaired, text):
                logger.debug(
                    "[DoubaoParser][Repair] repaired mojibake "
                    f"(enc={source_encoding}, before_len={len(text)}, after_len={len(repaired)})"
                )
                return repaired
        return text

    @staticmethod
    def _should_try_repair_text(text: str) -> bool:
        if not text:
            return False

        if any(0x4E00 <= ord(ch) <= 0x9FFF or ord(ch) > 0xFFFF for ch in text):
            return False

        cp1252_extra = {
            0x0152, 0x0153, 0x0160, 0x0161, 0x0178, 0x017D, 0x017E,
            0x02C6, 0x02DC, 0x2013, 0x2014, 0x2018, 0x2019, 0x201A,
            0x201C, 0x201D, 0x201E, 0x2020, 0x2021, 0x2022, 0x2026,
            0x2030, 0x2039, 0x203A, 0x20AC, 0x2122,
        }
        suspicious_count = 0
        for ch in text:
            if DoubaoParser._is_suspicious_char(ch, cp1252_extra):
                suspicious_count += 1

        return suspicious_count >= max(2, len(text) // 4)

    @staticmethod
    def _is_suspicious_char(ch: str, cp1252_extra: set[int] | None = None) -> bool:
        if not ch:
            return False
        if cp1252_extra is None:
            cp1252_extra = {
                0x0152, 0x0153, 0x0160, 0x0161, 0x0178, 0x017D, 0x017E,
                0x02C6, 0x02DC, 0x2013, 0x2014, 0x2018, 0x2019, 0x201A,
                0x201C, 0x201D, 0x201E, 0x2020, 0x2021, 0x2022, 0x2026,
                0x2030, 0x2039, 0x203A, 0x20AC, 0x2122,
            }
        code = ord(ch)
        return 0x80 <= code <= 0x00FF or code in cp1252_extra

    @staticmethod
    def _try_redecode_text(text: str, source_encoding: str) -> str:
        data = bytearray()

        for ch in text:
            code = ord(ch)
            if code <= 0xFF:
                data.append(code)
                continue

            try:
                encoded = ch.encode(source_encoding)
            except Exception:
                return ""

            if len(encoded) != 1:
                return ""

            data.extend(encoded)

        try:
            return bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(data).decode("utf-8", errors="ignore")

    @staticmethod
    def _looks_more_readable(candidate: str, original: str) -> bool:
        readable_punct = set(".,!?;:'\"()[]{}-_/~`@#$%^&*+=<>，。！？：；（）【】《》、～“”‘’…")

        def score(value: str) -> int:
            total = 0
            for ch in value:
                code = ord(ch)
                if 0x4E00 <= code <= 0x9FFF:
                    total += 2
                elif code > 0xFFFF:
                    total += 2
                elif ch.isascii() and (ch.isalnum() or ch.isspace() or ch in readable_punct):
                    total += 1
                elif ch in readable_punct or ch.isspace():
                    total += 1
            return total

        return score(candidate) > score(original)

    @classmethod
    def get_id(cls) -> str:
        return "doubao"

    @classmethod
    def get_name(cls) -> str:
        return "Doubao"

    @classmethod
    def get_description(cls) -> str:
        return "Parse Doubao SSE streams with STREAM_MSG_NOTIFY / CHUNK_DELTA events"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["**/chat/completion**"]


__all__ = ["DoubaoParser"]
