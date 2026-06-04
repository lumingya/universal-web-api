"""
app/api/chat.py - 核心聊天 API

职责：
- OpenAI 兼容的 /v1/chat/completions 接口
- 流式/非流式响应处理
- 模型列表
"""

import json
import os
import re
import time
import asyncio
import queue
import threading
import uuid
from typing import Optional, Any, Dict, List, Callable

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from app.core.config import AppConfig, get_logger, SSEFormatter
from app.core import get_browser
from app.services.request_manager import (
    request_manager, 
    RequestContext, 
    RequestStatus, 
    watch_client_disconnect
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
from app.utils.model_routing import collect_route_domain_models, inspect_model_route

logger = get_logger("API.CHAT")

router = APIRouter()
STREAM_QUEUE_POLL_TIMEOUT = 0.5
SSE_HEARTBEAT_INTERVAL = 15.0


class _ToolCallingExecutionCancelled(Exception):
    """Raised when a non-stream tool-calling worker is still running after cancellation."""


def _set_worker_future_result(
    future: "asyncio.Future[Any]",
    *,
    result: Any = None,
    error: Optional[BaseException] = None,
) -> None:
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
        return
    future.set_result(result)


def _get_tool_calling_cancel_reason(ctx: RequestContext) -> str:
    reason = str(ctx.cancel_reason or "").strip()
    return reason or "tool_calling_cancelled"


async def _run_tracked_tool_calling_worker(
    worker_fn: Callable[[], Any],
    *,
    ctx: RequestContext,
    worker_state: Dict[str, Any],
    label: str,
) -> Any:
    loop = asyncio.get_running_loop()
    result_future: "asyncio.Future[Any]" = loop.create_future()

    def worker() -> None:
        try:
            result = worker_fn()
        except Exception as exc:
            loop.call_soon_threadsafe(
                lambda exc=exc: _set_worker_future_result(
                    result_future,
                    error=exc,
                )
            )
        else:
            loop.call_soon_threadsafe(
                lambda result=result: _set_worker_future_result(
                    result_future,
                    result=result,
                )
            )

    worker_thread = threading.Thread(
        target=worker,
        daemon=True,
        name=f"tool-calling-{label}",
    )
    worker_state["thread"] = worker_thread
    worker_state["label"] = label
    worker_thread.start()

    try:
        while True:
            if ctx.should_stop():
                raise _ToolCallingExecutionCancelled(_get_tool_calling_cancel_reason(ctx))
            try:
                return await asyncio.wait_for(
                    asyncio.shield(result_future),
                    timeout=STREAM_QUEUE_POLL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                continue
    finally:
        if result_future.done() and worker_state.get("thread") is worker_thread:
            worker_state["thread"] = None
            worker_state["label"] = None


def _put_worker_queue_item(
    chunk_queue: queue.Queue,
    ctx: RequestContext,
    item: Any,
    *,
    final: bool = False,
) -> bool:
    """Put worker output with bounded backpressure and cancellation awareness."""
    deadline = time.monotonic() + 5.0 if final else None

    while final or not ctx.should_stop():
        try:
            chunk_queue.put(item, timeout=STREAM_QUEUE_POLL_TIMEOUT)
            return True
        except queue.Full:
            if final:
                if ctx.should_stop() or (deadline is not None and time.monotonic() >= deadline):
                    return False
                continue
            if ctx.should_stop():
                return False

    return False


def _extract_stream_error_message(chunk: Any) -> str:
    if not isinstance(chunk, str) or not chunk.startswith("data: "):
        return ""
    try:
        data_str = chunk[6:].strip()
        if not data_str or data_str == "[DONE]":
            return ""
        data = json.loads(data_str)
        error = data.get("error")
        if not isinstance(error, dict):
            return ""
        return str(error.get("message") or "").strip()
    except Exception:
        return ""


def _retire_bound_tab_after_worker_leak(ctx: RequestContext, reason: str) -> None:
    tab_id = str(ctx.tab_id or "").strip()
    if not tab_id:
        return
    try:
        browser = get_browser(auto_connect=False)
        pool = getattr(browser, "_tab_pool", None)
        session = getattr(pool, "_tabs", {}).get(tab_id) if pool is not None else None
        if session is None:
            return

        current_task = str(getattr(session, "current_task_id", "") or "").strip()
        bound_request_id = str(getattr(session, "_bound_request_id", "") or "").strip()
        if current_task and current_task != ctx.request_id:
            logger.warning(
                f"[{tab_id}] 工作线程残留但标签页已被其他任务接管，跳过隔离 "
                f"(request={ctx.request_id}, current_task={current_task})"
            )
            return
        if bound_request_id and bound_request_id != ctx.request_id:
            logger.warning(
                f"[{tab_id}] 工作线程残留但绑定请求已变化，跳过隔离 "
                f"(request={ctx.request_id}, bound={bound_request_id})"
            )
            return

        if hasattr(session, "mark_error"):
            session.mark_error(reason)
        raw_tab_id = ""
        if pool is not None:
            try:
                persistent_index = int(getattr(session, "persistent_index", 0) or 0)
                for candidate_raw_id, candidate_index in getattr(pool, "_raw_id_to_persistent", {}).items():
                    if int(candidate_index or 0) == persistent_index:
                        raw_tab_id = str(candidate_raw_id or "").strip()
                        break
            except Exception:
                raw_tab_id = ""
        if not raw_tab_id:
            try:
                raw_tab_id = str(getattr(getattr(session, "tab", None), "tab_id", "") or "").strip()
            except Exception:
                raw_tab_id = ""

        closed_raw_tab = False
        if pool is not None:
            try:
                monitor = getattr(pool, "_global_network_monitor", None)
                if monitor is not None and hasattr(monitor, "request_stop_for_session"):
                    monitor.request_stop_for_session(tab_id, reason=reason, detach=True)
            except Exception as e:
                logger.debug(f"[{tab_id}] 停止残留标签页全局监听失败（忽略）: {e}")

            if raw_tab_id and hasattr(pool, "_close_raw_tab"):
                try:
                    closed_raw_tab = bool(pool._close_raw_tab(raw_tab_id))
                except Exception as e:
                    logger.debug(f"[{tab_id}] 关闭残留底层标签页失败（忽略）: raw={raw_tab_id}, err={e}")

            context_id = str(getattr(session, "browser_context_id", "") or "").strip()
            if closed_raw_tab and context_id and hasattr(pool, "_dispose_browser_context"):
                try:
                    pool._dispose_browser_context(context_id)
                except Exception as e:
                    logger.debug(f"[{tab_id}] 释放残留浏览器上下文失败（忽略）: {e}")
        if pool is not None and hasattr(pool, "_condition"):
            try:
                with pool._condition:
                    pool._condition.notify_all()
            except Exception:
                pass
        logger.warning(
            f"[{tab_id}] 工作线程未能及时退出，已标记标签页为 ERROR 并尝试断开底层标签页 "
            f"(request={ctx.request_id}, reason={reason}, raw={raw_tab_id or '-'}, "
            f"closed={closed_raw_tab})"
        )
    except Exception as e:
        logger.debug(f"标记残留工作线程标签页失败（忽略）: {e}")


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

    return media_items


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
    response_format: Optional[dict] = Field(default=None)
    tools: Optional[list] = Field(default=None)
    tool_choice: Optional[Any] = Field(default=None)
    parallel_tool_calls: Optional[bool] = Field(default=None)
    functions: Optional[list] = Field(default=None)
    function_call: Optional[Any] = Field(default=None)
    preset_name: Optional[str] = Field(default=None)


class ResponsesRequest(BaseModel):
    """Responses API 请求模型（兼容 Codex / OpenAI Responses wire format）"""
    model: str = Field(default="gpt-3.5-turbo")
    input: Optional[Any] = Field(default="")
    instructions: Optional[str] = Field(default=None)
    stream: Optional[bool] = Field(default=False)
    temperature: Optional[float] = Field(default=0.7, ge=0, le=2)
    max_output_tokens: Optional[int] = Field(default=None, ge=1)
    tools: Optional[list] = Field(default=None)
    tool_choice: Optional[Any] = Field(default=None)
    parallel_tool_calls: Optional[bool] = Field(default=None)
    text: Optional[dict] = Field(default=None)
    metadata: Optional[dict] = Field(default=None)
    prompt: Optional[Any] = Field(default=None)
    previous_response_id: Optional[str] = Field(default=None)

    model_config = {
        "extra": "allow",
    }


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

        if part_type in {"input_image", "image_url"}:
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

        text_fallback = str(part.get("text") or part.get("output") or "").strip()
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


def _normalize_response_tool_output_content(content: Any) -> Any:
    normalized = _normalize_response_message_content(content)
    if normalized in ("", None):
        return ""
    return normalized


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
        normalized_role = role or "user"
        content = item.get("content")
        if content is None and "text" in item:
            content = item.get("text")
        message_payload: Dict[str, Any] = {
            "role": normalized_role if normalized_role in {"system", "user", "assistant", "tool"} else "user",
            "content": _normalize_response_message_content(content),
        }
        if normalized_role == "assistant" and isinstance(item.get("tool_calls"), list):
            message_payload["tool_calls"] = item.get("tool_calls")
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
        output = _normalize_response_tool_output_content(item.get("output"))
        messages.append(
            {
                "role": "tool",
                "tool_call_id": str(item.get("call_id") or item.get("id") or "").strip(),
                "name": str(item.get("name") or "").strip() or None,
                "content": output,
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
    if content not in ("", None):
        output.append(_response_message_item_from_text(str(content)))

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
    if isinstance(format_payload, dict):
        return {"format": format_payload}
    return {"format": {"type": "text"}}


def _build_responses_object(
    body: ResponsesRequest,
    chat_payload: Dict[str, Any],
    *,
    response_id: Optional[str] = None,
    status: str = "completed",
    error: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    response_obj: Dict[str, Any] = {
        "id": response_id or _new_response_id(),
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "error": error,
        "incomplete_details": None,
        "model": body.model,
        "output": _chat_payload_to_responses_output(chat_payload),
        "parallel_tool_calls": bool(body.parallel_tool_calls) if body.parallel_tool_calls is not None else bool(body.tools),
        "tool_choice": body.tool_choice if body.tool_choice is not None else ("auto" if body.tools else "none"),
        "tools": body.tools or [],
        "text": _build_responses_text_payload(body),
        "usage": _build_responses_usage(chat_payload),
    }

    if body.instructions is not None:
        response_obj["instructions"] = body.instructions
    if body.max_output_tokens is not None:
        response_obj["max_output_tokens"] = body.max_output_tokens
    if body.temperature is not None:
        response_obj["temperature"] = body.temperature
    if isinstance(body.metadata, dict):
        response_obj["metadata"] = body.metadata
    if body.previous_response_id:
        response_obj["previous_response_id"] = body.previous_response_id

    return response_obj


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

    yield _pack_responses_sse(
        "response.created",
        {
            "id": response_id,
            "object": "response",
            "created_at": created_at,
            "status": "in_progress",
            "error": None,
            "incomplete_details": None,
            "model": body.model,
            "output": [],
            "tools": body.tools or [],
            "tool_choice": body.tool_choice if body.tool_choice is not None else ("auto" if body.tools else "none"),
            "parallel_tool_calls": bool(body.parallel_tool_calls) if body.parallel_tool_calls is not None else bool(body.tools),
            "text": _build_responses_text_payload(body),
        },
    )

    chat_body = _responses_request_to_chat_request(body, stream=False)

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
            response_id=response_id,
            status="failed",
            error={
                "message": str(e),
                "type": "execution_error",
                "code": "responses_backing_request_failed",
            },
        )
        yield _pack_responses_sse("response.failed", failed)
        return

    if status_code >= 400 or "error" in payload:
        error_payload = payload.get("error") if isinstance(payload.get("error"), dict) else {
            "message": "responses_backing_request_failed",
            "type": "execution_error",
            "code": "responses_backing_request_failed",
        }
        failed = _build_responses_object(
            body,
            payload,
            response_id=response_id,
            status="failed",
            error=error_payload,
        )
        yield _pack_responses_sse("response.failed", failed)
        return

    completed = _build_responses_object(
        body,
        payload,
        response_id=response_id,
        status="completed",
        error=None,
    )

    for output_index, item in enumerate(completed.get("output") or []):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        yield _pack_responses_sse(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "response_id": response_id,
                "output_index": output_index,
                "item": _build_in_progress_response_item(item),
            },
        )

        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "message":
            content_items = item.get("content") if isinstance(item.get("content"), list) else []
            for content_index, part in enumerate(content_items):
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "").strip().lower()
                if part_type != "output_text":
                    continue
                yield _pack_responses_sse(
                    "response.content_part.added",
                    {
                        "type": "response.content_part.added",
                        "response_id": response_id,
                        "item_id": item_id,
                        "output_index": output_index,
                        "content_index": content_index,
                        "part": {
                            "type": "output_text",
                            "text": "",
                            "annotations": [],
                        },
                    },
                )
                text = str(part.get("text") or "")
                if text:
                    yield _pack_responses_sse(
                        "response.output_text.delta",
                        {
                            "type": "response.output_text.delta",
                            "response_id": response_id,
                            "item_id": item_id,
                            "output_index": output_index,
                            "content_index": content_index,
                            "delta": text,
                        },
                    )
                yield _pack_responses_sse(
                    "response.output_text.done",
                    {
                        "type": "response.output_text.done",
                        "response_id": response_id,
                        "item_id": item_id,
                        "output_index": output_index,
                        "content_index": content_index,
                        "text": text,
                    },
                )
                yield _pack_responses_sse(
                    "response.content_part.done",
                    {
                        "type": "response.content_part.done",
                        "response_id": response_id,
                        "item_id": item_id,
                        "output_index": output_index,
                        "content_index": content_index,
                        "part": part,
                    },
                )
        elif item_type == "function_call":
            arguments = str(item.get("arguments") or "")
            if arguments:
                yield _pack_responses_sse(
                    "response.function_call_arguments.delta",
                    {
                        "type": "response.function_call_arguments.delta",
                        "response_id": response_id,
                        "item_id": item_id,
                        "output_index": output_index,
                        "delta": arguments,
                    },
                )
            yield _pack_responses_sse(
                "response.function_call_arguments.done",
                {
                    "type": "response.function_call_arguments.done",
                    "response_id": response_id,
                    "item_id": item_id,
                    "output_index": output_index,
                    "arguments": arguments,
                    "name": str(item.get("name") or ""),
                    "call_id": str(item.get("call_id") or ""),
                },
            )

        yield _pack_responses_sse(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "response_id": response_id,
                "output_index": output_index,
                "item": item,
            },
        )

    yield _pack_responses_sse("response.completed", completed)


# ================= response_format 转化 =================

DEFAULT_RESPONSE_FORMAT_HINTS = {
    "json_object": "\n\n[系统指令：请以 JSON 格式输出你的回复。确保输出是有效的 JSON 对象，不要包含 ```json 代码块标记或任何其他非 JSON 文字。]",
    "json_schema": "\n\n[系统指令：请严格按照以下 JSON Schema 格式输出你的回复，确保输出是有效的 JSON，不要包含代码块标记：\n{schema}]",
    "text": ""
}


def _get_response_format_hint(format_type: str) -> str:
    """获取指定格式类型的提示词模板"""
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
    if not response_format:
        return messages
    
    format_type = response_format.get("type", "text")
    hint_template = _get_response_format_hint(format_type)
    
    if not hint_template:
        return messages
    
    hint = hint_template
    
    if format_type == "json_schema":
        json_schema = response_format.get("json_schema", {})
        schema_content = json_schema.get("schema", json_schema)
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


# ================= 认证依赖 =================


async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    """验证 Bearer Token"""
    if not AppConfig.is_auth_enabled():
        return True

    if not AppConfig.AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="服务配置错误")

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = authorization.replace("Bearer ", "").strip()

    if token != AppConfig.get_auth_token():
        raise HTTPException(
            status_code=401,
            detail="认证令牌无效",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return True


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

    if body.response_format:
        format_type = body.response_format.get("type", "text")
        if format_type != "text":
            logger.debug(f"检测到 response_format.type={format_type}，转化为提示词")
            body.messages = _apply_response_format(body.messages, body.response_format)

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

    status_code, payload = await _run_chat_completion_final(
        request=request,
        body=chat_body,
        authenticated=authenticated,
    )

    if status_code >= 400 or "error" in payload:
        return JSONResponse(content=payload, status_code=status_code)

    response_obj = _build_responses_object(body, payload, status="completed", error=None)
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
        done_emitted = False
        client_disconnected = False

        while True:
            if await request.is_disconnected():
                logger.debug("客户端断开")
                ctx.request_cancel("client_disconnected")
                client_disconnected = True
                break

            try:
                chunk = await asyncio.to_thread(
                    chunk_queue.get,
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
                yield _pack_error(f"执行错误: {chunk[1]}", "internal_error")
                break

            # 🔍 探针：记录发送给客户端的 chunk
            has_images = '"images"' in chunk if isinstance(chunk, str) else False
            if has_images:
                logger.info(f"[SEND] 发送包含图片的 chunk 给客户端")
            
            request_manager.capture_response_chunk(ctx, chunk)
            yield chunk
            last_sse_emit_at = time.monotonic()
            if isinstance(chunk, str) and chunk.startswith("data: [DONE]"):
                done_emitted = True
            error_message = _extract_stream_error_message(chunk)
            if error_message:
                logger.warning(f"流式响应返回错误事件: {error_message}")
                request_manager.capture_error(ctx, error_message, code="stream_error")
                ctx.mark_failed(error_message)
                break
            await asyncio.sleep(0)

        if not client_disconnected and not done_emitted:
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

    finally:
        if worker_thread and worker_thread.is_alive():
            ctx.request_cancel("cleanup")
            worker_thread.join(timeout=5.0)
            if worker_thread.is_alive():
                _retire_bound_tab_after_worker_leak(ctx, "worker_cleanup_timeout")

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

    async for chunk in _stream_with_lifecycle(request, body, ctx):
        if isinstance(chunk, str):
            if chunk.startswith("data: [DONE]"):
                continue

            if chunk.startswith("data: "):
                try:
                    data_str = chunk[6:].strip()
                    if not data_str:
                        continue
                    data = json.loads(data_str)

                    if "error" in data:
                        error_data = data
                        break

                    collected_media.extend(_extract_chunk_media_items(data))

                    if "choices" in data and data["choices"]:
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            collected_content.append(content)
                except json.JSONDecodeError:
                    continue

    if error_data:
        return JSONResponse(content=error_data, status_code=500)

    full_content = "".join(collected_content)
    placeholder_pattern = re.compile(
        r"^\s*https?://(?:[\w.-]+\.)?googleusercontent\.com/(?:image_generation_content|generated_music_content)/\d+\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    full_content = placeholder_pattern.sub("", full_content)
    full_content = re.sub(r"\n{3,}", "\n\n", full_content).strip()

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
        if not ctx.should_stop():
            ctx.request_cancel("tool_calling_cancelled")
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
            ctx.request_cancel("cleanup")
            worker_thread.join(timeout=5.0)
            if worker_thread.is_alive():
                _retire_bound_tab_after_worker_leak(ctx, "worker_cleanup_timeout")
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
        request_manager.capture_error(ctx, e, code="tool_calling_failed")
        return JSONResponse(
            content={
                "error": {
                    "message": f"执行错误: {e}",
                    "type": "execution_error",
                    "code": "tool_calling_failed",
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
        for chunk in iter_tool_stream_chunks(body.model, parsed):
            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                break
            yield chunk
            await asyncio.sleep(0)
    except Exception as e:
        yield _pack_error(f"执行错误: {str(e)}", "tool_calling_failed")
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


def _collect_model_entries() -> List[Dict[str, Any]]:
    entries = [
        {
            "id": "web-browser",
            "object": "model",
            "created": int(time.time()),
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
                    "created": int(time.time()),
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
    if not AppConfig.is_auth_enabled():
        return

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
    if isinstance(x_api_key, str):
        raw = x_api_key.strip()
        if raw:
            candidates.append(raw)

    if token_value not in candidates:
        raise HTTPException(
            status_code=401,
            detail="认证令牌无效",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ================= 模型列表 =================

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
async def get_pool_status(authenticated: bool = Depends(verify_auth)):
    """获取标签页池状态"""
    try:
        browser = get_browser(auto_connect=False)
        return browser.get_pool_status()
    except Exception as e:
        return {"error": str(e), "initialized": False}
