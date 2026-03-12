"""
app/services/command_engine_storage.py - Command engine storage and CRUD mixin

Responsibilities:
- Command file loading and caching
- Command CRUD operations
- Command grouping metadata operations
"""

import copy
import json
import os
import re
from typing import Any, Dict, List, Optional

from app.core.config import atomic_write_json, get_logger
from app.services.command_defs import _new_command_id, get_default_command

logger = get_logger("CMD_ENG.STORAGE")


class CommandEngineStorageMixin:
    """Command storage and CRUD capabilities."""

    def _get_commands_file(self) -> str:
        if self._commands_file is None:
            from app.services.config_engine import ConfigConstants
            self._commands_file = ConfigConstants.COMMANDS_FILE
        return self._commands_file

    def _read_commands_file(self) -> List[Dict]:
        commands_file = self._get_commands_file()
        if not os.path.exists(commands_file):
            return []

        try:
            # Be tolerant to files rewritten by external tools such as PowerShell.
            # We still save JSON as UTF-8 without BOM, but accept BOM on read.
            with open(commands_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)

            if isinstance(data, dict):
                data = data.get("commands", [])

            if isinstance(data, list):
                return data

            logger.warning(f"命令配置文件格式无效: {commands_file}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"命令配置文件格式错误: {e}")
            return []
        except Exception as e:
            logger.error(f"加载命令配置失败: {e}")
            return []

    def _refresh_commands_if_changed(self, force: bool = False):
        commands_file = self._get_commands_file()
        current_mtime = os.path.getmtime(commands_file) if os.path.exists(commands_file) else 0.0

        if force or not self._commands_loaded or current_mtime != self._commands_mtime:
            self._commands_cache = self._read_commands_file()
            self._commands_mtime = current_mtime
            self._commands_loaded = True

    def _save_commands(self, commands: List[Dict]) -> bool:
        commands_file = self._get_commands_file()

        try:
            with self._commands_lock:
                commands_snapshot = copy.deepcopy(commands)
                os.makedirs(os.path.dirname(commands_file), exist_ok=True)
                atomic_write_json(commands_file, {"commands": commands_snapshot})
                self._commands_mtime = os.path.getmtime(commands_file) if os.path.exists(commands_file) else 0.0
                self._commands_loaded = True
                self._commands_cache = commands_snapshot
                return True
        except Exception as e:
            logger.error(f"????????: {e}")
            return False

    def _normalize_group_name(self, group_name: Any) -> str:
        return str(group_name or "").strip()

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

    # ================= CRUD =================

    def _load_commands(self) -> List[Dict]:
        """从配置引擎加载命令列表（可变引用）"""
        self._get_config_engine()
        self._refresh_commands_if_changed()
        return self._commands_cache

    def list_commands(self) -> List[Dict]:
        """获取所有命令（深拷贝）"""
        return copy.deepcopy(self._load_commands())

    def get_command(self, command_id: str) -> Optional[Dict]:
        for cmd in self.list_commands():
            if cmd.get("id") == command_id:
                return cmd
        return None

    def add_command(self, command: Dict = None) -> Dict:
        if command is None:
            command = get_default_command()
        else:
            if not command.get("id"):
                command["id"] = _new_command_id()

        with self._commands_lock:
            commands = self._load_commands()
            command["name"] = self._ensure_unique_command_name(command.get("name"), commands)
            command["group_name"] = self._normalize_group_name(command.get("group_name"))
            commands.append(command)
            self._save_commands(commands)

        logger.info(f"✅ 命令已添加: {command.get('name')} ({command['id']})")
        return copy.deepcopy(command)

    def update_command(self, command_id: str, updates: Dict) -> Optional[Dict]:
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
                    cmd.update(updates)
                    commands[i] = cmd
                    self._save_commands(commands)
                    logger.debug(f"✅ 命令已更新: {cmd.get('name')} ({command_id})")
                    return copy.deepcopy(cmd)

        return None

    def delete_command(self, command_id: str) -> bool:
        with self._commands_lock:
            commands = self._load_commands()
            new_commands = [c for c in commands if c.get("id") != command_id]

            if len(new_commands) == len(commands):
                return False

            self._save_commands(new_commands)

            # 清理触发状态
            with self._lock:
                keys_to_remove = [k for k in self._trigger_states if k[0] == command_id]
                for k in keys_to_remove:
                    del self._trigger_states[k]
                result_keys = [k for k in self._command_results if k[0] == command_id]
                for k in result_keys:
                    del self._command_results[k]

        logger.info(f"✅ 命令已删除: {command_id}")
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

            self._save_commands(new_commands)
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
            if updated > 0:
                self._save_commands(commands)

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
            if updated > 0:
                self._save_commands(commands)
        return updated

    def list_command_groups(self) -> List[Dict[str, Any]]:
        groups: Dict[str, Dict[str, Any]] = {}
        for cmd in self.list_commands():
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

