"""OpenAI Responses API 的**纯转换层**（无 I/O、无浏览器、无请求上下文）。

从 ``app/api/chat.py`` 抽出：请求字段归一化、message/tool 结构转换、
响应对象拼装等。这些函数只处理 dict / pydantic 对象，因此可以独立测试与复用。

仍留在 ``app/api/chat.py`` 的部分：
- 端点本身（``POST /v1/responses``）与 ``ChatRequest`` / ``ResponsesRequest`` 模型；
- 需要请求上下文/浏览器/流式生命周期的逻辑（``_stream_responses_compat`` 等）；
- Responses 状态缓存（``_responses_state_by_id`` 等），因为测试会直接 patch 它们。

``app/api/chat.py`` 仍然 re-export 这里的所有名字，``chat.<name>`` 的导入与
monkeypatch 语义保持不变。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException


def _responses_state_env_bytes(name: str, default_mib: float) -> int:
    raw = os.getenv(name)
    try:
        value = float(str(raw).strip()) if raw not in (None, "") else float(default_mib)
    except (TypeError, ValueError):
        value = float(default_mib)
    if value != value or value <= 0:  # NaN / 非正数回落默认
        value = float(default_mib)
    return int(value * 1024 * 1024)


def _responses_principal_from_request(request: Any) -> str:
    """B3：续接状态按调用主体隔离——取请求携带的 API 令牌（Bearer / X-API-Key）的 SHA-256 指纹。

    未携带令牌（认证关闭的本机场景）时为空串，所有匿名调用共享一个命名空间，与旧行为一致。
    """
    import hashlib

    headers = getattr(request, "headers", None)
    if headers is None:
        return ""
    raw = str(headers.get("authorization") or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        raw = str(headers.get("x-api-key") or "").strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_genuine_task_cancellation() -> bool:
    """修复(2)：区分「当前协程真的被外部 cancel」与「内部路径把取消转换成的 CancelledError」。

    cancelling() > 0 说明本任务确实收到了外部取消（如服务关闭、连接层取消），
    此时必须继续向上抛；否则视为内部转换信号，可安全地做结构化收尾。
    """
    task = asyncio.current_task()
    if task is None:
        return True  # 保守：无法判断时按真实取消处理
    try:
        return task.cancelling() > 0
    except AttributeError:
        return True


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

        from app.utils.attachments import AttachmentError, normalize_attachment_part
        try:
            attachment = normalize_attachment_part(part)
        except AttachmentError as exc:
            raise HTTPException(status_code=400, detail=exc.detail()) from exc
        if attachment is not None:
            normalized_parts.append(attachment)
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

        if part_type in {"output_audio", "output_video"}:
            if part_type == "output_audio":
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
    return f"{header}\n{output or ''!s}".strip()


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


def _build_responses_usage(chat_payload: Dict[str, Any]) -> Dict[str, int]:
    usage = chat_payload.get("usage") if isinstance(chat_payload.get("usage"), dict) else {}
    return {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


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


def _new_response_id() -> str:
    return f"resp_{int(time.time() * 1000)}_{uuid.uuid4().hex[:10]}"


def _new_response_item_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


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
