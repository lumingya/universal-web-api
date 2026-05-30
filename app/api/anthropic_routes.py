"""
app/api/anthropic_routes.py - Anthropic Messages API compatibility layer

职责：
- /v1/messages - 将 Anthropic Messages 请求转换到现有 OpenAI 兼容工作流
- /v1/messages/count_tokens - 提供 Claude Code 网关所需的最小 token 估算接口
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api import chat as chat_api
from app.core.config import AppConfig, get_logger
from app.services.request_manager import request_manager
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
    if not AppConfig.is_auth_enabled():
        return True

    token_value = AppConfig.get_auth_token()
    if not token_value:
        raise HTTPException(status_code=500, detail="服务配置错误")

    candidates: List[str] = []
    if isinstance(authorization, str):
        raw = authorization.strip()
        if raw:
            candidates.append(raw)
            if raw.lower().startswith("bearer "):
                candidates.append(raw[7:].strip())
    if isinstance(x_api_key, str) and x_api_key.strip():
        candidates.append(x_api_key.strip())

    if token_value not in candidates:
        raise HTTPException(
            status_code=401,
            detail="认证令牌无效",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:24]}"


def _pack_anthropic_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


def _convert_openai_error_to_anthropic(
    payload: Dict[str, Any],
    *,
    status_code: int,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    message = str(error.get("message") or "gateway_error")
    return _build_anthropic_error_payload(
        status_code=status_code,
        message=message,
        error_type=_anthropic_error_type_for_status(status_code),
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
    previous = normalized_messages[-1]
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
    return {
        "model": body.model,
        "messages": messages,
        "stream": bool(body.stream),
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "tools": _anthropic_tools_to_openai(body.tools),
        "tool_choice": _anthropic_tool_choice_to_openai(body.tool_choice),
    }


def _openai_message_to_anthropic_content(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    content_blocks: List[Dict[str, Any]] = []
    content_text = str(message.get("content") or "")
    if content_text:
        content_blocks.append({"type": "text", "text": content_text})

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


def _openai_response_to_anthropic(payload: Dict[str, Any], request_model: str) -> Dict[str, Any]:
    message = (
        payload.get("choices", [{}])[0].get("message", {})
        if isinstance(payload, dict)
        else {}
    )
    if not isinstance(message, dict):
        message = {}

    content_blocks = _openai_message_to_anthropic_content(message)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    stop_reason = "tool_use" if message.get("tool_calls") else "end_turn"
    return {
        "id": _new_message_id(),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": str(payload.get("model") or request_model or "claude-sonnet-4"),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


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


async def _iter_openai_stream_chunks(body_iterator: AsyncIterator[Any]) -> AsyncIterator[Dict[str, Any]]:
    buffer = ""

    def _decode_sse_segment(segment: str) -> Optional[Dict[str, Any]]:
        data_lines: List[str] = []
        for line in segment.splitlines():
            if not line.startswith("data:"):
                continue
            data_lines.append(line[5:].lstrip())
        if not data_lines:
            return None

        payload_text = "\n".join(data_lines).strip()
        if not payload_text or payload_text == "[DONE]":
            return None
        try:
            return json.loads(payload_text)
        except Exception:
            logger.debug(f"无法解析 OpenAI SSE chunk: {payload_text[:200]}")
            return None

    async for raw_chunk in body_iterator:
        text = raw_chunk.decode("utf-8", errors="ignore") if isinstance(raw_chunk, bytes) else str(raw_chunk or "")
        if not text:
            continue
        buffer += text
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


async def _anthropic_stream_from_openai(
    openai_stream: StreamingResponse,
    request_model: str,
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
    tool_blocks: Dict[int, Dict[str, Any]] = {}
    stop_reason = "end_turn"

    def _tool_delta_index(item: Dict[str, Any], fallback: int) -> int:
        try:
            return int(item.get("index"))
        except Exception:
            return int(fallback)

    async for data in _iter_openai_stream_chunks(openai_stream.body_iterator):
        if "error" in data:
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            yield _pack_anthropic_sse(
                "error",
                {
                    "type": "error",
                    "error": {
                        "type": str(error.get("type") or "api_error"),
                        "message": str(error.get("message") or "gateway_error"),
                    },
                },
            )
            return

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        first = choices[0] if isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
        finish_reason = str(first.get("finish_reason") or "").strip()

        content = delta.get("content")
        if isinstance(content, str) and content:
            if text_block_index is None:
                text_block_index = next_content_block_index
                next_content_block_index += 1
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
                    "delta": {"type": "text_delta", "text": content},
                },
            )

        raw_tool_calls = delta.get("tool_calls")
        if isinstance(raw_tool_calls, list) and raw_tool_calls:
            stop_reason = "tool_use"
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
                        "name": str(function_data.get("name") or ""),
                    }
                    tool_blocks[tool_delta_index] = state
                    started_block_indices.append(block_index)
                    yield _pack_anthropic_sse(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": state["id"],
                                "name": state["name"],
                                "input": {},
                            },
                        },
                    )

                arguments_fragment = function_data.get("arguments")
                if isinstance(arguments_fragment, dict):
                    partial_json = json.dumps(arguments_fragment, ensure_ascii=False)
                elif isinstance(arguments_fragment, str):
                    partial_json = arguments_fragment
                else:
                    partial_json = ""

                if partial_json:
                    yield _pack_anthropic_sse(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": state["block_index"],
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": partial_json,
                            },
                        },
                    )

        if finish_reason:
            if finish_reason == "tool_calls":
                stop_reason = "tool_use"
            elif finish_reason == "length":
                stop_reason = "max_tokens"
            else:
                stop_reason = "end_turn"

    for block_index in started_block_indices:
        yield _pack_anthropic_sse(
            "content_block_stop",
            {"type": "content_block_stop", "index": block_index},
        )

    yield _pack_anthropic_sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 0},
        },
    )
    yield _pack_anthropic_sse("message_stop", {"type": "message_stop"})


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

    if isinstance(response, StreamingResponse):
        return StreamingResponse(
            _anthropic_stream_from_openai(response, body.model),
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
        content=_openai_response_to_anthropic(payload, body.model),
        status_code=response.status_code,
        headers={"request-id": request_id},
    )


@router.post("/v1/messages/count_tokens")
async def count_message_tokens(
    body: AnthropicCountTokensRequest,
    authenticated: bool = Depends(verify_anthropic_auth),
):
    del authenticated
    request_id = _new_request_id()
    return JSONResponse(
        content={"input_tokens": _count_tokens_payload(body)},
        headers={"request-id": request_id},
    )
