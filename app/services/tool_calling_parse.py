"""
Parsing and response helpers for tool-calling.
"""

from __future__ import annotations

import html
import json
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import json_repair
from bs4 import BeautifulSoup, NavigableString, Tag

from app.services.sse_utils import sse_frame_data_text

from app.services.tool_calling_common import (
    _LEGACY_XML_ARG_TAG,
    _LEGACY_XML_CALL_TAG,
    _LEGACY_XML_WRAPPER_TAG,
    _PREFERRED_XML_ARG_TAG,
    _PREFERRED_XML_CALL_TAG,
    _PREFERRED_XML_WRAPPER_TAG,
    _debug_preview,
    _new_completion_id,
    _new_tool_call_id,
    _pack_sse_chunk,
    _resolve_tool_name,
    _serialize_content,
    get_tool_calling_sanitize_assistant_content_enabled,
    logger,
)

def parse_tool_response(
    text: str,
    tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    raw = str(text or "")
    logger.debug(
        f"[tool_calling] raw assistant text len={len(raw)} "
        f"preview={_debug_preview(raw)}"
    )
    allowed = {
        str(item.get("function", {}).get("name", "") or "").strip(): item
        for item in tools or []
        if isinstance(item, dict)
    }

    json_payload = _try_parse_json_payload(raw, allowed)
    if json_payload is not None:
        tool_names = [
            str(item.get("function", {}).get("name", "") or "").strip()
            for item in json_payload.get("tool_calls") or []
            if isinstance(item, dict)
        ]
        content_text = str(json_payload.get("content") or "")
        logger.debug(
            "[tool_calling] parsed JSON payload "
            f"mode={json_payload.get('mode')} "
            f"tool_calls={len(json_payload.get('tool_calls') or [])} "
            f"tool_names={tool_names or ['none']} "
            f"content_len={len(content_text)} "
            f"content={_debug_preview(content_text)}"
        )
        return json_payload

    xml_payload = _try_parse_xml_tool_calls(raw, allowed)
    if xml_payload is not None:
        logger.debug(
            "[tool_calling] parsed XML payload "
            f"tool_calls={len(xml_payload.get('tool_calls') or [])}"
        )
        return xml_payload

    logger.debug(f"[tool_calling] falling back to final-text mode (len={len(raw)})")
    return {
        "mode": "final",
        "content": raw.strip(),
        "tool_calls": [],
    }


def build_tool_completion_response(
    model: str,
    parsed: Dict[str, Any],
    *,
    legacy_function_call: bool = False,
) -> Dict[str, Any]:
    completion_id = _new_completion_id()
    tool_calls = parsed.get("tool_calls") or []
    content = parsed.get("content")
    if tool_calls:
        message: Dict[str, Any] = {
            "role": "assistant",
            "content": content if content not in ("", None) else None,
        }
        if legacy_function_call:
            message["function_call"] = _legacy_function_call_from_tool_call(tool_calls[0])
            finish_reason = "function_call"
        else:
            message["tool_calls"] = tool_calls
            finish_reason = "tool_calls"
    else:
        message = {
            "role": "assistant",
            "content": str(content or ""),
        }
        finish_reason = "stop"

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def iter_tool_stream_chunks(
    model: str,
    parsed: Dict[str, Any],
    *,
    legacy_function_call: bool = False,
) -> Iterable[str]:
    completion_id = _new_completion_id()
    created = int(time.time())

    first_delta: Dict[str, Any] = {"role": "assistant"}
    if parsed.get("content") not in ("", None):
        first_delta["content"] = str(parsed.get("content") or "")
    if parsed.get("tool_calls"):
        if legacy_function_call:
            first_delta["function_call"] = _legacy_function_call_from_tool_call(
                parsed["tool_calls"][0]
            )
        else:
            first_delta["tool_calls"] = _tool_calls_for_stream_delta(parsed["tool_calls"])

    yield _pack_sse_chunk(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": first_delta, "finish_reason": None}],
        }
    )

    finish_reason = (
        "function_call"
        if parsed.get("tool_calls") and legacy_function_call
        else "tool_calls"
        if parsed.get("tool_calls")
        else "stop"
    )
    yield _pack_sse_chunk(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }
    )
    yield "data: [DONE]\n\n"


def _tool_calls_for_stream_delta(tool_calls: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for fallback_index, item in enumerate(tool_calls or []):
        if not isinstance(item, dict):
            continue
        delta_item = dict(item)
        try:
            delta_item["index"] = int(delta_item.get("index"))
        except Exception:
            delta_item["index"] = fallback_index
        normalized.append(delta_item)
    return normalized


def _legacy_function_call_from_tool_call(tool_call: Any) -> Dict[str, str]:
    item = tool_call if isinstance(tool_call, dict) else {}
    function_data = item.get("function") if isinstance(item.get("function"), dict) else {}
    arguments = function_data.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
    return {
        "name": str(function_data.get("name") or "").strip(),
        "arguments": arguments,
    }

_TOOL_CALLING_PLACEHOLDER_URL_RE = re.compile(
    r"^\s*https?://(?:[\w.-]+\.)?googleusercontent\.com/"
    r"(?:image_generation_content|generated_music_content)/\d+\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _cleanup_tool_calling_content_text(content: Any) -> str:
    text = _serialize_content(content)
    cleaned = _TOOL_CALLING_PLACEHOLDER_URL_RE.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _resolve_response_media_ref(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    ref = str(item.get("url") or item.get("data_uri") or "").strip()
    return ref


def _build_response_media_markdown_block(media_items: Any) -> str:
    if not isinstance(media_items, list):
        return ""

    image_blocks: List[str] = []
    audio_lines: List[str] = []
    video_lines: List[str] = []

    for item in media_items:
        ref = _resolve_response_media_ref(item)
        if not ref:
            continue

        media_type = str((item or {}).get("media_type") or "image").strip().lower()
        if media_type == "image":
            image_blocks.append(f"\n\n![image_{len(image_blocks)}]({ref})")
            continue

        label = str((item or {}).get("label") or (item or {}).get("mime") or "").strip()
        label_suffix = f" - {label}" if label else ""
        if media_type == "audio":
            audio_lines.append(f"[audio_{len(audio_lines)}]({ref}){label_suffix}")
        elif media_type == "video":
            video_lines.append(f"[video_{len(video_lines)}]({ref}){label_suffix}")

    blocks: List[str] = []
    if image_blocks:
        blocks.append("".join(image_blocks))
    if audio_lines:
        blocks.append("\n\n" + "\n".join(audio_lines))
    if video_lines:
        blocks.append("\n\n" + "\n".join(video_lines))

    if not blocks:
        return ""

    return "".join(blocks) + "\n\n"


def _strip_trailing_response_media_markdown(content: str, media_items: Any) -> str:
    text = str(content or "")
    media_markdown = _build_response_media_markdown_block(media_items)
    if media_markdown:
        candidates = []
        for variant in (media_markdown, media_markdown.rstrip(), media_markdown.strip()):
            if variant and variant not in candidates:
                candidates.append(variant)
        for candidate in candidates:
            if text.endswith(candidate):
                return text[: -len(candidate)].rstrip()
    return text


def extract_tool_calling_assistant_content(response: Dict[str, Any]) -> str:
    try:
        message = (
            response.get("choices", [])[0]
            .get("message", {})
        )
    except Exception:
        message = {}

    if not isinstance(message, dict):
        return ""

    sanitize = get_tool_calling_sanitize_assistant_content_enabled()
    content = _serialize_content(message.get("content"))
    if not sanitize:
        return content.strip()

    content = _cleanup_tool_calling_content_text(content)
    media_items = message.get("media")
    if not isinstance(media_items, list):
        media_items = response.get("media")

    return _strip_trailing_response_media_markdown(content, media_items)


def decode_browser_non_stream_payload(payload: Any) -> Dict[str, Any]:
    text = str(payload or "").strip()
    if not text:
        raise RuntimeError("empty_browser_response")

    normalized = text.lstrip()
    looks_like_sse = (
        normalized.startswith(("data:", "event:", ":"))
        or "\ndata:" in normalized
        or "\r\ndata:" in normalized
    )
    candidates = _extract_sse_json_payloads(text) if looks_like_sse else [text]
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception as exc:
            last_error = exc
            continue
        if isinstance(data, dict):
            return data
        raise RuntimeError(f"browser_response_not_object: {_debug_preview(data)}")

    preview = _debug_preview(text, 500)
    if last_error:
        raise RuntimeError(
            f"invalid_browser_json_response: {last_error}; payload_preview={preview}"
        )
    raise RuntimeError(f"invalid_browser_json_response: payload_preview={preview}")


def _extract_sse_json_payloads(text: str) -> List[str]:
    payloads: List[str] = []
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    for block in blocks:
        payload_text = sse_frame_data_text(block)
        if payload_text and payload_text.strip() != "[DONE]":
            payloads.append(payload_text)
    return payloads


def _try_parse_json_payload(text: str, allowed_tools: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = _extract_json_candidates(text)
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            repaired = _repair_json_like_argument_string(candidate)
            if repaired == candidate:
                continue
            try:
                payload = json.loads(repaired)
            except Exception:
                continue

        normalized = _normalize_parsed_payload(payload, allowed_tools)
        if normalized is not None:
            return normalized

    return None


def _extract_json_candidates(text: str) -> List[str]:
    stripped = str(text or "").strip()
    candidates: List[str] = []
    if not stripped:
        return candidates

    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)

    candidates.append(stripped)
    candidates.extend(_extract_balanced_json_object_candidates(stripped))

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])

    seen = set()
    result = []
    for item in candidates:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _extract_balanced_json_object_candidates(text: str) -> List[str]:
    value = str(text or "")
    spans: List[List[int]] = []
    stack: List[int] = []
    in_string = False
    escape = False

    for index, current in enumerate(value):
        if in_string:
            if escape:
                escape = False
            elif current == "\\":
                escape = True
            elif current == '"':
                in_string = False
            continue

        if current == '"':
            in_string = True
            continue
        if current == "{":
            stack.append(len(spans))
            spans.append([index, -1])
            continue
        if current == "}":
            if not stack:
                continue
            span_index = stack.pop()
            spans[span_index][1] = index + 1

    return [value[start:end] for start, end in spans if end >= start]


def _normalize_parsed_payload(
    payload: Any,
    allowed_tools: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    openai_like = _normalize_openai_like_payload(payload, allowed_tools)
    if openai_like is not None:
        return openai_like

    mode = str(payload.get("mode", "") or "").strip().lower()
    if "tool_calls" in payload or mode == "tool_calls":
        raw_calls = payload.get("tool_calls")
        if not isinstance(raw_calls, list):
            return None
        tool_calls = _normalize_tool_calls(raw_calls, allowed_tools)
        if tool_calls:
            return {
                "mode": "tool_calls",
                "content": payload.get("content"),
                "tool_calls": tool_calls,
            }
        if raw_calls:
            return None

    if mode == "final":
        return {
            "mode": "final",
            "content": str(payload.get("content", "") or ""),
            "tool_calls": [],
        }

    return None


def _normalize_openai_like_payload(
    payload: Dict[str, Any],
    allowed_tools: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    message: Optional[Dict[str, Any]] = None

    if isinstance(payload.get("message"), dict):
        message = payload.get("message")
    elif isinstance(payload.get("choices"), list) and payload["choices"]:
        first_choice = payload["choices"][0]
        if isinstance(first_choice, dict) and isinstance(first_choice.get("message"), dict):
            message = first_choice.get("message")
    elif str(payload.get("role", "") or "").strip().lower() == "assistant":
        message = payload

    if not isinstance(message, dict):
        return None

    content = message.get("content")
    raw_tool_calls = message.get("tool_calls")
    if isinstance(raw_tool_calls, list):
        tool_calls = _normalize_tool_calls(raw_tool_calls, allowed_tools)
        if tool_calls:
            return {
                "mode": "tool_calls",
                "content": content,
                "tool_calls": tool_calls,
            }
        if raw_tool_calls:
            return None

    if "content" in message:
        return {
            "mode": "final",
            "content": "" if content is None else str(content),
            "tool_calls": [],
        }

    return None


def _normalize_tool_calls(
    raw_calls: List[Any],
    allowed_tools: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in raw_calls:
        if not isinstance(item, dict):
            continue

        function_data = item.get("function") if isinstance(item.get("function"), dict) else {}
        raw_name = (
            item.get("name")
            or item.get("tool_name")
            or function_data.get("name")
            or ""
        )
        raw_name_text = str(raw_name or "").strip()
        name = _resolve_tool_name(raw_name_text, allowed_tools) or raw_name_text

        args = item.get("arguments", function_data.get("arguments"))
        args_obj = _coerce_arguments_object(args)
        if args_obj is None:
            arguments_payload = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        else:
            arguments_payload = json.dumps(args_obj, ensure_ascii=False)

        result.append(
            {
                "id": str(item.get("id") or _new_tool_call_id()),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments_payload,
                },
            }
        )

    return result


def _coerce_arguments_object(args: Any) -> Optional[Dict[str, Any]]:
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        stripped = args.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            repaired = _repair_json_like_argument_string(stripped)
            if repaired != stripped:
                try:
                    parsed = json.loads(repaired)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    pass
            return None
    return None


def _decode_tool_arguments(tool_call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    function_data = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    raw_arguments = function_data.get("arguments")
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        stripped = raw_arguments.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except Exception:
            repaired = _repair_json_like_argument_string(stripped)
            if repaired == stripped:
                return None
            try:
                parsed = json.loads(repaired)
            except Exception:
                return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _repair_json_like_argument_string(raw: str) -> str:
    """Return canonical JSON repaired by json-repair, or the original text."""
    text = str(raw or "")
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[":
        return text
    try:
        repaired = json_repair.loads(text)
    except Exception:
        return text
    if not isinstance(repaired, (dict, list)):
        return text
    return json.dumps(repaired, ensure_ascii=False)


_TOOL_XML_WRAPPER_OPEN_RE = re.compile(
    r"<\s*(?:adapter_calls|tool_calls)\b[^>]*>",
    flags=re.IGNORECASE,
)
_TOOL_XML_WRAPPER_CLOSE_RE = re.compile(
    r"<\s*/\s*(?:adapter_calls|tool_calls)\s*>",
    flags=re.IGNORECASE,
)
_TOOL_XML_INVOKE_OPEN_RE = re.compile(
    r"<\s*(?:call|invoke|tool_call)\b[^>]*>",
    flags=re.IGNORECASE,
)
_TOOL_XML_INVOKE_CLOSE_RE = re.compile(
    r"<\s*/\s*(?:call|invoke|tool_call)\s*>",
    flags=re.IGNORECASE,
)
_TOOL_XML_STRING_PARAM_NAMES = {
    "code",
    "command",
    "content",
    "description",
    "new_string",
    "old_string",
    "path",
    "prompt",
    "query",
    "question",
}

_TOOL_XML_MAX_CHARS = 200_000
_TOOL_XML_FORBIDDEN_DECL_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _mask_ignored_tool_markup_regions(text: str) -> str:
    if not text:
        return ""

    chars = list(text)

    def _blank(start: int, end: int) -> None:
        for index in range(max(0, start), min(len(chars), end)):
            if chars[index] not in "\r\n":
                chars[index] = " "

    fence_pattern = re.compile(
        r"(?ms)^[ \t]*(```+|~~~+)[^\r\n]*\r?\n.*?^[ \t]*\1[ \t]*(?=\r?\n|$)"
    )
    for match in fence_pattern.finditer(text):
        _blank(match.start(), match.end())

    scan_text = "".join(chars)
    index = 0
    while index < len(scan_text):
        if scan_text[index] != "`":
            index += 1
            continue
        tick_count = 1
        while index + tick_count < len(scan_text) and scan_text[index + tick_count] == "`":
            tick_count += 1
        closing = scan_text.find("`" * tick_count, index + tick_count)
        if closing == -1:
            index += tick_count
            continue
        _blank(index, closing + tick_count)
        scan_text = "".join(chars)
        index = closing + tick_count

    return "".join(chars)


def _find_tool_xml_wrapper_blocks(text: str) -> List[str]:
    masked = _mask_ignored_tool_markup_regions(text)
    blocks: List[str] = []
    search_from = 0
    while True:
        match = _TOOL_XML_WRAPPER_OPEN_RE.search(masked, search_from)
        if not match:
            break
        block_end = _find_tool_xml_wrapper_end(masked, match.end())
        if block_end == -1:
            blocks.append(text[match.start() :])
            break
        blocks.append(text[match.start() : block_end])
        search_from = block_end
    return blocks


def _find_tool_xml_wrapper_end(masked: str, start: int) -> int:
    depth = 1
    index = max(0, start)
    while index < len(masked):
        if masked.startswith("<![CDATA[", index):
            cdata_end = masked.find("]]>", index + 9)
            if cdata_end == -1:
                return -1
            index = cdata_end + 3
            continue

        open_match = _TOOL_XML_WRAPPER_OPEN_RE.match(masked, index)
        if open_match:
            depth += 1
            index = open_match.end()
            continue

        close_match = _TOOL_XML_WRAPPER_CLOSE_RE.match(masked, index)
        if close_match:
            depth -= 1
            index = close_match.end()
            if depth == 0:
                return index
            continue

        index += 1
    return -1


def _find_tool_xml_invoke_end(masked: str, start: int) -> int:
    depth = 1
    index = max(0, start)
    while index < len(masked):
        if masked.startswith("<![CDATA[", index):
            cdata_end = masked.find("]]>", index + 9)
            if cdata_end == -1:
                return -1
            index = cdata_end + 3
            continue

        open_match = _TOOL_XML_INVOKE_OPEN_RE.match(masked, index)
        if open_match:
            depth += 1
            index = open_match.end()
            continue

        close_match = _TOOL_XML_INVOKE_CLOSE_RE.match(masked, index)
        if close_match:
            depth -= 1
            index = close_match.end()
            if depth == 0:
                return index
            continue

        index += 1
    return -1


def _repair_missing_tool_xml_wrapper(text: str) -> str:
    masked = _mask_ignored_tool_markup_regions(text)
    wrapper_open = _TOOL_XML_WRAPPER_OPEN_RE.search(masked)
    invoke_ranges: List[Tuple[int, int]] = []
    search_from = 0
    while True:
        invoke_match = _TOOL_XML_INVOKE_OPEN_RE.search(masked, search_from)
        if not invoke_match:
            break
        if wrapper_open and wrapper_open.start() <= invoke_match.start():
            search_from = invoke_match.end()
            continue
        invoke_end = _find_tool_xml_invoke_end(masked, invoke_match.end())
        if invoke_end == -1:
            search_from = invoke_match.end()
            continue
        invoke_ranges.append((invoke_match.start(), invoke_end))
        search_from = invoke_end

    if invoke_ranges:
        start = invoke_ranges[0][0]
        end = invoke_ranges[-1][1]
        return (
            text[:start]
            + f"<{_PREFERRED_XML_WRAPPER_TAG}>"
            + text[start:end]
            + f"</{_PREFERRED_XML_WRAPPER_TAG}>"
            + text[end:]
        )

    invoke_match = _TOOL_XML_INVOKE_OPEN_RE.search(masked)
    close_match = _TOOL_XML_WRAPPER_CLOSE_RE.search(masked)
    if not invoke_match or not close_match:
        return text
    if invoke_match.start() >= close_match.start():
        return text
    return (
        text[: invoke_match.start()]
        + f"<{_PREFERRED_XML_WRAPPER_TAG}>"
        + text[invoke_match.start() : close_match.start()]
        + f"</{_PREFERRED_XML_WRAPPER_TAG}>"
        + text[close_match.end() :]
    )


def _normalize_tool_xml_markup(text: str) -> str:
    return str(text or "")


def _safe_xml_fromstring(text: str) -> Tag:
    """Parse an LLM tool block with HTML recovery semantics."""
    value = str(text or "")
    if len(value) > _TOOL_XML_MAX_CHARS:
        raise ValueError("tool XML block exceeds maximum length")
    if _TOOL_XML_FORBIDDEN_DECL_RE.search(value):
        raise ValueError("DTD and entity declarations are not allowed in tool XML")

    soup = BeautifulSoup(value, "html.parser")
    root = soup.find(
        lambda tag: isinstance(tag, Tag)
        and _xml_local_name(tag.name).lower()
        in {_PREFERRED_XML_WRAPPER_TAG, _LEGACY_XML_WRAPPER_TAG}
    )
    if not isinstance(root, Tag):
        raise ValueError("tool XML wrapper not found")
    return root


def _xml_local_name(tag: Any) -> str:
    value = str(tag or "")
    if "}" in value:
        value = value.rsplit("}", 1)[-1]
    return value.strip()


def _append_xml_value(target: Dict[str, Any], key: str, value: Any) -> None:
    if key in target:
        existing = target[key]
        if isinstance(existing, list):
            existing.append(value)
        else:
            target[key] = [existing, value]
        return
    target[key] = value


def _schema_prefers_string(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return schema_type.strip().lower() == "string"
    if isinstance(schema_type, list):
        return any(str(item).strip().lower() == "string" for item in schema_type)
    return False


def _schema_property_name(schema: Any, field_name: str) -> str:
    raw_name = str(field_name or "").strip()
    if not isinstance(schema, dict):
        return raw_name
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if raw_name in properties:
        return raw_name
    folded_name = raw_name.casefold()
    return next(
        (str(name) for name in properties if str(name).casefold() == folded_name),
        raw_name,
    )


def _schema_property_schema(schema: Any, field_name: str) -> Optional[Dict[str, Any]]:
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    prop_schema = properties.get(_schema_property_name(schema, field_name))
    if isinstance(prop_schema, dict):
        return prop_schema
    return None


def _tool_parameters_schema(tool_def: Any) -> Dict[str, Any]:
    if not isinstance(tool_def, dict):
        return {}
    function_data = tool_def.get("function") if isinstance(tool_def.get("function"), dict) else {}
    parameters = function_data.get("parameters")
    if isinstance(parameters, dict):
        return parameters
    parameters = tool_def.get("parameters")
    return parameters if isinstance(parameters, dict) else {}


def _parse_xml_scalar_value(
    raw_text: str,
    param_name: str = "",
    param_schema: Optional[Dict[str, Any]] = None,
) -> Any:
    text = html.unescape(str(raw_text or ""))
    stripped = text.strip()
    if not stripped:
        return ""

    normalized_name = str(param_name or "").strip().lower()
    if not _schema_prefers_string(param_schema) and normalized_name not in _TOOL_XML_STRING_PARAM_NAMES:
        try:
            parsed = json.loads(stripped)
        except Exception:
            repaired = _repair_json_like_argument_string(stripped)
            if repaired != stripped:
                try:
                    parsed = json.loads(repaired)
                except Exception:
                    parsed = None
                else:
                    if not isinstance(parsed, str):
                        return parsed
            parsed = None
        else:
            if not isinstance(parsed, str):
                return parsed

    return stripped


def _parse_xml_element_value(
    element: Tag,
    field_name: str = "",
    param_schema: Optional[Dict[str, Any]] = None,
) -> Any:
    children = element.find_all(recursive=False)
    normalized_name = str(field_name or "").strip().lower()
    if _schema_prefers_string(param_schema) or normalized_name in _TOOL_XML_STRING_PARAM_NAMES:
        inner_markup = "".join(str(item) for item in element.contents)
        return _parse_xml_scalar_value(inner_markup, field_name, param_schema)
    if not children:
        return _parse_xml_scalar_value(element.get_text(), field_name, param_schema)

    result: Dict[str, Any] = {}
    for child in children:
        child_name = _schema_property_name(param_schema, _xml_local_name(child.name))
        if not child_name:
            continue
        child_schema = _schema_property_schema(param_schema, child_name)
        _append_xml_value(
            result,
            child_name,
            _parse_xml_element_value(child, child_name, child_schema),
        )

    item_key = next((key for key in result if key.lower() == "item"), "")
    if len(result) == 1 and item_key:
        items = result[item_key]
        return items if isinstance(items, list) else [items]

    text_parts = [
        str(item)
        for item in element.contents
        if isinstance(item, NavigableString) and str(item).strip()
    ]
    if text_parts:
        result["_text"] = _parse_xml_scalar_value("".join(text_parts), field_name, param_schema)
    return result


def _parse_xml_invoke_arguments(
    invoke: Tag,
    tool_def: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    children = invoke.find_all(recursive=False)
    if not children:
        inner_text = invoke.get_text().strip()
        if not inner_text:
            return {}
        try:
            payload = json_repair.loads(inner_text)
        except Exception:
            return None
        if isinstance(payload, dict):
            if isinstance(payload.get("input"), dict):
                return payload.get("input")
            if isinstance(payload.get("parameters"), dict):
                return payload.get("parameters")
            return payload
        return None

    arguments: Dict[str, Any] = {}
    parameters_schema = _tool_parameters_schema(tool_def)
    for child in children:
        child_tag = _xml_local_name(child.name)
        is_named_arg = child_tag.lower() in {
            _PREFERRED_XML_ARG_TAG,
            _LEGACY_XML_ARG_TAG,
            "argument",
        }
        if is_named_arg:
            param_name = str(child.attrs.get("name", "") or "").strip()
        else:
            param_name = _schema_property_name(parameters_schema, child_tag)
        if not param_name:
            continue
        schema_properties = (
            parameters_schema.get("properties")
            if isinstance(parameters_schema.get("properties"), dict)
            else {}
        )
        if not is_named_arg and param_name not in schema_properties:
            continue
        param_name = _schema_property_name(parameters_schema, param_name)
        param_schema = _schema_property_schema(parameters_schema, param_name)
        _append_xml_value(
            arguments,
            param_name,
            _parse_xml_element_value(child, param_name, param_schema),
        )
    return arguments


def _parse_wrapped_xml_tool_calls(
    text: str,
    allowed_tools: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    normalized = _normalize_tool_xml_markup(text)
    try:
        root = _safe_xml_fromstring(normalized)
    except ValueError:
        return []

    if _xml_local_name(root.name).lower() not in {
        _PREFERRED_XML_WRAPPER_TAG,
        _LEGACY_XML_WRAPPER_TAG,
    }:
        return []

    tool_calls: List[Dict[str, Any]] = []
    for child in root.find_all(recursive=False):
        if _xml_local_name(child.name).lower() not in {
            _PREFERRED_XML_CALL_TAG,
            _LEGACY_XML_CALL_TAG,
            "tool_call",
        }:
            continue
        raw_name = str(child.attrs.get("name", "") or "").strip()
        name = _resolve_tool_name(raw_name, allowed_tools)
        if not name:
            continue
        arguments = _parse_xml_invoke_arguments(child, allowed_tools.get(name))
        if arguments is None:
            continue
        tool_calls.append(
            {
                "id": _new_tool_call_id(),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )
    return tool_calls


def _try_parse_xml_tool_calls(
    text: str,
    allowed_tools: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    raw = str(text or "")
    tool_calls: List[Dict[str, Any]] = []
    for block in _find_tool_xml_wrapper_blocks(raw):
        tool_calls.extend(_parse_wrapped_xml_tool_calls(block, allowed_tools))

    if not tool_calls:
        repaired = _repair_missing_tool_xml_wrapper(raw)
        if repaired != raw:
            for block in _find_tool_xml_wrapper_blocks(repaired):
                tool_calls.extend(_parse_wrapped_xml_tool_calls(block, allowed_tools))

    if not tool_calls:
        pattern = re.compile(r"<([A-Za-z0-9_.:-]+)\s*([^<>]*?)\s*/>")
        matches = list(pattern.finditer(raw))
        for match in matches:
            raw_name = str(match.group(1) or "").strip()
            name = _resolve_tool_name(raw_name, allowed_tools)
            if not name:
                continue

            attrs = _parse_xml_attrs(match.group(2) or "")
            tool_calls.append(
                {
                    "id": _new_tool_call_id(),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(attrs, ensure_ascii=False),
                    },
                }
            )

    if not tool_calls:
        return None

    return {
        "mode": "tool_calls",
        "content": None,
        "tool_calls": tool_calls,
    }


def _parse_xml_attrs(raw_attrs: str) -> Dict[str, Any]:
    attrs: Dict[str, Any] = {}
    attr_pattern = re.compile(
        r"""([A-Za-z_][A-Za-z0-9_.:-]*)\s*=\s*(['"])(.*?)\2""",
        flags=re.DOTALL,
    )
    for match in attr_pattern.finditer(raw_attrs or ""):
        key = match.group(1)
        value = match.group(3)
        attrs[key] = html.unescape(value)
    return attrs
