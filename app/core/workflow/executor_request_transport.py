"""
Request transport mixin for WorkflowExecutor.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.config import logger, WorkflowError
from app.core.request_transport import (
    REQUEST_TRANSPORT_MODE_PAGE_FETCH,
    execute_request_transport,
    get_default_request_transport_config,
)


class WorkflowExecutorRequestTransportMixin:
    def _get_request_transport_mode(self) -> str:
        return str(self._request_transport.get("mode") or get_default_request_transport_config().get("mode") or "workflow").strip().lower()

    def _get_request_transport_profile(self) -> str:
        return str(self._request_transport.get("profile") or "").strip()

    def _get_request_transport_options(self) -> Dict[str, Any]:
        options = self._request_transport.get("options") or {}
        return dict(options) if isinstance(options, dict) else {}

    def _get_request_transport_fallback_mode(self) -> str:
        fallback_mode = str(self._get_request_transport_options().get("fallback_mode") or "workflow").strip().lower()
        return fallback_mode if fallback_mode in {"workflow", "error"} else "workflow"

    def _request_transport_enabled(self) -> bool:
        return (
            not self._request_transport_bypass
            and self._get_request_transport_mode() == REQUEST_TRANSPORT_MODE_PAGE_FETCH
            and bool(self._get_request_transport_profile())
            and self._stream_mode == "network"
            and self._network_monitor is not None
        )

    def _context_has_non_text_inputs(self, prompt: str = "") -> bool:
        context = getattr(self, "_context", None) or {}
        if bool(context.get("images")):
            return True
        try:
            if prompt and self._text_handler._should_use_file_paste(prompt):
                return True
        except Exception:
            pass
        return False

    def _can_stage_request_transport(self, prompt: str = "") -> bool:
        effective_prompt = str(prompt or "").strip()
        if not effective_prompt:
            return False
        if not self._request_transport_enabled():
            return False
        if self._context_has_non_text_inputs(effective_prompt):
            return False
        return True

    def _queue_request_transport_prompt(
        self,
        *,
        selector: str,
        target_key: str,
        optional: bool,
        prompt: str,
    ) -> None:
        self._pending_request_transport_state = {
            "selector": str(selector or ""),
            "target_key": str(target_key or ""),
            "optional": bool(optional),
            "prompt": str(prompt or ""),
        }

    def _has_pending_request_transport_prompt(self) -> bool:
        return bool(
            isinstance(self._pending_request_transport_state, dict)
            and str(self._pending_request_transport_state.get("prompt") or "").strip()
        )

    def _clear_request_transport_state(self) -> None:
        self._pending_request_transport_state = None

    def consume_last_request_transport_sent(self) -> bool:
        sent = bool(self._last_request_transport_sent)
        self._last_request_transport_sent = False
        return sent

    def _reset_request_transport_monitor_if_needed(self) -> None:
        if self._network_monitor is None:
            return
        try:
            self._network_monitor._cleanup()
        except Exception as e:
            logger.debug(f"[REQUEST_TRANSPORT] 清理网络监听失败（忽略）: {e}")

    def _ensure_cached_prompt_filled(self) -> None:
        pending = self._pending_request_transport_state or {}
        prompt = str(pending.get("prompt") or "").strip()
        selector = str(pending.get("selector") or "")
        target_key = str(pending.get("target_key") or "")
        optional = bool(pending.get("optional", False))
        if not prompt:
            return

        self._request_transport_bypass = True
        try:
            self._execute_fill(selector, prompt, target_key, optional)
        finally:
            self._request_transport_bypass = False
            self._clear_request_transport_state()

    def _stage_request_transport_from_context(
        self,
        *,
        selector: str = "",
        target_key: str = "",
        optional: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        prompt = str((context or {}).get("prompt") or "").strip()
        if not self._can_stage_request_transport(prompt):
            return False
        self._queue_request_transport_prompt(
            selector=selector,
            target_key=target_key,
            optional=optional,
            prompt=prompt,
        )
        logger.debug(
            f"[REQUEST_TRANSPORT] 已缓存页面直发 prompt "
            f"(profile={self._get_request_transport_profile()}, chars={len(prompt)})"
        )
        return True

    def _consume_request_transport_followup_steps(self, workflow: list, current_index: int) -> int:
        if current_index < 0 or current_index >= len(workflow):
            return current_index
        if str(workflow[current_index].get("action") or "").strip() != "PAGE_FETCH":
            return current_index

        next_index = current_index + 1
        consumed = 0
        while next_index < len(workflow):
            next_step = workflow[next_index] if isinstance(workflow[next_index], dict) else {}
            next_action = str(next_step.get("action") or "").strip()
            next_target = str(next_step.get("target") or "").strip()
            if next_action in {"FILL_INPUT", "KEY_PRESS"}:
                consumed += 1
                next_index += 1
                continue
            if next_action == "CLICK" and next_target == "send_btn":
                consumed += 1
                next_index += 1
                continue
            if next_action == "WAIT":
                lookahead = next_index + 1
                while lookahead < len(workflow):
                    lookahead_step = workflow[lookahead] if isinstance(workflow[lookahead], dict) else {}
                    lookahead_action = str(lookahead_step.get("action") or "").strip()
                    lookahead_target = str(lookahead_step.get("target") or "").strip()
                    if lookahead_action == "WAIT":
                        lookahead += 1
                        continue
                    break
                if (
                    lookahead < len(workflow)
                    and (
                        lookahead_action in {"FILL_INPUT", "KEY_PRESS"}
                        or (lookahead_action == "CLICK" and lookahead_target == "send_btn")
                    )
                ):
                    consumed += 1
                    next_index += 1
                    continue
            break

        if consumed > 0:
            logger.debug(
                f"[REQUEST_TRANSPORT] 页面直发成功后跳过 {consumed} 个回退步骤"
            )
        return next_index - 1

    def _attempt_request_transport_send(self) -> bool:
        if not self._has_pending_request_transport_prompt():
            return False
        if not self._request_transport_enabled():
            return False

        pending = self._pending_request_transport_state or {}
        prompt = str(pending.get("prompt") or "").strip()
        if not prompt:
            return False

        result = execute_request_transport(
            self.tab,
            self._request_transport,
            prompt=prompt,
            consume_response=False,
        )
        if result.get("ok"):
            logger.info(
                "[REQUEST_TRANSPORT] 页面直发已触发 "
                f"(profile={self._get_request_transport_profile()}, status={result.get('status')}, "
                f"session_id={result.get('session_id') or '-'})"
            )
            self._clear_request_transport_state()
            self._last_request_transport_sent = True
            return True

        fallback_mode = self._get_request_transport_fallback_mode()
        logger.warning(
            "[REQUEST_TRANSPORT] 页面直发失败: "
            f"profile={self._get_request_transport_profile()}, "
            f"error={result.get('error')}, status={result.get('status')}, "
            f"fallback={fallback_mode}"
        )
        if fallback_mode == "workflow":
            self._reset_request_transport_monitor_if_needed()
            return False

        self._clear_request_transport_state()
        raise WorkflowError(
            f"request_transport_failed:{result.get('error') or result.get('status') or 'unknown'}"
        )
