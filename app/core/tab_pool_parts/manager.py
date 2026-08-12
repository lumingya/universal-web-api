import asyncio
import json
import os
import random
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from app.core.config import BrowserConstants, logger
from app.utils.site_url import (
    encode_tab_url_route_token,
    extract_remote_site_domain,
    normalize_exact_tab_url,
    normalize_route_domain,
    route_domain_matches,
    tab_url_matches,
)
from app.utils.tab_route_groups import (
    normalize_route_group_id,
    normalize_route_groups,
    route_group_member_key,
    route_groups_by_id,
)

from ._utils import _looks_like_transient_local_debug_error, _should_skip_pool_url
from .network import _GlobalNetworkInterceptionManager
from .recovery import TabQuarantineEntry, TabRecoveryService
from .session import TabSession, TabStatus


class TabPoolManager:
    """标签页池管理器"""

    DOMAIN_ABBR_MAP = {
        "chatgpt": "gpt",
        "openai": "gpt",
        "gemini": "gemini",
        "aistudio": "aistudio",
        "claude": "claude",
        "anthropic": "claude",
        "poe": "poe",
        "bing": "bing",
        "copilot": "copilot",
        "perplexity": "pplx",
        "lmarena": "lmarena",
        "chat": "chat",
    }

    # 卡死超时时间（秒）
    STUCK_TIMEOUT = 180

    # 新标签页扫描间隔（秒）
    SCAN_INTERVAL = 10
    QUERY_SCAN_MIN_INTERVAL_SEC = 1.0
    ISOLATED_CONTEXT_ORPHAN_GRACE_SEC = 3.0
    ISOLATED_CONTEXT_REBIND_GRACE_SEC = 20.0
    GET_TABS_FAILURE_COOLDOWN_SEC = 5.0
    GET_TABS_WARNING_INTERVAL_SEC = 10.0
    ROUTE_CURSOR_LIMIT = 1000
    MAINTENANCE_WORKER_LIMIT = 4
    TERMINATION_RELEASE_WAIT_SEC = 5.0
    IDLE_MEMORY_PURGE_MIN_SEC = 60.0
    CONTEXT_RECOVERY_CDP_TIMEOUT_SEC = 2.0
    CONTEXT_RECOVERY_COOLDOWN_SEC = 30.0
    CONTEXT_RECOVERY_CACHE_LIMIT = 2048

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _normalize_allocation_mode(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"round_robin", "random"}:
            return normalized
        return "first_idle"

    def __init__(
        self,
        browser_page,
        max_tabs: int = 5,
        min_tabs: int = 1,
        idle_timeout: float = 300,
        acquire_timeout: float = 60,
        stuck_timeout: float = STUCK_TIMEOUT,
        allocation_mode: str = "first_idle",
        excluded_urls: Optional[List[str]] = None,
        preserve_error_tabs: bool = False,
        model_name_overrides: Optional[Dict[str, Any]] = None,
        route_groups: Optional[List[Dict[str, Any]]] = None,
    ):
        self.page = browser_page
        self.max_tabs = max(1, int(max_tabs))
        self.min_tabs = min(self.max_tabs, max(1, int(min_tabs)))
        self.idle_timeout = max(1.0, float(idle_timeout))
        self.acquire_timeout = max(1.0, float(acquire_timeout))
        self.stuck_timeout = max(1.0, float(stuck_timeout))
        self.allocation_mode = self._normalize_allocation_mode(allocation_mode)
        self.excluded_urls = self._normalize_excluded_urls(excluded_urls)
        self.preserve_error_tabs = self._to_bool(preserve_error_tabs, False)
        self.model_name_overrides = self._normalize_model_name_overrides(model_name_overrides)
        self.route_groups = normalize_route_groups(route_groups)

        self._tabs: Dict[str, TabSession] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._scan_snapshot_lock = threading.Lock()
        self._global_monitor_transition_lock = threading.RLock()

        self._initialized = False
        self._shutdown = False
        self._tab_counter = 0

        self._last_scan_time: float = 0
        self._get_tabs_retry_after: float = 0.0
        self._last_get_tabs_warning_at: float = 0.0
        self._context_recovery_lock = threading.Lock()
        self._context_recovery_pending: set[str] = set()
        self._context_recovery_requested_at: Dict[str, float] = {}

        # 记录已知的标签页底层 ID（用于检测新标签页）
        self._known_tab_ids: set = set()
        # 🆕 记录当前活动的标签页 ID（避免重复激活）
        self._active_session_id: Optional[str] = None
        self._auto_activate_on_acquire = self._to_bool(
            os.getenv("TAB_AUTO_ACTIVATE_ON_ACQUIRE"), False
        )
        self._acquire_waiters: deque[str] = deque()
        self._index_waiters: Dict[int, deque[str]] = {}
        self._route_waiters: Dict[str, deque[str]] = {}
        self._group_waiters: Dict[str, deque[str]] = {}
        self._route_group_bindings: Dict[str, Dict[str, str]] = {}
        self._waiter_counter = 0

        # 🆕 持久化编号系统
        self._next_persistent_index: int = 1  # 下一个可分配的编号
        self._raw_id_to_persistent: Dict[str, int] = {}  # raw_tab_id → persistent_index
        self._persistent_to_session_id: Dict[int, str] = {}  # persistent_index → session.id
        self._isolated_context_by_raw_id: Dict[str, str] = {}
        self._orphaned_isolated_contexts: Dict[str, float] = {}
        self._round_robin_cursor: int = 0
        self._route_round_robin_cursor: OrderedDict[str, int] = OrderedDict()
        self._preserved_error_session_ids = set()
        self._maintenance_executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
            max_workers=self.MAINTENANCE_WORKER_LIMIT,
            thread_name_prefix="tab-maint",
        )
        # acquire 专用线程池（懒创建）：阻塞式 acquire 可占线程最长 acquire_timeout，
        # 若复用 asyncio 默认 executor，池满时一批被取消/慢的 acquire 会占满默认
        # executor，拖垮事件循环上所有 to_thread 调用。创建用独立小锁保护，
        # 避免事件循环线程为建池去竞争可能被长持有的池锁。
        self._acquire_executor: Optional[ThreadPoolExecutor] = None
        self._acquire_executor_lock = threading.Lock()

        # 错误标签页隔离集合（池锁 self._condition 保护下读写）。
        # ERROR 会话被移出池但浏览器标签页保留时，其 raw id 先进隔离集合，
        # 阻止 _scan_new_tabs 立即把同一 tab 当"新标签页"重新入池——
        # 旧请求的 worker 线程可能仍阻塞在对该 tab 的同步 DrissionPage 调用里，
        # 直接重入池会造成新旧工作流并发驱动同一页面。
        self._quarantined_raw_ids: Dict[str, TabQuarantineEntry] = {}
        # 恢复服务懒创建：创建仅涉及线程池构造（非阻塞），
        # 用独立小锁保护，不与池锁竞争。
        self._recovery_service: Optional[TabRecoveryService] = None
        self._recovery_service_lock = threading.Lock()

        # 全局常驻网络监听（可配置）
        self._global_network_enabled = self._to_bool(
            BrowserConstants.get("GLOBAL_NETWORK_INTERCEPTION_ENABLED"), False
        )
        self._global_network_listen_pattern = str(
            BrowserConstants.get("GLOBAL_NETWORK_INTERCEPTION_LISTEN_PATTERN") or "http"
        ).strip() or "http"
        self._global_network_wait_timeout = max(
            0.1,
            self._to_float(BrowserConstants.get("GLOBAL_NETWORK_INTERCEPTION_WAIT_TIMEOUT"), 0.5),
        )
        self._global_network_retry_delay = max(
            0.2,
            self._to_float(BrowserConstants.get("GLOBAL_NETWORK_INTERCEPTION_RETRY_DELAY"), 1.0),
        )
        # A JavaScript heap purge does not freeze or discard a page. Run it
        # only for sessions that have been idle for a while, so DOM clicks and
        # active workflows keep their existing timing behavior.
        self._idle_memory_purge_enabled = self._to_bool(
            os.getenv("BROWSER_IDLE_MEMORY_PURGE_ENABLED"),
            True,
        )
        self._idle_memory_purge_after_sec = max(
            self.IDLE_MEMORY_PURGE_MIN_SEC,
            self._to_float(os.getenv("BROWSER_IDLE_MEMORY_PURGE_AFTER_SEC"), 300.0),
        )
        self._idle_memory_purge_interval_sec = max(
            self.IDLE_MEMORY_PURGE_MIN_SEC,
            self._to_float(os.getenv("BROWSER_IDLE_MEMORY_PURGE_INTERVAL_SEC"), 180.0),
        )
        self._global_network_monitor: Optional[_GlobalNetworkInterceptionManager] = None
        if self._global_network_enabled:
            self._global_network_monitor = _GlobalNetworkInterceptionManager(
                get_session_fn=self._get_session_for_monitor_snapshot,
                is_shutdown_fn=lambda: self._shutdown,
                listen_pattern=self._global_network_listen_pattern,
                wait_timeout=self._global_network_wait_timeout,
                retry_delay=self._global_network_retry_delay,
            )

        logger.debug(
            f"TabPoolManager 初始化 (max={max_tabs}, stuck_timeout={self.stuck_timeout}s, "
            f"allocation_mode={self.allocation_mode}, preserve_error_tabs={self.preserve_error_tabs})"
        )

    @staticmethod
    def _normalize_excluded_urls(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []

        normalized: List[str] = []
        seen = set()
        for item in value:
            text = str(item or "").strip()
            normalized_text = normalize_exact_tab_url(text) or text
            if not normalized_text or normalized_text in seen:
                continue
            seen.add(normalized_text)
            normalized.append(normalized_text)
        return normalized

    @staticmethod
    def _normalize_model_name(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _normalize_model_name_overrides(cls, value: Any) -> Dict[str, Dict[str, str]]:
        payload = value if isinstance(value, dict) else {}
        normalized = {"sites": {}, "urls": {}}

        sites = payload.get("sites") if isinstance(payload, dict) else {}
        if isinstance(sites, dict):
            for key, model_name in sites.items():
                route_key = normalize_route_domain(key)
                display_name = cls._normalize_model_name(model_name)
                if route_key and display_name:
                    normalized["sites"][route_key] = display_name

        urls = payload.get("urls") if isinstance(payload, dict) else {}
        if isinstance(urls, dict):
            for key, model_name in urls.items():
                url_key = normalize_exact_tab_url(str(key or "").strip())
                display_name = cls._normalize_model_name(model_name)
                if url_key and display_name:
                    normalized["urls"][url_key] = display_name

        return normalized

    @staticmethod
    def _default_model_name_for_info(info: Dict[str, Any]) -> str:
        route_domain = str(info.get("route_domain") or "").strip()
        current_domain = str(info.get("current_domain") or "").strip()
        tab_id = str(info.get("id") or "").strip()
        return route_domain or current_domain or tab_id or "web-browser"

    def _apply_exposed_model_name(self, info: Dict[str, Any]) -> None:
        default_name = self._default_model_name_for_info(info)
        override_source = ""
        override_name = self._normalize_model_name(info.get("model_name_override"))

        if override_name:
            override_source = "tab"
        else:
            overrides = self._normalize_model_name_overrides(
                getattr(self, "model_name_overrides", None)
            )
            current_url = str(info.get("url") or "").strip()
            normalized_url = normalize_exact_tab_url(current_url)
            if normalized_url:
                override_name = overrides["urls"].get(normalized_url, "")
                if override_name:
                    override_source = "url"

            if not override_name:
                route_domain = normalize_route_domain(
                    info.get("route_domain") or info.get("current_domain") or ""
                )
                if route_domain:
                    override_name = overrides["sites"].get(route_domain, "")
                    if override_name:
                        override_source = "site"

        info["default_model_name"] = default_name
        info["exposed_model_name"] = override_name or default_name
        info["model_name_override_source"] = override_source

    def is_url_excluded(self, url: str) -> bool:
        current_url = str(url or "").strip()
        if not current_url:
            return False

        with self._lock:
            excluded_urls = list(self.excluded_urls)

        return any(tab_url_matches(excluded_url, current_url) for excluded_url in excluded_urls)

    def _is_session_excluded_from_dynamic_routing(
        self,
        session: TabSession,
        current_url: Optional[str] = None,
    ) -> bool:
        if current_url is None:
            try:
                current_url, _actual_domain = session.get_cached_route_snapshot()
            except Exception:
                current_url = ""
        return self.is_url_excluded(current_url)

    def _get_sessions_for_dynamic_routing(self) -> List[TabSession]:
        return [
            session
            for session in self._tabs.values()
            if not self._is_session_excluded_from_dynamic_routing(session)
        ]

    def apply_runtime_config(
        self,
        *,
        max_tabs: Optional[int] = None,
        min_tabs: Optional[int] = None,
        idle_timeout: Optional[float] = None,
        acquire_timeout: Optional[float] = None,
        stuck_timeout: Optional[float] = None,
        allocation_mode: Optional[str] = None,
        excluded_urls: Optional[List[str]] = None,
        preserve_error_tabs: Optional[bool] = None,
        model_name_overrides: Optional[Dict[str, Any]] = None,
        route_groups: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """同步更新运行中的标签页池参数。"""
        with self._lock:
            new_max_tabs = self.max_tabs if max_tabs is None else max(1, int(max_tabs))
            new_min_tabs = self.min_tabs if min_tabs is None else max(1, int(min_tabs))
            if new_min_tabs > new_max_tabs:
                new_min_tabs = new_max_tabs

            self.max_tabs = new_max_tabs
            self.min_tabs = new_min_tabs

            if idle_timeout is not None:
                self.idle_timeout = max(1.0, float(idle_timeout))
            if acquire_timeout is not None:
                self.acquire_timeout = max(1.0, float(acquire_timeout))
            if stuck_timeout is not None:
                self.stuck_timeout = max(1.0, float(stuck_timeout))
            if allocation_mode is not None:
                self.allocation_mode = self._normalize_allocation_mode(allocation_mode)
            if excluded_urls is not None:
                self.excluded_urls = self._normalize_excluded_urls(excluded_urls)
            if preserve_error_tabs is not None:
                self.preserve_error_tabs = self._to_bool(preserve_error_tabs, False)
            if model_name_overrides is not None:
                self.model_name_overrides = self._normalize_model_name_overrides(model_name_overrides)
            if route_groups is not None:
                self.route_groups = normalize_route_groups(route_groups)
                valid_groups = route_groups_by_id(self.route_groups)
                self._route_group_bindings = {
                    group_id: bindings
                    for group_id, bindings in self._route_group_bindings.items()
                    if group_id in valid_groups
                }
                self._condition.notify_all()

            updated = {
                "max_tabs": self.max_tabs,
                "min_tabs": self.min_tabs,
                "idle_timeout": self.idle_timeout,
                "acquire_timeout": self.acquire_timeout,
                "stuck_timeout": self.stuck_timeout,
                "allocation_mode": self.allocation_mode,
                "excluded_urls": list(self.excluded_urls),
                "preserve_error_tabs": self.preserve_error_tabs,
                "model_name_overrides": {
                    "sites": dict(getattr(self, "model_name_overrides", {}).get("sites", {})),
                    "urls": dict(getattr(self, "model_name_overrides", {}).get("urls", {})),
                },
                "route_groups": normalize_route_groups(self.route_groups),
            }

            logger.info(
                "[TabPool] 运行时配置已更新: "
                f"max_tabs={self.max_tabs}, min_tabs={self.min_tabs}, "
                f"idle_timeout={self.idle_timeout}, acquire_timeout={self.acquire_timeout}, "
                f"stuck_timeout={self.stuck_timeout}, allocation_mode={self.allocation_mode}, "
                f"excluded_urls={len(self.excluded_urls)}, "
                f"preserve_error_tabs={self.preserve_error_tabs}, "
                f"model_name_overrides="
                f"{len(updated['model_name_overrides']['sites'])}/"
                f"{len(updated['model_name_overrides']['urls'])}"
                f", route_groups={len(updated['route_groups'])}"
            )
            return updated

    def _get_target_info(self, raw_tab_id: str) -> Dict[str, Any]:
        if not raw_tab_id:
            return {}

        try:
            browser = self._get_browser_handle()
            if browser is None or not hasattr(browser, "_run_cdp"):
                return {}

            result = browser._run_cdp("Target.getTargetInfo", targetId=raw_tab_id) or {}
            info = result.get("targetInfo") or {}
            return info if isinstance(info, dict) else {}
        except Exception as e:
            logger.debug(f"[TabPool] get target info failed ({raw_tab_id}): {e}")
            return {}

    def _get_browser_context_id(self, raw_tab_id: str) -> Optional[str]:
        if raw_tab_id in self._isolated_context_by_raw_id:
            return self._isolated_context_by_raw_id.get(raw_tab_id)

        info = self._get_target_info(raw_tab_id)
        browser_context_id = str(info.get("browserContextId") or "").strip()
        return browser_context_id or None

    def _snapshot_current_tab_targets(
        self,
    ) -> tuple[List[Any], List[str], Dict[str, str], Dict[str, str], set[str]]:
        current_tabs = self._list_current_tab_refs()
        current_tab_ids = [
            raw_id for raw_id in
            (self._get_tab_ref_id(tab_ref) for tab_ref in current_tabs)
            if raw_id
        ]

        current_context_by_raw: Dict[str, str] = {}
        current_url_by_raw: Dict[str, str] = {}
        current_url_known_raw: set[str] = set()
        for tab_ref in current_tabs:
            raw_id = self._get_tab_ref_id(tab_ref)
            if not raw_id:
                continue
            if self._tab_ref_has_url_field(tab_ref):
                current_url_known_raw.add(raw_id)
            url = self._get_tab_ref_url(tab_ref)
            if url:
                current_url_by_raw[raw_id] = url

        target_info_by_raw: Dict[str, Dict[str, Any]] = {}
        for tab_ref in current_tabs:
            raw_id = self._get_tab_ref_id(tab_ref)
            if not raw_id:
                continue
            if isinstance(tab_ref, tuple) and len(tab_ref) >= 2 and isinstance(tab_ref[1], dict):
                target_info_by_raw[raw_id] = tab_ref[1]
            elif isinstance(tab_ref, dict):
                target_info_by_raw[raw_id] = tab_ref

        with self._lock:
            cached_contexts = dict(self._isolated_context_by_raw_id)
        for raw_id in current_tab_ids:
            cached_context = str(cached_contexts.get(raw_id) or "").strip()
            if cached_context:
                current_context_by_raw[raw_id] = cached_context
                continue
            target_info = target_info_by_raw.get(raw_id) or {}
            context_id = str(target_info.get("browserContextId") or "").strip()
            if context_id:
                current_context_by_raw[raw_id] = context_id
            if raw_id in current_context_by_raw:
                continue
            try:
                info = self._get_target_info(raw_id)
                context_id = str(info.get("browserContextId") or "").strip()
                if context_id:
                    current_context_by_raw[raw_id] = context_id
            except Exception as e:
                logger.debug(f"[TabPool] skip context lookup for {raw_id}: {e}")

        return current_tabs, current_tab_ids, current_context_by_raw, current_url_by_raw, current_url_known_raw

    def _is_site_independent_cookie_enabled(self, domain: str) -> bool:
        normalized_domain = str(domain or "").strip()
        if not normalized_domain:
            return False

        try:
            from app.services.config_engine import config_engine

            advanced = config_engine.get_site_advanced_config(normalized_domain)
            return bool(advanced.get("independent_cookies", False))
        except Exception as e:
            logger.debug(f"[TabPool] load site advanced config failed ({normalized_domain}): {e}")
            return False

    def _is_site_independent_cookie_auto_takeover_enabled(self, domain: str) -> bool:
        normalized_domain = str(domain or "").strip()
        if not normalized_domain:
            return False

        try:
            from app.services.config_engine import config_engine

            advanced = config_engine.get_site_advanced_config(normalized_domain)
            return bool(advanced.get("independent_cookies_auto_takeover", False))
        except Exception as e:
            logger.debug(f"[TabPool] load site advanced config failed ({normalized_domain}): {e}")
            return False

    def _register_isolated_context(self, raw_tab_id: str, browser_context_id: Optional[str]) -> None:
        context_id = str(browser_context_id or "").strip()
        if raw_tab_id and context_id:
            self._isolated_context_by_raw_id[raw_tab_id] = context_id
            self._orphaned_isolated_contexts.pop(context_id, None)

    def _mark_orphaned_isolated_context(self, browser_context_id: Optional[str]) -> None:
        context_id = str(browser_context_id or "").strip()
        if context_id:
            self._orphaned_isolated_contexts[context_id] = (
                time.time() + self.ISOLATED_CONTEXT_ORPHAN_GRACE_SEC
            )

    def _cleanup_orphaned_isolated_contexts(
        self,
        current_context_ids: Optional[List[str]] = None,
    ) -> None:
        if not self._orphaned_isolated_contexts:
            return

        now = time.time()
        active_contexts = {
            str(context_id).strip()
            for context_id in (current_context_ids or [])
            if str(context_id).strip()
        }
        active_contexts.update(
            str(context_id).strip()
            for context_id in self._isolated_context_by_raw_id.values()
            if str(context_id).strip()
        )

        for context_id, expire_at in list(self._orphaned_isolated_contexts.items()):
            if context_id in active_contexts:
                self._orphaned_isolated_contexts.pop(context_id, None)
                continue
            if now < expire_at:
                continue
            self._orphaned_isolated_contexts.pop(context_id, None)
            self._dispose_browser_context_async(context_id)
            logger.info(
                f"[TabPool] disposed orphaned isolated context after grace: {context_id}"
            )

    def _dispose_browser_context(self, browser_context_id: Optional[str]) -> None:
        context_id = str(browser_context_id or "").strip()
        if not context_id:
            return

        try:
            browser = self._get_browser_handle()
            if browser is None or not hasattr(browser, "_run_cdp"):
                return
            browser._run_cdp("Target.disposeBrowserContext", browserContextId=context_id)
        except Exception as e:
            logger.debug(f"[TabPool] dispose browser context failed ({context_id}): {e}")

    def _dispose_browser_context_async(self, browser_context_id: Optional[str]) -> None:
        context_id = str(browser_context_id or "").strip()
        if not context_id:
            return

        executor = self._maintenance_executor
        if executor is None:
            return

        try:
            executor.submit(self._dispose_browser_context, context_id)
        except RuntimeError as e:
            logger.debug(f"[TabPool] maintenance submit failed (dispose:{context_id}): {e}")

    def _arm_isolated_rebind_grace(self, session: TabSession, reason: str, detail: str) -> bool:
        now = time.time()
        same_reason = session.transient_disconnect_reason == reason
        if same_reason and session.transient_disconnect_until > now:
            return True
        if same_reason and session.transient_disconnect_until > 0:
            logger.warning(
                f"[{session.id}] isolated session grace expired ({detail}), removing from pool"
            )
            session.clear_transient_disconnect()
            return False
        session.mark_transient_disconnect(self.ISOLATED_CONTEXT_REBIND_GRACE_SEC, reason=reason)
        logger.warning(
            f"[{session.id}] isolated session entered {self.ISOLATED_CONTEXT_REBIND_GRACE_SEC:.0f}s "
            f"rebind grace ({detail})"
        )
        return True

    def _refresh_isolated_session_binding(self, session: TabSession, raw_tab_id: str) -> bool:
        if not session.is_isolated_context or not raw_tab_id:
            return False

        replacement_tab = self._resolve_tab_from_ref(raw_tab_id)
        if not replacement_tab:
            return False

        self._detach_global_monitor_for_session(session.id, reason="target_rebind")
        session.tab = replacement_tab
        session.clear_transient_disconnect()
        try:
            _cached_url, cached_domain = session.get_cached_route_snapshot()
            if cached_domain:
                session.current_domain = cached_domain
        except Exception:
            pass
        if session.status == TabStatus.IDLE:
            self._restart_global_monitor_for_session_async(session.id, "target_rebind")
        return True

    def _get_browser_handle(self):
        browser = getattr(self.page, "browser", None)
        if browser is not None:
            return browser
        return self.page

    @staticmethod
    def _run_cdp_compat(target: Any, method: str, *, timeout: float = 2.0, **params):
        """Call DrissionPage CDP APIs across minor 4.x signature differences."""
        runner = getattr(target, "run_cdp", None)
        if not callable(runner):
            runner = getattr(target, "_run_cdp", None)
        if not callable(runner):
            raise AttributeError(f"CDP runner unavailable for {method}")

        timeout_value = max(0.1, float(timeout))
        try:
            return runner(method, _timeout=timeout_value, **params)
        except TypeError as exc:
            # A few older wrappers do not expose DrissionPage's private timeout
            # keyword. Retry only when the exception clearly concerns that kwarg.
            error_text = str(exc).lower()
            if "timeout" not in error_text:
                raise
            return runner(method, **params)

    @staticmethod
    def _is_execution_context_error(error: Any) -> bool:
        text = str(error or "").strip().lower()
        if not text:
            return False
        return any(
            marker in text
            for marker in (
                "cannot find default execution context",
                "execution context was destroyed",
                "execution context is not available",
                "context was destroyed",
            )
        )

    @staticmethod
    def _is_unsupported_cdp_method(error: Any) -> bool:
        text = str(error or "").strip().lower()
        return any(
            marker in text
            for marker in (
                "unknown method",
                "method not found",
                "not supported",
                "unsupported command",
            )
        )

    def _find_idle_tab_by_raw_id(self, raw_tab_id: str) -> Optional[Any]:
        """Find an existing idle wrapper before opening another CDP session."""
        raw_id = str(raw_tab_id or "").strip()
        if not raw_id:
            return None

        with self._lock:
            sessions = list(getattr(self, "_tabs", {}).values())
        for session in sessions:
            try:
                with session._lock:
                    if session.status != TabStatus.IDLE or session._termination_in_progress:
                        continue
                    tab = getattr(session, "tab", None)
                if self._get_tab_ref_id(tab) == raw_id:
                    return tab
            except Exception:
                continue
        return None

    def _force_reload_flat_via_cdp(self, browser: Any, target_id: str) -> bool:
        """Fallback for protocol versions where nested Target messaging is unavailable."""
        driver = getattr(browser, "_driver", None)
        send = getattr(driver, "_send", None)
        if not callable(send):
            return False

        session_id = None
        try:
            result = self._run_cdp_compat(
                browser,
                "Target.attachToTarget",
                timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                targetId=target_id,
                flatten=True,
            ) or {}
            session_id = result.get("sessionId") if isinstance(result, dict) else None
            if not session_id:
                return False

            message = {
                "method": "Page.reload",
                "params": {},
                "sessionId": session_id,
            }
            try:
                response = send(message, timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC)
            except TypeError as exc:
                if "timeout" not in str(exc).lower():
                    raise
                response = send(message)
            return isinstance(response, dict) and "error" not in response
        except Exception as exc:
            logger.debug(f"[TabPool] flat CDP 刷新唤醒失败 (target={target_id}): {exc}")
            return False
        finally:
            if session_id:
                try:
                    self._run_cdp_compat(
                        browser,
                        "Target.detachFromTarget",
                        timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                        sessionId=session_id,
                    )
                except Exception as exc:
                    logger.debug(
                        f"[TabPool] flat CDP session detach 失败 "
                        f"(target={target_id}, session={session_id}): {exc}"
                    )

    def _force_reload_tab_via_cdp(self, target_id: str) -> bool:
        """Best-effort reload that works with old nested and newer flat CDP sessions."""
        raw_id = str(target_id or "").strip()
        if not raw_id or self._shutdown:
            return False

        existing_tab = self._find_idle_tab_by_raw_id(raw_id)
        if existing_tab is not None:
            try:
                reload_result = self._run_cdp_compat(
                    existing_tab,
                    "Page.reload",
                    timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                )
                if isinstance(reload_result, dict) and reload_result.get("error"):
                    raise RuntimeError(str(reload_result.get("error")))
                return True
            except Exception as exc:
                logger.debug(
                    f"[TabPool] 复用现有 tab wrapper 刷新失败，改用 browser CDP "
                    f"(target={raw_id}): {exc}"
                )

        try:
            browser = self._get_browser_handle()
            if browser is None:
                return False
            run_cdp = getattr(browser, "_run_cdp", None)
            if not callable(run_cdp):
                # Some older/public wrappers expose only run_cdp().
                run_cdp = getattr(browser, "run_cdp", None)
            if not callable(run_cdp):
                return False
        except Exception:
            return False

        session_id = None
        use_flat_fallback = False
        try:
            # Omitting flatten keeps the legacy nested-session path compatible
            # with older Chromium versions. Current Chrome accepts this too.
            result = self._run_cdp_compat(
                browser,
                "Target.attachToTarget",
                timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                targetId=raw_id,
            ) or {}
            session_id = result.get("sessionId") if isinstance(result, dict) else None
            if not session_id:
                return False

            message = json.dumps(
                {"id": 1001, "method": "Page.reload", "params": {}},
                separators=(",", ":"),
            )
            try:
                nested_result = self._run_cdp_compat(
                    browser,
                    "Target.sendMessageToTarget",
                    timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                    message=message,
                    sessionId=session_id,
                )
                if isinstance(nested_result, dict) and nested_result.get("error"):
                    raise RuntimeError(str(nested_result.get("error")))
                return True
            except Exception as exc:
                use_flat_fallback = self._is_unsupported_cdp_method(exc)
                if not use_flat_fallback:
                    logger.debug(
                        f"[TabPool] 嵌套 CDP 刷新唤醒失败 "
                        f"(target={raw_id}): {exc}"
                    )
                    return False
        except Exception as exc:
            logger.debug(f"[TabPool] CDP attach 刷新唤醒失败 (target={raw_id}): {exc}")
            return False
        finally:
            if session_id:
                try:
                    self._run_cdp_compat(
                        browser,
                        "Target.detachFromTarget",
                        timeout=self.CONTEXT_RECOVERY_CDP_TIMEOUT_SEC,
                        sessionId=session_id,
                    )
                except Exception as exc:
                    logger.debug(
                        f"[TabPool] CDP session detach 失败 "
                        f"(target={raw_id}, session={session_id}): {exc}"
                    )

        return use_flat_fallback and self._force_reload_flat_via_cdp(browser, raw_id)

    def _run_context_recovery(self, raw_tab_id: str) -> None:
        try:
            self._force_reload_tab_via_cdp(raw_tab_id)
        except Exception as exc:
            logger.debug(f"[TabPool] 上下文恢复任务失败 (target={raw_tab_id}): {exc}")
        finally:
            recovery_lock = getattr(self, "_context_recovery_lock", None)
            if recovery_lock is not None:
                with recovery_lock:
                    getattr(self, "_context_recovery_pending", set()).discard(raw_tab_id)

    def _schedule_context_recovery(self, raw_tab_id: str) -> bool:
        """Schedule one reload per target cooldown without holding the pool lock."""
        raw_id = str(raw_tab_id or "").strip()
        if not raw_id or self._shutdown:
            return False

        recovery_lock = getattr(self, "_context_recovery_lock", None)
        if recovery_lock is None:
            recovery_lock = threading.Lock()
            self._context_recovery_lock = recovery_lock
        pending = getattr(self, "_context_recovery_pending", None)
        requested_at = getattr(self, "_context_recovery_requested_at", None)
        if pending is None:
            pending = set()
            self._context_recovery_pending = pending
        if requested_at is None:
            requested_at = {}
            self._context_recovery_requested_at = requested_at

        now = time.monotonic()
        with recovery_lock:
            last_requested = float(requested_at.get(raw_id, 0.0) or 0.0)
            if raw_id in pending or (
                last_requested
                and now - last_requested < self.CONTEXT_RECOVERY_COOLDOWN_SEC
            ):
                return False

            requested_at[raw_id] = now
            pending.add(raw_id)
            if len(requested_at) > self.CONTEXT_RECOVERY_CACHE_LIMIT:
                oldest_id = min(requested_at, key=requested_at.get)
                requested_at.pop(oldest_id, None)

        executor = getattr(self, "_maintenance_executor", None)
        if executor is None:
            with recovery_lock:
                pending.discard(raw_id)
            return False
        try:
            executor.submit(self._run_context_recovery, raw_id)
            return True
        except RuntimeError as exc:
            with recovery_lock:
                pending.discard(raw_id)
            logger.debug(f"[TabPool] 上下文恢复任务提交失败 (target={raw_id}): {exc}")
            return False

    def _list_current_tab_ids_via_cdp(self) -> List[str]:
        return [
            item[0]
            for item in (self._list_current_tab_targets_via_cdp() or [])
            if item and item[0]
        ]

    def _list_current_tab_targets_via_cdp(self) -> Optional[List[tuple[str, Dict[str, Any]]]]:
        browser = self._get_browser_handle()
        if browser is None or not hasattr(browser, "_run_cdp"):
            return None

        try:
            result = browser._run_cdp("Target.getTargets")
            if not isinstance(result, dict) or not isinstance(result.get("targetInfos"), list):
                logger.debug("[TabPool] Target.getTargets returned an incomplete snapshot")
                return None
            target_infos = result["targetInfos"]
        except Exception as e:
            logger.debug(f"[TabPool] Target.getTargets 失败: {e}")
            return None

        targets: List[tuple[str, Dict[str, Any]]] = []
        for info in target_infos:
            if not isinstance(info, dict):
                continue
            if str(info.get("type") or "").strip().lower() != "page":
                continue
            target_id = str(info.get("targetId") or "").strip()
            if target_id:
                targets.append((target_id, info))
        return targets

    def _log_get_tabs_warning(self, message: str) -> None:
        now = time.time()
        if now - self._last_get_tabs_warning_at < self.GET_TABS_WARNING_INTERVAL_SEC:
            return
        self._last_get_tabs_warning_at = now
        logger.warning(message)

    def _list_current_tab_refs(self) -> List[Any]:
        browser = self._get_browser_handle()
        if browser is None:
            raise RuntimeError("browser_handle_unavailable")

        cdp_targets = self._list_current_tab_targets_via_cdp()
        if cdp_targets is not None:
            self._get_tabs_retry_after = 0.0
            return cdp_targets

        now = time.time()
        if now < self._get_tabs_retry_after:
            raise RuntimeError("tab_snapshot_temporarily_unavailable")

        try:
            tabs = browser.get_tabs()
            if tabs is None:
                raise RuntimeError("browser.get_tabs returned no snapshot")
            self._get_tabs_retry_after = 0.0
            return list(tabs)
        except Exception as e:
            self._get_tabs_retry_after = max(
                self._get_tabs_retry_after,
                time.time() + self.GET_TABS_FAILURE_COOLDOWN_SEC,
            )
            cdp_targets = self._list_current_tab_targets_via_cdp()
            if cdp_targets is not None:
                message = f"[TabPool] browser.get_tabs() 失败，回退到 Target.getTargets 扫描: {e}"
                if _looks_like_transient_local_debug_error(e):
                    logger.debug(message)
                else:
                    self._log_get_tabs_warning(message)
                return cdp_targets
            fallback_ids = list(getattr(browser, "tab_ids", []) or [])
            if fallback_ids:
                message = f"[TabPool] browser.get_tabs() 失败，回退到 tab_ids 扫描: {e}"
                if _looks_like_transient_local_debug_error(e):
                    logger.debug(message)
                else:
                    self._log_get_tabs_warning(message)
                return fallback_ids
            raise

    def _build_site_entry_url(self, domain: str) -> str:
        normalized_domain = str(domain or "").strip()
        if not normalized_domain:
            return ""

        for session in self._tabs.values():
            current_url, actual_domain = session.get_cached_route_snapshot()
            if route_domain_matches(normalized_domain, actual_domain):
                if current_url and not _should_skip_pool_url(current_url):
                    return current_url
                built = session._build_domain_url(current_url, actual_domain)
                if built:
                    return built

        return f"https://{normalized_domain}/"

    def _create_isolated_tab(self, url: str, background: bool = False) -> Optional[Dict[str, Any]]:
        try:
            browser = self._get_browser_handle()
            tab = browser.new_tab(background=background, new_context=True, new_window=True)
            raw_tab_id = getattr(tab, "tab_id", None)
            browser_context_id = self._get_browser_context_id(raw_tab_id)
            self._register_isolated_context(raw_tab_id, browser_context_id)
            if url:
                tab.get(url)
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "url": str(getattr(tab, "url", "") or url or ""),
            }
        except Exception as e:
            logger.warning(f"[TabPool] create isolated tab failed: {e}")
            return None

    def _create_shared_tab(
        self,
        url: str,
        *,
        background: bool = False,
        new_window: bool = True,
    ) -> Optional[Dict[str, Any]]:
        try:
            browser = self._get_browser_handle()
            tab = browser.new_tab(url=url or None, background=background, new_window=new_window)
            raw_tab_id = getattr(tab, "tab_id", None)
            browser_context_id = self._get_browser_context_id(raw_tab_id)
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "url": str(getattr(tab, "url", "") or url or ""),
            }
        except Exception as e:
            logger.warning(f"[TabPool] create shared tab failed: {e}")
            return None

    def _ensure_cookie_isolation_for_new_tab(self, tab: Any, raw_tab_id: str, url: str) -> Dict[str, Any]:
        final_url = str(url or "").strip()
        browser_context_id = self._get_browser_context_id(raw_tab_id)
        if raw_tab_id in self._isolated_context_by_raw_id:
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "is_isolated_context": True,
                "url": final_url,
            }

        domain = extract_remote_site_domain(final_url) or ""
        if not self._is_site_independent_cookie_enabled(domain):
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "is_isolated_context": False,
                "url": final_url,
            }

        if not self._is_site_independent_cookie_auto_takeover_enabled(domain):
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "is_isolated_context": False,
                "url": final_url,
            }

        logger.warning(
            f"[TabPool] skipped automatic isolated-cookie takeover because existing tabs are never closed: "
            f"{domain or final_url}"
        )
        return {
            "tab": tab,
            "raw_tab_id": raw_tab_id,
            "browser_context_id": browser_context_id,
            "is_isolated_context": False,
            "url": final_url,
        }

    def create_isolated_site_tab(self, domain: str) -> Dict[str, Any]:
        target_domain = str(domain or "").strip()
        if not target_domain:
            return {"ok": False, "error": "domain_required"}

        with self._condition:
            self._scan_new_tabs()
            if len(self._tabs) >= self.max_tabs:
                return {"ok": False, "error": "tab_pool_full"}

            target_url = self._build_site_entry_url(target_domain)

        created = self._create_isolated_tab(target_url, background=False)
        if not created:
            return {"ok": False, "error": "create_isolated_tab_failed"}

        with self._condition:
            if len(self._tabs) >= self.max_tabs:
                logger.warning(
                    "[TabPool] pool reached capacity while creating an isolated tab; "
                    "keeping the newly created tab managed"
                )

            session = self._wrap_tab(
                created["tab"],
                created["raw_tab_id"],
                browser_context_id=created.get("browser_context_id"),
                is_isolated_context=True,
                initial_url=created.get("url") or target_url,
            )
            self._tabs[session.id] = session
            self._start_global_monitor_for_session(session)
            self._last_scan_time = time.time()
            self._condition.notify_all()

            info = session.get_info(use_cached_url=True)
            info["tab_route_prefix"] = f"/tab/{session.persistent_index}"
            route_domain = str(info.get("route_domain") or "").strip()
            info["domain_route_prefix"] = f"/url/{route_domain}" if route_domain else ""
            preset_route_domain = str(info.get("current_domain") or route_domain).strip()
            info["preset_route_domain"] = preset_route_domain
            info["preset_domain_route_prefix"] = f"/url/{preset_route_domain}" if preset_route_domain else ""
            url_route_token = str(info.get("url_route_token") or "").strip()
            info["exact_url_route_prefix"] = f"/tab-url/{url_route_token}" if url_route_token else ""
            info["route_prefix"] = info["domain_route_prefix"] or info["tab_route_prefix"]
            self._apply_exposed_model_name(info)

            return {
                "ok": True,
                "domain": target_domain,
                "message": f"已为 {target_domain} 新建独立 Cookie 标签页",
                "tab": info,
            }

    def create_shared_site_tab(self, domain: str) -> Dict[str, Any]:
        target_domain = str(domain or "").strip()
        if not target_domain:
            return {"ok": False, "error": "domain_required"}

        with self._condition:
            self._scan_new_tabs()
            if len(self._tabs) >= self.max_tabs:
                return {"ok": False, "error": "tab_pool_full"}

            target_url = self._build_site_entry_url(target_domain)

        created = self._create_shared_tab(target_url, background=False, new_window=True)
        if not created:
            return {"ok": False, "error": "create_shared_tab_failed"}

        with self._condition:
            if len(self._tabs) >= self.max_tabs:
                logger.warning(
                    "[TabPool] pool reached capacity while creating a shared tab; "
                    "keeping the newly created tab managed"
                )

            session = self._wrap_tab(
                created["tab"],
                created["raw_tab_id"],
                browser_context_id=created.get("browser_context_id"),
                is_isolated_context=False,
                initial_url=created.get("url") or target_url,
            )
            self._tabs[session.id] = session
            self._start_global_monitor_for_session(session)
            self._last_scan_time = time.time()
            self._condition.notify_all()

            info = session.get_info(use_cached_url=True)
            info["tab_route_prefix"] = f"/tab/{session.persistent_index}"
            route_domain = str(info.get("route_domain") or "").strip()
            info["domain_route_prefix"] = f"/url/{route_domain}" if route_domain else ""
            preset_route_domain = str(info.get("current_domain") or route_domain).strip()
            info["preset_route_domain"] = preset_route_domain
            info["preset_domain_route_prefix"] = f"/url/{preset_route_domain}" if preset_route_domain else ""
            url_route_token = str(info.get("url_route_token") or "").strip()
            info["exact_url_route_prefix"] = f"/tab-url/{url_route_token}" if url_route_token else ""
            info["route_prefix"] = info["domain_route_prefix"] or info["tab_route_prefix"]
            self._apply_exposed_model_name(info)

            return {
                "ok": True,
                "domain": target_domain,
                "message": f"已为 {target_domain} 打开共享 Cookie 受控窗口",
                "tab": info,
            }

    def _order_sessions_for_allocation(
        self,
        sessions: List[TabSession],
        *,
        route_domain: Optional[str] = None,
        allocation_mode: Optional[str] = None,
    ) -> List[TabSession]:
        ordered = sorted(list(sessions or []), key=self._session_allocation_key)
        mode = self._normalize_allocation_mode(allocation_mode or self.allocation_mode)
        if mode == "random" and len(ordered) > 1:
            randomized = ordered[:]
            random.shuffle(randomized)
            return randomized

        if mode != "round_robin" or len(ordered) <= 1:
            return ordered

        cursor = (
            self._route_round_robin_cursor.get(route_domain, 0)
            if route_domain
            else self._round_robin_cursor
        )
        cursor_index = self._persistent_index_value(cursor)
        next_items = [
            item for item in ordered
            if self._persistent_index_value(item.persistent_index) > cursor_index
        ]
        return next_items + [
            item for item in ordered
            if self._persistent_index_value(item.persistent_index) <= cursor_index
        ]

    @staticmethod
    def _persistent_index_value(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _session_allocation_key(cls, session: TabSession):
        return (
            cls._persistent_index_value(getattr(session, "persistent_index", 0)),
            str(getattr(session, "id", "") or ""),
        )

    def _select_first_idle_acquire_candidate(
        self,
        sessions,
        task_id: str,
        attempted_ids: set,
        *,
        defer_context: str = "acquire",
    ) -> Optional[TabSession]:
        selected = None
        selected_key = None
        for session in sessions or []:
            session_id = str(getattr(session, "id", "") or "")
            if session_id in attempted_ids:
                continue
            if session.status != TabStatus.IDLE:
                continue
            if not session.is_healthy(allow_live_check=False):
                logger.warning(f"[{session.id}] skip unhealthy tab")
                continue
            if self._should_defer_to_command(session, task_id):
                logger.debug(f"[{session.id}] defer {defer_context} to high-priority command")
                continue

            candidate_key = self._session_allocation_key(session)
            if selected is None or candidate_key < selected_key:
                selected = session
                selected_key = candidate_key

        return selected

    def _try_acquire_session_for_request(
        self,
        sessions,
        task_id: str,
        *,
        route_domain: Optional[str] = None,
        allocation_mode: Optional[str] = None,
        defer_context: str = "acquire",
    ) -> Optional[TabSession]:
        mode = self._normalize_allocation_mode(allocation_mode or self.allocation_mode)
        if mode == "first_idle":
            attempted_ids = set()
            while True:
                session = self._select_first_idle_acquire_candidate(
                    sessions,
                    task_id,
                    attempted_ids,
                    defer_context=defer_context,
                )
                if session is None:
                    return None
                attempted_ids.add(str(session.id or ""))
                if session.acquire(task_id):
                    return session

        for session in self._order_sessions_for_allocation(
            sessions,
            route_domain=route_domain,
            allocation_mode=mode,
        ):
            if session.status != TabStatus.IDLE:
                continue
            if not session.is_healthy(allow_live_check=False):
                logger.warning(f"[{session.id}] skip unhealthy tab")
                continue
            if self._should_defer_to_command(session, task_id):
                logger.debug(f"[{session.id}] defer {defer_context} to high-priority command")
                continue
            if session.acquire(task_id):
                return session
        return None

    def _mark_allocation_cursor(self, session: TabSession, route_domain: Optional[str] = None) -> None:
        current_index = int(session.persistent_index or 0)
        if route_domain:
            self._route_round_robin_cursor[route_domain] = current_index
            self._route_round_robin_cursor.move_to_end(route_domain)
            while len(self._route_round_robin_cursor) > self.ROUTE_CURSOR_LIMIT:
                self._route_round_robin_cursor.popitem(last=False)
        else:
            self._round_robin_cursor = current_index

    def _get_session_for_monitor(self, session_id: str) -> Optional[TabSession]:
        with self._lock:
            return self._tabs.get(session_id)

    def _get_session_for_monitor_snapshot(self, session_id: str) -> Optional[TabSession]:
        # Global monitor workers may be joined while the pool lock is held elsewhere.
        # Use a lock-free snapshot lookup here so the worker can observe removal and exit
        # instead of blocking behind the pool lock during shutdown/rebind paths.
        return self._tabs.get(session_id)

    def _start_global_monitor_for_session(self, session: Optional[TabSession]) -> bool:
        if not session or not self._global_network_monitor:
            return False
        transition_lock = getattr(self, "_global_monitor_transition_lock", None)
        if transition_lock is None:
            transition_lock = self._global_monitor_transition_lock = threading.RLock()
        # Start and stop use one transition lock. If acquisition changes the
        # session to BUSY during a delayed start, its handoff stop waits here and
        # removes the newly registered worker before returning the session.
        with transition_lock:
            if self._shutdown:
                return False
            if self._tabs.get(session.id) is not session:
                return False
            # 仅在空闲标签页常驻监听，任务执行时让位
            if session.status != TabStatus.IDLE:
                return False
            return bool(self._global_network_monitor.start_for_session(session))

    def _restart_global_monitor_for_session_async(self, session_id: str, reason: str) -> None:
        """Restart an idle session monitor outside the pool lock after a target rebind."""
        session_key = str(session_id or "").strip()
        executor = self._maintenance_executor
        if not session_key or executor is None or not self._global_network_monitor:
            return

        def _restart() -> None:
            deadline = time.monotonic() + 5.0
            while True:
                with self._lock:
                    if self._shutdown:
                        return
                    session = self._tabs.get(session_key)
                    if session is None or session.status != TabStatus.IDLE:
                        return
                if self._start_global_monitor_for_session(session):
                    # logger.debug(f"[GlobalNet] rebound listener restarted: {session_key} ({reason})")
                    return
                if time.monotonic() >= deadline:
                    logger.warning(f"[GlobalNet] rebound listener restart failed: {session_key} ({reason})")
                    return
                time.sleep(0.1)

        try:
            executor.submit(_restart)
        except RuntimeError as e:
            logger.debug(f"[GlobalNet] restart submit failed ({session_key}): {e}")

    def _stop_global_monitor_for_session(self, session_id: str, reason: str = "", wait: bool = False) -> bool:
        if not self._global_network_monitor:
            return True
        transition_lock = getattr(self, "_global_monitor_transition_lock", None)
        if transition_lock is None:
            transition_lock = self._global_monitor_transition_lock = threading.RLock()
        # Do not hold the pool lock while joining a worker: command callbacks can
        # legitimately need that lock while the worker is winding down.
        with transition_lock:
            monitor = self._global_network_monitor
            if not monitor:
                return True
            return bool(monitor.stop_for_session(session_id, reason=reason, join=wait))

    def _request_global_monitor_stop_for_session(
        self,
        session_id: str,
        reason: str = "",
        *,
        detach: bool = False,
    ) -> bool:
        if not self._global_network_monitor:
            return True
        request_stop = getattr(self._global_network_monitor, "request_stop_for_session", None)
        if callable(request_stop):
            return bool(request_stop(session_id, reason=reason, detach=detach))
        return self._stop_global_monitor_for_session(session_id, reason=reason, wait=False)

    def _detach_global_monitor_for_session(
        self,
        session_id: str,
        reason: str = "",
    ) -> bool:
        session_id = str(session_id or "").strip()
        if not session_id:
            return True
        if not self._global_network_monitor:
            return True
        return self._request_global_monitor_stop_for_session(
            session_id,
            reason=reason,
            detach=True,
        )

    def _prepare_acquired_session_for_handoff(
        self,
        session: TabSession,
        reason: str,
        *,
        rollback_request_count: bool = True,
    ) -> bool:
        self._request_global_monitor_stop_for_session(session.id, reason=reason)
        return True

    def _finish_acquired_session_handoff(
        self,
        session: TabSession,
        reason: str,
        task_id: str,
        *,
        rollback_request_count: bool = True,
    ) -> bool:
        if self._stop_global_monitor_for_session(session.id, reason=reason, wait=True):
            return True

        logger.warning(
            f"[{session.id}] global monitor did not stop before handoff "
            f"(reason={reason}); marking session unhealthy"
        )
        expected_task = str(task_id or "").strip()
        release_state = None
        with self._condition:
            current = self._tabs.get(session.id)
            current_task = str(getattr(session, "current_task_id", "") or "").strip()
            if (
                current is session
                and session.status == TabStatus.BUSY
                and (not expected_task or current_task == expected_task)
            ):
                release_state = session._begin_release_state(
                    clear_page=False,
                    rollback_request_count=rollback_request_count,
                    force=False,
                )

        if release_state is not None:
            session._run_release_from_state(
                release_state,
                clear_page=False,
                check_triggers=False,
                rollback_request_count=rollback_request_count,
            )

        with self._condition:
            current = self._tabs.get(session.id)
            current_task = str(getattr(session, "current_task_id", "") or "").strip()
            if current is session and (not expected_task or not current_task or current_task == expected_task):
                session.mark_error("global_monitor_stop_timeout")
            self._condition.notify_all()
        return False

    def _complete_acquired_session_for_return(
        self,
        session: TabSession,
        reason: str,
        task_id: str,
        *,
        rollback_request_count: bool = True,
        activate: bool = False,
    ) -> bool:
        self._prepare_acquired_session_for_handoff(
            session,
            reason,
            rollback_request_count=rollback_request_count,
        )

        self._condition.release()
        try:
            handoff_ok = self._finish_acquired_session_handoff(
                session,
                reason,
                task_id,
                rollback_request_count=rollback_request_count,
            )
        finally:
            self._condition.acquire()

        if not handoff_ok:
            return False

        current_task = str(getattr(session, "current_task_id", "") or "").strip()
        expected_task = str(task_id or "").strip()
        if (
            self._tabs.get(session.id) is not session
            or session.status != TabStatus.BUSY
            or (expected_task and current_task != expected_task)
        ):
            logger.warning(
                f"[{session.id}] acquire handoff lost ownership "
                f"(reason={reason}, expected_task={expected_task or '-'}, "
                f"current_task={current_task or '-'}, status={session.status.value})"
            )
            return False

        if activate:
            self._condition.release()
            try:
                session.activate()
            finally:
                self._condition.acquire()

            current_task = str(getattr(session, "current_task_id", "") or "").strip()
            if (
                self._tabs.get(session.id) is not session
                or session.status != TabStatus.BUSY
                or (expected_task and current_task != expected_task)
            ):
                logger.warning(
                    f"[{session.id}] acquire activation lost ownership "
                    f"(reason={reason}, expected_task={expected_task or '-'}, "
                    f"current_task={current_task or '-'}, status={session.status.value})"
                )
                return False
            self._active_session_id = session.id
        return True

    def suspend_global_network_monitor(self, tab_id: str, reason: str = "manual"):
        self._stop_global_monitor_for_session(tab_id, reason=reason)

    def resume_global_network_monitor(self, tab_id: str, reason: str = "manual"):
        with self._lock:
            session = self._tabs.get(tab_id)
        if not session:
            return
        if session.status != TabStatus.IDLE or not session.is_healthy():
            return
        self._start_global_monitor_for_session(session)
        logger.debug(f"[GlobalNet] 恢复监听: {tab_id} ({reason})")

    def _get_domain_abbr(self, url: str) -> str:
        try:
            if not url or "://" not in url:
                return "tab"

            domain = url.split("//")[-1].split("/")[0].lower()
            clean_domain = domain.replace("www.", "")

            for key, abbr in self.DOMAIN_ABBR_MAP.items():
                if key in clean_domain:
                    return abbr

            first_part = clean_domain.split(".")[0]
            return first_part[:10]

        except Exception:
            return "tab"

    @staticmethod
    def _get_tab_ref_id(tab_ref: Any) -> Optional[str]:
        if tab_ref is None:
            return None

        if isinstance(tab_ref, tuple) and tab_ref:
            return str(tab_ref[0] or "").strip() or None
        if isinstance(tab_ref, dict):
            target_id = str(tab_ref.get("targetId") or tab_ref.get("tab_id") or "").strip()
            return target_id or None

        tab_id = getattr(tab_ref, "tab_id", None)
        if tab_id:
            return str(tab_id)

        raw = str(tab_ref or "").strip()
        return raw or None

    @staticmethod
    def _get_tab_ref_url(tab_ref: Any) -> str:
        if isinstance(tab_ref, tuple) and len(tab_ref) >= 2 and isinstance(tab_ref[1], dict):
            return str(tab_ref[1].get("url") or "").strip()
        if isinstance(tab_ref, dict):
            return str(tab_ref.get("url") or "").strip()
        try:
            return str(getattr(tab_ref, "url", "") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _tab_ref_has_url_field(tab_ref: Any) -> bool:
        if isinstance(tab_ref, tuple) and len(tab_ref) >= 2 and isinstance(tab_ref[1], dict):
            return "url" in tab_ref[1]
        if isinstance(tab_ref, dict):
            return "url" in tab_ref
        return False

    def _resolve_tab_from_ref(self, tab_ref: Any) -> Optional[Any]:
        if tab_ref is None:
            return None
        if isinstance(tab_ref, tuple) and len(tab_ref) >= 3:
            existing_tab = tab_ref[2]
            if existing_tab is not None:
                return existing_tab

        try:
            existing_tab_id = getattr(tab_ref, "tab_id", None)
        except Exception:
            existing_tab_id = None
        if existing_tab_id:
            return tab_ref

        raw_tab_id = self._get_tab_ref_id(tab_ref)
        if not raw_tab_id:
            return None

        try:
            browser = self._get_browser_handle()
            return browser.get_tab(raw_tab_id)
        except Exception as exc:
            if self._is_execution_context_error(exc) and self._schedule_context_recovery(raw_tab_id):
                logger.warning(
                    f"[TabPool] 标签页 {raw_tab_id} 丢失 JS 执行上下文，"
                    "已异步请求 CDP 刷新唤醒"
                )
            return None

    def _wrap_tab(
        self,
        tab,
        raw_tab_id: str = None,
        *,
        browser_context_id: Optional[str] = None,
        is_isolated_context: bool = False,
        initial_url: str = "",
    ) -> TabSession:
        self._tab_counter += 1

        url = str(initial_url or "").strip()
        if not url:
            try:
                url = tab.url or ""
            except Exception:
                pass

        abbr = self._get_domain_abbr(url)
        tab_id = f"{abbr}_{self._tab_counter}"

        session = TabSession(
            id=tab_id,
            tab=tab,
            browser_context_id=browser_context_id,
            is_isolated_context=bool(is_isolated_context),
        )
        session._remember_url(url)

        try:
            session.current_domain = extract_remote_site_domain(url)
        except:
            pass

        # 记录底层标签页 ID
        if raw_tab_id:
            self._known_tab_ids.add(raw_tab_id)
            if session.is_isolated_context:
                self._register_isolated_context(raw_tab_id, browser_context_id)

            # 🆕 分配持久化编号
            if raw_tab_id not in self._raw_id_to_persistent:
                persistent_idx = self._next_persistent_index
                self._next_persistent_index += 1
                self._raw_id_to_persistent[raw_tab_id] = persistent_idx
            else:
                persistent_idx = self._raw_id_to_persistent[raw_tab_id]

            session.persistent_index = persistent_idx
            self._persistent_to_session_id[persistent_idx] = session.id
            logger.debug(f"标签页 {session.id} 分配编号 #{persistent_idx}")

        return session

    def _on_session_removed(self, session_id: str):
        """Notify command engine after a pool session is removed without blocking pool locks."""
        session_key = str(session_id or "").strip()
        if not session_key:
            return

        def _evict():
            try:
                from app.services.command_engine import command_engine

                if hasattr(command_engine, "evict_session"):
                    command_engine.evict_session(session_key)
            except Exception as e:
                logger.debug(f"[TabPool] evict session {session_key} failed: {e}")

        executor = self._maintenance_executor
        if executor is None:
            return
        try:
            executor.submit(_evict)
        except RuntimeError as e:
            logger.debug(f"[TabPool] maintenance submit failed (evict:{session_key}): {e}")

    def _submit_request_cancel(
        self,
        task_id: str,
        reason: str,
        *,
        session_id: str = "",
        detail: str = "",
    ) -> bool:
        """Cancel a request from the maintenance worker so pool locks never call outward."""
        task_key = str(task_id or "").strip()
        if not task_key:
            return False
        reason_key = str(reason or "").strip() or "unknown"
        session_key = str(session_id or "").strip()
        detail_text = str(detail or "").strip()

        def _cancel():
            cancelled = False
            try:
                from app.services.request_manager import request_manager

                cancelled = bool(request_manager.cancel_request(task_key, reason_key))
            except Exception as e:
                logger.debug(
                    f"[{session_key or 'TabPool'}] async cancel failed "
                    f"(task={task_key}, reason={reason_key}, detail={detail_text or '-'}): {e}"
                )
                return

            logger.debug(
                f"[{session_key or 'TabPool'}] async cancel result "
                f"(task={task_key}, reason={reason_key}, cancelled={cancelled}, "
                f"detail={detail_text or '-'})"
            )

        executor = self._maintenance_executor
        if executor is None:
            logger.debug(
                f"[{session_key or 'TabPool'}] async cancel skipped: maintenance executor unavailable "
                f"(task={task_key}, reason={reason_key}, detail={detail_text or '-'})"
            )
            return False
        try:
            executor.submit(_cancel)
            return True
        except RuntimeError as e:
            logger.debug(
                f"[{session_key or 'TabPool'}] maintenance submit failed "
                f"(cancel:{task_key}, reason={reason_key}, detail={detail_text or '-'}): {e}"
            )
            return False

    # ================= 错误标签页隔离与恢复 =================

    def _close_quarantined_tab(self, tab: Any, raw_tab_id: str) -> bool:
        """Close a permanently quarantined browser target outside the pool lock."""
        close = getattr(tab, "close", None)
        if callable(close):
            try:
                close_result = close()
                if close_result is not False:
                    return True
                logger.debug(
                    f"[TabRecovery] 标签页对象 close() 返回失败，尝试 CDP 兜底 "
                    f"(raw={raw_tab_id})"
                )
            except Exception as exc:
                logger.debug(
                    f"[TabRecovery] 关闭隔离标签页对象失败，尝试 CDP 兜底 "
                    f"(raw={raw_tab_id}, error={exc})"
                )

        try:
            browser = self._get_browser_handle()
            run_cdp = getattr(browser, "_run_cdp", None)
            if callable(run_cdp):
                result = run_cdp("Target.closeTarget", targetId=str(raw_tab_id))
                if not isinstance(result, dict) or result.get("success", True):
                    return True
        except Exception as exc:
            logger.warning(
                f"[TabRecovery] 关闭永久隔离标签页失败 "
                f"(raw={raw_tab_id}, error={exc})"
            )
        return False

    def resolve_quarantine(self, raw_tab_id: str, release: bool) -> bool:
        """解除或永久化一个隔离条目（供 TabRecoveryService 回调）。

        release=True：从隔离集合移除，下一次 _scan_new_tabs 自然重新入池；
        release=False：关闭浏览器标签页并释放强引用；关闭失败时保留不带
        tab 引用的轻量永久隔离记录，避免扫描期间重新入池。
        """
        raw_id = str(raw_tab_id or "").strip()
        if not raw_id:
            return False

        tab_to_close = None
        entry = None
        with self._condition:
            entry = self._quarantined_raw_ids.get(raw_id)
            if entry is None:
                return False
            if release:
                self._quarantined_raw_ids.pop(raw_id, None)
                # 恢复任务结束后不再需要保留 DrissionPage tab wrapper。
                entry.tab = None
                self._condition.notify_all()
                return True

            entry.permanent = True
            tab_to_close = getattr(entry, "tab", None)
            # 先在锁内摘掉强引用；真实 CDP 调用必须在锁外执行。
            entry.tab = None

        closed = self._close_quarantined_tab(tab_to_close, raw_id)
        with self._condition:
            if self._quarantined_raw_ids.get(raw_id) is entry:
                if closed:
                    self._quarantined_raw_ids.pop(raw_id, None)
                self._condition.notify_all()
        return True

    def get_quarantine_entry(self, raw_tab_id: str) -> Optional[TabQuarantineEntry]:
        with self._condition:
            return self._quarantined_raw_ids.get(str(raw_tab_id or "").strip())

    def _get_recovery_service(self) -> Optional[TabRecoveryService]:
        if self._shutdown:
            return None
        service = self._recovery_service
        if service is not None:
            return service
        with self._recovery_service_lock:
            if self._recovery_service is None:
                self._recovery_service = TabRecoveryService(
                    resolve_quarantine=self.resolve_quarantine,
                    get_quarantine_entry=self.get_quarantine_entry,
                )
            return self._recovery_service

    def _submit_recovery(self, entry: TabQuarantineEntry) -> None:
        """把隔离条目投递给恢复服务；持池锁调用也安全（仅入队，不阻塞外呼）。"""

        def _submit() -> None:
            try:
                service = self._get_recovery_service()
                if service is not None:
                    service.submit(entry)
            except Exception as e:
                logger.warning(
                    f"[TabRecovery] 投递恢复任务失败 ({entry.raw_tab_id}): {e}"
                )

        executor = self._maintenance_executor
        if executor is not None:
            try:
                executor.submit(_submit)
                return
            except RuntimeError as e:
                logger.debug(
                    f"[TabRecovery] maintenance submit failed "
                    f"(recovery:{entry.raw_tab_id}): {e}"
                )
        # 兜底：maintenance executor 不可用时直接入队（submit 本身非阻塞）。
        _submit()

    def _should_scan(self) -> bool:
        """检查是否需要扫描新标签页"""
        return time.time() - self._last_scan_time >= self.SCAN_INTERVAL

    def _should_scan_for_query(self) -> bool:
        """读接口需要较新的浏览器 target 状态，但按 QUERY_SCAN_MIN_INTERVAL_SEC 节流。

        此前恒返回 True，导致每个 /v1/chat/completions、/v1/models 请求都触发
        一次全量 CDP 扫描（Target.getTargets 等同步网络 I/O），并发时扫描成本
        线性叠加到首字延迟上；浏览器卡顿时还会拖住调用线程。
        1 秒内的快照对路由决策而言足够新鲜。
        """
        return time.time() - self._last_scan_time >= self.QUERY_SCAN_MIN_INTERVAL_SEC

    def _scan_new_tabs(self):
        """扫描并添加新标签页（已持有锁）"""
        try:
            # 先占住本次扫描窗口，避免唤醒后多个等待线程重复扫描。
            self._last_scan_time = time.time()
            # 锁序考量：下面会释放池锁去采集浏览器快照，期间新入池的会话
            # （create_shared_site_tab / create_isolated_site_tab 等）不会出现在
            # 这份过期快照里。记录快照起始时间，应用移除逻辑时据此跳过晚入池的会话
            # （session.created_at 即入池时刻，构造后随即持池锁入池）。
            snapshot_started_at = time.time()
            self._condition.release()
            try:
                with self._scan_snapshot_lock:
                    (
                        current_tabs,
                        current_tab_ids,
                        current_context_by_raw,
                        current_url_by_raw,
                        current_url_known_raw,
                    ) = self._snapshot_current_tab_targets()
            finally:
                self._condition.acquire()

            if self._shutdown:
                return

            current_tab_set = set(current_tab_ids)
            # 物理关闭成功后，Target.getTargets 可能要到下一轮才反映结果；
            # 这里清理已永久隔离且不再持有 tab 引用的陈旧元数据，避免隔离
            # 字典只增不减。
            for raw_id, entry in list(self._quarantined_raw_ids.items()):
                if (
                    getattr(entry, "permanent", False)
                    and getattr(entry, "tab", None) is None
                    and raw_id not in current_tab_set
                ):
                    self._quarantined_raw_ids.pop(raw_id, None)
            self._cleanup_orphaned_isolated_contexts(list(current_context_by_raw.values()))
            changed = False

            session_raw_by_id: Dict[str, str] = {}
            for rid, pidx in self._raw_id_to_persistent.items():
                sid = self._persistent_to_session_id.get(pidx)
                if sid and sid in self._tabs:
                    session_raw_by_id[sid] = rid

            for session_id, session in self._tabs.items():
                if session_raw_by_id.get(session_id):
                    continue
                candidate_raw_id = self._get_tab_ref_id(getattr(session, "tab", None))
                if not candidate_raw_id or candidate_raw_id not in current_tab_set:
                    continue

                existing_persistent_idx = self._raw_id_to_persistent.get(candidate_raw_id)
                if existing_persistent_idx is not None:
                    existing_session_id = self._persistent_to_session_id.get(existing_persistent_idx)
                    if (
                        existing_session_id
                        and existing_session_id != session_id
                        and existing_session_id in self._tabs
                    ):
                        continue

                persistent_idx = int(
                    existing_persistent_idx
                    or getattr(session, "persistent_index", 0)
                    or 0
                )
                if persistent_idx <= 0:
                    persistent_idx = self._next_persistent_index
                    self._next_persistent_index += 1
                session.persistent_index = persistent_idx

                self._raw_id_to_persistent[candidate_raw_id] = persistent_idx
                self._persistent_to_session_id[persistent_idx] = session_id
                self._known_tab_ids.add(candidate_raw_id)
                if session.is_isolated_context:
                    context_id = (
                        str(session.browser_context_id or "").strip()
                        or current_context_by_raw.get(candidate_raw_id)
                    )
                    if context_id:
                        session.browser_context_id = context_id
                        self._register_isolated_context(candidate_raw_id, context_id)
                session_raw_by_id[session_id] = candidate_raw_id
                changed = True
                logger.warning(
                    f"[{session_id}] repaired missing raw tab mapping "
                    f"(raw={candidate_raw_id}, idx=#{persistent_idx})"
                )

            for session_id, session in self._tabs.items():
                raw_id = session_raw_by_id.get(session_id)
                if not raw_id or raw_id not in current_tab_set:
                    continue
                if raw_id in current_url_known_raw:
                    current_url = current_url_by_raw.get(raw_id, "")
                    session._remember_url(current_url)
                    try:
                        session.current_domain = extract_remote_site_domain(current_url)
                    except Exception:
                        session.current_domain = None
                if not session.is_isolated_context:
                    continue
                if session.status == TabStatus.BUSY:
                    continue
                bound_raw_id = self._get_tab_ref_id(getattr(session, "tab", None))
                if bound_raw_id == raw_id:
                    session.clear_transient_disconnect()
                    continue
                if self._refresh_isolated_session_binding(session, raw_id):
                    logger.info(f"[{session_id}] isolated session target recovered: {raw_id}")
                    continue
                if session.is_in_transient_disconnect():
                    logger.debug(
                        f"[{session_id}] isolated session still waiting for target recovery: {raw_id}"
                    )

            reserved_raw_ids = {
                raw_id
                for raw_id in session_raw_by_id.values()
                if raw_id in current_tab_set
            }

            # ===== 第一步：清理已关闭的标签页 =====
            # 找出池中存在、但浏览器中已消失的标签页
            sessions_to_remove = []
            for session_id, session in self._tabs.items():
                # 查找该 session 对应的 raw_tab_id
                raw_id = session_raw_by_id.get(session_id)

                if raw_id is None or raw_id not in current_tab_set:
                    # 入池晚于本次快照起始的会话必然不在快照里，不能据此判定"已关闭"
                    # （isolated 会话有 rebind 宽限兜底，shared 会话没有，会被直接移除）。
                    # 跳过本轮，等下一轮新快照再判定。
                    joined_at = float(getattr(session, "created_at", 0.0) or 0.0)
                    if joined_at > snapshot_started_at:
                        logger.debug(
                            f"[{session_id}] 跳过过期快照的移除判定 "
                            f"(入池晚于快照起始 {joined_at - snapshot_started_at:.3f}s, raw={raw_id})"
                        )
                        continue
                    sessions_to_remove.append((session_id, raw_id, session))

            for session_id, raw_id, session in sessions_to_remove:
                replacement_raw_id = None
                replacement_tab = None
                browser_context_id = str(session.browser_context_id or "").strip()
                if session.is_isolated_context and browser_context_id:
                    for candidate_raw_id in current_tab_ids:
                        if candidate_raw_id in reserved_raw_ids:
                            continue
                        if current_context_by_raw.get(candidate_raw_id) != browser_context_id:
                            continue
                        replacement_tab = self._resolve_tab_from_ref(candidate_raw_id)
                        if not replacement_tab:
                            continue
                        replacement_raw_id = candidate_raw_id
                        break

                if replacement_raw_id and replacement_tab:
                    self._detach_global_monitor_for_session(session_id, reason="target_rebind")
                    p_idx = self._raw_id_to_persistent.pop(raw_id, None) if raw_id else None
                    if p_idx is None:
                        p_idx = int(getattr(session, "persistent_index", 0) or 0) or None
                    if p_idx is not None:
                        self._raw_id_to_persistent[replacement_raw_id] = p_idx
                        self._persistent_to_session_id[p_idx] = session_id
                    if raw_id:
                        self._known_tab_ids.discard(raw_id)
                    self._known_tab_ids.add(replacement_raw_id)
                    if raw_id:
                        self._isolated_context_by_raw_id.pop(raw_id, None)
                    self._register_isolated_context(replacement_raw_id, browser_context_id)
                    session.tab = replacement_tab
                    session.browser_context_id = browser_context_id or current_context_by_raw.get(replacement_raw_id)
                    session.clear_transient_disconnect()
                    try:
                        _cached_url, cached_domain = session.get_cached_route_snapshot()
                        if cached_domain:
                            session.current_domain = cached_domain
                    except Exception:
                        pass
                    reserved_raw_ids.add(replacement_raw_id)
                    if session.status == TabStatus.IDLE:
                        self._restart_global_monitor_for_session_async(session_id, "target_rebind")
                    logger.info(
                        f"[{session_id}] rebound isolated context target: {raw_id} -> {replacement_raw_id}"
                    )
                    changed = True
                    continue

                if session.is_isolated_context and browser_context_id:
                    if self._arm_isolated_rebind_grace(
                        session,
                        reason="target_missing",
                        detail=f"target missing (raw={raw_id})",
                    ):
                        continue

                if session.status == TabStatus.BUSY:
                    self._cancel_active_request_for_session(
                        session,
                        "tab_closed",
                        detail=f"raw={raw_id}",
                    )
                    logger.warning(f"[{session_id}] 标签页已关闭但仍在忙碌，标记为关闭")
                    session.mark_closed("tab_closed")
                    self._detach_global_monitor_for_session(session_id, reason="tab_closed")
                else:
                    logger.info(f"[{session_id}] 标签页已关闭，从池中移除")
                    session.mark_closed("tab_closed")
                    self._detach_global_monitor_for_session(session_id, reason="tab_closed")
                    del self._tabs[session_id]
                    self._on_session_removed(session_id)

                # 清理映射
                if raw_id:
                    self._known_tab_ids.discard(raw_id)
                p_idx = self._raw_id_to_persistent.pop(raw_id, None) if raw_id else None
                if p_idx is None:
                    p_idx = int(getattr(session, "persistent_index", 0) or 0) or None
                if p_idx is not None:
                    if self._persistent_to_session_id.get(p_idx) == session_id:
                        self._persistent_to_session_id.pop(p_idx, None)
                browser_context_id = self._isolated_context_by_raw_id.pop(raw_id, None) if raw_id else None
                if not browser_context_id and session.is_isolated_context:
                    browser_context_id = str(session.browser_context_id or "").strip()
                if browser_context_id:
                    self._mark_orphaned_isolated_context(browser_context_id)
                if self._active_session_id == session_id:
                    self._active_session_id = None
                changed = True

            # 顺手清理已切换到本地页/无效页的空闲标签，避免继续展示和参与调度。
            self._cleanup_unhealthy_tabs()

            # ===== 第二步：构建"已在池中的 tab 对象"集合 =====
            tabs_in_pool = set()
            for rid in self._raw_id_to_persistent:
                pidx = self._raw_id_to_persistent[rid]
                sid = self._persistent_to_session_id.get(pidx)
                if sid and sid in self._tabs:
                    tabs_in_pool.add(rid)

            # ===== 第三步：扫描新标签页 =====
            new_count = 0
            for tab_ref in current_tabs:
                if len(self._tabs) >= self.max_tabs:
                    break

                raw_tab = self._get_tab_ref_id(tab_ref)
                if not raw_tab:
                    continue

                # 已在池中，跳过
                if raw_tab in tabs_in_pool:
                    continue

                # 隔离中的错误标签页不重新入池：旧 worker 可能尚未退出，
                # 由 TabRecoveryService 决定何时解除隔离（或永久排除）。
                if raw_tab in self._quarantined_raw_ids:
                    logger.debug(f"[TabRecovery] 扫描跳过隔离中的标签页: {raw_tab}")
                    continue

                try:
                    url = current_url_by_raw.get(raw_tab) or self._get_tab_ref_url(tab_ref)

                    # 本地页、浏览器内部页、空白页都不纳入标签页池。
                    if _should_skip_pool_url(url):
                        continue

                    tab = self._resolve_tab_from_ref(tab_ref)
                    if not tab:
                        continue

                    isolation_result = self._ensure_cookie_isolation_for_new_tab(tab, raw_tab, url)
                    tab = isolation_result["tab"]
                    raw_tab = isolation_result["raw_tab_id"]
                    url = isolation_result["url"]

                    # 有效页面 - 添加到池
                    session = self._wrap_tab(
                        tab,
                        raw_tab,
                        browser_context_id=isolation_result.get("browser_context_id"),
                        is_isolated_context=isolation_result.get("is_isolated_context", False),
                        initial_url=url,
                    )
                    self._tabs[session.id] = session
                    self._start_global_monitor_for_session(session)
                    new_count += 1
                    changed = True

                    display_url = url[:60] + "..." if len(url) > 60 else url
                    logger.debug(f"🆕 发现新标签页: {session.id} -> {display_url}")

                except Exception as e:
                    logger.debug(f"处理标签页出错: {e}")
                    continue

            # Remove unusable pool sessions after discovery without closing targets.
            self._cleanup_unhealthy_tabs()
            self._last_scan_time = time.time()
            if changed:
                self._condition.notify_all()

            if new_count > 0:
                logger.info(f"扫描完成: +{new_count} 个，当前共 {len(self._tabs)} 个标签页")

        except Exception as e:
            logger.warning(f"扫描标签页失败: {e}")

    def initialize(self):
        """初始化标签页池"""
        with self._lock:
            if self._initialized:
                return

            raw_target_count = 0
            try:
                browser = self._get_browser_handle()
                existing_tabs = self._list_current_tab_refs()
                raw_target_count = len(existing_tabs)
                logger.debug(f"[TabPool] 检测到 {raw_target_count} 个浏览器 page target")

                for tab_ref in existing_tabs:
                    if len(self._tabs) >= self.max_tabs:
                        break

                    try:
                        raw_tab = self._get_tab_ref_id(tab_ref)
                        if not raw_tab:
                            continue

                        url = self._get_tab_ref_url(tab_ref)
                        # 初始化时直接跳过本地页和浏览器内部页。
                        if _should_skip_pool_url(url):
                            continue

                        tab = self._resolve_tab_from_ref(tab_ref)
                        if not tab:
                            continue

                        isolation_result = self._ensure_cookie_isolation_for_new_tab(tab, raw_tab, url)
                        tab = isolation_result["tab"]
                        raw_tab = isolation_result["raw_tab_id"]
                        url = isolation_result["url"]

                        # 有效页面 - 添加到池
                        session = self._wrap_tab(
                            tab,
                            raw_tab,
                            browser_context_id=isolation_result.get("browser_context_id"),
                            is_isolated_context=isolation_result.get("is_isolated_context", False),
                            initial_url=url,
                        )
                        self._tabs[session.id] = session

                        display_url = url[:60] + "..." if len(url) > 60 else url
                        logger.info(f"TabPool: {session.id} -> {display_url}")
                    except Exception as e:
                        logger.debug(f"处理标签页出错: {e}")
                        continue

            except Exception as e:
                logger.warning(f"扫描标签页失败: {e}")

            # 重置所有状态为 IDLE
            for session in self._tabs.values():
                # 锁序考量：状态迁移须在 session._lock 内进行，与 acquire/release 的
                # 状态检查互斥。既有锁序为 池锁 → session._lock（release 等路径同序），
                # session._lock 内不会反向取池锁，无死锁风险。
                with session._lock:
                    session.status = TabStatus.IDLE
                    session.current_task_id = None
                self._start_global_monitor_for_session(session)

            self._initialized = True
            self._last_scan_time = time.time()
            ignored_count = max(0, raw_target_count - len(self._tabs))
            logger.info(
                f"TabPool 就绪: {len(self._tabs)} 个远程网页"
                + (f"（已忽略 {ignored_count} 个内部/无效 target）" if ignored_count else "")
            )

    def _check_stuck_tabs(self):
        """检查并释放卡死的标签页"""
        now = time.time()
        released_any = False

        for session in self._tabs.values():
            if session.status == TabStatus.BUSY:
                try:
                    timeout_info = session.get_busy_timeout_info(now=now)
                except Exception:
                    timeout_info = {
                        "basis": "session",
                        "duration": now - session.last_used_at,
                        "command_loop": {},
                    }
                busy_duration = float(timeout_info.get("duration") or 0.0)
                timeout_basis = str(timeout_info.get("basis") or "session")
                command_loop = timeout_info.get("command_loop") if isinstance(timeout_info, dict) else {}

                if busy_duration > self.stuck_timeout:
                    task_id = session.current_task_id or ""
                    detail_parts = [
                        f"busy_duration={busy_duration:.0f}s",
                        f"basis={timeout_basis}",
                    ]
                    if isinstance(command_loop, dict) and command_loop.get("active"):
                        loop_iteration = command_loop.get("iteration")
                        loop_total = command_loop.get("total")
                        loop_label = str(command_loop.get("label") or "").strip()
                        loop_text = f"loop={loop_iteration or '-'}"
                        if loop_total:
                            loop_text = f"{loop_text}/{loop_total}"
                        if loop_label:
                            loop_text = f"{loop_label}:{loop_text}"
                        detail_parts.append(loop_text)
                    cancel_submitted = self._cancel_active_request_for_session(
                        session,
                        "stuck_timeout",
                        detail=", ".join(detail_parts),
                    )
                    snapshot = self._describe_session(session)
                    action_label = "record stuck session" if self.preserve_error_tabs else "retire session"
                    logger.warning(
                        f"[{session.id}] stuck for {busy_duration:.0f}s "
                        f"(basis={timeout_basis}), {action_label} "
                        f"(task={task_id or '-'}, cancel_submitted={cancel_submitted}, "
                        f"snapshot={snapshot})"
                    )
                    session.mark_error("stuck_timeout")
                    released_any = True

        if released_any:
            self._condition.notify_all()
        return released_any

    def run_watchdog_tick(self) -> bool:
        """Run periodic stuck-tab maintenance from an external watchdog thread."""
        with self._condition:
            if self._shutdown:
                return False
            changed = bool(self._check_stuck_tabs())
            self._cleanup_unhealthy_tabs()

        self._schedule_idle_memory_purge()
        return changed

    def _schedule_idle_memory_purge(self) -> bool:
        """Request best-effort JS GC for long-idle tabs without holding pool locks."""
        if not self._idle_memory_purge_enabled:
            return False

        now_wall = time.time()
        now_mono = time.monotonic()
        with self._lock:
            if self._shutdown:
                return False
            sessions = list(self._tabs.values())

        candidates: List[TabSession] = []
        for session in sessions:
            try:
                with session._lock:
                    if session.status != TabStatus.IDLE or session._termination_in_progress:
                        continue
                    idle_for = now_wall - float(session.last_used_at or now_wall)
                    last_requested = float(
                        getattr(session, "_last_memory_purge_requested_at", 0.0) or 0.0
                    )
                    if idle_for < self._idle_memory_purge_after_sec:
                        continue
                    if last_requested and (now_mono - last_requested) < self._idle_memory_purge_interval_sec:
                        continue
                    setattr(session, "_last_memory_purge_requested_at", now_mono)
                candidates.append(session)
            except Exception:
                continue

        if not candidates:
            return False

        executor = self._maintenance_executor
        if executor is None:
            return False

        scheduled = False
        for session in candidates:
            try:
                executor.submit(self._purge_idle_session_memory, session)
                scheduled = True
            except RuntimeError:
                break
        return scheduled

    def _purge_idle_session_memory(self, session: TabSession) -> None:
        """Force renderer GC for one idle session; never changes page lifecycle."""
        if self._shutdown:
            return
        heap_profiler_enabled = False
        try:
            with session._lock:
                if session.status != TabStatus.IDLE or session._termination_in_progress:
                    return

            try:
                self._run_cdp_compat(
                    session.tab,
                    "HeapProfiler.enable",
                    timeout=1.0,
                )
                heap_profiler_enabled = True
            except Exception:
                # collectGarbage is available without enabling the domain on
                # some older Chromium/DrissionPage combinations.
                pass

            self._run_cdp_compat(
                session.tab,
                "HeapProfiler.collectGarbage",
                timeout=1.0,
            )
            logger.debug_throttled(
                f"tab_pool.memory_purge.{session.id}",
                f"[{session.id}] 空闲标签页已执行 JavaScript 内存回收",
                interval_sec=self._idle_memory_purge_interval_sec,
            )
        except Exception as e:
            logger.debug_throttled(
                f"tab_pool.memory_purge_error.{session.id}",
                f"[{session.id}] 空闲标签页内存回收失败（忽略）: {e}",
                interval_sec=self._idle_memory_purge_interval_sec,
            )
        finally:
            if heap_profiler_enabled:
                try:
                    self._run_cdp_compat(
                        session.tab,
                        "HeapProfiler.disable",
                        timeout=1.0,
                    )
                except Exception as e:
                    logger.debug_throttled(
                        f"tab_pool.memory_purge_disable_error.{session.id}",
                        f"[{session.id}] HeapProfiler 关闭失败（忽略）: {e}",
                        interval_sec=self._idle_memory_purge_interval_sec,
                    )

    def _cancel_active_request_for_session(
        self,
        session: Optional[TabSession],
        reason: str,
        *,
        detail: str = "",
    ) -> bool:
        """Request the bound workflow to stop when a session becomes unusable."""
        if session is None:
            return False

        request_ids = []
        for attr_name in ("current_task_id", "_command_request_id", "_bound_request_id", "_command_loop_request_id"):
            try:
                request_id = str(getattr(session, attr_name, "") or "").strip()
            except Exception:
                request_id = ""
            if request_id and request_id not in request_ids:
                request_ids.append(request_id)
        if not request_ids:
            return False

        task_id = request_ids[0]
        cancel_key = "|".join(request_ids)
        if getattr(session, "_last_cancel_request_task_id", None) == cancel_key:
            logger.debug(
                f"[{session.id}] duplicate cancel skipped "
                f"(task={cancel_key}, reason={reason}, "
                f"previous_reason={getattr(session, '_last_cancel_request_reason', '-')}, "
                f"detail={detail or '-'})"
            )
            return False

        try:
            setattr(session, "_workflow_stop_reason", reason)
            setattr(session, "_last_cancel_request_task_id", cancel_key)
            setattr(session, "_last_cancel_request_reason", reason)
        except Exception:
            pass

        cancel_submitted = False
        for request_id in request_ids:
            cancel_submitted = bool(self._submit_request_cancel(
                request_id,
                reason,
                session_id=session.id,
                detail=detail,
            )) or cancel_submitted

        logger.warning(
            f"[{session.id}] 会话失效，已请求取消任务 "
            f"(task={task_id}, requests={','.join(request_ids)}, reason={reason}, "
            f"cancel_submitted={cancel_submitted}, detail={detail or '-'})"
        )
        return cancel_submitted

    def _cleanup_unhealthy_tabs(self):
        """清理不健康的空闲标签页、关闭状态和错误状态的标签页"""
        to_remove: Dict[str, str] = {}

        for tab_id, session in list(self._tabs.items()):
            try:
                if session.is_isolated_context and session.is_healthy(allow_live_check=False):
                    session.clear_transient_disconnect()
                if session.status != TabStatus.ERROR:
                    self._preserved_error_session_ids.discard(tab_id)

                if session.status == TabStatus.CLOSED:
                    to_remove[tab_id] = "closed"
                # 清理 ERROR 状态的标签页（包括强制释放失败的）
                elif session.status == TabStatus.ERROR:
                    if self.preserve_error_tabs:
                        if tab_id not in self._preserved_error_session_ids:
                            logger.warning(
                                f"[{tab_id}] 错误状态已记录，按配置保留标签页 "
                                "(preserve_error_tabs=True)"
                            )
                            self._preserved_error_session_ids.add(tab_id)
                        continue
                    to_remove[tab_id] = "error"
                # 清理空闲但不健康的标签页
                elif session.status == TabStatus.IDLE and not session.is_healthy(allow_live_check=False):
                    if session.is_isolated_context:
                        if session.is_in_transient_disconnect():
                            continue
                        if self._arm_isolated_rebind_grace(
                            session,
                            reason="health_probe_failed",
                            detail="health probe failed",
                        ):
                            continue
                    to_remove[tab_id] = "unhealthy"
            except Exception as e:
                logger.warning(f"[TabPool] cleanup check failed for tab {tab_id}: {e}")
                to_remove[tab_id] = "health_check_failed"

        newly_quarantined: List[TabQuarantineEntry] = []
        for tab_id, removal_reason in list(to_remove.items()):
            try:
                session = self._tabs.get(tab_id)
                if not session:
                    continue
                # mark_closed 会清空 current_task_id，先捕获用于隔离条目
                quarantine_task_id = str(
                    getattr(session, "current_task_id", "") or ""
                ).strip()
                self._cancel_active_request_for_session(
                    session,
                    removal_reason,
                    detail=(
                        f"status={getattr(getattr(session, 'status', None), 'value', 'unknown')}, "
                        f"reason={removal_reason}"
                    ),
                )
                logger.warning(
                    f"[{tab_id}] 不健康或错误状态，从池中移除但保留浏览器标签页 "
                    f"({removal_reason})"
                )
                session.mark_closed(removal_reason)
                self._detach_global_monitor_for_session(tab_id, reason=removal_reason)

                # 清理映射表，允许相同 raw_tab_id 被重新扫描
                raw_ids_to_remove = [
                    raw_id for raw_id, p_idx in list(self._raw_id_to_persistent.items())
                    if self._persistent_to_session_id.get(p_idx) == tab_id
                ]
                for raw_id in raw_ids_to_remove:
                    self._known_tab_ids.discard(raw_id)
                    self._raw_id_to_persistent.pop(raw_id, None)
                    browser_context_id = self._isolated_context_by_raw_id.pop(raw_id, None)
                    if browser_context_id:
                        self._mark_orphaned_isolated_context(browser_context_id)
                    if removal_reason == "error":
                        # ERROR 会话移出池但浏览器标签页保留：旧 worker 可能仍
                        # 阻塞在该 tab 的同步调用里。先隔离 raw id，阻止下次扫描
                        # 立即重新入池（双工作流串扰的根源）；由 TabRecoveryService
                        # 在锁外确认旧 worker 退出后决定解除或永久隔离。
                        entry = TabQuarantineEntry(
                            raw_tab_id=raw_id,
                            persistent_index=int(
                                getattr(session, "persistent_index", 0) or 0
                            ),
                            task_id=quarantine_task_id,
                            tab=getattr(session, "tab", None),
                            session_id=tab_id,
                            reason=str(
                                getattr(session, "_workflow_stop_reason", "") or ""
                            ).strip() or removal_reason,
                            url=str(getattr(session, "last_known_url", "") or ""),
                        )
                        self._quarantined_raw_ids[raw_id] = entry
                        newly_quarantined.append(entry)
                        logger.info(
                            f"[TabRecovery] 已隔离错误标签页 "
                            f"(raw={raw_id}, tab={tab_id}, "
                            f"task={quarantine_task_id or '-'}, "
                            f"reason={entry.reason})"
                        )

                # 清理持久编号映射
                p_idx = session.persistent_index
                if p_idx and self._persistent_to_session_id.get(p_idx) == tab_id:
                    self._persistent_to_session_id.pop(p_idx, None)

                # 清理活动标签页记录
                if self._active_session_id == tab_id:
                    self._active_session_id = None

                self._tabs.pop(tab_id, None)
                self._preserved_error_session_ids.discard(tab_id)
                self._on_session_removed(tab_id)
            except Exception as e:
                logger.warning(f"[TabPool] cleanup remove failed for tab {tab_id}: {e}")

        # 投递到 maintenance executor 由恢复服务处理（submit 仅入队，
        # 持池锁调用不会阻塞，也不触发任何 CDP/网络操作）。
        for entry in newly_quarantined:
            self._submit_recovery(entry)

        if to_remove:
            self._condition.notify_all()

    def _should_defer_to_command(self, session: TabSession, task_id: str) -> bool:
        """Whether request acquisition should defer to high-priority pending/running commands."""
        task = str(task_id or "").strip().lower()
        if task.startswith("cmd_") or task.startswith("group_"):
            return False
        try:
            from app.services.command_engine import command_engine
            if hasattr(command_engine, "should_block_request_for_session"):
                return bool(command_engine.should_block_request_for_session(session, task_id=task_id))
        except Exception:
            return False
        return False

    def _normalize_acquire_timeout(self, timeout: Optional[float]) -> float:
        """统一各 acquire 路径的 timeout 语义。

        原先大多数路径写 `timeout = timeout or self.acquire_timeout`，于是
        timeout=0（调用方想表达"不等待，立刻失败"）会被当成"未传"而退化成默认
        60 秒阻塞，只有 acquire_by_route_group 一条路径按显式值处理。
        统一成：None -> 池默认；其余按显式值处理，负数收敛到 0。
        """
        if timeout is None:
            return float(self.acquire_timeout)
        try:
            return max(0.0, float(timeout))
        except (TypeError, ValueError):
            return float(self.acquire_timeout)

    def _next_waiter_token(self, task_id: str) -> str:
        self._waiter_counter += 1
        base = str(task_id or "task").strip() or "task"
        return f"{base}#{self._waiter_counter}"

    @staticmethod
    def _is_waiter_turn(waiters: deque[str], waiter_token: str) -> bool:
        return not waiters or waiters[0] == waiter_token

    @staticmethod
    def _count_waiters_ahead(waiters: deque[str], waiter_token: str) -> int:
        try:
            return waiters.index(waiter_token)
        except ValueError:
            return 0

    def _unregister_waiter(
        self,
        waiters: deque[str],
        waiter_token: str,
        *,
        owner_map: Optional[Dict[Any, deque[str]]] = None,
        owner_key: Optional[Any] = None,
    ) -> bool:
        removed = False
        try:
            waiters.remove(waiter_token)
            removed = True
        except ValueError:
            removed = False

        # 只有当 owner_map 里登记的仍是同一个队列对象时才摘除。
        # 成功路径会先显式 _unregister_waiter 一次（此时队列可能已空并被摘除），
        # 随后 finally 再调一次；期间池锁可能被 _complete_acquired_session_for_return
        # 短暂释放，其他线程会用同一个 key 建出**新的**队列。若这里无条件 pop，
        # 就会把新队列连同里面排队的等待者一起丢掉，破坏 FIFO 公平性。
        if (
            owner_map is not None
            and owner_key is not None
            and not waiters
            and owner_map.get(owner_key) is waiters
        ):
            owner_map.pop(owner_key, None)

        if removed:
            self._condition.notify_all()
        return removed

    @staticmethod
    def _describe_session(session: Optional[TabSession]) -> str:
        if session is None:
            return "session=-"
        try:
            return session.debug_summary()
        except Exception as e:
            return f"session={getattr(session, 'id', '-')}, snapshot_error={e}"

    def _describe_index_wait_state(
        self,
        persistent_index: int,
        waiters: deque[str],
        waiter_token: str,
    ) -> str:
        session_id = self._persistent_to_session_id.get(persistent_index)
        session = self._tabs.get(session_id) if session_id else None
        ahead = self._count_waiters_ahead(waiters, waiter_token)
        busy_for = "-"
        if session is not None:
            try:
                if getattr(session, "status", None) == TabStatus.BUSY:
                    busy_for = (
                        f"{max(0.0, time.time() - float(getattr(session, 'last_used_at', 0.0) or 0.0)):.1f}s"
                    )
            except Exception:
                busy_for = "?"
        return (
            f"idx=#{persistent_index}, ahead={ahead}, waiters={len(waiters)}, "
            f"busy_for={busy_for}, holder={self._describe_session(session)}"
        )


    async def acquire_async(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        return await self._run_cancellable_acquire(
            lambda: self.acquire(task_id, timeout),
            task_id,
        )

    def _ensure_acquire_executor(self) -> ThreadPoolExecutor:
        # 双重检查懒创建：读属性无锁，创建走专用小锁（不碰池锁，事件循环线程不会
        # 被池维护路径阻塞）。max_workers=32 覆盖"池满 + 一批客户端断连"的堆积场景。
        executor = self._acquire_executor
        if executor is not None:
            return executor
        with self._acquire_executor_lock:
            if self._acquire_executor is None:
                self._acquire_executor = ThreadPoolExecutor(
                    max_workers=32,
                    thread_name_prefix="tab-acquire",
                )
            return self._acquire_executor

    async def _run_cancellable_acquire(self, acquire_fn, task_id: str) -> Optional[TabSession]:
        """Release a session acquired after the awaiting task was cancelled."""
        # 改用专用 executor：默认 executor 线程数有限（约 min(32, cpu+4)），被取消的
        # acquire 仍会占线程直到超时，占满后所有 to_thread 排不上队。
        # run_in_executor 返回的 Future 同样兼容 shield 与 add_done_callback，
        # shield 语义不变：外层取消不打断底层 acquire 线程，结果由回调兜底释放。
        loop = asyncio.get_running_loop()
        acquire_task = loop.run_in_executor(self._ensure_acquire_executor(), acquire_fn)
        try:
            return await asyncio.shield(acquire_task)
        except asyncio.CancelledError:
            def _release_late_session(completed_task) -> None:
                try:
                    session = completed_task.result()
                except BaseException:
                    return
                if session is None:
                    return

                def _do_release() -> None:
                    try:
                        self.release(
                            session.id,
                            check_triggers=False,
                            rollback_request_count=True,
                            expected_task_id=task_id,
                        )
                    except Exception as exc:
                        logger.warning(
                            f"Cancelled acquire cleanup failed (task={task_id}, "
                            f"tab={getattr(session, 'id', '-')}, error={exc})"
                        )

                # 锁序考量：done callback 在事件循环线程执行，release 含池锁竞争与
                # CDP 调用（visibility 恢复各 0.5s 超时），不能同步跑在事件循环里。
                # 投递到 maintenance executor；submit 失败（已关闭）回退 daemon 线程。
                executor = self._maintenance_executor
                submitted = False
                if executor is not None:
                    try:
                        executor.submit(_do_release)
                        submitted = True
                    except RuntimeError as exc:
                        logger.debug(
                            f"[TabPool] maintenance submit failed "
                            f"(late-release:{getattr(session, 'id', '-')}): {exc}"
                        )
                if not submitted:
                    threading.Thread(
                        target=_do_release,
                        name="tab-late-release",
                        daemon=True,
                    ).start()

            acquire_task.add_done_callback(_release_late_session)
            raise

    def release(
        self,
        tab_id: str,
        clear_page: bool = False,
        check_triggers: bool = True,
        rollback_request_count: bool = False,
        expected_task_id: str = "",
    ) -> bool:
        """释放标签页"""
        with self._condition:
            session = self._tabs.get(tab_id)
            if not session:
                return False

            before_snapshot = self._describe_session(session)
            expected_task = str(expected_task_id or "").strip()
            current_task = str(getattr(session, "current_task_id", "") or "").strip()
            session_status = getattr(getattr(session, "status", None), "value", "")
            if current_task and not expected_task:
                logger.warning(
                    f"[{tab_id}] 跳过无所有权释放 "
                    f"(current_task={current_task}, snapshot={before_snapshot})"
                )
                return False
            if expected_task:
                if current_task and current_task != expected_task:
                    logger.warning(
                        f"[{tab_id}] 跳过释放：标签页已被其他任务接管 "
                        f"(expected_task={expected_task}, current_task={current_task}, "
                        f"snapshot={before_snapshot})"
                    )
                    return False
                if not current_task:
                    logger.warning(
                        f"[{tab_id}] 跳过释放：标签页 task_id 已丢失 "
                        f"(expected_task={expected_task}, status={session_status or 'unknown'}, "
                        f"snapshot={before_snapshot})"
                    )
                    return False

            release_state = session._begin_release_state(
                clear_page=clear_page,
                rollback_request_count=rollback_request_count,
                force=False,
                expected_task_id=expected_task,
                reject_ownerless=True,
            )
            if release_state is None:
                self._condition.notify_all()
                return False

        session._run_release_from_state(
            release_state,
            clear_page=clear_page,
            check_triggers=check_triggers,
            rollback_request_count=rollback_request_count,
        )

        with self._condition:
            if self._tabs.get(tab_id) is session:
                # 锁序考量：不在持池锁时同步启动 monitor——start_for_session 可能
                # join 停止中的 worker 最长 2s（本文件他处已注明持池锁禁止 join worker）。
                # 复用 _restart_global_monitor_for_session_async 投递到 maintenance
                # executor：它会在池锁外复核会话仍存在且 IDLE 再启动，start_for_session
                # 本身幂等（已有存活 worker 时直接返回）；若会话已被再次 acquire，
                # 复核发现非 IDLE 即放弃，acquire 侧的 handoff stop 亦会兜底摘除。
                self._restart_global_monitor_for_session_async(tab_id, "release")
                self._condition.notify_all()
                logger.debug(
                    f"[{tab_id}] 已释放 "
                    f"(expected_task={expected_task or '-'}, clear_page={clear_page}, "
                    f"check_triggers={check_triggers}, rollback_request_count={rollback_request_count}, "
                    f"before={before_snapshot}, after={self._describe_session(session)})"
                )
                return session.status == TabStatus.IDLE
        return False

    def force_release_all(self):
        """强制释放所有标签页（调试用）"""
        with self._condition:
            pending: List[tuple[TabSession, Dict[str, Any]]] = []
            for session in self._tabs.values():
                if session.status == TabStatus.BUSY:
                    # 先请求取消旧工作流再强制释放：否则 BUSY 会话被洗成 IDLE 后，
                    # 旧工作流尚未退出，同一 tab 可能立刻被再分配，新旧任务并发操作
                    # 同一页面。锁序安全：_cancel_active_request_for_session 只向
                    # maintenance executor 投递，持池锁时不外呼。
                    busy_task = str(session.current_task_id or "").strip()
                    cancel_submitted = self._cancel_active_request_for_session(
                        session,
                        "force_release_all",
                        detail=f"task={busy_task or '-'}",
                    )
                    logger.warning(
                        f"[{session.id}] force_release_all 强制释放忙碌会话 "
                        f"(task={busy_task or '-'}, cancel_submitted={cancel_submitted})"
                    )
                    release_state = session._begin_release_state(
                        clear_page=False,
                        force=True,
                    )
                    if release_state is not None:
                        pending.append((session, release_state))

        for session, release_state in pending:
            session._run_force_release_from_state(
                release_state,
                clear_page=False,
                check_triggers=False,
            )

        with self._condition:
            for session, _release_state in pending:
                if self._tabs.get(session.id) is session and session.status == TabStatus.IDLE:
                    self._start_global_monitor_for_session(session)
            self._condition.notify_all()
            count = len(pending)
            logger.info(f"强制释放 {count} 个标签页")
            return count

    def refresh_tabs(self) -> Dict:
        """手动刷新标签页列表（供外部调用）"""
        with self._condition:
            old_count = len(self._tabs)
            old_ids = set(self._tabs.keys())

            # 强制扫描（不受时间间隔限制）
            self._last_scan_time = 0
            self._scan_new_tabs()

            # 同时清理不健康的标签页
            self._cleanup_unhealthy_tabs()

            new_ids = set(self._tabs.keys())
            added = new_ids - old_ids
            removed = old_ids - new_ids

            if added or removed:
                self._condition.notify_all()
                logger.info(f"刷新完成: +{len(added)} -{len(removed)} = {len(self._tabs)} 个标签页")

            return {
                "added": len(added),
                "removed": len(removed),
                "total": len(self._tabs)
            }

    @asynccontextmanager
    async def get_tab(self, task_id: str, timeout: float = None):
        session = await self.acquire_async(task_id, timeout)
        if session is None:
            raise TimeoutError(f"获取标签页超时 (task: {task_id})")

        try:
            yield session
        except Exception as e:
            session.mark_error(str(e))
            raise
        finally:
            # 锁序考量：release 含池锁竞争与 CDP 调用（visibility 恢复各 0.5s 超时），
            # 移出事件循环线程执行。shield 保证 __aexit__ 期间再次被取消时，
            # release 仍在后台任务/线程中完成，不会泄漏标签页占用。
            await asyncio.shield(
                asyncio.to_thread(self.release, session.id, expected_task_id=task_id)
            )

    def acquire_by_raw_tab_id(
        self,
        raw_tab_id: str,
        task_id: str,
        timeout: float = None,
        count_request: bool = True,
        activate: Optional[bool] = None,
    ) -> Optional[TabSession]:
        """
        根据底层浏览器标签页 ID 获取指定标签页会话。

        这用于外部已经明确知道目标标签页时，复用 TabPool 的运行时上下文，
        保持与正常工作流执行一致的监听、占用与释放行为。
        """
        raw_tab_id = str(raw_tab_id or "").strip()
        if not raw_tab_id:
            return None

        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout

        with self._condition:
            while True:
                if self._shutdown:
                    return None

                if self._should_scan():
                    self._scan_new_tabs()

                self._check_stuck_tabs()
                self._cleanup_unhealthy_tabs()

                persistent_index = self._raw_id_to_persistent.get(raw_tab_id)
                if not persistent_index:
                    logger.warning(f"底层标签页 ID 不存在: {raw_tab_id}")
                    return None

                session_id = self._persistent_to_session_id.get(persistent_index)
                if not session_id:
                    logger.warning(f"底层标签页 {raw_tab_id} 未绑定会话")
                    return None

                session = self._tabs.get(session_id)
                if not session:
                    logger.warning(f"底层标签页 {raw_tab_id} 对应会话已移除")
                    return None

                if not session.is_healthy(allow_live_check=False):
                    logger.warning(f"[{session.id}] 标签页不健康")
                    return None

                if self._should_defer_to_command(session, task_id):
                    logger.debug(f"[{session.id}] defer raw-tab acquire to high-priority command")
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return None
                    self._condition.wait(timeout=min(remaining, 0.5))
                    continue

                if session.status == TabStatus.IDLE:
                    acquired = (
                        session.acquire(task_id)
                        if count_request
                        else session.acquire_for_command(task_id)
                    )
                    if acquired:
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_raw_tab_id",
                            task_id,
                            rollback_request_count=count_request,
                            activate=(
                                self._auto_activate_on_acquire and session.id != self._active_session_id
                                if activate is None
                                else bool(activate)
                            ),
                        ):
                            continue
                        logger.debug(
                            f"TabPool → {session.id} "
                            f"(task={task_id}, raw_tab_id={raw_tab_id}, idx=#{persistent_index}, "
                            f"count_request={count_request}, snapshot={self._describe_session(session)})"
                        )
                        return session

                remaining = deadline - time.time()
                if remaining <= 0:
                    logger.warning(
                        f"获取底层标签页 {raw_tab_id} 超时 "
                        f"(task={task_id}, 当前状态: {session.status.value}, "
                        f"snapshot={self._describe_session(session)})"
                    )
                    return None

                logger.debug_throttled(
                    f"tab_pool.wait_raw.{raw_tab_id}",
                    f"等待底层标签页 {raw_tab_id} 释放...",
                    interval_sec=5.0,
                )
                self._condition.wait(timeout=min(remaining, 1.0))


    async def acquire_by_index_async(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """异步版本的按编号获取"""
        return await self._run_cancellable_acquire(
            lambda: self.acquire_by_index(persistent_index, task_id, timeout),
            task_id,
        )

    def _get_sessions_for_route_domain(self, route_domain: str) -> List[TabSession]:
        target = normalize_route_domain(route_domain)
        if not target:
            return []

        matches: List[TabSession] = []
        for session in self._tabs.values():
            current_url, actual_domain = session.get_cached_route_snapshot()
            if route_domain_matches(target, actual_domain):
                if self._is_session_excluded_from_dynamic_routing(session, current_url):
                    continue
                matches.append(session)

        return matches

    def _get_route_group_config(self, group_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = normalize_route_group_id(group_id)
        if not normalized_id:
            return None
        return route_groups_by_id(getattr(self, "route_groups", [])).get(normalized_id)

    @staticmethod
    def _session_matches_route_group_member(
        session: TabSession,
        member: Dict[str, Any],
    ) -> bool:
        current_url, _actual_domain = session.get_cached_route_snapshot()
        member_url = str(member.get("url") or "").strip()
        member_token = str(member.get("url_token") or "").strip().lower()
        if member_url and tab_url_matches(member_url, current_url):
            return True
        if member_token and encode_tab_url_route_token(current_url) == member_token:
            return True
        return False

    def _get_sessions_for_route_group(
        self,
        group_id: str,
        *,
        bind: bool = True,
    ) -> List[TabSession]:
        """解析路由组当前的成员标签页。

        bind=False 供只读查询（/api/tabs 面板轮询、组快照）使用：只做匹配，
        不写 _route_group_bindings。否则一次与请求无关的面板轮询就会替真实请求
        提前把成员钉死在某个标签页上（绑定是粘滞的），后续请求再也轮不到同 URL
        的其他标签页。
        """
        group = self._get_route_group_config(group_id)
        if not group:
            return []

        normalized_id = group["id"]
        members = group.get("members") or []
        valid_member_keys = {route_group_member_key(member) for member in members}
        if bind:
            bindings = self._route_group_bindings.setdefault(normalized_id, {})
            for member_key in list(bindings):
                session_id = bindings.get(member_key)
                if member_key not in valid_member_keys or session_id not in self._tabs:
                    bindings.pop(member_key, None)
        else:
            bindings = {
                member_key: session_id
                for member_key, session_id in self._route_group_bindings.get(normalized_id, {}).items()
                if member_key in valid_member_keys and session_id in self._tabs
            }

        sessions: List[TabSession] = []
        used_session_ids = set()
        ordered_sessions = sorted(self._tabs.values(), key=self._session_allocation_key)
        for member in members:
            member_key = route_group_member_key(member)
            bound_session = self._tabs.get(bindings.get(member_key, ""))
            if bound_session is not None and bound_session.id not in used_session_ids:
                sessions.append(bound_session)
                used_session_ids.add(bound_session.id)
                continue

            matching = [
                session
                for session in ordered_sessions
                if session.id not in used_session_ids
                and self._session_matches_route_group_member(session, member)
            ]
            try:
                preferred_index = int(member.get("tab_index") or 0)
            except (TypeError, ValueError):
                preferred_index = 0
            if preferred_index > 0:
                matching.sort(
                    key=lambda session: (
                        int(session.persistent_index or 0) != preferred_index,
                        self._session_allocation_key(session),
                    )
                )
            if not matching:
                bindings.pop(member_key, None)
                continue

            selected = matching[0]
            bindings[member_key] = selected.id
            sessions.append(selected)
            used_session_ids.add(selected.id)

        return sessions

    def get_route_groups_snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            groups = normalize_route_groups(getattr(self, "route_groups", []))
            result = []
            for group in groups:
                sessions = self._get_sessions_for_route_group(group["id"], bind=False)
                item = dict(group)
                item["live_member_count"] = len(sessions)
                item["idle_member_count"] = sum(
                    1 for session in sessions if session.status == TabStatus.IDLE
                )
                item["busy_member_count"] = sum(
                    1 for session in sessions if session.status == TabStatus.BUSY
                )
                item["live_tab_indices"] = [
                    int(session.persistent_index or 0) for session in sessions
                ]
                result.append(item)
            return result

    def _refresh_route_snapshots_for_sessions(self, sessions: List[TabSession]) -> None:
        """Refresh live tab URL/domain snapshots; callers must not hold self._condition."""
        for session in sessions:
            try:
                # BUSY 标签页跳过实时读取：
                # 1) 该 tab 正被别的工作流线程用同步 DrissionPage 调用驱动，
                #    这里再发一次 CDP 是并发访问同一 target（_scan_new_tabs 同样避开 BUSY）；
                # 2) 工作流中途导航时 tab.url 会瞬时变成 about:blank，
                #    _refresh_current_domain 会据此把 current_domain 清空，
                #    使并发的 /url/<domain> 请求匹配不到这个标签页而误报 404。
                # BUSY 标签页的 URL 已由 _scan_new_tabs 从 Target.getTargets 快照刷新。
                if session.status == TabStatus.BUSY:
                    continue
                current_url = session._safe_get_url(allow_live_when_busy=True)
                if current_url:
                    session._refresh_current_domain(current_url)
            except Exception as e:
                logger.debug(f"[{session.id}] failed to refresh route snapshot: {e}")

    def _refresh_route_snapshots_unlocked_once(self) -> None:
        """Refresh route snapshots once without holding the pool condition."""
        sessions_to_refresh = list(self._tabs.values())
        self._condition.release()
        try:
            self._refresh_route_snapshots_for_sessions(sessions_to_refresh)
        finally:
            self._condition.acquire()


    async def acquire_by_route_domain_async(
        self,
        route_domain: str,
        task_id: str,
        timeout: float = None,
        allocation_mode: Optional[str] = None,
    ) -> Optional[TabSession]:
        """异步版本的按域名路由获取。"""
        return await self._run_cancellable_acquire(
            lambda: self.acquire_by_route_domain(
                route_domain,
                task_id,
                timeout,
                allocation_mode,
            ),
            task_id,
        )

    async def acquire_by_route_group_async(
        self,
        group_id: str,
        task_id: str,
        timeout: float = None,
        allocation_mode: Optional[str] = None,
    ) -> Optional[TabSession]:
        return await self._run_cancellable_acquire(
            lambda: self.acquire_by_route_group(
                group_id,
                task_id,
                timeout,
                allocation_mode,
            ),
            task_id,
        )




    def acquire(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """ASCII-safe fair acquire for generic request routing."""
        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout
        logged_waiting = False
        first_iteration = True

        with self._condition:
            waiter_token = self._next_waiter_token(task_id)
            self._acquire_waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None

                    if first_iteration or self._should_scan():
                        self._scan_new_tabs()
                        first_iteration = False

                    self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()

                    if not self._is_waiter_turn(self._acquire_waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            busy_info = [
                                f"{s.id}({s.current_task_id})"
                                for s in self._tabs.values()
                                if s.status == TabStatus.BUSY
                            ]
                            unhealthy_count = sum(
                                1 for s in self._tabs.values()
                                if s.status == TabStatus.IDLE and not s.is_healthy(allow_live_check=False)
                            )
                            logger.warning(
                                f"Acquire tab timed out (task={task_id}, busy: {', '.join(busy_info) or 'none'}, "
                                f"unhealthy: {unhealthy_count})"
                            )
                            return None

                        if not logged_waiting:
                            busy_tabs = [s.id for s in self._tabs.values() if s.status == TabStatus.BUSY]
                            ahead = self._count_waiters_ahead(self._acquire_waiters, waiter_token)
                            if busy_tabs:
                                logger.debug(f"Waiting for tab (busy: {', '.join(busy_tabs)})")
                            elif ahead > 0:
                                logger.debug(f"Waiting in queue (ahead: {ahead})")
                            logged_waiting = True

                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    dynamic_sessions = self._get_sessions_for_dynamic_routing()
                    if not dynamic_sessions and self._tabs:
                        logger.warning(
                            f"Acquire tab skipped: all open tabs are excluded from dynamic routing "
                            f"(task={task_id})"
                        )
                        return None

                    session = self._try_acquire_session_for_request(
                        dynamic_sessions,
                        task_id,
                    )
                    if session is not None:
                        self._unregister_waiter(self._acquire_waiters, waiter_token)
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            return None
                        self._mark_allocation_cursor(session)

                        if logged_waiting:
                            logger.debug(
                                f"Acquire finished -> {session.id} "
                                f"(task={task_id}, snapshot={self._describe_session(session)})"
                            )
                        else:
                            logger.debug(
                                f"TabPool -> {session.id} "
                                f"(task={task_id}, snapshot={self._describe_session(session)})"
                            )
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        busy_info = [
                            f"{s.id}({s.current_task_id})"
                            for s in self._tabs.values()
                            if s.status == TabStatus.BUSY
                        ]
                        unhealthy_count = sum(
                            1 for s in self._tabs.values()
                            if s.status == TabStatus.IDLE and not s.is_healthy(allow_live_check=False)
                        )
                        logger.warning(
                            f"Acquire tab timed out (task={task_id}, busy: {', '.join(busy_info) or 'none'}, "
                            f"unhealthy: {unhealthy_count})"
                        )
                        return None

                    if not logged_waiting:
                        busy_tabs = [s.id for s in self._tabs.values() if s.status == TabStatus.BUSY]
                        if busy_tabs:
                            logger.debug(f"Waiting for tab (busy: {', '.join(busy_tabs)})")
                        logged_waiting = True

                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(self._acquire_waiters, waiter_token)

    def _get_sessions_for_exact_url(self, exact_url: str) -> List[TabSession]:
        target = str(exact_url or "").strip()
        if not target:
            return []

        matches: List[TabSession] = []
        for session in self._tabs.values():
            current_url, _actual_domain = session.get_cached_route_snapshot()
            if tab_url_matches(target, current_url):
                matches.append(session)

        return matches

    def acquire_by_exact_url(self, exact_url: str, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """Acquire a tab by strict full-URL match, round-robin within identical URLs."""
        raw_target = str(exact_url or "").strip()
        if not raw_target:
            logger.warning("Exact tab URL is empty; cannot acquire a tab")
            return None

        # 队列 key / 轮询游标 key 必须用归一化后的 URL：匹配侧 tab_url_matches 会剥掉
        # 默认端口等差异，若这里仍用原始串，"https://x/a" 与 "https://x:443/a"
        # 会落进两条互不相干的等待队列，各自都是队首，FIFO 互斥形同虚设。
        target = normalize_exact_tab_url(raw_target) or raw_target
        waiter_key = f"url::{target}"

        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout

        with self._condition:
            waiters = self._route_waiters.setdefault(waiter_key, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None

                    if self._should_scan():
                        self._scan_new_tabs()

                    self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(
                                f"Timed out waiting for exact URL '{target}' (task={task_id})"
                            )
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_exact_url.{target}",
                            f"Waiting for exact URL '{target}' release...",
                            interval_sec=5.0,
                        )
                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    self._refresh_route_snapshots_unlocked_once()
                    matching_sessions = self._get_sessions_for_exact_url(target)
                    if not matching_sessions:
                        logger.warning(f"No tab matches exact URL '{target}'")
                        return None
                    session = self._try_acquire_session_for_request(
                        matching_sessions,
                        task_id,
                        route_domain=waiter_key,
                        defer_context="exact-url acquire",
                    )
                    if session is not None:
                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._route_waiters,
                            owner_key=waiter_key,
                        )
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_exact_url",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            return None
                        self._mark_allocation_cursor(session, route_domain=waiter_key)

                        logger.debug(
                            f"TabPool -> {session.id} "
                            f"(task={task_id}, exact_url={target}, "
                            f"idx=#{session.persistent_index}, snapshot={self._describe_session(session)})"
                        )
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        logger.warning(
                            f"Timed out waiting for exact URL '{target}' "
                            f"(task={task_id}, matching tabs: "
                            f"{', '.join(f'{s.id}(#{s.persistent_index}:{s.status.value})' for s in matching_sessions) or 'none'})"
                        )
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_exact_url.{target}",
                        f"Waiting for exact URL '{target}' release...",
                        interval_sec=5.0,
                    )
                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._route_waiters,
                    owner_key=waiter_key,
                )

    def acquire_by_index(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """ASCII-safe fair acquire for a fixed persistent tab index."""
        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout

        with self._condition:
            waiters = self._index_waiters.setdefault(persistent_index, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None

                    if self._should_scan():
                        self._scan_new_tabs()

                    # Keep fixed-index acquisition aligned with other wait paths so a wedged
                    # holder can be cancelled and released instead of blocking the queue.
                    released_stuck = self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()
                    if released_stuck:
                        logger.debug(
                            f"Rechecking tab #{persistent_index} after stuck-tab maintenance "
                            f"(task={task_id}, wait_state={self._describe_index_wait_state(persistent_index, waiters, waiter_token)})"
                        )

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(
                                f"Timed out waiting for tab #{persistent_index} "
                                f"(task={task_id}, wait_state={self._describe_index_wait_state(persistent_index, waiters, waiter_token)})"
                            )
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_index.{persistent_index}",
                            f"Waiting for tab #{persistent_index} release... "
                            f"({self._describe_index_wait_state(persistent_index, waiters, waiter_token)})",
                            interval_sec=5.0,
                        )
                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    session_id = self._persistent_to_session_id.get(persistent_index)
                    if not session_id:
                        logger.warning(f"Persistent tab #{persistent_index} does not exist")
                        return None

                    session = self._tabs.get(session_id)
                    if not session:
                        logger.warning(f"Tab {session_id} (#{persistent_index}) was removed")
                        return None

                    if not session.is_healthy(allow_live_check=False):
                        logger.warning(
                            f"[{session.id}] tab is unhealthy "
                            f"(task={task_id}, snapshot={self._describe_session(session)})"
                        )
                        return None

                    if self._should_defer_to_command(session, task_id):
                        logger.debug(
                            f"[{session.id}] defer by index acquire to high-priority command "
                            f"(task={task_id}, snapshot={self._describe_session(session)})"
                        )
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            return None
                        self._condition.wait(timeout=min(remaining, 0.5))
                        continue

                    if session.status == TabStatus.IDLE and session.acquire(task_id):
                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._index_waiters,
                            owner_key=persistent_index,
                        )
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_index",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            return None
                        logger.debug(
                            f"TabPool -> {session.id} "
                            f"(task={task_id}, idx=#{persistent_index}, "
                            f"snapshot={self._describe_session(session)})"
                        )
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        logger.warning(
                            f"Timed out waiting for tab #{persistent_index} "
                            f"(task={task_id}, status: {session.status.value}, "
                            f"wait_state={self._describe_index_wait_state(persistent_index, waiters, waiter_token)}, "
                            f"snapshot={self._describe_session(session)})"
                        )
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_index.{persistent_index}",
                        f"Waiting for tab #{persistent_index} release... "
                        f"({self._describe_index_wait_state(persistent_index, waiters, waiter_token)})",
                        interval_sec=5.0,
                    )
                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._index_waiters,
                    owner_key=persistent_index,
                )

    def acquire_by_route_domain(
        self,
        route_domain: str,
        task_id: str,
        timeout: float = None,
        allocation_mode: Optional[str] = None,
    ) -> Optional[TabSession]:
        """ASCII-safe fair acquire for tabs matching the same route domain."""
        target = normalize_route_domain(route_domain)
        if not target:
            logger.warning("Route domain is empty; cannot acquire a tab")
            return None

        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout

        with self._condition:
            waiters = self._route_waiters.setdefault(target, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None

                    if self._should_scan():
                        self._scan_new_tabs()

                    self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(
                                f"Timed out waiting for route domain '{target}' (task={task_id})"
                            )
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_route.{target}",
                            f"Waiting for route domain '{target}' release...",
                            interval_sec=5.0,
                        )
                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    self._refresh_route_snapshots_unlocked_once()
                    matching_sessions = self._get_sessions_for_route_domain(target)
                    if not matching_sessions:
                        logger.warning(f"No tab matches route domain '{target}'")
                        return None

                    session = self._try_acquire_session_for_request(
                        matching_sessions,
                        task_id,
                        route_domain=target,
                        allocation_mode=allocation_mode,
                        defer_context="route-domain acquire",
                    )
                    if session is not None:
                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._route_waiters,
                            owner_key=target,
                        )
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_route_domain",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            return None
                        self._mark_allocation_cursor(session, route_domain=target)

                        logger.debug(
                            f"TabPool -> {session.id} "
                            f"(task={task_id}, route_domain={target}, "
                            f"idx=#{session.persistent_index}, snapshot={self._describe_session(session)})"
                        )
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        busy_info = [
                            f"{session.id}(#{session.persistent_index}:{session.status.value})"
                            for session in matching_sessions
                        ]
                        logger.warning(
                            f"Timed out waiting for route domain '{target}' "
                            f"(task={task_id}, matching tabs: {', '.join(busy_info) or 'none'})"
                        )
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_route.{target}",
                        f"Waiting for route domain '{target}' release...",
                        interval_sec=5.0,
                    )
                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._route_waiters,
                    owner_key=target,
                )

    def acquire_by_route_group(
        self,
        group_id: str,
        task_id: str,
        timeout: float = None,
        allocation_mode: Optional[str] = None,
    ) -> Optional[TabSession]:
        target = normalize_route_group_id(group_id)
        if not target:
            logger.warning("Route group ID is invalid")
            return None

        timeout = self._normalize_acquire_timeout(timeout)
        deadline = time.time() + timeout
        first_iteration = True

        with self._condition:
            group = self._get_route_group_config(target)
            if not group:
                logger.warning(f"Route group '{target}' does not exist")
                return None

            waiters = self._group_waiters.setdefault(target, deque())
            waiter_token = self._next_waiter_token(task_id)
            waiters.append(waiter_token)
            try:
                while True:
                    if self._shutdown:
                        return None

                    # 组配置每轮重新读取：apply_runtime_config 可能在等待期间
                    # 删除/改名该组或改掉分配模式。用循环外的旧快照会让等待者既不
                    # 报"组不存在"也匹配不到成员，只能空转到超时。
                    group = self._get_route_group_config(target)
                    if not group:
                        logger.warning(
                            f"Route group '{target}' no longer exists (task={task_id})"
                        )
                        return None

                    if first_iteration or self._should_scan():
                        self._scan_new_tabs()
                        first_iteration = False

                    self._check_stuck_tabs()
                    self._cleanup_unhealthy_tabs()

                    if not self._is_waiter_turn(waiters, waiter_token):
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            logger.warning(
                                f"Timed out waiting in route group '{target}' (task={task_id})"
                            )
                            return None
                        logger.debug_throttled(
                            f"tab_pool.wait_group.{target}",
                            f"Waiting in route group '{target}' queue...",
                            interval_sec=5.0,
                        )
                        self._condition.wait(timeout=min(remaining, 1.0))
                        continue

                    matching_sessions = self._get_sessions_for_route_group(target)
                    # 空组没有任何可能在后续释放的成员。立即返回，让上游的
                    # 多 URL 轮询跳到下一个 URL，而不是按默认 60 秒超时等待。
                    if not matching_sessions:
                        logger.info(
                            f"Route group '{target}' has no live members; skip immediately "
                            f"(task={task_id})"
                        )
                        return None

                    mode = allocation_mode or group.get("allocation_mode") or self.allocation_mode
                    session = self._try_acquire_session_for_request(
                        matching_sessions,
                        task_id,
                        route_domain=f"group::{target}",
                        allocation_mode=mode,
                        defer_context=f"route-group {target} acquire",
                    )
                    if session is not None:
                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._group_waiters,
                            owner_key=target,
                        )
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_route_group",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            # 与 acquire/acquire_by_route_domain 等路径保持一致：
                            # handoff 失败直接返回 None。此处 waiter 已出队，若 continue
                            # 会因 _is_waiter_turn 永远轮不到而空转到超时。
                            # 调用方（workflow.py）已有 session is None 的处理分支。
                            return None
                        self._mark_allocation_cursor(session, route_domain=f"group::{target}")
                        logger.debug(
                            f"TabPool -> {session.id} "
                            f"(task={task_id}, route_group={target}, "
                            f"idx=#{session.persistent_index}, snapshot={self._describe_session(session)})"
                        )
                        return session

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        member_state = ", ".join(
                            f"{session.id}(#{session.persistent_index}:{session.status.value})"
                            for session in matching_sessions
                        ) or "no live members"
                        logger.warning(
                            f"Timed out waiting for route group '{target}' "
                            f"(task={task_id}, members: {member_state})"
                        )
                        return None

                    logger.debug_throttled(
                        f"tab_pool.wait_group.{target}",
                        f"Waiting for route group '{target}' member release...",
                        interval_sec=5.0,
                    )
                    self._condition.wait(timeout=min(remaining, 1.0))
            finally:
                self._unregister_waiter(
                    waiters,
                    waiter_token,
                    owner_map=self._group_waiters,
                    owner_key=target,
                )

    def terminate_by_index(
        self,
        persistent_index: int,
        reason: str = "manual_terminate",
        clear_page: bool = True,
        scope: str = "task",
        expected_session_id: str = "",
        expected_task_id: str = "",
    ) -> Dict[str, Any]:
        """
        按标签页编号终止当前任务，并在旧所有者退出前隔离该成员。

        行为：
        1) 校验调用方看到的 session/task 所有权快照；
        2) 设置终止隔离闸门，阻止组/域名/编号路由重新分配；
        3) 取消该成员关联请求并中断页面活动；
        4) 等待旧工作线程完成正常释放，超时则继续保持隔离。
        """
        normalized_scope = str(scope or "task").strip().lower()
        if normalized_scope not in {"task", "loop"}:
            normalized_scope = "task"
        expected_session = str(expected_session_id or "").strip()
        expected_task = str(expected_task_id or "").strip()

        with self._condition:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}

            session = self._tabs.get(session_id)
            if not session:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}
            if expected_session and session.id != expected_session:
                return {
                    "ok": False,
                    "error": "session_ownership_changed",
                    "tab_index": persistent_index,
                    "expected_session_id": expected_session,
                    "current_session_id": session.id,
                }

            current_task = str(session.current_task_id or "").strip()
            if expected_task and current_task != expected_task:
                return {
                    "ok": False,
                    "error": "task_ownership_changed",
                    "tab_index": persistent_index,
                    "tab_id": session.id,
                    "expected_task_id": expected_task,
                    "current_task_id": current_task,
                }

            if normalized_scope == "loop":
                loop_cancelled = session.request_command_loop_cancel(reason)
                self._condition.notify_all()
                if not loop_cancelled:
                    return {
                        "ok": False,
                        "error": "no_active_command_loop",
                        "tab_index": persistent_index,
                        "tab_id": session.id,
                        "status": session.status.value,
                        "reason": reason,
                        "scope": normalized_scope,
                    }
                logger.warning(
                    f"[{session.id}] 手动终止当前循环: idx=#{persistent_index}, "
                    f"status={session.status.value}, reason={reason}, "
                    f"loop={session.get_command_loop_info()}"
                )
                return {
                    "ok": True,
                    "tab_index": persistent_index,
                    "tab_id": session.id,
                    "was_busy": session.status == TabStatus.BUSY,
                    "task_id": str(session.current_task_id or "").strip(),
                    "cancelled": True,
                    "status": session.status.value,
                    "reason": reason,
                    "scope": normalized_scope,
                    "loop_cancelled": True,
                    "command_loop": session.get_command_loop_info(),
                }

            before_snapshot = self._describe_session(session)
            was_busy = session.status == TabStatus.BUSY
            termination_state = session.begin_termination(expected_task)
            if not termination_state.get("ok"):
                # A second terminate request can race with the first one while
                # the old owner is still unwinding.  That is an idempotent
                # in-progress result, not a failed termination.
                if termination_state.get("error") == "termination_in_progress":
                    return {
                        "ok": True,
                        "tab_index": persistent_index,
                        "tab_id": session.id,
                        "was_busy": was_busy,
                        "task_id": str(
                            termination_state.get("current_task_id") or current_task
                        ).strip(),
                        "cancelled": False,
                        "status": session.status.value,
                        "pending": True,
                        "released": False,
                        "interrupt_success": False,
                        "already_in_progress": True,
                        "reason": reason,
                        "scope": normalized_scope,
                    }
                return {
                    "ok": False,
                    **termination_state,
                    "tab_index": persistent_index,
                    "tab_id": session.id,
                    "status": session.status.value,
                    "reason": reason,
                }
            task_id = str(termination_state.get("task_id") or "").strip()
            command_request_id = str(
                termination_state.get("command_request_id") or ""
            ).strip()
            command_loop_request_id = str(
                termination_state.get("command_loop_request_id") or ""
            ).strip()

        cancelled = False
        cancel_error = ""

        request_ids_to_cancel = []
        for request_id in (task_id, command_request_id, command_loop_request_id):
            if request_id and request_id not in request_ids_to_cancel:
                request_ids_to_cancel.append(request_id)

        if request_ids_to_cancel:
            try:
                from app.services.request_manager import request_manager
                for request_id in request_ids_to_cancel:
                    cancelled = bool(request_manager.cancel_request(request_id, reason)) or cancelled
            except Exception as e:
                cancel_error = str(e)
                logger.debug(f"[{session.id}] 取消任务失败（忽略）: {e}")

        self._stop_global_monitor_for_session(session.id, reason=f"terminate:{reason}", wait=True)
        interrupt_success = session.interrupt_for_termination(clear_page=clear_page)

        with self._condition:
            wait_deadline = time.monotonic() + self.TERMINATION_RELEASE_WAIT_SEC
            while (
                self._tabs.get(session.id) is session
                and session.is_termination_in_progress()
            ):
                remaining = wait_deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=min(remaining, 0.1))

            pending = bool(
                self._tabs.get(session.id) is session
                and session.is_termination_in_progress()
            )
            if not pending and self._tabs.get(session.id) is session and session.status == TabStatus.IDLE:
                self._start_global_monitor_for_session(session)

            self._condition.notify_all()

            logger.warning(
                f"[{session.id}] 手动终止: idx=#{persistent_index}, "
                f"task={task_id or '-'}, cancelled={cancelled}, "
                f"pending={pending}, interrupt_success={interrupt_success}, "
                f"status={session.status.value}, reason={reason}, "
                f"before={before_snapshot}, after={self._describe_session(session)}"
            )

            result = {
                "ok": True,
                "tab_index": persistent_index,
                "tab_id": session.id,
                "was_busy": was_busy,
                "task_id": task_id,
                "cancelled": cancelled,
                "status": session.status.value,
                "pending": pending,
                "released": not pending and session.status == TabStatus.IDLE,
                "interrupt_success": interrupt_success,
                "reason": reason,
                "scope": normalized_scope,
            }
            if cancel_error:
                result["cancel_error"] = cancel_error
            return result

    def get_tabs_with_index(self) -> List[Dict]:
        """获取所有标签页及其持久编号（供 API 调用）"""
        with self._lock:
            if self._should_scan_for_query():
                self._scan_new_tabs()

            sessions = list(self._tabs.values())
            groups_by_session: Dict[str, List[str]] = {}
            for group in normalize_route_groups(getattr(self, "route_groups", [])):
                for member_session in self._get_sessions_for_route_group(group["id"], bind=False):
                    groups_by_session.setdefault(member_session.id, []).append(group["id"])

        result = []
        for session in sessions:
            info = session.get_info(use_cached_url=True)
            tab_route_prefix = f"/tab/{session.persistent_index}"
            route_domain = str(info.get("route_domain") or "").strip()
            domain_route_prefix = f"/url/{route_domain}" if route_domain else ""
            preset_route_domain = str(info.get("current_domain") or route_domain).strip()
            url_route_token = str(info.get("url_route_token") or "").strip()
            exact_url_route_prefix = f"/tab-url/{url_route_token}" if url_route_token else ""
            info["tab_route_prefix"] = tab_route_prefix
            info["domain_route_prefix"] = domain_route_prefix
            info["preset_route_domain"] = preset_route_domain
            info["preset_domain_route_prefix"] = f"/url/{preset_route_domain}" if preset_route_domain else ""
            info["exact_url_route_prefix"] = exact_url_route_prefix
            info["route_prefix"] = domain_route_prefix or tab_route_prefix
            info["route_groups"] = groups_by_session.get(session.id, [])
            self._apply_exposed_model_name(info)
            result.append(info)

        # 按编号排序
        result.sort(key=lambda x: x.get("persistent_index", 0))
        return result

    # ================= 预设管理 =================

    def set_tab_preset(self, persistent_index: int, preset_name: str) -> bool:
        """
        为指定标签页设置预设

        Args:
            persistent_index: 标签页持久化编号
            preset_name: 预设名称（None 或空字符串表示恢复为跟随站点默认预设）

        Returns:
            是否成功
        """
        with self._lock:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                logger.warning(f"标签页 #{persistent_index} 不存在")
                return False

            session = self._tabs.get(session_id)
            if not session:
                logger.warning(f"标签页 {session_id} 已被移除")
                return False

            old_preset = session.preset_name
            session.preset_name = preset_name if preset_name else None
            if old_preset != session.preset_name:
                session.reset_conversation_state()

            logger.debug(
                f"[{session.id}] 预设切换: "
                f"'{old_preset or '跟随站点默认预设'}' → '{preset_name or '跟随站点默认预设'}'"
            )
            return True

    def get_tab_preset(self, persistent_index: int) -> Optional[str]:
        """获取指定标签页的当前预设名称"""
        with self._lock:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                return None

            session = self._tabs.get(session_id)
            if not session:
                return None

            return session.preset_name

    # ================= 暴露模型名管理 =================

    def set_tab_model_name(self, persistent_index: int, model_name: Optional[str]) -> Dict[str, Any]:
        """为指定标签页设置临时暴露模型名。空值表示恢复到持久化/默认规则。"""
        normalized_name = self._normalize_model_name(model_name)
        with self._lock:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}

            session = self._tabs.get(session_id)
            if not session:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}

            session.model_name_override = normalized_name or None
            info = session.get_info(use_cached_url=True)
            tab_route_prefix = f"/tab/{session.persistent_index}"
            route_domain = str(info.get("route_domain") or "").strip()
            domain_route_prefix = f"/url/{route_domain}" if route_domain else ""
            preset_route_domain = str(info.get("current_domain") or route_domain).strip()
            url_route_token = str(info.get("url_route_token") or "").strip()
            info["tab_route_prefix"] = tab_route_prefix
            info["domain_route_prefix"] = domain_route_prefix
            info["preset_route_domain"] = preset_route_domain
            info["preset_domain_route_prefix"] = f"/url/{preset_route_domain}" if preset_route_domain else ""
            info["exact_url_route_prefix"] = f"/tab-url/{url_route_token}" if url_route_token else ""
            info["route_prefix"] = domain_route_prefix or tab_route_prefix
            self._apply_exposed_model_name(info)

            return {
                "ok": True,
                "tab_index": persistent_index,
                "tab": info,
            }

    # ================= 状态查询 =================

    def get_status(self) -> Dict:
        with self._lock:
            sessions = list(self._tabs.values())
            total = len(sessions)
            idle = sum(1 for s in sessions if s.status == TabStatus.IDLE)
            busy = sum(1 for s in sessions if s.status == TabStatus.BUSY)
            max_tabs = self.max_tabs
            min_tabs = self.min_tabs
            idle_timeout = self.idle_timeout
            acquire_timeout = self.acquire_timeout
            stuck_timeout = self.stuck_timeout
            allocation_mode = self.allocation_mode
            excluded_urls = list(self.excluded_urls)
            preserve_error_tabs = self.preserve_error_tabs
            route_groups = normalize_route_groups(getattr(self, "route_groups", []))
            global_network_enabled = self._global_network_enabled
            known_raw_tabs = len(self._known_tab_ids)
            last_scan = round(time.time() - self._last_scan_time, 1)

        tabs_info = [s.get_info(use_cached_url=True) for s in sessions]

        return {
            "total": total,
            "idle": idle,
            "busy": busy,
            "max_tabs": max_tabs,
            "min_tabs": min_tabs,
            "idle_timeout": idle_timeout,
            "acquire_timeout": acquire_timeout,
            "stuck_timeout": stuck_timeout,
            "allocation_mode": allocation_mode,
            "excluded_urls": excluded_urls,
            "preserve_error_tabs": preserve_error_tabs,
            "route_groups": route_groups,
            "global_network_enabled": global_network_enabled,
            "known_raw_tabs": known_raw_tabs,
            "last_scan": last_scan,
            "tabs": tabs_info
        }

    def get_watchdog_summary(self, limit: int = 4) -> Dict[str, Any]:
        """Return the lightweight tab pool fields needed by BrowserWatchdog logs."""
        try:
            max_items = max(0, int(limit))
        except Exception:
            max_items = 4

        with self._lock:
            sessions = list(self._tabs.values())
            total = len(sessions)
            idle = sum(1 for s in sessions if s.status == TabStatus.IDLE)
            busy = sum(1 for s in sessions if s.status == TabStatus.BUSY)
            preview = sessions[:max_items]

        tabs = []
        for session in preview:
            status = getattr(getattr(session, "status", None), "value", None)
            tabs.append({
                "id": getattr(session, "id", None),
                "status": status or str(getattr(session, "status", "") or "?"),
                "is_isolated_context": bool(getattr(session, "is_isolated_context", False)),
            })

        return {
            "total": total,
            "idle": idle,
            "busy": busy,
            "tabs": tabs,
        }

    def get_idle_sessions_snapshot(self) -> List[TabSession]:
        """Return a shallow snapshot of currently idle tab sessions."""
        with self._lock:
            return [s for s in self._tabs.values() if s.status == TabStatus.IDLE]

    def get_sessions_snapshot(self) -> List[TabSession]:
        """Return a shallow snapshot of all current tab sessions."""
        with self._lock:
            return list(self._tabs.values())

    def shutdown(self):
        monitor = None
        maintenance_executor = None
        with self._lock:
            self._shutdown = True
            self._condition.notify_all()
            monitor = self._global_network_monitor
            self._global_network_monitor = None
            maintenance_executor = self._maintenance_executor
            self._maintenance_executor = None
        # acquire 专用线程池随池一起关闭（不等待：阻塞中的 acquire 线程会因
        # _shutdown + notify_all 自行退出）。
        with self._acquire_executor_lock:
            acquire_executor = self._acquire_executor
            self._acquire_executor = None
        with self._recovery_service_lock:
            recovery_service = self._recovery_service
            self._recovery_service = None

        if monitor:
            monitor.shutdown()
        if maintenance_executor:
            maintenance_executor.shutdown(wait=False, cancel_futures=True)
        if acquire_executor:
            acquire_executor.shutdown(wait=False, cancel_futures=True)
        if recovery_service:
            # 内部同样以 wait=False, cancel_futures=True 关闭其线程池
            recovery_service.shutdown()

        with self._lock:
            self._tabs.clear()
            self._known_tab_ids.clear()
            self._quarantined_raw_ids.clear()
            self._active_session_id = None  # 🆕 重置活动标签页记录
            recovery_lock = getattr(self, "_context_recovery_lock", None)
            if recovery_lock is not None:
                with recovery_lock:
                    getattr(self, "_context_recovery_pending", set()).clear()
                    getattr(self, "_context_recovery_requested_at", {}).clear()
            # 🆕 清理编号映射
            self._raw_id_to_persistent.clear()
            self._persistent_to_session_id.clear()
            self._isolated_context_by_raw_id.clear()
            self._orphaned_isolated_contexts.clear()
            self._round_robin_cursor = 0
            self._route_round_robin_cursor.clear()
            self._next_persistent_index = 1
            logger.info("TabPoolManager 已关闭")
