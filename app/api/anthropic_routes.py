"""
app/api/anthropic_routes.py - Anthropic Messages API compatibility layer

职责：
- /v1/messages - 将 Anthropic Messages 请求转换到现有 OpenAI 兼容工作流
- /v1/messages/count_tokens - 提供 Claude Code 网关所需的最小 token 估算接口
"""

from __future__ import annotations

import codecs
import json
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api import chat as chat_api
from app.api.openai_stop import (
    find_first_stop_sequence,
    normalize_openai_stop_sequences,
    split_stream_text_for_stop_sequences,
)
from app.api.deps import verify_service_token
from app.core.config import get_logger
from app.services.request_manager import request_manager
from app.services.sse_utils import sse_frame_data_text
from app.services.tool_calling import _decode_tool_arguments

logger = get_logger("API.ANTHROPIC")

router = APIRouter()

_TOOL_NAME_CACHE_MAX = 2048
_tool_name_cache_lock = threading.RLock()
_tool_name_by_id_cache: "OrderedDict[str, str]" = OrderedDict()


def _remember_tool_name(tool_use_id: str, tool_name: str) -> None:
    key = str(tool_use_id or "").strip()
    value = str(tool_name or "").strip()
    if not key or not value:
        return
    with _tool_name_cache_lock:
        _tool_name_by_id_cache[key] = value
        _tool_name_by_id_cache.move_to_end(key)
        while len(_tool_name_by_id_cache) > _TOOL_NAME_CACHE_MAX:
            _tool_name_by_id_cache.popitem(last=False)


def _lookup_tool_name(tool_use_id: str) -> str:
    key = str(tool_use_id or "").strip()
    if not key:
        return ""
    with _tool_name_cache_lock:
        value = _tool_name_by_id_cache.get(key, "")
        if value:
            _tool_name_by_id_cache.move_to_end(key)
        return value


class AnthropicMessageRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list = Field(default_factory=list)
    max_tokens: Optional[int] = Field(default=4096, ge=1)
    system: Optional[Any] = Field(default=None)
    stream: Optional[bool] = Field(default=False)
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    tools: Optional[list] = Field(default=None)
    tool_choice: Optional[Any] = Field(default=None)
    stop_sequences: Optional[List[str]] = Field(default=None)


class AnthropicCountTokensRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list = Field(default_factory=list)
    system: Optional[Any] = Field(default=None)
    tools: Optional[list] = Field(default=None)


async def verify_anthropic_auth(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
) -> bool:
    """兼容 Claude Code 常见的 Authorization / x-api-key 两种认证头。"""
    verify_service_token(authorization=authorization, x_api_key=x_api_key)
    return True


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:24]}"


def _pack_anthropic_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _normalize_anthropic_stop_sequences(stop_sequences: Optional[List[str]]) -> List[str]:
    return normalize_openai_stop_sequences(stop_sequences)


async def _close_async_iterator(iterator: Any) -> None:
    close = getattr(iterator, "aclose", None)
    if not callable(close):
        return
    try:
        await close()
    except Exception as e:
        logger.debug(f"关闭 Anthropic 上游流失败: {e}")


def _anthropic_error_type_for_status(status_code: int) -> str:
    mapping = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        413: "request_too_large",
        429: "rate_limit_error",
        500: "api_error",
        502: "api_error",
        503: "api_error",
        504: "api_error",
        529: "overloaded_error",
    }
    return mapping.get(int(status_code or 500), "api_error" if int(status_code or 500) >= 500 else "invalid_request_error")


def _build_anthropic_error_payload(
    *,
    status_code: int,
    message: str,
    error_type: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "type": "error",
        "error": {
            "type": str(error_type or _anthropic_error_type_for_status(status_code)),
            "message": str(message or "gateway_error"),
        },
        "request_id": str(request_id or _new_request_id()),
    }


def _extract_openai_error_details(payload: Dict[str, Any]) -> tuple[str, Optional[str]]:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return (
            str(error.get("message") or "gateway_error"),
            str(error.get("type") or "").strip() or None,
        )
    if isinstance(error, str) and error.strip():
        return error.strip(), None
    return "gateway_error", None


def _convert_openai_error_to_anthropic(
    payload: Dict[str, Any],
    *,
    status_code: int,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    message, upstream_error_type = _extract_openai_error_details(payload)
    return _build_anthropic_error_payload(
        status_code=status_code,
        message=message,
        error_type=upstream_error_type or _anthropic_error_type_for_status(status_code),
        request_id=request_id,
    )


def _serialize_content_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, dict):
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "text":
                    text = str(item.get("text") or "")
                    if text:
                        parts.append(text)
                    continue
                if item_type == "image":
                    parts.append("[image omitted]")
                    continue
            if item is not None:
                parts.append(json.dumps(item, ensure_ascii=False) if not isinstance(item, str) else item)
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _content_value_to_openai_parts(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        parts: List[Dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                part = _anthropic_block_to_openai_part(item)
                if part:
                    parts.append(part)
                    continue

                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "tool_result":
                    nested_parts = _content_value_to_openai_parts(item.get("content"))
                    if nested_parts:
                        parts.extend(nested_parts)
                        continue

            serialized = _serialize_content_value(item)
            if serialized:
                parts.append({"type": "text", "text": serialized})
        return parts

    serialized = _serialize_content_value(value)
    return [{"type": "text", "text": serialized}] if serialized else []


def _anthropic_block_to_openai_part(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    block_type = str(block.get("type") or "").strip().lower()
    if block_type == "text":
        text = str(block.get("text") or "")
        return {"type": "text", "text": text}

    if block_type == "image":
        source = block.get("source") if isinstance(block.get("source"), dict) else {}
        source_type = str(source.get("type") or "").strip().lower()
        if source_type == "base64":
            media_type = str(source.get("media_type") or "image/png").strip() or "image/png"
            data = str(source.get("data") or "").strip()
            if data:
                return {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                }
        if source_type == "url":
            url = str(source.get("url") or "").strip()
            if url:
                return {"type": "image_url", "image_url": {"url": url}}
    return None


def _normalize_openai_content(parts: List[Dict[str, Any]]) -> Any:
    normalized_parts = [item for item in parts if isinstance(item, dict)]
    if not normalized_parts:
        return ""
    if all(str(item.get("type") or "").strip() == "text" for item in normalized_parts):
        return "".join(str(item.get("text") or "") for item in normalized_parts)
    return normalized_parts


def _tool_messages_can_follow_openai(
    normalized_messages: List[Dict[str, Any]],
    tool_messages: List[Dict[str, Any]],
) -> bool:
    if not tool_messages or not normalized_messages:
        return False

    previous: Optional[Dict[str, Any]] = None
    for candidate in reversed(normalized_messages):
        if not isinstance(candidate, dict):
            continue
        if candidate.get("role") == "tool":
            continue
        previous = candidate
        break

    if previous is None:
        return False
    if not isinstance(previous, dict) or previous.get("role") != "assistant":
        return False
    previous_tool_calls = previous.get("tool_calls")
    if not isinstance(previous_tool_calls, list) or not previous_tool_calls:
        return False
    expected_ids = {
        str(item.get("id") or "").strip()
        for item in previous_tool_calls
        if isinstance(item, dict)
    }
    return all(str(item.get("tool_call_id") or "").strip() in expected_ids for item in tool_messages)


def _tool_message_to_user_text(tool_message: Dict[str, Any]) -> str:
    name = str(tool_message.get("name") or "tool").strip() or "tool"
    tool_call_id = str(tool_message.get("tool_call_id") or "").strip()
    content = str(tool_message.get("content") or "")
    header = f"[Tool Result: {name}"
    if tool_call_id:
        header += f" ({tool_call_id})"
    header += "]"
    return f"{header}\n{content}".strip()


def _anthropic_tools_to_openai(tools: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(tools, list):
        return None

    normalized: List[Dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        normalized.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(item.get("description") or "").strip(),
                    "parameters": item.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return normalized or None


def _anthropic_tool_choice_to_openai(tool_choice: Any) -> Any:
    if isinstance(tool_choice, str):
        lowered = tool_choice.strip().lower()
        if lowered == "auto":
            return "auto"
        if lowered in {"any", "required"}:
            return "required"
        if lowered == "none":
            return "none"
        return None

    if not isinstance(tool_choice, dict):
        return None

    choice_type = str(tool_choice.get("type") or "").strip().lower()
    if choice_type == "auto":
        return "auto"
    if choice_type == "any":
        return "required"
    if choice_type == "none":
        return "none"
    if choice_type == "tool":
        name = str(tool_choice.get("name") or "").strip()
        if name:
            return {"type": "function", "function": {"name": name}}
    return None


def _anthropic_parallel_tool_calls(tool_choice: Any) -> Optional[bool]:
    if isinstance(tool_choice, dict) and bool(tool_choice.get("disable_parallel_tool_use")):
        return False
    return None


def _anthropic_system_to_openai_messages(system: Any) -> List[Dict[str, Any]]:
    if system in (None, "", []):
        return []
    if isinstance(system, str):
        return [{"role": "system", "content": system}]
    if isinstance(system, list):
        parts: List[Dict[str, Any]] = []
        for item in system:
            if isinstance(item, dict):
                part = _anthropic_block_to_openai_part(item)
                if part:
                    parts.append(part)
        content = _normalize_openai_content(parts)
        if content not in ("", []):
            return [{"role": "system", "content": content}]
    return [{"role": "system", "content": _serialize_content_value(system)}]


def _anthropic_messages_to_openai(messages: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    tool_name_by_id: Dict[str, str] = {}

    for item in messages or []:
        if not isinstance(item, dict):
            continue

        role = str(item.get("role") or "").strip().lower()
        content = item.get("content")
        if isinstance(content, str):
            if content or role in {"user", "assistant"}:
                normalized.append({"role": role or "user", "content": content})
            continue

        if not isinstance(content, list):
            serialized = _serialize_content_value(content)
            if serialized or role in {"user", "assistant"}:
                normalized.append({"role": role or "user", "content": serialized})
            continue

        text_parts: List[Dict[str, Any]] = []
        assistant_tool_calls: List[Dict[str, Any]] = []
        user_tool_messages: List[Dict[str, Any]] = []

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = str(block.get("type") or "").strip().lower()
            if block_type == "tool_use" and role == "assistant":
                tool_name = str(block.get("name") or "").strip()
                tool_use_id = str(block.get("id") or "").strip() or f"call_{uuid.uuid4().hex[:12]}"
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                assistant_tool_calls.append(
                    {
                        "id": tool_use_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_input, ensure_ascii=False),
                        },
                    }
                )
                if tool_name:
                    tool_name_by_id[tool_use_id] = tool_name
                    _remember_tool_name(tool_use_id, tool_name)
                continue

            if block_type == "tool_result" and role == "user":
                tool_use_id = str(block.get("tool_use_id") or "").strip()
                content_parts = _content_value_to_openai_parts(block.get("content"))
                content_text_parts = [
                    part for part in content_parts if str(part.get("type") or "") == "text"
                ]
                content_image_parts = [
                    part for part in content_parts if str(part.get("type") or "") != "text"
                ]
                content_text = "\n".join(
                    str(part.get("text") or "") for part in content_text_parts
                    if str(part.get("text") or "")
                ).strip()
                if not content_text:
                    content_text = _serialize_content_value(block.get("content"))
                if content_image_parts:
                    content_text = (content_text + "\n[tool_result_image_attached_below]").strip()
                if block.get("is_error"):
                    content_text = "[tool_result_error]\n" + str(content_text or "")
                user_tool_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_use_id,
                        "name": tool_name_by_id.get(tool_use_id) or _lookup_tool_name(tool_use_id) or "tool",
                        "content": content_text,
                    }
                )
                if content_image_parts:
                    tool_name = tool_name_by_id.get(tool_use_id) or _lookup_tool_name(tool_use_id) or "tool"
                    text_parts.append(
                        {
                            "type": "text",
                            "text": f"[Tool Result Media: {tool_name} ({tool_use_id})]\n",
                        }
                    )
                    text_parts.extend(content_image_parts)
                continue

            part = _anthropic_block_to_openai_part(block)
            if part:
                text_parts.append(part)
                continue

            fallback_text = _serialize_content_value(block)
            if fallback_text:
                text_parts.append({"type": "text", "text": fallback_text})

        if role == "assistant":
            message: Dict[str, Any] = {
                "role": "assistant",
                "content": _normalize_openai_content(text_parts),
            }
            if assistant_tool_calls:
                message["tool_calls"] = assistant_tool_calls
                if message["content"] == "":
                    message["content"] = None
            if message.get("content") not in ("", None) or assistant_tool_calls:
                normalized.append(message)
            continue

        if role == "user":
            if user_tool_messages and _tool_messages_can_follow_openai(normalized, user_tool_messages):
                normalized.extend(user_tool_messages)
            elif user_tool_messages:
                fallback_parts = [
                    {"type": "text", "text": _tool_message_to_user_text(tool_message) + "\n"}
                    for tool_message in user_tool_messages
                ]
                text_parts = fallback_parts + text_parts

            user_content = _normalize_openai_content(text_parts)
            if user_content not in ("", []):
                normalized.append({"role": "user", "content": user_content})
            continue

        other_content = _normalize_openai_content(text_parts)
        if other_content not in ("", []):
            normalized.append({"role": role or "user", "content": other_content})

    return normalized


def _anthropic_request_to_openai_payload(body: AnthropicMessageRequest) -> Dict[str, Any]:
    messages = _anthropic_system_to_openai_messages(body.system)
    messages.extend(_anthropic_messages_to_openai(body.messages))
    payload = {
        "model": body.model,
        "messages": messages,
        "stream": bool(body.stream),
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "tools": _anthropic_tools_to_openai(body.tools),
        "tool_choice": _anthropic_tool_choice_to_openai(body.tool_choice),
        "parallel_tool_calls": _anthropic_parallel_tool_calls(body.tool_choice),
    }
    stop_sequences = _normalize_anthropic_stop_sequences(body.stop_sequences)
    if stop_sequences:
        payload["stop"] = stop_sequences
    return payload


def _openai_message_to_anthropic_content(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    content_blocks: List[Dict[str, Any]] = []
    content_value = message.get("content")
    if isinstance(content_value, list):
        content_blocks.extend(_openai_content_parts_to_anthropic_blocks(content_value))
    else:
        content_text = str(content_value or "")
        if content_text:
            content_blocks.append({"type": "text", "text": content_text})

    media_text = _openai_media_items_to_text(message.get("media"))
    if media_text:
        content_blocks.append({"type": "text", "text": media_text})

    for item in message.get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        function_data = item.get("function") if isinstance(item.get("function"), dict) else {}
        args = _decode_tool_arguments(item)
        if args is None:
            args = {}
        content_blocks.append(
            {
                "type": "tool_use",
                "id": str(item.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"),
                "name": str(function_data.get("name") or ""),
                "input": args,
            }
        )

    return content_blocks


def _openai_content_parts_to_anthropic_blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        text = str(content or "")
        return [{"type": "text", "text": text}] if text else []

    blocks: List[Dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            if part:
                blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            text = str(part or "")
            if text:
                blocks.append({"type": "text", "text": text})
            continue

        part_type = str(part.get("type") or "").strip().lower()
        if part_type in {"text", "input_text", "output_text"}:
            text = str(part.get("text") or "")
            if text:
                blocks.append({"type": "text", "text": text})
            continue

        if part_type in {"image_url", "input_image", "output_image"}:
            image_value = part.get("image_url") or part.get("url") or {}
            image_url = str(
                image_value.get("url") if isinstance(image_value, dict) else image_value
            ).strip()
            if image_url:
                blocks.append({"type": "text", "text": f"![image]({image_url})"})
            continue

        if part_type in {"input_audio", "audio_url", "output_audio"}:
            media_value = part.get("audio_url") or part.get("input_audio") or part.get("url") or {}
            media_url = str(
                media_value.get("url") if isinstance(media_value, dict) else media_value
            ).strip()
            if media_url:
                blocks.append({"type": "text", "text": f"[audio]({media_url})"})
            continue

        if part_type in {"input_video", "video_url", "output_video"}:
            media_value = part.get("video_url") or part.get("url") or {}
            media_url = str(
                media_value.get("url") if isinstance(media_value, dict) else media_value
            ).strip()
            if media_url:
                blocks.append({"type": "text", "text": f"[video]({media_url})"})
            continue

        fallback = str(part.get("text") or part.get("output") or "").strip()
        if fallback:
            blocks.append({"type": "text", "text": fallback})
    return blocks


def _openai_media_items_to_text(media_items: Any) -> str:
    media_lines: List[str] = []
    if isinstance(media_items, list):
        for index, item in enumerate(media_items):
            if not isinstance(item, dict):
                continue
            ref = str(item.get("url") or item.get("data_uri") or "").strip()
            if not ref:
                continue
            media_type = str(item.get("media_type") or "image").strip().lower()
            label = str(item.get("label") or item.get("mime") or media_type).strip()
            if media_type == "image":
                media_lines.append(f"![image_{index}]({ref})")
            elif media_type == "audio":
                media_lines.append(f"[audio_{index}]({ref})" + (f" - {label}" if label else ""))
            elif media_type == "video":
                media_lines.append(f"[video_{index}]({ref})" + (f" - {label}" if label else ""))
            else:
                media_lines.append(f"[{media_type}_{index}]({ref})" + (f" - {label}" if label else ""))
    return "\n\n".join(media_lines)


def _openai_response_to_anthropic(
    payload: Dict[str, Any],
    request_model: str,
    stop_sequences: Optional[List[str]] = None,
) -> Dict[str, Any]:
    first_choice = (
        payload.get("choices", [{}])[0]
        if isinstance(payload, dict)
        and isinstance(payload.get("choices"), list)
        and payload.get("choices")
        else {}
    )
    if not isinstance(first_choice, dict):
        first_choice = {}

    message = first_choice.get("message", {}) if isinstance(payload, dict) else {}
    if not isinstance(message, dict):
        message = {}
    if not isinstance(message.get("media"), list) and isinstance(payload.get("media"), list):
        message = dict(message)
        message["media"] = payload.get("media")

    content_blocks = _openai_message_to_anthropic_content(message)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    normalized_stop_sequences = _normalize_anthropic_stop_sequences(stop_sequences)
    finish_reason = str(first_choice.get("finish_reason") or "").strip()
    stop_reason = "tool_use" if message.get("tool_calls") else "end_turn"
    stop_sequence = None
    if stop_reason != "tool_use":
        if finish_reason == "length":
            stop_reason = "max_tokens"
        elif finish_reason == "stop" and normalized_stop_sequences:
            stop_probe = _apply_anthropic_stop_sequences(
                {
                    "content": [
                        dict(block) for block in content_blocks if isinstance(block, dict)
                    ],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                },
                normalized_stop_sequences,
            )
            if stop_probe.get("stop_reason") == "stop_sequence":
                stop_reason = "stop_sequence"
                stop_sequence = str(stop_probe.get("stop_sequence") or "") or None
                probe_content = stop_probe.get("content")
                if isinstance(probe_content, list):
                    content_blocks = probe_content
            else:
                stop_reason = "end_turn"
    return {
        "id": _new_message_id(),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": str(payload.get("model") or request_model or "claude-sonnet-4"),
        "stop_reason": stop_reason,
        "stop_sequence": stop_sequence,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def _apply_anthropic_stop_sequences(
    response: Dict[str, Any],
    stop_sequences: Optional[List[str]],
) -> Dict[str, Any]:
    sequences = _normalize_anthropic_stop_sequences(stop_sequences)
    if not sequences or response.get("stop_reason") == "tool_use":
        return response

    content = response.get("content")
    if not isinstance(content, list):
        return response

    first_match: Optional[tuple[int, str, int]] = None
    rolling_text = ""
    text_positions: List[tuple[int, int, int]] = []
    for index, block in enumerate(content):
        if not isinstance(block, dict) or block.get("type") != "text":
            rolling_text = ""
            text_positions = []
            continue
        text = str(block.get("text") or "")
        start_offset = len(rolling_text)
        rolling_text += text
        end_offset = len(rolling_text)
        text_positions.append((index, start_offset, end_offset))
        match = find_first_stop_sequence(rolling_text, sequences)
        if match is None:
            continue
        position, sequence = match
        match_block_index = index
        match_block_position = 0
        for block_index, block_start, block_end in text_positions:
            if block_start <= position <= block_end:
                match_block_index = block_index
                match_block_position = position - block_start
                break
        candidate = (match_block_index, sequence, match_block_position)
        if first_match is None or (index, position) < (first_match[0], first_match[2]):
            first_match = candidate
        break

    if first_match is None:
        return response

    match_index, matched_sequence, match_position = first_match
    trimmed_content: List[Dict[str, Any]] = []
    for index, block in enumerate(content):
        if index > match_index:
            break
        if index == match_index and isinstance(block, dict) and block.get("type") == "text":
            trimmed_block = dict(block)
            trimmed_block["text"] = str(trimmed_block.get("text") or "")[:match_position]
            if trimmed_block["text"]:
                trimmed_content.append(trimmed_block)
            continue
        trimmed_content.append(block)

    response["content"] = trimmed_content
    response["stop_reason"] = "stop_sequence"
    response["stop_sequence"] = matched_sequence
    return response


def _estimate_message_tokens(text: str) -> int:
    return int(request_manager._estimate_tokens(text))


def _count_tokens_payload(body: AnthropicCountTokensRequest) -> int:
    parts: List[str] = []
    if body.system not in (None, "", []):
        parts.append(_serialize_content_value(body.system))
    parts.append(_serialize_content_value(body.messages))
    if body.tools:
        parts.append(json.dumps(body.tools, ensure_ascii=False))
    return _estimate_message_tokens("\n\n".join(part for part in parts if part))


def _serialize_tool_arguments_fragment(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)


async def _iter_openai_stream_chunks(body_iterator: AsyncIterator[Any]) -> AsyncIterator[Dict[str, Any]]:
    buffer = ""
    utf8_decoder = codecs.getincrementaldecoder("utf-8")("ignore")

    def _decode_sse_segment(segment: str) -> Optional[Dict[str, Any]]:
        payload_text = sse_frame_data_text(segment)
        if not payload_text or payload_text.strip() == "[DONE]":
            return None
        try:
            return json.loads(payload_text)
        except Exception:
            logger.debug(f"无法解析 OpenAI SSE chunk: {payload_text[:200]}")
            return None

    try:
        async for raw_chunk in body_iterator:
            text = utf8_decoder.decode(raw_chunk) if isinstance(raw_chunk, bytes) else str(raw_chunk or "")
            if not text:
                continue
            buffer += text
            buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
            while "\n\n" in buffer:
                segment, buffer = buffer.split("\n\n", 1)
                data = _decode_sse_segment(segment.strip())
                if data is not None:
                    yield data

        decoder_tail = utf8_decoder.decode(b"", final=True)
        if decoder_tail:
            buffer += decoder_tail
            buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
            while "\n\n" in buffer:
                segment, buffer = buffer.split("\n\n", 1)
                data = _decode_sse_segment(segment.strip())
                if data is not None:
                    yield data

        tail = buffer.strip()
        if tail:
            data = _decode_sse_segment(tail)
            if data is not None:
                yield data
    finally:
        await _close_async_iterator(body_iterator)


async def _anthropic_stream_from_openai(
    openai_stream: StreamingResponse,
    request_model: str,
    stop_sequences: Optional[List[str]] = None,
) -> AsyncIterator[str]:
    message_id = _new_message_id()
    yield _pack_anthropic_sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": request_model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )

    text_block_index: Optional[int] = None
    next_content_block_index = 0
    started_block_indices: List[int] = []
    tool_blocks: Dict[Any, Dict[str, Any]] = {}
    stop_reason = "end_turn"
    matched_stop_sequence: Optional[str] = None
    fallback_stop_sequence: Optional[str] = None
    normalized_stop_sequences = _normalize_anthropic_stop_sequences(stop_sequences)
    max_stop_sequence_len = max((len(sequence) for sequence in normalized_stop_sequences), default=0)
    text_tail_buffer = ""
    input_tokens = 0
    output_tokens = 0
    saw_usage_chunk = False
    saw_unscoped_responses_message_content = False
    emitted_responses_content_text: Dict[str, str] = {}
    emitted_responses_message_parts: Dict[str, Dict[int, str]] = {}
    emitted_responses_message_text: Dict[str, str] = {}
    emitted_text_for_usage: List[str] = []

    def _tool_delta_index(item: Dict[str, Any], fallback: int) -> int:
        try:
            return int(item.get("index"))
        except Exception:
            return int(fallback)

    def _start_tool_block(state: Dict[str, Any]) -> str:
        state["started"] = True
        block_index = int(state["block_index"])
        started_block_indices.append(block_index)
        return _pack_anthropic_sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {
                    "type": "tool_use",
                    "id": str(state.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"),
                    "name": str(state.get("name") or "tool"),
                    "input": {},
                },
            },
        )

    def _tool_delta_event(block_index: int, partial_json: str) -> str:
        return _pack_anthropic_sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": block_index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": partial_json,
                },
            },
        )

    def _ensure_tool_block_started(state: Dict[str, Any]) -> List[str]:
        events: List[str] = []
        if not state.get("started") and state.get("name"):
            events.append(_start_tool_block(state))
            pending_json = state.get("pending_json")
            if isinstance(pending_json, list):
                for pending_fragment in pending_json:
                    if pending_fragment:
                        events.append(_tool_delta_event(int(state["block_index"]), str(pending_fragment)))
                pending_json.clear()
        return events

    def _start_text_block() -> str:
        nonlocal text_block_index, next_content_block_index
        text_block_index = next_content_block_index
        next_content_block_index += 1
        started_block_indices.append(text_block_index)
        return _pack_anthropic_sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": text_block_index,
                "content_block": {"type": "text", "text": ""},
            },
        )

    def _text_delta_event(text: str) -> str:
        return _pack_anthropic_sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": int(text_block_index if text_block_index is not None else 0),
                "delta": {"type": "text_delta", "text": text},
            },
        )

    def _content_part_blocks_to_text(parts: Any) -> str:
        blocks = _openai_content_parts_to_anthropic_blocks(parts)
        return "\n".join(
            str(block.get("text") or "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text") or "")
        )

    def _split_stream_text(text: str) -> tuple[str, Optional[str]]:
        nonlocal text_tail_buffer
        emit_text, text_tail_buffer, matched = split_stream_text_for_stop_sequences(
            text_tail_buffer + text,
            normalized_stop_sequences,
            max_stop_sequence_len,
        )
        return emit_text, matched

    async def _emit_text_delta_from_openai_text(text: str) -> AsyncIterator[str]:
        nonlocal matched_stop_sequence, stop_reason
        if not text:
            return
        if text_block_index is None:
            yield _start_text_block()
        emit_text, matched = _split_stream_text(text)
        if emit_text:
            emitted_text_for_usage.append(emit_text)
            yield _text_delta_event(emit_text)
        if matched is not None:
            matched_stop_sequence = matched
            stop_reason = "stop_sequence"

    async def _flush_stream_text_tail() -> AsyncIterator[str]:
        nonlocal text_tail_buffer
        if not text_tail_buffer:
            return
        if text_block_index is None:
            yield _start_text_block()
        tail = text_tail_buffer
        text_tail_buffer = ""
        emitted_text_for_usage.append(tail)
        yield _text_delta_event(tail)

    def _responses_stream_text_delta(data: Dict[str, Any]) -> str:
        event_type = str(data.get("type") or "").strip()
        if event_type == "response.output_text.delta":
            return str(data.get("delta") or "")
        if event_type == "response.output_text.done":
            return str(data.get("text") or data.get("delta") or "")
        if event_type in {"response.content_part.added", "response.content_part.done"}:
            part = data.get("part")
            if not isinstance(part, dict):
                return ""
            return _content_part_blocks_to_text(part)
        return ""

    def _responses_message_item_text(data: Dict[str, Any]) -> str:
        event_type = str(data.get("type") or "").strip()
        if event_type not in {"response.output_item.added", "response.output_item.done"}:
            return ""
        item = data.get("item")
        if not isinstance(item, dict):
            return ""
        if str(item.get("type") or "").strip() != "message":
            return ""
        if str(item.get("role") or "").strip() not in {"", "assistant"}:
            return ""
        return _content_part_blocks_to_text(item.get("content"))

    def _responses_message_key(data: Dict[str, Any]) -> str:
        item = data.get("item")
        if isinstance(item, dict):
            item_id = str(item.get("id") or data.get("item_id") or "").strip()
            if item_id:
                return f"response_message:{item_id}"
        item_id = str(data.get("item_id") or "").strip()
        if item_id:
            return f"response_message:{item_id}"
        output_index = data.get("output_index")
        if output_index is not None:
            return f"response_message_output:{output_index}"
        return "response_message:auto"

    def _responses_content_index(data: Dict[str, Any]) -> int:
        try:
            return int(data.get("content_index"))
        except (TypeError, ValueError):
            return 0

    def _responses_content_key(data: Dict[str, Any]) -> str:
        return f"{_responses_message_key(data)}:content:{_responses_content_index(data)}"

    def _responses_join_message_parts(message_key: str) -> str:
        parts = emitted_responses_message_parts.get(message_key)
        if not isinstance(parts, dict):
            return ""
        return "\n".join(
            text
            for _index, text in sorted(parts.items())
            if str(text or "")
        )

    def _responses_emitted_message_text(message_key: str) -> str:
        message_text = emitted_responses_message_text.get(message_key)
        if message_text:
            return message_text
        return _responses_join_message_parts(message_key)

    def _record_responses_content_text(
        data: Dict[str, Any],
        emitted_text: str,
        *,
        full_text: Optional[str] = None,
    ) -> None:
        event_type = str(data.get("type") or "").strip()
        if event_type not in {
            "response.output_text.delta",
            "response.output_text.done",
            "response.content_part.added",
            "response.content_part.done",
        }:
            return

        message_key = _responses_message_key(data)
        content_key = _responses_content_key(data)
        content_index = _responses_content_index(data)
        parts = emitted_responses_message_parts.setdefault(message_key, {})
        if full_text is not None:
            text = str(full_text or "")
            emitted_responses_content_text[content_key] = text
            parts[content_index] = text
            return
        if not emitted_text:
            return
        text = str(emitted_text)
        emitted_responses_content_text[content_key] = (
            emitted_responses_content_text.get(content_key, "") + text
        )
        parts[content_index] = parts.get(content_index, "") + text

    def _record_responses_message_text(
        data: Dict[str, Any],
        emitted_text: str,
        *,
        full_text: Optional[str] = None,
    ) -> None:
        message_key = _responses_message_key(data)
        if full_text is not None:
            emitted_responses_message_text[message_key] = str(full_text or "")
            return
        if emitted_text:
            emitted_responses_message_text[message_key] = (
                emitted_responses_message_text.get(message_key, "") + str(emitted_text)
            )

    def _responses_done_suffix_for_content(text: str, data: Dict[str, Any]) -> str:
        content_key = _responses_content_key(data)
        emitted_text = emitted_responses_content_text.get(content_key, "")
        if emitted_text:
            if text.startswith(emitted_text):
                return text[len(emitted_text):]
            return ""
        if saw_unscoped_responses_message_content:
            auto_text = _responses_emitted_message_text("response_message:auto")
            if auto_text and text.startswith(auto_text):
                return text[len(auto_text):]
        return text

    def _responses_done_suffix_for_message(text: str, data: Dict[str, Any]) -> str:
        message_key = _responses_message_key(data)
        emitted_text = _responses_emitted_message_text(message_key)
        if emitted_text:
            if text.startswith(emitted_text):
                return text[len(emitted_text):]
            return ""
        if saw_unscoped_responses_message_content:
            auto_text = _responses_emitted_message_text("response_message:auto")
            if auto_text and text.startswith(auto_text):
                return text[len(auto_text):]
        return text

    def _responses_tool_key(item_id: str, output_index: Any = None) -> str:
        normalized_item_id = str(item_id or "").strip()
        if normalized_item_id:
            return f"response:{normalized_item_id}"
        if output_index is not None:
            return f"response_output:{output_index}"
        return f"response_auto:{len(tool_blocks)}"

    def _responses_tool_state(item_id: str, output_index: Any = None) -> Dict[str, Any]:
        nonlocal next_content_block_index
        key = _responses_tool_key(item_id, output_index)
        state = tool_blocks.get(key)
        if state is None:
            block_index = next_content_block_index
            next_content_block_index += 1
            state = {
                "block_index": block_index,
                "id": str(item_id or f"toolu_{uuid.uuid4().hex[:24]}"),
                "name": "",
                "started": False,
                "pending_json": [],
                "seen_argument_delta": False,
                "emitted_arguments": False,
            }
            tool_blocks[key] = state
        return state

    def _apply_responses_tool_metadata(
        state: Dict[str, Any],
        source: Dict[str, Any],
    ) -> None:
        call_id = str(source.get("call_id") or "").strip()
        item_id = str(source.get("id") or source.get("item_id") or "").strip()
        name = str(source.get("name") or "").strip()
        if call_id:
            state["id"] = call_id
        elif item_id and str(state.get("id") or "").startswith("toolu_"):
            state["id"] = item_id
        if name:
            state["name"] = name

    async def _emit_responses_function_call_events(data: Dict[str, Any]) -> AsyncIterator[str]:
        nonlocal stop_reason
        event_type = str(data.get("type") or "").strip()

        item_source: Optional[Dict[str, Any]] = None
        if event_type in {"response.output_item.added", "response.output_item.done"}:
            item = data.get("item")
            if not isinstance(item, dict) or str(item.get("type") or "").strip() != "function_call":
                return
            item_source = item
            item_id = str(item.get("id") or data.get("item_id") or "").strip()
        elif event_type in {
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        }:
            item_id = str(data.get("item_id") or "").strip()
            item_source = data
        else:
            return

        stop_reason = "tool_use"
        state = _responses_tool_state(item_id, data.get("output_index"))
        _apply_responses_tool_metadata(state, item_source)

        for event in _ensure_tool_block_started(state):
            yield event

        if event_type == "response.function_call_arguments.delta":
            partial_json = str(data.get("delta") or "")
            if partial_json:
                state["seen_argument_delta"] = True
                if state.get("started"):
                    yield _tool_delta_event(int(state["block_index"]), partial_json)
                else:
                    pending_json = state.get("pending_json")
                    if isinstance(pending_json, list):
                        pending_json.append(partial_json)
            return

        arguments_source = item_source.get("arguments") if isinstance(item_source, dict) else None
        arguments = _serialize_tool_arguments_fragment(arguments_source)
        if (
            arguments
            and not state.get("seen_argument_delta")
            and not state.get("emitted_arguments")
        ):
            if state.get("started"):
                yield _tool_delta_event(int(state["block_index"]), arguments)
            else:
                pending_json = state.get("pending_json")
                if isinstance(pending_json, list):
                    pending_json.append(arguments)
            state["emitted_arguments"] = True

    stream_iterator = _iter_openai_stream_chunks(openai_stream.body_iterator)
    try:
        async for data in stream_iterator:
            if "error" in data:
                error_message, upstream_error_type = _extract_openai_error_details(data)
                yield _pack_anthropic_sse(
                    "error",
                    {
                        "type": "error",
                        "error": {
                            "type": str(upstream_error_type or "api_error"),
                            "message": error_message,
                        },
                    },
                )
                return

            event_type = str(data.get("type") or "").strip()
            if event_type == "response.failed":
                response_payload = data.get("response") if isinstance(data.get("response"), dict) else {}
                error_source = response_payload.get("error") if response_payload else data.get("error")
                error_payload = {"error": error_source}
                error_message, upstream_error_type = _extract_openai_error_details(error_payload)
                yield _pack_anthropic_sse(
                    "error",
                    {
                        "type": "error",
                        "error": {
                            "type": str(upstream_error_type or "api_error"),
                            "message": error_message,
                        },
                    },
                )
                return

            if event_type in {"response.completed", "response.incomplete"}:
                response_payload = data.get("response") if isinstance(data.get("response"), dict) else {}
                usage = response_payload.get("usage") if isinstance(response_payload.get("usage"), dict) else data.get("usage")
                if isinstance(usage, dict):
                    saw_usage_chunk = True
                    try:
                        input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or input_tokens or 0)
                    except (TypeError, ValueError):
                        pass
                    try:
                        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or output_tokens or 0)
                    except (TypeError, ValueError):
                        pass
                if event_type == "response.incomplete":
                    incomplete_details = (
                        response_payload.get("incomplete_details")
                        if isinstance(response_payload.get("incomplete_details"), dict)
                        else data.get("incomplete_details")
                    )
                    reason = (
                        str(incomplete_details.get("reason") or "").strip()
                        if isinstance(incomplete_details, dict)
                        else ""
                    )
                    if reason == "max_output_tokens":
                        stop_reason = "max_tokens"
                break

            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                responses_message_key = _responses_message_key(data)
                responses_text = _responses_stream_text_delta(data)
                responses_text_is_fallback = event_type in {
                    "response.output_text.done",
                    "response.content_part.done",
                }
                if responses_text:
                    responses_full_text = responses_text if responses_text_is_fallback else None
                    if responses_text_is_fallback:
                        responses_text = _responses_done_suffix_for_content(responses_text, data)
                    if responses_text:
                        async for event in _emit_text_delta_from_openai_text(responses_text):
                            yield event
                        if responses_message_key == "response_message:auto":
                            saw_unscoped_responses_message_content = True
                        _record_responses_content_text(
                            data,
                            responses_text,
                            full_text=responses_full_text,
                        )
                        if matched_stop_sequence is not None:
                            await _close_async_iterator(stream_iterator)
                            break
                    elif responses_full_text is not None:
                        _record_responses_content_text(
                            data,
                            "",
                            full_text=responses_full_text,
                        )

                async for event in _emit_responses_function_call_events(data):
                    async for tail_event in _flush_stream_text_tail():
                        yield tail_event
                    yield event

                responses_message_text = _responses_message_item_text(data)
                if responses_message_text:
                    responses_full_message_text = responses_message_text
                    responses_message_text = _responses_done_suffix_for_message(
                        responses_message_text,
                        data,
                    )
                    if responses_message_text:
                        async for event in _emit_text_delta_from_openai_text(responses_message_text):
                            yield event
                        _record_responses_message_text(
                            data,
                            responses_message_text,
                            full_text=responses_full_message_text,
                        )
                        if matched_stop_sequence is not None:
                            await _close_async_iterator(stream_iterator)
                            break
                    else:
                        _record_responses_message_text(
                            data,
                            "",
                            full_text=responses_full_message_text,
                        )

                usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
                if usage is not None:
                    saw_usage_chunk = True
                    try:
                        input_tokens = int(usage.get("prompt_tokens") or input_tokens or 0)
                    except (TypeError, ValueError):
                        pass
                    try:
                        output_tokens = int(usage.get("completion_tokens") or output_tokens or 0)
                    except (TypeError, ValueError):
                        pass
                continue
            first = choices[0] if isinstance(choices[0], dict) else {}
            delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
            finish_reason = str(first.get("finish_reason") or "").strip()

            content = delta.get("content")
            if isinstance(content, str) and content:
                if text_block_index is None:
                    yield _start_text_block()
                emit_text, matched = _split_stream_text(content)
                if emit_text:
                    emitted_text_for_usage.append(emit_text)
                    yield _text_delta_event(emit_text)
                if matched is not None:
                    matched_stop_sequence = matched
                    stop_reason = "stop_sequence"
                    await _close_async_iterator(stream_iterator)
                    break
            elif isinstance(content, (list, dict)):
                content_text = _content_part_blocks_to_text(content)
                if content_text:
                    if text_block_index is None:
                        yield _start_text_block()
                    emit_text, matched = _split_stream_text(content_text)
                    if emit_text:
                        emitted_text_for_usage.append(emit_text)
                        yield _text_delta_event(emit_text)
                    if matched is not None:
                        matched_stop_sequence = matched
                        stop_reason = "stop_sequence"
                        await _close_async_iterator(stream_iterator)
                        break

            media_items = delta.get("media")
            if not isinstance(media_items, list) and isinstance(data.get("media"), list):
                media_items = data.get("media")
            media_text = _openai_media_items_to_text(media_items)
            if media_text:
                if text_block_index is None:
                    yield _start_text_block()
                async for event in _flush_stream_text_tail():
                    yield event
                emitted_text_for_usage.append(media_text)
                yield _text_delta_event(media_text)

            raw_tool_calls = delta.get("tool_calls")
            if isinstance(raw_tool_calls, list) and raw_tool_calls:
                stop_reason = "tool_use"
                async for event in _flush_stream_text_tail():
                    yield event
                for offset, item in enumerate(raw_tool_calls):
                    if not isinstance(item, dict):
                        continue
                    function_data = item.get("function") if isinstance(item.get("function"), dict) else {}
                    tool_delta_index = _tool_delta_index(item, offset)
                    state = tool_blocks.get(tool_delta_index)
                    if state is None:
                        block_index = next_content_block_index
                        next_content_block_index += 1
                        state = {
                            "block_index": block_index,
                            "id": str(item.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"),
                            "name": "",
                            "started": False,
                            "pending_json": [],
                        }
                        tool_blocks[tool_delta_index] = state
                    item_id = str(item.get("id") or "").strip()
                    if item_id and str(state.get("id") or "").startswith("toolu_"):
                        state["id"] = item_id
                    function_name = str(function_data.get("name") or "").strip()
                    if function_name:
                        state["name"] = function_name

                    if not state.get("started") and state.get("name"):
                        for event in _ensure_tool_block_started(state):
                            yield event

                    partial_json = _serialize_tool_arguments_fragment(function_data.get("arguments"))

                    if partial_json:
                        if state.get("started"):
                            yield _tool_delta_event(int(state["block_index"]), partial_json)
                        else:
                            pending_json = state.get("pending_json")
                            if isinstance(pending_json, list):
                                pending_json.append(partial_json)

            if finish_reason:
                if finish_reason == "tool_calls":
                    stop_reason = "tool_use"
                elif finish_reason == "length":
                    stop_reason = "max_tokens"
                elif finish_reason == "stop":
                    if matched_stop_sequence is not None:
                        stop_reason = "stop_sequence"
                    else:
                        stop_reason = "end_turn"
                else:
                    stop_reason = "end_turn"
    except Exception as e:
        logger.error(f"Anthropic stream upstream failed: {e}")
        yield _pack_anthropic_sse(
            "error",
            {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": str(e or "upstream_stream_error"),
                },
            },
        )
        return

    if text_tail_buffer and matched_stop_sequence is None:
        if text_block_index is None:
            text_block_index = next_content_block_index
            started_block_indices.append(text_block_index)
            yield _pack_anthropic_sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": text_block_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
        yield _pack_anthropic_sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": text_block_index,
                "delta": {"type": "text_delta", "text": text_tail_buffer},
            },
        )
        emitted_text_for_usage.append(text_tail_buffer)

    for state in sorted(tool_blocks.values(), key=lambda item: int(item.get("block_index", 0))):
        if not state.get("started"):
            yield _start_tool_block(state)
        pending_json = state.get("pending_json")
        if isinstance(pending_json, list):
            for pending_fragment in pending_json:
                if pending_fragment:
                    yield _tool_delta_event(int(state["block_index"]), str(pending_fragment))
            pending_json.clear()

    for block_index in started_block_indices:
        yield _pack_anthropic_sse(
            "content_block_stop",
            {"type": "content_block_stop", "index": block_index},
        )

    yield _pack_anthropic_sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": matched_stop_sequence or fallback_stop_sequence,
            },
            "usage": {
                "output_tokens": output_tokens
                if saw_usage_chunk
                else _estimate_message_tokens("".join(emitted_text_for_usage)),
            },
        },
    )
    yield _pack_anthropic_sse("message_stop", {"type": "message_stop"})


def _wrap_openai_response_as_anthropic(
    response: Any,
    body: AnthropicMessageRequest,
    request_id: str,
) -> JSONResponse | StreamingResponse:
    if isinstance(response, StreamingResponse):
        return StreamingResponse(
            _anthropic_stream_from_openai(response, body.model, body.stop_sequences),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "request-id": request_id,
            },
        )

    if not isinstance(response, JSONResponse):
        return JSONResponse(
            content=_build_anthropic_error_payload(
                status_code=500,
                message="unexpected_gateway_response",
                request_id=request_id,
            ),
            status_code=500,
            headers={"request-id": request_id},
        )

    payload = json.loads(response.body.decode("utf-8", errors="ignore"))
    if "error" in payload:
        return JSONResponse(
            content=_convert_openai_error_to_anthropic(
                payload,
                status_code=response.status_code,
                request_id=request_id,
            ),
            status_code=response.status_code,
            headers={"request-id": request_id},
        )
    return JSONResponse(
        content=_apply_anthropic_stop_sequences(
            _openai_response_to_anthropic(payload, body.model, body.stop_sequences),
            body.stop_sequences,
        ),
        status_code=response.status_code,
        headers={"request-id": request_id},
    )


def _build_count_tokens_response(body: AnthropicCountTokensRequest) -> JSONResponse:
    request_id = _new_request_id()
    return JSONResponse(
        content={"input_tokens": _count_tokens_payload(body)},
        headers={"request-id": request_id},
    )


@router.post("/v1/v1/messages")
@router.post("/v1/messages")
async def create_message(
    request: Request,
    body: AnthropicMessageRequest,
    authenticated: bool = Depends(verify_anthropic_auth),
):
    request_id = _new_request_id()
    openai_payload = _anthropic_request_to_openai_payload(body)
    chat_body = chat_api.ChatRequest(**openai_payload)
    response = await chat_api.chat_completions(
        request=request,
        body=chat_body,
        authenticated=authenticated,
    )
    return _wrap_openai_response_as_anthropic(response, body, request_id)


@router.post("/url/{route_domain}/v1/v1/messages")
@router.post("/url/{route_domain}/v1/messages")
async def create_message_with_route_domain(
    route_domain: str,
    request: Request,
    body: AnthropicMessageRequest,
    tab_index: Optional[int] = Query(default=None, ge=1),
    selector: Optional[str] = Query(default=None),
    authenticated: bool = Depends(verify_anthropic_auth),
):
    from app.api import tab_routes as tab_routes_api

    request_id = _new_request_id()
    openai_payload = _anthropic_request_to_openai_payload(body)
    chat_body = tab_routes_api.ChatRequest(**openai_payload)
    response = await tab_routes_api.chat_with_route_domain(
        route_domain=route_domain,
        request=request,
        body=chat_body,
        tab_index=tab_index,
        selector=selector,
        preset_name=None,
        authenticated=authenticated,
    )
    return _wrap_openai_response_as_anthropic(response, body, request_id)


@router.post("/url/{route_domain}/{preset_name}/v1/v1/messages")
@router.post("/url/{route_domain}/{preset_name}/v1/messages")
async def create_message_with_route_domain_and_preset(
    route_domain: str,
    preset_name: str,
    request: Request,
    body: AnthropicMessageRequest,
    tab_index: Optional[int] = Query(default=None, ge=1),
    selector: Optional[str] = Query(default=None),
    authenticated: bool = Depends(verify_anthropic_auth),
):
    from app.api import tab_routes as tab_routes_api

    request_id = _new_request_id()
    openai_payload = _anthropic_request_to_openai_payload(body)
    chat_body = tab_routes_api.ChatRequest(**openai_payload)
    response = await tab_routes_api.chat_with_route_domain(
        route_domain=route_domain,
        request=request,
        body=chat_body,
        tab_index=tab_index,
        selector=selector,
        preset_name=preset_name,
        authenticated=authenticated,
    )
    return _wrap_openai_response_as_anthropic(response, body, request_id)


@router.post("/url/{route_domain}/v1/v1/messages/count_tokens")
@router.post("/url/{route_domain}/v1/messages/count_tokens")
async def count_message_tokens_with_route_domain(
    route_domain: str,
    body: AnthropicCountTokensRequest,
    authenticated: bool = Depends(verify_anthropic_auth),
):
    del route_domain, authenticated
    return _build_count_tokens_response(body)


@router.post("/url/{route_domain}/{preset_name}/v1/v1/messages/count_tokens")
@router.post("/url/{route_domain}/{preset_name}/v1/messages/count_tokens")
async def count_message_tokens_with_route_domain_and_preset(
    route_domain: str,
    preset_name: str,
    body: AnthropicCountTokensRequest,
    authenticated: bool = Depends(verify_anthropic_auth),
):
    del route_domain, preset_name, authenticated
    return _build_count_tokens_response(body)


@router.post("/v1/v1/messages/count_tokens")
@router.post("/v1/messages/count_tokens")
async def count_message_tokens(
    body: AnthropicCountTokensRequest,
    authenticated: bool = Depends(verify_anthropic_auth),
):
    del authenticated
    return _build_count_tokens_response(body)
