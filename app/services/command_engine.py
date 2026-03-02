"""
app/services/command_engine.py - 命令引擎

职责：
- 命令的 CRUD 管理
- 触发条件检查（在标签页释放后调用）
- 动作执行调度
- 高级模式脚本执行（JavaScript / Python）

存储位置：sites.json → _global.commands
"""

import copy
import random
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
}

ACTION_TYPES = {
    "clear_cookies": "清除当前标签页的 Cookie",
    "refresh_page": "刷新页面",
    "new_chat": "点击新建对话按钮",
    "run_js": "在页面中执行 JavaScript",
    "wait": "等待指定秒数",
    "switch_preset": "切换标签页预设",
    "navigate": "导航到指定 URL",
    "switch_proxy": "切换代理节点（Clash）",
}


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
            "scope": "all",
            "domain": "",
            "tab_index": None,
        },
        "actions": [
            {"type": "clear_cookies"},
            {"type": "refresh_page"},
        ],
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
        self._lock = threading.Lock()

        # 触发状态：{(command_id, tab_id): {"req": int, "err": int}}
        self._trigger_states: Dict[tuple, Dict[str, int]] = {}
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

    # ================= CRUD =================

    def _load_commands(self) -> List[Dict]:
        """从配置引擎加载命令列表（可变引用）"""
        engine = self._get_config_engine()
        engine.refresh_if_changed()
        commands = engine.global_config.get("commands")
        if commands is None:
            commands = []
            engine.global_config.set("commands", commands)
        return commands

    def list_commands(self) -> List[Dict]:
        """获取所有命令（深拷贝）"""
        return copy.deepcopy(self._load_commands())

    def get_command(self, command_id: str) -> Optional[Dict]:
        for cmd in self.list_commands():
            if cmd.get("id") == command_id:
                return cmd
        return None

    def add_command(self, command: Dict = None) -> Dict:
        engine = self._get_config_engine()

        if command is None:
            command = get_default_command()
        else:
            if not command.get("id"):
                command["id"] = _new_command_id()

        commands = self._load_commands()
        commands.append(command)
        engine.global_config.set("commands", commands)
        engine.save_config()

        logger.info(f"✅ 命令已添加: {command.get('name')} ({command['id']})")
        return copy.deepcopy(command)

    def update_command(self, command_id: str, updates: Dict) -> Optional[Dict]:
        engine = self._get_config_engine()
        commands = self._load_commands()

        for i, cmd in enumerate(commands):
            if cmd.get("id") == command_id:
                updates.pop("id", None)
                cmd.update(updates)
                commands[i] = cmd
                engine.global_config.set("commands", commands)
                engine.save_config()
                logger.info(f"✅ 命令已更新: {cmd.get('name')} ({command_id})")
                return copy.deepcopy(cmd)

        return None

    def delete_command(self, command_id: str) -> bool:
        engine = self._get_config_engine()
        commands = self._load_commands()
        new_commands = [c for c in commands if c.get("id") != command_id]

        if len(new_commands) == len(commands):
            return False

        engine.global_config.set("commands", new_commands)
        engine.save_config()

        # 清理触发状态
        with self._lock:
            keys_to_remove = [k for k in self._trigger_states if k[0] == command_id]
            for k in keys_to_remove:
                del self._trigger_states[k]

        logger.info(f"✅ 命令已删除: {command_id}")
        return True

    def reorder_commands(self, command_ids: List[str]) -> bool:
        engine = self._get_config_engine()
        commands = self._load_commands()
        cmd_map = {c["id"]: c for c in commands}
        new_commands = []

        for cid in command_ids:
            if cid in cmd_map:
                new_commands.append(cmd_map.pop(cid))

        for remaining in cmd_map.values():
            new_commands.append(remaining)

        engine.global_config.set("commands", new_commands)
        engine.save_config()
        return True

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
        state_key = (command["id"], session.id)
        with self._lock:
            if state_key not in self._trigger_states:
                self._trigger_states[state_key] = {
                    "req": session.request_count,
                    "err": session.error_count,
                }
                return False  # 首次注册不触发

            state = self._trigger_states[state_key]

        # 按类型检查
        if trigger_type == "request_count":
            threshold = int(trigger.get("value", 10))
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
            threshold = int(trigger.get("value", 3))
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
            threshold_sec = float(trigger.get("value", 300))
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

        return False

    def _check_page_content(self, session: 'TabSession', text: str) -> bool:
        try:
            html = session.tab.html or ""
            return text.lower() in html.lower()
        except Exception:
            return False

    # ================= 动作执行 =================

    def _execute_command_async(self, command: Dict, session: 'TabSession'):
        exec_key = (command["id"], session.id)
        self._executing.add(exec_key)

        def _run():
            try:
                self._execute_command(command, session)
            except Exception as e:
                logger.error(f"[CMD] 命令执行失败 [{command.get('name')}]: {e}")
            finally:
                self._executing.discard(exec_key)

        thread = threading.Thread(
            target=_run, daemon=True,
            name=f"cmd-{command['id'][:8]}"
        )
        thread.start()

    def _execute_command(self, command: Dict, session: 'TabSession'):
        cmd_name = command.get("name", "未命名")
        mode = command.get("mode", "simple")

        logger.info(f"[CMD] ▶ 执行: {cmd_name} (mode={mode}, tab={session.id})")

        self._update_trigger_stats(command["id"])

        if mode == "advanced":
            self._execute_advanced(command, session)
        else:
            self._execute_simple(command, session)

        logger.info(f"[CMD] ✅ 完成: {cmd_name}")

    def _execute_simple(self, command: Dict, session: 'TabSession'):
        actions = command.get("actions", [])
        for i, action in enumerate(actions):
            action_type = action.get("type", "")
            logger.debug(f"[CMD] 步骤 {i + 1}/{len(actions)}: {action_type}")
            try:
                self._execute_action(action, session)
            except Exception as e:
                logger.error(f"[CMD] 步骤 {i + 1} 失败 ({action_type}): {e}")

    def _execute_action(self, action: Dict, session: 'TabSession'):
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
            except Exception as e:
                logger.warning(f"[CMD] 清除 Cookies 失败: {e}")

        elif action_type == "refresh_page":
            try:
                tab.refresh()
                time.sleep(2)
                logger.debug("[CMD] 页面已刷新")
            except Exception as e:
                logger.warning(f"[CMD] 刷新页面失败: {e}")

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
                        else:
                            logger.warning("[CMD] 新建对话按钮未找到")
                    else:
                        logger.warning("[CMD] 未配置 new_chat_btn 选择器")
            except Exception as e:
                logger.warning(f"[CMD] 新建对话失败: {e}")

        elif action_type == "run_js":
            code = action.get("code", "")
            if code:
                try:
                    result = tab.run_js(code)
                    logger.debug(f"[CMD] JS 执行完成: {str(result)[:100]}")
                except Exception as e:
                    logger.warning(f"[CMD] JS 执行失败: {e}")

        elif action_type == "wait":
            seconds = float(action.get("seconds", 1))
            time.sleep(seconds)
            logger.debug(f"[CMD] 等待 {seconds}s")

        elif action_type == "switch_preset":
            preset_name = action.get("preset_name", "")
            if preset_name:
                try:
                    browser = self._get_browser()
                    browser.tab_pool.set_tab_preset(
                        session.persistent_index, preset_name
                    )
                    logger.debug(f"[CMD] 预设已切换: {preset_name}")
                except Exception as e:
                    logger.warning(f"[CMD] 切换预设失败: {e}")

        elif action_type == "navigate":
            url = action.get("url", "")
            if url:
                try:
                    tab.get(url)
                    time.sleep(2)
                    logger.debug(f"[CMD] 已导航到: {url}")
                except Exception as e:
                    logger.warning(f"[CMD] 导航失败: {e}")

        elif action_type == "switch_proxy":
            self._execute_switch_proxy(action, session)

        else:
            logger.warning(f"[CMD] 未知动作类型: {action_type}")
    # ================= 代理切换 =================

    def _execute_switch_proxy(self, action: Dict, session: 'TabSession'):
        """
        执行代理节点切换（通过 Clash API）
        
        action 参数：
        - clash_api: Clash API 地址，默认 http://127.0.0.1:9090
        - clash_secret: Clash 密钥（可选）
        - selector: 代理组名称，默认 Proxy
        - mode: 切换模式 - random（随机）/ round_robin（轮询）/ specific（指定节点）
        - node_name: mode=specific 时使用的节点名称
        - exclude_keywords: 排除包含这些关键词的节点（逗号分隔）
        - refresh_after: 切换后是否刷新页面
        """
        if not HAS_REQUESTS:
            logger.error("[CMD] 切换代理需要 requests 库，请运行: pip install requests")
            return

        # 读取配置
        clash_api = action.get("clash_api", "http://127.0.0.1:9090").rstrip("/")
        clash_secret = action.get("clash_secret", "")
        selector = action.get("selector", "Proxy")
        mode = action.get("mode", "random")
        node_name = action.get("node_name", "")
        exclude_str = action.get("exclude_keywords", "DIRECT,REJECT,GLOBAL,自动选择,故障转移")
        refresh_after = action.get("refresh_after", True)

        # 排除关键词列表
        exclude_keywords = [k.strip() for k in exclude_str.split(",") if k.strip()]

        headers = {"Content-Type": "application/json"}
        if clash_secret:
            headers["Authorization"] = f"Bearer {clash_secret}"

        try:
            # 1. 获取代理组信息
            resp = requests.get(
                f"{clash_api}/proxies/{selector}",
                headers=headers,
                timeout=5
            )
            
            if resp.status_code == 404:
                logger.error(f"[CMD] 代理组 '{selector}' 不存在，请检查 Clash 配置")
                return
            
            resp.raise_for_status()
            data = resp.json()

            # 2. 获取当前节点和所有可用节点
            current_node = data.get("now", "")
            all_nodes = data.get("all", [])

            # 3. 过滤节点
            available = []
            for node in all_nodes:
                # 排除包含关键词的节点
                should_exclude = False
                for keyword in exclude_keywords:
                    if keyword and keyword in node:
                        should_exclude = True
                        break
                if not should_exclude:
                    available.append(node)

            if not available:
                logger.warning("[CMD] 没有可用的代理节点")
                return

            # 4. 选择新节点
            new_node = None

            if mode == "specific":
                # 指定节点模式
                if node_name in available:
                    new_node = node_name
                else:
                    logger.warning(f"[CMD] 指定节点 '{node_name}' 不可用，回退到随机模式")
                    mode = "random"

            if mode == "random":
                # 随机模式（排除当前节点）
                candidates = [n for n in available if n != current_node]
                if candidates:
                    new_node = random.choice(candidates)
                else:
                    new_node = random.choice(available)

            elif mode == "round_robin":
                # 轮询模式
                try:
                    current_idx = available.index(current_node)
                    next_idx = (current_idx + 1) % len(available)
                    new_node = available[next_idx]
                except ValueError:
                    # 当前节点不在列表中，选择第一个
                    new_node = available[0]

            if not new_node:
                logger.warning("[CMD] 无法选择新节点")
                return

            if new_node == current_node:
                logger.info(f"[CMD] 当前已是节点: {current_node}，跳过切换")
                return

            # 5. 执行切换
            switch_resp = requests.put(
                f"{clash_api}/proxies/{selector}",
                json={"name": new_node},
                headers=headers,
                timeout=5
            )
            switch_resp.raise_for_status()

            logger.info(f"[CMD] ✅ 代理已切换: {current_node} → {new_node}")

            # 6. 刷新页面
            if refresh_after:
                time.sleep(1)
                try:
                    session.tab.refresh()
                    time.sleep(2)
                    logger.debug("[CMD] 页面已刷新")
                except Exception as e:
                    logger.warning(f"[CMD] 刷新页面失败: {e}")

        except requests.exceptions.ConnectionError:
            logger.error(f"[CMD] ❌ 无法连接到 Clash API ({clash_api})，请检查 Clash 是否运行")
        except requests.exceptions.Timeout:
            logger.error("[CMD] ❌ Clash API 请求超时")
        except requests.exceptions.HTTPError as e:
            logger.error(f"[CMD] ❌ Clash API 错误: {e}")
        except Exception as e:
            logger.error(f"[CMD] ❌ 切换代理失败: {e}")
    # ================= 高级模式 =================

    def _execute_advanced(self, command: Dict, session: 'TabSession'):
        script = command.get("script", "")
        lang = command.get("script_lang", "javascript")

        if not script.strip():
            logger.warning("[CMD] 高级模式脚本为空")
            return

        if lang == "javascript":
            try:
                result = session.tab.run_js(script)
                logger.info(f"[CMD] JS 脚本执行完成: {str(result)[:200]}")
            except Exception as e:
                logger.error(f"[CMD] JS 脚本执行失败: {e}")

        elif lang == "python":
            import json as json_module
            context = {
                "tab": session.tab,
                "session": session,
                "browser": self._get_browser(),
                "config_engine": self._get_config_engine(),
                "logger": logger,
                "time": time,
                "json": json_module,
            }
            try:
                exec(script, {"__builtins__": __builtins__}, context)
                logger.info("[CMD] Python 脚本执行完成")
            except Exception as e:
                logger.error(f"[CMD] Python 脚本执行失败: {e}")
        else:
            logger.warning(f"[CMD] 不支持的脚本语言: {lang}")

    # ================= 统计 =================

    def _update_trigger_stats(self, command_id: str):
        engine = self._get_config_engine()
        commands = self._load_commands()

        for cmd in commands:
            if cmd.get("id") == command_id:
                cmd["last_triggered"] = time.time()
                cmd["trigger_count"] = cmd.get("trigger_count", 0) + 1
                break

        engine.global_config.set("commands", commands)
        engine.save_config()

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