"""
Validation and retry helpers for tool-calling.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
from typing import Any, Dict, List, Optional

from app.services.tool_calling_common import (
    _LEGACY_XML_ARG_TAG,
    _LEGACY_XML_CALL_TAG,
    _LEGACY_XML_WRAPPER_TAG,
    _PREFERRED_XML_ARG_TAG,
    _PREFERRED_XML_CALL_TAG,
    _PREFERRED_XML_WRAPPER_TAG,
    _describe_tool_choice,
    _format_tool_result_message,
    _get_max_tool_argument_chars,
    _get_max_tool_argument_depth,
    _get_max_tool_argument_nodes,
    _serialize_content,
    logger,
)
from app.services.tool_calling_parse import (
    _decode_tool_arguments,
    _repair_json_like_argument_string,
)
from app.services.tool_calling_prompts import _generate_tool_few_shot_examples

def _get_tool_validation_retry_limit() -> int:
    raw_value = str(os.getenv("TOOL_CALLING_INTERNAL_RETRY_MAX", "2") or "2").strip()
    try:
        value = int(raw_value)
    except Exception:
        value = 2
    return max(0, min(5, value))


def _get_tool_retry_strategy() -> str:
    raw_value = str(os.getenv("TOOL_CALLING_RETRY_STRATEGY", "focused_repair") or "focused_repair").strip().lower()
    aliases = {
        "focused": "focused_repair",
        "repair": "focused_repair",
        "minimal": "focused_repair",
        "compact": "focused_repair",
        "focused_repair": "focused_repair",
        "聚焦修复": "focused_repair",
        "full": "full_context",
        "legacy": "full_context",
        "context": "full_context",
        "full_context": "full_context",
        "完整上下文": "full_context",
    }
    return aliases.get(raw_value, "focused_repair")


def _describe_tool_retry_strategy(strategy: str) -> str:
    if strategy == "full_context":
        return "完整上下文"
    return "聚焦修复"


def _get_partial_tool_success_enabled() -> bool:
    raw_value = str(os.getenv("TOOL_CALLING_ALLOW_PARTIAL_SUCCESS", "1") or "1").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _get_tool_failure_degrade_enabled() -> bool:
    raw_value = str(os.getenv("TOOL_CALLING_DEGRADE_ON_FAILURE", "1") or "1").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _get_tool_failure_degrade_message() -> str:
    configured = str(os.getenv("TOOL_CALLING_DEGRADE_MESSAGE", "") or "").strip()
    if configured:
        return configured
    return (
        "Sorry, I ran into tool-call parsing issues. "
        "Please rephrase or provide more specific details."
    )


def _inspect_tool_response(
    raw_text: str,
    parsed: Dict[str, Any],
    tools: List[Dict[str, Any]],
    tool_choice: Any,
    parallel_tool_calls: Optional[bool],
) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    accepted_tool_calls: List[Dict[str, Any]] = []
    rejected_tool_calls: List[Dict[str, Any]] = []
    seen_tool_call_ids = set()
    seen_tool_call_signatures = set()
    allowed_tools = {
        str(item.get("function", {}).get("name", "") or "").strip(): item
        for item in tools or []
        if isinstance(item, dict)
    }
    tool_calls = parsed.get("tool_calls") or []
    required_tool_name = _get_required_tool_name(tool_choice)

    if tool_choice == "none" and tool_calls:
        errors.append(
            {
                "code": "tool_choice_none",
                "message": "tool_choice is 'none', but the assistant still returned tool_calls.",
            }
        )

    if parallel_tool_calls is False and len(tool_calls) > 1:
        errors.append(
            {
                "code": "parallel_tool_calls_disabled",
                "message": "Only one tool call is allowed in this response, but multiple tool_calls were returned.",
            }
        )

    if required_tool_name:
        if not tool_calls:
            errors.append(
                {
                    "code": "required_tool_missing",
                    "message": f'The tool "{required_tool_name}" was required but the assistant did not call it.',
                }
            )
        else:
            wrong_names = sorted(
                {
                    str(item.get("function", {}).get("name", "") or "").strip()
                    for item in tool_calls
                    if str(item.get("function", {}).get("name", "") or "").strip() != required_tool_name
                }
            )
            if wrong_names:
                errors.append(
                    {
                        "code": "wrong_required_tool",
                        "message": (
                            f'The tool "{required_tool_name}" was required, but the assistant returned '
                            f"{', '.join(wrong_names)}."
                        ),
                    }
                )

    if not tool_calls:
        if tool_choice == "required":
            errors.append(
                {
                    "code": "tool_required_but_missing",
                    "message": "At least one tool call was required, but the assistant answered without any tool_calls.",
                }
            )
        parsed_mode = str(parsed.get("mode", "") or "").strip().lower()
        parsed_content = "" if parsed.get("content") is None else str(parsed.get("content"))
        raw_stripped = str(raw_text or "").strip()
        is_structured_final_payload = (
            parsed_mode == "final" and parsed_content != raw_stripped
        )
        if not is_structured_final_payload:
            malformed_reason = _detect_malformed_tool_payload(raw_text)
            if malformed_reason:
                errors.append(
                    {
                        "code": "malformed_tool_payload",
                        "message": malformed_reason,
                    }
                )
        return {
            "errors": errors,
            "accepted_tool_calls": accepted_tool_calls,
            "rejected_tool_calls": rejected_tool_calls,
        }

    for index, tool_call in enumerate(tool_calls):
        tool_call_errors: List[Dict[str, Any]] = []
        function_data = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        tool_name = str(function_data.get("name", "") or "").strip()
        tool_call_id = str(tool_call.get("id", "") or "").strip()
        tool_def = allowed_tools.get(tool_name)
        if not tool_def:
            tool_call_errors.append(
                {
                    "code": "unknown_tool",
                    "message": f'Tool "{tool_name or "(missing)"}" is not declared in AVAILABLE_TOOLS.',
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_call_index": index,
                }
            )
            errors.extend(tool_call_errors)
            rejected_tool_calls.append(copy.deepcopy(tool_call))
            continue

        args = _decode_tool_arguments(tool_call)
        if args is None:
            tool_call_errors.append(
                {
                    "code": "invalid_arguments_json",
                    "message": f'Tool "{tool_name}" returned arguments that are not a valid JSON object.',
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_call_index": index,
                }
            )
            errors.extend(tool_call_errors)
            rejected_tool_calls.append(copy.deepcopy(tool_call))
            continue

        shape_errors = _validate_tool_argument_shape_limits(args)
        for message in shape_errors:
            tool_call_errors.append(
                {
                    "code": "argument_shape_limit_exceeded",
                    "message": f'Tool "{tool_name}" {message}',
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_call_index": index,
                }
            )

        schema = tool_def.get("function", {}).get("parameters")
        schema_errors = _validate_tool_arguments_against_schema(
            args=args,
            schema=schema,
            path="arguments",
        )
        for message in schema_errors:
            tool_call_errors.append(
                {
                    "code": "schema_validation_failed",
                    "message": f'Tool "{tool_name}" {message}',
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_call_index": index,
                }
            )

        if tool_call_id:
            if tool_call_id in seen_tool_call_ids:
                tool_call_errors.append(
                    {
                        "code": "duplicate_tool_call_id",
                        "message": f'Tool "{tool_name}" reuses the duplicate tool_call id "{tool_call_id}".',
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "tool_call_index": index,
                    }
                )
            else:
                seen_tool_call_ids.add(tool_call_id)

        signature = f"{tool_name}\u0000{_canonicalize_tool_args(args)}"
        if signature in seen_tool_call_signatures:
            tool_call_errors.append(
                {
                    "code": "duplicate_tool_call",
                    "message": f'Tool "{tool_name}" duplicates an earlier tool call with identical arguments.',
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_call_index": index,
                }
            )
        else:
            seen_tool_call_signatures.add(signature)

        if tool_call_errors:
            errors.extend(tool_call_errors)
            rejected_tool_calls.append(copy.deepcopy(tool_call))
            continue

        function_data["arguments"] = json.dumps(args, ensure_ascii=False)
        accepted_tool_calls.append(tool_call)

    return {
        "errors": errors,
        "accepted_tool_calls": accepted_tool_calls,
        "rejected_tool_calls": rejected_tool_calls,
    }


def _collect_tool_response_errors(
    raw_text: str,
    parsed: Dict[str, Any],
    tools: List[Dict[str, Any]],
    tool_choice: Any,
    parallel_tool_calls: Optional[bool],
) -> List[Dict[str, Any]]:
    return _inspect_tool_response(
        raw_text=raw_text,
        parsed=parsed,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
    ).get("errors", [])


def _is_partial_tool_success_eligible(
    inspection: Dict[str, Any],
    parallel_tool_calls: Optional[bool],
) -> bool:
    if not _get_partial_tool_success_enabled():
        return False
    if parallel_tool_calls is False:
        return False
    accepted_tool_calls = inspection.get("accepted_tool_calls") or []
    rejected_tool_calls = inspection.get("rejected_tool_calls") or []
    if not accepted_tool_calls or not rejected_tool_calls:
        return False

    blocking_codes = {
        "tool_choice_none",
        "parallel_tool_calls_disabled",
        "required_tool_missing",
        "wrong_required_tool",
        "tool_required_but_missing",
        "malformed_tool_payload",
    }
    return not any(
        str(item.get("code", "") or "").strip() in blocking_codes
        for item in inspection.get("errors") or []
        if isinstance(item, dict)
    )


def _build_partial_tool_success_response(
    parsed: Dict[str, Any],
    inspection: Dict[str, Any],
) -> Dict[str, Any]:
    accepted_tool_calls = inspection.get("accepted_tool_calls") or []
    rejected_tool_calls = inspection.get("rejected_tool_calls") or []
    logger.warning(
        "[tool_calling] 并行工具调用部分成功，已放行通过校验的 tool_calls "
        f"accepted={len(accepted_tool_calls)} rejected={len(rejected_tool_calls)}"
    )
    return {
        "mode": "tool_calls",
        "content": parsed.get("content"),
        "tool_calls": copy.deepcopy(accepted_tool_calls),
    }


def _build_tool_calling_degraded_response(
    parsed: Optional[Dict[str, Any]],
    inspection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if isinstance(inspection, dict):
        rejected_tool_calls = [
            item for item in (inspection.get("rejected_tool_calls") or [])
            if isinstance(item, dict)
        ]
        if rejected_tool_calls:
            logger.warning(
                "[tool_calling] 修复耗尽后保留原始 tool_calls 以闭合客户端工具协议 "
                f"count={len(rejected_tool_calls)}"
            )
            return {
                "mode": "tool_calls",
                "content": None,
                "tool_calls": copy.deepcopy(rejected_tool_calls),
            }

    content = ""
    if isinstance(parsed, dict) and not parsed.get("tool_calls"):
        content = str(parsed.get("content") or "").strip()
    if not content:
        content = _get_tool_failure_degrade_message()
    return {
        "mode": "final",
        "content": content,
        "tool_calls": [],
    }


def _get_required_tool_name(tool_choice: Any) -> str:
    if isinstance(tool_choice, dict):
        function_data = (
            tool_choice.get("function")
            if isinstance(tool_choice.get("function"), dict)
            else {}
        )
        return str(function_data.get("name", "") or "").strip()
    return ""


def _detect_malformed_tool_payload(raw_text: str) -> str:
    stripped = str(raw_text or "").strip()
    if not stripped:
        return ""

    lowered = stripped.lower()
    if stripped[:1] in {"{", "["}:
        if any(
            marker in lowered
            for marker in ('"tool_calls"', '"function"', '"arguments"', '"tool_name"')
        ):
            return (
                "The reply looked like a structured tool payload, but it could not be parsed "
                "into valid tool_calls."
            )

    if stripped.startswith("<") and re.search(r"<[A-Za-z0-9_.:-]+\s+[^<>]*/>", stripped):
        return (
            "The reply looked like an XML-style tool call, but it could not be parsed "
            "into a valid declared tool."
        )

    return ""


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


def _validate_tool_arguments_against_schema(
    args: Dict[str, Any],
    schema: Any,
    path: str,
) -> List[str]:
    if not isinstance(schema, dict):
        return []
    return _validate_json_schema_value(args, schema, path=path)


def _walk_json_shape(
    value: Any,
    path: str,
    depth: int,
    counters: Dict[str, int],
    errors: List[str],
) -> None:
    counters["nodes"] += 1
    counters["max_depth"] = max(counters.get("max_depth", 0), depth)
    max_depth = _get_max_tool_argument_depth()
    max_nodes = _get_max_tool_argument_nodes()

    if counters["nodes"] > max_nodes:
        errors.append(f"{path} exceeds the maximum structural node count of {max_nodes}.")
        return

    if depth > max_depth:
        errors.append(f"{path} exceeds the maximum nesting depth of {max_depth}.")
        return

    if isinstance(value, dict):
        for key, item in value.items():
            _walk_json_shape(item, f"{path}.{key}", depth + 1, counters, errors)
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk_json_shape(item, f"{path}[{index}]", depth + 1, counters, errors)


def _validate_tool_argument_shape_limits(args: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    try:
        serialized = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return ["arguments could not be serialized into a stable JSON object."]

    max_chars = _get_max_tool_argument_chars()
    if len(serialized) > max_chars:
        errors.append(f"arguments exceed the maximum serialized size of {max_chars} characters.")

    counters = {"nodes": 0, "max_depth": 0}
    _walk_json_shape(args, "arguments", 1, counters, errors)
    return errors


def _validate_json_schema_value(value: Any, schema: Dict[str, Any], path: str) -> List[str]:
    errors: List[str] = []

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        if not any(not _validate_json_schema_value(value, item, path) for item in any_of if isinstance(item, dict)):
            errors.append(f"{path} does not satisfy any allowed schema branch.")
        return errors

    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        valid_count = sum(
            1
            for item in one_of
            if isinstance(item, dict) and not _validate_json_schema_value(value, item, path)
        )
        if valid_count != 1:
            errors.append(f"{path} must satisfy exactly one schema branch.")
        return errors

    all_of = schema.get("allOf")
    if isinstance(all_of, list) and all_of:
        for item in all_of:
            if isinstance(item, dict):
                errors.extend(_validate_json_schema_value(value, item, path))

    expected_types = _extract_schema_types(schema)
    if expected_types and not any(_value_matches_schema_type(value, item) for item in expected_types):
        errors.append(
            f"{path} must be {_describe_schema_types(expected_types)}, got {_describe_runtime_type(value)}."
        )
        return errors

    if "const" in schema and value != schema.get("const"):
        errors.append(f"{path} must equal {schema.get('const')!r}.")

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values and value not in enum_values:
        errors.append(f"{path} must be one of {enum_values!r}.")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path} must be at least {min_length} characters long.")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path} must be at most {max_length} characters long.")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                if re.search(pattern, value) is None:
                    errors.append(f"{path} must match pattern {pattern!r}.")
            except re.error:
                pass

    if _is_number_like(value):
        if not math.isfinite(float(value)):
            errors.append(f"{path} must be a finite number.")
            return errors
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path} must be >= {minimum}.")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path} must be <= {maximum}.")
        exclusive_minimum = schema.get("exclusiveMinimum")
        if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
            errors.append(f"{path} must be > {exclusive_minimum}.")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
            errors.append(f"{path} must be < {exclusive_maximum}.")
        multiple_of = schema.get("multipleOf")
        if isinstance(multiple_of, (int, float)) and multiple_of not in (0, 0.0):
            try:
                quotient = float(value) / float(multiple_of)
                if not math.isclose(quotient, round(quotient), rel_tol=0.0, abs_tol=1e-9):
                    errors.append(f"{path} must be a multiple of {multiple_of}.")
            except Exception:
                pass

    if isinstance(value, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path} must contain at least {min_items} items.")
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{path} must contain at most {max_items} items.")
        if schema.get("uniqueItems") is True:
            seen = set()
            for index, item in enumerate(value):
                try:
                    marker = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                except Exception:
                    marker = repr(item)
                if marker in seen:
                    errors.append(f"{path}[{index}] duplicates an earlier array item.")
                    break
                seen.add(marker)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    _validate_json_schema_value(item, item_schema, path=f"{path}[{index}]")
                )

    if isinstance(value, dict):
        min_properties = schema.get("minProperties")
        if isinstance(min_properties, int) and len(value) < min_properties:
            errors.append(f"{path} must contain at least {min_properties} properties.")
        max_properties = schema.get("maxProperties")
        if isinstance(max_properties, int) and len(value) > max_properties:
            errors.append(f"{path} must contain at most {max_properties} properties.")
        required_fields = schema.get("required")
        if isinstance(required_fields, list):
            for field_name in required_fields:
                if field_name not in value:
                    errors.append(f"{path}.{field_name} is required.")

        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for field_name, field_schema in properties.items():
            if field_name in value and isinstance(field_schema, dict):
                errors.extend(
                    _validate_json_schema_value(
                        value[field_name],
                        field_schema,
                        path=f"{path}.{field_name}",
                    )
                )

        additional_properties = schema.get("additionalProperties", True)
        extra_keys = [field_name for field_name in value.keys() if field_name not in properties]
        if additional_properties is False:
            for field_name in extra_keys:
                logger.warning(
                    "[tool_calling] rejected hallucinated argument "
                    f"path={path}.{field_name}"
                )
                errors.append(f"{path}.{field_name} is not allowed.")
        elif isinstance(additional_properties, dict):
            for field_name in extra_keys:
                errors.extend(
                    _validate_json_schema_value(
                        value[field_name],
                        additional_properties,
                        path=f"{path}.{field_name}",
                    )
                )

    return errors


def _extract_schema_types(schema: Dict[str, Any]) -> List[str]:
    raw_type = schema.get("type")
    if isinstance(raw_type, str):
        return [raw_type]
    if isinstance(raw_type, list):
        return [item for item in raw_type if isinstance(item, str)]
    if any(key in schema for key in ("properties", "required", "additionalProperties")):
        return ["object"]
    if any(key in schema for key in ("items", "minItems", "maxItems")):
        return ["array"]
    return []


def _value_matches_schema_type(value: Any, schema_type: str) -> bool:
    normalized = str(schema_type or "").strip().lower()
    if normalized == "object":
        return isinstance(value, dict)
    if normalized == "array":
        return isinstance(value, list)
    if normalized == "string":
        return isinstance(value, str)
    if normalized == "boolean":
        return isinstance(value, bool)
    if normalized == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized == "number":
        return _is_number_like(value)
    if normalized == "null":
        return value is None
    return True


def _describe_schema_types(schema_types: List[str]) -> str:
    normalized = [str(item or "").strip().lower() for item in schema_types if str(item or "").strip()]
    if not normalized:
        return "a valid value"
    if len(normalized) == 1:
        return normalized[0]
    return " or ".join(normalized)


def _describe_runtime_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _is_number_like(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _canonicalize_tool_args(args: Dict[str, Any]) -> str:
    try:
        return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return repr(args)


def _build_tool_retry_messages(
    raw_text: str,
    parsed: Dict[str, Any],
    errors: List[Dict[str, Any]],
    attempt: int,
    total_attempts: int,
) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    assistant_message = _build_rejected_assistant_message(raw_text, parsed)
    if assistant_message:
        messages.append(assistant_message)
    messages.append(
        {
            "role": "user",
            "content": _format_tool_retry_feedback(errors, parsed, raw_text, attempt, total_attempts),
        }
    )
    return messages


def _build_focused_tool_retry_messages(
    original_messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    tool_choice: Any,
    parallel_tool_calls: Optional[bool],
    raw_text: str,
    parsed: Dict[str, Any],
    errors: List[Dict[str, Any]],
    attempt: int,
    total_attempts: int,
) -> List[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_tool_repair_system_prompt(
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
            ),
        },
        {
            "role": "user",
            "content": _format_focused_tool_retry_feedback(
                original_messages=original_messages,
                errors=errors,
                parsed=parsed,
                raw_text=raw_text,
                attempt=attempt,
                total_attempts=total_attempts,
            ),
        },
    ]


def _build_rejected_assistant_message(
    raw_text: str,
    parsed: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    tool_calls = parsed.get("tool_calls") or []
    if tool_calls:
        content = parsed.get("content")
        return {
            "role": "assistant",
            "content": content if content not in ("", None) else None,
            "tool_calls": copy.deepcopy(tool_calls),
        }

    preview = str(raw_text or "").strip()
    if preview:
        return {"role": "assistant", "content": preview}

    content = parsed.get("content")
    if content not in ("", None):
        return {"role": "assistant", "content": str(content)}
    return None


def _format_tool_retry_feedback(
    errors: List[Dict[str, Any]],
    parsed: Dict[str, Any],
    raw_text: str,
    attempt: int,
    total_attempts: int,
) -> str:
    lines = [
        "[Tool Repair Feedback]",
        "Your previous assistant response was rejected before execution and was not forwarded to the user.",
        "Keep the original intent and make the smallest possible valid fix.",
        f"Attempt: {attempt}/{total_attempts}",
        "Validation errors:",
    ]
    for index, item in enumerate(errors, start=1):
        lines.append(f"{index}. {item.get('message')}")

    rejected_tool_calls = _summarize_tool_calls_for_feedback(parsed)
    if rejected_tool_calls:
        lines.append("Rejected tool calls:")
        lines.append(json.dumps(rejected_tool_calls, ensure_ascii=False, indent=2))
    else:
        preview = str(raw_text or "").strip()
        if preview:
            if len(preview) > 1200:
                preview = preview[:1197] + "..."
            lines.append("Rejected assistant reply:")
            lines.append(preview)

    lines.extend(
        [
            "Return only the corrected tool-call output now.",
            "Prefer the XML tool-call block for tool use. JSON assistant payloads are still accepted.",
            "Prefer repairing the rejected response instead of rewriting from scratch.",
            "Do not repeat the same invalid tool call unchanged.",
        ]
    )
    return "\n".join(lines)


def _build_tool_repair_system_prompt(
    tools: List[Dict[str, Any]],
    tool_choice: Any,
    parallel_tool_calls: Optional[bool],
) -> str:
    choice_instruction = _describe_tool_choice(tool_choice)
    parallel_instruction = (
        "You may return more than one tool call in a single response."
        if parallel_tool_calls is not False
        else "Return at most one tool call in a single response."
    )
    tool_defs = json.dumps(tools or [], ensure_ascii=False, indent=2)
    examples = _generate_tool_few_shot_examples(tools)
    return (
        "You are repairing a previously rejected assistant response for an OpenAI-compatible tool-calling adapter.\n"
        "Do not solve the whole task again from scratch unless the rejected response is unusable.\n"
        "Preserve the original intent and make the smallest valid correction.\n"
        "Typical fixes include: wrong tool name, missing required tool, invalid argument JSON, schema mismatch, "
        "tool-choice violation, or too many tool calls.\n"
        "If tools are needed, prefer returning only one standalone XML tool-call block and nothing else.\n"
        "Preferred XML tool-call format:\n"
        f"<{_PREFERRED_XML_WRAPPER_TAG}>\n"
        f"  <{_PREFERRED_XML_CALL_TAG} name=\"tool_name\">\n"
        f"    <{_PREFERRED_XML_ARG_TAG} name=\"arg_name\"><![CDATA[value]]></{_PREFERRED_XML_ARG_TAG}>\n"
        f"  </{_PREFERRED_XML_CALL_TAG}>\n"
        f"</{_PREFERRED_XML_WRAPPER_TAG}>\n"
        f"Legacy XML compatibility is still accepted: <{_LEGACY_XML_WRAPPER_TAG}> / <{_LEGACY_XML_CALL_TAG}> / <{_LEGACY_XML_ARG_TAG}>.\n"
        "Compatibility JSON tool-call schema is still accepted:\n"
        '{"role":"assistant","content":null,"tool_calls":[{"type":"function","function":{"name":"tool_name","arguments":{"arg":"value"}}}]}\n'
        "If no tool is needed, answer normally in plain text.\n"
        "Rules:\n"
        "- Never use markdown code fences.\n"
        "- Only use tools declared in AVAILABLE_TOOLS.\n"
        "- If you use the JSON schema, arguments must be a JSON object, not a string.\n"
        f"- {choice_instruction}\n"
        f"- {parallel_instruction}\n"
        f"{examples}"
        "AVAILABLE_TOOLS:\n"
        f"{tool_defs}"
    )


def _format_focused_tool_retry_feedback(
    original_messages: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    parsed: Dict[str, Any],
    raw_text: str,
    attempt: int,
    total_attempts: int,
) -> str:
    lines = [
        "[Focused Repair Task]",
        "Repair the rejected assistant JSON response below.",
        "Do not reconsider the full conversation. Keep the original intent and change as little as possible.",
        f"Attempt: {attempt}/{total_attempts}",
        "Validation errors:",
    ]
    for index, item in enumerate(errors, start=1):
        lines.append(f"{index}. {item.get('message')}")

    compact_context = _build_compact_tool_retry_context(original_messages)
    if compact_context:
        lines.append("Minimal context:")
        lines.append(compact_context)

    rejected_tool_calls = _summarize_tool_calls_for_feedback(parsed)
    if rejected_tool_calls:
        lines.append("Rejected tool calls:")
        lines.append(json.dumps(rejected_tool_calls, ensure_ascii=False, indent=2))
        rejected_content = parsed.get("content")
        if rejected_content not in ("", None):
            lines.append("Rejected assistant content:")
            lines.append(_trim_retry_text(str(rejected_content), 1200))
    else:
        preview = str(raw_text or "").strip()
        if preview:
            lines.append("Rejected assistant reply:")
            lines.append(_trim_retry_text(preview, 1200))

    lines.extend(
        [
            "Return only the corrected tool-call output.",
            "Prefer the XML tool-call block for tool use. JSON assistant payloads are still accepted.",
            "If the rejected response is almost correct, make the smallest possible fix.",
            "Do not repeat the same invalid response unchanged.",
        ]
    )
    return "\n".join(lines)


def _build_compact_tool_retry_context(
    messages: List[Dict[str, Any]],
    max_messages: int = 3,
    max_chars: int = 2200,
) -> str:
    items = list(messages or [])
    selected: List[str] = []
    anchor_indexes = set()

    for index, msg in enumerate(items):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "") or "").strip().lower()
        if role != "system":
            continue
        block = _format_anchor_message_for_retry_context(msg, "Original System Message")
        if block:
            selected.append(block)
            anchor_indexes.add(index)
        break

    for index, msg in enumerate(items):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "") or "").strip().lower()
        if role != "user":
            continue
        block = _format_anchor_message_for_retry_context(msg, "Original User Request")
        if block:
            selected.append(block)
            anchor_indexes.add(index)
        break

    recent: List[str] = []
    for index, msg in reversed(list(enumerate(items))):
        if index in anchor_indexes:
            continue
        block = _format_message_for_retry_context(msg)
        if not block:
            continue
        recent.append(block)
        if len(recent) >= max_messages:
            break

    recent.reverse()
    selected.extend(recent)
    if not selected:
        return ""

    parts: List[str] = []
    used = 0
    for block in selected:
        remaining = max_chars - used
        if remaining <= 0:
            break
        trimmed = _trim_retry_text(block, remaining)
        if not trimmed:
            continue
        parts.append(trimmed)
        used += len(trimmed) + 2
    return "\n\n".join(parts)


def _format_anchor_message_for_retry_context(msg: Any, label: str) -> str:
    if not isinstance(msg, dict):
        return ""
    content = _serialize_content(msg.get("content", "")).strip()
    if not content:
        return ""
    return f"[{label}]\n" + _trim_retry_text(content, 1200)


def _format_message_for_retry_context(msg: Any) -> str:
    if not isinstance(msg, dict):
        return ""

    role = str(msg.get("role", "") or "").strip().lower()
    if role == "system":
        return ""

    if role == "tool":
        payload = _format_tool_result_message(
            name=str(msg.get("name", "") or "").strip() or "tool",
            tool_call_id=str(msg.get("tool_call_id", "") or "").strip(),
            content=_serialize_content(msg.get("content", "")),
        )
        return "[Recent Tool Result]\n" + _trim_retry_text(payload, 1200)

    if role == "assistant" and msg.get("tool_calls"):
        tool_calls_payload = []
        for item in msg.get("tool_calls") or []:
            if not isinstance(item, dict):
                continue
            function_data = item.get("function") if isinstance(item.get("function"), dict) else {}
            tool_calls_payload.append(
                {
                    "id": item.get("id"),
                    "type": item.get("type", "function"),
                    "function": {
                        "name": function_data.get("name"),
                        "arguments": function_data.get("arguments"),
                    },
                }
            )
        body = json.dumps(tool_calls_payload, ensure_ascii=False, indent=2)
        content = _serialize_content(msg.get("content", "")).strip()
        if content:
            body = content + "\n\n" + body
        return "[Recent Assistant Tool Calls]\n" + _trim_retry_text(body, 1200)

    content = _serialize_content(msg.get("content", "")).strip()
    if not content:
        return ""

    role_title = {
        "user": "Recent User Message",
        "assistant": "Recent Assistant Message",
    }.get(role, "Recent Message")
    return f"[{role_title}]\n" + _trim_retry_text(content, 1200)


def _trim_retry_text(text: str, limit: int) -> str:
    value = str(text or "")
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit <= 9:
        return value[:limit]

    reserved = 5
    head = max(1, int((limit - reserved) * 0.7))
    tail = max(1, limit - reserved - head)
    return value[:head] + "\n...\n" + value[-tail:]


def _summarize_tool_calls_for_feedback(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    summary: List[Dict[str, Any]] = []
    for item in parsed.get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        function_data = item.get("function") if isinstance(item.get("function"), dict) else {}
        arguments = _decode_tool_arguments(item)
        summary.append(
            {
                "id": str(item.get("id", "") or ""),
                "name": str(function_data.get("name", "") or ""),
                "arguments": arguments if arguments is not None else function_data.get("arguments"),
            }
        )
    return summary


def _summarize_tool_response_errors(errors: List[Dict[str, Any]]) -> str:
    messages = [str(item.get("message") or "").strip() for item in errors if str(item.get("message") or "").strip()]
    if not messages:
        return "tool_call_validation_failed"
    return "; ".join(messages[:3])
