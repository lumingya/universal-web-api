import copy
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app.core.config import get_logger

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession


logger = get_logger("CMD_ENG")


class CommandEngineRuntimeMixin:
    def _workflow_queue_key(
        self,
        command: Dict[str, Any],
        trigger_rollback: Optional[Dict[str, Any]] = None,
    ) -> str:
        cmd_id = str((command or {}).get("id", "")).strip()
        rollback = trigger_rollback or {}
        kind = str(rollback.get("kind", "") or "").strip()
        token = str(rollback.get("token", "") or "").strip()
        if cmd_id and kind and token:
            return f"{cmd_id}:{kind}:{token}"
        return cmd_id

    def _dedupe_deferred_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            queue_key = str(item.get("queue_key", "") or "").strip()
            if not queue_key:
                queue_key = self._workflow_queue_key(
                    item.get("command") or {},
                    item.get("trigger_rollback") if isinstance(item.get("trigger_rollback"), dict) else None,
                )
                if queue_key:
                    item = dict(item)
                    item["queue_key"] = queue_key
            if queue_key and queue_key in seen:
                continue
            if queue_key:
                seen.add(queue_key)
            deduped.append(item)
        return deduped

    def _has_active_workflow(self, session: 'TabSession') -> bool:
        runtime = self._get_active_workflow_runtime(session)
        return bool(runtime and runtime.get("active"))

    def begin_workflow_runtime(
        self,
        session: 'TabSession',
        *,
        task_id: str = "",
        preset_name: str = "",
        priority: Optional[int] = None,
    ) -> Dict[str, Any]:
        runtime = {
            "runtime_id": uuid.uuid4().hex,
            "task_id": str(task_id or "").strip(),
            "preset_name": str(preset_name or "").strip(),
            "priority": self._normalize_priority(priority, self._get_request_priority_baseline()),
            "current_step_index": -1,
            "current_step_action": "",
            "pending_interrupts": [],
            "pending_interrupt_ids": set(),
            "deferred_commands": [],
            "deferred_command_ids": set(),
            "active": True,
            "interrupting": False,
        }
        with self._lock:
            stack = list(getattr(session, "_workflow_runtime_stack", None) or [])
            stack.append(runtime)
            setattr(session, "_workflow_runtime_stack", stack)
        return runtime

    def update_workflow_runtime_step(
        self,
        session: 'TabSession',
        step_index: int,
        step: Optional[Dict[str, Any]] = None,
    ):
        runtime = self._get_active_workflow_runtime(session)
        if not runtime:
            return
        runtime["current_step_index"] = int(step_index)
        runtime["current_step_action"] = str((step or {}).get("action", "") or "")

    def finish_workflow_runtime(
        self,
        session: 'TabSession',
        *,
        aborted: bool = False,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            stack = list(getattr(session, "_workflow_runtime_stack", None) or [])
            if not stack:
                return None
            runtime = stack.pop()
            setattr(session, "_workflow_runtime_stack", stack)

        pending_interrupts = list(runtime.get("pending_interrupts") or [])
        deferred_commands = list(runtime.get("deferred_commands") or [])
        stop_reason = str(getattr(session, "_workflow_stop_reason", "") or "").strip()
        preserve_pending_interrupts = bool(pending_interrupts) and (
            not aborted or stop_reason in {"command_interrupt", "command_interrupt_abort"}
        )
        if preserve_pending_interrupts:
            deferred_ids = {
                str(item.get("queue_key", "") or "").strip()
                or self._workflow_queue_key(
                    item.get("command") or {},
                    item.get("trigger_rollback") if isinstance(item.get("trigger_rollback"), dict) else None,
                )
                for item in deferred_commands
                if isinstance(item, dict)
            }
            for item in pending_interrupts:
                if not isinstance(item, dict):
                    continue
                command = copy.deepcopy(item.get("command") or {})
                queue_key = str(item.get("queue_key", "") or "").strip() or self._workflow_queue_key(
                    command,
                    item.get("trigger_rollback") if isinstance(item.get("trigger_rollback"), dict) else None,
                )
                if queue_key and queue_key in deferred_ids:
                    continue
                deferred_commands.append({
                    "command": command,
                    "chain": list(item.get("chain") or []),
                    "interrupt_context": copy.deepcopy(item.get("interrupt_context") or {}),
                    "trigger_rollback": copy.deepcopy(item.get("trigger_rollback") or {}),
                    "queue_key": queue_key,
                    "queued_at": float(item.get("requested_at", time.time()) or time.time()),
                })
                if queue_key:
                    deferred_ids.add(queue_key)

        runtime["active"] = False
        runtime["interrupting"] = False
        runtime["pending_interrupts"] = []
        runtime["pending_interrupt_ids"] = set()

        preserve_deferred = bool(deferred_commands) and (
            not aborted or stop_reason == "command_interrupt_abort"
        )
        if stack:
            parent = stack[-1]
            if preserve_deferred:
                parent_deferred = parent.setdefault("deferred_commands", [])
                parent_ids = parent.setdefault("deferred_command_ids", set())
                for item in deferred_commands:
                    queue_key = str(item.get("queue_key", "") or "").strip() or self._workflow_queue_key(
                        item.get("command") or {},
                        item.get("trigger_rollback") if isinstance(item.get("trigger_rollback"), dict) else None,
                    )
                    if queue_key and queue_key in parent_ids:
                        continue
                    parent_deferred.append(item)
                    if queue_key:
                        parent_ids.add(queue_key)
        elif preserve_deferred:
            existing = list(getattr(session, "_pending_post_workflow_commands", None) or [])
            existing_ids = {
                str((item.get("command") or {}).get("id", "")).strip()
                for item in existing
                if isinstance(item, dict)
            }
            merged = self._dedupe_deferred_items(existing + deferred_commands)
            setattr(session, "_pending_post_workflow_commands", merged)
        else:
            setattr(session, "_pending_post_workflow_commands", [])

        return runtime

    def flush_deferred_workflow_commands(self, session: 'TabSession'):
        with self._lock:
            items = list(getattr(session, "_pending_post_workflow_commands", None) or [])
            setattr(session, "_pending_post_workflow_commands", [])
        if not items:
            return

        def _run_deferred():
            acquired = False
            task_id = f"workflow_resume_{int(time.time() * 1000)}"
            try:
                if hasattr(session, "acquire_for_command"):
                    acquired = bool(session.acquire_for_command(task_id))
                if not acquired:
                    logger.info(
                        f"[CMD] 跳过延后命令补跑（标签页忙碌）: {getattr(session, 'id', '')}"
                    )
                    with self._lock:
                        existing = list(getattr(session, "_pending_post_workflow_commands", None) or [])
                        setattr(
                            session,
                            "_pending_post_workflow_commands",
                            self._dedupe_deferred_items(existing + items),
                        )
                    return

                for item in items:
                    command = copy.deepcopy((item or {}).get("command") or {})
                    if not command or not command.get("enabled", True):
                        continue
                    chain = list((item or {}).get("chain") or [])
                    interrupt_context = copy.deepcopy((item or {}).get("interrupt_context") or {})
                    exec_key = (command.get("id"), getattr(session, "id", ""))
                    with self._lock:
                        if exec_key in self._executing:
                            continue
                        self._executing.add(exec_key)
                    try:
                        logger.info(
                            f"[CMD] 恢复后补跑延后命令: {command.get('name')} "
                            f"(标签页={getattr(session, 'id', '')})"
                        )
                        self._execute_command(
                            command,
                            session,
                            chain=chain,
                            interrupt_context=interrupt_context or None,
                        )
                    finally:
                        with self._lock:
                            self._executing.discard(exec_key)
            finally:
                if acquired:
                    try:
                        browser = self._get_browser()
                        pool = getattr(browser, "_tab_pool", None)
                        if pool is not None and hasattr(pool, "release"):
                            pool.release(session.id, check_triggers=False)
                        else:
                            session.release(clear_page=False, check_triggers=False)
                    except Exception as e:
                        logger.debug(f"[CMD] deferred release failed (ignored): {e}")

        thread = threading.Thread(
            target=_run_deferred,
            daemon=True,
            name=f"cmd-resume-{getattr(session, 'id', 'tab')}",
        )
        thread.start()

    def schedule_deferred_workflow_commands(
        self,
        session: 'TabSession',
        *,
        delay_sec: float = 0.25,
    ):
        delay = max(0.0, float(delay_sec or 0.0))

        def _delayed_flush():
            if delay > 0:
                time.sleep(delay)
            self.flush_deferred_workflow_commands(session)

        thread = threading.Thread(
            target=_delayed_flush,
            daemon=True,
            name=f"cmd-resume-delay-{getattr(session, 'id', 'tab')}",
        )
        thread.start()

    def _get_workflow_interrupt_policy(self, command: Dict[str, Any]) -> str:
        trigger = command.get("trigger", {}) or {}
        policy = str(
            trigger.get("interrupt_policy", command.get("interrupt_policy", "auto"))
            or "auto"
        ).strip().lower()
        return policy if policy in {"auto", "resume", "abort"} else "auto"

    def _build_workflow_interrupt_message(self, command: Dict[str, Any]) -> str:
        trigger = command.get("trigger", {}) or {}
        custom = str(
            trigger.get("interrupt_message", command.get("interrupt_message", ""))
            or ""
        ).strip()
        if custom:
            return custom
        return f"触发 {command.get('name', '命令')}，后续工作流已打断，请重试"

    def _allow_command_during_workflow(self, command: Dict[str, Any]) -> bool:
        trigger = command.get("trigger", {}) or {}
        raw = trigger.get("allow_during_workflow", None)
        if raw is None:
            trigger_type = str(trigger.get("type", "")).strip().lower()
            if trigger_type != "page_check":
                return False
            policy = self._get_workflow_interrupt_policy(command)
            if policy in {"resume", "abort"}:
                return True
            return self._get_command_priority(command) > self._get_request_priority_baseline()
        return bool(raw)

    def _queue_workflow_interrupt(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
        chain: Optional[List[str]] = None,
        interrupt_context: Optional[Dict[str, Any]] = None,
        trigger_rollback: Optional[Dict[str, Any]] = None,
    ) -> bool:
        runtime = self._get_active_workflow_runtime(session)
        if not runtime or not runtime.get("active"):
            return False
        if not self._allow_command_during_workflow(command):
            return False

        queue_key = self._workflow_queue_key(command, trigger_rollback)
        if not queue_key:
            return False

        pending_ids = runtime.setdefault("pending_interrupt_ids", set())
        if queue_key in pending_ids:
            return False

        runtime.setdefault("pending_interrupts", []).append({
            "command": copy.deepcopy(command),
            "chain": list(chain or []),
            "interrupt_context": copy.deepcopy(interrupt_context or {}),
            "trigger_rollback": copy.deepcopy(trigger_rollback or {}),
            "queue_key": queue_key,
            "requested_at": time.time(),
            "priority": self._get_command_priority(command),
        })
        pending_ids.add(queue_key)
        setattr(session, "_workflow_stop_reason", "command_interrupt")
        logger.info(
            f"[CMD] 请求暂停工作流: {command.get('name')} "
            f"(标签页={session.id}, 工作流优先级={runtime.get('priority')}, "
            f"命令优先级={self._get_command_priority(command)})"
        )
        return True

    def _schedule_command_for_active_workflow(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
        chain: Optional[List[str]] = None,
        interrupt_context: Optional[Dict[str, Any]] = None,
        trigger_rollback: Optional[Dict[str, Any]] = None,
    ) -> bool:
        runtime = self._get_active_workflow_runtime(session)
        if not runtime or not runtime.get("active"):
            return False

        if self._allow_command_during_workflow(command):
            return self._queue_workflow_interrupt(
                command,
                session,
                chain=chain,
                interrupt_context=interrupt_context,
                trigger_rollback=trigger_rollback,
            )

        self._defer_command_until_workflow_resume(
            runtime,
            command,
            chain=chain,
            interrupt_context=interrupt_context,
            trigger_rollback=trigger_rollback,
        )
        logger.info(
            f"[CMD] 工作流忙碌，延后执行命令: {command.get('name')} "
            f"(标签页={session.id}, 工作流优先级={runtime.get('priority')}, "
            f"命令优先级={self._get_command_priority(command)})"
        )
        return True

    def workflow_interrupt_requested(self, session: 'TabSession') -> bool:
        runtime = self._get_active_workflow_runtime(session)
        if not runtime:
            return False
        return bool(runtime.get("pending_interrupts"))

    def _defer_command_until_workflow_resume(
        self,
        runtime: Dict[str, Any],
        command: Dict[str, Any],
        chain: Optional[List[str]] = None,
        interrupt_context: Optional[Dict[str, Any]] = None,
        trigger_rollback: Optional[Dict[str, Any]] = None,
    ):
        queue_key = self._workflow_queue_key(command, trigger_rollback)
        if not queue_key:
            return
        deferred_ids = runtime.setdefault("deferred_command_ids", set())
        if queue_key in deferred_ids:
            return
        runtime.setdefault("deferred_commands", []).append({
            "command": copy.deepcopy(command),
            "chain": list(chain or []),
            "interrupt_context": copy.deepcopy(interrupt_context or {}),
            "trigger_rollback": copy.deepcopy(trigger_rollback or {}),
            "queue_key": queue_key,
            "queued_at": time.time(),
        })
        deferred_ids.add(queue_key)

    def _mark_interrupt_abort(
        self,
        command: Dict[str, Any],
        interrupt_context: Dict[str, Any],
    ):
        if interrupt_context.get("abort"):
            return
        policy = self._get_workflow_interrupt_policy(command)
        priority = self._get_command_priority(command)
        workflow_priority = self._normalize_priority(
            interrupt_context.get("workflow_priority", self._get_request_priority_baseline()),
            self._get_request_priority_baseline(),
        )
        if policy == "abort" or (policy == "auto" and priority > workflow_priority):
            interrupt_context["abort"] = True
            interrupt_context["message"] = self._build_workflow_interrupt_message(command)
            interrupt_context["abort_by"] = command.get("name", "")

    def _dispatch_interrupt_followup_command(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
        chain: Optional[List[str]],
        interrupt_context: Dict[str, Any],
    ):
        runtime = interrupt_context.get("runtime") or {}
        workflow_priority = self._normalize_priority(
            interrupt_context.get("workflow_priority", self._get_request_priority_baseline()),
            self._get_request_priority_baseline(),
        )
        priority = self._get_command_priority(command)
        policy = self._get_workflow_interrupt_policy(command)

        if priority > workflow_priority or policy == "abort":
            logger.info(
                f"[CMD] 工作流暂停期间继续执行高优先级命令: {command.get('name')} "
                f"(标签页={session.id}, 命令优先级={priority}, 工作流优先级={workflow_priority})"
            )
            self._execute_command(command, session, chain=chain, interrupt_context=interrupt_context)
            self._mark_interrupt_abort(command, interrupt_context)
            return

        logger.info(
            f"[CMD] 延后命令至工作流恢复后执行: {command.get('name')} "
            f"(标签页={session.id}, 命令优先级={priority}, 工作流优先级={workflow_priority})"
        )
        self._defer_command_until_workflow_resume(
            runtime,
            command,
            chain=chain,
            interrupt_context=interrupt_context,
        )

    def handle_pending_workflow_interrupts(self, session: 'TabSession') -> Dict[str, Any]:
        runtime = self._get_active_workflow_runtime(session)
        if not runtime or runtime.get("interrupting"):
            return {"handled": False, "abort": False, "message": ""}

        pending = runtime.get("pending_interrupts") or []
        if not pending:
            return {"handled": False, "abort": False, "message": ""}

        runtime["interrupting"] = True
        interrupt_context = {
            "workflow_priority": runtime.get("priority", self._get_request_priority_baseline()),
            "runtime": runtime,
            "abort": False,
            "message": "",
            "abort_by": "",
        }

        try:
            while runtime.get("pending_interrupts"):
                queue = list(runtime.get("pending_interrupts") or [])
                queue.sort(key=lambda item: (-int(item.get("priority", 0) or 0), float(item.get("requested_at", 0.0) or 0.0)))
                item = queue.pop(0)
                cmd = copy.deepcopy(item.get("command") or {})
                chain = list(item.get("chain") or [])
                queue_key = str(item.get("queue_key", "") or "").strip() or self._workflow_queue_key(
                    cmd,
                    item.get("trigger_rollback") if isinstance(item.get("trigger_rollback"), dict) else None,
                )
                interrupt_payload = copy.deepcopy(item.get("interrupt_context") or {})

                runtime["pending_interrupts"] = [
                    existing for existing in runtime.get("pending_interrupts", [])
                    if (
                        str(existing.get("queue_key", "") or "").strip()
                        or self._workflow_queue_key(
                            (existing.get("command") or {}),
                            existing.get("trigger_rollback") if isinstance(existing.get("trigger_rollback"), dict) else None,
                        )
                    ) != queue_key
                ]
                runtime.setdefault("pending_interrupt_ids", set()).discard(queue_key)

                if not cmd or not cmd.get("enabled", True):
                    continue

                logger.info(
                    f"[CMD] 工作流已暂停，执行插队命令: {cmd.get('name')} "
                    f"(标签页={session.id}, 步骤={runtime.get('current_step_index')})"
                )
                merged_context = dict(interrupt_context)
                if interrupt_payload:
                    merged_context.update(interrupt_payload)
                self._execute_command(cmd, session, chain=chain, interrupt_context=merged_context)
                self._mark_interrupt_abort(cmd, interrupt_context)
                if interrupt_context.get("abort"):
                    runtime["pending_interrupts"] = []
                    runtime["pending_interrupt_ids"] = set()
                    break
        finally:
            runtime["interrupting"] = False

        if interrupt_context.get("abort"):
            setattr(session, "_workflow_stop_reason", "command_interrupt_abort")
        else:
            setattr(session, "_workflow_stop_reason", None)

        return {
            "handled": True,
            "abort": bool(interrupt_context.get("abort")),
            "message": str(interrupt_context.get("message", "") or ""),
            "abort_by": str(interrupt_context.get("abort_by", "") or ""),
        }
