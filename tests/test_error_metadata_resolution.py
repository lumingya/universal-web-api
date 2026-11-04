"""`resolve_error_metadata` 的特征测试。

该模块此前**没有任何专门测试**，而 `resolve_error_metadata`（229 行 /
CC 98）是错误响应的统一入口，被 app/api/chat.py、app/api/openai/tab.py
等多处调用。步骤 11 要重构它，因此先补上行为基线。

本文件记录的是**当前**行为，包括两处看起来可疑、但本次不做改动的地方
（改动会波及错误响应文案，属于行为变更，应单独评审）：

1. 带 `code` 属性的异常：走 `resolve_error_metadata(str(code), ...)`
   把 code 当字符串再解析一次，结果 code 串成了 message，而异常的
   `message` 属性被丢弃（见 test_exception_code_becomes_message）。
2. 剥掉 `stream_terminal_error:` 等前缀后，若剩余串不在已知码表里，
   就退化成 `workflow_failed` + 裸码作为 message
   （见 test_terminal_prefix_stripped_then_treated_as_plain_text）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from app.services.error_metadata import (
    _CANCEL_REASONS,
    _KNOWN_NON_RETRYABLE_CODES,
    _PROMPT_REJECTION_CODES,
    ErrorMetadata,
    resolve_error_metadata,
)


class _Ctx:
    """模拟带 cancel_reason 的 RequestContext。"""

    def __init__(self, reason: Optional[str]) -> None:
        self.cancel_reason = reason


class _CodedError(Exception):
    def __init__(self, code: Optional[str] = None, message: Optional[str] = None) -> None:
        super().__init__(message or "boom")
        self.code = code
        self.message = message


def _as_dict(meta: Optional[ErrorMetadata]) -> Optional[Dict[str, Any]]:
    if meta is None:
        return None
    return {
        "code": meta.code,
        "message": meta.message,
        "status_code": meta.status_code,
        "retryable": meta.retryable,
        "error_type": meta.error_type,
        "param": meta.param,
        "extra": meta.extra,
    }


# ---------------------------------------------------------------------------
# 不产生元数据
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, 42, 3.5, [1], {"a": 1}, object()])
def test_non_error_inputs_return_none(value: Any):
    assert resolve_error_metadata(value) is None


def test_empty_string_returns_none():
    assert resolve_error_metadata("") is None
    assert resolve_error_metadata("   ") is None


@pytest.mark.parametrize("reason", sorted(_CANCEL_REASONS))
def test_cancel_reasons_are_not_errors(reason: str):
    """取消原因不是错误——它们被显式排除，不应生成错误响应。"""
    assert resolve_error_metadata(reason) is None
    assert resolve_error_metadata(_Ctx(reason)) is None


def test_context_with_empty_cancel_reason_returns_none():
    assert resolve_error_metadata(_Ctx("")) is None
    assert resolve_error_metadata(_Ctx(None)) is None


def test_empty_dict_returns_none():
    assert resolve_error_metadata({}) is None


# ---------------------------------------------------------------------------
# 字符串：已知码表
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(_PROMPT_REJECTION_CODES))
def test_prompt_rejection_codes(code: str):
    meta = resolve_error_metadata(code)
    assert meta is not None
    assert meta.code == code
    assert meta.status_code == 422
    assert meta.retryable is False
    assert meta.error_type == "invalid_request_error"


def test_prompt_rejection_uses_default_message_when_given():
    meta = resolve_error_metadata("prompt_rejected", default_message="自定义文案")
    assert meta is not None
    assert meta.message == "自定义文案"


@pytest.mark.parametrize("code", sorted(_KNOWN_NON_RETRYABLE_CODES))
def test_known_non_retryable_codes(code: str):
    meta = resolve_error_metadata(code)
    assert meta is not None
    assert meta.code == code
    assert meta.status_code == 422
    assert meta.retryable is False


# ---------------------------------------------------------------------------
# 字符串：HTTP 状态码
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, code, status, error_type, retryable",
    [
        ("HTTP 429 too many", "rate_limit_exceeded", 429, "rate_limit_error", False),
        ("HTTP 400 bad", "bad_request", 400, "invalid_request_error", False),
        ("HTTP 422 nope", "unprocessable_entity", 422, "invalid_request_error", False),
        ("HTTP 401 nope", "permission_denied", 401, "permission_error", False),
        ("HTTP 403 nope", "permission_denied", 403, "permission_error", False),
        ("HTTP 500 oops", "upstream_service_error", 500, "api_error", True),
        ("HTTP 503 oops", "upstream_service_error", 503, "api_error", True),
    ],
)
def test_status_codes_in_text(text: str, code: str, status: int, error_type: str, retryable: bool):
    meta = resolve_error_metadata(text)
    assert meta is not None
    assert (meta.code, meta.status_code, meta.error_type, meta.retryable) == (
        code,
        status,
        error_type,
        retryable,
    )


def test_five_hundred_is_retryable_but_four_twenty_nine_is_not():
    """429 与 5xx 都被识别，但可重试性相反——这是有意的区分。"""
    assert resolve_error_metadata("429 Too Many Requests").retryable is False
    assert resolve_error_metadata("500 Internal Server Error").retryable is True


def test_status_code_needs_contextual_prefix():
    """裸数字不算状态码：_extract_status_code_from_text 要求 http /
    status / code 之类的上下文，或"数字 + 标准描述词"的组合。

    这是有意的——错误信息里出现的任意 3 位数字不应被误判成 HTTP 状态码。
    """
    assert resolve_error_metadata("500").code == "workflow_failed"
    assert resolve_error_metadata("429").code == "workflow_failed"
    # 加上上下文后即可识别
    assert resolve_error_metadata("HTTP 500").code == "upstream_service_error"
    assert resolve_error_metadata("status code: 429").code == "rate_limit_exceeded"


# ---------------------------------------------------------------------------
# 字符串：前缀剥离与嵌套 JSON
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefixed, stripped",
    [
        ("stream_terminal_error:some_code", "some_code"),
        ("execution_error:bad", "bad"),
        ("执行错误:坏了", "坏了"),
    ],
)
def test_internal_prefixes_are_stripped(prefixed: str, stripped: str):
    meta = resolve_error_metadata(prefixed)
    assert meta is not None
    assert meta.message == stripped


def test_prefix_stripping_is_repeated():
    assert resolve_error_metadata("stream_terminal_error:execution_error:deep").message == "deep"


def test_terminal_prefix_stripped_then_treated_as_plain_text():
    """当前行为：剥掉前缀后若不在已知码表里，退化为 workflow_failed，
    裸码成为 message。这里记录现状，供后续单独评审行为变更。"""
    meta = resolve_error_metadata("stream_terminal_error:arena_direct_unexpected_battle_redirect")
    assert _as_dict(meta) == {
        "code": "workflow_failed",
        "message": "arena_direct_unexpected_battle_redirect",
        "status_code": 500,
        "retryable": False,
        "error_type": "execution_error",
        "param": None,
        "extra": None,
    }


def test_nested_json_error_is_unpacked():
    meta = resolve_error_metadata('{"error": {"message": "inner", "code": "inner_code", "type": "api_error"}}')
    assert meta is not None
    assert meta.code == "inner_code"
    assert meta.message == "inner"
    assert meta.error_type == "api_error"


def test_plain_string_falls_back_to_workflow_failed():
    meta = resolve_error_metadata("something went wrong")
    assert _as_dict(meta) == {
        "code": "workflow_failed",
        "message": "something went wrong",
        "status_code": 500,
        "retryable": False,
        "error_type": "execution_error",
        "param": None,
        "extra": None,
    }


def test_default_message_used_when_string_is_blank_after_stripping():
    meta = resolve_error_metadata("   ", default_message="兜底")
    assert meta is None


# ---------------------------------------------------------------------------
# 异常对象
# ---------------------------------------------------------------------------


def test_plain_exception_message_becomes_message():
    meta = resolve_error_metadata(Exception("plain boom"))
    assert meta is not None
    assert meta.message == "plain boom"
    assert meta.code == "workflow_failed"


def test_exception_code_becomes_message():
    """当前行为（疑似缺陷，记录不改）：异常的 code 属性被当作字符串
    重新解析，于是成了 message，而 message 属性被丢弃。"""
    meta = resolve_error_metadata(_CodedError(code="nsfw_rejected", message="rejected"))
    assert meta is not None
    assert meta.message == "nsfw_rejected"
    assert meta.code == "workflow_failed"


def test_exception_without_code_uses_str():
    meta = resolve_error_metadata(Exception("boom"), default_message="兜底")
    assert meta is not None
    assert meta.message == "boom"


# ---------------------------------------------------------------------------
# 上下文字典
# ---------------------------------------------------------------------------


def test_dict_with_error_object():
    meta = resolve_error_metadata({"error": {"code": "x", "message": "m"}})
    assert meta is not None
    assert (meta.code, meta.message) == ("x", "m")
    assert meta.status_code == 500, "未提供 status_code 时默认 500"


def test_dict_status_code_is_honoured():
    meta = resolve_error_metadata({"error": {"code": "x", "message": "m", "status_code": 429}})
    assert meta is not None
    assert meta.status_code == 429


def test_dict_extra_fields_are_collected():
    meta = resolve_error_metadata({"error": {"code": "x", "message": "m", "detail": "d"}})
    assert meta is not None
    assert meta.extra == {"detail": "d"}


def test_bare_dict_is_treated_as_the_error_itself():
    """没有 "error" 键时，整个 dict 被当作 error 对象。"""
    meta = resolve_error_metadata({"code": "c", "message": "m"})
    assert meta is not None
    assert (meta.code, meta.message) == ("c", "m")


# ---------------------------------------------------------------------------
# RequestContext 对象
# ---------------------------------------------------------------------------


def test_context_prompt_rejection():
    meta = resolve_error_metadata(_Ctx("prompt_rejected"))
    assert meta is not None
    assert meta.code == "prompt_rejected"
    assert meta.status_code == 422


def test_context_unknown_reason_is_recursed():
    """未知取消原因会递归按字符串再解析一次。"""
    meta = resolve_error_metadata(_Ctx("some_reason"))
    assert meta is not None
    assert meta.message == "some_reason"
    assert meta.code == "workflow_failed"


# ---------------------------------------------------------------------------
# 码表自身
# ---------------------------------------------------------------------------


def test_prompt_rejection_codes_are_a_subset_of_non_retryable():
    assert _PROMPT_REJECTION_CODES <= _KNOWN_NON_RETRYABLE_CODES


def test_known_code_sets_are_non_empty():
    assert _CANCEL_REASONS and _PROMPT_REJECTION_CODES and _KNOWN_NON_RETRYABLE_CODES
