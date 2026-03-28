import copy
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import urlsplit

from app.core.config import get_logger

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession


logger = get_logger("CMD_ENG")


class CommandEngineResultsMixin:
    @staticmethod
    def _normalize_domain_host(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        try:
            if "://" in text:
                parsed = urlsplit(text)
                text = parsed.hostname or text
            else:
                text = text.split("/", 1)[0]
        except Exception:
            pass
        if text.count(":") == 1:
            host, port = text.rsplit(":", 1)
            if port.isdigit():
                text = host
        return text.strip().strip(".")

    def _domain_matches(self, target_domain: Any, actual_domain: Any) -> bool:
        target = self._normalize_domain_host(target_domain)
        actual = self._normalize_domain_host(actual_domain)
        if not target or not actual:
            return False
        return actual == target or actual.endswith(f".{target}")

    def _stringify_result(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple)):
            try:
                return json.dumps(value, ensure_ascii=False, sort_keys=True)
            except Exception:
                return str(value)
        return str(value)

    def _match_value_rule(self, actual: str, expected: str, rule: str) -> bool:
        normalized_rule = self._normalize_match_rule(rule)
        if normalized_rule == "contains":
            if not expected:
                return False
            return expected in actual
        if normalized_rule == "not_equals":
            return actual != expected
        return actual == expected

    def _get_command_result(self, command_id: str, tab_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._command_results.get((command_id, tab_id)))

    def _match_command_result_trigger(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
        consume: bool = False,
    ) -> bool:
        trigger = command.get("trigger", {})
        source_id = str(trigger.get("command_id", "")).strip()
        if not source_id:
            return False

        result_entry = self._get_command_result(source_id, session.id)
        if not result_entry:
            return False

        action_ref = str(trigger.get("action_ref", "")).strip()
        actual = result_entry.get("result", "")
        if action_ref:
            actual = result_entry.get("step_results", {}).get(action_ref, actual)

        expected = self._stringify_result(trigger.get("expected_value", ""))
        rule = trigger.get("match_rule", "equals")
        if not self._match_value_rule(self._stringify_result(actual), expected, rule):
            return False

        if consume:
            state, _ = self._ensure_trigger_state(command["id"], session)
            token = str(result_entry.get("token", ""))
            if state.get("result_token") == token:
                return False
            with self._lock:
                state["result_token"] = token

        return True

    def _prepare_command_result_trigger_dispatch(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
    ) -> Optional[Dict[str, Any]]:
        trigger = command.get("trigger", {})
        source_id = str(trigger.get("command_id", "")).strip()
        if not source_id:
            return None

        result_entry = self._get_command_result(source_id, session.id)
        if not result_entry:
            return None

        action_ref = str(trigger.get("action_ref", "")).strip()
        actual = result_entry.get("result", "")
        if action_ref:
            actual = result_entry.get("step_results", {}).get(action_ref, actual)

        expected = self._stringify_result(trigger.get("expected_value", ""))
        rule = trigger.get("match_rule", "equals")
        if not self._match_value_rule(self._stringify_result(actual), expected, rule):
            return None

        token = str(result_entry.get("token", ""))
        state, _ = self._ensure_trigger_state(command["id"], session)
        with self._lock:
            if state.get("result_token") == token:
                return None
            state["result_token"] = token

        return {
            "rollback": {"kind": "result_token", "token": token},
        }

    def _record_command_result(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
        execution_result: Dict[str, Any],
    ):
        if not command.get("id"):
            return

        token = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        step_results = {}
        for step in execution_result.get("steps", []) or []:
            ref = str(step.get("action_ref", "")).strip()
            if not ref:
                continue
            step_results[ref] = self._stringify_result(step.get("result", ""))

        entry = {
            "token": token,
            "timestamp": time.time(),
            "result": self._stringify_result(execution_result.get("result", "")),
            "step_results": step_results,
            "mode": execution_result.get("mode", ""),
        }
        logger.debug(f"[CMD] 执行结果已记录: {command.get('name')} (标签页={session.id})")
        event = self._build_command_result_event(command, execution_result, entry, session)
        with self._lock:
            self._command_results[(command["id"], session.id)] = entry
            queue = list(self._command_result_events.get(session.id) or [])
            queue.append(event)
            self._command_result_events[session.id] = queue[-50:]

    def _build_command_result_event(
        self,
        command: Dict[str, Any],
        execution_result: Dict[str, Any],
        entry: Dict[str, Any],
        session: 'TabSession',
    ) -> Dict[str, Any]:
        summary, informative = self._summarize_command_result(command, execution_result, entry)
        return {
            "token": entry.get("token", ""),
            "timestamp": float(entry.get("timestamp", time.time()) or time.time()),
            "source_command_id": str(command.get("id", "")).strip(),
            "source_command_name": str(command.get("name", "")).strip(),
            "source_group_name": self._normalize_group_name(command.get("group_name")),
            "result": str(entry.get("result", "") or ""),
            "summary": summary,
            "informative": bool(informative),
            "mode": str(entry.get("mode", "") or ""),
            "task_id": str(getattr(session, "current_task_id", "") or ""),
        }

    def _summarize_command_result(
        self,
        command: Dict[str, Any],
        execution_result: Dict[str, Any],
        entry: Dict[str, Any],
    ) -> tuple[str, bool]:
        error_text = str(execution_result.get("error", "") or "").strip()
        if error_text:
            return (error_text, True)

        result_text = str(entry.get("result", "") or "").strip()
        if result_text:
            generic_prefixes = (
                "waited:",
                "page_refreshed",
                "cookies_cleared",
                "new_chat_clicked",
                "element_clicked:",
                "element_stealth_clicked:",
                "coordinates_clicked:",
                "navigated:",
                "preset_switched:",
            )
            informative_values = {"VERIFY", "BLACK", "READY", "UNKNOWN"}
            if result_text in informative_values:
                return (result_text, True)
            lowered = result_text.lower()
            if "failed" in lowered or "error" in lowered or "timeout" in lowered:
                return (result_text, True)
            if not lowered.startswith(generic_prefixes):
                return (result_text, True)

        for step in execution_result.get("steps", []) or []:
            if not bool(step.get("ok", True)):
                text = self._stringify_result(step.get("result", "")).strip()
                return (text or "步骤执行失败", True)

        cmd_name = str(command.get("name", "")).strip() or "命令"
        return (f"{cmd_name} 执行完成", False)

    def _get_latest_command_result_event(self, tab_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            items = self._command_result_events.get(tab_id) or []
            if not items:
                return None
            return copy.deepcopy(items[-1])

    def _get_command_result_events(self, tab_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(self._command_result_events.get(tab_id) or []))

    def emit_external_command_result_event(
        self,
        session: 'TabSession',
        *,
        source_command_id: str,
        source_command_name: str,
        summary: Any,
        result: Any = "",
        informative: bool = True,
        mode: str = "external",
        group_name: Any = "",
        trigger_commands: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if session is None:
            return None

        normalized_command_id = str(source_command_id or "").strip()
        normalized_name = str(source_command_name or "").strip()
        normalized_summary = str(summary or "").strip()
        if not normalized_command_id or not normalized_name or not normalized_summary:
            return None

        event = {
            "token": f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
            "timestamp": time.time(),
            "source_command_id": normalized_command_id,
            "source_command_name": normalized_name,
            "source_group_name": self._normalize_group_name(group_name),
            "result": self._stringify_result(result),
            "summary": normalized_summary,
            "informative": bool(informative),
            "mode": str(mode or "external").strip() or "external",
            "task_id": str(getattr(session, "current_task_id", "") or ""),
        }

        with self._lock:
            queue = list(self._command_result_events.get(session.id) or [])
            queue.append(copy.deepcopy(event))
            self._command_result_events[session.id] = queue[-50:]

        logger.info(
            f"[CMD] 外部结果事件已记录: {normalized_name} "
            f"(标签页={session.id}, 摘要={normalized_summary})"
        )

        if trigger_commands:
            pseudo_command = {
                "id": normalized_command_id,
                "name": normalized_name,
                "group_name": self._normalize_group_name(group_name),
            }
            self._trigger_result_event_commands(pseudo_command, session)

        return copy.deepcopy(event)

    def _parse_result_event_source_ids(self, trigger: Dict[str, Any]) -> tuple[bool, set[str]]:
        listen_all = bool(trigger.get("listen_all_commands", False))
        raw = trigger.get("command_ids", [])
        values = raw if isinstance(raw, list) else str(raw or "").split(",")
        normalized = {str(item or "").strip() for item in values if str(item or "").strip()}
        if "*" in normalized:
            listen_all = True
            normalized.discard("*")
        return listen_all, normalized

    @staticmethod
    def _get_consumed_history(state: Dict[str, Any], field: str) -> List[str]:
        history_field = f"{field}s"
        raw_history = state.get(history_field, [])
        if isinstance(raw_history, list):
            history = [str(item or "").strip() for item in raw_history if str(item or "").strip()]
        else:
            history = []
        latest = str(state.get(field, "") or "").strip()
        if latest and latest not in history:
            history.append(latest)
        return history[-50:]

    @staticmethod
    def _result_event_matches_trigger(trigger: Dict[str, Any], event: Dict[str, Any]) -> bool:
        listen_all = bool(trigger.get("listen_all_commands", False))
        raw = trigger.get("command_ids", [])
        values = raw if isinstance(raw, list) else str(raw or "").split(",")
        command_ids = {str(item or "").strip() for item in values if str(item or "").strip()}
        if "*" in command_ids:
            listen_all = True
            command_ids.discard("*")

        source_id = str(event.get("source_command_id", "")).strip()
        if not listen_all and command_ids and source_id not in command_ids:
            return False
        if not listen_all and not command_ids:
            return False
        if bool(trigger.get("informative_only", True)) and not bool(event.get("informative", False)):
            return False
        return True

    @staticmethod
    def _normalize_result_event_dedupe_fields(trigger: Dict[str, Any]) -> List[str]:
        raw = trigger.get("dedupe_key_fields", trigger.get("result_event_dedupe_fields", []))
        if isinstance(raw, str):
            values = str(raw or "").replace("，", ",").split(",")
        elif isinstance(raw, (list, tuple, set)):
            values = list(raw)
        else:
            values = []
        fields = [str(item or "").strip() for item in values if str(item or "").strip()]
        return fields

    def _build_result_event_dedupe_signature(
        self,
        trigger: Dict[str, Any],
        event: Dict[str, Any],
    ) -> str:
        fields = self._normalize_result_event_dedupe_fields(trigger)
        if not fields:
            return ""

        parts: List[str] = []
        for field in fields:
            value = event.get(field, "")
            parts.append(f"{field}={str(value or '').strip()}")
        return "|".join(parts)

    def _remember_consumed_token(
        self,
        state: Dict[str, Any],
        field: str,
        token: str,
    ) -> None:
        token = str(token or "").strip()
        if not token:
            return
        history = [item for item in self._get_consumed_history(state, field) if item != token]
        history.append(token)
        state[field] = token
        state[f"{field}s"] = history[-50:]

    def _get_next_command_result_event(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
        consume: bool = False,
    ) -> Optional[Dict[str, Any]]:
        trigger = command.get("trigger", {}) or {}
        state, _ = self._ensure_trigger_state(command["id"], session)
        with self._lock:
            items = list(self._command_result_events.get(session.id) or [])
            consumed = set(self._get_consumed_history(state, "result_event_token"))
            latched_signature = str(state.get("result_event_latch_sig", "") or "").strip()
            for event in items:
                token = str(event.get("token", "")).strip()
                if not token or token in consumed:
                    continue
                if not self._result_event_matches_trigger(trigger, event):
                    continue
                signature = self._build_result_event_dedupe_signature(trigger, event)
                if signature and signature == latched_signature:
                    if consume:
                        self._remember_consumed_token(state, "result_event_token", token)
                    continue
                event_copy = copy.deepcopy(event)
                if consume:
                    self._remember_consumed_token(state, "result_event_token", token)
                    if signature:
                        state["result_event_latch_sig"] = signature
                return event_copy
        return None

    def _match_command_result_event_trigger(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
        consume: bool = False,
    ) -> bool:
        return self._get_next_command_result_event(command, session, consume=consume) is not None

    def _prepare_command_result_event_dispatch(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
    ) -> Optional[Dict[str, Any]]:
        event = self._get_next_command_result_event(command, session, consume=True)
        if not event:
            return None
        token = str(event.get("token", "")).strip()

        return {
            "rollback": {"kind": "result_event_token", "token": token},
            "interrupt_context": {"command_result_event": copy.deepcopy(event)},
        }

    def _normalize_status_codes(self, raw_codes: Any) -> set[int]:
        if isinstance(raw_codes, (list, tuple, set)):
            values = raw_codes
        else:
            values = str(raw_codes or "").replace("，", ",").split(",")

        result: set[int] = set()
        for item in values:
            text = str(item).strip()
            if not text:
                continue
            try:
                result.add(int(text))
            except Exception:
                continue
        return result

    def _pattern_to_listen_hint(self, pattern: str, mode: str) -> str:
        raw = str(pattern or "").strip()
        if not raw:
            return ""

        if str(mode or "").strip().lower() == "regex":
            if "|" in raw:
                return "http"
            tokens = re.split(r"[\^\$\.\*\+\?\(\)\[\]\{\}\|\\]+", raw)
            tokens = [t.strip() for t in tokens if t and len(t.strip()) >= 3]
            if not tokens:
                wildcard_fallback = raw.replace(".*", "").replace("\\/", "/")
                wildcard_fallback = wildcard_fallback.strip("* ").strip()
                return wildcard_fallback[:120]
            tokens.sort(key=len, reverse=True)
            return tokens[0][:120]

        simplified = raw.strip("* ").strip()
        return simplified[:120]

    def _matches_url_rule(self, url: str, pattern: str, mode: str) -> bool:
        if not pattern:
            return True
        if mode == "regex":
            try:
                return bool(re.search(pattern, url, flags=re.IGNORECASE))
            except re.error:
                logger.warning(f"[CMD] 无效正则，回退通配/关键词匹配: {pattern}")
                wildcard = str(pattern).replace(".", r"\.").replace("*", ".*")
                try:
                    return bool(re.search(wildcard, url, flags=re.IGNORECASE))
                except re.error:
                    pass
                simplified = str(pattern).replace("*", "").strip()
                if simplified:
                    return simplified.lower() in url.lower()
        return pattern.lower() in url.lower()

    def _matches_network_trigger(self, trigger: Dict[str, Any], event: Dict[str, Any]) -> bool:
        url = str(event.get("url", "") or "")
        status = event.get("status")
        pattern = str(trigger.get("url_pattern") or trigger.get("value") or "").strip()
        match_mode = str(trigger.get("match_mode", "keyword")).strip().lower()
        codes = self._normalize_status_codes(trigger.get("status_codes", ""))

        if not self._matches_url_rule(url, pattern, match_mode):
            return False
        if codes and int(status or 0) not in codes:
            return False
        return True

    def _build_network_signature(self, event: Dict[str, Any]) -> str:
        event_id = str(event.get("event_id", "")).strip()
        if event_id:
            return event_id
        ts = str(event.get("timestamp", ""))
        return f"{ts}:{event.get('status')}:{event.get('url', '')}"

    def _consume_network_signature(self, command_id: str, session: 'TabSession', signature: str) -> bool:
        if not command_id:
            return False
        state, _ = self._ensure_trigger_state(command_id, session)
        with self._lock:
            if signature in self._get_consumed_history(state, "net_sig"):
                return False
            self._remember_consumed_token(state, "net_sig", signature)
        return True

    def _prepare_network_trigger_dispatch(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
        event: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        trigger = command.get("trigger", {}) or {}
        if event is None:
            for candidate in self._get_network_events(session.id):
                if not self._matches_network_trigger(trigger, candidate):
                    continue
                signature = self._build_network_signature(candidate)
                if not self._consume_network_signature(command.get("id"), session, signature):
                    continue
                event = candidate
                break
        else:
            if not self._matches_network_trigger(trigger, event):
                return None
            signature = self._build_network_signature(event)
            if not self._consume_network_signature(command.get("id"), session, signature):
                return None
        if event is None:
            return None
        signature = self._build_network_signature(event)

        return {
            "rollback": {"kind": "net_sig", "token": signature},
            "interrupt_context": {"network_event": copy.deepcopy(event)},
        }

    def _get_latest_network_event(self, tab_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            items = self._network_events.get(tab_id) or []
            if not items:
                return None
            return copy.deepcopy(items[-1])

    def _get_network_events(self, tab_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(self._network_events.get(tab_id) or []))

    def _matches_scope(self, command: Dict, session: 'TabSession') -> bool:
        trigger = command.get("trigger", {})
        scope = trigger.get("scope", "all")

        if scope == "domain":
            target_domain = str(trigger.get("domain", "") or "").strip().lower()
            session_domain = self._get_session_domain(session)
            if target_domain and session_domain:
                return self._domain_matches(target_domain, session_domain)
            return not target_domain

        if scope == "tab":
            target_index = trigger.get("tab_index")
            return target_index is None or session.persistent_index == target_index

        return True

    def _rollback_trigger_consumption(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
        rollback: Optional[Dict[str, Any]],
    ):
        if not rollback:
            return
        state, _ = self._ensure_trigger_state(command["id"], session)
        field_map = {
            "result_token": "result_token",
            "result_event_token": "result_event_token",
            "net_sig": "net_sig",
        }
        field = field_map.get(str(rollback.get("kind", "")).strip())
        token = str(rollback.get("token", "") or "")
        if not field or not token:
            return
        with self._lock:
            history = [item for item in self._get_consumed_history(state, field) if item != token]
            state[f"{field}s"] = history
            state[field] = history[-1] if history else ""

    def _trigger_chained_commands(
        self,
        source_command: Dict,
        session: 'TabSession',
        chain: Optional[List[str]] = None,
        interrupt_context: Optional[Dict[str, Any]] = None,
    ):
        source_id = source_command.get("id")
        if not source_id:
            return

        chain = list(chain or [])
        next_chain = chain + [source_id]

        try:
            commands = self._load_commands()
        except Exception as e:
            logger.debug(f"链式命令加载失败，跳过: {e}")
            return

        for cmd in commands:
            if not cmd.get("enabled", True):
                continue

            target_id = cmd.get("id")
            trigger = cmd.get("trigger", {})
            if trigger.get("type") != "command_triggered":
                continue
            if trigger.get("command_id") != source_id:
                continue
            if not target_id or target_id in next_chain:
                continue
            if not self._matches_scope(cmd, session):
                continue

            exec_key = (target_id, session.id)
            with self._lock:
                if exec_key in self._executing:
                    continue

            logger.info(
                f"[CMD] 链式触发: {source_command.get('name')} -> {cmd.get('name')} "
                f"(标签页={session.id})"
            )
            if interrupt_context is not None:
                self._dispatch_interrupt_followup_command(
                    cmd, session, next_chain, interrupt_context
                )
            else:
                self._execute_command_async(cmd, session, chain=next_chain)

    def _trigger_result_match_commands(
        self,
        source_command: Dict[str, Any],
        session: 'TabSession',
        chain: Optional[List[str]] = None,
        interrupt_context: Optional[Dict[str, Any]] = None,
    ):
        source_id = source_command.get("id")
        if not source_id:
            return

        chain = list(chain or [])
        next_chain = chain + [source_id]

        try:
            commands = self._load_commands()
        except Exception as e:
            logger.debug(f"条件分支命令加载失败，跳过: {e}")
            return

        for cmd in commands:
            if not cmd.get("enabled", True):
                continue

            target_id = cmd.get("id")
            trigger = cmd.get("trigger", {})
            if trigger.get("type") != "command_result_match":
                continue
            if str(trigger.get("command_id", "")).strip() != source_id:
                continue
            if not target_id or target_id in next_chain:
                continue
            if not self._matches_scope(cmd, session):
                continue
            dispatch = self._prepare_command_result_trigger_dispatch(cmd, session)
            if not dispatch:
                continue

            logger.info(
                f"[CMD] 条件分支触发: {source_command.get('name')} -> {cmd.get('name')} "
                f"(标签页={session.id})"
            )
            combined_context = copy.deepcopy(interrupt_context) if interrupt_context is not None else None
            extra_context = dispatch.get("interrupt_context")
            if extra_context:
                combined_context = combined_context or {}
                combined_context.update(copy.deepcopy(extra_context))
            if interrupt_context is not None:
                self._dispatch_interrupt_followup_command(
                    cmd, session, next_chain, combined_context or interrupt_context
                )
            else:
                self._execute_command_async(
                    cmd,
                    session,
                    chain=next_chain,
                    interrupt_context=combined_context,
                    trigger_rollback=dispatch.get("rollback"),
                )

    def _trigger_result_event_commands(
        self,
        source_command: Dict[str, Any],
        session: 'TabSession',
        chain: Optional[List[str]] = None,
        interrupt_context: Optional[Dict[str, Any]] = None,
    ):
        source_id = str(source_command.get("id", "")).strip()
        if not source_id:
            return

        chain = list(chain or [])
        next_chain = chain + [source_id]

        try:
            commands = self._load_commands()
        except Exception as e:
            logger.debug(f"结果事件命令加载失败，跳过: {e}")
            return

        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            target_id = str(cmd.get("id", "")).strip()
            if not target_id or target_id in next_chain:
                continue
            trigger = cmd.get("trigger", {}) or {}
            if str(trigger.get("type", "")).strip() != "command_result_event":
                continue
            if not self._matches_scope(cmd, session):
                continue
            dispatch = self._prepare_command_result_event_dispatch(cmd, session)
            if not dispatch:
                continue

            logger.info(
                f"[CMD] 结果事件触发: {source_command.get('name')} -> {cmd.get('name')} "
                f"(标签页={session.id})"
            )
            combined_context = copy.deepcopy(interrupt_context) if interrupt_context is not None else None
            extra_context = dispatch.get("interrupt_context")
            if extra_context:
                combined_context = combined_context or {}
                combined_context.update(copy.deepcopy(extra_context))
            if interrupt_context is not None:
                self._dispatch_interrupt_followup_command(
                    cmd, session, next_chain, combined_context or interrupt_context
                )
            else:
                self._execute_command_async(
                    cmd,
                    session,
                    chain=next_chain,
                    interrupt_context=combined_context,
                    trigger_rollback=dispatch.get("rollback"),
                )
