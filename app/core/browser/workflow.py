# app/core/browser/workflow.py

import json
import time
import contextlib
import random
import re
from typing import Optional, List, Dict, Any, Generator, Callable, TYPE_CHECKING

from app.core.config import (
    logger,
    BrowserConstants,
    ElementNotFoundError,
    WorkflowError,
    MessageValidator,
)
from app.utils.site_url import extract_remote_site_domain, tab_url_matches
from app.utils.image_handler import extract_images_from_messages
from app.core.workflow import WorkflowExecutor
from app.core.tab_pool import TabSession
from app.models.schemas import get_modality_run_policy, is_modality_enabled

if TYPE_CHECKING:
    from .main import BrowserCore


class BrowserWorkflowMixin:
    """工作流执行、流式/非流式响应、中断处理相关的混入类"""

    @staticmethod
    def _format_log_counts(counts: Dict[str, int]) -> str:
        if not counts:
            return "-"
        return ",".join(f"{key}:{counts[key]}" for key in sorted(counts))

    @staticmethod
    def _compact_log_value(value: Any, max_len: int = 96) -> str:
        text = str(value or "").replace("\r", "\\r").replace("\n", "\\n").strip()
        if not text:
            return "-"
        if len(text) > max_len:
            return f"{text[:max(0, max_len - 3)]}..."
        return text

    @staticmethod
    def _message_declares_image(message: Dict[str, Any]) -> bool:
        if not isinstance(message, dict):
            return False

        content = message.get("content")
        if isinstance(content, str):
            stripped = content.strip()
            if "image_url" not in stripped and "data:image" not in stripped:
                return False
            if stripped.startswith(("[", "{")):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        parsed = [parsed]
                    if isinstance(parsed, list):
                        return BrowserWorkflowMixin._content_parts_declare_image(parsed)
                except Exception:
                    pass
            return "base64," in stripped or "http://" in stripped or "https://" in stripped

        return BrowserWorkflowMixin._content_parts_declare_image(content)

    @staticmethod
    def _content_parts_declare_image(content: Any) -> bool:
        if not isinstance(content, (list, tuple)):
            return False

        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "") or "").strip().lower() != "image_url":
                continue
            image_url = item.get("image_url") or {}
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            if str(url or "").strip():
                return True
        return False

    @staticmethod
    def _is_tool_output_format_reminder(message: Dict[str, Any]) -> bool:
        if not isinstance(message, dict):
            return False
        content = message.get("content")
        if not isinstance(content, str):
            return False
        return "[Tool Output Format Reminder]" in content

    @staticmethod
    def _select_image_source_messages(messages: List[Dict], upload_history: bool) -> List[Dict]:
        """选择本轮图片提取来源；关闭历史上传时保留最近一条带图用户消息。"""
        if upload_history:
            return messages or []

        last_user = None
        last_user_with_image = None
        for message in reversed(messages or []):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            if last_user is None:
                last_user = message
            if BrowserWorkflowMixin._message_declares_image(message):
                last_user_with_image = message
                break

        selected = last_user_with_image or last_user
        return [selected] if selected else []

    @staticmethod
    def _emit_request_block(emitted_blocks: set[int], block_no: int, title: str, detail: str = "") -> None:
        if block_no in emitted_blocks:
            return
        emitted_blocks.add(block_no)
        detail_text = f" | {detail}" if detail else ""
        logger.debug(f"[请求块 {block_no}/4] ---------- {title}{detail_text} ----------")

    def set_stop_checker(self, checker: Callable[[], bool]):
        """设置停止检查器"""
        self._should_stop_checker = checker or (lambda: False)

    def _get_request_state_snapshot(self, task_id: str = "") -> Dict[str, Any]:
        task = str(task_id or "").strip()
        snapshot = {
            "exists": False,
            "status": "",
            "cancel_reason": "",
            "terminal": False,
        }
        if not task:
            return snapshot

        try:
            from app.services.request_manager import request_manager
            ctx = request_manager.get_request(task)
        except Exception:
            return snapshot

        if ctx is None:
            return snapshot

        snapshot["exists"] = True
        try:
            status = getattr(getattr(ctx, "status", None), "value", "")
            snapshot["status"] = str(status or "").strip().lower()
        except Exception:
            snapshot["status"] = ""
        try:
            snapshot["cancel_reason"] = str(getattr(ctx, "cancel_reason", "") or "").strip().lower()
        except Exception:
            snapshot["cancel_reason"] = ""
        try:
            snapshot["terminal"] = bool(ctx.is_terminal())
        except Exception:
            snapshot["terminal"] = snapshot["status"] in {"completed", "cancelled", "failed"}
        return snapshot

    def _get_request_cancel_reason(self, task_id: str = "") -> str:
        return str(self._get_request_state_snapshot(task_id).get("cancel_reason", "") or "").strip().lower()

    def _should_rollback_request_count_on_cancel(self, task_id: str = "") -> bool:
        reason = self._get_request_cancel_reason(task_id)
        if not reason:
            return False
        manual_reasons = {
            "manual",
            "manual_terminate",
            "user_cancel",
            "user_cancelled",
            "cancel_button",
        }
        return reason in manual_reasons

    def _build_task_ownership_stop_checker(
        self,
        session: Optional[TabSession],
        task_id: str,
        base_checker: Optional[Callable[[], bool]] = None,
    ) -> Callable[[], bool]:
        expected_task_id = str(task_id or "").strip()
        base = base_checker or self._should_stop_checker
        ownership_lost_logged = False

        def _checker() -> bool:
            nonlocal ownership_lost_logged
            if base():
                return True
            if not session or not expected_task_id:
                return False

            try:
                current_task_id = str(getattr(session, "current_task_id", "") or "").strip()
                session_status = getattr(getattr(session, "status", None), "value", "")
            except Exception:
                current_task_id = ""
                session_status = ""

            ownership_lost = False
            detail = ""
            if current_task_id and current_task_id != expected_task_id:
                ownership_lost = True
                detail = f"current_task={current_task_id}"
            elif session_status in {"error", "closed"}:
                ownership_lost = True
                detail = f"status={session_status}"
            elif not current_task_id:
                ownership_lost = True
                detail = f"missing_task_id,status={session_status or 'unknown'}"

            if ownership_lost:
                if not ownership_lost_logged:
                    self._cancel_request_due_to_ownership_loss(
                        expected_task_id,
                        session,
                        detail=detail,
                    )
                    logger.warning(
                        f"[{session.id}] 检测到工作流所有权丢失，停止当前任务 "
                        f"(expected_task={expected_task_id}, {detail})"
                    )
                    ownership_lost_logged = True
                return True

            return False

        return _checker

    def _cancel_request_due_to_ownership_loss(
        self,
        task_id: str,
        session: Optional[TabSession],
        detail: str = "",
    ) -> None:
        request_id = str(task_id or "").strip()
        if not request_id:
            return

        request_state = self._get_request_state_snapshot(request_id)
        if request_state.get("terminal"):
            logger.debug(
                f"[{getattr(session, 'id', '-')}] 请求已结束，跳过所有权丢失取消: "
                f"request={request_id}, detail={detail or '-'}, "
                f"request_status={request_state.get('status') or '-'}, "
                f"request_reason={request_state.get('cancel_reason') or '-'}"
            )
            return

        if session is not None:
            try:
                current_task_id = str(getattr(session, "current_task_id", "") or "").strip()
                if not current_task_id or current_task_id == request_id:
                    setattr(session, "_workflow_stop_reason", "ownership_lost")
            except Exception:
                pass

        try:
            from app.services.request_manager import request_manager
            cancelled = bool(request_manager.cancel_request(request_id, "task_ownership_lost"))
            if cancelled:
                logger.warning(
                    f"[{getattr(session, 'id', '-')}] 所有权丢失后取消请求: "
                    f"request={request_id}, cancelled={cancelled}, detail={detail or '-'}, "
                    f"current_task={str(getattr(session, 'current_task_id', '') or '').strip() or '-'}, "
                    f"bound_req={str(getattr(session, '_bound_request_id', '') or '').strip() or '-'}, "
                    f"status={getattr(getattr(session, 'status', None), 'value', '') or '-'}"
                )
                return

            refreshed_state = self._get_request_state_snapshot(request_id)
            if refreshed_state.get("terminal"):
                logger.debug(
                    f"[{getattr(session, 'id', '-')}] 所有权丢失时请求已结束，忽略重复取消: "
                    f"request={request_id}, detail={detail or '-'}, "
                    f"request_status={refreshed_state.get('status') or '-'}, "
                    f"request_reason={refreshed_state.get('cancel_reason') or '-'}"
                )
            else:
                logger.warning(
                    f"[{getattr(session, 'id', '-')}] 所有权丢失后取消请求: "
                    f"request={request_id}, cancelled={cancelled}, detail={detail or '-'}, "
                    f"current_task={str(getattr(session, 'current_task_id', '') or '').strip() or '-'}, "
                    f"bound_req={str(getattr(session, '_bound_request_id', '') or '').strip() or '-'}, "
                    f"status={getattr(getattr(session, 'status', None), 'value', '') or '-'}"
                )
        except Exception as e:
            logger.debug(
                f"[{getattr(session, 'id', '-')}] 所有权丢失后取消请求失败（忽略）: {e}"
                + (f" ({detail})" if detail else "")
            )

    def _release_workflow_session(
        self,
        session: TabSession,
        *,
        effective_stop_checker: Optional[Callable[[], bool]] = None,
        task_id: str = "",
    ):
        expected_task_id = str(task_id or "").strip()
        current_task_id = str(getattr(session, "current_task_id", "") or "").strip()
        session_status = getattr(getattr(session, "status", None), "value", "")
        request_state = self._get_request_state_snapshot(expected_task_id) if expected_task_id else {}
        if session_status in {"error", "closed"}:
            logger.warning(
                f"[{session.id}] 跳过释放：标签页已处于不可复用状态 "
                f"(expected_task={expected_task_id or '-'}, status={session_status})"
            )
            return
        if expected_task_id:
            if current_task_id and current_task_id != expected_task_id:
                if request_state.get("terminal"):
                    logger.debug(
                        f"[{session.id}] 请求已结束，跳过迟到的释放收尾 "
                        f"(expected_task={expected_task_id}, current_task={current_task_id}, "
                        f"request_status={request_state.get('status') or '-'}, "
                        f"request_reason={request_state.get('cancel_reason') or '-'})"
                    )
                    return
                self._cancel_request_due_to_ownership_loss(
                    expected_task_id,
                    session,
                    detail=f"current_task={current_task_id}",
                )
                logger.warning(
                    f"[{session.id}] 跳过释放：标签页已被其他任务接管 "
                    f"(expected_task={expected_task_id}, current_task={current_task_id})"
                )
                return
            if not current_task_id:
                if request_state.get("terminal"):
                    logger.debug(
                        f"[{session.id}] 请求已结束，跳过迟到的释放收尾 "
                        f"(expected_task={expected_task_id}, status={session_status or 'unknown'}, "
                        f"request_status={request_state.get('status') or '-'}, "
                        f"request_reason={request_state.get('cancel_reason') or '-'})"
                    )
                    return
                self._cancel_request_due_to_ownership_loss(
                    expected_task_id,
                    session,
                    detail=f"missing_task_id,status={session_status or 'unknown'}",
                )
                logger.warning(
                    f"[{session.id}] 跳过释放：标签页 task_id 已丢失 "
                    f"(expected_task={expected_task_id}, status={session_status or 'unknown'})"
                )
                return

        cancelled = bool(effective_stop_checker and effective_stop_checker())
        rollback_request_count = cancelled and self._should_rollback_request_count_on_cancel(task_id)
        if cancelled and not rollback_request_count:
            logger.debug(
                f"[{session.id}] stop detected but request_count preserved "
                f"(task={task_id or '-'}, reason={self._get_request_cancel_reason(task_id) or 'unknown'})"
            )

        logger.debug(
            f"[{session.id}] 工作流释放请求: expected_task={expected_task_id or '-'}, "
            f"current_task={current_task_id or '-'}, session_status={session_status or '-'}, "
            f"cancelled={cancelled}, rollback_request_count={rollback_request_count}, "
            f"bound_req={str(getattr(session, '_bound_request_id', '') or '').strip() or '-'}"
        )

        self.tab_pool.release(
            session.id,
            check_triggers=not rollback_request_count,
            rollback_request_count=rollback_request_count,
            expected_task_id=expected_task_id,
        )

    def execute_workflow(
        self, 
        messages: List[Dict],
        stream: bool = True,
        task_id: str = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
        allow_media_postprocess: bool = True,
    ) -> Generator[str, None, None]:
        """
        工作流执行入口（v2.0 改进版）
        
        改动：
        - 自动从池中获取标签页
        - 执行完自动释放
        """
        is_valid, error_msg, sanitized_messages = MessageValidator.validate(messages)
        
        if not is_valid:
            yield self.formatter.pack_error(
                f"无效请求: {error_msg}",
                error_type="invalid_request_error",
                code="invalid_messages"
            )
            return
        
        if task_id is None:
            task_id = f"task_{int(time.time() * 1000)}"
        effective_stop_checker = stop_checker or self._should_stop_checker
        
        session = None
        try:
            session = self.tab_pool.acquire(task_id, timeout=60)
            
            if session is None:
                yield self.formatter.pack_error(
                    "服务繁忙，请稍后重试",
                    error_type="capacity_error",
                    code="no_available_tab"
                )
                yield self.formatter.pack_finish()
                return

            self._bind_request_tab_id(task_id, session)
            effective_stop_checker = self._build_task_ownership_stop_checker(
                session,
                task_id,
                effective_stop_checker,
            )
             
            if stream:
                yield from self._execute_workflow_stream(
                    session,
                    sanitized_messages,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                    allow_media_postprocess=allow_media_postprocess,
                )
            else:
                yield from self._execute_workflow_non_stream(
                    session,
                    sanitized_messages,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                    allow_media_postprocess=allow_media_postprocess,
                )
        
        finally:
            if session:
                self._release_workflow_session(
                    session,
                    effective_stop_checker=effective_stop_checker,
                    task_id=task_id,
                )
                try:
                    from app.services.command_engine import command_engine
                    command_engine.schedule_deferred_workflow_commands(session, delay_sec=0.25)
                except Exception:
                    pass

    def execute_workflow_for_tab_index(
        self, 
        tab_index: int,
        messages: List[Dict],
        stream: bool = True,
        task_id: str = None,
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
        allow_media_postprocess: bool = True,
    ) -> Generator[str, None, None]:
        """使用指定编号的标签页执行工作流"""
        is_valid, error_msg, sanitized_messages = MessageValidator.validate(messages)
        
        if not is_valid:
            yield self.formatter.pack_error(
                f"无效请求: {error_msg}",
                error_type="invalid_request_error",
                code="invalid_messages"
            )
            return
        
        if task_id is None:
            task_id = f"tab{tab_index}_{int(time.time() * 1000)}"
        effective_stop_checker = stop_checker or self._should_stop_checker
        
        session = None
        try:
            session = self.tab_pool.acquire_by_index(tab_index, task_id, timeout=60)
            
            if session is None:
                yield self.formatter.pack_error(
                    f"标签页 #{tab_index} 不可用或不存在",
                    error_type="not_found_error",
                    code="tab_not_found"
                )
                yield self.formatter.pack_finish()
                return

            self._bind_request_tab_id(task_id, session)
            effective_stop_checker = self._build_task_ownership_stop_checker(
                session,
                task_id,
                effective_stop_checker,
            )
             
            if stream:
                yield from self._execute_workflow_stream(
                    session,
                    sanitized_messages,
                    preset_name=preset_name,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                    allow_media_postprocess=allow_media_postprocess,
                )
            else:
                yield from self._execute_workflow_non_stream(
                    session,
                    sanitized_messages,
                    preset_name=preset_name,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                    allow_media_postprocess=allow_media_postprocess,
                )
        
        finally:
            if session:
                self._release_workflow_session(
                    session,
                    effective_stop_checker=effective_stop_checker,
                    task_id=task_id,
                )
                try:
                    from app.services.command_engine import command_engine
                    command_engine.schedule_deferred_workflow_commands(session, delay_sec=0.25)
                except Exception:
                    pass

    def execute_workflow_for_route_domain(
        self,
        route_domain: str,
        messages: List[Dict],
        stream: bool = True,
        task_id: str = None,
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
        allow_media_postprocess: bool = True,
        allocation_mode: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """使用指定域名路由匹配的标签页执行工作流。"""
        is_valid, error_msg, sanitized_messages = MessageValidator.validate(messages)

        if not is_valid:
            yield self.formatter.pack_error(
                f"无效请求: {error_msg}",
                error_type="invalid_request_error",
                code="invalid_messages"
            )
            return

        normalized_route_domain = str(route_domain or "").strip()
        if not normalized_route_domain:
            yield self.formatter.pack_error(
                "域名路由不能为空",
                error_type="invalid_request_error",
                code="invalid_route_domain"
            )
            return

        if task_id is None:
            safe_route_key = normalized_route_domain.replace(".", "_")
            task_id = f"url_{safe_route_key}_{int(time.time() * 1000)}"
        effective_stop_checker = stop_checker or self._should_stop_checker

        session = None
        try:
            session = self.tab_pool.acquire_by_route_domain(
                normalized_route_domain,
                task_id,
                timeout=60,
                allocation_mode=allocation_mode,
            )

            if session is None:
                yield self.formatter.pack_error(
                    f"域名路由 '{normalized_route_domain}' 没有可用标签页",
                    error_type="not_found_error",
                    code="route_domain_not_found"
                )
                yield self.formatter.pack_finish()
                return

            self._bind_request_tab_id(task_id, session)
            effective_stop_checker = self._build_task_ownership_stop_checker(
                session,
                task_id,
                effective_stop_checker,
            )

            if stream:
                yield from self._execute_workflow_stream(
                    session,
                    sanitized_messages,
                    preset_name=preset_name,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                    allow_media_postprocess=allow_media_postprocess,
                )
            else:
                yield from self._execute_workflow_non_stream(
                    session,
                    sanitized_messages,
                    preset_name=preset_name,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                    allow_media_postprocess=allow_media_postprocess,
                )

        finally:
            if session:
                self._release_workflow_session(
                    session,
                    effective_stop_checker=effective_stop_checker,
                    task_id=task_id,
                )
                try:
                    from app.services.command_engine import command_engine
                    command_engine.schedule_deferred_workflow_commands(session, delay_sec=0.25)
                except Exception:
                    pass

    def execute_workflow_for_exact_url(
        self,
        exact_url: str,
        messages: List[Dict],
        stream: bool = True,
        task_id: str = None,
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
        allow_media_postprocess: bool = True,
        resolved_tab_index: Optional[int] = None,
    ) -> Generator[str, None, None]:
        """使用标签页完整 URL 严格匹配的唯一标签页执行工作流。"""
        is_valid, error_msg, sanitized_messages = MessageValidator.validate(messages)

        if not is_valid:
            yield self.formatter.pack_error(
                f"无效请求: {error_msg}",
                error_type="invalid_request_error",
                code="invalid_messages"
            )
            return

        normalized_exact_url = str(exact_url or "").strip()
        if not normalized_exact_url:
            yield self.formatter.pack_error(
                "URL 路由不能为空",
                error_type="invalid_request_error",
                code="invalid_route_url"
            )
            return

        if task_id is None:
            task_id = f"tab_url_{int(time.time() * 1000)}"
        effective_stop_checker = stop_checker or self._should_stop_checker

        session = None
        try:
            if resolved_tab_index is not None:
                try:
                    resolved_index = int(resolved_tab_index)
                except Exception:
                    resolved_index = 0
                if resolved_index <= 0:
                    yield self.formatter.pack_error(
                        "URL 路由解析的标签页编号无效",
                        error_type="invalid_request_error",
                        code="resolved_tab_index_invalid",
                    )
                    yield self.formatter.pack_finish()
                    return
                session = self.tab_pool.acquire_by_index(resolved_index, task_id, timeout=60)
                if session is not None:
                    cached_url, _cached_domain = session.get_cached_route_snapshot()
                    if not tab_url_matches(normalized_exact_url, cached_url):
                        self.tab_pool.release(session.id, check_triggers=False, rollback_request_count=True)
                        session = None
                        yield self.formatter.pack_error(
                            f"URL 路由 '{normalized_exact_url}' 已不匹配标签页 #{resolved_index}",
                            error_type="not_found_error",
                            code="exact_url_resolved_tab_mismatch",
                        )
                        yield self.formatter.pack_finish()
                        return
            else:
                session = self.tab_pool.acquire_by_exact_url(normalized_exact_url, task_id, timeout=60)

            if session is None:
                yield self.formatter.pack_error(
                    f"URL 路由 '{normalized_exact_url}' 没有唯一可用标签页",
                    error_type="not_found_error",
                    code="exact_url_not_found"
                )
                yield self.formatter.pack_finish()
                return

            self._bind_request_tab_id(task_id, session)
            effective_stop_checker = self._build_task_ownership_stop_checker(
                session,
                task_id,
                effective_stop_checker,
            )

            if stream:
                yield from self._execute_workflow_stream(
                    session,
                    sanitized_messages,
                    preset_name=preset_name,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                    allow_media_postprocess=allow_media_postprocess,
                )
            else:
                yield from self._execute_workflow_non_stream(
                    session,
                    sanitized_messages,
                    preset_name=preset_name,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                    allow_media_postprocess=allow_media_postprocess,
                )

        finally:
            if session:
                self._release_workflow_session(
                    session,
                    effective_stop_checker=effective_stop_checker,
                    task_id=task_id,
                )
                try:
                    from app.services.command_engine import command_engine
                    command_engine.schedule_deferred_workflow_commands(session, delay_sec=0.25)
                except Exception:
                    pass

    def _bind_request_tab_id(self, task_id: str, session: Optional[TabSession]):
        if not session:
            return
        request_id = str(task_id or "").strip()
        if not request_id:
            return
        try:
            setattr(session, "_bound_request_id", request_id)
            from app.services.request_manager import request_manager
            bind_ok = bool(request_manager.bind_tab(request_id, session.id))
            tab_index = int(getattr(session, "persistent_index", 0) or 0)
            request_manager.update_request_metadata(
                request_id,
                tab_id=session.id,
                tab_index=tab_index if tab_index > 0 else None,
                target_domain=str(getattr(session, "current_domain", "") or "").strip(),
                preset_name=str(getattr(session, "preset_name", "") or "").strip(),
            )
            logger.debug(
                f"[{session.id}] 绑定请求标签页: request={request_id}, "
                f"bind_ok={bind_ok}, current_task={str(getattr(session, 'current_task_id', '') or '').strip() or '-'}, "
                f"status={getattr(getattr(session, 'status', None), 'value', '') or '-'}, "
                f"idx=#{getattr(session, 'persistent_index', 0) or '-'}"
            )
        except Exception as e:
            logger.debug(f"[{session.id}] 绑定请求标签页失败（忽略）: {e}")

    def _execute_workflow_stream(
        self,
        session: TabSession,
        messages: List[Dict],
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
        allow_media_postprocess: bool = True,
    ) -> Generator[str, None, None]:
        max_terminal_retries = 1
        attempt = 0
        retry_origin_chunk = None

        while True:
            stream = self._execute_workflow_stream_once(
                session,
                messages,
                preset_name=preset_name,
                stop_checker=stop_checker,
                workflow_priority=workflow_priority,
                allow_media_postprocess=allow_media_postprocess,
            )
            saw_content = False
            retry_requested = False

            try:
                for chunk in stream:
                    is_terminal_error = self._is_stream_terminal_error_chunk(chunk)
                    if (
                        not saw_content
                        and attempt < max_terminal_retries
                        and is_terminal_error
                        and self._is_retriable_stream_terminal_error_chunk(chunk)
                        and not (stop_checker or self._should_stop_checker)()
                    ):
                        retry_requested = True
                        retry_origin_chunk = chunk
                        logger.warning(
                            self._build_stream_terminal_alert_message(
                                session.id,
                                chunk,
                                retrying=True,
                                attempt=attempt + 1,
                                max_attempts=max_terminal_retries,
                            )
                        )
                        break

                    if not is_terminal_error and self._chunk_has_stream_content(chunk):
                        saw_content = True

                    if is_terminal_error:
                        if retry_origin_chunk and attempt > 0 and not saw_content:
                            chunk = self._build_retry_failure_error_chunk(
                                retry_origin_chunk,
                                chunk,
                            )
                        logger.error(
                            self._build_stream_terminal_alert_message(
                                session.id,
                                chunk,
                                retrying=False,
                                saw_content=saw_content,
                            )
                        )
                        self._emit_stream_terminal_alert_event(
                            session,
                            chunk,
                            saw_content=saw_content,
                        )

                    yield chunk
            finally:
                with contextlib.suppress(Exception):
                    stream.close()

            if not retry_requested:
                return

            attempt += 1
            setattr(session, "_workflow_stop_reason", None)
            setattr(session, "_workflow_user_stop_logged", False)
            time.sleep(0.5)

    @staticmethod
    def _extract_stream_error_payload(chunk: str) -> Optional[Dict]:
        if not isinstance(chunk, str) or not chunk.startswith("data: "):
            return None
        data_str = chunk[6:].strip()
        if not data_str or data_str == "[DONE]":
            return None
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            return None
        error = payload.get("error")
        return error if isinstance(error, dict) else None

    @classmethod
    def _is_stream_terminal_error_chunk(cls, chunk: str) -> bool:
        error = cls._extract_stream_error_payload(chunk)
        if not error:
            return False
        message = str(error.get("message") or "").strip().lower()
        return "stream_terminal_error:" in message

    @classmethod
    def _extract_stream_terminal_http_status(cls, detail: str) -> int:
        match = re.search(r"\bhttp\s+(\d{3})\b", str(detail or ""), re.IGNORECASE)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except Exception:
            return 0

    @classmethod
    def _is_retriable_stream_terminal_error_chunk(cls, chunk: str) -> bool:
        if not cls._is_stream_terminal_error_chunk(chunk):
            return False

        detail = cls._get_stream_terminal_error_detail(chunk).strip()
        if not detail:
            return False

        detail_lower = detail.lower()
        if detail_lower in {"send_unconfirmed", "new_chat_transition_timeout"}:
            return True

        status_code = cls._extract_stream_terminal_http_status(detail)
        if status_code:
            return status_code == 429 or status_code >= 500

        return "too many requests" in detail_lower or "rate limit" in detail_lower

    @classmethod
    def _get_stream_terminal_error_detail(cls, chunk: str) -> str:
        error = cls._extract_stream_error_payload(chunk)
        if not error:
            return ""

        message = " ".join(str(error.get("message") or "").split())
        if not message:
            return ""

        marker = "stream_terminal_error:"
        lowered = message.lower()
        marker_index = lowered.find(marker)
        if marker_index >= 0:
            detail = message[marker_index + len(marker):].strip()
            return detail or message

        return message

    def _build_retry_failure_error_chunk(self, first_chunk: str, final_chunk: str) -> str:
        first_detail = self._get_stream_terminal_error_detail(first_chunk)
        final_detail = self._get_stream_terminal_error_detail(final_chunk)
        if not first_detail or not final_detail or first_detail == final_detail:
            return final_chunk

        first_error = self._extract_stream_error_payload(first_chunk) or {}
        final_error = self._extract_stream_error_payload(final_chunk) or {}
        code = str(
            first_error.get("code")
            or final_error.get("code")
            or "workflow_failed"
        ).strip() or "workflow_failed"
        return self.formatter.pack_error(
            f"stream_terminal_error:{first_detail}; retry_failed:{final_detail}",
            code=code,
        )

    @classmethod
    def _summarize_stream_terminal_alert(
        cls,
        chunk: str,
        *,
        retrying: bool,
        saw_content: bool = False,
        attempt: int = 0,
        max_attempts: int = 0,
    ) -> str:
        detail = cls._get_stream_terminal_error_detail(chunk) or "unknown stream terminal error"
        lowered = detail.lower()
        category = "限流终止" if ("too many requests" in lowered or "429" in lowered) else "异常终止"

        if retrying:
            return (
                f"目标流告警：检测到{category}（{detail}），"
                f"自动重试工作流 ({attempt}/{max_attempts})"
            )

        suffix = "当前工作流将报错结束（已有部分输出）" if saw_content else "当前工作流将报错结束"
        return f"目标流告警：检测到{category}（{detail}），{suffix}"

    @classmethod
    def _build_stream_terminal_alert_message(
        cls,
        session_id: str,
        chunk: str,
        *,
        retrying: bool,
        saw_content: bool = False,
        attempt: int = 0,
        max_attempts: int = 0,
    ) -> str:
        summary = cls._summarize_stream_terminal_alert(
            chunk,
            retrying=retrying,
            saw_content=saw_content,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        return f"[ALERT][{session_id}] {summary}"

    def _emit_stream_terminal_alert_event(
        self,
        session: TabSession,
        chunk: str,
        *,
        saw_content: bool = False,
    ) -> None:
        summary = self._summarize_stream_terminal_alert(
            chunk,
            retrying=False,
            saw_content=saw_content,
        )
        detail = self._get_stream_terminal_error_detail(chunk)
        if not summary:
            return

        try:
            from app.services.command_engine import command_engine
            command_engine.emit_external_command_result_event(
                session,
                source_command_id="evt_stream_terminal_error",
                source_command_name="ARENA_STREAM_TERMINAL_ALERT",
                summary=summary,
                result=detail or summary,
                informative=True,
                mode="external_alert",
                group_name="arena_commands",
            )
        except Exception as e:
            logger.debug(f"[{session.id}] stream terminal alert event skipped: {e}")

    @staticmethod
    def _extract_stream_delta_content(chunk: str) -> str:
        if not isinstance(chunk, str):
            return ""

        parts = []
        for frame in chunk.split("\n\n"):
            frame = frame.strip()
            if not frame.startswith("data: "):
                continue

            data_str = frame[6:].strip()
            if not data_str or data_str == "[DONE]":
                continue

            try:
                payload = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                continue

            delta = choices[0].get("delta", {}) if isinstance(choices[0], dict) else {}
            content = delta.get("content", "") if isinstance(delta, dict) else ""
            if content:
                parts.append(str(content))

        return "".join(parts)

    @staticmethod
    def _chunk_has_stream_content(chunk: str) -> bool:
        return bool(BrowserWorkflowMixin._extract_stream_delta_content(chunk))

    @staticmethod
    def _looks_like_image_generation_request(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False

        direct_markers = (
            "生成图片",
            "生成图像",
            "生成一张图",
            "生成一张图片",
            "画一张",
            "画一幅",
            "帮我画",
            "请画",
            "出图",
            "做图",
            "文生图",
            "以图生图",
            "image generation",
            "generate image",
            "generate an image",
            "create image",
            "create an image",
            "draw an image",
            "draw me",
            "make an image",
            "render an image",
            "render image",
        )
        if any(marker in lowered for marker in direct_markers):
            return True

        english_actions = ("generate", "create", "draw", "make", "render", "design", "produce")
        english_objects = (
            "image",
            "images",
            "picture",
            "pictures",
            "photo",
            "photos",
            "illustration",
            "artwork",
            "poster",
            "logo",
            "icon",
            "banner",
            "wallpaper",
            "portrait",
        )
        if any(action in lowered for action in english_actions) and any(obj in lowered for obj in english_objects):
            return True

        chinese_actions = ("画", "绘制", "生成", "创作", "设计")
        chinese_objects = ("图片", "图像", "照片", "插画", "海报", "logo", "图标", "头像", "封面", "壁纸")
        return any(action in lowered for action in chinese_actions) and any(
            obj in lowered for obj in chinese_objects
        )

    def _execute_workflow_stream_once(
        self,
        session: TabSession,
        messages: List[Dict],
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
        allow_media_postprocess: bool = True,
    ) -> Generator[str, None, None]:
        """流式工作流执行（v2.0）"""
        tab = session.tab
        effective_stop_checker = stop_checker or self._should_stop_checker
        workflow_priority_value = 2
        workflow_runtime = None
        executor = None
        workflow_aborted = False
        workflow_abort_message = ""
        command_engine = None
        try:
            from app.services.command_engine import command_engine as _command_engine
            command_engine = _command_engine
            workflow_priority_value = command_engine._normalize_priority(
                workflow_priority, command_engine._get_request_priority_baseline()
            )
        except Exception:
            workflow_priority_value = 2
    
        if effective_stop_checker():
            yield self.formatter.pack_error("请求已取消", code="cancelled")
            yield self.formatter.pack_finish()
            return
    
        try:
            url = tab.url
        except Exception as e:
            logger.warning(f"[{session.id}] 标签页访问失败: {e}")
            session.mark_error("tab_access_failed")
            yield self.formatter.pack_error(
                "标签页已关闭或失效，请刷新页面后重试",
                code="tab_closed"
            )
            yield self.formatter.pack_finish()
            return
    
        if not url:
            yield self.formatter.pack_error(
                "请先打开目标AI网站",
                code="no_page"
            )
            yield self.formatter.pack_finish()
            return
    
        invalid_urls = ("about:blank", "chrome://newtab/", "chrome://new-tab-page/")
        if url in invalid_urls:
            yield self.formatter.pack_error(
                "当前是空白页，请先打开目标AI网站",
                code="blank_page"
            )
            yield self.formatter.pack_finish()
            return
    
        if "chrome-error://" in url or "about:neterror" in url:
            yield self.formatter.pack_error(
                "页面加载错误，请刷新后重试",
                code="page_error"
            )
            yield self.formatter.pack_finish()
            return
    
        try:
            domain = extract_remote_site_domain(url)
            if not domain:
                raise ValueError(f"not a remote site url: {url}")
            session.current_domain = domain
        except Exception as e:
            logger.warning(f"[{session.id}] URL 解析失败: {url}, 错误: {e}")
            yield self.formatter.pack_error(
                "当前页面不是可解析的网站，请打开真实的远程站点页面后再试",
                code="invalid_url"
            )
            yield self.formatter.pack_finish()
            return
    
        logger.debug(f"[{session.id}] 域名: {domain}")
        
        page_status = self._check_page_status(tab)
        if not page_status["ready"]:
            yield self.formatter.pack_error(
                f"页面未就绪: {page_status['reason']}",
                code="page_not_ready"
            )
            yield self.formatter.pack_finish()
            return
        
        config_engine = self._get_config_engine()
        effective_preset_name = preset_name if preset_name is not None else session.preset_name
        resolved_preset_name = effective_preset_name or config_engine.get_default_preset(domain) or "主预设"
        site_config = config_engine.get_site_config(domain, tab.html, preset_name=effective_preset_name)
        if not site_config:
            yield self.formatter.pack_error(
                "配置加载失败",
                code="config_error"
            )
            yield self.formatter.pack_finish()
            return
        
        selectors = site_config.get("selectors", {})
        workflow = site_config.get("workflow", [])
        stealth_mode = site_config.get("stealth", False)
        force_new_conversation = bool(BrowserConstants.get("FORCE_NEW_CONVERSATION"))
        conversation_threshold = self._get_conversation_timeout_threshold()
        skip_new_chat = not session.should_start_new_conversation(
            current_domain=domain,
            preset_name=resolved_preset_name,
            threshold_seconds=conversation_threshold,
            force_new=force_new_conversation,
        )

        if force_new_conversation:
            logger.debug(f"[{session.id}] 已启用强制新建对话")
        elif skip_new_chat:
            logger.debug(
                f"[{session.id}] 复用当前对话: domain={domain}, "
                f"preset={resolved_preset_name}, threshold={conversation_threshold}s"
            )
        else:
            logger.debug(
                f"[{session.id}] 本轮将新建对话: domain={domain}, "
                f"preset={resolved_preset_name}, threshold={conversation_threshold}s"
            )
        
        image_config = site_config.get("image_extraction", {})
        modalities = image_config.get("modalities") or {}
        image_extraction_enabled = bool(image_config.get("enabled", False)) or any(
            is_modality_enabled(modalities, key) for key in ("image", "audio", "video")
        )
        stream_config = site_config.get("stream_config", {}) or {}
        file_paste_config = site_config.get("file_paste", {}) or {}
        prompt_padding_config = site_config.get("prompt_padding", {}) or {}
        request_blocks: set[int] = set()
        self._emit_request_block(
            request_blocks,
            1,
            "准备",
            f"domain={domain}, preset={resolved_preset_name}, workflow={len(workflow)}",
        )

        audio_capture_preload_enabled = (
            is_modality_enabled(modalities, "audio")
            and get_modality_run_policy(modalities, "audio") == "always_probe"
            and bool(image_config.get("audio_capture_enabled", True))
            and bool(image_config.get("audio_capture_preload_enabled", True))
        )
        if not audio_capture_preload_enabled:
            if getattr(session, "_audio_capture_init_script_source", None) is not None:
                setattr(session, "_audio_capture_init_script_source", None)
                logger.debug("页面音频捕获预注入脚本已按配置停用")
        if audio_capture_preload_enabled and not effective_stop_checker():
            try:
                from app.core.extractors.media_extractor import media_extractor

                init_script = media_extractor.build_page_audio_capture_init_script(image_config)
                capture_status = media_extractor.get_page_audio_capture_status(tab)
                current_capture_version = int(capture_status.get("version") or 0) if isinstance(capture_status, dict) else 0
                has_current_capture = current_capture_version == int(getattr(media_extractor, "PAGE_AUDIO_CAPTURE_SCRIPT_VERSION", 0) or 0)
                tracked_audio_nodes = 0
                if isinstance(capture_status, dict):
                    tracked_audio_nodes = int(capture_status.get("tracked_media_elements") or 0) + int(capture_status.get("tracked_web_audio") or 0)
                try:
                    if getattr(session, "_audio_capture_init_script_source", None) != init_script:
                        tab.run_cdp(
                            "Page.addScriptToEvaluateOnNewDocument",
                            source=init_script,
                        )
                        setattr(session, "_audio_capture_init_script_source", init_script)
                        logger.debug("页面音频捕获预注入脚本已注册")
                    else:
                        logger.debug("页面音频捕获预注入脚本已存在")
                except Exception as cdp_exc:
                    logger.debug(f"页面音频捕获预注入脚本注册失败（已忽略）: {cdp_exc}")

                media_extractor.prepare_page_audio_capture(tab, image_config)
                should_reload_capture = (
                    bool(image_config.get("audio_capture_reload_before_workflow", False))
                    and (
                        not has_current_capture
                        or tracked_audio_nodes <= 0
                    )
                )
                if should_reload_capture:
                    current_tab_url = ""
                    try:
                        current_tab_url = str(tab.url or "")
                    except Exception:
                        current_tab_url = ""
                    should_reload_for_capture = (
                        "/settings" not in current_tab_url
                        and "chrome://" not in current_tab_url
                        and "about:" not in current_tab_url
                    )
                    if not should_reload_for_capture:
                        logger.debug(f"页面音频捕获跳过刷新预热: url={current_tab_url!r}")
                    else:
                        try:
                            tab.refresh(ignore_cache=True)
                            try:
                                tab.wait.doc_loaded(timeout=15)
                            except Exception:
                                pass
                            input_selector = selectors.get("input_box", "")
                            if input_selector:
                                deadline = time.time() + 20.0
                                while time.time() < deadline and not effective_stop_checker():
                                    try:
                                        if tab.ele(input_selector, timeout=0.5):
                                            break
                                    except Exception:
                                        pass
                                    time.sleep(0.5)
                            media_extractor.prepare_page_audio_capture(tab, image_config)
                            logger.debug("页面音频捕获已刷新页面并重新初始化")
                        except Exception as refresh_exc:
                            logger.debug(f"页面音频捕获刷新预热失败（已忽略）: {refresh_exc}")
                elif bool(image_config.get("audio_capture_reload_before_workflow", False)):
                    logger.debug(
                        "页面音频捕获跳过刷新预热：当前脚本版本已就绪且已接管音频节点 "
                        f"(tracked_nodes={tracked_audio_nodes})"
                    )
            except Exception as preload_exc:
                logger.debug(f"页面音频捕获预热失败（已忽略）: {preload_exc}")

        upload_history = self._get_upload_history_images_flag(default=True)
        logger.debug(f"图片历史上传: {upload_history}")
        image_source_messages = self._select_image_source_messages(messages, upload_history)

        logger.debug(f"图片源消息数: {len(image_source_messages)}/{len(messages)}")
        user_images = extract_images_from_messages(image_source_messages)
        logger.info(
            "[IMAGE_FLOW_DIAG] backend.workflow.images | "
            f"upload_history={upload_history} "
            f"source_messages={len(image_source_messages)}/{len(messages)} "
            f"roles={[str(m.get('role', '')) for m in image_source_messages if isinstance(m, dict)]} "
            f"extracted={len(user_images)} "
            f"paths={[str(path) for path in user_images[:3]]}"
        )

        has_declared_image = False
        try:
            for mm in image_source_messages:
                c = mm.get("content")
                if isinstance(c, str):
                    if '"type"' in c and "image_url" in c:
                        has_declared_image = True
                        break
                elif isinstance(c, (list, tuple)):
                    for it in c:
                        if isinstance(it, dict) and it.get("type") == "image_url":
                            has_declared_image = True
                            break
                    if has_declared_image:
                        break
        except Exception:
            pass

        if has_declared_image and not user_images:
            logger.warning(
                "收到图片占位符但没有实际图片数据：image_url.url 为空或无效，"
                "已自动忽略图片并继续执行纯文本对话。"
            )
        
        prompt_text = self._build_prompt_from_messages(messages)
        prompt_text = self._apply_prompt_padding(prompt_text, prompt_padding_config)

        context = {
            "prompt": prompt_text,
            "images": user_images
        }
        
        extractor = config_engine.get_site_extractor(domain, preset_name=effective_preset_name)
        site_advanced_config = config_engine.get_site_advanced_config(
            domain,
            preset_name=resolved_preset_name,
        )
        logger.debug(f"[{session.id}] 使用提取器: {extractor.get_id()} [预设: {resolved_preset_name}]")

        try:
            from app.services.request_manager import request_manager
            request_manager.update_request_metadata(
                str(getattr(session, "_bound_request_id", "") or ""),
                target_domain=domain,
                route_domain=domain,
                preset_name=resolved_preset_name,
                tab_index=int(getattr(session, "persistent_index", 0) or 0) or None,
                tab_id=session.id,
            )
        except Exception as e:
            logger.debug(f"[{session.id}] 更新请求监控元数据失败（忽略）: {e}")

        if command_engine is not None:
            try:
                workflow_runtime = command_engine.begin_workflow_runtime(
                    session,
                    task_id=str(getattr(session, "current_task_id", "") or ""),
                    preset_name=resolved_preset_name,
                    priority=workflow_priority_value,
                )
            except Exception as e:
                logger.debug(f"[{session.id}] 工作流运行时注册失败（忽略）: {e}")

        def _combined_stop_checker() -> bool:
            if effective_stop_checker():
                return True
            if command_engine is not None and command_engine.workflow_interrupt_requested(session):
                setattr(session, "_workflow_stop_reason", "command_interrupt")
                return True
            return False
        
        executor = WorkflowExecutor(
            tab=tab,
            stealth_mode=stealth_mode,
            should_stop_checker=_combined_stop_checker,
            extractor=extractor,
            image_config=image_config,
            stream_config=stream_config,
            file_paste_config=file_paste_config,
            site_advanced_config=site_advanced_config,
            selectors=selectors,
            session=session,
        )
        
        result_container_selector = selectors.get("result_container", "")
        setattr(session, "_workflow_stop_reason", None)
        if not effective_stop_checker():
            setattr(session, "_workflow_user_stop_logged", False)
        streamed_text_parts: List[str] = []
        conversation_activity_marked = False
        
        try:
            with executor.workflow_execution_scope():
                step_index = 0
                workflow_total = len(workflow)
                while step_index < len(workflow):
                    step = workflow[step_index]
                    if command_engine is not None:
                        command_engine.update_workflow_runtime_step(session, step_index, step)

                    stop_reason = str(getattr(session, "_workflow_stop_reason", "") or "").strip()
                    if stop_reason == "command_interrupt" or (
                        command_engine is not None and command_engine.workflow_interrupt_requested(session)
                    ):
                        interrupt_result = (
                            command_engine.handle_pending_workflow_interrupts(session)
                            if command_engine is not None
                            else {"handled": False, "abort": False, "message": ""}
                        )
                        if interrupt_result.get("abort"):
                            workflow_aborted = True
                            workflow_abort_message = str(
                                interrupt_result.get("message") or "工作流已被命令打断"
                            )
                            logger.error(
                                f"[{session.id}] 工作流被命令打断: "
                                f"{interrupt_result.get('abort_by') or 'unknown'}"
                            )
                            yield self.formatter.pack_error(
                                workflow_abort_message,
                                code="workflow_interrupted",
                            )
                            break
                        if interrupt_result.get("handled"):
                            logger.info(f"[{session.id}] 工作流恢复执行")
                            continue

                    if effective_stop_checker():
                        if getattr(session, "_workflow_user_stop_logged", False):
                            break
                        if stop_reason == "timeout":
                            logger.warning(f"[{session.id}] 工作流因超时停止")
                        else:
                            logger.info(f"[{session.id}] 工作流被用户中断")
                        setattr(session, "_workflow_user_stop_logged", True)
                        break

                    action = step.get('action', '')
                    target_key = step.get('target', '')
                    optional = step.get('optional', False)
                    param_value = step.get('value')
                    action_upper = str(action or "").strip().upper()
                    target_key_normalized = str(target_key or "").strip().lower()

                    if skip_new_chat and (
                        target_key_normalized in {"new_chat_btn", "new_chat", "new_conversation"}
                        or action_upper in {"NEW_CHAT", "NEW_CONVERSATION"}
                    ):
                        logger.debug(
                            f"[{session.id}] 会话仍有效，跳过新建对话步骤 "
                            f"(action={action_upper or '-'}, target={target_key_normalized or '-'})"
                        )
                        step_index += 1
                        continue

                    selector = selectors.get(target_key, '')
                    if action_upper in {"STREAM_WAIT", "STREAM_OUTPUT", "PAGE_FETCH"}:
                        self._emit_request_block(
                            request_blocks,
                            3,
                            "响应",
                            "网络/DOM 监听",
                        )
                    else:
                        self._emit_request_block(
                            request_blocks,
                            2,
                            "交互",
                            "页面动作/输入/发送",
                        )

                    step_started_at = time.perf_counter()
                    step_no = step_index + 1
                    step_tag = f"[STEP {step_no}]"
                    selector_preview = self._compact_log_value(selector, 100)
                    step_extra_parts = [
                        f"total={workflow_total}",
                        f"optional={bool(optional)}",
                        f"stealth={bool(stealth_mode)}",
                    ]
                    if action_upper == "FILL_INPUT":
                        step_extra_parts.append(f"prompt_len={len(str(context.get('prompt') or ''))}")
                        step_extra_parts.append(f"images={len(context.get('images') or [])}")
                    elif action_upper == "WAIT":
                        step_extra_parts.append(f"wait={param_value if param_value is not None else 0.5}")
                    elif action_upper == "JS_EXEC":
                        step_extra_parts.append(f"value_len={len(str(param_value or ''))}")
                    elif action_upper in {"CLICK", "COORD_CLICK", "COORD_SCROLL", "KEY_PRESS"}:
                        step_extra_parts.append(f"value_len={len(str(param_value or ''))}")

                    logger.debug(
                        f"{step_tag} 开始: "
                        f"action={action_upper or action or '-'}, "
                        f"target={target_key or '-'}, selector={selector_preview}, "
                        f"{', '.join(step_extra_parts)}"
                    )

                    if not selector and action not in ("WAIT", "KEY_PRESS", "COORD_CLICK", "COORD_SCROLL", "JS_EXEC", "READONLY_HINT", "PAGE_FETCH"):
                        if optional:
                            logger.debug(
                                f"{step_tag} 跳过: "
                                f"action={action_upper or action or '-'}, "
                                f"target={target_key or '-'}, reason=missing_selector_optional"
                            )
                            step_index += 1
                            continue
                        else:
                            logger.error(
                                f"{step_tag} 失败: "
                                f"action={action_upper or action or '-'}, "
                                f"target={target_key or '-'}, elapsed=0.00s, "
                                "error=missing_selector"
                            )
                            yield self.formatter.pack_error(
                                f"缺少配置: {target_key}",
                                code="missing_selector"
                            )
                            break

                    try:
                        chunk_count = 0
                        delta_chars = 0
                        for chunk in executor.execute_step(
                            action=action,
                            selector=selector,
                            target_key=target_key,
                            value=param_value,
                            optional=optional,
                            context=context
                        ):
                            chunk_count += 1
                            delta_content = self._extract_stream_delta_content(chunk)
                            if delta_content:
                                delta_chars += len(delta_content)
                                streamed_text_parts.append(delta_content)
                            yield chunk

                        step_elapsed = time.perf_counter() - step_started_at
                        logger.debug(
                            f"{step_tag} 完成: "
                            f"action={action_upper or action or '-'}, "
                            f"target={target_key or '-'}, elapsed={step_elapsed:.2f}s, "
                            f"chunks={chunk_count}, stream_chars={delta_chars}"
                        )

                        if effective_stop_checker():
                            logger.info(f"[{session.id}] 步骤完成后检测到取消，提前结束工作流")
                            if command_engine is not None and command_engine.workflow_interrupt_requested(session):
                                setattr(session, "_workflow_stop_reason", "command_interrupt")
                                continue
                            break

                        page_fetch_sent = False
                        if action in ("STREAM_WAIT", "STREAM_OUTPUT"):
                            result_container_selector = selector
                        if (
                            action == "PAGE_FETCH"
                            and hasattr(executor, "consume_last_request_transport_sent")
                        ):
                            page_fetch_sent = bool(executor.consume_last_request_transport_sent())
                            if page_fetch_sent:
                                step_index = executor._consume_request_transport_followup_steps(
                                    workflow,
                                    step_index,
                                )
                        if (
                            not conversation_activity_marked
                            and (
                                self._step_submits_conversation_request(action, target_key, param_value)
                                or page_fetch_sent
                                or action_upper in {"STREAM_WAIT", "STREAM_OUTPUT"}
                            )
                        ):
                            session.mark_conversation_activity(domain, resolved_preset_name)
                            conversation_activity_marked = True
                        step_index += 1

                    except (ElementNotFoundError, WorkflowError) as e:
                        step_elapsed = time.perf_counter() - step_started_at
                        logger.warning(
                            f"{step_tag} 中断: "
                            f"action={action_upper or action or '-'}, "
                            f"target={target_key or '-'}, elapsed={step_elapsed:.2f}s, "
                            f"error={self._compact_log_value(e, 180)}"
                        )
                        if isinstance(e, WorkflowError) and str(e) in {
                            "new_chat_transition_timeout",
                            "send_unconfirmed",
                        }:
                            error_code = str(e)
                            workflow_aborted = True
                            yield self.formatter.pack_error(
                                f"stream_terminal_error:{error_code}",
                                code=error_code,
                            )
                        break
                    except Exception as e:
                        step_elapsed = time.perf_counter() - step_started_at
                        if effective_stop_checker():
                            logger.info(f"[{session.id}] 取消后忽略步骤异常: {e}")
                            break
                        logger.error(
                            f"{step_tag} 失败: "
                            f"action={action_upper or action or '-'}, "
                            f"target={target_key or '-'}, elapsed={step_elapsed:.2f}s, "
                            f"optional={bool(optional)}, error={self._compact_log_value(e, 180)}"
                        )
                        if not optional:
                            yield self.formatter.pack_error(f"执行中断: {str(e)}")
                            break

            if (
                not workflow_aborted
                and command_engine is not None
                and command_engine.workflow_interrupt_requested(session)
            ):
                interrupt_result = command_engine.handle_pending_workflow_interrupts(session)
                if interrupt_result.get("abort"):
                    workflow_aborted = True
                    workflow_abort_message = str(
                        interrupt_result.get("message") or "工作流已被命令打断"
                    )
                    logger.error(
                        f"[{session.id}] 流程结束时工作流被命令打断: "
                        f"{interrupt_result.get('abort_by') or 'unknown'}"
                    )
                    yield self.formatter.pack_error(
                        workflow_abort_message,
                        code="workflow_interrupted",
                    )
                elif interrupt_result.get("handled"):
                    logger.info(f"[{session.id}] 工作流收尾阶段已执行挂起命令")

            # 多模态提取
            self._emit_request_block(
                request_blocks,
                4,
                "收尾",
                f"image_enabled={image_extraction_enabled}, stop={effective_stop_checker()}",
            )
            logger.debug(f"[WORKFLOW] 主循环结束: image_enabled={image_extraction_enabled}, should_stop={effective_stop_checker()}")
            if (
                allow_media_postprocess
                and image_extraction_enabled
                and not effective_stop_checker()
                and not workflow_aborted
            ):
                response_text_hint = "".join(streamed_text_parts)
                request_text_hint = str(context.get("prompt") or "")
                media_generation_state = getattr(executor, "_last_stream_media_state", None)
                stream_media_items = getattr(executor, "_last_stream_media_items", None)
                dom_stream_media_items = []
                dom_image_detected = False
                dom_final_image_urls = []
                try:
                    stream_monitor = getattr(executor, "_stream_monitor", None)
                    if stream_monitor is not None:
                        dom_stream_media_items = stream_monitor.get_final_images() or []
                        dom_image_detected = bool(stream_monitor.has_detected_images())
                        dom_final_image_urls = stream_monitor.get_final_image_urls() or []
                except Exception:
                    dom_stream_media_items = []
                    dom_image_detected = False
                    dom_final_image_urls = []

                should_run_media_postprocess, media_postprocess_diag = self._should_run_media_postprocess(
                    image_config,
                    request_text_hint=request_text_hint,
                    response_text_hint=response_text_hint,
                    media_generation_state=media_generation_state,
                    stream_media_items=stream_media_items,
                    dom_stream_media_items=dom_stream_media_items,
                    dom_image_detected=dom_image_detected,
                    dom_final_image_urls=dom_final_image_urls,
                )
                if not should_run_media_postprocess:
                    logger.debug(
                        "[WORKFLOW] 跳过多模态提取分支："
                        f"{json.dumps(media_postprocess_diag, ensure_ascii=False)}"
                    )
                else:
                    logger.debug(
                        "[WORKFLOW] 进入多模态提取分支："
                        f"{json.dumps(media_postprocess_diag, ensure_ascii=False)}"
                    )
                try:
                    media_items = []
                    if should_run_media_postprocess:
                        if dom_stream_media_items:
                            media_items = [
                                dict(item)
                                for item in dom_stream_media_items
                                if isinstance(item, dict)
                            ]
                            image_items = [item for item in media_items if item.get("media_type") == "image"]
                            if image_items:
                                localized_images = self._localize_images_with_background_cache(
                                    image_items,
                                    wait_seconds=float(
                                        image_config.get("background_download_wait_seconds")
                                        or image_config.get("download_wait_seconds")
                                        or 1.0
                                    ),
                                )
                                other_items = [item for item in media_items if item.get("media_type") != "image"]
                                media_items = localized_images + other_items
                            logger.debug(
                                f"[WORKFLOW] 复用 DOM 监听已提取的媒体结果: {len(media_items)} 项"
                            )
                        else:
                            direct_modalities = (
                                media_postprocess_diag.get("direct_postprocess_modalities")
                                if media_postprocess_diag.get("decision") == "direct_postprocess_modalities"
                                else None
                            )
                            media_items = self._extract_media_after_stream(
                                tab=tab,
                                extractor=extractor,
                                image_config=image_config,
                                result_selector=result_container_selector,
                                message_wrapper_selector=selectors.get("message_wrapper", ""),
                                completion_id=executor._completion_id,
                                stop_checker=_combined_stop_checker,
                                response_text_hint=response_text_hint,
                                request_text_hint=request_text_hint,
                                media_generation_state=media_generation_state,
                                stream_media_items=stream_media_items,
                                direct_modalities=direct_modalities,
                            )
                    
                    if media_items:
                        download_urls = image_config.get("download_urls", False)
                        if download_urls:
                            image_items = [item for item in media_items if item.get("media_type") == "image"]
                            other_items = [item for item in media_items if item.get("media_type") != "image"]
                            image_items = self._download_url_images(image_items, tab=tab)
                            media_items = image_items + other_items

                        media_items = self._persist_remote_media_urls_to_local(
                            media_items,
                            tab=tab,
                            max_size_mb=int(image_config.get("max_size_mb", 10) or 10),
                        )
                        
                        logger.debug(f"[PROBE] 即将发送多模态资源（Markdown），数量={len(media_items)}")

                        try:
                            response_media = self._prepare_media_items_for_response(media_items)
                            md = self._build_media_markdown_block(media_items)
                            if md:
                                yield self.formatter.pack_chunk(
                                    md,
                                    completion_id=executor._completion_id,
                                    media=response_media,
                                )
                                logger.debug(f"[MD_MEDIA] 已发送结构化多模态资源，共 {len(media_items)} 项")
                            else:
                                yield self.formatter.pack_chunk(
                                    "",
                                    completion_id=executor._completion_id,
                                    media=response_media,
                                )
                                logger.debug(f"[MD_MEDIA] 已发送纯结构化多模态资源，共 {len(media_items)} 项")
                        except Exception as e:
                            logger.warning(f"[MD_MEDIA] 发送 Markdown 媒体链接失败: {e}")
                except Exception as e:
                    logger.warning(f"[{session.id}] 多模态提取失败: {e}")
        
        except Exception as e:
            if not (effective_stop_checker()):
                logger.error(f"[{session.id}] 工作流执行异常: {e}", exc_info=True)
                yield self.formatter.pack_error(f"系统错误: {str(e)}")
            yield self.formatter.pack_finish()
        finally:
            if executor is not None:
                try:
                    executor.cleanup_after_workflow()
                except Exception as e:
                    logger.debug(f"[{session.id}] 工作流执行器清理失败（忽略）: {e}")
            if command_engine is not None and workflow_runtime is not None:
                try:
                    stop_reason = str(getattr(session, "_workflow_stop_reason", "") or "").strip()
                    externally_stopped = bool(effective_stop_checker()) and stop_reason != "command_interrupt"
                    command_engine.finish_workflow_runtime(
                        session,
                        aborted=workflow_aborted or bool(workflow_abort_message) or externally_stopped,
                    )
                except Exception as e:
                    logger.debug(f"[{session.id}] 工作流运行时清理失败（忽略）: {e}")
            yield self.formatter.pack_finish()

    def _execute_workflow_non_stream(
        self, 
        session: TabSession,
        messages: List[Dict],
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
        allow_media_postprocess: bool = True,
    ) -> Generator[str, None, None]:
        """非流式工作流执行"""
        collected_content = []
        collected_media = []
        error_data = None
        
        stream = self._execute_workflow_stream(
            session,
            messages,
            preset_name=preset_name,
            stop_checker=stop_checker,
            workflow_priority=workflow_priority,
            allow_media_postprocess=allow_media_postprocess,
        )

        try:
            for chunk in stream:
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

                        media_items = data.get("media")
                        if isinstance(media_items, list):
                            collected_media.extend(media_items)
                        
                        if "choices" in data and data["choices"]:
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                collected_content.append(content)
                    except json.JSONDecodeError:
                        continue
        except GeneratorExit:
            with contextlib.suppress(Exception):
                stream.close()
            raise
        finally:
            with contextlib.suppress(Exception):
                stream.close()
        
        if error_data:
            yield json.dumps(error_data, ensure_ascii=False)
        else:
            full_content = "".join(collected_content)
            if allow_media_postprocess and not collected_media and full_content.strip():
                extra_media_items = self._retry_pending_media_from_response_text(
                    session,
                    full_content,
                    preset_name=preset_name,
                    stop_checker=stop_checker,
                )
                if extra_media_items:
                    collected_media.extend(extra_media_items)
            response = self.formatter.pack_non_stream(
                full_content,
                media=self._dedupe_media_items(collected_media),
            )
            yield json.dumps(response, ensure_ascii=False)
