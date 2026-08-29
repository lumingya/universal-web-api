"""
app/services/error_metadata.py - 标准异常元数据与响应构建器

职责：
- 解耦业务特定错误码（如 Arena / Gemini / Claude 拒绝与失败码）与主执行流
- 提供标准化的 ErrorMetadata 数据结构
- 提供基于元数据的 422 / 非可重试错误识别与统一 JSONResponse 构造
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional, Set
from fastapi.responses import JSONResponse

from app.services.arena_image_generation import (
    ARENA_NON_RETRYABLE_CODES,
    ARENA_PROMPT_REJECTED_CODE,
)
from app.core.workflow.arena_send_watchdog import (
    ARENA_PAGE_ERROR,
    ARENA_SEND_NO_TARGET_AFTER_RETRY,
)


@dataclasses.dataclass(frozen=True)
class ErrorMetadata:
    code: str
    message: str
    status_code: int = 422
    retryable: bool = False
    error_type: str = "invalid_request_error"
    param: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


_MANUAL_TERMINATE_REASONS: Set[str] = {
    "manual_terminate",
    "manual_terminate_from_tab_pool",
}

_CANCEL_REASONS: Set[str] = {
    "manual_terminate",
    "manual_terminate_from_tab_pool",
    "client_disconnected",
    "coroutine_cancelled",
    "cleanup",
    "stream_done",
    "stop_sequence",
    "audio_media_fast_return",
    "request_cancelled",
}

_PROMPT_REJECTION_CODES: Set[str] = {
    ARENA_PROMPT_REJECTED_CODE,
    "prompt_rejected",
}

_KNOWN_NON_RETRYABLE_CODES: Set[str] = {
    *ARENA_NON_RETRYABLE_CODES,
    ARENA_SEND_NO_TARGET_AFTER_RETRY,
    ARENA_PAGE_ERROR,
    *_PROMPT_REJECTION_CODES,
}


def is_manual_terminate(reason_or_ctx: Any) -> bool:
    """Check if the cancellation is due to manual termination."""
    if hasattr(reason_or_ctx, "cancel_reason"):
        reason = getattr(reason_or_ctx, "cancel_reason")
    else:
        reason = reason_or_ctx
    return str(reason or "").strip() in _MANUAL_TERMINATE_REASONS


def is_prompt_rejection_code(code: Any) -> bool:
    """Check if code matches known prompt rejection codes."""
    return str(code or "").strip() in _PROMPT_REJECTION_CODES


def _parse_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        s = val.strip().lower()
        if s in {"1", "true", "yes"}:
            return True
        if s in {"0", "false", "no"}:
            return False
    return bool(val)


def _parse_int(val: Any, default: int = 422) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _extract_json_dict_from_text(text: str) -> Optional[Dict[str, Any]]:
    """尝试从文本中提取最外层的 JSON 字典对象。"""
    import json
    if not text or "{" not in text or "}" not in text:
        return None
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return None
    candidate = text[start_idx : end_idx + 1].strip()
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _extract_status_code_from_text(text: str) -> Optional[int]:
    """从错误字符串中提取 HTTP 状态码。"""
    import re
    if not text:
        return None
    # 模式 1: 带有明确上下文前缀
    match = re.search(
        r"\b(?:http(?:\s+status)?\s+|status(?:\s*code)?\s*[:=]?\s*|failed\s+to\s+fetch(?:\s+image)?:\s*|code\s*[:=]\s*)([45]\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if match:
        try:
            return int(match.group(1))
        except Exception:
            pass
    # 模式 2: 常见 HTTP 状态码 + 描述词组合（如 "400 Bad Request", "429 Too Many Requests", "502 Bad Gateway"）
    match_status_desc = re.search(
        r"\b([45]\d{2})\s+(?:Bad\s+Request|Unauthorized|Forbidden|Not\s+Found|Unprocessable\s+Entity|Too\s+Many\s+Requests|Internal\s+Server\s+Error|Bad\s+Gateway|Service\s+Unavailable|Gateway\s+Timeout)\b",
        text,
        re.IGNORECASE,
    )
    if match_status_desc:
        try:
            return int(match_status_desc.group(1))
        except Exception:
            pass
    return None


def resolve_error_metadata(
    reason_or_payload: Any,
    default_message: Optional[str] = None,
) -> Optional[ErrorMetadata]:
    """Extract standard ErrorMetadata from a context, cancel reason, exception, or payload dict."""
    if reason_or_payload is None:
        return None

    # Handle Exception objects
    if isinstance(reason_or_payload, BaseException):
        code = getattr(reason_or_payload, "code", None) or getattr(reason_or_payload, "error_code", None)
        msg = (
            getattr(reason_or_payload, "message", None)
            or getattr(reason_or_payload, "error_msg", None)
            or str(reason_or_payload)
        )
        if code:
            return resolve_error_metadata(str(code), default_message=str(msg))
        return resolve_error_metadata(str(reason_or_payload), default_message=default_message)

    # Handle RequestContext-like objects
    if hasattr(reason_or_payload, "cancel_reason"):
        raw_reason = str(getattr(reason_or_payload, "cancel_reason") or "").strip()
        if not raw_reason:
            return None
        if raw_reason in _CANCEL_REASONS:
            return None
        if raw_reason in _PROMPT_REJECTION_CODES:
            return ErrorMetadata(
                code=raw_reason,
                message=default_message or "Arena 拒绝了该提示词：内容违反 Terms of Use",
                status_code=422,
                retryable=False,
                error_type="invalid_request_error",
            )
        if raw_reason in _KNOWN_NON_RETRYABLE_CODES:
            return ErrorMetadata(
                code=raw_reason,
                message=default_message or "Arena 图片生成失败，响应不可重试",
                status_code=422,
                retryable=False,
                error_type="invalid_request_error",
            )
        return resolve_error_metadata(raw_reason, default_message=default_message)

    # Handle payload dict like {"error": {...}} or SSE payload dict
    if isinstance(reason_or_payload, dict):
        error = reason_or_payload.get("error") if "error" in reason_or_payload else reason_or_payload
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            msg = str(error.get("message") or "").strip()
            raw_status_code = error.get("status_code")
            raw_retryable = error.get("retryable")
            param = error.get("param")
            extra = {
                k: v for k, v in error.items()
                if k not in {"message", "type", "code", "status_code", "retryable", "param"}
            }
            status_code = _parse_int(raw_status_code, 422) if raw_status_code is not None else 422
            retryable = _parse_bool(raw_retryable, False) if raw_retryable is not None else False
            error_type = str(error.get("type") or "invalid_request_error").strip()

            # If inner message itself contains a serialized JSON error or terminal error prefix, resolve it
            if msg and ("{" in msg or "stream_terminal_error:" in msg):
                nested_meta = resolve_error_metadata(msg, default_message=default_message)
                if nested_meta:
                    merged_extra = dict(nested_meta.extra or {})
                    if extra:
                        merged_extra.update(extra)
                    return ErrorMetadata(
                        code=code if (code and code not in {"error", "workflow_failed", "arena_page_error"}) else nested_meta.code,
                        message=nested_meta.message,
                        status_code=status_code if raw_status_code is not None else nested_meta.status_code,
                        retryable=retryable if raw_retryable is not None else nested_meta.retryable,
                        error_type=error_type if error_type not in {"execution_error", "invalid_request_error"} else nested_meta.error_type,
                        param=param if param is not None else nested_meta.param,
                        extra=merged_extra or None,
                    )

            if code in _PROMPT_REJECTION_CODES:
                return ErrorMetadata(
                    code=code or ARENA_PROMPT_REJECTED_CODE,
                    message=msg or default_message or "Arena 拒绝了该提示词：内容违反 Terms of Use",
                    status_code=status_code,
                    retryable=retryable,
                    error_type=error_type,
                    param=param,
                    extra=extra or None,
                )
            if code in _KNOWN_NON_RETRYABLE_CODES:
                return ErrorMetadata(
                    code=code,
                    message=msg or default_message or "Arena 图片生成失败，响应不可重试",
                    status_code=status_code,
                    retryable=retryable,
                    error_type=error_type,
                    param=param,
                    extra=extra or None,
                )
            if (raw_retryable is not None and not retryable) or (raw_status_code is not None and status_code in {400, 401, 403, 422}):
                return ErrorMetadata(
                    code=code or "invalid_request_error",
                    message=msg or default_message or "请求不可重试",
                    status_code=status_code,
                    retryable=False,
                    error_type=error_type,
                    param=param,
                    extra=extra or None,
                )
            if msg or code:
                return ErrorMetadata(
                    code=code or "error",
                    message=msg or default_message or "请求处理失败",
                    status_code=status_code if raw_status_code is not None else 500,
                    retryable=retryable,
                    error_type=error_type,
                    param=param,
                    extra=extra or None,
                )
        return None

    # Handle string code / error message
    if isinstance(reason_or_payload, str):
        raw_str = reason_or_payload.strip()
        if not raw_str:
            return None
        if raw_str in _CANCEL_REASONS:
            return None

        # 剥离内部标识前缀（支持多层嵌套剥离）
        changed = True
        while changed:
            changed = False
            for prefix in ("stream_terminal_error:", "execution_error:", "执行错误:", "[错误]", "[error]"):
                if raw_str.lower().startswith(prefix.lower()):
                    raw_str = raw_str[len(prefix):].strip()
                    changed = True
                    break

        # 1. 尝试从文本中解析嵌套的 JSON 对象
        json_obj = _extract_json_dict_from_text(raw_str)
        if json_obj:
            nested_error = json_obj.get("error") if isinstance(json_obj.get("error"), dict) else json_obj
            if isinstance(nested_error, dict):
                extracted_msg = str(nested_error.get("message") or "").strip()
                extracted_code = str(nested_error.get("code") or "").strip()
                extracted_type = str(nested_error.get("type") or "").strip()
                extracted_param = nested_error.get("param")
                extra = {
                    k: v for k, v in nested_error.items()
                    if k not in {"message", "type", "code", "status_code", "retryable", "param"}
                }
                detected_status = _extract_status_code_from_text(raw_str) or _parse_int(nested_error.get("status_code"), 400)
                is_retryable = detected_status >= 500 and extracted_code not in {"moderation_blocked", "content_policy_violation"}
                
                return ErrorMetadata(
                    code=extracted_code or "upstream_error",
                    message=extracted_msg or raw_str or (default_message or "上游服务报错"),
                    status_code=detected_status or 400,
                    retryable=is_retryable,
                    error_type=extracted_type or "invalid_request_error",
                    param=str(extracted_param) if extracted_param is not None else None,
                    extra=extra or None,
                )

        # 2. 检查已知错误代码
        if raw_str in _PROMPT_REJECTION_CODES:
            return ErrorMetadata(
                code=raw_str,
                message=default_message or "Arena 拒绝了该提示词：内容违反 Terms of Use",
                status_code=422,
                retryable=False,
                error_type="invalid_request_error",
            )
        if raw_str in _KNOWN_NON_RETRYABLE_CODES:
            return ErrorMetadata(
                code=raw_str,
                message=default_message or "Arena 图片生成失败，响应不可重试",
                status_code=422,
                retryable=False,
                error_type="invalid_request_error",
            )

        # 3. 检查是否含有 HTTP 状态码
        status_code = _extract_status_code_from_text(raw_str)
        if status_code is not None:
            if status_code == 429:
                return ErrorMetadata(
                    code="rate_limit_exceeded",
                    message=raw_str,
                    status_code=429,
                    retryable=False,
                    error_type="rate_limit_error",
                )
            if status_code in {400, 422}:
                return ErrorMetadata(
                    code="bad_request" if status_code == 400 else "unprocessable_entity",
                    message=raw_str,
                    status_code=status_code,
                    retryable=False,
                    error_type="invalid_request_error",
                )
            if status_code in {401, 403}:
                return ErrorMetadata(
                    code="permission_denied",
                    message=raw_str,
                    status_code=status_code,
                    retryable=False,
                    error_type="permission_error",
                )
            if status_code >= 500:
                return ErrorMetadata(
                    code="upstream_service_error",
                    message=raw_str,
                    status_code=status_code,
                    retryable=True,
                    error_type="api_error",
                )

        # 4. 普通纯字符串
        return ErrorMetadata(
            code="workflow_failed",
            message=raw_str or (default_message or "请求处理失败"),
            status_code=500,
            retryable=False,
            error_type="execution_error",
        )

    return None


def is_non_retryable_error(reason_or_payload: Any) -> bool:
    """Check if a given reason or error payload represents a non-retryable 4xx error."""
    meta = resolve_error_metadata(reason_or_payload)
    return meta is not None and not meta.retryable and meta.status_code in {400, 401, 403, 422}


def build_error_response(
    metadata: ErrorMetadata,
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    """Build a standard JSONResponse from ErrorMetadata."""
    response_headers = dict(headers or {})
    if not metadata.retryable:
        response_headers["x-should-retry"] = "false"

    error_body: Dict[str, Any] = {
        "message": metadata.message,
        "type": metadata.error_type,
        "code": metadata.code,
        "status_code": metadata.status_code,
        "retryable": metadata.retryable,
    }
    if metadata.param is not None:
        error_body["param"] = metadata.param
    if metadata.extra:
        for k, v in metadata.extra.items():
            if k not in error_body:
                error_body[k] = v

    return JSONResponse(
        content={
            "error": error_body
        },
        status_code=metadata.status_code,
        headers=response_headers,
    )
