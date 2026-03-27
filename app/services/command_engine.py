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
from urllib.parse import urlsplit

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from app.core.config import get_logger
from app.services.command_defs import ACTION_TYPES, TRIGGER_TYPES, CommandFlowAbort, _new_command_id, get_default_command
from app.services.command_engine_actions import CommandEngineActionsMixin
from app.services.command_engine_results import CommandEngineResultsMixin
from app.services.command_engine_runtime import CommandEngineRuntimeMixin
from app.utils.site_url import extract_remote_site_domain

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession

logger = get_logger("CMD_ENG")
FOLLOW_DEFAULT_PRESET = "__DEFAULT__"


# ================= 常量 =================

class CommandEngine(CommandEngineRuntimeMixin, CommandEngineResultsMixin, CommandEngineActionsMixin):
    """命令引擎"""

    def __init__(self):
        self._config_engine = None
        self._browser = None
        self._commands_file = None
        self._commands_local_file = None
        self._commands_mtime = 0.0
        self._commands_local_mtime = 0.0
        self._commands_loaded = False
        self._commands_cache: List[Dict[str, Any]] = []
        self._command_runtime_stats: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._commands_lock = threading.RLock()

        # 触发状态：{(command_id, tab_id): {"req": int, "err": int, ...}}
        self._trigger_states: Dict[tuple, Dict[str, Any]] = {}
        self._pending_async_trigger_meta: Dict[tuple, Dict[str, Any]] = {}
        # 最近命令执行结果：{(source_command_id, tab_id): {...}}
        self._command_results: Dict[tuple, Dict[str, Any]] = {}
        # 命令结果事件：{tab_id: [event, ...]}
        self._command_result_events: Dict[str, List[Dict[str, Any]]] = {}
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
        self._request_priority_baseline = _baseline
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
        self._last_periodic_summary_log_at = 0.0
        self._periodic_active_log_interval_sec = 8.0
        self._periodic_idle_log_interval_sec = 30.0
        # page_check observer: {session_id: set_of_keywords_installed}
        self._observer_keywords_by_session: Dict[str, set] = {}

        logger.debug("命令引擎已初始化")
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

    @staticmethod
    def _format_scope_label(scope: str) -> str:
        mapping = {
            "all": "全部标签页",
            "domain": "同域",
            "tab": "当前标签页",
        }
        return mapping.get(str(scope or "").strip().lower(), str(scope or "未指定"))

    def _set_pending_async_trigger_meta(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
        meta: Optional[Dict[str, Any]],
    ):
        key = (str(command.get("id", "")).strip(), str(getattr(session, "id", "") or ""))
        if not all(key):
            return
        with self._lock:
            if meta:
                self._pending_async_trigger_meta[key] = copy.deepcopy(meta)
            else:
                self._pending_async_trigger_meta.pop(key, None)

    def _take_pending_async_trigger_meta(
        self,
        command: Dict[str, Any],
        session: 'TabSession',
    ) -> Optional[Dict[str, Any]]:
        key = (str(command.get("id", "")).strip(), str(getattr(session, "id", "") or ""))
        if not all(key):
            return None
        with self._lock:
            meta = self._pending_async_trigger_meta.pop(key, None)
        return copy.deepcopy(meta) if meta else None

    def _should_log_periodic_summary(self, now_ts: float, due_total: int) -> bool:
        interval = (
            self._periodic_active_log_interval_sec
            if due_total > 0
            else self._periodic_idle_log_interval_sec
        )
        if (now_ts - self._last_periodic_summary_log_at) < interval:
            return False
        self._last_periodic_summary_log_at = now_ts
        return True

    def _set_focus_emulation(self, session: 'TabSession', enabled: bool):
        """Best-effort focus emulation without stealing OS/browser foreground focus."""
        try:
            session.tab.run_cdp("Emulation.setFocusEmulationEnabled", enabled=bool(enabled))
        except Exception as e:
            logger.debug(f"[CMD] 焦点模拟设置失败（忽略）: enabled={enabled}, 错误={e}")

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

    # --------- MutationObserver page_check 实时检测 ---------

    _PAGE_CHECK_OBSERVER_JS = r"""
(function() {
    var kws = %KEYWORDS%;
    function appendText(parts, value) {
        if (typeof value === 'string' && value) parts.push(value);
    }
    function collectRootText(root, parts, seen) {
        if (!root || seen.indexOf(root) !== -1) return;
        seen.push(root);
        try {
            appendText(parts, root.innerText || root.textContent || '');
        } catch (e) {}
        var nodes = [];
        try {
            nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
        } catch (e) {}
        for (var i = 0; i < nodes.length; i++) {
            var node = nodes[i];
            try {
                if (node && node.shadowRoot) collectRootText(node.shadowRoot, parts, seen);
            } catch (e) {}
        }
        var iframes = [];
        try {
            iframes = root.querySelectorAll ? root.querySelectorAll('iframe') : [];
        } catch (e) {}
        for (var j = 0; j < iframes.length; j++) {
            try {
                var frame = iframes[j];
                var doc = frame.contentDocument || (frame.contentWindow && frame.contentWindow.document);
                if (doc) collectRootText(doc.documentElement || doc.body, parts, seen);
            } catch (e) {}
        }
    }
    function hasSelector(selector) {
        try {
            return !!document.querySelector(selector);
        } catch (e) {
            return false;
        }
    }
    function buildSnapshot() {
        var parts = [];
        var seen = [];
        appendText(parts, document.title || '');
        collectRootText(document.documentElement || document.body, parts, seen);
        var text = parts.join('\n').toLowerCase();
        var cfIndicators = [];
        if (text.indexOf('security verification') !== -1) cfIndicators.push('security verification');
        if (text.indexOf('protected by cloudflare') !== -1) cfIndicators.push('protected by cloudflare');
        if (text.indexOf('verify you are human') !== -1) cfIndicators.push('verify you are human');
        if (text.indexOf('checking your browser') !== -1) cfIndicators.push('checking your browser');
        if (text.indexOf('确认您是真人') !== -1) cfIndicators.push('确认您是真人');
        if (
            hasSelector('iframe[src*="challenges.cloudflare.com"]') ||
            hasSelector('iframe[src*="turnstile"]') ||
            hasSelector('.cf-turnstile') ||
            hasSelector('[name="cf-turnstile-response"]') ||
            hasSelector('[data-testid*="cf" i]')
        ) {
            cfIndicators.push('cloudflare');
        } else if (text.indexOf('cloudflare') !== -1) {
            cfIndicators.push('cloudflare');
        }
        if (cfIndicators.length) {
            text += '\n' + cfIndicators.join('\n');
        }
        return text;
    }
    if (window.__pcObserver && window.__pcKeywords) {
        var same = kws.length === window.__pcKeywords.length &&
                   kws.every(function(k){ return window.__pcKeywords.indexOf(k) !== -1; });
        if (same) return 'already_installed';
        window.__pcObserver.disconnect();
        window.__pcObserver = null;
    }
    window.__pcKeywords = kws;
    window.__pcHits = {};
    var pending = null;
    function doCheck() {
        pending = null;
        try {
            var text = buildSnapshot();
            window.__pcSnapshot = text;
            for (var i = 0; i < window.__pcKeywords.length; i++) {
                var k = window.__pcKeywords[i];
                window.__pcHits[k] = text.indexOf(k) !== -1;
            }
        } catch(e) {}
    }
    doCheck();
    window.__pcObserver = new MutationObserver(function() {
        if (!pending) pending = setTimeout(doCheck, 200);
    });
    var target = document.body || document.documentElement;
    if (target) {
        window.__pcObserver.observe(target, {
            childList: true, subtree: true, characterData: true
        });
    }
    return 'installed';
})();
"""

    _PAGE_CHECK_SNAPSHOT_JS = r"""
return (function() {
    function appendText(parts, value) {
        if (typeof value === 'string' && value) parts.push(value);
    }
    function collectRootText(root, parts, seen) {
        if (!root || seen.indexOf(root) !== -1) return;
        seen.push(root);
        try {
            appendText(parts, root.innerText || root.textContent || '');
        } catch (e) {}
        var nodes = [];
        try {
            nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
        } catch (e) {}
        for (var i = 0; i < nodes.length; i++) {
            var node = nodes[i];
            try {
                if (node && node.shadowRoot) collectRootText(node.shadowRoot, parts, seen);
            } catch (e) {}
        }
        var iframes = [];
        try {
            iframes = root.querySelectorAll ? root.querySelectorAll('iframe') : [];
        } catch (e) {}
        for (var j = 0; j < iframes.length; j++) {
            try {
                var frame = iframes[j];
                var doc = frame.contentDocument || (frame.contentWindow && frame.contentWindow.document);
                if (doc) collectRootText(doc.documentElement || doc.body, parts, seen);
            } catch (e) {}
        }
    }
    function hasSelector(selector) {
        try {
            return !!document.querySelector(selector);
        } catch (e) {
            return false;
        }
    }
    var parts = [];
    appendText(parts, document.title || '');
    collectRootText(document.documentElement || document.body, parts, []);
    var text = parts.join('\n').toLowerCase();
    var cfIndicators = [];
    if (text.indexOf('security verification') !== -1) cfIndicators.push('security verification');
    if (text.indexOf('protected by cloudflare') !== -1) cfIndicators.push('protected by cloudflare');
    if (text.indexOf('verify you are human') !== -1) cfIndicators.push('verify you are human');
    if (text.indexOf('checking your browser') !== -1) cfIndicators.push('checking your browser');
    if (text.indexOf('确认您是真人') !== -1) cfIndicators.push('确认您是真人');
    if (
        hasSelector('iframe[src*="challenges.cloudflare.com"]') ||
        hasSelector('iframe[src*="turnstile"]') ||
        hasSelector('.cf-turnstile') ||
        hasSelector('[name="cf-turnstile-response"]') ||
        hasSelector('[data-testid*="cf" i]')
    ) {
        cfIndicators.push('cloudflare');
    } else if (text.indexOf('cloudflare') !== -1) {
        cfIndicators.push('cloudflare');
    }
    if (cfIndicators.length) {
        text += '\n' + cfIndicators.join('\n');
    }
    return text;
})();
"""

    def _collect_page_check_keywords(
        self,
        commands: List[Dict],
        session: 'TabSession',
    ) -> set:
        """Collect all page_check keywords that apply to this session."""
        keywords: set = set()
        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            trigger = cmd.get("trigger", {}) or {}
            if str(trigger.get("type", "")).strip().lower() != "page_check":
                continue
            if not bool(trigger.get("periodic_enabled", True)):
                continue
            if not self._matches_scope(cmd, session):
                continue
            value = str(trigger.get("value", "") or "").strip()
            if value:
                _, parts = self._parse_page_check_expression(value)
                for part in parts:
                    kw = part.lower().strip()
                    if kw:
                        keywords.add(kw)
        return keywords

    def _ensure_page_check_observer(
        self,
        session: 'TabSession',
        keywords: set,
    ):
        """Inject or update MutationObserver for real-time page_check text detection."""
        if not keywords:
            return
        sid = str(getattr(session, "id", "") or "")
        if not sid:
            return
        # Skip if already installed with the same keywords AND observer is still alive
        with self._lock:
            installed = self._observer_keywords_by_session.get(sid)
        if installed == keywords:
            # Quick liveness check (outside lock — run_js is I/O)
            try:
                alive = session.tab.run_js("return !!window.__pcObserver")
                if alive:
                    return
            except Exception:
                pass
            # Observer lost — clear cache, re-inject below
            with self._lock:
                if self._observer_keywords_by_session.get(sid) == installed:
                    self._observer_keywords_by_session.pop(sid, None)
        # Inject observer
        sorted_kws = sorted(keywords)
        js = self._PAGE_CHECK_OBSERVER_JS.replace(
            "%KEYWORDS%", json.dumps(sorted_kws)
        )
        try:
            result = session.tab.run_js(js)
            with self._lock:
                self._observer_keywords_by_session[sid] = set(keywords)
            if result != "already_installed":
                logger.debug(
                    f"[CMD] 页面检查观察器已注入: "
                    f"标签页={sid}, 关键词={sorted_kws}"
                )
        except Exception as e:
            logger.debug(f"[CMD] 页面检查观察器注入失败: {e}")

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
            logger.debug(f"[CMD] 刷新标签页池失败（忽略）: {e}")

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
        logger.debug("[CMD] 周期调度器已启动")

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
                logger.debug(f"[CMD] 周期调度循环异常（忽略）: {e}")

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
                logger.debug("[CMD] 周期调度器已初始化标签页池")
            except Exception as e:
                now = time.time()
                if (now - self._last_tab_pool_wait_log_at) >= 10:
                    self._last_tab_pool_wait_log_at = now
                    logger.debug(f"[CMD] 周期调度器等待标签页池初始化: {e}")
                return

        if not hasattr(pool, "get_idle_sessions_snapshot"):
            return
        self._refresh_tab_pool_if_due(pool)

        if hasattr(pool, "get_sessions_snapshot"):
            sessions = pool.get_sessions_snapshot()
        else:
            sessions = pool.get_idle_sessions_snapshot()
        if not sessions:
            return

        now = time.time()
        active_keys = set()
        due_total = 0

        enabled_commands = [
            (idx, cmd) for idx, cmd in enumerate(commands)
            if cmd.get("enabled", True)
        ]

        for session in sessions:
            session_status = str(getattr(getattr(session, "status", None), "value", "")).lower()
            if session_status not in {"idle", "busy"}:
                continue
            is_busy_workflow = session_status == "busy" and self._has_active_workflow(session)
            if session_status == "busy" and not is_busy_workflow:
                continue
            if session_status == "idle":
                self._maybe_periodic_keepalive(session, now)

            # Inject/update MutationObserver for page_check keywords on this session
            try:
                pc_keywords = self._collect_page_check_keywords(commands, session)
                if pc_keywords:
                    self._ensure_page_check_observer(session, pc_keywords)
            except Exception:
                pass

            due_commands: List[tuple[int, int, Dict[str, Any]]] = []
            for idx, cmd in enabled_commands:
                cmd_id = str(cmd.get("id", "")).strip()
                if not cmd_id:
                    continue

                trigger = cmd.get("trigger", {}) or {}
                if not bool(trigger.get("periodic_enabled", True)):
                    continue

                trigger_type = str(trigger.get("type", "")).strip().lower()
                if (
                    is_busy_workflow
                    and trigger_type == "page_check"
                    and not self._should_evaluate_page_check_while_busy_workflow(cmd)
                ):
                    # While a workflow is actively running, low-priority page_check
                    # commands should not consume transient intermediate states.
                    continue

                key = (cmd_id, session.id)
                active_keys.add(key)

                interval = max(1.0, self._coerce_float(trigger.get("periodic_interval_sec", 8), 8.0))
                jitter = max(0.0, self._coerce_float(trigger.get("periodic_jitter_sec", 2), 2.0))

                # page_check commands with observer use shorter interval (observer makes check cheap)
                if trigger_type == "page_check":
                    sid = str(getattr(session, "id", "") or "")
                    with self._lock:
                        has_observer = sid in self._observer_keywords_by_session
                    if has_observer:
                        interval = min(interval, 1.5)
                        jitter = 0.0

                with self._lock:
                    next_at = float(self._periodic_next_run.get(key, 0.0))
                if now < next_at:
                    continue

                delay = interval + (random.uniform(0.0, jitter) if jitter > 0 else 0.0)
                with self._lock:
                    self._periodic_next_run[key] = now + delay

                due_commands.append((self._get_command_priority(cmd), idx, cmd))

            due_total += len(due_commands)
            due_commands.sort(key=lambda item: (-item[0], item[1]))
            for _, _, cmd in due_commands:
                current_status = str(getattr(getattr(session, "status", None), "value", "")).lower()
                if current_status == "busy" and not self._has_active_workflow(session):
                    break
                if current_status not in {"idle", "busy"}:
                    break
                if self._should_trigger(cmd, session):
                    meta = self._take_pending_async_trigger_meta(cmd, session) or {}
                    if current_status == "busy":
                        scheduled = self._schedule_command_for_active_workflow(
                            cmd,
                            session,
                            interrupt_context=meta.get("interrupt_context"),
                            trigger_rollback=meta.get("rollback"),
                        )
                        if not scheduled:
                            if meta.get("rollback"):
                                self._rollback_trigger_consumption(cmd, session, meta.get("rollback"))
                            self._finalize_request_count_trigger_state(cmd, session, rollback=True)
                            self._reset_page_check_latch(cmd, session, reason="workflow_schedule_failed")
                    else:
                        self._execute_command_async(
                            cmd,
                            session,
                            interrupt_context=meta.get("interrupt_context"),
                            trigger_rollback=meta.get("rollback"),
                        )

        if self._should_log_periodic_summary(now, due_total):
            logger.debug(
                f"[CMD] 周期检查: 会话数={len(sessions)}, "
                f"已启用周期命令={len(active_keys)}, 本轮到点={due_total}"
            )

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

    def _get_commands_local_file(self) -> str:
        if self._commands_local_file is None:
            from app.services.config_engine import ConfigConstants
            self._commands_local_file = ConfigConstants.COMMANDS_LOCAL_FILE
        return self._commands_local_file

    def _read_commands_file(self) -> List[Dict]:
        commands_file = self._get_commands_file()
        commands = []

        if os.path.exists(commands_file):
            try:
                with open(commands_file, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    data = data.get("commands", [])

                if isinstance(data, list):
                    commands = data
                else:
                    logger.warning(f"命令配置文件格式无效: {commands_file}")
            except json.JSONDecodeError as e:
                logger.error(f"命令配置文件格式错误: {e}")
                return []
            except Exception as e:
                logger.error(f"加载命令配置失败: {e}")
                return []

        return self._apply_local_command_state(commands)

    def _load_command_state_entries(self) -> List[Dict[str, Any]]:
        local_file = self._get_commands_local_file()
        if not os.path.exists(local_file):
            self._commands_local_mtime = 0.0
            return []

        try:
            with open(local_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)

            self._commands_local_mtime = os.path.getmtime(local_file)
            entries = data.get("commands", []) if isinstance(data, dict) else []
            if not isinstance(entries, list):
                return []
            return [entry for entry in entries if isinstance(entry, dict)]
        except json.JSONDecodeError as e:
            logger.error(f"本地命令状态文件格式错误: {e}")
            return []
        except Exception as e:
            logger.error(f"加载本地命令状态失败: {e}")
            return []

    def _apply_local_command_state(self, commands: List[Dict]) -> List[Dict]:
        entries = self._load_command_state_entries()
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
            self._commands_local_mtime = os.path.getmtime(local_file) if os.path.exists(local_file) else 0.0
            return True
        except Exception as e:
            logger.error(f"保存本地命令状态失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
            return False

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
                if not self._save_local_command_state(commands_snapshot):
                    return False
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

    def _merge_runtime_stats_into_commands(self, commands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not commands:
            return commands
        with self._lock:
            runtime_stats = copy.deepcopy(self._command_runtime_stats)
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

    def _normalize_group_name(self, group_name: Any) -> str:
        return str(group_name or "").strip()

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

    # ================= CRUD =================

    def _load_commands(self) -> List[Dict]:
        """从配置引擎加载命令列表快照，避免共享可变引用。"""
        with self._commands_lock:
            self._get_config_engine()
            self._refresh_commands_if_changed()
            snapshot = self._commands_cache
        return self._merge_runtime_stats_into_commands(copy.deepcopy(snapshot))

    def list_commands(self) -> List[Dict]:
        """获取所有命令"""
        return self._load_commands()

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

        logger.info(f"[OK] 命令已添加: {command.get('name')} ({command['id']})")
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
                    logger.debug(f"[OK] 命令已更新: {cmd.get('name')} ({command_id})")
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
            if updated > 0:
                self._save_commands(commands)
        return updated

    def set_commands_enabled(self, command_ids: List[str], enabled: bool) -> int:
        """鎵归噺鏇存柊鍛戒护鍚敤鐘舵€併€?"""
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

    def set_group_enabled(self, group_name: str, enabled: bool) -> int:
        """鐩存帴鏇存柊鏁翠釜鍛戒护缁勭殑鍚敤鐘舵€併€?"""
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
        ancestry_chain: Optional[List[str]] = None,
        acquire_policy: Optional[str] = None,
    ) -> Dict[str, Any]:
        """在当前会话中顺序执行命令组内的命令。"""
        normalized_group = self._normalize_group_name(group_name)
        if not normalized_group:
            return {"ok": False, "error": "empty_group_name"}
        effective_policy = self._normalize_group_acquire_policy(acquire_policy)

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
        group_task_id = f"group_{normalized_group}_{int(time.time() * 1000)}"
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
                        pool.release(session.id, check_triggers=False)
                    else:
                        session.release(clear_page=False, check_triggers=False)
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
                    meta = self._take_pending_async_trigger_meta(cmd, session) or {}
                    self._execute_command_async(
                        cmd,
                        session,
                        interrupt_context=meta.get("interrupt_context"),
                        trigger_rollback=meta.get("rollback"),
                    )
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

        for cmd in commands:
            if not cmd.get("enabled", True):
                continue
            trigger = cmd.get("trigger", {})
            if trigger.get("type") != "network_request_error":
                continue
            if not self._matches_scope(cmd, session):
                continue
            dispatch = self._prepare_network_trigger_dispatch(cmd, session, event_copy)
            if not dispatch:
                continue
            scheduled = self._execute_command_async(
                cmd,
                session,
                interrupt_context=dispatch.get("interrupt_context"),
                trigger_rollback=dispatch.get("rollback"),
            )
            if scheduled:
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
        return p

    def _get_request_priority_baseline(self) -> int:
        return self._normalize_priority(getattr(self, "_request_priority_baseline", 2), 2)

    def _get_command_priority(self, command: Dict) -> int:
        trigger = command.get("trigger", {}) or {}
        raw = trigger.get("priority", trigger.get("command_priority", 2))
        return self._normalize_priority(raw, 2)

    def _should_evaluate_page_check_while_busy_workflow(self, command: Dict) -> bool:
        trigger = command.get("trigger", {}) or {}
        raw = trigger.get("check_while_busy_workflow", None)
        if raw is None:
            return True
        return bool(raw)

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
                if self._domain_matches(normalized, tab_domain):
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
        try:
            url = str(getattr(session.tab, "url", "") or "")
            domain = str(extract_remote_site_domain(url) or "").strip().lower()
            if domain:
                session.current_domain = domain
                return domain
        except Exception:
            pass
        domain = str(getattr(session, "current_domain", "") or "").strip().lower()
        if domain:
            return domain
        return ""

    def _get_active_workflow_runtime(self, session: 'TabSession') -> Optional[Dict[str, Any]]:
        stack = getattr(session, "_workflow_runtime_stack", None) or []
        if not stack:
            return None
        runtime = stack[-1]
        return runtime if isinstance(runtime, dict) else None

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
                    if self._domain_matches(target_domain, tab_domain):
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
                if not self._domain_matches(target_domain, session_domain):
                    return False
            elif target_domain:
                return False
        elif scope == "tab":
            target_index = trigger.get("tab_index")
            if target_index is not None and session.persistent_index != target_index:
                return False

        # Skip if same command already executing on this tab
        exec_key = (command["id"], session.id)
        with self._lock:
            if exec_key in self._executing:
                return False

        request_state_key = None
        scope_request_count = None
        initial_req = None
        if trigger_type == "request_count":
            request_state_key = self._build_request_count_state_key(command, session)
            scope_request_count = self._get_scope_request_count(command, session)
            try:
                # Count the current completed request as the first hit so
                # a threshold of N fires on the Nth completed request rather
                # than the (N+1)th after state initialization.
                initial_req = max(0, int(scope_request_count) - 1)
            except Exception:
                initial_req = scope_request_count

        # Initialize or load trigger state
        state, is_new = self._ensure_trigger_state(
            command["id"],
            session,
            state_key=request_state_key,
            initial_req=initial_req if trigger_type == "request_count" else scope_request_count,
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
            logger.debug_throttled(
                f"cmd.request_count.{command.get('id')}:{session.id}:{self._format_scope_label(scope)}",
                f"[CMD] 请求计数检查: {command.get('name')} "
                f"(当前={current_count}, 基线={baseline}, 增量={delta}, 阈值={threshold}, "
                f"标签页={session.id}, 范围={self._format_scope_label(scope)})",
                interval_sec=10.0,
            )
            if should_fire:
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(请求增量={delta}, 阈值={threshold}, 标签页={session.id}, "
                    f"范围={self._format_scope_label(scope)})"
                )
                return True

        elif trigger_type == "error_count":
            threshold = max(1, self._coerce_int(trigger.get("value", 3), 3))
            delta = session.error_count - state["err"]
            if delta >= threshold:
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(错误增量={delta}, 阈值={threshold})"
                )
                with self._lock:
                    state["err"] = session.error_count
                return True

        elif trigger_type == "idle_timeout":
            threshold_sec = max(1.0, self._coerce_float(trigger.get("value", 300), 300.0))
            idle = time.time() - session.last_used_at
            if idle >= threshold_sec:
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(空闲时长={idle:.0f}秒, 阈值={threshold_sec}秒)"
                )
                return True

        elif trigger_type == "page_check":
            check_text = str(trigger.get("value", ""))
            normalized_text = check_text.lower().strip()
            op, keywords = self._parse_page_check_expression(check_text)
            current_hit = bool(keywords and self._check_page_content_expr(session, op, keywords))
            fire_mode = str(trigger.get("fire_mode", "edge") or "edge").strip().lower()
            cooldown_sec = max(0.0, self._coerce_float(trigger.get("cooldown_sec", 0), 0.0))
            stable_for_sec = max(0.0, self._coerce_float(trigger.get("stable_for_sec", 0), 0.0))
            now_ts = time.time()

            with self._lock:
                prev_key = str(state.get("page_key", ""))
                prev_hit = bool(state.get("page_hit", False)) if prev_key == normalized_text else False
                prev_stable = bool(state.get("page_stable", False)) if prev_key == normalized_text else False
                hit_since = float(state.get("page_hit_since", 0.0) or 0.0) if prev_key == normalized_text else 0.0
                if current_hit:
                    if not prev_hit or hit_since <= 0:
                        hit_since = now_ts
                else:
                    hit_since = 0.0
                state["page_key"] = normalized_text
                state["page_hit"] = current_hit
                state["page_hit_since"] = hit_since
                last_fire_at = float(state.get("page_last_fire_at", 0.0) or 0.0)
                stable_hit = bool(current_hit and ((now_ts - hit_since) >= stable_for_sec))
                state["page_stable"] = stable_hit

                if fire_mode == "level":
                    if stable_hit and (cooldown_sec <= 0 or (now_ts - last_fire_at) >= cooldown_sec):
                        state["page_last_fire_at"] = now_ts
                        logger.info(
                            f"[CMD] 触发命令: {command.get('name')} "
                            f"(页面检查命中, 模式=持续触发, 文本='{check_text[:30]}', "
                            f"冷却={cooldown_sec}秒)"
                        )
                        return True
                else:
                    if stable_hit and not prev_stable:
                        state["page_last_fire_at"] = now_ts
                        logger.info(
                            f"[CMD] 触发命令: {command.get('name')} "
                            f"(页面检查命中, 模式=边沿触发, 文本='{check_text[:30]}')"
                        )
                        return True

        elif trigger_type == "command_result_match":
            dispatch = self._prepare_command_result_trigger_dispatch(command, session)
            if dispatch:
                self._set_pending_async_trigger_meta(command, session, dispatch)
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(命中命令结果匹配条件)"
                )
                return True

        elif trigger_type == "command_result_event":
            dispatch = self._prepare_command_result_event_dispatch(command, session)
            if dispatch:
                self._set_pending_async_trigger_meta(command, session, dispatch)
                logger.info(
                    f"[CMD] 触发命令: {command.get('name')} "
                    f"(命中命令结果事件条件)"
                )
                return True

        elif trigger_type == "network_request_error":
            dispatch = self._prepare_network_trigger_dispatch(command, session)
            if dispatch:
                event = (dispatch.get("interrupt_context") or {}).get("network_event", {})
                self._set_pending_async_trigger_meta(command, session, dispatch)
                if (dispatch.get("rollback") or {}).get("token"):
                    logger.info(
                        f"[CMD] 触发命令: {command.get('name')} "
                        f"(网络异常状态码={event.get('status')}, 地址={event.get('url', '')[:80]})"
                    )
                    return True

        return False

    @staticmethod
    def _parse_page_check_expression(value: str):
        """解析 page_check 的 value 字段，支持 || (OR) 和 && (AND) 语法。

        返回 (operator, keywords_list):
        - ("or",  [kw1, kw2, ...])  — 任意一个关键词命中即触发
        - ("and", [kw1, kw2, ...])  — 所有关键词都命中才触发
        - ("single", [value])       — 单关键词（向后兼容）

        当 || 和 && 同时出现时，|| 优先拆分。
        """
        raw = str(value or "").strip()
        if not raw:
            return ("single", [])
        if "||" in raw:
            parts = [p.strip() for p in raw.split("||") if p.strip()]
            return ("or", parts) if parts else ("single", [])
        if "&&" in raw:
            parts = [p.strip() for p in raw.split("&&") if p.strip()]
            return ("and", parts) if parts else ("single", [])
        return ("single", [raw])

    @staticmethod
    def _normalize_match_text(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return re.sub(r"\s+", " ", text)

    def _text_contains_needle(self, haystack: str, needle: str) -> bool:
        hay = self._normalize_match_text(haystack)
        ned = self._normalize_match_text(needle)
        if not hay or not ned:
            return False

        if re.search(r"[^\x00-\x7F]", ned):
            compact_hay = re.sub(r"\s+", "", hay)
            compact_ned = re.sub(r"\s+", "", ned)
            if compact_hay and compact_ned and compact_ned in compact_hay:
                return True

        # For plain word-like keywords (for example "battle"), prefer whole-word
        # matching to reduce accidental substring hits.
        if re.fullmatch(r"[a-z0-9 _-]+", ned):
            pattern = rf"(?<![a-z0-9]){re.escape(ned)}(?![a-z0-9])"
            return re.search(pattern, hay) is not None

        return ned in hay

    def _check_page_content_expr(
        self, session: 'TabSession', op: str, keywords: list
    ) -> bool:
        """根据解析后的表达式 (op, keywords) 检查页面内容。

        - op="or"    任意一个关键词命中即返回 True
        - op="and"   所有关键词都命中才返回 True
        - op="single" 等同于 and（只有一个关键词）
        """
        if not keywords:
            return False
        if op == "or":
            return any(self._check_page_content(session, kw) for kw in keywords)
        else:  # "and" / "single"
            return all(self._check_page_content(session, kw) for kw in keywords)

    def _check_page_content(self, session: 'TabSession', text: str) -> bool:
        needle = str(text or "").strip()
        if not needle:
            return False

        self._try_wake_tab(session, reason="page_check")

        # Fast path: read from MutationObserver cache (near-instant)
        try:
            observer_ok = session.tab.run_js("return !!window.__pcObserver")
            if observer_ok:
                snapshot = str(session.tab.run_js("return String(window.__pcSnapshot || '')") or "")
                if snapshot.strip():
                    return self._text_contains_needle(snapshot, needle)
        except Exception:
            pass

        # Slow fallback: full page snapshot scan (observer not installed or broken)
        page_text = ""
        try:
            page_text = str(
                session.tab.run_js(self._PAGE_CHECK_SNAPSHOT_JS) or ""
            )
        except Exception:
            page_text = ""

        if page_text.strip():
            return self._text_contains_needle(page_text, needle)

        # Fallback to title only if page text is unavailable.
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
            state["page_stable"] = False
            state["page_hit_since"] = 0.0

        if reason:
            logger.debug(
                f"[CMD] 页面检查锁存已重置: {command.get('name')} "
                f"(标签页={session.id}, 原因={reason})"
            )

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
        Retry signal is inferred from:
        - Step-level ok flag being False (exception during execution)
        - Action results shaped like {"ok": False, ...}
        - String results containing known failure prefixes (e.g. "js_failed:")
        """
        if not isinstance(execution_result, dict):
            return False

        _FAIL_PREFIXES = (
            "js_failed:",
            "python_failed:",
            "unsupported_lang:",
            "ERROR:",
            "refresh_failed:",
            "navigate_failed:",
        )

        direct_result = execution_result.get("result")
        if isinstance(direct_result, dict) and direct_result.get("ok") is False:
            return True
        if isinstance(direct_result, str) and direct_result.startswith(_FAIL_PREFIXES):
            return True

        steps = execution_result.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                # Check step-level ok flag (set by _execute_simple on exceptions)
                if step.get("ok") is False:
                    return True
                step_result = step.get("result")
                if isinstance(step_result, dict) and step_result.get("ok") is False:
                    return True
                if isinstance(step_result, str) and step_result.startswith(_FAIL_PREFIXES):
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
        }
        return mapping.get(rule_value, "equals")


# ================= 单例 =================
command_engine = CommandEngine()

__all__ = [
    'CommandEngine',
    'command_engine',
    'TRIGGER_TYPES',
    'ACTION_TYPES',
    'get_default_command',
]
