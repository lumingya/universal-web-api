"""
app/services/tool_calling.py - OpenAI-compatible tool calling adapter.

This module bridges plain-text web model responses and OpenAI tool-calling
responses by:
- normalizing incoming `tools` / legacy `functions`
- converting tool history into plain-text prompts the web model can follow
- parsing structured JSON (and simple XML-like fallbacks) back into tool_calls
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.tool_calling_common import (
    AsyncToolRoundExecutor,
    ToolRoundExecutor,
    get_tool_calling_allow_media_postprocess,
    get_tool_calling_sanitize_assistant_content_enabled,
    logger,
)
from app.services.tool_calling_parse import (
    _decode_tool_arguments,
    build_tool_completion_response,
    decode_browser_non_stream_payload,
    extract_tool_calling_assistant_content,
    iter_tool_stream_chunks,
    parse_tool_response,
)
from app.services.tool_calling_prompts import (
    build_browser_messages_for_tools,
    has_tool_calling_request,
    normalize_tool_request,
    summarize_messages_for_debug,
)
from app.services.tool_calling_validation_retry import (
    _build_focused_tool_retry_messages,
    _build_partial_tool_success_response,
    _build_tool_calling_degraded_response,
    _build_tool_retry_messages,
    _describe_tool_retry_strategy,
    _get_tool_failure_degrade_enabled,
    _get_tool_retry_strategy,
    _get_tool_validation_retry_limit,
    _inspect_tool_response,
    _is_partial_tool_success_eligible,
    _summarize_tool_response_errors,
)


def _copy_message_list_shallow(messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return [dict(msg) if isinstance(msg, dict) else msg for msg in (messages or [])]


def complete_tool_calling_roundtrip(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    tool_choice: Any,
    parallel_tool_calls: Optional[bool],
    round_executor: ToolRoundExecutor,
    stop_checker=None,
) -> Dict[str, Any]:
    conversation = _copy_message_list_shallow(messages)
    retry_limit = _get_tool_validation_retry_limit()
    retry_strategy = _get_tool_retry_strategy()
    total_attempts = retry_limit + 1
    last_summary = "tool_call_validation_failed"
    last_parsed: Dict[str, Any] = {"mode": "final", "content": "", "tool_calls": []}
    pending_retry_messages: Optional[List[Dict[str, str]]] = None

    for attempt in range(1, total_attempts + 1):
        if stop_checker and stop_checker():
            raise RuntimeError("tool_calling_cancelled")

        if pending_retry_messages is not None:
            browser_messages = pending_retry_messages
        else:
            browser_messages = build_browser_messages_for_tools(
                messages=conversation,
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
            )
        assistant_text = str(round_executor(browser_messages) or "")
        parsed = parse_tool_response(assistant_text, tools)
        last_parsed = parsed
        inspection = _inspect_tool_response(
            raw_text=assistant_text,
            parsed=parsed,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
        )
        errors = inspection.get("errors") or []
        if not errors:
            if attempt > 1:
                logger.warning(
                    "[tool_calling] 函数调用候选已在内部修复后通过校验 "
                    f"轮次={attempt}/{total_attempts} "
                    f"已用修复重试={attempt - 1}/{retry_limit} "
                    f"策略={_describe_tool_retry_strategy(retry_strategy)}"
                )
            return parsed

        if _is_partial_tool_success_eligible(inspection, parallel_tool_calls):
            return _build_partial_tool_success_response(parsed, inspection)

        last_summary = _summarize_tool_response_errors(errors)
        if attempt >= total_attempts:
            logger.warning(
                "[tool_calling] 函数调用内部修复次数已耗尽 "
                f"轮次={attempt}/{total_attempts} "
                f"配置上限={retry_limit} "
                f"策略={_describe_tool_retry_strategy(retry_strategy)} "
                f"最后错误={last_summary}"
            )
            if _get_tool_failure_degrade_enabled():
                logger.warning("[tool_calling] 修复耗尽，尝试保留原始 tool_calls；不会降级为普通文本")
                return _build_tool_calling_degraded_response(last_parsed, inspection, last_summary)
            raise RuntimeError(f"tool_call_validation_exhausted: {last_summary}")

        remaining_retries = total_attempts - attempt
        logger.warning(
            "[tool_calling] 函数调用候选校验未通过，准备进行内部修复重试 "
            f"轮次={attempt}/{total_attempts} "
            f"配置上限={retry_limit} "
            f"策略={_describe_tool_retry_strategy(retry_strategy)} "
            f"剩余重试={remaining_retries} "
            f"原因={last_summary}"
        )
        if retry_strategy == "focused_repair":
            pending_retry_messages = _build_focused_tool_retry_messages(
                original_messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
                raw_text=assistant_text,
                parsed=parsed,
                errors=errors,
                attempt=attempt,
                total_attempts=total_attempts,
            )
        else:
            conversation.extend(
                _build_tool_retry_messages(
                    raw_text=assistant_text,
                    parsed=parsed,
                    errors=errors,
                    attempt=attempt,
                    total_attempts=total_attempts,
                )
            )
            pending_retry_messages = None

    if _get_tool_failure_degrade_enabled():
        logger.warning("[tool_calling] 修复耗尽，尝试保留原始 tool_calls；不会降级为普通文本")
        return _build_tool_calling_degraded_response(last_parsed, None, last_summary)
    raise RuntimeError(f"tool_call_validation_exhausted: {last_summary}")


async def complete_tool_calling_roundtrip_async(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    tool_choice: Any,
    parallel_tool_calls: Optional[bool],
    round_executor: AsyncToolRoundExecutor,
    stop_checker=None,
) -> Dict[str, Any]:
    conversation = _copy_message_list_shallow(messages)
    retry_limit = _get_tool_validation_retry_limit()
    retry_strategy = _get_tool_retry_strategy()
    total_attempts = retry_limit + 1
    last_summary = "tool_call_validation_failed"
    last_parsed: Dict[str, Any] = {"mode": "final", "content": "", "tool_calls": []}
    pending_retry_messages: Optional[List[Dict[str, str]]] = None

    for attempt in range(1, total_attempts + 1):
        if stop_checker and stop_checker():
            raise RuntimeError("tool_calling_cancelled")

        if pending_retry_messages is not None:
            browser_messages = pending_retry_messages
        else:
            browser_messages = build_browser_messages_for_tools(
                messages=conversation,
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
            )
        assistant_text = str(await round_executor(browser_messages) or "")
        parsed = parse_tool_response(assistant_text, tools)
        last_parsed = parsed
        inspection = _inspect_tool_response(
            raw_text=assistant_text,
            parsed=parsed,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
        )
        errors = inspection.get("errors") or []
        if not errors:
            if attempt > 1:
                logger.warning(
                    "[tool_calling] 函数调用候选已在内部修复后通过校验 "
                    f"轮次={attempt}/{total_attempts} "
                    f"已用修复重试={attempt - 1}/{retry_limit} "
                    f"策略={_describe_tool_retry_strategy(retry_strategy)}"
                )
            return parsed

        if _is_partial_tool_success_eligible(inspection, parallel_tool_calls):
            return _build_partial_tool_success_response(parsed, inspection)

        last_summary = _summarize_tool_response_errors(errors)
        if attempt >= total_attempts:
            logger.warning(
                "[tool_calling] 函数调用内部修复次数已耗尽 "
                f"轮次={attempt}/{total_attempts} "
                f"配置上限={retry_limit} "
                f"策略={_describe_tool_retry_strategy(retry_strategy)} "
                f"最后错误={last_summary}"
            )
            if _get_tool_failure_degrade_enabled():
                logger.warning("[tool_calling] 修复耗尽，尝试保留原始 tool_calls；不会降级为普通文本")
                return _build_tool_calling_degraded_response(last_parsed, inspection, last_summary)
            raise RuntimeError(f"tool_call_validation_exhausted: {last_summary}")

        remaining_retries = total_attempts - attempt
        logger.warning(
            "[tool_calling] 函数调用候选校验未通过，准备进行内部修复重试 "
            f"轮次={attempt}/{total_attempts} "
            f"配置上限={retry_limit} "
            f"策略={_describe_tool_retry_strategy(retry_strategy)} "
            f"剩余重试={remaining_retries} "
            f"原因={last_summary}"
        )
        if retry_strategy == "focused_repair":
            pending_retry_messages = _build_focused_tool_retry_messages(
                original_messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
                raw_text=assistant_text,
                parsed=parsed,
                errors=errors,
                attempt=attempt,
                total_attempts=total_attempts,
            )
        else:
            conversation.extend(
                _build_tool_retry_messages(
                    raw_text=assistant_text,
                    parsed=parsed,
                    errors=errors,
                    attempt=attempt,
                    total_attempts=total_attempts,
                )
            )
            pending_retry_messages = None

    if _get_tool_failure_degrade_enabled():
        logger.warning("[tool_calling] 修复耗尽，尝试保留原始 tool_calls；不会降级为普通文本")
        return _build_tool_calling_degraded_response(last_parsed, None, last_summary)
    raise RuntimeError(f"tool_call_validation_exhausted: {last_summary}")

__all__ = [
    "build_browser_messages_for_tools",
    "build_tool_completion_response",
    "extract_tool_calling_assistant_content",
    "get_tool_calling_allow_media_postprocess",
    "get_tool_calling_sanitize_assistant_content_enabled",
    "decode_browser_non_stream_payload",
    "complete_tool_calling_roundtrip",
    "complete_tool_calling_roundtrip_async",
    "has_tool_calling_request",
    "iter_tool_stream_chunks",
    "normalize_tool_request",
    "parse_tool_response",
    "summarize_messages_for_debug",
    "_decode_tool_arguments",
]
