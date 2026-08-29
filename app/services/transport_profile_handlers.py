"""
app/services/transport_profile_handlers.py - 命令引擎 HTTP Request Transport Profile 处理器注册中心与实现

职责：
- 定义 TransportProfileHandler 规范与注册表；
- 将针对特定厂商协议/接口的页面直发请求（如 deepseek_completion）独立为专属 Handler；
- 动态通过 ParserRegistry 获取响应解析器，消除 Action 执行器对特定 Parser 的写死依赖。
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Dict, Optional, Protocol

from app.core.parsers import ParserRegistry
from app.core.request_transport import (
    execute_request_transport,
    get_default_request_transport_config,
)

logger = logging.getLogger(__name__)


class TransportProfileHandler(Protocol):
    def __call__(
        self,
        action: Dict[str, Any],
        session: Any,
        ctx: Dict[str, Any],
        action_executor: Any,
    ) -> Any:
        ...


_TRANSPORT_PROFILE_HANDLERS: Dict[str, Callable[..., Any]] = {}


def register_transport_profile_handler(
    profile_id: str,
    handler: Callable[..., Any],
) -> None:
    """注册页面请求 Transport Profile 处理器。"""
    pid = str(profile_id or "").strip().lower()
    if not pid:
        raise ValueError("profile_id cannot be empty")
    if not callable(handler):
        raise ValueError(f"handler for {profile_id} must be callable")
    _TRANSPORT_PROFILE_HANDLERS[pid] = handler
    logger.debug(f"[TRANSPORT_HANDLER] 已注册 Profile Handler: {pid}")


def get_transport_profile_handler(profile_id: str) -> Optional[Callable[..., Any]]:
    """获取指定 profile_id 的 Handler。"""
    pid = str(profile_id or "").strip().lower()
    return _TRANSPORT_PROFILE_HANDLERS.get(pid)


class DeepSeekCompletionTransportHandler:
    """DeepSeek Chat Completion 页面直发请求处理器。"""

    def __call__(
        self,
        action: Dict[str, Any],
        session: Any,
        ctx: Dict[str, Any],
        action_executor: Any,
    ) -> Any:
        prompt = action_executor._render_template(
            action.get("prompt", action.get("body", "")), ctx
        ).strip()
        if not prompt:
            return {"ok": False, "error": "empty_prompt"}

        response_mode = str(action.get("response_mode", "text") or "text").strip().lower()
        consume_response = action_executor._coerce_action_bool(action.get("consume_response"), False)
        transport_defaults = get_default_request_transport_config()
        transport_options = {
            **(transport_defaults.get("options") or {}),
            "model_type": action_executor._render_template(action.get("model_type", ""), ctx).strip() or "auto",
            "context_mode": "full_prompt",
            "search_enabled": action_executor._render_template(str(action.get("search_enabled", "auto") or "auto"), ctx).strip() or "auto",
            "thinking_enabled": action_executor._render_template(str(action.get("thinking_enabled", "auto") or "auto"), ctx).strip() or "auto",
            "fallback_mode": "workflow",
            "client_version": action_executor._render_template(str(action.get("client_version", "2.0.0") or "2.0.0"), ctx).strip() or "2.0.0",
            "app_version": action_executor._render_template(
                str(action.get("app_version", action.get("client_version", "2.0.0")) or action.get("client_version", "2.0.0")),
                ctx,
            ).strip() or action_executor._render_template(str(action.get("client_version", "2.0.0") or "2.0.0"), ctx).strip() or "2.0.0",
        }
        transport_config = {
            "mode": "page_fetch",
            "profile": "deepseek_completion",
            "options": transport_options,
        }

        result = execute_request_transport(
            session.tab,
            transport_config,
            prompt=prompt,
            consume_response=consume_response,
        )

        if not isinstance(result, dict):
            logger.warning(f"[CMD] DeepSeek 直发返回格式异常: {type(result).__name__}")
            return {"ok": False, "error": "invalid_result_type"}

        if not result.get("ok"):
            logger.warning(
                "[CMD] DeepSeek 直发失败: "
                f"status={result.get('status')}, error={result.get('error')}, "
                f"preview={action_executor._preview_text(result.get('responsePreview') or result.get('raw_text') or '')!r}"
            )
            return {
                "ok": False,
                "error": str(result.get("error") or "deepseek_completion_failed"),
                "status": result.get("status"),
                "response": result,
            }

        raw_text = str(result.get("raw_text", "") or "")
        content_type = str(result.get("content_type", "") or "")
        parsed_content = raw_text
        parse_error = ""

        if raw_text and "text/event-stream" in content_type.lower():
            try:
                parser = ParserRegistry.get("deepseek")
                parsed = parser.parse_chunk(raw_text)
                parsed_content = str(parsed.get("content", "") or "")
                parse_error = str(parsed.get("error", "") or "")
                if not parsed_content and not parse_error:
                    parsed_content = raw_text
            except Exception as e:
                parse_error = str(e)
                parsed_content = raw_text

        response_payload = {
            "ok": True,
            "status": result.get("status"),
            "url": result.get("url") or "/api/v0/chat/completion",
            "content_type": content_type,
            "session_id": result.get("session_id") or "",
            "model_type": result.get("model_type") or "",
            "body": parsed_content,
            "raw_text": raw_text,
        }
        if parse_error:
            response_payload["parse_error"] = parse_error

        saved_as = action_executor._save_generated_value(
            session,
            action.get("save_as"),
            parsed_content,
            extras={
                "session_id": response_payload["session_id"],
                "model_type": response_payload["model_type"],
                "status": response_payload["status"],
            },
        )

        logger.info(
            f"[CMD] DeepSeek 页面直发{'完成' if consume_response else '已触发'}: "
            f"status={response_payload['status']}, session_id={response_payload['session_id'] or '-'}, "
            f"save_as={saved_as or '-'}, preview={action_executor._preview_text(parsed_content)!r}"
        )

        if response_mode == "status":
            return {
                "ok": True,
                "status": response_payload["status"],
                "url": response_payload["url"],
                "session_id": response_payload["session_id"],
                "model_type": response_payload["model_type"],
            }

        if response_mode == "response":
            return response_payload

        if response_mode == "json":
            return {
                "content": parsed_content,
                "session_id": response_payload["session_id"],
                "model_type": response_payload["model_type"],
                "status": response_payload["status"],
                "raw_text": raw_text,
            }

        if response_mode == "raw":
            return raw_text

        return parsed_content


# 注册内置 Handler
register_transport_profile_handler("deepseek_completion", DeepSeekCompletionTransportHandler())

__all__ = [
    "DeepSeekCompletionTransportHandler",
    "TransportProfileHandler",
    "get_transport_profile_handler",
    "register_transport_profile_handler",
]
