"""
app/services/command_engine.py - 命令引擎

职责：
- 命令的 CRUD 管理
- 触发条件检查（在标签页释放后调用）
- 动作执行调度
- 高级模式脚本执行（JavaScript / Python）

存储位置：config/commands.json
"""

import copy
import json
import os
import random
import re
import threading
import time
import uuid
from typing import Dict, List, Optional, Any, TYPE_CHECKING

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from app.core.config import get_logger

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession

logger = get_logger("CMD_ENG")


# ================= 常量 =================

TRIGGER_TYPES = {
    "request_count": "对话次数达到阈值",
    "error_count": "连续错误次数达到阈值",
    "idle_timeout": "标签页空闲超过指定时间（秒）",
    "page_check": "页面出现指定内容（如 Cloudflare 验证）",
    "command_triggered": "当指定命令触发后执行",
    "command_result_match": "命令执行结果匹配",
    "network_request_error": "网络请求异常拦截",
}

ACTION_TYPES = {
    "clear_cookies": "清除当前标签页的 Cookie",
    "refresh_page": "刷新页面",
    "new_chat": "点击新建对话按钮",
    "run_js": "在页面中执行 JavaScript",
    "wait": "等待指定秒数",
    "execute_preset": "切换预设",
    "execute_workflow": "执行工作流",
    "switch_preset": "切换标签页预设",
    "navigate": "导航到指定 URL",
    "switch_proxy": "切换代理节点（Clash）",
    "send_webhook": "发送 Webhook / 外部请求",
    "execute_command_group": "执行命令组",
    "abort_task": "中断当前任务",
    "release_tab_lock": "解除当前标签页占用",
}


class CommandFlowAbort(Exception):
    """用于中断当前命令后续动作的内部控制异常。"""
    pass


# ================= 工具函数 =================

def _new_command_id() -> str:
    return f"cmd_{uuid.uuid4().hex[:8]}"


def get_default_command() -> Dict[str, Any]:
    """获取默认命令结构"""
    return {
        "id": _new_command_id(),
        "name": "新命令",
        "enabled": True,
        "mode": "simple",
        "trigger": {
            "type": "request_count",
            "value": 10,
            "command_id": "",
            "action_ref": "",
            "match_rule": "equals",
            "expected_value": "",
            "match_mode": "keyword",
            "status_codes": "403,429,500,502,503,504",
            "abort_on_match": True,
            "scope": "all",
            "domain": "",
            "tab_index": None,
        },
        "actions": [
            {"type": "clear_cookies"},
            {"type": "refresh_page"},
        ],
        "group_name": "",
        "script": "",
        "script_lang": "javascript",
        "last_triggered": None,
        "trigger_count": 0,
    }


# ================= 命令引擎 =================

class CommandEngine:
    """命令引擎"""

    def __init__(self):
        self._config_engine = None
        self._browser = None
        self._commands_file = None
        self._commands_mtime = 0.0
        self._commands_loaded = False
        self._commands_cache: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._commands_lock = threading.RLock()

        # 触发状态：{(command_id, tab_id): {"req": int, "err": int, ...}}
        self._trigger_states: Dict[tuple, Dict[str, Any]] = {}
        # 最近命令执行结果：{(source_command_id, tab_id): {...}}
        self._command_results: Dict[tuple, Dict[str, Any]] = {}
        # 最近网络事件：{tab_id: [event, ...]}
        self._network_events: Dict[str, List[Dict[str, Any]]] = {}
        # 正在执行的命令（防止重复触发）
        self._executing: set = set()

        logger.debug("CommandEngine 初始化")

    # ================= 延迟依赖 =================

    def _get_config_engine(self):
        if self._config_engine is None:
            from app.services.config_engine import config_engine
            self._config_engine = config_engine
        return self._config_engine

    def _get_browser(self):
        if self._browser is None:
            from app.core.browser import get_browser
            self._browser = get_browser(auto_connect=False)
        return self._browser

    def _suspend_tab_global_network(self, session: 'TabSession', reason: str = "command"):
        """命令执行期间暂停标签页全局网络监听，避免和工作流监听冲突。"""
        try:
            browser = self._get_browser()
            pool = getattr(browser, "_tab_pool", None)
            if pool is not None and hasattr(pool, "suspend_global_network_monitor"):
                pool.suspend_global_network_monitor(session.id, reason=reason)
        except Exception as e:
            logger.debug(f"[CMD] 暂停全局网络监听失败（忽略）: {e}")

    def _resume_tab_global_network(self, session: 'TabSession', reason: str = "command"):
        """命令执行结束后恢复标签页全局网络监听。"""
        try:
            browser = self._get_browser()
            pool = getattr(browser, "_tab_pool", None)
            if pool is not None and hasattr(pool, "resume_global_network_monitor"):
                pool.resume_global_network_monitor(session.id, reason=reason)
        except Exception as e:
            logger.debug(f"[CMD] 恢复全局网络监听失败（忽略）: {e}")

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
            with open(commands_file, "r", encoding="utf-8") as f:
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
        tmp_file = commands_file + ".tmp"

        try:
            with self._commands_lock:
                commands_snapshot = copy.deepcopy(commands)
                os.makedirs(os.path.dirname(commands_file), exist_ok=True)
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump({"commands": commands_snapshot}, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())

                os.replace(tmp_file, commands_file)
                self._commands_mtime = os.path.getmtime(commands_file) if os.path.exists(commands_file) else 0.0
                self._commands_loaded = True
                self._commands_cache = commands_snapshot
                return True
        except Exception as e:
            logger.error(f"保存命令配置失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
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

    def execute_command_group(
        self,
        group_name: str,
        session: 'TabSession',
        include_disabled: bool = False,
        source_command_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """在当前会话中顺序执行命令组内的命令。"""
        normalized_group = self._normalize_group_name(group_name)
        if not normalized_group:
            return {"ok": False, "error": "empty_group_name"}

        with self._commands_lock:
            commands = copy.deepcopy(self._load_commands())

        selected: List[Dict[str, Any]] = []
        for cmd in commands:
            if self._normalize_group_name(cmd.get("group_name")) != normalized_group:
                continue
            if not include_disabled and not cmd.get("enabled", True):
                continue
            if source_command_id and cmd.get("id") == source_command_id:
                continue
            selected.append(cmd)

        if not selected:
            return {"ok": False, "error": "group_empty_or_no_runnable_commands", "group_name": normalized_group}

        results: List[Dict[str, Any]] = []
        chain_seed = [source_command_id] if source_command_id else []
        for cmd in selected:
            command_id = cmd.get("id")
            if not command_id:
                continue
            exec_key = (command_id, session.id)
            if exec_key in self._executing:
                results.append({
                    "id": command_id,
                    "name": cmd.get("name", command_id),
                    "ok": False,
                    "error": "already_executing",
                })
                continue

            self._executing.add(exec_key)
            try:
                self._execute_command(cmd, session, chain=chain_seed)
                results.append({
                    "id": command_id,
                    "name": cmd.get("name", command_id),
                    "ok": True,
                })
            except Exception as e:
                logger.error(f"[CMD] 执行命令组失败 [{cmd.get('name')}]: {e}")
                results.append({
                    "id": command_id,
                    "name": cmd.get("name", command_id),
                    "ok": False,
                    "error": str(e),
                })
            finally:
                self._executing.discard(exec_key)

        success_count = sum(1 for item in results if item.get("ok"))
        return {
            "ok": success_count > 0,
            "group_name": normalized_group,
            "executed": success_count,
            "total": len(results),
            "results": results,
        }

    # ================= 触发检查 =================

    def check_triggers(self, session: 'TabSession'):
        """
        检查所有命令的触发条件

        在 TabSession.release() 后调用（锁外、后台，不阻塞主流程）
        """
        try:
            commands = self._load_commands()
        except Exception as e:
            logger.debug(f"命令加载失败，跳过触发检查: {e}")
            return

        if not commands:
            return

        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            try:
                if self._should_trigger(cmd, session):
                    self._execute_command_async(cmd, session)
            except Exception as e:
                logger.error(f"触发检查异常 [{cmd.get('name')}]: {e}")

    def handle_network_event(self, session: 'TabSession', event: Dict[str, Any]) -> bool:
        """
        处理实时网络事件。

        返回值：
        - True: 命中了“网络请求异常拦截”且应立即中断当前等待
        - False: 不需要中断
        """
        if not event:
            return False

        event_copy = dict(event)
        if not event_copy.get("event_id"):
            event_copy["event_id"] = f"net_{uuid.uuid4().hex[:10]}"
        event_copy.setdefault("timestamp", time.time())

        with self._lock:
            bucket = self._network_events.setdefault(session.id, [])
            bucket.append(event_copy)
            if len(bucket) > 50:
                del bucket[:-50]

        try:
            commands = self._load_commands()
        except Exception as e:
            logger.debug(f"命令加载失败，跳过网络事件触发: {e}")
            return False

        should_abort = False
        event_sig = self._build_network_signature(event_copy)

        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            trigger = cmd.get("trigger", {})
            if trigger.get("type") != "network_request_error":
                continue
            if not self._matches_scope(cmd, session):
                continue
            if not self._matches_network_trigger(trigger, event_copy):
                continue

            if not self._consume_network_signature(cmd.get("id"), session, event_sig):
                continue

            self._execute_command_async(cmd, session)
            should_abort = should_abort or bool(trigger.get("abort_on_match", True))

        return should_abort

    def has_network_interception_for_session(self, session: 'TabSession') -> bool:
        """当前会话是否存在可生效的网络异常拦截触发器。"""
        try:
            commands = self._load_commands()
        except Exception:
            return False

        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            trigger = cmd.get("trigger", {})
            if trigger.get("type") != "network_request_error":
                continue
            if self._matches_scope(cmd, session):
                return True
        return False

    def get_network_listen_pattern(self, session: 'TabSession') -> str:
        """
        依据网络异常拦截命令推断一个 listen_pattern。
        仅用于事件监听，不要求完全精准。
        """
        try:
            commands = self._load_commands()
        except Exception:
            return "http"

        hints: List[str] = []
        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            trigger = cmd.get("trigger", {})
            if trigger.get("type") != "network_request_error":
                continue
            if not self._matches_scope(cmd, session):
                continue

            pattern = str(trigger.get("url_pattern") or trigger.get("value") or "").strip()
            if not pattern:
                continue
            hint = self._pattern_to_listen_hint(pattern, str(trigger.get("match_mode", "keyword")))
            if hint:
                hints.append(hint)

        if not hints:
            return "http"
        hints.sort(key=len, reverse=True)
        return hints[0]

    def _ensure_trigger_state(self, command_id: str, session: 'TabSession') -> tuple[Dict[str, Any], bool]:
        state_key = (command_id, session.id)
        with self._lock:
            state = self._trigger_states.get(state_key)
            if state is None:
                state = {
                    "req": session.request_count,
                    "err": session.error_count,
                    "result_token": "",
                    "net_sig": "",
                }
                self._trigger_states[state_key] = state
                return state, True
            return state, False

    def _coerce_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _coerce_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _should_trigger(self, command: Dict, session: 'TabSession') -> bool:
        trigger = command.get("trigger", {})
        trigger_type = trigger.get("type", "")
        scope = trigger.get("scope", "all")

        # 作用域过滤
        if scope == "domain":
            target_domain = trigger.get("domain", "")
            if target_domain and session.current_domain:
                if target_domain not in session.current_domain:
                    return False
            elif target_domain:
                return False
        elif scope == "tab":
            target_index = trigger.get("tab_index")
            if target_index is not None and session.persistent_index != target_index:
                return False

        # 防重复执行
        exec_key = (command["id"], session.id)
        if exec_key in self._executing:
            return False

        # 获取/创建触发状态
        state, is_new = self._ensure_trigger_state(command["id"], session)
        if is_new:
            return False  # 首次注册不触发

        # 按类型检查
        if trigger_type == "request_count":
            threshold = max(1, self._coerce_int(trigger.get("value", 10), 10))
            delta = session.request_count - state["req"]
            if delta >= threshold:
                logger.info(
                    f"[CMD] 触发: {command.get('name')} "
                    f"(requests={delta}>={threshold}, tab={session.id})"
                )
                with self._lock:
                    state["req"] = session.request_count
                return True

        elif trigger_type == "error_count":
            threshold = max(1, self._coerce_int(trigger.get("value", 3), 3))
            delta = session.error_count - state["err"]
            if delta >= threshold:
                logger.info(
                    f"[CMD] 触发: {command.get('name')} "
                    f"(errors={delta}>={threshold})"
                )
                with self._lock:
                    state["err"] = session.error_count
                return True

        elif trigger_type == "idle_timeout":
            threshold_sec = max(1.0, self._coerce_float(trigger.get("value", 300), 300.0))
            idle = time.time() - session.last_used_at
            if idle >= threshold_sec:
                logger.info(
                    f"[CMD] 触发: {command.get('name')} "
                    f"(idle={idle:.0f}s>={threshold_sec}s)"
                )
                return True

        elif trigger_type == "page_check":
            check_text = str(trigger.get("value", ""))
            if check_text and self._check_page_content(session, check_text):
                logger.info(
                    f"[CMD] 触发: {command.get('name')} "
                    f"(page_check: '{check_text[:30]}')"
                )
                return True

        elif trigger_type == "command_result_match":
            if self._match_command_result_trigger(command, session, consume=True):
                logger.info(
                    f"[CMD] 触发: {command.get('name')} "
                    f"(command_result_match)"
                )
                return True

        elif trigger_type == "network_request_error":
            event = self._get_latest_network_event(session.id)
            if event and self._matches_network_trigger(trigger, event):
                sig = self._build_network_signature(event)
                if state.get("net_sig") != sig:
                    with self._lock:
                        state["net_sig"] = sig
                    logger.info(
                        f"[CMD] 触发: {command.get('name')} "
                        f"(network_request_error status={event.get('status')}, url={event.get('url', '')[:80]})"
                    )
                    return True

        return False

    def _check_page_content(self, session: 'TabSession', text: str) -> bool:
        try:
            html = session.tab.html or ""
            return text.lower() in html.lower()
        except Exception:
            return False

    def _normalize_match_rule(self, rule: Any) -> str:
        rule_value = str(rule or "").strip().lower()
        mapping = {
            "eq": "equals",
            "equal": "equals",
            "equals": "equals",
            "is": "equals",
            "contains": "contains",
            "include": "contains",
            "includes": "contains",
            "ne": "not_equals",
            "not_equal": "not_equals",
            "not_equals": "not_equals",
            "not": "not_equals",
        }
        return mapping.get(rule_value, "equals")

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
        with self._lock:
            self._command_results[(command["id"], session.id)] = entry

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
        if state.get("net_sig") == signature:
            return False
        with self._lock:
            state["net_sig"] = signature
        return True

    def _get_latest_network_event(self, tab_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            items = self._network_events.get(tab_id) or []
            if not items:
                return None
            return copy.deepcopy(items[-1])

    def _matches_scope(self, command: Dict, session: 'TabSession') -> bool:
        trigger = command.get("trigger", {})
        scope = trigger.get("scope", "all")

        if scope == "domain":
            target_domain = trigger.get("domain", "")
            if target_domain and session.current_domain:
                return target_domain in session.current_domain
            return not target_domain

        if scope == "tab":
            target_index = trigger.get("tab_index")
            return target_index is None or session.persistent_index == target_index

        return True

    def _trigger_chained_commands(
        self,
        source_command: Dict,
        session: 'TabSession',
        chain: Optional[List[str]] = None,
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
            if exec_key in self._executing:
                continue

            logger.info(
                f"[CMD] 链式触发: {source_command.get('name')} -> {cmd.get('name')} "
                f"(tab={session.id})"
            )
            self._execute_command_async(cmd, session, chain=next_chain)

    def _trigger_result_match_commands(
        self,
        source_command: Dict[str, Any],
        session: 'TabSession',
        chain: Optional[List[str]] = None,
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
            if not self._match_command_result_trigger(cmd, session, consume=True):
                continue

            exec_key = (target_id, session.id)
            if exec_key in self._executing:
                continue

            logger.info(
                f"[CMD] 条件分支触发: {source_command.get('name')} -> {cmd.get('name')} "
                f"(tab={session.id})"
            )
            self._execute_command_async(cmd, session, chain=next_chain)

    # ================= 动作执行 =================

    def _execute_command_async(
        self,
        command: Dict,
        session: 'TabSession',
        chain: Optional[List[str]] = None,
    ):
        exec_key = (command["id"], session.id)
        self._executing.add(exec_key)

        def _run():
            try:
                self._execute_command(command, session, chain=chain)
            except Exception as e:
                logger.error(f"[CMD] 命令执行失败 [{command.get('name')}]: {e}")
            finally:
                self._executing.discard(exec_key)

        thread = threading.Thread(
            target=_run, daemon=True,
            name=f"cmd-{command['id'][:8]}"
        )
        thread.start()

    def _execute_command(
        self,
        command: Dict,
        session: 'TabSession',
        chain: Optional[List[str]] = None,
    ):
        cmd_name = command.get("name", "未命名")
        mode = command.get("mode", "simple")

        logger.debug(f"[CMD] ▶ 执行: {cmd_name} (mode={mode}, tab={session.id})")
        self._suspend_tab_global_network(session, reason=f"command:{command.get('id', '')}")
        try:
            self._update_trigger_stats(command["id"])

            execution_result: Dict[str, Any]
            if mode == "advanced":
                execution_result = self._execute_advanced(command, session)
            else:
                execution_result = self._execute_simple(command, session)

            self._record_command_result(command, session, execution_result)

            logger.debug(f"[CMD] ✅ 完成: {cmd_name}")
            self._trigger_chained_commands(command, session, chain=chain)
            self._trigger_result_match_commands(command, session, chain=chain)
        finally:
            self._resume_tab_global_network(session, reason=f"command:{command.get('id', '')}")

    def _execute_simple(self, command: Dict, session: 'TabSession') -> Dict[str, Any]:
        actions = command.get("actions", [])
        step_results: List[Dict[str, Any]] = []
        last_result: Any = ""

        for i, action in enumerate(actions):
            action_type = action.get("type", "")
            action_ref = str(action.get("action_id") or f"step_{i + 1}")
            logger.debug(f"[CMD] 步骤 {i + 1}/{len(actions)}: {action_type}")
            try:
                action_result = self._execute_action(action, session)
                last_result = action_result
                step_results.append({
                    "index": i,
                    "action_ref": action_ref,
                    "type": action_type,
                    "result": action_result,
                    "ok": True,
                })
            except CommandFlowAbort as e:
                last_result = str(e)
                step_results.append({
                    "index": i,
                    "action_ref": action_ref,
                    "type": action_type,
                    "result": last_result,
                    "ok": False,
                })
                logger.info(f"[CMD] 动作链提前结束: {e}")
                break
            except Exception as e:
                logger.error(f"[CMD] 步骤 {i + 1} 失败 ({action_type}): {e}")
                last_result = f"ERROR: {e}"
                step_results.append({
                    "index": i,
                    "action_ref": action_ref,
                    "type": action_type,
                    "result": last_result,
                    "ok": False,
                })

        return {
            "mode": "simple",
            "result": last_result,
            "steps": step_results,
        }

    def _execute_action(self, action: Dict, session: 'TabSession') -> Any:
        action_type = action.get("type", "")
        tab = session.tab

        if action_type == "clear_cookies":
            try:
                tab.run_js(
                    "document.cookie.split(';').forEach(c => "
                    "document.cookie = c.trim().split('=')[0] + "
                    "'=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/;')"
                )
                logger.debug("[CMD] Cookies 已清除")
                return "cookies_cleared"
            except Exception as e:
                logger.warning(f"[CMD] 清除 Cookies 失败: {e}")
                return f"cookies_clear_failed: {e}"

        elif action_type == "refresh_page":
            try:
                tab.refresh()
                time.sleep(2)
                logger.debug("[CMD] 页面已刷新")
                return "page_refreshed"
            except Exception as e:
                logger.warning(f"[CMD] 刷新页面失败: {e}")
                return f"refresh_failed: {e}"

        elif action_type == "new_chat":
            try:
                engine = self._get_config_engine()
                domain = session.current_domain or ""
                site_data = engine._get_site_data(domain, session.preset_name)
                if site_data:
                    selector = site_data.get("selectors", {}).get("new_chat_btn", "")
                    if selector:
                        ele = tab.ele(selector, timeout=3)
                        if ele:
                            ele.click()
                            time.sleep(1)
                            logger.debug("[CMD] 新建对话完成")
                            return "new_chat_clicked"
                        else:
                            logger.warning("[CMD] 新建对话按钮未找到")
                            return "new_chat_button_not_found"
                    else:
                        logger.warning("[CMD] 未配置 new_chat_btn 选择器")
                        return "new_chat_selector_missing"
            except Exception as e:
                logger.warning(f"[CMD] 新建对话失败: {e}")
                return f"new_chat_failed: {e}"

        elif action_type == "run_js":
            code = action.get("code", "")
            if code:
                try:
                    result = tab.run_js(code)
                    logger.debug(f"[CMD] JS 执行完成: {str(result)[:100]}")
                    return result
                except Exception as e:
                    logger.warning(f"[CMD] JS 执行失败: {e}")
                    return f"js_failed: {e}"
            return ""

        elif action_type == "wait":
            seconds = float(action.get("seconds", 1))
            time.sleep(seconds)
            logger.debug(f"[CMD] 等待 {seconds}s")
            return f"waited:{seconds}"

        elif action_type in {"execute_preset", "switch_preset"}:
            return self._execute_preset_action(action, session)

        elif action_type == "execute_workflow":
            return self._execute_workflow_action(action, session)

        elif action_type == "navigate":
            url = action.get("url", "")
            if url:
                try:
                    tab.get(url)
                    time.sleep(2)
                    logger.debug(f"[CMD] 已导航到: {url}")
                    return f"navigated:{url}"
                except Exception as e:
                    logger.warning(f"[CMD] 导航失败: {e}")
                    return f"navigate_failed:{e}"
            return "navigate_skipped"

        elif action_type == "switch_proxy":
            return self._execute_switch_proxy(action, session)

        elif action_type == "send_webhook":
            return self._execute_webhook_action(action, session)

        elif action_type == "execute_command_group":
            return self._execute_command_group_action(action, session)

        elif action_type == "abort_task":
            result = self._execute_abort_task(action, session)
            if bool(action.get("stop_actions", True)):
                raise CommandFlowAbort("abort_task_triggered")
            return result

        elif action_type == "release_tab_lock":
            result = self._execute_release_tab_lock(action, session)
            if bool(action.get("stop_actions", True)):
                raise CommandFlowAbort("release_tab_lock_triggered")
            return result

        else:
            logger.warning(f"[CMD] 未知动作类型: {action_type}")
            return ""

    def _execute_preset_action(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        """执行预设动作，兼容旧版 switch_preset。"""
        preset_name = str(action.get("preset_name", "")).strip()
        if not preset_name:
            logger.warning("[CMD] 预设名称为空，跳过执行")
            return {"ok": False, "error": "empty_preset"}

        try:
            browser = self._get_browser()
            browser.tab_pool.set_tab_preset(
                session.persistent_index, preset_name
            )
            logger.debug(f"[CMD] 预设已切换: {preset_name}")
            return {"ok": True, "preset": preset_name}
        except Exception as e:
            logger.warning(f"[CMD] 切换预设失败: {e}")
            return {"ok": False, "error": str(e)}

    def _execute_workflow_action(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        """在当前标签页上立即执行目标预设的工作流。"""
        try:
            browser = self._get_browser()
            preset_name = str(action.get("preset_name", "")).strip()
            prompt = str(action.get("prompt", ""))

            if preset_name:
                effective_preset = preset_name
                logger.debug(
                    f"[CMD] 开始执行工作流: tab=#{session.persistent_index}, preset={effective_preset}"
                )

                messages = [{"role": "user", "content": prompt}]
                chunks = list(
                    browser._execute_workflow_non_stream(
                        session,
                        messages,
                        preset_name=preset_name,
                    )
                )

                for chunk in chunks:
                    payload = chunk[6:].strip() if chunk.startswith("data: ") else chunk
                    if not payload:
                        continue
                    try:
                        data = json.loads(payload)
                    except Exception:
                        continue
                    if isinstance(data, dict) and data.get("error"):
                        logger.warning(f"[CMD] 工作流返回错误: {data['error']}")
                        return {"ok": False, "error": data["error"]}

                logger.debug(
                    f"[CMD] 工作流执行完成: tab=#{session.persistent_index}, preset={effective_preset}"
                )
                return {"ok": True, "preset": effective_preset}

            effective_preset = session.preset_name or "主预设"
            logger.debug(
                f"[CMD] 开始执行工作流: tab=#{session.persistent_index}, preset={effective_preset}"
            )

            messages = [{"role": "user", "content": prompt}]
            chunks = list(browser._execute_workflow_non_stream(session, messages))

            for chunk in chunks:
                payload = chunk[6:].strip() if chunk.startswith("data: ") else chunk
                if not payload:
                    continue
                try:
                    data = json.loads(payload)
                except Exception:
                    continue
                if isinstance(data, dict) and data.get("error"):
                    logger.warning(f"[CMD] 工作流返回错误: {data['error']}")
                    return {"ok": False, "error": data["error"]}

            logger.debug(
                f"[CMD] 工作流执行完成: tab=#{session.persistent_index}, preset={effective_preset}"
            )
            return {"ok": True, "preset": effective_preset}
        except Exception as e:
            logger.warning(f"[CMD] 执行工作流失败: {e}")
            return {"ok": False, "error": str(e)}

    def _build_template_context(self, session: 'TabSession') -> Dict[str, Any]:
        latest_event = self._get_latest_network_event(session.id) or {}
        return {
            "tab_id": session.id,
            "tab_index": session.persistent_index,
            "domain": session.current_domain or "",
            "preset": session.preset_name or "主预设",
            "request_count": session.request_count,
            "error_count": session.error_count,
            "task_id": session.current_task_id or "",
            "timestamp": int(time.time()),
            "iso_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "network_url": str(latest_event.get("url", "") or ""),
            "network_status": str(latest_event.get("status", "") or ""),
            "network_method": str(latest_event.get("method", "") or ""),
        }

    def _render_template(self, template: Any, context: Dict[str, Any]) -> str:
        raw = str(template or "")

        def _replace(match: re.Match) -> str:
            key = match.group(1).strip()
            return str(context.get(key, ""))

        return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", _replace, raw)

    def _parse_json_or_string(self, raw: str) -> Any:
        text = str(raw or "").strip()
        if not text:
            return ""
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except Exception:
                return text
        return text

    def _execute_webhook_action(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        if not HAS_REQUESTS:
            logger.error("[CMD] send_webhook 需要 requests 库，请运行: pip install requests")
            return {"ok": False, "error": "requests_not_installed"}

        ctx = self._build_template_context(session)
        method = str(action.get("method", "POST") or "POST").strip().upper()
        timeout = float(action.get("timeout", 8))
        url = self._render_template(action.get("url", ""), ctx).strip()
        if not url:
            logger.warning("[CMD] Webhook URL 为空，跳过执行")
            return {"ok": False, "error": "empty_url"}

        payload_text = self._render_template(action.get("payload", ""), ctx)
        payload = self._parse_json_or_string(payload_text)

        raw_headers = action.get("headers")
        headers: Dict[str, str] = {}
        if isinstance(raw_headers, dict):
            for key, value in raw_headers.items():
                headers[str(key)] = self._render_template(value, ctx)
        elif isinstance(raw_headers, str) and raw_headers.strip():
            parsed = self._parse_json_or_string(self._render_template(raw_headers, ctx))
            if isinstance(parsed, dict):
                headers = {str(k): str(v) for k, v in parsed.items()}

        request_kwargs: Dict[str, Any] = {
            "method": method,
            "url": url,
            "timeout": timeout,
            "headers": headers or None,
        }

        if method == "GET":
            if isinstance(payload, dict):
                request_kwargs["params"] = payload
            elif payload:
                request_kwargs["params"] = {"payload": payload}
        else:
            if isinstance(payload, (dict, list)):
                request_kwargs["json"] = payload
            elif payload:
                request_kwargs["data"] = payload

        try:
            response = requests.request(**request_kwargs)
            if bool(action.get("raise_for_status", False)):
                response.raise_for_status()

            logger.info(f"[CMD] Webhook 已发送: {method} {url} -> {response.status_code}")
            return {
                "ok": response.ok,
                "status_code": response.status_code,
                "url": url,
                "body_preview": response.text[:200],
            }
        except Exception as e:
            logger.warning(f"[CMD] Webhook 发送失败: {e}")
            return {"ok": False, "error": str(e), "url": url}

    def _execute_command_group_action(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        group_name = self._normalize_group_name(action.get("group_name"))
        include_disabled = bool(action.get("include_disabled", False))
        if not group_name:
            logger.warning("[CMD] execute_command_group 缺少 group_name，跳过执行")
            return {"ok": False, "error": "empty_group_name"}

        logger.info(f"[CMD] 执行命令组动作: {group_name} (include_disabled={include_disabled})")
        return self.execute_command_group(
            group_name=group_name,
            session=session,
            include_disabled=include_disabled,
        )

    def _execute_abort_task(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        reason = str(action.get("reason", "abort_task_action")).strip() or "abort_task_action"
        cancelled = False
        try:
            from app.services.request_manager import request_manager
            cancelled = request_manager.cancel_current(reason)
        except Exception as e:
            logger.debug(f"[CMD] 取消请求失败（可忽略）: {e}")

        try:
            if hasattr(session.tab, "stop_loading"):
                session.tab.stop_loading()
            session.tab.run_js("if (window.stop) { window.stop(); }")
        except Exception:
            pass

        logger.info(f"[CMD] 中断任务动作已执行 (cancelled={cancelled}, reason={reason})")
        return {"ok": True, "cancelled": cancelled, "reason": reason}

    def _execute_release_tab_lock(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        """
        解除当前标签页占用：
        - 尝试取消该标签页关联请求；
        - 强制释放标签页 BUSY 状态；
        - 可选重置到 about:blank。
        """
        reason = str(action.get("reason", "release_tab_lock_action")).strip() or "release_tab_lock_action"
        clear_page = bool(action.get("clear_page", True))
        try:
            browser = self._get_browser()
            pool = browser.tab_pool
            result = pool.terminate_by_index(
                session.persistent_index,
                reason=reason,
                clear_page=clear_page,
            )
            logger.info(
                f"[CMD] 解除标签页占用完成: tab=#{session.persistent_index}, "
                f"cancelled={result.get('cancelled')}, status={result.get('status')}, reason={reason}"
            )
            return result
        except Exception as e:
            logger.warning(f"[CMD] 解除标签页占用失败: {e}")
            return {"ok": False, "error": str(e), "reason": reason}

    # ================= 代理切换 =================

    def _execute_switch_proxy(self, action: Dict, session: 'TabSession') -> Dict[str, Any]:
        """
        执行代理节点切换（通过 Clash API）
        """
        if not HAS_REQUESTS:
            logger.error("[CMD] 切换代理需要 requests 库，请运行: pip install requests")
            return {"ok": False, "error": "requests_not_installed"}

        # 读取配置
        clash_api = action.get("clash_api", "http://127.0.0.1:9090").rstrip("/")
        clash_secret = action.get("clash_secret", "")
        selector = action.get("selector", "Proxy")
        mode = action.get("mode", "random")
        node_name = action.get("node_name", "")
        exclude_str = action.get("exclude_keywords", "DIRECT,REJECT,GLOBAL,自动选择,故障转移")
        refresh_after = action.get("refresh_after", True)

        exclude_keywords = [k.strip() for k in exclude_str.split(",") if k.strip()]

        headers = {"Content-Type": "application/json"}
        if clash_secret:
            headers["Authorization"] = f"Bearer {clash_secret}"

        try:
            resp = requests.get(
                f"{clash_api}/proxies/{selector}",
                headers=headers,
                timeout=5
            )

            if resp.status_code == 404:
                logger.error(f"[CMD] 代理组 '{selector}' 不存在，请检查 Clash 配置")
                return {"ok": False, "error": "proxy_group_not_found"}

            resp.raise_for_status()
            data = resp.json()

            current_node = data.get("now", "")
            all_nodes = data.get("all", [])

            available = []
            for node in all_nodes:
                should_exclude = False
                for keyword in exclude_keywords:
                    if keyword and keyword in node:
                        should_exclude = True
                        break
                if not should_exclude:
                    available.append(node)

            if not available:
                logger.warning("[CMD] 没有可用的代理节点")
                return {"ok": False, "error": "no_available_nodes"}

            new_node = None

            if mode == "specific":
                if node_name in available:
                    new_node = node_name
                else:
                    logger.warning(f"[CMD] 指定节点 '{node_name}' 不可用，回退到随机模式")
                    mode = "random"

            if mode == "random":
                candidates = [n for n in available if n != current_node]
                if candidates:
                    new_node = random.choice(candidates)
                else:
                    new_node = random.choice(available)

            elif mode == "round_robin":
                try:
                    current_idx = available.index(current_node)
                    next_idx = (current_idx + 1) % len(available)
                    new_node = available[next_idx]
                except ValueError:
                    new_node = available[0]

            if not new_node:
                logger.warning("[CMD] 无法选择新节点")
                return {"ok": False, "error": "cannot_pick_node"}

            if new_node == current_node:
                logger.info(f"[CMD] 当前已是节点: {current_node}，跳过切换")
                return {"ok": True, "switched": False, "node": current_node}

            switch_resp = requests.put(
                f"{clash_api}/proxies/{selector}",
                json={"name": new_node},
                headers=headers,
                timeout=5
            )
            switch_resp.raise_for_status()

            logger.info(f"[CMD] ✅ 代理已切换: {current_node} → {new_node}")

            if refresh_after:
                time.sleep(1)
                try:
                    session.tab.refresh()
                    time.sleep(2)
                    logger.debug("[CMD] 页面已刷新")
                except Exception as e:
                    logger.warning(f"[CMD] 刷新页面失败: {e}")

            return {
                "ok": True,
                "switched": True,
                "from": current_node,
                "to": new_node,
            }

        except requests.exceptions.ConnectionError:
            logger.error(f"[CMD] ❌ 无法连接到 Clash API ({clash_api})，请检查 Clash 是否运行")
            return {"ok": False, "error": "connection_error"}
        except requests.exceptions.Timeout:
            logger.error("[CMD] ❌ Clash API 请求超时")
            return {"ok": False, "error": "timeout"}
        except requests.exceptions.HTTPError as e:
            logger.error(f"[CMD] ❌ Clash API 错误: {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.error(f"[CMD] ❌ 切换代理失败: {e}")
            return {"ok": False, "error": str(e)}

    # ================= 高级模式 =================

    def _execute_advanced(self, command: Dict, session: 'TabSession') -> Dict[str, Any]:
        script = command.get("script", "")
        lang = command.get("script_lang", "javascript")

        if not script.strip():
            logger.warning("[CMD] 高级模式脚本为空")
            return {"mode": "advanced", "result": "", "steps": []}

        if lang == "javascript":
            try:
                result = session.tab.run_js(script)
                logger.info(f"[CMD] JS 脚本执行完成: {str(result)[:200]}")
                return {"mode": "advanced", "result": result, "steps": []}
            except Exception as e:
                logger.error(f"[CMD] JS 脚本执行失败: {e}")
                return {"mode": "advanced", "result": f"js_failed: {e}", "steps": []}

        if lang == "python":
            import json as json_module
            context = {
                "tab": session.tab,
                "session": session,
                "browser": self._get_browser(),
                "config_engine": self._get_config_engine(),
                "logger": logger,
                "time": time,
                "json": json_module,
                "result": "",
            }
            try:
                exec(script, {"__builtins__": __builtins__}, context)
                logger.info("[CMD] Python 脚本执行完成")
                return {"mode": "advanced", "result": context.get("result", ""), "steps": []}
            except Exception as e:
                logger.error(f"[CMD] Python 脚本执行失败: {e}")
                return {"mode": "advanced", "result": f"python_failed: {e}", "steps": []}

        logger.warning(f"[CMD] 不支持的脚本语言: {lang}")
        return {"mode": "advanced", "result": f"unsupported_lang:{lang}", "steps": []}

    # ================= 统计 =================

    def _update_trigger_stats(self, command_id: str):
        with self._commands_lock:
            commands = self._load_commands()

            for cmd in commands:
                if cmd.get("id") == command_id:
                    cmd["last_triggered"] = time.time()
                    cmd["trigger_count"] = cmd.get("trigger_count", 0) + 1
                    break

            self._save_commands(commands)

    # ================= 元信息 =================

    def get_trigger_types(self) -> Dict[str, str]:
        return copy.deepcopy(TRIGGER_TYPES)

    def get_action_types(self) -> Dict[str, str]:
        return copy.deepcopy(ACTION_TYPES)

    def get_trigger_states(self) -> Dict[str, Any]:
        result = {}
        for (cmd_id, tab_id), state in self._trigger_states.items():
            result[f"{cmd_id}:{tab_id}"] = state
        return result


# ================= 单例 =================
command_engine = CommandEngine()

__all__ = [
    'CommandEngine',
    'command_engine',
    'TRIGGER_TYPES',
    'ACTION_TYPES',
    'get_default_command',
]
