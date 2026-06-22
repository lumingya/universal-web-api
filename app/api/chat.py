"""
app/api/chat.py - 核心聊天 API

职责：
- OpenAI 兼容的 /v1/chat/completions 接口
- 流式/非流式响应处理
- 模型列表
"""

import json
import codecs
import os
import re
import time
import asyncio
import queue
import threading
import uuid
from typing import Optional, Any, Dict, List, Callable, AsyncIterator

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from app.core.config import get_logger, SSEFormatter
from app.core import get_browser
from app.services.request_manager import (
    request_manager, 
    RequestContext, 
    RequestStatus, 
    watch_client_disconnect
)
from app.services.request_lifecycle import (
    TrackedWorkerExecutionCancelled,
    get_max_request_execute_time_sec,
    cleanup_worker_thread_after_request,
    mark_request_hard_timeout,
    put_worker_queue_item,
    run_tracked_blocking_call,
    wait_worker_queue_item,
)
from app.services.tool_calling import (
    build_tool_completion_response,
    complete_tool_calling_roundtrip_async,
    decode_browser_non_stream_payload,
    extract_tool_calling_assistant_content,
    get_tool_calling_allow_media_postprocess,
    has_tool_calling_request,
    iter_tool_stream_chunks,
    normalize_tool_request,
    summarize_messages_for_debug,
)
from app.api.openai_stop import (
    apply_stop_sequences_to_text,
    build_stop_sequence_stream_state,
    extract_openai_sse_error_message,
    filter_openai_stop_sse_chunk,
    flush_openai_stop_state,
    iter_openai_sse_payloads,
    sse_chunk_has_done,
    sse_frame_data_text,
)
from app.api.deps import (
    verify_dashboard_auth,
    verify_service_auth,
    verify_service_token,
)
from app.utils.model_routing import collect_route_domain_models, inspect_model_route

logger = get_logger("API.CHAT")

router = APIRouter()
MODEL_LIST_CREATED = int(time.time())
STREAM_QUEUE_POLL_TIMEOUT = 0.5
SSE_HEARTBEAT_INTERVAL = 15.0


class _ToolCallingExecutionCancelled(Exception):
    """Raised when a non-stream tool-calling worker is still running after cancellation."""


def _get_tool_calling_cancel_reason(ctx: RequestContext) -> str:
    reason = str(ctx.cancel_reason or "").strip()
    return reason or "tool_calling_cancelled"


def _is_absolute_request_timeout_error(error: Any) -> bool:
    return str(error or "").strip() == "absolute_request_timeout"


def _format_tool_calling_error(error: Any) -> tuple[str, str]:
    if _is_absolute_request_timeout_error(error):
        return "请求执行超过最大绝对超时，已强制中断", "absolute_request_timeout"
    return f"执行错误: {error}", "tool_calling_failed"


async def _run_tracked_tool_calling_worker(
    worker_fn: Callable[[], Any],
    *,
    ctx: RequestContext,
    worker_state: Dict[str, Any],
    label: str,
) -> Any:
    try:
        return await run_tracked_blocking_call(
            worker_fn,
            ctx=ctx,
            worker_state=worker_state,
            label=label,
            poll_timeout=STREAM_QUEUE_POLL_TIMEOUT,
        )
    except TrackedWorkerExecutionCancelled as e:
        raise _ToolCallingExecutionCancelled(str(e) or _get_tool_calling_cancel_reason(ctx))


def _put_worker_queue_item(
    chunk_queue: queue.Queue,
    ctx: RequestContext,
    item: Any,
    *,
    final: bool = False,
) -> bool:
    return put_worker_queue_item(
        chunk_queue,
        ctx,
        item,
        final=final,
        poll_timeout=STREAM_QUEUE_POLL_TIMEOUT,
    )


def _extract_stream_error_message(chunk: Any) -> str:
    return extract_openai_sse_error_message(chunk)


def _extract_chunk_media_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    media_items: List[Dict[str, Any]] = []

    top_level_media = data.get("media")
    if isinstance(top_level_media, list):
        media_items.extend(item for item in top_level_media if isinstance(item, dict))

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        delta = choices[0].get("delta", {})
        if isinstance(delta, dict):
            delta_media = delta.get("media")
            if isinstance(delta_media, list):
                media_items.extend(item for item in delta_media if isinstance(item, dict))
            media_items.extend(_extract_content_part_media_items(delta.get("content")))

    return media_items


def _extract_media_part_ref(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("url") or value.get("data_uri") or "").strip()
    return str(value or "").strip()


def _extract_content_part_media_items(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return []

    media_items: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue

        part_type = str(part.get("type") or "").strip().lower()
        ref = ""
        media_type = ""
        if part_type in {"image_url", "input_image", "output_image"}:
            ref = _extract_media_part_ref(
                part.get("image_url") or part.get("url") or part.get("data_uri")
            )
            media_type = "image"
        elif part_type in {"audio_url", "input_audio", "output_audio"}:
            ref = _extract_media_part_ref(
                part.get("audio_url") or part.get("input_audio") or part.get("url")
            )
            media_type = "audio"
        elif part_type in {"video_url", "input_video", "output_video"}:
            ref = _extract_media_part_ref(
                part.get("video_url") or part.get("input_video") or part.get("url")
            )
            media_type = "video"

        if not ref:
            continue

        media_item: Dict[str, Any] = {"media_type": media_type}
        if ref.startswith("data:"):
            media_item["data_uri"] = ref
        else:
            media_item["url"] = ref

        detail = str(part.get("detail") or "").strip()
        if detail:
            media_item["detail"] = detail
        mime = str(part.get("mime_type") or part.get("mime") or "").strip()
        if mime:
            media_item["mime"] = mime
        label = str(part.get("label") or "").strip()
        if label:
            media_item["label"] = label
        media_items.append(media_item)

    return media_items


def _extract_delta_content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type in {"text", "input_text", "output_text"}:
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    if isinstance(content, dict):
        item_type = str(content.get("type") or "").strip().lower()
        if item_type in {"text", "input_text", "output_text"}:
            return str(content.get("text") or "")
    return ""


def _dedupe_media_items(media_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()

    for item in media_items or []:
        media_type = str(item.get("media_type") or "").strip().lower()
        ref = str(item.get("url") or item.get("data_uri") or "").strip()
        key = (media_type, ref)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped


def _validate_image_inputs(messages: list) -> None:
    try:
        has_image_declared = False
        has_any_valid_image = False

        for m in messages or []:
            content = m.get("content")

            if isinstance(content, str):
                s = content.strip()
                if "image_url" in s:
                    has_image_declared = True
                if "data:image" in s and "base64," in s and not s.endswith("base64,"):
                    has_any_valid_image = True
                continue

            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") != "image_url":
                        continue

                    has_image_declared = True
                    image_url = item.get("image_url") or {}
                    url = image_url.get("url") if isinstance(image_url, dict) else str(image_url)

                    if not isinstance(url, str):
                        continue

                    u = url.strip()
                    if u.startswith("data:image") and "base64," in u and not u.endswith("base64,"):
                        has_any_valid_image = True
                    elif u.startswith("http://") or u.startswith("https://"):
                        has_any_valid_image = True
                    elif os.path.exists(u):
                        has_any_valid_image = True

        if has_image_declared and not has_any_valid_image:
            raise HTTPException(
                status_code=400,
                detail="检测到图片输入，但未收到任何可用图片数据。"
                       "上游发送的是空的 data:image/...;base64, 前缀（或缺失图片 URL/base64）。"
                       "请让上游客户端透传完整 base64 或可访问的图片 URL。"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"图片输入校验异常（已放行）: {e}")


# ================= 请求模型 =================

class ChatRequest(BaseModel):
    """聊天请求模型"""
    model: str = Field(default="gpt-3.5-turbo")
    messages: list = Field(...)
    stream: Optional[bool] = Field(default=False)
    temperature: Optional[float] = Field(default=0.7, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    n: Optional[int] = Field(default=1, ge=1)
    response_format: Optional[dict] = Field(default=None)
    stop: Optional[Any] = Field(default=None)
    tools: Optional[list] = Field(default=None)
    tool_choice: Optional[Any] = Field(default=None)
    parallel_tool_calls: Optional[bool] = Field(default=None)
    functions: Optional[list] = Field(default=None)
    function_call: Optional[Any] = Field(default=None)
    preset_name: Optional[str] = Field(default=None)
    stream_options: Optional[dict] = Field(default=None)


class ResponsesRequest(BaseModel):
    """Responses API 请求模型（兼容 Codex / OpenAI Responses wire format）"""
    model: str = Field(default="gpt-3.5-turbo")
    input: Optional[Any] = Field(default="")
    instructions: Optional[str] = Field(default=None)
    stream: Optional[bool] = Field(default=False)
    temperature: Optional[float] = Field(default=1.0, ge=0, le=2)
    max_output_tokens: Optional[int] = Field(default=None, ge=1)
    tools: Optional[list] = Field(default=None)
    tool_choice: Optional[Any] = Field(default=None)
    parallel_tool_calls: Optional[bool] = Field(default=None)
    text: Optional[dict] = Field(default=None)
    metadata: Optional[dict] = Field(default=None)
    prompt: Optional[Any] = Field(default=None)
    previous_response_id: Optional[str] = Field(default=None)
    reasoning: Optional[dict] = Field(default=None)
    store: Optional[bool] = Field(default=None)
    top_p: Optional[float] = Field(default=None, ge=0, le=1)
    truncation: Optional[str] = Field(default=None)
    user: Optional[str] = Field(default=None)
    stop: Optional[Any] = Field(default=None)

    model_config = {
        "extra": "allow",
    }


def _is_single_choice_request(body: ChatRequest) -> bool:
    try:
        return int(getattr(body, "n", 1) or 1) <= 1
    except (TypeError, ValueError):
        return True


def _new_response_id() -> str:
    return f"resp_{int(time.time() * 1000)}_{uuid.uuid4().hex[:10]}"


def _new_response_item_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _pack_responses_sse(event: str, data: Dict[str, Any]) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


def _format_rfc3339_timestamp(timestamp: Optional[float] = None) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(float(timestamp if timestamp is not None else time.time())),
    )


def _normalize_response_tool_choice(value: Any) -> Any:
    if isinstance(value, dict):
        value_type = str(value.get("type") or "").strip().lower()
        if value_type == "function":
            function_block = value.get("function") if isinstance(value.get("function"), dict) else {}
            name = str(function_block.get("name") or value.get("name") or "").strip()
            if name:
                return {
                    "type": "function",
                    "function": {
                        "name": name,
                    },
                }
    return value


def _normalize_responses_tools(tools: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(tools, list):
        return None

    normalized: List[Dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type != "function":
            continue

        if isinstance(item.get("function"), dict):
            fn = item["function"]
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(fn.get("description") or "").strip(),
                        "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
            )
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
                    "parameters": item.get("parameters", {"type": "object", "properties": {}}),
                },
            }
        )

    return normalized or None


def _normalize_responses_text_format(text_config: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(text_config, dict):
        return None

    format_config = text_config.get("format")
    if not isinstance(format_config, dict):
        return None

    format_type = str(format_config.get("type") or "text").strip().lower()
    if format_type == "json_schema":
        if isinstance(format_config.get("json_schema"), dict):
            payload = format_config["json_schema"]
        else:
            payload = {}
            if "name" in format_config:
                payload["name"] = format_config.get("name")
            if "schema" in format_config:
                payload["schema"] = format_config.get("schema")
            if "strict" in format_config:
                payload["strict"] = format_config.get("strict")
        return {
            "type": "json_schema",
            "json_schema": payload,
        }

    if format_type == "json_object":
        return {"type": "json_object"}

    return {"type": format_type}


def _normalize_response_message_content(content: Any) -> Any:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _normalize_response_message_content([content])
    if not isinstance(content, list):
        return str(content)

    normalized_parts: List[Dict[str, Any]] = []
    leading_text_parts: List[str] = []

    for part in content:
        if isinstance(part, str):
            if part:
                leading_text_parts.append(part)
            continue
        if not isinstance(part, dict):
            text = str(part or "")
            if text:
                leading_text_parts.append(text)
            continue

        part_type = str(part.get("type") or "").strip().lower()
        if part_type in {"input_text", "output_text", "text"}:
            text = str(part.get("text") or "")
            if not text:
                continue
            if normalized_parts:
                normalized_parts.append({"type": "text", "text": text})
            else:
                leading_text_parts.append(text)
            continue

        if part_type in {"input_image", "image_url", "output_image"}:
            image_value = part.get("image_url")
            if isinstance(image_value, dict):
                image_url = str(image_value.get("url") or "").strip()
                detail = str(image_value.get("detail") or "").strip()
            else:
                image_url = str(image_value or part.get("url") or "").strip()
                detail = str(part.get("detail") or "").strip()

            if not image_url:
                continue

            image_payload: Dict[str, Any] = {"url": image_url}
            if detail:
                image_payload["detail"] = detail

            normalized_parts.append(
                {
                    "type": "image_url",
                    "image_url": image_payload,
                }
            )
            continue

        if part_type in {"input_audio", "audio_url", "output_audio", "input_video", "video_url", "output_video"}:
            if part_type in {"input_audio", "audio_url", "output_audio"}:
                media_value = part.get("audio_url") or part.get("input_audio") or part.get("url") or ""
                media_label = "audio"
            else:
                media_value = part.get("video_url") or part.get("url") or ""
                media_label = "video"
            media_url = str(
                media_value.get("url") if isinstance(media_value, dict) else media_value
            ).strip()
            if media_url:
                media_text = f"[{media_label}]({media_url})"
                if normalized_parts:
                    normalized_parts.append({"type": "text", "text": media_text})
                else:
                    leading_text_parts.append(media_text)
            continue

        text_fallback_value = part.get("text")
        if text_fallback_value is None:
            text_fallback_value = part.get("output")
        if text_fallback_value is None:
            text_fallback_value = part.get("content")
        if text_fallback_value is None:
            text_fallback = json.dumps(part, ensure_ascii=False)
        elif isinstance(text_fallback_value, (dict, list)):
            text_fallback = json.dumps(text_fallback_value, ensure_ascii=False)
        else:
            text_fallback = str(text_fallback_value).strip()
        if text_fallback:
            if normalized_parts:
                normalized_parts.append({"type": "text", "text": text_fallback})
            else:
                leading_text_parts.append(text_fallback)

    joined_text = "\n".join(part for part in leading_text_parts if part)

    if not normalized_parts:
        return joined_text

    if joined_text:
        normalized_parts.insert(
            0,
            {
                "type": "text",
                "text": joined_text,
            },
        )
    return normalized_parts


def _normalize_response_input_role(role: Any) -> str:
    normalized_role = str(role or "user").strip().lower()
    if normalized_role == "developer":
        return "system"
    if normalized_role in {"system", "user", "assistant", "tool"}:
        return normalized_role
    return "user"


def _normalize_response_tool_output_content(content: Any) -> Any:
    normalized = _normalize_response_message_content(content)
    if normalized in ("", None):
        return ""
    return normalized


def _responses_tool_output_can_follow_openai(
    messages: List[Dict[str, Any]],
    tool_call_id: str,
) -> bool:
    if not tool_call_id:
        return False

    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role == "tool":
            continue
        if role != "assistant":
            return False
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            return False
        return any(
            isinstance(item, dict)
            and str(item.get("id") or "").strip() == tool_call_id
            for item in tool_calls
        )

    return False


def _responses_tool_output_fallback_content(
    item: Dict[str, Any],
    output: Any,
) -> Any:
    call_id = str(item.get("call_id") or item.get("tool_call_id") or item.get("id") or "").strip()
    name = str(item.get("name") or "").strip()
    header = "[Function Call Output"
    if name:
        header += f": {name}"
    if call_id:
        header += f" ({call_id})"
    header += "]"

    if isinstance(output, list):
        return [{"type": "text", "text": f"{header}\n"}] + output
    if isinstance(output, dict):
        output = json.dumps(output, ensure_ascii=False)
    return f"{header}\n{str(output or '')}".strip()


def _response_function_call_to_tool_call(item: Dict[str, Any]) -> Dict[str, Any]:
    arguments = item.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
    return {
        "id": str(item.get("call_id") or item.get("id") or _new_response_item_id("call")).strip(),
        "type": "function",
        "function": {
            "name": str(item.get("name") or "").strip(),
            "arguments": arguments,
        },
    }


def _normalize_chat_style_tool_calls(tool_calls: Any) -> Optional[List[Any]]:
    if not isinstance(tool_calls, list):
        return None

    normalized: List[Any] = []
    for item in tool_calls:
        if not isinstance(item, dict):
            normalized.append(item)
            continue

        next_item = dict(item)
        function_data = next_item.get("function")
        if isinstance(function_data, dict):
            next_function = dict(function_data)
            arguments = next_function.get("arguments")
            if not isinstance(arguments, str):
                next_function["arguments"] = json.dumps(
                    arguments if arguments is not None else {},
                    ensure_ascii=False,
                )
            next_item["function"] = next_function
        normalized.append(next_item)

    return normalized


def _append_response_input_item(messages: List[Dict[str, Any]], item: Any) -> None:
    if item is None:
        return

    if isinstance(item, str):
        if item:
            messages.append({"role": "user", "content": item})
        return

    if not isinstance(item, dict):
        text = str(item)
        if text:
            messages.append({"role": "user", "content": text})
        return

    role = str(item.get("role") or "").strip().lower()
    item_type = str(item.get("type") or "").strip().lower()

    if role or item_type == "message":
        normalized_role = _normalize_response_input_role(role)
        content = item.get("content")
        if content is None and "text" in item:
            content = item.get("text")
        message_payload: Dict[str, Any] = {
            "role": normalized_role,
            "content": _normalize_response_message_content(content),
        }
        if normalized_role == "assistant":
            tool_calls = _normalize_chat_style_tool_calls(item.get("tool_calls"))
            if tool_calls is not None:
                message_payload["tool_calls"] = tool_calls
        if normalized_role == "tool":
            tool_call_id = str(
                item.get("tool_call_id") or item.get("call_id") or item.get("id") or ""
            ).strip()
            if tool_call_id:
                message_payload["tool_call_id"] = tool_call_id
            name = str(item.get("name") or "").strip()
            if name:
                message_payload["name"] = name
        messages.append(message_payload)
        return

    if item_type == "function_call":
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_response_function_call_to_tool_call(item)],
            }
        )
        return

    if item_type in {"function_call_output", "tool_result"}:
        output_source = item.get("output")
        if output_source is None and "content" in item:
            output_source = item.get("content")
        output = _normalize_response_tool_output_content(output_source)
        tool_call_id = str(
            item.get("call_id") or item.get("tool_call_id") or item.get("id") or ""
        ).strip()
        if _responses_tool_output_can_follow_openai(messages, tool_call_id):
            tool_message: Dict[str, Any] = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": output,
            }
            name = str(item.get("name") or "").strip()
            if name:
                tool_message["name"] = name
            messages.append(tool_message)
        else:
            messages.append(
                {
                    "role": "user",
                    "content": _responses_tool_output_fallback_content(item, output),
                }
            )
        return

    content = item.get("content")
    if content is None and "text" in item:
        content = item.get("text")
    normalized_content = _normalize_response_message_content(content)
    if normalized_content not in ("", [], None):
        messages.append({"role": "user", "content": normalized_content})


def _responses_input_to_messages(body: ResponsesRequest) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []

    instructions = str(body.instructions or "").strip()
    if instructions:
        messages.append({"role": "system", "content": instructions})

    source = body.input
    if source in (None, "") and body.prompt not in (None, ""):
        source = body.prompt

    if isinstance(source, list):
        pending_tool_calls: List[Dict[str, Any]] = []

        def _flush_pending_tool_calls() -> None:
            nonlocal pending_tool_calls
            if not pending_tool_calls:
                return
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": pending_tool_calls,
                }
            )
            pending_tool_calls = []

        for item in source:
            if isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "function_call":
                pending_tool_calls.append(_response_function_call_to_tool_call(item))
                continue
            _flush_pending_tool_calls()
            _append_response_input_item(messages, item)
        _flush_pending_tool_calls()
    else:
        _append_response_input_item(messages, source)

    if not messages:
        messages.append({"role": "user", "content": ""})

    return messages


def _responses_request_to_chat_request(body: ResponsesRequest, *, stream: bool) -> ChatRequest:
    return ChatRequest(
        model=body.model,
        messages=_responses_input_to_messages(body),
        stream=stream,
        temperature=body.temperature,
        max_tokens=body.max_output_tokens,
        response_format=_normalize_responses_text_format(body.text),
        tools=_normalize_responses_tools(body.tools),
        tool_choice=_normalize_response_tool_choice(body.tool_choice),
        parallel_tool_calls=body.parallel_tool_calls,
        stop=body.stop,
    )


def _response_message_item_from_text(text: str) -> Dict[str, Any]:
    return {
        "id": _new_response_item_id("msg"),
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": str(text or ""),
                "annotations": [],
            }
        ],
    }


def _response_content_part_from_media_item(item: Any, index: int) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    media_type = str(item.get("media_type") or item.get("type") or "image").strip().lower()
    ref = str(item.get("url") or item.get("data_uri") or "").strip()
    if not ref:
        return None

    if media_type == "audio":
        payload: Dict[str, Any] = {"type": "output_audio", "audio_url": ref}
    elif media_type == "video":
        payload = {"type": "output_video", "video_url": ref}
    else:
        payload = {"type": "output_image", "image_url": ref}

    payload["annotations"] = []
    payload["index"] = index
    mime = str(item.get("mime") or "").strip()
    if mime:
        payload["mime_type"] = mime
    label = str(item.get("label") or "").strip()
    if label:
        payload["label"] = label
    return payload


def _response_message_item_from_content(
    text: str,
    media_items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = []
    if text:
        content.append(
            {
                "type": "output_text",
                "text": str(text or ""),
                "annotations": [],
            }
        )

    for item in media_items or []:
        media_part = _response_content_part_from_media_item(item, len(content))
        if media_part is not None:
            content.append(media_part)

    if not content:
        content.append(
            {
                "type": "output_text",
                "text": "",
                "annotations": [],
            }
        )

    return {
        "id": _new_response_item_id("msg"),
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": content,
    }


def _response_function_call_item_from_tool_call(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    function_data = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    call_id = str(tool_call.get("id") or _new_response_item_id("call")).strip()
    item_id = (
        f"fc_{call_id[5:]}" if call_id.startswith("call_") else _new_response_item_id("fc")
    )
    arguments = function_data.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
    return {
        "id": item_id,
        "type": "function_call",
        "status": "completed",
        "call_id": call_id,
        "name": str(function_data.get("name") or "").strip(),
        "arguments": arguments,
    }


def _chat_payload_to_responses_output(chat_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    choices = chat_payload.get("choices") if isinstance(chat_payload.get("choices"), list) else []
    if not choices:
        return []

    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    if not isinstance(message, dict):
        return []

    output: List[Dict[str, Any]] = []
    tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        output.append(_response_function_call_item_from_tool_call(tool_call))

    content = message.get("content")
    media_items = message.get("media")
    if not isinstance(media_items, list):
        media_items = chat_payload.get("media") if isinstance(chat_payload.get("media"), list) else []
    if content not in ("", None) or media_items:
        output.append(_response_message_item_from_content(str(content or ""), media_items))

    return output


def _build_in_progress_response_item(item: Dict[str, Any]) -> Dict[str, Any]:
    item_type = str(item.get("type") or "").strip().lower()
    if item_type == "function_call":
        return {
            "id": item.get("id"),
            "type": "function_call",
            "status": "in_progress",
            "call_id": item.get("call_id"),
            "name": item.get("name"),
            "arguments": "",
        }

    if item_type == "message":
        return {
            "id": item.get("id"),
            "type": "message",
            "status": "in_progress",
            "role": item.get("role") or "assistant",
            "content": [],
        }

    return dict(item)


def _build_responses_usage(chat_payload: Dict[str, Any]) -> Dict[str, int]:
    usage = chat_payload.get("usage") if isinstance(chat_payload.get("usage"), dict) else {}
    return {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def _build_responses_text_payload(body: ResponsesRequest) -> Dict[str, Any]:
    format_payload = _normalize_responses_text_format(body.text)
    text_payload = {
        "format": format_payload if isinstance(format_payload, dict) else {"type": "text"},
        "verbosity": "medium",
    }
    if isinstance(body.text, dict) and body.text.get("verbosity") is not None:
        text_payload["verbosity"] = body.text.get("verbosity")
    return text_payload


def _responses_parallel_tool_calls(body: ResponsesRequest) -> bool:
    if body.parallel_tool_calls is None:
        return True
    return bool(body.parallel_tool_calls)


def _responses_tool_choice(body: ResponsesRequest) -> Any:
    if body.tool_choice is None:
        return "auto"
    return body.tool_choice


def _responses_metadata(body: ResponsesRequest) -> Dict[str, Any]:
    if isinstance(body.metadata, dict):
        return body.metadata
    return {}


def _responses_reasoning_payload(body: ResponsesRequest) -> Dict[str, Any]:
    if isinstance(body.reasoning, dict):
        return body.reasoning
    return {"effort": None, "summary": None}


def _responses_request_settings(body: ResponsesRequest) -> Dict[str, Any]:
    return {
        "max_output_tokens": body.max_output_tokens,
        "parallel_tool_calls": _responses_parallel_tool_calls(body),
        "previous_response_id": body.previous_response_id,
        "reasoning": _responses_reasoning_payload(body),
        "store": True if body.store is None else bool(body.store),
        "temperature": body.temperature if body.temperature is not None else 1.0,
        "tool_choice": _responses_tool_choice(body),
        "top_p": body.top_p if body.top_p is not None else 1.0,
        "truncation": body.truncation if body.truncation is not None else "disabled",
        "user": body.user,
        "metadata": _responses_metadata(body),
    }


def _build_responses_object(
    body: ResponsesRequest,
    chat_payload: Dict[str, Any],
    *,
    response_id: Optional[str] = None,
    created_at: Optional[int] = None,
    status: str = "completed",
    error: Optional[Dict[str, Any]] = None,
    incomplete_details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    response_obj: Dict[str, Any] = {
        "id": response_id or _new_response_id(),
        "object": "response",
        "created_at": int(created_at if created_at is not None else time.time()),
        "status": status,
        "error": error,
        "incomplete_details": incomplete_details,
        "instructions": body.instructions,
        "model": body.model,
        "output": _chat_payload_to_responses_output(chat_payload),
        "tools": body.tools or [],
        "text": _build_responses_text_payload(body),
        "usage": _build_responses_usage(chat_payload),
    }
    response_obj.update(_responses_request_settings(body))

    if status == "completed":
        response_obj["completed_at"] = int(time.time())

    return response_obj


def _responses_completion_status_from_chat_payload(chat_payload: Dict[str, Any]) -> tuple[str, Optional[Dict[str, Any]], str]:
    choices = chat_payload.get("choices") if isinstance(chat_payload.get("choices"), list) else []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        finish_reason = str(choice.get("finish_reason") or "").strip().lower()
        if finish_reason == "length":
            return "incomplete", {"reason": "max_output_tokens"}, "response.incomplete"
        if finish_reason == "content_filter":
            return "incomplete", {"reason": "content_filter"}, "response.incomplete"
    return "completed", None, "response.completed"


def _responses_error_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        error_payload = dict(error)
        error_payload["message"] = str(error_payload.get("message") or "responses_backing_request_failed")
        error_payload["type"] = str(error_payload.get("type") or "execution_error")
        if error_payload.get("code") is None:
            error_payload["code"] = "responses_backing_request_failed"
        return error_payload
    if isinstance(error, str) and error.strip():
        return {
            "message": error.strip(),
            "type": "execution_error",
            "code": "responses_backing_request_failed",
        }
    return {
        "message": "responses_backing_request_failed",
        "type": "execution_error",
        "code": "responses_backing_request_failed",
    }


def _decode_json_response(response: JSONResponse) -> Dict[str, Any]:
    raw = response.body
    if isinstance(raw, bytes):
        text = raw.decode("utf-8")
    else:
        text = str(raw or "")
    data = json.loads(text or "{}")
    return data if isinstance(data, dict) else {}


async def _run_chat_completion_final(
    request: Request,
    body: ChatRequest,
    authenticated: bool,
) -> tuple[int, Dict[str, Any]]:
    effective_body = body if body.stream is False else body.model_copy(update={"stream": False})
    response = await chat_completions(
        request=request,
        body=effective_body,
        authenticated=authenticated,
    )

    if not isinstance(response, JSONResponse):
        raise RuntimeError("responses_backing_request_unexpected_response_type")

    return int(response.status_code), _decode_json_response(response)


async def _stream_responses_compat(
    request: Request,
    body: ResponsesRequest,
    authenticated: bool,
):
    response_id = _new_response_id()
    created_at = int(time.time())
    sequence_number = 0
    next_output_index = 0
    message_item_id = ""
    message_output_index: Optional[int] = None
    text_part_started = False
    message_item_done = False
    collected_text_parts: List[str] = []
    collected_media: List[Dict[str, Any]] = []
    tool_call_states: Dict[int, Dict[str, Any]] = {}
    finish_reason: Optional[str] = None
    usage_payload: Dict[str, Any] = {}
    sse_buffer = ""
    utf8_decoder = codecs.getincrementaldecoder("utf-8")("ignore")

    def _pack_response_event(event: str, data: Dict[str, Any]) -> str:
        nonlocal sequence_number
        sequence_number += 1

        payload = dict(data)
        if event in {"response.created", "response.completed", "response.failed", "response.incomplete"}:
            payload = {"response": payload}
        payload.setdefault("type", event)
        payload["sequence_number"] = sequence_number
        return _pack_responses_sse(event, payload)

    def _next_output_index() -> int:
        nonlocal next_output_index
        value = next_output_index
        next_output_index += 1
        return value

    def _decode_stream_chunk(chunk: Any, *, final: bool = False) -> str:
        if isinstance(chunk, bytes):
            return utf8_decoder.decode(chunk, final=final)
        if final:
            return utf8_decoder.decode(b"", final=True)
        return str(chunk or "")

    def _iter_buffered_openai_sse_payloads(chunk: str) -> List[Dict[str, Any]]:
        nonlocal sse_buffer
        if not chunk:
            return []

        sse_buffer += chunk.replace("\r\n", "\n").replace("\r", "\n")
        payloads: List[Dict[str, Any]] = []
        while "\n\n" in sse_buffer:
            frame, sse_buffer = sse_buffer.split("\n\n", 1)
            if not frame.strip():
                continue
            payloads.extend(iter_openai_sse_payloads(frame + "\n\n"))
        return payloads

    def _flush_buffered_openai_sse_payloads() -> List[Dict[str, Any]]:
        nonlocal sse_buffer
        tail = sse_buffer
        sse_buffer = ""
        if not tail.strip():
            return []
        return iter_openai_sse_payloads(tail + "\n\n")

    def _first_choice(payload: Dict[str, Any]) -> Dict[str, Any]:
        choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
        if not choices or not isinstance(choices[0], dict):
            return {}
        return choices[0]

    def _ensure_message_item() -> List[str]:
        nonlocal message_item_id, message_output_index
        if message_output_index is not None:
            return []
        message_item_id = _new_response_item_id("msg")
        message_output_index = _next_output_index()
        return [
            _pack_response_event(
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "response_id": response_id,
                    "output_index": message_output_index,
                    "item": {
                        "id": message_item_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                },
            )
        ]

    def _ensure_text_part() -> List[str]:
        nonlocal text_part_started
        events = _ensure_message_item()
        if text_part_started:
            return events
        text_part_started = True
        events.append(
            _pack_response_event(
                "response.content_part.added",
                {
                    "type": "response.content_part.added",
                    "response_id": response_id,
                    "item_id": message_item_id,
                    "output_index": message_output_index,
                    "content_index": 0,
                    "part": {
                        "type": "output_text",
                        "text": "",
                        "annotations": [],
                    },
                },
            )
        )
        return events

    def _final_message_item() -> Dict[str, Any]:
        content: List[Dict[str, Any]] = []
        text = "".join(collected_text_parts)
        if text_part_started or text:
            content.append(
                {
                    "type": "output_text",
                    "text": text,
                    "annotations": [],
                }
            )
        for media_item in _dedupe_media_items(collected_media):
            media_part = _response_content_part_from_media_item(media_item, len(content))
            if media_part is not None:
                content.append(media_part)
        if not content:
            content.append(
                {
                    "type": "output_text",
                    "text": "",
                    "annotations": [],
                }
            )
        return {
            "id": message_item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": content,
        }

    def _final_tool_item(state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": state["item_id"],
            "type": "function_call",
            "status": "completed",
            "call_id": state["call_id"],
            "name": state["name"],
            "arguments": state["arguments"],
        }

    def _ensure_tool_state(index: int, tool_delta: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
        function_delta = tool_delta.get("function") if isinstance(tool_delta.get("function"), dict) else {}
        state = tool_call_states.get(index)
        if state is not None:
            if tool_delta.get("id"):
                state["call_id"] = str(tool_delta.get("id") or state["call_id"])
            if function_delta.get("name"):
                state["name"] = str(function_delta.get("name") or state["name"])
            return state, []

        call_id = str(tool_delta.get("id") or _new_response_item_id("call")).strip()
        name = str(function_delta.get("name") or "").strip()
        item_id = _response_function_call_item_from_tool_call(
            {"id": call_id, "function": {"name": name, "arguments": ""}}
        )["id"]
        state = {
            "output_index": _next_output_index(),
            "item_id": item_id,
            "call_id": call_id,
            "name": name,
            "arguments": "",
            "done": False,
        }
        tool_call_states[index] = state
        return state, [
            _pack_response_event(
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "response_id": response_id,
                    "output_index": state["output_index"],
                    "item": {
                        "id": state["item_id"],
                        "type": "function_call",
                        "status": "in_progress",
                        "call_id": state["call_id"],
                        "name": state["name"],
                        "arguments": "",
                    },
                },
            )
        ]

    def _completed_output() -> List[Dict[str, Any]]:
        output: List[Optional[Dict[str, Any]]] = [None] * max(next_output_index, 0)
        for state in tool_call_states.values():
            output[int(state["output_index"])] = _final_tool_item(state)
        if message_output_index is not None:
            output[int(message_output_index)] = _final_message_item()
        return [item for item in output if isinstance(item, dict)]

    def _completed_chat_payload() -> Dict[str, Any]:
        message: Dict[str, Any] = {
            "role": "assistant",
            "content": "".join(collected_text_parts),
        }
        if collected_media:
            message["media"] = _dedupe_media_items(collected_media)
        if tool_call_states:
            message["tool_calls"] = [
                {
                    "id": state["call_id"],
                    "type": "function",
                    "function": {
                        "name": state["name"],
                        "arguments": state["arguments"],
                    },
                }
                for _index, state in sorted(tool_call_states.items())
            ]
        return {
            "model": body.model,
            "choices": [
                {
                    "message": message,
                    "finish_reason": finish_reason or "stop",
                }
            ],
            "usage": usage_payload,
            "media": _dedupe_media_items(collected_media),
        }

    def _failed_response(payload: Dict[str, Any], error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return _build_responses_object(
            body,
            payload if isinstance(payload, dict) else {"choices": [], "usage": {}},
            response_id=response_id,
            created_at=created_at,
            status="failed",
            error=error or _responses_error_payload(payload),
        )

    def _complete_tool_events(state: Dict[str, Any]) -> List[str]:
        if state.get("done"):
            return []
        state["done"] = True
        return [
            _pack_response_event(
                "response.function_call_arguments.done",
                {
                    "type": "response.function_call_arguments.done",
                    "response_id": response_id,
                    "item_id": state["item_id"],
                    "output_index": state["output_index"],
                    "arguments": state["arguments"],
                    "name": state["name"],
                    "call_id": state["call_id"],
                },
            ),
            _pack_response_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "response_id": response_id,
                    "output_index": state["output_index"],
                    "item": _final_tool_item(state),
                },
            ),
        ]

    def _complete_message_events() -> List[str]:
        nonlocal message_item_done
        if message_output_index is None or message_item_done:
            return []
        message_item_done = True
        text = "".join(collected_text_parts)
        events: List[str] = []
        if text_part_started:
            events.extend(
                [
                    _pack_response_event(
                        "response.output_text.done",
                        {
                            "type": "response.output_text.done",
                            "response_id": response_id,
                            "item_id": message_item_id,
                            "output_index": message_output_index,
                            "content_index": 0,
                            "text": text,
                        },
                    ),
                    _pack_response_event(
                        "response.content_part.done",
                        {
                            "type": "response.content_part.done",
                            "response_id": response_id,
                            "item_id": message_item_id,
                            "output_index": message_output_index,
                            "content_index": 0,
                            "part": {
                                "type": "output_text",
                                "text": text,
                                "annotations": [],
                            },
                        },
                    ),
                ]
            )

        content_index = 1 if text_part_started else 0
        for media_item in _dedupe_media_items(collected_media):
            media_part = _response_content_part_from_media_item(media_item, content_index)
            if media_part is None:
                continue
            events.append(
                _pack_response_event(
                    "response.content_part.added",
                    {
                        "type": "response.content_part.added",
                        "response_id": response_id,
                        "item_id": message_item_id,
                        "output_index": message_output_index,
                        "content_index": content_index,
                        "part": media_part,
                    },
                )
            )
            events.append(
                _pack_response_event(
                    "response.content_part.done",
                    {
                        "type": "response.content_part.done",
                        "response_id": response_id,
                        "item_id": message_item_id,
                        "output_index": message_output_index,
                        "content_index": content_index,
                        "part": media_part,
                    },
                )
            )
            content_index += 1

        events.append(
            _pack_response_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "response_id": response_id,
                    "output_index": message_output_index,
                    "item": _final_message_item(),
                },
            )
        )
        return events

    async def _iter_backing_chunks(streaming_response: StreamingResponse):
        iterator = streaming_response.body_iterator.__aiter__()
        next_task = asyncio.create_task(iterator.__anext__())
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {next_task},
                    timeout=SSE_HEARTBEAT_INTERVAL,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    if await request.is_disconnected():
                        next_task.cancel()
                        try:
                            await next_task
                        except asyncio.CancelledError:
                            pass
                        return
                    yield ": keepalive\n\n"
                    continue
                try:
                    chunk = next_task.result()
                except StopAsyncIteration:
                    break
                yield chunk
                next_task = asyncio.create_task(iterator.__anext__())
        finally:
            if not next_task.done():
                next_task.cancel()
                try:
                    await next_task
                except asyncio.CancelledError:
                    pass
            close = getattr(iterator, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    pass

    async def _emit_openai_payload_events(payloads: List[Dict[str, Any]]) -> AsyncIterator[str]:
        nonlocal finish_reason, usage_payload
        for payload in payloads:
            if "error" in payload:
                yield _pack_response_event("response.failed", _failed_response(payload))
                return

            if isinstance(payload.get("usage"), dict):
                usage_payload = dict(payload.get("usage") or {})

            choice = _first_choice(payload)
            if not choice:
                continue
            if choice.get("finish_reason") is not None:
                finish_reason = str(choice.get("finish_reason") or "")

            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            collected_media.extend(_extract_chunk_media_items(payload))

            content_text = _extract_delta_content_text(delta.get("content"))
            if content_text:
                collected_text_parts.append(content_text)
                for event_chunk in _ensure_text_part():
                    yield event_chunk
                yield _pack_response_event(
                    "response.output_text.delta",
                    {
                        "type": "response.output_text.delta",
                        "response_id": response_id,
                        "item_id": message_item_id,
                        "output_index": message_output_index,
                        "content_index": 0,
                        "delta": content_text,
                    },
                )

            tool_call_deltas = delta.get("tool_calls")
            if not isinstance(tool_call_deltas, list):
                continue
            for fallback_index, tool_delta in enumerate(tool_call_deltas):
                if not isinstance(tool_delta, dict):
                    continue
                try:
                    tool_index = int(tool_delta.get("index", fallback_index) or fallback_index)
                except Exception:
                    tool_index = fallback_index
                state, added_events = _ensure_tool_state(tool_index, tool_delta)
                for event_chunk in added_events:
                    yield event_chunk
                function_delta = tool_delta.get("function") if isinstance(tool_delta.get("function"), dict) else {}
                if function_delta.get("name"):
                    state["name"] = str(function_delta.get("name") or state["name"])
                arguments_delta = function_delta.get("arguments")
                if arguments_delta:
                    text_delta = str(arguments_delta)
                    state["arguments"] = str(state.get("arguments") or "") + text_delta
                    yield _pack_response_event(
                        "response.function_call_arguments.delta",
                        {
                            "type": "response.function_call_arguments.delta",
                            "response_id": response_id,
                            "item_id": state["item_id"],
                            "output_index": state["output_index"],
                            "delta": text_delta,
                        },
                    )

    yield _pack_response_event(
        "response.created",
        {
            "id": response_id,
            "object": "response",
            "created_at": created_at,
            "status": "in_progress",
            "error": None,
            "incomplete_details": None,
            "instructions": body.instructions,
            "model": body.model,
            "output": [],
            "tools": body.tools or [],
            "text": _build_responses_text_payload(body),
            **_responses_request_settings(body),
        },
    )

    try:
        chat_response = await chat_completions(
            request=request,
            body=_responses_request_to_chat_request(body, stream=True),
            authenticated=authenticated,
        )

        if isinstance(chat_response, JSONResponse):
            yield _pack_response_event(
                "response.failed",
                _failed_response(_decode_json_response(chat_response)),
            )
            return
        if not isinstance(chat_response, StreamingResponse):
            raise RuntimeError("responses_backing_request_unexpected_response_type")

        async for raw_chunk in _iter_backing_chunks(chat_response):
            if await request.is_disconnected():
                return

            chunk = _decode_stream_chunk(raw_chunk)
            if not chunk:
                continue
            if chunk.lstrip().startswith(":") and "data:" not in chunk:
                yield chunk
                continue

            async for event_chunk in _emit_openai_payload_events(_iter_buffered_openai_sse_payloads(chunk)):
                yield event_chunk
                if event_chunk.startswith("event: response.failed"):
                    return

        decoder_tail = _decode_stream_chunk(b"", final=True)
        if decoder_tail:
            async for event_chunk in _emit_openai_payload_events(_iter_buffered_openai_sse_payloads(decoder_tail)):
                yield event_chunk
                if event_chunk.startswith("event: response.failed"):
                    return

        async for event_chunk in _emit_openai_payload_events(_flush_buffered_openai_sse_payloads()):
            yield event_chunk
            if event_chunk.startswith("event: response.failed"):
                return

        if collected_media and message_output_index is None:
            for event_chunk in _ensure_message_item():
                yield event_chunk
        for _index, state in sorted(tool_call_states.items()):
            for event_chunk in _complete_tool_events(state):
                yield event_chunk
        for event_chunk in _complete_message_events():
            yield event_chunk

        chat_payload = _completed_chat_payload()
        response_status, incomplete_details, terminal_event = _responses_completion_status_from_chat_payload(chat_payload)
        completed = _build_responses_object(
            body,
            chat_payload,
            response_id=response_id,
            created_at=created_at,
            status=response_status,
            error=None,
            incomplete_details=incomplete_details,
        )
        completed["output"] = _completed_output()
        yield _pack_response_event(terminal_event, completed)
    except Exception as e:
        yield _pack_response_event(
            "response.failed",
            _failed_response(
                {"choices": [], "usage": {}},
                error={
                    "message": str(e),
                    "type": "execution_error",
                    "code": "responses_backing_request_failed",
                },
            ),
        )

# ================= response_format 转化 =================

DEFAULT_RESPONSE_FORMAT_HINTS = {
    "json_object": "\n\n[系统指令：请以 JSON 格式输出你的回复。确保输出是有效的 JSON 对象，不要包含 ```json 代码块标记或任何其他非 JSON 文字。]",
    "json_schema": "\n\n[系统指令：请严格按照以下 JSON Schema 格式输出你的回复，确保输出是有效的 JSON，不要包含代码块标记：\n{schema}]",
    "text": ""
}


def _get_response_format_hint(format_type: str) -> str:
    """获取指定格式类型的提示词模板"""
    format_type = str(format_type or "text").strip().lower() or "text"
    try:
        from app.services.config_engine import config_engine
        hints = config_engine.global_config.get("response_format_hints")
        if hints and isinstance(hints, dict) and format_type in hints:
            return hints[format_type]
    except Exception:
        pass
    return DEFAULT_RESPONSE_FORMAT_HINTS.get(format_type, "")


def _apply_response_format(messages: list, response_format: dict) -> list:
    """将 response_format 转化为提示词并追加到最后一条用户消息"""
    if not isinstance(response_format, dict) or not response_format:
        return messages

    format_type = str(response_format.get("type") or "text").strip().lower() or "text"
    hint_template = _get_response_format_hint(format_type)

    if not hint_template:
        return messages

    hint = hint_template

    if format_type == "json_schema":
        json_schema = response_format.get("json_schema", {})
        schema_content = (
            json_schema.get("schema", json_schema)
            if isinstance(json_schema, dict)
            else json_schema
        )
        try:
            schema_str = json.dumps(schema_content, ensure_ascii=False, indent=2)
            hint = hint_template.replace("{schema}", schema_str)
        except Exception:
            hint = hint_template.replace("{schema}", str(schema_content))
    
    import copy
    new_messages = copy.deepcopy(messages)
    
    for i in range(len(new_messages) - 1, -1, -1):
        msg = new_messages[i]
        if msg.get("role") == "user":
            content = msg.get("content", "")
            
            if isinstance(content, str):
                msg["content"] = content + hint
                break
            elif isinstance(content, list):
                for j in range(len(content) - 1, -1, -1):
                    item = content[j]
                    if isinstance(item, dict) and item.get("type") == "text":
                        item["text"] = item.get("text", "") + hint
                        break
                else:
                    content.append({"type": "text", "text": hint})
                break
    
    return new_messages


def _should_include_stream_usage(body: ChatRequest) -> bool:
    stream_options = body.stream_options
    return isinstance(stream_options, dict) and bool(stream_options.get("include_usage"))


def _pack_stream_usage_chunk(model: str) -> str:
    data = {
        "id": f"chatcmpl-usage-{int(time.time() * 1000)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _maybe_pack_stream_usage_chunk(body: ChatRequest) -> Optional[str]:
    if not _should_include_stream_usage(body):
        return None
    return _pack_stream_usage_chunk(body.model)


def _split_sse_done_frame(chunk: Any) -> tuple[str, bool]:
    if not isinstance(chunk, str) or not sse_chunk_has_done(chunk):
        return str(chunk or ""), False

    frames = chunk.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
    kept_frames = []
    had_done = False
    for frame in frames:
        if not frame:
            continue
        if sse_frame_data_text(frame).strip() == "[DONE]":
            had_done = True
            continue
        kept_frames.append(f"{frame}\n\n")
    return "".join(kept_frames), had_done


def _iter_stream_chunks_with_optional_usage(body: ChatRequest, chunks):
    usage_emitted = False
    for chunk in chunks:
        emit_chunk, chunk_had_done = _split_sse_done_frame(chunk)
        if emit_chunk:
            yield emit_chunk
        if chunk_had_done:
            if not usage_emitted:
                usage_chunk = _maybe_pack_stream_usage_chunk(body)
                if usage_chunk:
                    usage_emitted = True
                    yield usage_chunk
            yield _pack_done()


# ================= 认证依赖 =================


async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    """验证对外服务 API 的 Bearer Token。"""
    return await verify_service_auth(authorization)


# ================= 核心聊天 API =================

@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatRequest,
    authenticated: bool = Depends(verify_auth)
):
    """
    OpenAI 兼容的聊天补全接口
    """
    _validate_image_inputs(body.messages)

    if isinstance(body.response_format, dict) and body.response_format:
        format_type = str(body.response_format.get("type") or "text").strip().lower() or "text"
        if format_type != "text":
            logger.debug(f"检测到 response_format.type={format_type}，转化为提示词")
            body.messages = _apply_response_format(body.messages, body.response_format)
            setattr(body, "_response_format_applied", True)

    try:
        browser = get_browser(auto_connect=False)
        tabs = browser.tab_pool.get_tabs_with_index()
        route_info = inspect_model_route(body.model, tabs)
        route_domain = str(route_info.get("route_domain") or "")
    except Exception as e:
        logger.debug(f"模型路由解析失败（已忽略）: {e}")
        route_info = {
            "normalized_model": str(body.model or "").strip().lower(),
            "route_domain": "",
            "matched_id": "",
            "match_type": "error",
            "available_model_ids": [],
        }
        route_domain = ""

    if route_domain:
        logger.info(
            "模型路由命中: "
            f"model={body.model!r}, normalized={route_info.get('normalized_model')!r}, "
            f"matched_id={route_info.get('matched_id')!r}, match_type={route_info.get('match_type')}, "
            f"route_domain={route_domain}, available={route_info.get('available_model_ids')}"
        )
        from app.api import tab_routes as tab_routes_api

        route_body = tab_routes_api.ChatRequest(**body.model_dump())
        if bool(getattr(body, "_response_format_applied", False)):
            setattr(route_body, "_response_format_applied", True)
        return await tab_routes_api.chat_with_route_domain(
            route_domain=route_domain,
            request=request,
            body=route_body,
            tab_index=None,
            selector=None,
            preset_name=body.preset_name,
            authenticated=authenticated,
        )
    elif route_info.get("match_type") == "none" and route_info.get("normalized_model"):
        logger.debug(
            "模型路由未命中: "
            f"model={body.model!r}, normalized={route_info.get('normalized_model')!r}, "
            f"available={route_info.get('available_model_ids')}"
        )

    ctx = request_manager.create_request()
    try:
        raw_input_len = sum(len(str(msg.get("content") or "")) for msg in body.messages if isinstance(msg, dict))
        logger.info(f"[DIAG] 接收到的原始请求 messages 总字符长度: {raw_input_len} 字符, 消息数: {len(body.messages)}")
    except Exception as e:
        logger.debug(f"[DIAG] 估算原始请求长度失败: {e}")

    request_manager.record_request_input(
        ctx,
        body.model_dump(),
        endpoint="/v1/chat/completions",
        route_domain=route_domain,
        preset_name=body.preset_name,
    )
    with logger.context(ctx.request_id):
        logger.info("开始")

        try:
            logger.debug(
                "[chat] 请求消息摘要: "
                f"{summarize_messages_for_debug(body.messages)}"
            )
        except Exception as e:
            logger.debug(f"[chat] 请求消息摘要生成失败: {e}")

        if has_tool_calling_request(
            messages=body.messages,
            tools=body.tools,
            functions=body.functions,
        ):
            if body.stream:
                return StreamingResponse(
                    _stream_tool_calling_with_lifecycle(request, body, ctx),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no"
                    }
                )
            return await _non_stream_tool_calling_with_lifecycle(request, body, ctx)

        if body.stream:
            return StreamingResponse(
                _stream_with_lifecycle(request, body, ctx),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        else:
            return await _non_stream_with_lifecycle(request, body, ctx)


@router.post("/v1/responses")
async def create_response(
    request: Request,
    body: ResponsesRequest,
    authenticated: bool = Depends(verify_auth)
):
    """OpenAI Responses API 兼容入口。"""
    chat_body = _responses_request_to_chat_request(body, stream=False)

    if body.stream:
        return StreamingResponse(
            _stream_responses_compat(
                request=request,
                body=body,
                authenticated=authenticated,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        status_code, payload = await _run_chat_completion_final(
            request=request,
            body=chat_body,
            authenticated=authenticated,
        )
    except Exception as e:
        failed = _build_responses_object(
            body,
            {"choices": [], "usage": {}},
            status="failed",
            error={
                "message": str(e),
                "type": "execution_error",
                "code": "responses_backing_request_failed",
            },
        )
        return JSONResponse(content=failed, status_code=500)

    if status_code >= 400 or "error" in payload:
        failed = _build_responses_object(
            body,
            payload,
            status="failed",
            error=_responses_error_payload(payload),
        )
        return JSONResponse(content=failed, status_code=status_code)

    response_status, incomplete_details, _terminal_event = _responses_completion_status_from_chat_payload(payload)
    response_obj = _build_responses_object(
        body,
        payload,
        status=response_status,
        error=None,
        incomplete_details=incomplete_details,
    )
    return JSONResponse(content=response_obj)


async def _stream_with_lifecycle(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext
):
    """流式响应 + 完整生命周期管理"""
    disconnect_task = None
    worker_thread = None
    chunk_queue: Optional[queue.Queue] = None

    try:
        request_manager.start_request(ctx)
        disconnect_task = asyncio.create_task(
            watch_client_disconnect(request, ctx, check_interval=0.3)
        )

        browser = get_browser(auto_connect=False)

        chunk_queue = queue.Queue(maxsize=100)

        def worker():
            gen = None
            chunk_counter = 0
            try:
                gen = browser.execute_workflow(
                    body.messages,
                    stream=True,
                    task_id=ctx.request_id,
                    stop_checker=ctx.should_stop,
                )

                for chunk in gen:
                    chunk_counter += 1
                    
                    
                    if ctx.should_stop():
                        cancel_reason = str(ctx.cancel_reason or "unknown")
                        if cancel_reason in {"cleanup", "client_disconnected", "coroutine_cancelled"}:
                            logger.debug(f"工作线程检测到停止: {cancel_reason}")
                        else:
                            logger.info(f"工作线程检测到取消: {cancel_reason}")
                        break
                    if not _put_worker_queue_item(chunk_queue, ctx, chunk):
                        logger.debug("工作线程停止入队，结束流式生产")
                        break

            except Exception as e:
                logger.error(f"工作线程异常: {e}")
                _put_worker_queue_item(chunk_queue, ctx, ("ERROR", str(e)), final=True)
            finally:
                if gen is not None:
                    try:
                        gen.close()
                    except Exception as e:
                        logger.debug(f"关闭工作流生成器失败（忽略）: {e}")
                _put_worker_queue_item(chunk_queue, ctx, None, final=True)
                logger.debug("工作线程结束")

        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()

        last_sse_emit_at = time.monotonic()
        request_started_at = time.monotonic()
        max_execute_time_sec = get_max_request_execute_time_sec()
        done_emitted = False
        client_disconnected = False
        stop_state = build_stop_sequence_stream_state(
            body.stop,
            single_choice=_is_single_choice_request(body),
            upstream_single_choice=True,
        )

        while True:
            if mark_request_hard_timeout(
                ctx,
                request_started_at,
                max_execute_time_sec,
                label="chat_stream",
            ):
                request_manager.capture_error(ctx, "请求执行超过最大绝对超时", code="absolute_request_timeout")
                ctx.mark_failed("absolute_request_timeout")
                yield _pack_error_done("请求执行超过最大绝对超时，已强制中断", "absolute_request_timeout")
                done_emitted = True
                break

            if await request.is_disconnected():
                logger.debug("客户端断开")
                ctx.request_cancel("client_disconnected")
                client_disconnected = True
                break

            try:
                chunk = await wait_worker_queue_item(
                    chunk_queue,
                    timeout=STREAM_QUEUE_POLL_TIMEOUT,
                )
            except queue.Empty:
                if time.monotonic() - last_sse_emit_at >= SSE_HEARTBEAT_INTERVAL:
                    yield SSEFormatter.pack_comment("keepalive")
                    last_sse_emit_at = time.monotonic()
                continue

            if chunk is None:
                logger.debug("收到结束标记")
                break

            if isinstance(chunk, tuple) and chunk[0] == "ERROR":
                logger.error(f"错误: {chunk[1]}")
                request_manager.capture_error(ctx, chunk[1], code="worker_error")
                ctx.mark_failed(chunk[1])
                yield _pack_error_done(f"执行错误: {chunk[1]}", "internal_error")
                break

            # 🔍 探针：记录发送给客户端的 chunk
            has_images = '"images"' in chunk if isinstance(chunk, str) else False
            if has_images:
                logger.info(f"[SEND] 发送包含图片的 chunk 给客户端")
            
            outgoing_chunks = filter_openai_stop_sse_chunk(chunk, stop_state, body.model)
            for outgoing_chunk in outgoing_chunks:
                emit_chunk, chunk_had_done = _split_sse_done_frame(outgoing_chunk)
                if emit_chunk:
                    request_manager.capture_response_chunk(ctx, emit_chunk)
                    yield emit_chunk
                last_sse_emit_at = time.monotonic()
                error_message = _extract_stream_error_message(emit_chunk or outgoing_chunk)
                if error_message:
                    logger.error(f"流式响应返回错误事件: {error_message}")
                    request_manager.capture_error(ctx, error_message, code="stream_error")
                    ctx.mark_failed(error_message)
                    done_chunk = _pack_done()
                    request_manager.capture_response_chunk(ctx, done_chunk)
                    yield done_chunk
                    done_emitted = True
                    break
                if chunk_had_done:
                    usage_chunk = _maybe_pack_stream_usage_chunk(body)
                    if usage_chunk:
                        request_manager.capture_response_chunk(ctx, usage_chunk)
                        yield usage_chunk
                    done_chunk = _pack_done()
                    request_manager.capture_response_chunk(ctx, done_chunk)
                    yield done_chunk
                    done_emitted = True
                    ctx.request_cancel("stream_done")
                    ctx.mark_completed()
            if done_emitted:
                break
            if stop_state.stopped:
                done_emitted = True
                ctx.request_cancel("stop_sequence")
                ctx.mark_completed()
                break
            if ctx.status == RequestStatus.FAILED:
                break
            await asyncio.sleep(0)

        if (
            not client_disconnected
            and not done_emitted
            and ctx.status != RequestStatus.FAILED
        ):
            for tail_chunk in flush_openai_stop_state(stop_state, body.model):
                request_manager.capture_response_chunk(ctx, tail_chunk)
                yield tail_chunk
            usage_chunk = _maybe_pack_stream_usage_chunk(body)
            if usage_chunk:
                request_manager.capture_response_chunk(ctx, usage_chunk)
                yield usage_chunk
            done_chunk = _pack_done()
            request_manager.capture_response_chunk(ctx, done_chunk)
            yield done_chunk

        if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
            ctx.mark_completed()

    except asyncio.CancelledError:
        logger.debug("协程取消")
        ctx.request_cancel("coroutine_cancelled")
        raise

    except Exception as e:
        logger.error(f"异常: {e}")
        request_manager.capture_error(ctx, e, code="internal_error")
        ctx.mark_failed(str(e))
        yield _pack_error(f"执行错误: {str(e)}", "internal_error")
        yield _pack_done()

    finally:
        if worker_thread and worker_thread.is_alive():
            await cleanup_worker_thread_after_request(
                worker_thread,
                ctx,
                completed=ctx.status == RequestStatus.COMPLETED,
                cancel_reason="cleanup",
                join_timeout=5.0,
                retire_reason="worker_cleanup_timeout",
                completed_join_timeout=0.5,
            )

        if chunk_queue is not None:
            try:
                while not chunk_queue.empty():
                    chunk_queue.get_nowait()
            except Exception:
                pass

        if disconnect_task:
            disconnect_task.cancel()
            try:
                await disconnect_task
            except asyncio.CancelledError:
                pass

        request_manager.finish_request(ctx, success=(ctx.status == RequestStatus.COMPLETED))


async def _non_stream_with_lifecycle(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext
) -> JSONResponse:
    """非流式响应 + 生命周期管理"""
    collected_content = []
    collected_media = []
    error_data = None
    sse_buffer = ""

    def _iter_buffered_stream_payloads(chunk: str) -> List[Dict[str, Any]]:
        nonlocal sse_buffer
        if not chunk:
            return []

        sse_buffer += chunk.replace("\r\n", "\n").replace("\r", "\n")
        payloads: List[Dict[str, Any]] = []
        while "\n\n" in sse_buffer:
            frame, sse_buffer = sse_buffer.split("\n\n", 1)
            if not frame.strip():
                continue
            payloads.extend(iter_openai_sse_payloads(frame + "\n\n"))
        return payloads

    def _flush_buffered_stream_payloads() -> List[Dict[str, Any]]:
        nonlocal sse_buffer
        tail = sse_buffer
        sse_buffer = ""
        if not tail.strip():
            return []
        return iter_openai_sse_payloads(tail + "\n\n")

    def _consume_stream_payload(data: Dict[str, Any]) -> bool:
        nonlocal error_data
        if "error" in data:
            error_data = data
            return False

        collected_media.extend(_extract_chunk_media_items(data))

        if "choices" in data and data["choices"]:
            delta = data["choices"][0].get("delta", {})
            content = delta.get("content", "")
            content_text = _extract_delta_content_text(content)
            if content_text:
                collected_content.append(content_text)
        return True

    async for chunk in _stream_with_lifecycle(request, body, ctx):
        if isinstance(chunk, str):
            for data in _iter_buffered_stream_payloads(chunk):
                if not _consume_stream_payload(data):
                    break
            if error_data:
                break

    if not error_data:
        for data in _flush_buffered_stream_payloads():
            if not _consume_stream_payload(data):
                break

    if error_data:
        return JSONResponse(content=error_data, status_code=500)

    full_content = "".join(collected_content)
    placeholder_pattern = re.compile(
        r"^\s*https?://(?:[\w.-]+\.)?googleusercontent\.com/(?:image_generation_content|generated_music_content)/\d+\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    full_content = placeholder_pattern.sub("", full_content)
    full_content = re.sub(r"\n{3,}", "\n\n", full_content).strip()
    full_content = apply_stop_sequences_to_text(full_content, body.stop)

    response = SSEFormatter.pack_non_stream(
        full_content,
        model=body.model,
        media=_dedupe_media_items(collected_media),
    )
    request_manager.capture_response_payload(ctx, response)

    return JSONResponse(content=response)


def _execute_browser_non_stream_messages(
    browser,
    messages: List[Dict[str, Any]],
    request_id: str,
    stop_checker=None,
) -> Dict[str, Any]:
    payload = None
    for chunk in browser.execute_workflow(
        messages,
        stream=False,
        task_id=request_id,
        stop_checker=stop_checker,
        allow_media_postprocess=get_tool_calling_allow_media_postprocess(),
    ):
        payload = chunk

    if not payload:
        raise RuntimeError("empty_browser_response")

    data = decode_browser_non_stream_payload(payload)
    if "error" in data:
        error = data.get("error") or {}
        raise RuntimeError(str(error.get("message") or "browser_execution_failed"))
    return data


def _extract_assistant_content(response: Dict[str, Any]) -> str:
    try:
        return extract_tool_calling_assistant_content(response)
    except Exception:
        return ""


async def _run_tool_calling_async(
    browser,
    body: ChatRequest,
    request_id: str,
    stop_checker=None,
    worker_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tools, tool_choice = normalize_tool_request(
        tools=body.tools,
        tool_choice=body.tool_choice,
        functions=body.functions,
        function_call=body.function_call,
    )
    tracked_worker_state = worker_state if isinstance(worker_state, dict) else {}

    async def _round_executor(browser_messages: List[Dict[str, str]]) -> str:
        worker_fn = lambda: _extract_assistant_content(
            _execute_browser_non_stream_messages(
                browser=browser,
                messages=browser_messages,
                request_id=request_id,
                stop_checker=stop_checker,
            )
        )
        if isinstance(tracked_worker_state.get("ctx"), RequestContext):
            return await _run_tracked_tool_calling_worker(
                worker_fn,
                ctx=tracked_worker_state["ctx"],
                worker_state=tracked_worker_state,
                label=f"{request_id[:8]}-round",
            )
        return await asyncio.to_thread(worker_fn)

    parsed = await complete_tool_calling_roundtrip_async(
        messages=body.messages,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=body.parallel_tool_calls,
        round_executor=_round_executor,
        stop_checker=stop_checker,
    )
    if not parsed.get("tool_calls"):
        parsed = dict(parsed)
        parsed["content"] = apply_stop_sequences_to_text(
            str(parsed.get("content") or ""),
            body.stop,
        )
    return build_tool_completion_response(body.model, parsed)


async def _complete_tool_calling_with_lifecycle(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
) -> Dict[str, Any]:
    disconnect_task = None
    worker_state: Dict[str, Any] = {"thread": None, "label": None, "ctx": ctx}
    try:
        disconnect_task = asyncio.create_task(
            watch_client_disconnect(request, ctx, check_interval=0.3)
        )

        browser = get_browser(auto_connect=False)
        request_manager.start_request(ctx)

        response = await _run_tool_calling_async(
            browser,
            body,
            ctx.request_id,
            ctx.should_stop,
            worker_state=worker_state,
        )
        request_manager.capture_response_payload(ctx, response)

        if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
            ctx.mark_completed()

        return response

    except _ToolCallingExecutionCancelled:
        cancel_reason = _get_tool_calling_cancel_reason(ctx)
        if cancel_reason == "absolute_request_timeout":
            request_manager.capture_error(ctx, "请求执行超过最大绝对超时", code="absolute_request_timeout")
            ctx.mark_failed("absolute_request_timeout")
            raise RuntimeError("absolute_request_timeout")
        if not ctx.should_stop():
            ctx.request_cancel(cancel_reason or "tool_calling_cancelled")
        raise asyncio.CancelledError()
    except RuntimeError as e:
        if str(e) == "tool_calling_cancelled" and ctx.should_stop():
            raise asyncio.CancelledError()
        logger.error(f"tool_calling_failed: {e}")
        request_manager.capture_error(ctx, e, code="tool_calling_failed")
        ctx.mark_failed(str(e))
        raise
    except asyncio.CancelledError:
        ctx.request_cancel("coroutine_cancelled")
        raise asyncio.CancelledError()
    except Exception as e:
        logger.error(f"tool_calling_failed: {e}")
        request_manager.capture_error(ctx, e, code="tool_calling_failed")
        ctx.mark_failed(str(e))
        raise
    finally:
        if disconnect_task:
            disconnect_task.cancel()
            try:
                await disconnect_task
            except asyncio.CancelledError:
                pass
        worker_thread = worker_state.get("thread")
        if isinstance(worker_thread, threading.Thread) and worker_thread.is_alive():
            await cleanup_worker_thread_after_request(
                worker_thread,
                ctx,
                completed=ctx.status == RequestStatus.COMPLETED,
                cancel_reason="cleanup",
                join_timeout=5.0,
                retire_reason="worker_cleanup_timeout",
            )
        worker_state["thread"] = None
        worker_state["label"] = None
        request_manager.finish_request(ctx, success=(ctx.status == RequestStatus.COMPLETED))


async def _non_stream_tool_calling_with_lifecycle(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
) -> JSONResponse:
    try:
        response = await _complete_tool_calling_with_lifecycle(request, body, ctx)
        return JSONResponse(content=response)
    except Exception as e:
        message, code = _format_tool_calling_error(e)
        ctx.mark_failed(message)
        request_manager.capture_error(ctx, message, code=code)
        request_manager.finish_request(ctx, success=False)
        return JSONResponse(
            content={
                "error": {
                    "message": message,
                    "type": "execution_error",
                    "code": code,
                }
            },
            status_code=500,
        )


async def _stream_tool_calling_with_lifecycle(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext
):
    try:
        response = await _complete_tool_calling_with_lifecycle(request, body, ctx)
        message = response.get("choices", [{}])[0].get("message", {}) or {}
        parsed = {
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls") or [],
        }
        for chunk in _iter_stream_chunks_with_optional_usage(
            body,
            iter_tool_stream_chunks(body.model, parsed),
        ):
            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                break
            yield chunk
            await asyncio.sleep(0)
    except Exception as e:
        message, code = _format_tool_calling_error(e)
        ctx.mark_failed(message)
        request_manager.capture_error(ctx, message, code=code)
        request_manager.finish_request(ctx, success=False)
        yield _pack_error(message, code)
        yield _pack_done()


def _pack_error(message: str, code: str = "error") -> str:
    """打包 SSE 错误"""
    data = {
        "id": f"chatcmpl-error-{int(time.time() * 1000)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "web-browser",
        "choices": [{
            "index": 0,
            "delta": {"content": f"[错误] {message}"},
            "finish_reason": None
        }],
        "error": {
            "message": message,
            "type": "execution_error",
            "code": code
        }
    }
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _pack_done() -> str:
    """打包 SSE 结束标记"""
    return "data: [DONE]\n\n"


def _pack_error_done(message: str, code: str = "error") -> str:
    return f"{_pack_error(message, code)}{_pack_done()}"


def _collect_model_entries() -> List[Dict[str, Any]]:
    entries = [
        {
            "id": "claude-web-browser",
            "object": "model",
            "type": "model",
            "created": MODEL_LIST_CREATED,
            "owned_by": "universal-web-api",
            "display_name": "Claude Code Web Browser Gateway",
        },
        {
            "id": "web-browser",
            "object": "model",
            "type": "model",
            "created": MODEL_LIST_CREATED,
            "owned_by": "universal-web-api",
            "display_name": "web-browser",
        }
    ]

    try:
        browser = get_browser(auto_connect=False)
        tabs = browser.tab_pool.get_tabs_with_index()
        for item in collect_route_domain_models(tabs):
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            entries.append(
                {
                    "id": model_id,
                    "object": "model",
                    "type": "model",
                    "created": MODEL_LIST_CREATED,
                    "owned_by": str(item.get("route_domain") or "universal-web-api"),
                    "display_name": model_id,
                }
            )
    except Exception as e:
        logger.debug(f"构建模型列表失败（已忽略）: {e}")

    return entries


def _build_anthropic_models_response(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    data = []
    for item in entries:
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        data.append(
            {
                "type": "model",
                "id": model_id,
                "display_name": str(item.get("display_name") or model_id),
                "created_at": _format_rfc3339_timestamp(item.get("created")),
            }
        )

    first_id = data[0]["id"] if data else None
    last_id = data[-1]["id"] if data else None
    return {
        "data": data,
        "has_more": False,
        "first_id": first_id,
        "last_id": last_id,
    }


def _verify_models_auth(
    authorization: Optional[str],
    x_api_key: Optional[str],
) -> None:
    verify_service_token(authorization=authorization, x_api_key=x_api_key)


# ================= 模型列表 =================

@router.get("/models")
@router.get("/v1/v1/models")
@router.get("/v1/models")
async def list_models(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    anthropic_version: Optional[str] = Header(None, alias="anthropic-version"),
):
    """列出可用模型"""
    _verify_models_auth(authorization, x_api_key)
    data = _collect_model_entries()

    if anthropic_version:
        return _build_anthropic_models_response(data)

    return {
        "object": "list",
        "data": data,
    }


@router.get("/api/pool/status")
async def get_pool_status(authenticated: bool = Depends(verify_dashboard_auth)):
    """获取标签页池状态"""
    try:
        browser = get_browser(auto_connect=False)
        return browser.get_pool_status()
    except Exception as e:
        return {"error": str(e), "initialized": False}
