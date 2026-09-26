"""CommandEngine 的一部分（R2-3 拆分）：命令与分组的增删改查、分组执行与预览。

方法原样从 command_engine.py 迁出，行为不变；通过 mixin 组合回 CommandEngine，
类属性与实例状态都在 CommandEngine（command_engine.py）里定义。
"""

import copy
import uuid
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from app.services.command_defs import _new_command_id, get_default_command
from app.services.command_engine_common import logger

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession  # noqa: F401


class CommandEngineCrudMixin:
    """命令与分组的增删改查、分组执行与预览"""

    def list_commands(self) -> List[Dict]:
        """获取所有命令"""
        return self._merge_runtime_stats_into_commands(self._load_commands())
    def get_command(self, command_id: str) -> Optional[Dict]:
        command_key = str(command_id or "").strip()
        if not command_key:
            return None
        command = None
        with self._commands_lock:
            self._get_config_engine()
            self._refresh_commands_if_changed()
            for cmd in self._commands_cache:
                if not isinstance(cmd, dict):
                    continue
                if str(cmd.get("id", "")).strip() == command_key:
                    command = copy.deepcopy(cmd)
                    break
        if command is None:
            return None
        return self._merge_runtime_stats_into_command(command)
    def get_command_config(self, command_id: str) -> Optional[Dict]:
        """获取单条命令原始配置，不合并展示用运行态统计。"""
        command_key = str(command_id or "").strip()
        if not command_key:
            return None
        with self._commands_lock:
            self._get_config_engine()
            self._refresh_commands_if_changed()
            for cmd in self._commands_cache:
                if not isinstance(cmd, dict):
                    continue
                if str(cmd.get("id", "")).strip() == command_key:
                    return copy.deepcopy(cmd)
        return None
    def add_command(self, command: Dict = None) -> Optional[Dict]:
        if command is None:
            command = get_default_command()
        else:
            if not command.get("id"):
                command["id"] = _new_command_id()

        with self._commands_lock:
            commands = self._load_commands()
            command["name"] = self._ensure_unique_command_name(command.get("name"), commands)
            command["group_name"] = self._normalize_group_name(command.get("group_name"))
            command["advanced_ui"] = self._normalize_advanced_ui(command.get("advanced_ui"))
            self._normalize_command_logging(command)
            commands.append(command)
            if not self._save_commands(commands):
                return None

        logger.info(f"[OK] 命令已添加: {command.get('name')} ({command['id']})")
        return copy.deepcopy(command)
    def duplicate_command(self, command_id: str) -> Optional[Dict]:
        """复制单条命令，并将副本紧跟在原命令之后。"""
        command_key = str(command_id or "").strip()
        if not command_key:
            return None

        with self._commands_lock:
            commands = self._load_commands()
            source_index = next(
                (index for index, item in enumerate(commands) if item.get("id") == command_key),
                -1,
            )
            if source_index < 0:
                return None

            duplicated = copy.deepcopy(commands[source_index])
            duplicated["id"] = _new_command_id()
            duplicated["name"] = self._ensure_unique_copy_name(
                duplicated.get("name"),
                {str(item.get("name") or "").strip() for item in commands},
            )
            self._normalize_command_logging(duplicated)
            commands.insert(source_index + 1, duplicated)
            if not self._save_commands(commands):
                return None

        logger.info(f"[OK] 命令已复制: {command_key} -> {duplicated['id']}")
        return copy.deepcopy(duplicated)
    def duplicate_group(self, group_name: str) -> Optional[Dict[str, Any]]:
        """复制整个命令组，并重映射副本组内的命令引用。"""
        source_group = self._normalize_group_name(group_name)
        if not source_group:
            return None

        with self._commands_lock:
            commands = self._load_commands()
            source_commands = [
                item
                for item in commands
                if self._normalize_group_name(item.get("group_name")) == source_group
            ]
            if not source_commands:
                return None

            existing_group_names = {
                self._normalize_group_name(item.get("group_name"))
                for item in commands
                if self._normalize_group_name(item.get("group_name"))
            }
            target_group = self._ensure_unique_copy_name(source_group, existing_group_names)
            command_id_map = {
                str(item.get("id") or "").strip(): _new_command_id()
                for item in source_commands
                if str(item.get("id") or "").strip()
            }
            existing_command_names = {
                str(item.get("name") or "").strip()
                for item in commands
                if str(item.get("name") or "").strip()
            }
            duplicated_commands: List[Dict[str, Any]] = []

            for source in source_commands:
                duplicated = copy.deepcopy(source)
                source_id = str(source.get("id") or "").strip()
                duplicated["id"] = command_id_map.get(source_id) or _new_command_id()
                duplicated["name"] = self._ensure_unique_copy_name(
                    source.get("name"),
                    existing_command_names,
                )
                existing_command_names.add(duplicated["name"])
                duplicated["group_name"] = target_group
                self._remap_duplicated_command_references(
                    duplicated,
                    command_id_map,
                    source_group_name=source_group,
                    target_group_name=target_group,
                )
                self._normalize_command_logging(duplicated)
                duplicated_commands.append(duplicated)

            commands.extend(duplicated_commands)
            if not self._save_commands(commands):
                return None

        logger.info(
            f"[OK] 命令组已复制: {source_group} -> {target_group} "
            f"({len(duplicated_commands)} 条命令)"
        )
        return {
            "group_name": target_group,
            "commands": copy.deepcopy(duplicated_commands),
            "count": len(duplicated_commands),
        }
    def update_command(self, command_id: str, updates: Dict) -> Optional[Dict]:
        updates = dict(updates or {})
        with self._commands_lock:
            commands = self._load_commands()

            for i, cmd in enumerate(commands):
                if cmd.get("id") == command_id:
                    updates.pop("id", None)
                    if "name" in updates:
                        updates["name"] = self._ensure_unique_command_name(
                            updates.get("name"),
                            commands,
                            exclude_id=command_id,
                        )
                    if "group_name" in updates:
                        updates["group_name"] = self._normalize_group_name(updates.get("group_name"))
                    if "advanced_ui" in updates:
                        updates["advanced_ui"] = self._normalize_advanced_ui(updates.get("advanced_ui"))
                    cmd.update(updates)
                    self._normalize_command_logging(cmd)
                    commands[i] = cmd
                    if not self._save_commands(commands):
                        return None
                    logger.debug(f"[OK] 命令已更新: {cmd.get('name')} ({command_id})")
                    return copy.deepcopy(cmd)

        return None
    def delete_command(self, command_id: str) -> bool:
        with self._commands_lock:
            commands = self._load_commands()
            new_commands = [c for c in commands if c.get("id") != command_id]

            if len(new_commands) == len(commands):
                return False

            if not self._save_commands(new_commands):
                return False

            # 清理触发状态
            with self._lock:
                keys_to_remove = [k for k in self._trigger_states if k[0] == command_id]
                for k in keys_to_remove:
                    del self._trigger_states[k]
                result_keys = [k for k in self._command_results if k[0] == command_id]
                for k in result_keys:
                    del self._command_results[k]
                self._command_runtime_stats.pop(command_id, None)

        logger.info(f"[OK] 命令已删除: {command_id}")
        return True
    def reorder_commands(self, command_ids: List[str]) -> bool:
        with self._commands_lock:
            commands = self._load_commands()
            cmd_map = {c["id"]: c for c in commands}
            new_commands = []

            for cid in command_ids:
                if cid in cmd_map:
                    new_commands.append(cmd_map.pop(cid))

            for remaining in cmd_map.values():
                new_commands.append(remaining)

            if len(new_commands) == len(commands) and all(
                current is updated for current, updated in zip(commands, new_commands)
            ):
                return True

            return self._save_commands(new_commands)
        return True
    def set_commands_group(self, command_ids: List[str], group_name: str) -> int:
        """批量设置命令分组。group_name 为空时表示解散选中的命令。"""
        target_ids = {str(cid).strip() for cid in (command_ids or []) if str(cid).strip()}
        if not target_ids:
            return 0

        normalized_group = self._normalize_group_name(group_name)
        updated = 0

        with self._commands_lock:
            commands = self._load_commands()
            for cmd in commands:
                if cmd.get("id") not in target_ids:
                    continue
                if self._normalize_group_name(cmd.get("group_name")) == normalized_group:
                    continue
                cmd["group_name"] = normalized_group
                updated += 1
            if updated > 0 and not self._save_commands(commands):
                return -1

        return updated
    def rename_group(self, old_group_name: str, new_group_name: str) -> int:
        """重命名命令组。"""
        source_name = self._normalize_group_name(old_group_name)
        target_name = self._normalize_group_name(new_group_name)
        if not source_name or not target_name or source_name == target_name:
            return 0

        updated = 0
        with self._commands_lock:
            commands = self._load_commands()
            for cmd in commands:
                if self._normalize_group_name(cmd.get("group_name")) != source_name:
                    continue
                cmd["group_name"] = target_name
                updated += 1
            if updated > 0 and not self._save_commands(commands):
                return -1
        return updated
    def set_commands_enabled(self, command_ids: List[str], enabled: bool) -> int:
        """批量更新命令启用状态。"""
        target_ids = {str(cid).strip() for cid in (command_ids or []) if str(cid).strip()}
        if not target_ids:
            return 0

        desired_enabled = bool(enabled)
        updated = 0

        with self._commands_lock:
            commands = self._load_commands()
            for cmd in commands:
                if cmd.get("id") not in target_ids:
                    continue
                current_enabled = bool(cmd.get("enabled", True))
                if current_enabled == desired_enabled:
                    continue
                cmd["enabled"] = desired_enabled
                updated += 1
            if updated > 0 and not self._save_commands(commands):
                return -1

        return updated
    def disband_group(self, group_name: str) -> int:
        """解散整个命令组。"""
        normalized_group = self._normalize_group_name(group_name)
        if not normalized_group:
            return 0

        updated = 0
        with self._commands_lock:
            commands = self._load_commands()
            for cmd in commands:
                if self._normalize_group_name(cmd.get("group_name")) != normalized_group:
                    continue
                cmd["group_name"] = ""
                updated += 1
            if updated > 0 and not self._save_commands(commands):
                return -1
        return updated
    def set_group_enabled(self, group_name: str, enabled: bool) -> int:
        """直接更新整个命令组的启用状态。"""
        normalized_group = self._normalize_group_name(group_name)
        if not normalized_group:
            return 0

        desired_enabled = bool(enabled)
        updated = 0
        with self._commands_lock:
            commands = self._load_commands()
            for cmd in commands:
                if self._normalize_group_name(cmd.get("group_name")) != normalized_group:
                    continue
                current_enabled = bool(cmd.get("enabled", True))
                if current_enabled == desired_enabled:
                    continue
                cmd["enabled"] = desired_enabled
                updated += 1
            if updated > 0 and not self._save_commands(commands):
                return -1
        return updated
    def list_command_groups(self) -> List[Dict[str, Any]]:
        groups: Dict[str, Dict[str, Any]] = {}
        for cmd in self._load_commands_for_checks():
            group_name = self._normalize_group_name(cmd.get("group_name"))
            if not group_name:
                continue
            bucket = groups.setdefault(group_name, {
                "name": group_name,
                "count": 0,
                "enabled_count": 0,
                "command_ids": [],
            })
            bucket["count"] += 1
            bucket["enabled_count"] += 1 if cmd.get("enabled", True) else 0
            bucket["command_ids"].append(cmd.get("id"))

        return [groups[name] for name in sorted(groups.keys())]
    def execute_command_group(
        self,
        group_name: str,
        session: 'TabSession',
        include_disabled: bool = False,
        source_command_id: Optional[str] = None,
        ancestry_chain: Optional[List[str]] = None,
        acquire_policy: Optional[str] = None,
        prepared_plan: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """在当前会话中顺序执行命令组内的命令。"""
        normalized_group = self._normalize_group_name(group_name)
        if not normalized_group:
            return {"ok": False, "error": "empty_group_name"}
        effective_policy = self._normalize_group_acquire_policy(acquire_policy)

        plan = dict(prepared_plan or {}) if isinstance(prepared_plan, dict) else {}
        if not plan:
            plan = self.preview_command_group(
                group_name=normalized_group,
                session=session,
                include_disabled=include_disabled,
                source_command_id=source_command_id,
                ancestry_chain=ancestry_chain,
            )
        candidates: List[Dict[str, Any]] = list(plan.pop("_candidate_commands", []))
        results: List[Dict[str, Any]] = []
        initial_scope_skipped = int(plan.get("scope_skipped", 0) or 0)

        if not candidates:
            return {
                "ok": False,
                "error": "group_empty_or_no_runnable_commands",
                "group_name": normalized_group,
                "scope_skipped": initial_scope_skipped,
                "runnable_count": plan.get("runnable_count", 0),
            }

        chain_seed = list(ancestry_chain or [])
        if not chain_seed and source_command_id:
            chain_seed = [source_command_id]
        group_acquired = False
        group_task_id = f"group_{normalized_group}_{uuid.uuid4().hex[:12]}"
        can_acquire = hasattr(session, "acquire_for_command")
        if effective_policy != "inherit_session":
            if not can_acquire:
                if effective_policy == "require_acquire":
                    return {
                        "ok": False,
                        "error": "group_acquire_unavailable",
                        "group_name": normalized_group,
                        "acquire_policy": effective_policy,
                    }
            else:
                group_acquired = bool(session.acquire_for_command(group_task_id))
                if not group_acquired and effective_policy == "require_acquire":
                    return {
                        "ok": False,
                        "error": "group_acquire_failed",
                        "group_name": normalized_group,
                        "acquire_policy": effective_policy,
                    }

        try:
            for cmd in candidates:
                command_id = cmd.get("id")
                if not command_id:
                    continue
                if not self._matches_scope(cmd, session):
                    results.append({
                        "id": command_id,
                        "name": cmd.get("name", command_id),
                        "ok": False,
                        "error": "scope_mismatch",
                    })
                    continue
                exec_key = (command_id, session.id)
                with self._lock:
                    if exec_key in self._executing:
                        results.append({
                            "id": command_id,
                            "name": cmd.get("name", command_id),
                            "ok": False,
                            "error": "already_executing",
                        })
                        continue
                    self._executing.add(exec_key)
                with self._command_logging_context(cmd):
                    try:
                        execution_result = self._execute_command(cmd, session, chain=chain_seed)
                        command_ok = not self._execution_needs_page_check_retry(execution_result)
                        results.append({
                            "id": command_id,
                            "name": cmd.get("name", command_id),
                            "ok": command_ok,
                            "result": execution_result,
                            **({"error": "execution_not_ok"} if not command_ok else {}),
                        })
                    except Exception as e:
                        logger.error(f"trigger check failed [{cmd.get('name')}]: {e}")
                        results.append({
                            "id": command_id,
                            "name": cmd.get("name", command_id),
                            "ok": False,
                            "error": str(e),
                        })
                    finally:
                        with self._lock:
                            self._executing.discard(exec_key)
        finally:
            if group_acquired:
                try:
                    browser = self._get_browser()
                    pool = getattr(browser, "_tab_pool", None)
                    if pool is not None and hasattr(pool, "release"):
                        pool.release(
                            session.id,
                            check_triggers=False,
                            expected_task_id=group_task_id,
                        )
                    else:
                        session.release(
                            clear_page=False,
                            check_triggers=False,
                            expected_task_id=group_task_id,
                        )
                except Exception as e:
                    logger.debug(f"[CMD] 命令组释放标签页失败（忽略）: {e}")

        success_count = sum(1 for item in results if item.get("ok"))
        failure_count = sum(1 for item in results if not item.get("ok"))
        return {
            "ok": len(results) > 0 and failure_count == 0,
            "partial_ok": success_count > 0,
            "group_name": normalized_group,
            "executed": success_count,
            "total": len(results),
            "failures": failure_count,
            "results": results,
            "acquire_policy": effective_policy,
            "acquired": group_acquired,
            "scope_skipped": sum(1 for item in results if item.get("error") == "scope_mismatch"),
            "runnable_count": plan.get("runnable_count", 0),
        }
    def preview_command_group(
        self,
        group_name: str,
        session: 'TabSession',
        include_disabled: bool = False,
        source_command_id: Optional[str] = None,
        ancestry_chain: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        normalized_group = self._normalize_group_name(group_name)
        if not normalized_group:
            return {
                "ok": False,
                "error": "empty_group_name",
                "group_name": normalized_group,
                "_candidate_commands": [],
                "_scope_skipped_results": [],
            }

        with self._commands_lock:
            commands = self._load_commands()

        candidates: List[Dict[str, Any]] = []
        scope_skipped_results: List[Dict[str, Any]] = []
        total_candidates = 0
        ancestors = {str(item or "").strip() for item in (ancestry_chain or []) if str(item or "").strip()}
        if source_command_id:
            ancestors.add(str(source_command_id).strip())
        for cmd in commands:
            if self._normalize_group_name(cmd.get("group_name")) != normalized_group:
                continue
            if not include_disabled and not cmd.get("enabled", True):
                continue
            if str(cmd.get("id", "")).strip() in ancestors:
                continue
            total_candidates += 1
            candidates.append(cmd)
            if not self._matches_scope(cmd, session):
                scope_skipped_results.append({
                    "id": cmd.get("id"),
                    "name": cmd.get("name", cmd.get("id", "")),
                    "ok": False,
                    "error": "scope_mismatch",
                })
                continue

        return {
            "ok": bool(candidates) and not scope_skipped_results,
            "fully_runnable": bool(candidates) and not scope_skipped_results,
            "group_name": normalized_group,
            "total_candidates": total_candidates,
            "runnable_count": total_candidates - len(scope_skipped_results),
            "scope_skipped": len(scope_skipped_results),
            "_candidate_commands": candidates,
            "_scope_skipped_results": scope_skipped_results,
        }
