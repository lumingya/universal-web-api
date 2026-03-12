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
from app.utils.site_url import extract_remote_site_domain

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
            "priority": 2,
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
        self._periodic_next_run: Dict[tuple, float] = {}
        self._periodic_stop_event = threading.Event()
        self._periodic_thread: Optional[threading.Thread] = None
        self._pending_high_by_session: Dict[str, int] = {}
        self._running_high_by_session: Dict[str, int] = {}
        self._pending_high_by_domain: Dict[str, int] = {}
        self._running_high_by_domain: Dict[str, int] = {}
        try:
            _baseline = int(os.getenv("CMD_REQUEST_PRIORITY_BASELINE", "2"))
        except Exception:
            _baseline = 2
        self._request_priority_baseline = max(1, min(4, _baseline))
        self._activate_tab_on_command = str(
            os.getenv("CMD_ACTIVATE_TAB_ON_COMMAND", "false")
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        self._use_focus_emulation_on_command = str(
            os.getenv("CMD_USE_FOCUS_EMULATION_ON_COMMAND", "true")
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        self._wake_tab_before_page_check = str(
            os.getenv("CMD_WAKE_TAB_BEFORE_PAGE_CHECK", "true")
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        self._tab_pool_auto_refresh = str(
            os.getenv("CMD_TAB_POOL_AUTO_REFRESH", "true")
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        try:
            _refresh_interval = float(os.getenv("CMD_TAB_POOL_REFRESH_INTERVAL_SEC", "5"))
        except Exception:
            _refresh_interval = 5.0
        self._tab_pool_refresh_interval_sec = max(1.0, _refresh_interval)
        self._last_tab_pool_refresh_at = 0.0
        self._periodic_keepalive_enabled = str(
            os.getenv("CMD_PERIODIC_KEEPALIVE_ENABLED", "true")
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        try:
            _keepalive_interval = float(os.getenv("CMD_PERIODIC_KEEPALIVE_INTERVAL_SEC", "20"))
        except Exception:
            _keepalive_interval = 20.0
        self._periodic_keepalive_interval_sec = max(5.0, _keepalive_interval)
        self._last_keepalive_by_session: Dict[str, float] = {}
        self._last_tab_pool_wait_log_at = 0.0

        logger.debug("CommandEngine 初始化")
        self._start_periodic_scheduler()

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

    def _set_focus_emulation(self, session: 'TabSession', enabled: bool):
        """Best-effort focus emulation without stealing OS/browser foreground focus."""
        try:
            session.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=bool(enabled))
        except Exception as e:
            logger.debug(f"[CMD] focus emulation set({enabled}) failed (ignored): {e}")

    def _try_wake_tab(self, session: 'TabSession', reason: str = ""):
        """
        Best-effort wake-up for background/discard-prone tabs.
        Uses lifecycle and lightweight JS ping, without forcing browser focus.
        """
        if not self._wake_tab_before_page_check:
            return
        focus_emulation_set = False
        try:
            session.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=True)
            focus_emulation_set = True
        except Exception:
            pass
        try:
            session.tab.run_cdp("Page.setWebLifecycleState", state="active")
        except Exception:
            pass
        try:
            session.tab.run_js("return document.readyState || '';")
        except Exception:
            pass
        finally:
            if focus_emulation_set:
                try:
                    session.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=False)
                except Exception:
                    pass

    def _refresh_tab_pool_if_due(self, pool: Any):
        if not self._tab_pool_auto_refresh:
            return
        now = time.time()
        if (now - self._last_tab_pool_refresh_at) < self._tab_pool_refresh_interval_sec:
            return
        self._last_tab_pool_refresh_at = now
        try:
            if hasattr(pool, "refresh_tabs"):
                pool.refresh_tabs()
        except Exception as e:
            logger.debug(f"[CMD] tab pool refresh failed (ignored): {e}")

    def _maybe_periodic_keepalive(self, session: 'TabSession', now_ts: float):
        if not self._periodic_keepalive_enabled:
            return
        sid = str(getattr(session, "id", "") or "")
        if not sid:
            return
        last_at = float(self._last_keepalive_by_session.get(sid, 0.0) or 0.0)
        if (now_ts - last_at) < self._periodic_keepalive_interval_sec:
            return
        self._last_keepalive_by_session[sid] = now_ts
        self._try_wake_tab(session, reason="periodic_keepalive")

    def _start_periodic_scheduler(self):
        if self._periodic_thread and self._periodic_thread.is_alive():
            return
        self._periodic_stop_event.clear()
        self._periodic_thread = threading.Thread(
            target=self._periodic_loop,
            daemon=True,
            name="cmd-periodic-checker",
        )
        self._periodic_thread.start()
        logger.debug("[CMD] periodic scheduler started")

    def is_scheduler_running(self) -> bool:
        thread = self._periodic_thread
        return bool(thread and thread.is_alive())

    def ensure_scheduler_running(self):
        """Best-effort watchdog: start periodic checker if it is not running."""
        if not self.is_scheduler_running():
            self._start_periodic_scheduler()

    def shutdown(self):
        self._periodic_stop_event.set()
        thread = self._periodic_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)

    def _periodic_loop(self):
        while not self._periodic_stop_event.wait(1.0):
            try:
                self._run_periodic_checks()
            except Exception as e:
                logger.debug(f"[CMD] periodic loop error (ignored): {e}")

    def _run_periodic_checks(self):
        try:
            commands = self._load_commands()
        except Exception:
            return
        if not commands:
            return

        try:
            browser = self._get_browser()
        except Exception:
            return

        pool = getattr(browser, "_tab_pool", None)
        if pool is None:
            try:
                pool = browser.tab_pool
                logger.debug("[CMD] periodic scheduler initialized tab pool")
            except Exception as e:
                now = time.time()
                if (now - self._last_tab_pool_wait_log_at) >= 10:
                    self._last_tab_pool_wait_log_at = now
                    logger.debug(f"[CMD] periodic scheduler waiting for tab pool init: {e}")
                return

        if not hasattr(pool, "get_idle_sessions_snapshot"):
            return
        self._refresh_tab_pool_if_due(pool)

        sessions = pool.get_idle_sessions_snapshot()
        if not sessions:
            return

        now = time.time()
        active_keys = set()

        enabled_commands = [
            (idx, cmd) for idx, cmd in enumerate(commands)
            if cmd.get("enabled", True)
        ]

        for session in sessions:
            session_status = str(getattr(getattr(session, "status", None), "value", "")).lower()
            if session_status != "idle":
                continue
            self._maybe_periodic_keepalive(session, now)

            due_commands: List[tuple[int, int, Dict[str, Any]]] = []
            for idx, cmd in enabled_commands:
                cmd_id = str(cmd.get("id", "")).strip()
                if not cmd_id:
                    continue

                trigger = cmd.get("trigger", {}) or {}
                if not bool(trigger.get("periodic_enabled", True)):
                    continue

                key = (cmd_id, session.id)
                active_keys.add(key)

                interval = max(1.0, self._coerce_float(trigger.get("periodic_interval_sec", 8), 8.0))
                jitter = max(0.0, self._coerce_float(trigger.get("periodic_jitter_sec", 2), 2.0))

                with self._lock:
                    next_at = float(self._periodic_next_run.get(key, 0.0))
                if now < next_at:
                    continue

                delay = interval + (random.uniform(0.0, jitter) if jitter > 0 else 0.0)
                with self._lock:
                    self._periodic_next_run[key] = now + delay

                due_commands.append((self._get_command_priority(cmd), idx, cmd))

            due_commands.sort(key=lambda item: (-item[0], item[1]))
            for _, _, cmd in due_commands:
                if str(getattr(getattr(session, "status", None), "value", "")).lower() != "idle":
                    break
                if self._should_trigger(cmd, session):
                    self._execute_command_async(cmd, session)

        with self._lock:
            stale_keys = [k for k in self._periodic_next_run if k not in active_keys]
            for key in stale_keys:
                self._periodic_next_run.pop(key, None)
            stale_keepalive_keys = [k for k in self._last_keepalive_by_session if k not in {s.id for s in sessions}]
            for key in stale_keepalive_keys:
                self._last_keepalive_by_session.pop(key, None)

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
                logger.error(f"trigger check failed [{cmd.get('name')}]: {e}")
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
        self.ensure_scheduler_running()
        try:
            commands = self._load_commands()
        except Exception as e:
            logger.debug(f"命令加载失败，跳过触发检查: {e}")
            return

        if not commands:
            return


        ordered_commands = [
            (idx, cmd) for idx, cmd in enumerate(commands)
            if cmd.get("enabled", True)
        ]
        ordered_commands.sort(key=lambda item: (-self._get_command_priority(item[1]), item[0]))

        for _, cmd in ordered_commands:
            try:
                if self._should_trigger(cmd, session):
                    self._execute_command_async(cmd, session)
            except Exception as e:
                logger.error(f"trigger check failed [{cmd.get('name')}]: {e}")

    def handle_network_event(self, session: 'TabSession', event: Dict[str, Any]) -> bool:
        """
        处理实时网络事件。

        返回值：
        - True: 命中了“网络请求异常拦截”且应立即中断当前等待
        - False: 不需要中断
        """
        self.ensure_scheduler_running()
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

    def _ensure_trigger_state(
        self,
        command_id: str,
        session: 'TabSession',
        state_key: Optional[tuple] = None,
        initial_req: Optional[int] = None,
        initial_err: Optional[int] = None,
    ) -> tuple[Dict[str, Any], bool]:
        state_key = state_key or (command_id, session.id)
        req_baseline = session.request_count if initial_req is None else int(initial_req)
        err_baseline = session.error_count if initial_err is None else int(initial_err)
        with self._lock:
            state = self._trigger_states.get(state_key)
            if state is None:
                state = {
                    "req": req_baseline,
                    "err": err_baseline,
                    "result_token": "",
                    "net_sig": "",
                    "page_key": "",
                    "page_hit": False,
                    "page_last_fire_at": 0.0,
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


    @staticmethod
    def _counter_inc(counter: Dict[str, int], key: str):
        if not key:
            return
        counter[key] = int(counter.get(key, 0)) + 1

    @staticmethod
    def _counter_dec(counter: Dict[str, int], key: str):
        if not key:
            return
        next_value = int(counter.get(key, 0)) - 1
        if next_value > 0:
            counter[key] = next_value
        else:
            counter.pop(key, None)

    def _normalize_priority(self, value: Any, default: int = 2) -> int:
        try:
            p = int(value)
        except Exception:
            p = int(default)
        return max(1, min(4, p))

    def _get_request_priority_baseline(self) -> int:
        return self._normalize_priority(getattr(self, "_request_priority_baseline", 2), 2)

    def _get_command_priority(self, command: Dict) -> int:
        trigger = command.get("trigger", {}) or {}
        raw = trigger.get("priority", trigger.get("command_priority", 2))
        return self._normalize_priority(raw, 2)

    def _command_affects_domain(self, command: Dict) -> bool:
        actions = command.get("actions", [])
        for action in actions or []:
            if str((action or {}).get("type", "")).strip() == "clear_cookies":
                return True
        return False

    def _has_busy_peer_on_domain(self, domain: str, exclude_session_id: str = "") -> bool:
        normalized = str(domain or "").strip().lower()
        if not normalized:
            return False
        try:
            browser = self._get_browser()
            pool = getattr(browser, "_tab_pool", None)
            if pool is None:
                return False
            status = pool.get_status() if hasattr(pool, "get_status") else {}
            for tab in status.get("tabs", []) or []:
                sid = str(tab.get("id", "") or "")
                if exclude_session_id and sid == exclude_session_id:
                    continue
                if str(tab.get("status", "")).lower() != "busy":
                    continue
                tab_domain = str(tab.get("current_domain", "") or "").strip().lower()
                if tab_domain and normalized in tab_domain:
                    return True
        except Exception:
            return False
        return False

    def should_block_request_for_session(self, session: "TabSession", task_id: str = "") -> bool:
        if session is None:
            return False
        task = str(task_id or "").strip().lower()
        if task.startswith("cmd_") or task.startswith("group_") or task.startswith("cmd_test_") or task.startswith("group_test_"):
            return False

        session_id = str(getattr(session, "id", "") or "")
        domain = self._get_session_domain(session)
        with self._lock:
            if int(self._pending_high_by_session.get(session_id, 0)) > 0:
                return True
            if int(self._running_high_by_session.get(session_id, 0)) > 0:
                return True
            if domain and int(self._pending_high_by_domain.get(domain, 0)) > 0:
                return True
            if domain and int(self._running_high_by_domain.get(domain, 0)) > 0:
                return True
        return False


    def _get_session_domain(self, session: 'TabSession') -> str:
        domain = str(getattr(session, "current_domain", "") or "").strip().lower()
        if domain:
            return domain
        try:
            url = str(getattr(session.tab, "url", "") or "")
            domain = extract_remote_site_domain(url) or ""
            if domain:
                session.current_domain = domain
                return domain
        except Exception:
            pass
        return ""


    def _build_request_count_state_key(self, command: Dict, session: 'TabSession') -> tuple:
        trigger = command.get("trigger", {}) or {}
        scope = str(trigger.get("scope", "all") or "all").strip().lower()

        if scope == "all":
            return (command["id"], "__scope:all")

        if scope == "domain":
            domain_key = str(trigger.get("domain", "") or "").strip().lower()
            if not domain_key:
                domain_key = self._get_session_domain(session)
            return (command["id"], f"__scope:domain:{domain_key or '_'}")

        return (command["id"], session.id)

    def _get_scope_request_count(self, command: Dict, session: 'TabSession') -> int:
        trigger = command.get("trigger", {}) or {}
        scope = str(trigger.get("scope", "all") or "all").strip().lower()

        if scope == "tab":
            try:
                return int(getattr(session, "request_count", 0) or 0)
            except Exception:
                return 0

        try:
            browser = self._get_browser()
            pool = getattr(browser, "_tab_pool", None)
            if pool is None:
                return int(getattr(session, "request_count", 0) or 0)
            status = pool.get_status() if hasattr(pool, "get_status") else {}
            tabs = status.get("tabs", []) or []
        except Exception:
            return int(getattr(session, "request_count", 0) or 0)

        if scope == "all":
            total = 0
            for tab in tabs:
                try:
                    total += int(tab.get("request_count", 0) or 0)
                except Exception:
                    continue
            return total

        if scope == "domain":
            target_domain = str(trigger.get("domain", "") or "").strip().lower()
            if not target_domain:
                target_domain = self._get_session_domain(session)
            if not target_domain:
                return int(getattr(session, "request_count", 0) or 0)

            total = 0
            for tab in tabs:
                try:
                    tab_domain = str(tab.get("current_domain", "") or "").strip().lower()
                    if not tab_domain:
                        url = str(tab.get("url", "") or "")
                        if "://" in url:
                            tab_domain = url.split("//", 1)[1].split("/", 1)[0].strip().lower()
                    if tab_domain and target_domain in tab_domain:
                        total += int(tab.get("request_count", 0) or 0)
                except Exception:
                    continue
            return total

        try:
            return int(getattr(session, "request_count", 0) or 0)
        except Exception:
            return 0

    def _should_trigger(self, command: Dict, session: 'TabSession') -> bool:
        trigger = command.get("trigger", {})
        trigger_type = trigger.get("type", "")
        scope = trigger.get("scope", "all")

        # Scope pre-check
        if scope == "domain":
            target_domain = str(trigger.get("domain", "") or "").strip().lower()
            session_domain = self._get_session_domain(session)
            if target_domain and session_domain:
                if target_domain not in session_domain:
                    return False
            elif target_domain:
                return False
        elif scope == "tab":
            target_index = trigger.get("tab_index")
            if target_index is not None and session.persistent_index != target_index:
                return False

        # Skip if same command already executing on this tab
        exec_key = (command["id"], session.id)
        if exec_key in self._executing:
            return False

        request_state_key = None
        scope_request_count = None
        if trigger_type == "request_count":
            request_state_key = self._build_request_count_state_key(command, session)
            scope_request_count = self._get_scope_request_count(command, session)

        # Initialize or load trigger state
        state, is_new = self._ensure_trigger_state(
            command["id"],
            session,
            state_key=request_state_key,
            initial_req=scope_request_count,
        )
        if is_new and trigger_type != "page_check":
            return False  # Newly initialized: wait for next check cycle

        # Evaluate trigger condition by trigger type
        if trigger_type == "request_count":
            threshold = max(1, self._coerce_int(trigger.get("value", 10), 10))
            current_count = (
                int(scope_request_count)
                if scope_request_count is not None
                else self._get_scope_request_count(command, session)
            )
            with self._lock:
                baseline = int(state.get("req", 0))
                delta = current_count - baseline
                should_fire = delta >= threshold
                if should_fire:
                    # 先记录旧基线；若后续因标签页忙碌/超时未实际执行，可回滚以便重试。
                    state["req_prev"] = baseline
                    state["req_pending"] = True
                    state["req"] = current_count
            if should_fire:
                logger.info(
                    f"[CMD] trigger: {command.get('name')} "
                    f"(requests={delta}>={threshold}, tab={session.id}, scope={scope})"
                )
                return True

        elif trigger_type == "error_count":
            threshold = max(1, self._coerce_int(trigger.get("value", 3), 3))
            delta = session.error_count - state["err"]
            if delta >= threshold:
                logger.info(
                    f"[CMD] trigger: {command.get('name')} "
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
                    f"[CMD] trigger: {command.get('name')} "
                    f"(idle={idle:.0f}s>={threshold_sec}s)"
                )
                return True

        elif trigger_type == "page_check":
            check_text = str(trigger.get("value", ""))
            normalized_text = check_text.lower().strip()
            current_hit = bool(check_text and self._check_page_content(session, check_text))
            fire_mode = str(trigger.get("fire_mode", "edge") or "edge").strip().lower()
            cooldown_sec = max(0.0, self._coerce_float(trigger.get("cooldown_sec", 0), 0.0))
            now_ts = time.time()

            with self._lock:
                prev_key = str(state.get("page_key", ""))
                prev_hit = bool(state.get("page_hit", False)) if prev_key == normalized_text else False
                state["page_key"] = normalized_text
                state["page_hit"] = current_hit
                last_fire_at = float(state.get("page_last_fire_at", 0.0) or 0.0)

                if fire_mode == "level":
                    if current_hit and (cooldown_sec <= 0 or (now_ts - last_fire_at) >= cooldown_sec):
                        state["page_last_fire_at"] = now_ts
                        logger.info(
                            f"[CMD] trigger: {command.get('name')} "
                            f"(page_check-level: '{check_text[:30]}', cooldown={cooldown_sec}s)"
                        )
                        return True
                else:
                    if current_hit and not prev_hit:
                        state["page_last_fire_at"] = now_ts
                        logger.info(
                            f"[CMD] trigger: {command.get('name')} "
                            f"(page_check-edge: '{check_text[:30]}')"
                        )
                        return True

        elif trigger_type == "command_result_match":
            if self._match_command_result_trigger(command, session, consume=True):
                logger.info(
                    f"[CMD] trigger: {command.get('name')} "
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
                        f"[CMD] trigger: {command.get('name')} "
                        f"(network_request_error status={event.get('status')}, url={event.get('url', '')[:80]})"
                    )
                    return True

        return False

    def _text_contains_needle(self, haystack: str, needle: str) -> bool:
        hay = str(haystack or "").strip().lower()
        ned = str(needle or "").strip().lower()
        if not hay or not ned:
            return False

        # For plain word-like keywords (for example "battle"), prefer whole-word
        # matching to reduce accidental substring hits.
        if re.fullmatch(r"[a-z0-9 _-]+", ned):
            pattern = rf"(?<![a-z0-9]){re.escape(ned)}(?![a-z0-9])"
            return re.search(pattern, hay) is not None

        return ned in hay

    def _check_page_content(self, session: 'TabSession', text: str) -> bool:
        needle = str(text or "").strip()
        if not needle:
            return False

        self._try_wake_tab(session, reason="page_check")

        body_text = ""
        try:
            body_text = str(
                session.tab.run_js(
                    "return (document.body && document.body.innerText) ? document.body.innerText : '';"
                ) or ""
            )
        except Exception:
            body_text = ""

        if body_text.strip():
            return self._text_contains_needle(body_text, needle)

        # Fallback to title only if body text is unavailable.
        try:
            title = str(session.tab.run_js("return document.title || '';") or "")
            return self._text_contains_needle(title, needle)
        except Exception:
            return False

    def _reset_page_check_latch(self, command: Dict, session: 'TabSession', reason: str = ""):
        """Allow page_check commands to retrigger when previous execution did not complete successfully."""
        trigger = command.get("trigger", {}) or {}
        if str(trigger.get("type", "")).strip().lower() != "page_check":
            return

        key = (command.get("id"), getattr(session, "id", ""))
        if not key[0] or not key[1]:
            return

        normalized_text = str(trigger.get("value", "") or "").strip().lower()
        with self._lock:
            state = self._trigger_states.get(key)
            if not state:
                return
            state["page_key"] = normalized_text
            state["page_hit"] = False

        if reason:
            logger.debug(f"[CMD] page_check latch reset: {command.get('name')} (tab={session.id}, reason={reason})")

    def _finalize_request_count_trigger_state(
        self,
        command: Dict,
        session: 'TabSession',
        *,
        rollback: bool = False
    ):
        """
        收尾 request_count 触发状态：
        - rollback=True: 未实际执行时回滚 req 到触发前基线，避免触发被“吃掉”
        - rollback=False: 实际开始执行后清理 pending 标记
        """
        trigger = command.get("trigger", {}) or {}
        if str(trigger.get("type", "")).strip().lower() != "request_count":
            return

        key = self._build_request_count_state_key(command, session)
        with self._lock:
            state = self._trigger_states.get(key)
            if not state:
                return

            pending = bool(state.pop("req_pending", False))
            prev_req = state.pop("req_prev", None)
            if rollback and pending and prev_req is not None:
                try:
                    state["req"] = int(prev_req)
                except Exception:
                    pass

    @staticmethod
    def _execution_needs_page_check_retry(execution_result: Any) -> bool:
        """
        Determine whether a page_check-triggered command should be retried.
        Retry signal is inferred from action results shaped like {"ok": False, ...}.
        """
        if not isinstance(execution_result, dict):
            return False

        direct_result = execution_result.get("result")
        if isinstance(direct_result, dict) and direct_result.get("ok") is False:
            return True

        steps = execution_result.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_result = step.get("result")
                if isinstance(step_result, dict) and step_result.get("ok") is False:
                    return True
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
            target_domain = str(trigger.get("domain", "") or "").strip().lower()
            session_domain = self._get_session_domain(session)
            if target_domain and session_domain:
                return target_domain in session_domain
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
        priority = self._get_command_priority(command)
        baseline = self._get_request_priority_baseline()
        is_high = priority > baseline
        domain = self._get_session_domain(session)
        domain_sensitive = bool(domain) and self._command_affects_domain(command)

        with self._lock:
            if exec_key in self._executing:
                return
            self._executing.add(exec_key)
            if is_high:
                self._counter_inc(self._pending_high_by_session, session.id)
                if domain_sensitive:
                    self._counter_inc(self._pending_high_by_domain, domain)

        def _run():
            acquired = False
            moved_running = False
            focus_emulation_applied = False
            cmd_task_id = f"cmd_{command['id'][:8]}_{int(time.time() * 1000)}"
            trigger = command.get("trigger", {}) or {}
            acquire_timeout = max(1.0, self._coerce_float(trigger.get("acquire_timeout_sec", 20), 20.0))
            deadline = time.time() + acquire_timeout

            try:
                while time.time() < deadline:
                    if is_high and domain_sensitive:
                        if self._has_busy_peer_on_domain(domain, exclude_session_id=session.id):
                            time.sleep(0.05)
                            continue

                    if not is_high:
                        try:
                            from app.services.request_manager import request_manager
                            status_counts = (request_manager.get_status() or {}).get("status_counts", {})
                            queued_count = int(status_counts.get("queued", 0) or 0)
                            if queued_count > 0:
                                time.sleep(0.05)
                                continue
                        except Exception:
                            pass

                    if hasattr(session, "acquire_for_command") and session.acquire_for_command(cmd_task_id):
                        acquired = True
                        break
                    status_value = str(getattr(getattr(session, "status", None), "value", "")).lower()
                    if status_value in {"closed", "error"}:
                        break
                    time.sleep(0.05)

                if not acquired:
                    logger.info(
                        f"[CMD] skip (tab busy/timeout): {command.get('name')} "
                        f"priority={priority}, timeout={acquire_timeout}s, tab={session.id}"
                    )
                    self._finalize_request_count_trigger_state(command, session, rollback=True)
                    self._reset_page_check_latch(command, session, reason="acquire_timeout")
                    return

                self._finalize_request_count_trigger_state(command, session, rollback=False)

                # Optional focus behavior: disabled by default to avoid stealing user focus.
                if self._activate_tab_on_command:
                    try:
                        browser = self._get_browser()
                        pool = getattr(browser, "_tab_pool", None)
                        active_id = getattr(pool, "_active_session_id", None) if pool is not None else None
                        if active_id != session.id and hasattr(session, "activate"):
                            session.activate()
                            if pool is not None:
                                pool._active_session_id = session.id
                    except Exception as e:
                        logger.debug(f"[CMD] activate target tab failed (ignored): {e}")
                elif self._use_focus_emulation_on_command:
                    self._set_focus_emulation(session, True)
                    focus_emulation_applied = True

                if is_high:
                    with self._lock:
                        self._counter_dec(self._pending_high_by_session, session.id)
                        self._counter_inc(self._running_high_by_session, session.id)
                        if domain_sensitive:
                            self._counter_dec(self._pending_high_by_domain, domain)
                            self._counter_inc(self._running_high_by_domain, domain)
                    moved_running = True

                execution_result = self._execute_command(command, session, chain=chain)
                if self._execution_needs_page_check_retry(execution_result):
                    self._reset_page_check_latch(command, session, reason="execution_not_ok")
            except Exception as e:
                logger.error(f"[CMD] command execution failed [{command.get('name')}]: {e}")
                if not acquired:
                    self._finalize_request_count_trigger_state(command, session, rollback=True)
                self._reset_page_check_latch(command, session, reason="execution_exception")
            finally:
                if focus_emulation_applied:
                    self._set_focus_emulation(session, False)
                if acquired:
                    try:
                        browser = self._get_browser()
                        pool = getattr(browser, "_tab_pool", None)
                        if pool is not None and hasattr(pool, "release"):
                            pool.release(session.id, check_triggers=False)
                        else:
                            session.release(clear_page=False, check_triggers=False)
                    except Exception as e:
                        logger.debug(f"[CMD] command release failed (ignored): {e}")

                with self._lock:
                    if is_high:
                        if moved_running:
                            self._counter_dec(self._running_high_by_session, session.id)
                            if domain_sensitive:
                                self._counter_dec(self._running_high_by_domain, domain)
                        else:
                            self._counter_dec(self._pending_high_by_session, session.id)
                            if domain_sensitive:
                                self._counter_dec(self._pending_high_by_domain, domain)
                    self._executing.discard(exec_key)

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"cmd-{command['id'][:8]}"
        )
        thread.start()

    def _execute_command(
        self,
        command: Dict,
        session: 'TabSession',
        chain: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
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
            return execution_result
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
            timeout_default_raw = os.getenv("CMD_EXECUTE_WORKFLOW_TIMEOUT_SEC", "45")
            timeout_sec = max(
                1.0,
                self._coerce_float(action.get("timeout_sec", timeout_default_raw), 45.0)
            )
            started_at = time.time()
            deadline = started_at + timeout_sec
            timed_out = False

            def _action_stop_checker() -> bool:
                nonlocal timed_out
                if time.time() >= deadline:
                    timed_out = True
                    return True
                return False

            if preset_name:
                effective_preset = preset_name
                logger.debug(
                    f"[CMD] 开始执行工作流: tab=#{session.persistent_index}, "
                    f"preset={effective_preset}, timeout={timeout_sec}s"
                )

                messages = [{"role": "user", "content": prompt}]
                for chunk in browser._execute_workflow_non_stream(
                    session,
                    messages,
                    preset_name=preset_name,
                    stop_checker=_action_stop_checker,
                ):
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
                if timed_out:
                    logger.warning(
                        f"[CMD] 工作流执行超时: tab=#{session.persistent_index}, "
                        f"preset={effective_preset}, timeout={timeout_sec}s"
                    )
                    return {
                        "ok": False,
                        "error": f"workflow_timeout:{timeout_sec}s",
                        "timeout": timeout_sec,
                        "preset": effective_preset,
                    }

                logger.debug(
                    f"[CMD] 工作流执行完成: tab=#{session.persistent_index}, preset={effective_preset}"
                )
                return {"ok": True, "preset": effective_preset}

            effective_preset = session.preset_name or "主预设"
            logger.debug(
                f"[CMD] 开始执行工作流: tab=#{session.persistent_index}, "
                f"preset={effective_preset}, timeout={timeout_sec}s"
            )

            messages = [{"role": "user", "content": prompt}]
            for chunk in browser._execute_workflow_non_stream(
                session,
                messages,
                stop_checker=_action_stop_checker,
            ):
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
            if timed_out:
                logger.warning(
                    f"[CMD] 工作流执行超时: tab=#{session.persistent_index}, "
                    f"preset={effective_preset}, timeout={timeout_sec}s"
                )
                return {
                    "ok": False,
                    "error": f"workflow_timeout:{timeout_sec}s",
                    "timeout": timeout_sec,
                    "preset": effective_preset,
                }

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
