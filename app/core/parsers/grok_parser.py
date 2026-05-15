"""
grok_parser.py - Grok newline-delimited JSON stream parser.

Observed stream traits:
- content-type: application/json
- body is a growing sequence of JSON objects separated by newlines
- visible answer tokens arrive as result.token with isThinking=false
- final assistant snapshot arrives as result.modelResponse
- generated images can be exposed via generatedImageUrls / imageAttachments
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

from app.core.config import logger
from .base import ResponseParser


class GrokParser(ResponseParser):
    """Parse Grok web NDJSON response streams."""

    _IMAGE_URL_KEYS = {
        "url",
        "downloadurl",
        "download_url",
        "imageurl",
        "image_url",
        "imageuri",
        "image_uri",
        "uri",
    }

    def __init__(self) -> None:
        self._last_raw_length = 0
        self._pending = ""
        self._rendered_content = ""
        self._seen_image_refs: set[str] = set()

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

            current_len = len(raw_response)
            if current_len <= self._last_raw_length:
                return result

            new_data = raw_response[self._last_raw_length :]
            self._last_raw_length = current_len

            delta_content, images, done = self._consume_new_data(new_data)
            if delta_content:
                result["content"] = delta_content
            if images:
                result["images"] = images
            result["done"] = done

        except Exception as e:
            logger.debug(f"[GrokParser] parse exception: {e}")
            result["error"] = str(e)

        return result

    def reset(self) -> None:
        self._last_raw_length = 0
        self._pending = ""
        self._rendered_content = ""
        self._seen_image_refs.clear()

    def get_media_generation_state(
        self,
        raw_response: str = "",
        parse_result: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        pending = False
        hint_parts: List[str] = []
        wait_timeout_seconds = None

        try:
            for payload in self._iter_payloads(str(raw_response or "")):
                result = payload.get("result")
                if not isinstance(result, dict):
                    continue

                event_payload = self._unwrap_result_payload(result)
                if not isinstance(event_payload, dict):
                    continue

                if self._collect_image_urls(event_payload):
                    return {}

                model_response = event_payload.get("modelResponse")
                if isinstance(model_response, dict) and self._collect_image_urls(model_response):
                    return {}

                progress = event_payload.get("progressReport")
                if not isinstance(progress, dict):
                    continue

                category = str(progress.get("category") or "").strip().upper()
                state = str(progress.get("state") or "").strip().upper()
                message = str(progress.get("message") or "").strip()

                if "IMAGE" not in category:
                    continue

                if any(token in state for token in ("PENDING", "STARTED", "RUNNING", "IN_PROGRESS")):
                    pending = True
                    wait_timeout_seconds = max(float(wait_timeout_seconds or 0), 120.0)
                    if message:
                        hint_parts.append(message)

        except Exception as e:
            logger.debug(f"[GrokParser] media state exception: {e}")

        if not pending:
            return {}

        deduped_hints: List[str] = []
        seen = set()
        for item in hint_parts:
            if item in seen:
                continue
            seen.add(item)
            deduped_hints.append(item)

        return {
            "pending": True,
            "media_type": "image",
            "hint_text": "\n\n".join(deduped_hints[:3]),
            "wait_timeout_seconds": wait_timeout_seconds,
        }

    def _consume_new_data(self, new_data: str) -> Tuple[str, List[Dict[str, Any]], bool]:
        previous_content = self._rendered_content
        complete_lines = self._extract_complete_lines(new_data)
        complete_lines.extend(self._drain_parseable_pending_line())
        if not complete_lines:
            return "", [], False

        images: List[Dict[str, Any]] = []
        done = False

        for raw_line in complete_lines:
            line = raw_line.strip()
            if not line:
                continue

            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            line_images, line_done = self._consume_payload(payload)
            if line_images:
                images.extend(line_images)
            if line_done:
                done = True

        delta = self._compute_delta(previous_content, self._rendered_content)
        return delta, images, done

    def _extract_complete_lines(self, new_data: str) -> List[str]:
        normalized = (self._pending + str(new_data or "")).replace("\r\n", "\n").replace("\r", "\n")
        if not normalized:
            return []

        parts = normalized.split("\n")
        if normalized.endswith("\n"):
            self._pending = ""
            return parts[:-1]

        self._pending = parts.pop() if parts else normalized
        return parts

    def _drain_parseable_pending_line(self) -> List[str]:
        pending = str(self._pending or "").strip()
        if not pending:
            return []

        try:
            json.loads(pending)
        except json.JSONDecodeError:
            return []

        self._pending = ""
        return [pending]

    def _consume_payload(self, payload: Any) -> Tuple[List[Dict[str, Any]], bool]:
        if not isinstance(payload, dict):
            return [], False

        result = payload.get("result")
        if not isinstance(result, dict):
            return [], False

        event_payload = self._unwrap_result_payload(result)
        if not isinstance(event_payload, dict):
            return [], False

        self._consume_token_event(event_payload)

        images = self._extract_image_items(event_payload)
        model_response = event_payload.get("modelResponse")
        done = False
        if isinstance(model_response, dict):
            self._consume_model_response(model_response)
            images.extend(self._extract_image_items(model_response))
            done = not bool(model_response.get("partial", False))

        return self._dedupe_images(images), done

    def _consume_token_event(self, result: Dict[str, Any]) -> None:
        token = result.get("token")
        if not isinstance(token, str) or not token:
            return

        if bool(result.get("isThinking", False)):
            return

        message_tag = str(result.get("messageTag") or "").strip().lower()
        if message_tag and message_tag not in {"final", "answer"}:
            return

        self._rendered_content += token

    def _consume_model_response(self, model_response: Dict[str, Any]) -> None:
        sender = str(model_response.get("sender") or "").strip().lower()
        if sender and sender not in {"assistant", "bot"}:
            return

        message = model_response.get("message")
        if isinstance(message, str) and message:
            self._rendered_content = message

    def _extract_image_items(self, container: Dict[str, Any]) -> List[Dict[str, Any]]:
        urls = self._collect_image_urls(container)
        image_items: List[Dict[str, Any]] = []
        for url in urls:
            if url in self._seen_image_refs:
                continue
            self._seen_image_refs.add(url)
            image_items.append(
                {
                    "media_type": "image",
                    "kind": "url",
                    "url": url,
                    "data_uri": None,
                    "mime": None,
                    "byte_size": None,
                    "alt": "",
                    "width": None,
                    "height": None,
                    "detected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "source": "grok_stream",
                }
            )

        return image_items

    def _collect_image_urls(self, container: Dict[str, Any]) -> List[str]:
        urls: List[str] = []

        generated = container.get("generatedImageUrls")
        if isinstance(generated, list):
            for item in generated:
                if isinstance(item, str) and self._looks_like_image_ref(item):
                    urls.append(item)

        attachments = container.get("imageAttachments")
        if isinstance(attachments, list):
            for item in attachments:
                urls.extend(self._extract_image_urls_from_value(item))

        cards = container.get("cardAttachmentsJson")
        if isinstance(cards, list):
            for item in cards:
                parsed = item
                if isinstance(item, str):
                    try:
                        parsed = json.loads(item)
                    except json.JSONDecodeError:
                        parsed = item
                urls.extend(self._extract_image_urls_from_value(parsed))

        deduped: List[str] = []
        seen = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)

        return deduped

    @staticmethod
    def _unwrap_result_payload(result: Dict[str, Any]) -> Dict[str, Any]:
        response = result.get("response")
        if isinstance(response, dict):
            return response
        return result

    def _extract_image_urls_from_value(self, value: Any) -> List[str]:
        urls: List[str] = []

        if isinstance(value, str):
            if self._looks_like_image_ref(value):
                urls.append(value)
            return urls

        if isinstance(value, list):
            for item in value:
                urls.extend(self._extract_image_urls_from_value(item))
            return urls

        if not isinstance(value, dict):
            return urls

        content_type = str(
            value.get("contentType") or value.get("mimeType") or value.get("mime") or ""
        ).strip().lower()

        for key, item in value.items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key in self._IMAGE_URL_KEYS:
                if isinstance(item, str) and (self._looks_like_image_ref(item) or content_type.startswith("image/")):
                    urls.append(item)
                continue

            if normalized_key in {"images", "attachments", "items", "content", "data"}:
                urls.extend(self._extract_image_urls_from_value(item))

        return urls

    @staticmethod
    def _looks_like_image_ref(value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return False

        lowered = text.lower()
        if lowered.startswith("data:image/"):
            return True

        if not lowered.startswith(("http://", "https://")):
            return False

        return any(marker in lowered for marker in ("/generated/", ".png", ".jpg", ".jpeg", ".webp", ".gif"))

    @staticmethod
    def _compute_delta(previous: str, current: str) -> str:
        if current == previous:
            return ""
        if not previous:
            return current
        if current.startswith(previous):
            return current[len(previous) :]
        if previous.startswith(current):
            return ""

        prefix_len = 0
        limit = min(len(previous), len(current))
        while prefix_len < limit and previous[prefix_len] == current[prefix_len]:
            prefix_len += 1

        if prefix_len >= len(previous) // 2:
            return current[prefix_len:]
        return current

    @staticmethod
    def _dedupe_images(images: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()

        for item in images:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("media_type") or ""),
                str(item.get("kind") or ""),
                str(item.get("url") or item.get("data_uri") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        return deduped

    def _iter_payloads(self, raw_response: str):
        self_pending = self._pending
        try:
            self._pending = ""
            lines = self._extract_complete_lines(raw_response)
            lines.extend(self._drain_parseable_pending_line())
            for raw_line in lines:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
        finally:
            self._pending = self_pending

    @classmethod
    def get_id(cls) -> str:
        return "grok"

    @classmethod
    def get_name(cls) -> str:
        return "Grok"

    @classmethod
    def get_description(cls) -> str:
        return "Parse Grok NDJSON chat streams, ignore thinking tokens, and extract generated images"

    @classmethod
    def get_supported_patterns(cls) -> List[str]:
        return ["/rest/app-chat/conversations/", "/responses"]


__all__ = ["GrokParser"]
