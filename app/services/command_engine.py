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
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Any, TYPE_CHECKING

from app.core.page_lifecycle import (
    BACKGROUND_WAKE_JS_TIMEOUT,
)
from app.services.command_defs import ACTION_TYPES, TRIGGER_TYPES, get_default_command
from app.services.command_engine_actions import CommandEngineActionsMixin
from app.services.command_engine_results import CommandEngineResultsMixin
from app.services.command_engine_runtime import CommandEngineRuntimeMixin
from app.services.command_engine_pages import CommandEnginePagesMixin
from app.services.command_engine_scheduler import CommandEngineSchedulerMixin
from app.services.command_engine_store import CommandEngineStoreMixin
from app.services.command_engine_crud import CommandEngineCrudMixin
from app.services.command_engine_triggers import CommandEngineTriggersMixin
from app.services.command_engine_page_check import CommandEnginePageCheckMixin

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession

# R2-3：模块级常量、日志器与可选依赖迁到 command_engine_common，这里重新导出以保持兼容
from app.services.command_engine_common import (  # noqa: E402,F401
    FOLLOW_DEFAULT_PRESET,
    HAS_REQUESTS,
    _NON_ASCII_RE,
    _WHITESPACE_RE,
    _WORD_LIKE_KEYWORD_RE,
    logger,
)


# ================= 常量 =================

class CommandEngine(
    CommandEnginePagesMixin,
    CommandEngineSchedulerMixin,
    CommandEngineStoreMixin,
    CommandEngineCrudMixin,
    CommandEngineTriggersMixin,
    CommandEnginePageCheckMixin,
    CommandEngineRuntimeMixin,
    CommandEngineResultsMixin,
    CommandEngineActionsMixin,
):
    """命令引擎"""
    def __init__(self):
        self._config_engine = None
        self._browser = None
        self._commands_file = None
        self._commands_local_file = None
        self._commands_mtime = 0.0
        self._commands_local_mtime = 0.0
        # B8：最近一次「读失败」的命令文件 mtime；同一 mtime 不重复读取/刷错误日志
        self._commands_failed_mtime: Optional[float] = None
        # (cache_key, compact_text)：page_check 去空白页面文本的单槽缓存
        self._compact_haystack_cache: Optional[tuple] = None
        self._commands_loaded = False
        self._commands_cache: List[Dict[str, Any]] = []
        self._command_runtime_stats: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._commands_lock = threading.RLock()
        self._scheduler_lifecycle_lock = threading.RLock()
        self._shutdown_requested = False

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
            _max_async_workers = int(os.getenv("CMD_ASYNC_MAX_WORKERS", "20"))
        except Exception:
            _max_async_workers = 20
        self._command_executor = ThreadPoolExecutor(
            max_workers=max(1, _max_async_workers),
            thread_name_prefix="cmd-exec",
        )
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
        # 唤醒标签页的 CDP 状态（focus / visibility / Page.setWebLifecycleState=active）
        # 是粘性的，不需要每条 page_check 命令都重发一遍。
        # 每次唤醒是 4~6 次 CDP 往返 + 一次整页可见性补丁 JS，
        # 而一个会话上往往同时挂着多条 page_check 命令，实际调用频率是每秒数次。
        # 默认 2s 限频：既能把唤醒风暴压下去，最坏也只比原来晚 2s 唤醒
        # （page_check 自身的最小轮询间隔就是 1.5s，量级相当）。
        # 设为 0 可恢复"每次都唤醒"的旧行为；若观察到后台页仍被冻结可调小。
        try:
            _wake_min_interval = float(os.getenv("CMD_WAKE_TAB_MIN_INTERVAL_SEC", "2"))
        except Exception:
            _wake_min_interval = 2.0
        self._wake_tab_min_interval_sec = max(0.0, _wake_min_interval)
        self._tab_pool_auto_refresh = str(
            os.getenv("CMD_TAB_POOL_AUTO_REFRESH", "true")
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        try:
            _refresh_interval = float(os.getenv("CMD_TAB_POOL_REFRESH_INTERVAL_SEC", "5"))
        except Exception:
            _refresh_interval = 5.0
        self._tab_pool_refresh_interval_sec = max(1.0, _refresh_interval)
        self._last_tab_pool_refresh_at = 0.0
        # In memory saver mode, do not wake every idle tab on a fixed cadence.
        # A page_check or real workflow still wakes its target tab on demand.
        memory_saver_enabled = str(
            os.getenv("BROWSER_MEMORY_SAVER", "false")
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        # P0-6：开启空闲冻结时，周期 keepalive 会不断唤醒标签页，默认关闭
        # （显式设置 CMD_PERIODIC_KEEPALIVE_ENABLED 仍以用户为准）。
        try:
            from app.core.tab_pool_parts.idle_maintenance import is_idle_freeze_enabled

            idle_freeze_enabled = is_idle_freeze_enabled()
        except Exception:
            idle_freeze_enabled = False
        keepalive_default = "false" if (memory_saver_enabled or idle_freeze_enabled) else "true"
        self._periodic_keepalive_enabled = str(
            os.getenv("CMD_PERIODIC_KEEPALIVE_ENABLED", keepalive_default)
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        try:
            _keepalive_interval = float(os.getenv("CMD_PERIODIC_KEEPALIVE_INTERVAL_SEC", "20"))
        except Exception:
            _keepalive_interval = 20.0
        self._periodic_keepalive_interval_sec = max(5.0, _keepalive_interval)
        try:
            _page_check_js_timeout = float(os.getenv("CMD_PAGE_CHECK_JS_TIMEOUT_SEC", str(BACKGROUND_WAKE_JS_TIMEOUT)))
        except Exception:
            _page_check_js_timeout = BACKGROUND_WAKE_JS_TIMEOUT
        self._page_check_js_timeout_sec = max(0.1, min(2.0, _page_check_js_timeout))
        try:
            _page_check_failure_backoff = float(os.getenv("CMD_PAGE_CHECK_FAILURE_BACKOFF_SEC", "30"))
        except Exception:
            _page_check_failure_backoff = 30.0
        self._page_check_failure_backoff_sec = max(1.0, _page_check_failure_backoff)
        try:
            _page_check_refresh_grace = float(
                os.getenv("CMD_PAGE_CHECK_REFRESH_GRACE_SEC", "2")
            )
        except Exception:
            _page_check_refresh_grace = 2.0
        self._page_check_refresh_grace_sec = max(0.2, min(10.0, _page_check_refresh_grace))
        self._last_keepalive_by_session: Dict[str, float] = {}
        self._last_tab_pool_wait_log_at = 0.0
        self._last_periodic_summary_log_at = 0.0
        self._periodic_active_log_interval_sec = 8.0
        self._periodic_idle_log_interval_sec = 30.0
        self._periodic_summary_log_enabled = str(
            os.getenv("CMD_PERIODIC_SUMMARY_LOG_ENABLED", "false")
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        # page_check observer: {session_id: set_of_keywords_installed}
        self._observer_keywords_by_session: Dict[str, set] = {}

        logger.debug("命令引擎已初始化")
        if self._should_auto_start_scheduler():
            self._start_periodic_scheduler()
    def evict_session(self, session_id: str):
        """Evict runtime caches and pending state associated with a removed tab session."""
        session_key = str(session_id or "").strip()
        if not session_key:
            return

        logger.info(f"逐出会话缓存: {session_key}")
        with self._lock:
            keys_to_remove = [
                key
                for key in self._trigger_states
                if isinstance(key, tuple) and len(key) > 1 and key[1] == session_key
            ]
            for key in keys_to_remove:
                self._trigger_states.pop(key, None)

            meta_keys_to_remove = [
                key
                for key in self._pending_async_trigger_meta
                if isinstance(key, tuple) and len(key) > 1 and key[1] == session_key
            ]
            for key in meta_keys_to_remove:
                self._pending_async_trigger_meta.pop(key, None)

            result_keys_to_remove = [
                key
                for key in self._command_results
                if isinstance(key, tuple) and len(key) > 1 and key[1] == session_key
            ]
            for key in result_keys_to_remove:
                self._command_results.pop(key, None)

            exec_keys_to_remove = [
                key
                for key in self._executing
                if isinstance(key, tuple) and len(key) > 1 and key[1] == session_key
            ]
            for key in exec_keys_to_remove:
                self._executing.discard(key)

            run_keys_to_remove = [
                key
                for key in self._periodic_next_run
                if isinstance(key, tuple) and len(key) > 1 and key[1] == session_key
            ]
            for key in run_keys_to_remove:
                self._periodic_next_run.pop(key, None)

            self._command_result_events.pop(session_key, None)
            self._network_events.pop(session_key, None)
            self._pending_high_by_session.pop(session_key, None)
            self._running_high_by_session.pop(session_key, None)
            self._last_keepalive_by_session.pop(session_key, None)
            self._observer_keywords_by_session.pop(session_key, None)
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
    _PAGE_CHECK_OBSERVER_JS = r"""
(function() {
    var kws = %KEYWORDS%;
    function newSeen() {
        // Set 判重是 O(1)；旧实现用数组 + indexOf 是 O(n)，整页遍历退化成
        // O(n^2)，长对话页面每次快照要跑上千万次比较，是浏览器端 CPU 的主要来源。
        try {
            if (typeof Set === 'function') {
                var s = new Set();
                return {
                    has: function(n) { return s.has(n); },
                    add: function(n) { s.add(n); }
                };
            }
        } catch (e) {}
        var arr = [];
        return {
            has: function(n) { return arr.indexOf(n) !== -1; },
            add: function(n) { arr.push(n); }
        };
    }
    function appendText(parts, value) {
        if (typeof value === 'string' && value) parts.push(value);
    }
    function collectText(node, parts, seen) {
        if (!node || seen.has(node)) return;
        seen.add(node);

        if (node.nodeType === 1) {
            var tag = String(node.tagName || '').toUpperCase();
            if (
                tag === 'SCRIPT' ||
                tag === 'STYLE' ||
                tag === 'NOSCRIPT' ||
                tag === 'TEMPLATE' ||
                tag === 'META' ||
                tag === 'HEAD'
            ) {
                return;
            }
            try {
                if (node.shadowRoot) collectText(node.shadowRoot, parts, seen);
            } catch (e) {}
            if (tag === 'IFRAME') {
                try {
                    var doc = node.contentDocument || (node.contentWindow && node.contentWindow.document);
                    if (doc) collectText(doc.documentElement || doc.body, parts, seen);
                } catch (e) {}
            }
        }

        if (node.nodeType === 3) {
            appendText(parts, String(node.nodeValue || '').trim());
        }

        var child = null;
        try {
            child = node.firstChild;
        } catch (e) {}
        while (child) {
            collectText(child, parts, seen);
            try {
                child = child.nextSibling;
            } catch (e) {
                child = null;
            }
        }
    }
    function collectRootText(root, parts, seen) {
        collectText(root, parts, seen);
    }
    function isElementVisible(el) {
        if (!el) return false;
        try {
            var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            var rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
            if (style) {
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (Number(style.opacity || 1) <= 0.02) return false;
            }
            return !!rect && rect.width > 5 && rect.height > 5;
        } catch (e) {
            return false;
        }
    }
    function hasVisibleSelector(selector) {
        try {
            var els = document.querySelectorAll(selector);
            for (var i = 0; i < els.length; i++) {
                if (isElementVisible(els[i])) return true;
            }
            return false;
        } catch (e) {
            return false;
        }
    }
    function buildSnapshot() {
        var parts = [];
        var seen = newSeen();
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
            hasVisibleSelector('iframe[src*="challenges.cloudflare.com"]') ||
            hasVisibleSelector('iframe[src*="turnstile"]') ||
            hasVisibleSelector('.cf-turnstile') ||
            hasVisibleSelector('[name="cf-turnstile-response"]') ||
            hasVisibleSelector('[data-testid*="cf" i]')
        ) {
            cfIndicators.push('cloudflare');
        } else if (text.indexOf('cloudflare') !== -1) {
            cfIndicators.push('cloudflare');
        }
        if (cfIndicators.length) {
            text += '\n' + cfIndicators.join('\n');
        }
        var recaptchaIndicators = [];
        if (text.indexOf('protected by recaptcha') !== -1) recaptchaIndicators.push('protected by recaptcha');
        if (
            hasVisibleSelector('iframe[src*="google.com/recaptcha"]') ||
            hasVisibleSelector('iframe[src*="recaptcha"]') ||
            hasVisibleSelector('.g-recaptcha') ||
            hasVisibleSelector('[name="g-recaptcha-response"]') ||
            hasVisibleSelector('[title*="recaptcha" i]') ||
            hasVisibleSelector('[aria-label*="recaptcha" i]')
        ) {
            recaptchaIndicators.push('recaptcha');
            recaptchaIndicators.push('\u4eba\u673a\u8eab\u4efd\u9a8c\u8bc1');
        } else if (text.indexOf('recaptcha') !== -1) {
            recaptchaIndicators.push('recaptcha');
        }
        if (recaptchaIndicators.length) {
            text += '\n' + recaptchaIndicators.join('\n');
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
    var MIN_DELAY_MS = 200;
    var MAX_DELAY_MS = 800;
    // 自适应节流：下一次扫描的间隔取"上次扫描耗时 × 4"，钳在 200~800ms。
    // 换成 Set 判重后，即使 6 万节点的页面单次扫描也只要 30ms 左右，
    // 算出来仍是下限 200ms —— 正常页面行为完全不变。
    // 只有扫描耗时超过 50ms 的病态页面才会退避，且最多退到 800ms，
    // 不会拖慢 page_check 对 Cloudflare 盾 / 报错文案的识别。
    var nextDelayMs = MIN_DELAY_MS;
    function doCheck() {
        pending = null;
        var startedAt = 0;
        try {
            startedAt = (window.performance && performance.now) ? performance.now() : Date.now();
        } catch (e) {}
        try {
            var text = buildSnapshot();
            window.__pcSnapshot = text;
            for (var i = 0; i < window.__pcKeywords.length; i++) {
                var k = window.__pcKeywords[i];
                window.__pcHits[k] = text.indexOf(k) !== -1;
            }
        } catch(e) {}
        try {
            var endedAt = (window.performance && performance.now) ? performance.now() : Date.now();
            var cost = Math.max(0, endedAt - startedAt);
            nextDelayMs = Math.min(MAX_DELAY_MS, Math.max(MIN_DELAY_MS, Math.round(cost * 4)));
        } catch (e) {
            nextDelayMs = MIN_DELAY_MS;
        }
    }
    doCheck();
    window.__pcObserver = new MutationObserver(function() {
        if (!pending) pending = setTimeout(doCheck, nextDelayMs);
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
    function newSeen() {
        // 同 _PAGE_CHECK_OBSERVER_JS：数组 indexOf 判重会让整页遍历退化成 O(n^2)。
        try {
            if (typeof Set === 'function') {
                var s = new Set();
                return {
                    has: function(n) { return s.has(n); },
                    add: function(n) { s.add(n); }
                };
            }
        } catch (e) {}
        var arr = [];
        return {
            has: function(n) { return arr.indexOf(n) !== -1; },
            add: function(n) { arr.push(n); }
        };
    }
    function appendText(parts, value) {
        if (typeof value === 'string' && value) parts.push(value);
    }
    function collectText(node, parts, seen) {
        if (!node || seen.has(node)) return;
        seen.add(node);

        if (node.nodeType === 1) {
            var tag = String(node.tagName || '').toUpperCase();
            if (
                tag === 'SCRIPT' ||
                tag === 'STYLE' ||
                tag === 'NOSCRIPT' ||
                tag === 'TEMPLATE' ||
                tag === 'META' ||
                tag === 'HEAD'
            ) {
                return;
            }
            try {
                if (node.shadowRoot) collectText(node.shadowRoot, parts, seen);
            } catch (e) {}
            if (tag === 'IFRAME') {
                try {
                    var doc = node.contentDocument || (node.contentWindow && node.contentWindow.document);
                    if (doc) collectText(doc.documentElement || doc.body, parts, seen);
                } catch (e) {}
            }
        }

        if (node.nodeType === 3) {
            appendText(parts, String(node.nodeValue || '').trim());
        }

        var child = null;
        try {
            child = node.firstChild;
        } catch (e) {}
        while (child) {
            collectText(child, parts, seen);
            try {
                child = child.nextSibling;
            } catch (e) {
                child = null;
            }
        }
    }
    function collectRootText(root, parts, seen) {
        collectText(root, parts, seen);
    }
    function isElementVisible(el) {
        if (!el) return false;
        try {
            var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            var rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
            if (style) {
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (Number(style.opacity || 1) <= 0.02) return false;
            }
            return !!rect && rect.width > 5 && rect.height > 5;
        } catch (e) {
            return false;
        }
    }
    function hasVisibleSelector(selector) {
        try {
            var els = document.querySelectorAll(selector);
            for (var i = 0; i < els.length; i++) {
                if (isElementVisible(els[i])) return true;
            }
            return false;
        } catch (e) {
            return false;
        }
    }
    var parts = [];
    appendText(parts, document.title || '');
    collectRootText(document.documentElement || document.body, parts, newSeen());
    var text = parts.join('\n').toLowerCase();
    var cfIndicators = [];
    if (text.indexOf('security verification') !== -1) cfIndicators.push('security verification');
    if (text.indexOf('protected by cloudflare') !== -1) cfIndicators.push('protected by cloudflare');
    if (text.indexOf('verify you are human') !== -1) cfIndicators.push('verify you are human');
    if (text.indexOf('checking your browser') !== -1) cfIndicators.push('checking your browser');
    if (text.indexOf('确认您是真人') !== -1) cfIndicators.push('确认您是真人');
    if (
        hasVisibleSelector('iframe[src*="challenges.cloudflare.com"]') ||
        hasVisibleSelector('iframe[src*="turnstile"]') ||
        hasVisibleSelector('.cf-turnstile') ||
        hasVisibleSelector('[name="cf-turnstile-response"]') ||
        hasVisibleSelector('[data-testid*="cf" i]')
    ) {
        cfIndicators.push('cloudflare');
    } else if (text.indexOf('cloudflare') !== -1) {
        cfIndicators.push('cloudflare');
    }
    if (cfIndicators.length) {
        text += '\n' + cfIndicators.join('\n');
    }
    var recaptchaIndicators = [];
    if (text.indexOf('protected by recaptcha') !== -1) recaptchaIndicators.push('protected by recaptcha');
    if (
        hasVisibleSelector('iframe[src*="google.com/recaptcha"]') ||
        hasVisibleSelector('iframe[src*="recaptcha"]') ||
        hasVisibleSelector('.g-recaptcha') ||
        hasVisibleSelector('[name="g-recaptcha-response"]') ||
        hasVisibleSelector('[title*="recaptcha" i]') ||
        hasVisibleSelector('[aria-label*="recaptcha" i]')
    ) {
        recaptchaIndicators.push('recaptcha');
        recaptchaIndicators.push('\u4eba\u673a\u8eab\u4efd\u9a8c\u8bc1');
    } else if (text.indexOf('recaptcha') !== -1) {
        recaptchaIndicators.push('recaptcha');
    }
    if (recaptchaIndicators.length) {
        text += '\n' + recaptchaIndicators.join('\n');
    }
    return text;
})();
"""
    def shutdown(self):
        with self._scheduler_lifecycle_lock:
            self._shutdown_requested = True
            self._periodic_stop_event.set()
            thread = self._periodic_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        try:
            self._command_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._command_executor.shutdown(wait=False)
        except Exception as e:
            logger.debug(f"[CMD] 命令线程池关闭失败（忽略）: {e}")


# ================= 单例 =================
command_engine = CommandEngine()

__all__ = [
    'CommandEngine',
    'command_engine',
    'TRIGGER_TYPES',
    'ACTION_TYPES',
    'get_default_command',
]
