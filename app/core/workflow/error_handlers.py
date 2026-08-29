"""
app/core/workflow/error_handlers.py - 通用工作流错误与重试恢复处理器注册中心

职责：
- 管理工作流重试错误分类与分发；
- 将针对特定厂商或站点的页面恢复逻辑（如 Arena Direct Battle 重定向恢复、Arena 监控重试）解耦为独立 Handler；
- 支持动态注册自定义可重试错误与处理回调。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Generator, List, Optional, Set

from app.core.config import logger
from app.core.workflow.arena_direct_guard import (
    ARENA_DIRECT_UNEXPECTED_BATTLE_REDIRECT,
    ArenaDirectRecoveryError,
    recover_arena_direct_page,
)
from app.core.workflow.arena_send_watchdog import (
    ARENA_SEND_NO_TARGET_AFTER_RETRY,
    ArenaSendRetryRefreshError,
    ArenaSendWatchdogCancelled,
    refresh_arena_page_for_retry,
)

WorkflowRetryHandlerFunc = Callable[[Any, Any, Any], Optional[Generator[str, None, None]]]

_RETRY_HANDLERS: Dict[str, WorkflowRetryHandlerFunc] = {}
_RETRIABLE_ERROR_CODES: Set[str] = {
    "send_unconfirmed",
    "new_chat_transition_timeout",
    "arena_direct_unexpected_battle_redirect",
    "arena_send_no_target",
}


def register_retriable_error_code(error_code: str) -> None:
    """注册可重试的错误码。"""
    code = str(error_code or "").strip().lower()
    if code:
        _RETRIABLE_ERROR_CODES.add(code)


def is_retriable_workflow_error(error_code: str) -> bool:
    """判断错误码是否为已注册的可重试错误。"""
    code = str(error_code or "").strip().lower()
    return code in _RETRIABLE_ERROR_CODES


def register_workflow_retry_handler(
    error_code: str,
    handler: WorkflowRetryHandlerFunc,
) -> None:
    """注册指定错误码的重试恢复处理器。"""
    code = str(error_code or "").strip().lower()
    if not code:
        raise ValueError("error_code cannot be empty")
    if not callable(handler):
        raise ValueError(f"handler for {error_code} must be callable")
    _RETRY_HANDLERS[code] = handler
    register_retriable_error_code(code)
    logger.debug(f"[WORKFLOW_ERROR] 已注册重试处理器: {code}")


def get_workflow_retry_handler(error_code: str) -> Optional[WorkflowRetryHandlerFunc]:
    """获取指定错误码的重试恢复处理器。"""
    code = str(error_code or "").strip().lower()
    return _RETRY_HANDLERS.get(code)


def _handle_arena_direct_unexpected_battle_redirect(
    session: Any,
    should_stop: Any,
    formatter: Any,
) -> Optional[Generator[str, None, None]]:
    def _gen():
        try:
            recovery_url = str(
                getattr(session, "_arena_direct_recovery_url", "") or ""
            ).strip()
            recovery_input_selector = str(
                getattr(session, "_arena_direct_input_selector", "") or ""
            ).strip()
            recover_arena_direct_page(
                session.tab,
                target_url=recovery_url,
                input_selector=recovery_input_selector,
                should_stop=should_stop,
            )
            setattr(session, "_arena_direct_recovered_retry", True)
        except ArenaDirectRecoveryError as exc:
            logger.error(
                f"[{getattr(session, 'id', 'unknown')}] Arena direct recovery failed: {exc}"
            )
            yield formatter.pack_error(
                f"stream_terminal_error:{exc}",
                code="arena_direct_recovery_failed",
            )
            yield formatter.pack_finish()
        except Exception as exc:
            logger.error(
                f"[{getattr(session, 'id', 'unknown')}] Arena direct recovery unexpected error: {exc}"
            )
            yield formatter.pack_error(
                f"stream_terminal_error:{exc}",
                code="arena_direct_recovery_failed",
            )
            yield formatter.pack_finish()

    return _gen()


def _handle_arena_send_no_target(
    session: Any,
    should_stop: Any,
    formatter: Any,
) -> Optional[Generator[str, None, None]]:
    def _gen():
        try:
            input_selector = str(
                getattr(session, "_arena_watchdog_input_selector", "")
                or getattr(session, "_arena_direct_input_selector", "")
                or 'textarea, [contenteditable="true"]'
            ).strip()
            refresh_arena_page_for_retry(
                session.tab,
                input_selector=input_selector,
                should_stop=should_stop,
            )
        except ArenaSendWatchdogCancelled:
            return
        except ArenaSendRetryRefreshError as exc:
            logger.error(
                f"[{getattr(session, 'id', 'unknown')}] Arena watchdog retry refresh failed: {exc}"
            )
            yield formatter.pack_error(
                f"stream_terminal_error:{ARENA_SEND_NO_TARGET_AFTER_RETRY}",
                code=ARENA_SEND_NO_TARGET_AFTER_RETRY,
                status_code=422,
                retryable=False,
            )
            yield formatter.pack_finish()
        except Exception as exc:
            logger.error(
                f"[{getattr(session, 'id', 'unknown')}] Arena watchdog retry refresh unexpected error: {exc}"
            )
            yield formatter.pack_error(
                f"stream_terminal_error:{ARENA_SEND_NO_TARGET_AFTER_RETRY}",
                code=ARENA_SEND_NO_TARGET_AFTER_RETRY,
                status_code=422,
                retryable=False,
            )
            yield formatter.pack_finish()

    return _gen()


# 注册内置错误恢复处理器
register_workflow_retry_handler(
    ARENA_DIRECT_UNEXPECTED_BATTLE_REDIRECT,
    _handle_arena_direct_unexpected_battle_redirect,
)
register_workflow_retry_handler(
    "arena_send_no_target",
    _handle_arena_send_no_target,
)


__all__ = [
    "get_workflow_retry_handler",
    "is_retriable_workflow_error",
    "register_retriable_error_code",
    "register_workflow_retry_handler",
]
