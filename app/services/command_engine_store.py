"""CommandEngine 的一部分（R2-3 拆分）：命令的读写与规范化：commands.json / 本地状态、运行统计合并、命名去重、run_js_file 清理。

方法原样从 command_engine.py 迁出，行为不变；通过 mixin 组合回 CommandEngine，
类属性与实例状态都在 CommandEngine（command_engine.py）里定义。
"""

import copy
import json
import os
import re
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from app.core.config import command_log_context
from app.services.command_engine_common import FOLLOW_DEFAULT_PRESET, logger

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession  # noqa: F401


class CommandEngineStoreMixin:
    """命令的读写与规范化：commands.json / 本地状态、运行统计合并、命名去重、run_js_file 清理"""

    def _get_commands_file(self) -> str:
        if self._commands_file is None:
            from app.services.config_engine import ConfigConstants
            self._commands_file = ConfigConstants.COMMANDS_FILE
        return self._commands_file
    def _get_commands_local_file(self) -> str:
        if self._commands_local_file is None:
            from app.services.config_engine import ConfigConstants
            self._commands_local_file = ConfigConstants.COMMANDS_LOCAL_FILE
        return self._commands_local_file
    def _read_commands_file(self) -> Optional[List[Dict]]:
        """读取命令配置。

        B8：返回 ``None`` 表示「读取失败」（JSON 损坏、结构不对、IO 错误），调用方必须保留
        last-known-good；只有文件不存在或合法的空列表才返回 ``[]``。旧实现把两者混为 ``[]``，
        编辑器半写入等短暂损坏会清空全部命令并触发 run_js_file 清理。
        """
        commands_file = self._get_commands_file()
        commands = []

        if os.path.exists(commands_file):
            try:
                with open(commands_file, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    data = data.get("commands", [])

                if isinstance(data, list):
                    commands = [entry for entry in data if isinstance(entry, dict)]
                    for entry in commands:
                        self._normalize_command_logging(entry)
                        entry["advanced_ui"] = self._normalize_advanced_ui(entry.get("advanced_ui"))
                else:
                    logger.warning(f"命令配置文件格式无效: {commands_file}")
                    return None
            except json.JSONDecodeError as e:
                logger.error(f"命令配置文件格式错误: {e}")
                return None
            except Exception as e:
                logger.error(f"加载命令配置失败: {e}")
                return None

        return self._apply_local_command_state(commands)
    def _load_command_state_entries(self) -> Optional[List[Dict[str, Any]]]:
        local_file = self._get_commands_local_file()
        if not os.path.exists(local_file):
            self._commands_local_mtime = 0.0
            return []

        try:
            with open(local_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)

            self._commands_local_mtime = os.path.getmtime(local_file)
            if not isinstance(data, dict):
                logger.error(f"本地命令状态文件格式无效: {local_file}")
                return None
            entries = data.get("commands", [])
            if not isinstance(entries, list):
                logger.error(f"本地命令状态 commands 字段格式无效: {local_file}")
                return None
            return [entry for entry in entries if isinstance(entry, dict)]
        except json.JSONDecodeError as e:
            logger.error(f"本地命令状态文件格式错误: {e}")
            return None
        except Exception as e:
            logger.error(f"加载本地命令状态失败: {e}")
            return None
    def _fallback_local_command_state_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for cmd in self._commands_cache or []:
            if not isinstance(cmd, dict):
                continue
            entries.append({
                "id": str(cmd.get("id", "")).strip(),
                "name": str(cmd.get("name", "")).strip(),
                "enabled": bool(cmd.get("enabled", True)),
                "group_name": self._normalize_group_name(cmd.get("group_name")),
            })
        if entries:
            logger.warning("本地命令状态读取失败，沿用内存中的上一轮状态覆盖")
        return entries
    def _apply_local_command_state(self, commands: List[Dict]) -> List[Dict]:
        entries = self._load_command_state_entries()
        if entries is None:
            entries = self._fallback_local_command_state_entries()
        if not entries or not commands:
            return commands

        by_id = {}
        by_name = {}
        for entry in entries:
            command_id = str(entry.get("id", "")).strip()
            command_name = str(entry.get("name", "")).strip()
            if command_id:
                by_id[command_id] = entry
            if command_name:
                by_name[command_name] = entry

        applied = 0
        for cmd in commands:
            if not isinstance(cmd, dict):
                continue
            command_id = str(cmd.get("id", "")).strip()
            command_name = str(cmd.get("name", "")).strip()
            entry = by_id.get(command_id) or by_name.get(command_name)
            if not entry:
                continue
            if "enabled" in entry:
                cmd["enabled"] = bool(entry.get("enabled"))
            if "group_name" in entry:
                cmd["group_name"] = self._normalize_group_name(entry.get("group_name"))
            applied += 1

        if applied > 0:
            logger.debug(f"已应用 {applied} 条本地命令状态覆盖")
        return commands
    def _save_local_command_state(self, commands: List[Dict[str, Any]]) -> bool:
        local_file = self._get_commands_local_file()
        tmp_file = local_file + ".tmp"
        entries = []
        for cmd in commands:
            if not isinstance(cmd, dict):
                continue
            entries.append({
                "id": str(cmd.get("id", "")).strip(),
                "name": str(cmd.get("name", "")).strip(),
                "enabled": bool(cmd.get("enabled", True)),
                "group_name": self._normalize_group_name(cmd.get("group_name")),
            })

        try:
            os.makedirs(os.path.dirname(local_file), exist_ok=True)
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump({"commands": entries}, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_file, local_file)
            try:
                self._commands_local_mtime = os.path.getmtime(local_file) if os.path.exists(local_file) else 0.0
            except Exception as mtime_error:
                logger.warning(f"本地命令状态已保存但更新时间戳失败: {mtime_error}")
            return True
        except Exception as e:
            logger.error(f"保存本地命令状态失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
            return False
    def _snapshot_local_command_state(self) -> Optional[tuple[bool, bytes]]:
        local_file = self._get_commands_local_file()
        try:
            if not os.path.exists(local_file):
                return (False, b"")
            with open(local_file, "rb") as f:
                return (True, f.read())
        except Exception as e:
            logger.error(f"读取本地命令状态快照失败: {e}")
            return None
    def _restore_local_command_state(self, snapshot: Optional[tuple[bool, bytes]]) -> None:
        if snapshot is None:
            return

        local_file = self._get_commands_local_file()
        tmp_file = local_file + ".restore.tmp"
        existed, payload = snapshot
        try:
            if existed:
                os.makedirs(os.path.dirname(local_file), exist_ok=True)
                with open(tmp_file, "wb") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, local_file)
            elif os.path.exists(local_file):
                os.remove(local_file)
            try:
                self._commands_local_mtime = os.path.getmtime(local_file) if os.path.exists(local_file) else 0.0
            except Exception as mtime_error:
                logger.warning(f"本地命令状态已恢复但更新时间戳失败: {mtime_error}")
        except Exception as e:
            logger.error(f"恢复本地命令状态失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
    @staticmethod
    def _iter_run_js_file_actions(command: Optional[Dict[str, Any]]):
        for action in list((command or {}).get("actions") or []):
            if not isinstance(action, dict):
                continue
            if str(action.get("type", "")).strip().lower() != "run_js_file":
                continue
            yield action
    def _collect_enabled_run_js_file_keys(
        self,
        commands: List[Dict[str, Any]],
        *,
        preinject_only: bool = False,
    ) -> set[str]:
        keys: set[str] = set()
        for command in commands or []:
            if not isinstance(command, dict) or not bool(command.get("enabled", True)):
                continue
            for action in self._iter_run_js_file_actions(command):
                if preinject_only and not self._coerce_action_bool(
                    action.get("inject_on_new_document", True),
                    True,
                ):
                    continue
                resolved_path = self._resolve_action_file_path(action.get("file_path", ""))
                if resolved_path:
                    keys.add(resolved_path.lower())
        return keys
    def _collect_run_js_file_cleanup_actions(
        self,
        previous_commands: List[Dict[str, Any]],
        next_commands: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        active_keys = self._collect_enabled_run_js_file_keys(next_commands)
        active_preinject_keys = self._collect_enabled_run_js_file_keys(
            next_commands,
            preinject_only=True,
        )
        next_by_id: Dict[str, Dict[str, Any]] = {}
        next_by_name: Dict[str, Dict[str, Any]] = {}
        for command in next_commands or []:
            if not isinstance(command, dict):
                continue
            command_id = str(command.get("id", "")).strip()
            command_name = str(command.get("name", "")).strip()
            if command_id:
                next_by_id[command_id] = command
            if command_name:
                next_by_name[command_name] = command

        cleanup_actions: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()
        for command in previous_commands or []:
            if not isinstance(command, dict) or not bool(command.get("enabled", True)):
                continue
            command_id = str(command.get("id", "")).strip()
            command_name = str(command.get("name", "")).strip()
            next_command = next_by_id.get(command_id) or next_by_name.get(command_name)
            command_still_enabled = next_command is not None and bool(next_command.get("enabled", True))
            for action in self._iter_run_js_file_actions(command):
                resolved_path = self._resolve_action_file_path(action.get("file_path", ""))
                action_key = resolved_path.lower() if resolved_path else f"{command_id}:{action.get('action_id', '')}"
                if command_still_enabled:
                    was_preinjected = self._coerce_action_bool(
                        action.get("inject_on_new_document", True),
                        True,
                    )
                    if not was_preinjected:
                        continue
                    if resolved_path and resolved_path.lower() in active_preinject_keys:
                        continue
                elif resolved_path and resolved_path.lower() in active_keys:
                    continue
                if action_key in seen_keys:
                    continue
                seen_keys.add(action_key)
                cleanup_actions.append(copy.deepcopy(action))
        return cleanup_actions
    def _cleanup_disabled_run_js_file_actions(self, actions: List[Dict[str, Any]]) -> None:
        if not actions:
            return

        try:
            browser = self._get_browser()
            pool = getattr(browser, "_tab_pool", None)
            if pool is None or not hasattr(pool, "get_sessions_snapshot"):
                return
            sessions = list(pool.get_sessions_snapshot() or [])
        except Exception as e:
            logger.debug(f"[CMD] run_js_file 清理前获取会话失败（忽略）: {e}")
            return

        cleaned = 0
        for session in sessions:
            for action in actions:
                try:
                    result = self._cleanup_run_js_file_action(
                        session,
                        action,
                        reason="command_disabled",
                    )
                    if result.get("removed_init_script") or result.get("ran_teardown"):
                        cleaned += 1
                    if not result.get("ok", True):
                        logger.debug(
                            f"[CMD] run_js_file 清理存在告警: "
                            f"session={getattr(session, 'id', '')}, path={result.get('path', '')}, "
                            f"errors={result.get('errors', [])}"
                        )
                except Exception as e:
                    logger.debug(
                        f"[CMD] run_js_file 清理失败（忽略）: "
                        f"session={getattr(session, 'id', '')}, error={e}"
                    )

        if cleaned > 0:
            logger.info(f"[CMD] 已清理 {cleaned} 个 run_js_file 注入实例")
    def _refresh_commands_if_changed(self, force: bool = False):
        with self._commands_lock:
            commands_file = self._get_commands_file()
            current_mtime = os.path.getmtime(commands_file) if os.path.exists(commands_file) else 0.0
            commands_local_file = self._get_commands_local_file()
            current_local_mtime = os.path.getmtime(commands_local_file) if os.path.exists(commands_local_file) else 0.0

            if (
                force
                or not self._commands_loaded
                or current_mtime != self._commands_mtime
                or current_local_mtime != self._commands_local_mtime
            ):
                if (
                    not force
                    and self._commands_loaded
                    and self._commands_failed_mtime is not None
                    and current_mtime == self._commands_failed_mtime
                    and current_local_mtime == self._commands_local_mtime
                ):
                    # 同一份损坏文件已判定过失败：继续使用 last-known-good，等待文件被修好
                    return
                previous_snapshot = copy.deepcopy(self._commands_cache) if self._commands_loaded else []
                next_snapshot = self._read_commands_file()
                if next_snapshot is None:
                    # B8：读失败 ≠ 合法空配置。保留上一版命令与预注入脚本，不推进 mtime、不做清理；
                    # 记录失败 mtime 以免每次轮询重复读取/刷日志，文件修复（mtime 变化）后自动重载。
                    self._commands_failed_mtime = current_mtime
                    if self._commands_loaded:
                        logger.warning(
                            f"[CMD] 命令配置读取失败，继续使用上一版 {len(self._commands_cache)} 条命令"
                            "（修复 commands.json 后将自动重新加载）"
                        )
                    else:
                        # 首次加载就失败：没有 last-known-good，只能以空配置启动，但不标记为已加载成功的 mtime
                        self._commands_cache = []
                        self._commands_loaded = True
                    return
                self._commands_failed_mtime = None
                cleanup_actions = self._collect_run_js_file_cleanup_actions(previous_snapshot, next_snapshot)
                self._commands_cache = next_snapshot
                self._commands_mtime = current_mtime
                self._commands_loaded = True
                self._cleanup_disabled_run_js_file_actions(cleanup_actions)
    def _save_commands(self, commands: List[Dict]) -> bool:
        commands_file = self._get_commands_file()
        tmp_file = commands_file + ".tmp"
        local_snapshot: Optional[tuple[bool, bytes]] = None
        local_state_written = False

        try:
            with self._commands_lock:
                commands_snapshot = copy.deepcopy(commands)
                previous_snapshot = copy.deepcopy(self._commands_cache) if self._commands_loaded else []
                os.makedirs(os.path.dirname(commands_file), exist_ok=True)
                local_snapshot = self._snapshot_local_command_state()
                if local_snapshot is None:
                    return False
                if not self._save_local_command_state(commands_snapshot):
                    self._restore_local_command_state(local_snapshot)
                    return False
                local_state_written = True
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump({"commands": commands_snapshot}, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(tmp_file, commands_file)
                try:
                    self._commands_mtime = os.path.getmtime(commands_file) if os.path.exists(commands_file) else 0.0
                except Exception as mtime_error:
                    logger.warning(f"命令配置已保存但更新时间戳失败: {mtime_error}")
                self._commands_loaded = True
                self._commands_failed_mtime = None
                self._commands_cache = commands_snapshot
                cleanup_actions = self._collect_run_js_file_cleanup_actions(previous_snapshot, commands_snapshot)
                self._cleanup_disabled_run_js_file_actions(cleanup_actions)
                return True
        except Exception as e:
            logger.error(f"保存命令配置失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
            if local_state_written:
                self._restore_local_command_state(local_snapshot)
            return False
    def _runtime_stats_for_command_ids(self, command_ids: set[str]) -> Dict[str, Dict[str, Any]]:
        if not command_ids:
            return {}
        with self._lock:
            return {
                command_id: copy.deepcopy(stats)
                for command_id, stats in self._command_runtime_stats.items()
                if command_id in command_ids and isinstance(stats, dict)
            }
    def _merge_runtime_stats_into_commands(self, commands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not commands:
            return commands
        command_ids = {
            str(cmd.get("id", "")).strip()
            for cmd in commands
            if isinstance(cmd, dict) and str(cmd.get("id", "")).strip()
        }
        runtime_stats = self._runtime_stats_for_command_ids(command_ids)
        if not runtime_stats:
            return commands
        for cmd in commands:
            cmd_id = str(cmd.get("id", "")).strip()
            if not cmd_id:
                continue
            stats = runtime_stats.get(cmd_id)
            if not stats:
                continue
            cmd.update(stats)
        return commands
    def _merge_runtime_stats_into_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        command_id = str((command or {}).get("id", "")).strip()
        if not command_id:
            return command
        runtime_stats = self._runtime_stats_for_command_ids({command_id})
        stats = runtime_stats.get(command_id)
        if stats:
            command.update(stats)
        return command
    def _normalize_group_name(self, group_name: Any) -> str:
        return str(group_name or "").strip()
    @staticmethod
    def _normalize_advanced_ui(ui: Any) -> Dict[str, Any]:
        if not isinstance(ui, dict):
            return {}

        kind = str(ui.get("kind") or "none").strip().lower()
        if kind not in {"none", "form"}:
            kind = "none"

        title = str(ui.get("title") or "").strip()
        description = str(ui.get("description") or "").strip()

        fields = ui.get("fields")
        if not isinstance(fields, list):
            fields = []
        normalized_fields: List[Dict[str, Any]] = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            field_type = str(field.get("type") or "text").strip().lower()
            if field_type not in {"text", "textarea", "number", "boolean", "select", "password", "rule_list", "image"}:
                continue
            options = field.get("options")
            if not isinstance(options, list):
                options = []
            rows = field.get("rows")
            try:
                rows = int(rows)
            except Exception:
                rows = rows
            normalized_fields.append({
                "key": str(field.get("key") or "").strip(),
                "label": str(field.get("label") or "").strip(),
                "type": field_type,
                "default": field.get("default"),
                "help": str(field.get("help") or "").strip(),
                "placeholder": str(field.get("placeholder") or "").strip(),
                "required": bool(field.get("required", False)),
                "options": options,
                "rows": rows,
                "item_defaults": field.get("item_defaults") if isinstance(field.get("item_defaults"), dict) else {},
            })

        values = ui.get("values")
        if not isinstance(values, dict):
            values = {}

        return {
            "kind": kind,
            "title": title,
            "description": description,
            "results_enabled": bool(ui.get("results_enabled", False)),
            "fields": normalized_fields,
            "values": values,
        }
    @staticmethod
    def _coerce_bool_flag(value: Any, default: bool = True) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return default
    @staticmethod
    def _normalize_command_log_level(value: Any) -> str:
        level = str(value or "GLOBAL").strip().upper()
        return level if level in {"GLOBAL", "DEBUG", "INFO", "WARNING", "ERROR"} else "GLOBAL"
    def _normalize_command_logging(self, command: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(command, dict):
            return command
        command["log_enabled"] = self._coerce_bool_flag(command.get("log_enabled", True), True)
        command["log_level"] = self._normalize_command_log_level(command.get("log_level"))
        return command
    def _get_command_log_context(self, command: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        normalized = self._normalize_command_logging(command if isinstance(command, dict) else {}) or {}
        return {
            "enabled": bool(normalized.get("log_enabled", True)),
            "level": self._normalize_command_log_level(normalized.get("log_level")),
        }
    def _command_logging_context(self, command: Optional[Dict[str, Any]]):
        return command_log_context(self._get_command_log_context(command))
    @staticmethod
    def _normalize_group_acquire_policy(value: Any) -> str:
        policy = str(value or "inherit_session").strip().lower()
        return policy if policy in {"inherit_session", "try_acquire", "require_acquire"} else "inherit_session"
    def _repair_mojibake_text(self, text: Any) -> str:
        value = str(text or "").strip()
        if not value:
            return ""

        candidates = [value]
        for source_encoding in ("latin-1", "cp1252"):
            try:
                repaired = value.encode(source_encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if repaired and repaired not in candidates:
                candidates.append(repaired)
        return candidates[-1]
    def _should_follow_default_preset(self, preset_name: Any) -> bool:
        value = str(preset_name or "").strip()
        return value in {"", FOLLOW_DEFAULT_PRESET}
    def _resolve_preset_name(self, preset_name: Any, session: Optional['TabSession'] = None) -> str:
        if self._should_follow_default_preset(preset_name):
            return ""

        raw_name = str(preset_name or "").strip()
        if not raw_name:
            return ""

        repaired_name = self._repair_mojibake_text(raw_name)
        if repaired_name == raw_name:
            return raw_name

        domain = str(getattr(session, "current_domain", "") or "").strip() if session else ""
        if not domain:
            logger.warning(f"[CMD] 检测到乱码预设名，已自动修正: {raw_name} -> {repaired_name}")
            return repaired_name

        try:
            config_engine = self._get_config_engine()
            site = getattr(config_engine, "sites", {}).get(domain, {}) or {}
            presets = site.get("presets", {}) or {}
            if not presets or repaired_name in presets:
                logger.warning(f"[CMD] 检测到乱码预设名，已自动修正: {raw_name} -> {repaired_name}")
                return repaired_name
        except Exception as e:
            logger.debug(f"[CMD] 预设名乱码修正校验失败（忽略）: {e}")

        return raw_name
    def _ensure_unique_command_name(
        self,
        raw_name: Any,
        commands: List[Dict[str, Any]],
        exclude_id: Optional[str] = None,
    ) -> str:
        existing = {
            str(cmd.get("name", "")).strip()
            for cmd in commands
            if cmd.get("id") != exclude_id and str(cmd.get("name", "")).strip()
        }

        base_name = str(raw_name or "").strip() or "新命令"
        if base_name != "新命令" and base_name not in existing:
            return base_name

        root = re.sub(r"\d+$", "", base_name).rstrip() or "新命令"
        pattern = re.compile(rf"^{re.escape(root)}(\d+)$")
        next_num = 1
        for name in existing:
            match = pattern.match(name)
            if match:
                next_num = max(next_num, int(match.group(1)) + 1)

        candidate = f"{root}{next_num}"
        while candidate in existing:
            next_num += 1
            candidate = f"{root}{next_num}"
        return candidate
    @staticmethod
    def _ensure_unique_copy_name(raw_name: Any, existing_names: set[str], max_length: int = 100) -> str:
        source_name = str(raw_name or "").strip() or "未命名"
        root = re.sub(r"\s*-\s*副本(?:\s+\d+)?$", "", source_name).strip() or source_name

        def build_candidate(copy_number: int) -> str:
            suffix = " - 副本" if copy_number == 1 else f" - 副本 {copy_number}"
            available = max(1, max_length - len(suffix))
            return f"{root[:available].rstrip()}{suffix}"

        copy_number = 1
        candidate = build_candidate(copy_number)
        while candidate in existing_names:
            copy_number += 1
            candidate = build_candidate(copy_number)
        return candidate
    @staticmethod
    def _remap_duplicated_command_references(
        command: Dict[str, Any],
        command_id_map: Dict[str, str],
        source_group_name: str = "",
        target_group_name: str = "",
    ) -> None:
        trigger = command.get("trigger")
        if isinstance(trigger, dict):
            source_id = str(trigger.get("command_id") or "").strip()
            if source_id in command_id_map:
                trigger["command_id"] = command_id_map[source_id]

            source_ids = trigger.get("command_ids")
            if isinstance(source_ids, list):
                trigger["command_ids"] = [
                    command_id_map.get(str(command_id or "").strip(), command_id)
                    for command_id in source_ids
                ]

        if not source_group_name or not target_group_name:
            return
        for action in command.get("actions") or []:
            if not isinstance(action, dict) or action.get("type") != "execute_command_group":
                continue
            if str(action.get("group_name") or "").strip() == source_group_name:
                action["group_name"] = target_group_name
    def _load_commands(self) -> List[Dict]:
        """从配置引擎加载命令列表原始快照，避免共享可变引用。"""
        with self._commands_lock:
            self._get_config_engine()
            self._refresh_commands_if_changed()
            snapshot = self._commands_cache
        return copy.deepcopy(snapshot)
    def _load_commands_for_checks(self) -> List[Dict]:
        """Load a lightweight command snapshot for read-mostly trigger checks."""
        with self._commands_lock:
            self._get_config_engine()
            self._refresh_commands_if_changed()
            snapshot = self._commands_cache
            commands = [dict(cmd) for cmd in snapshot if isinstance(cmd, dict)]
        return commands
