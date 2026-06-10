import asyncio
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
    extract_remote_site_domain,
    normalize_route_domain,
    route_domain_matches,
    tab_url_matches,
)

from ._utils import _looks_like_transient_local_debug_error, _should_skip_pool_url
from .network import _GlobalNetworkInterceptionManager
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
    ):
        self.page = browser_page
        self.max_tabs = max_tabs
        self.min_tabs = min_tabs
        self.idle_timeout = idle_timeout
        self.acquire_timeout = acquire_timeout
        self.stuck_timeout = max(1.0, float(stuck_timeout))
        self.allocation_mode = self._normalize_allocation_mode(allocation_mode)

        self._tabs: Dict[str, TabSession] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._scan_snapshot_lock = threading.Lock()

        self._initialized = False
        self._shutdown = False
        self._tab_counter = 0

        self._last_scan_time: float = 0
        self._get_tabs_retry_after: float = 0.0
        self._last_get_tabs_warning_at: float = 0.0

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
        self._waiter_counter = 0

        # 🆕 持久化编号系统
        self._next_persistent_index: int = 1  # 下一个可分配的编号
        self._raw_id_to_persistent: Dict[str, int] = {}  # raw_tab_id → persistent_index
        self._persistent_to_session_id: Dict[int, str] = {}  # persistent_index → session.id
        self._isolated_context_by_raw_id: Dict[str, str] = {}
        self._orphaned_isolated_contexts: Dict[str, float] = {}
        self._round_robin_cursor: int = 0
        self._route_round_robin_cursor: OrderedDict[str, int] = OrderedDict()
        self._maintenance_executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
            max_workers=self.MAINTENANCE_WORKER_LIMIT,
            thread_name_prefix="tab-maint",
        )

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
            f"allocation_mode={self.allocation_mode})"
        )

    def apply_runtime_config(
        self,
        *,
        max_tabs: Optional[int] = None,
        min_tabs: Optional[int] = None,
        idle_timeout: Optional[float] = None,
        acquire_timeout: Optional[float] = None,
        stuck_timeout: Optional[float] = None,
        allocation_mode: Optional[str] = None,
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

            updated = {
                "max_tabs": self.max_tabs,
                "min_tabs": self.min_tabs,
                "idle_timeout": self.idle_timeout,
                "acquire_timeout": self.acquire_timeout,
                "stuck_timeout": self.stuck_timeout,
                "allocation_mode": self.allocation_mode,
            }

            logger.info(
                "[TabPool] 运行时配置已更新: "
                f"max_tabs={self.max_tabs}, min_tabs={self.min_tabs}, "
                f"idle_timeout={self.idle_timeout}, acquire_timeout={self.acquire_timeout}, "
                f"stuck_timeout={self.stuck_timeout}, allocation_mode={self.allocation_mode}"
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

    def _snapshot_current_tab_targets(self) -> tuple[List[Any], List[str], Dict[str, str]]:
        current_tabs = self._list_current_tab_refs()
        current_tab_ids = [
            raw_id for raw_id in
            (self._get_tab_ref_id(tab_ref) for tab_ref in current_tabs)
            if raw_id
        ]

        current_context_by_raw: Dict[str, str] = {}
        with self._lock:
            cached_contexts = dict(self._isolated_context_by_raw_id)
        for raw_id in current_tab_ids:
            cached_context = str(cached_contexts.get(raw_id) or "").strip()
            if cached_context:
                current_context_by_raw[raw_id] = cached_context
                continue
            try:
                info = self._get_target_info(raw_id)
                context_id = str(info.get("browserContextId") or "").strip()
                if context_id:
                    current_context_by_raw[raw_id] = context_id
            except Exception as e:
                logger.debug(f"[TabPool] skip context lookup for {raw_id}: {e}")

        return current_tabs, current_tab_ids, current_context_by_raw

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

    def _close_raw_tab(self, raw_tab_id: str) -> bool:
        if not raw_tab_id:
            return False

        try:
            browser = self._get_browser_handle()
            if browser is None or not hasattr(browser, "_run_cdp"):
                return False

            # 🛡️ 安全防御：检查当前 Page 类型的 Tab 数量，防止关闭最后一个 Tab 导致整个浏览器退出
            try:
                targets_resp = browser._run_cdp("Target.getTargets") or {}
                all_targets = targets_resp.get("targetInfos") or []
                page_targets = [t for t in all_targets if str(t.get("type") or "").lower() == "page"]

                # 如果要关闭的 raw_tab_id 在页面列表中，且当前总页面数 <= 1，先新建一个空白页做缓冲
                if len(page_targets) <= 1 and any(t.get("targetId") == raw_tab_id for t in page_targets):
                    logger.warning(
                        f"[TabPool] 尝试关闭唯一的标签页 {raw_tab_id}，"
                        f"将新建一个空白标签页以防止浏览器进程自动退出"
                    )
                    try:
                        browser._run_cdp("Target.createTarget", url="about:blank")
                    except Exception as ce:
                        logger.error(f"[TabPool] 新建兜底标签页失败: {ce}")
            except Exception as e:
                logger.debug(f"[TabPool] 检查标签页数量失败（忽略并继续关闭）: {e}")

            browser._run_cdp("Target.closeTarget", targetId=raw_tab_id)
            return True
        except Exception as e:
            logger.debug(f"[TabPool] close raw tab failed ({raw_tab_id}): {e}")
            return False

    def _close_raw_tabs_async(self, raw_tab_ids: List[str], reason: str = "") -> None:
        targets = [str(raw_id or "").strip() for raw_id in raw_tab_ids or [] if str(raw_id or "").strip()]
        if not targets:
            return

        def _close_targets():
            for raw_id in targets:
                closed = self._close_raw_tab(raw_id)
                logger.debug(
                    f"[TabPool] close removed target raw={raw_id} "
                    f"closed={closed} reason={reason or '-'}"
                )

        executor = self._maintenance_executor
        if executor is None:
            return
        try:
            executor.submit(_close_targets)
        except RuntimeError as e:
            logger.debug(f"[TabPool] maintenance submit failed (close:{targets[0][:8]}): {e}")

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
        return True

    def _get_browser_handle(self):
        browser = getattr(self.page, "browser", None)
        if browser is not None:
            return browser
        return self.page

    def _list_current_tab_ids_via_cdp(self) -> List[str]:
        browser = self._get_browser_handle()
        if browser is None or not hasattr(browser, "_run_cdp"):
            return []

        try:
            result = browser._run_cdp("Target.getTargets") or {}
            target_infos = result.get("targetInfos") or []
        except Exception as e:
            logger.debug(f"[TabPool] Target.getTargets 失败: {e}")
            return []

        tab_ids: List[str] = []
        for info in target_infos:
            if not isinstance(info, dict):
                continue
            if str(info.get("type") or "").strip().lower() != "page":
                continue
            target_id = str(info.get("targetId") or "").strip()
            if target_id:
                tab_ids.append(target_id)
        return tab_ids

    def _log_get_tabs_warning(self, message: str) -> None:
        now = time.time()
        if now - self._last_get_tabs_warning_at < self.GET_TABS_WARNING_INTERVAL_SEC:
            return
        self._last_get_tabs_warning_at = now
        logger.warning(message)

    def _list_current_tab_refs(self) -> List[Any]:
        browser = self._get_browser_handle()
        if browser is None:
            return []

        cdp_tab_ids = self._list_current_tab_ids_via_cdp()
        if cdp_tab_ids:
            self._get_tabs_retry_after = 0.0
            return cdp_tab_ids

        now = time.time()
        if now < self._get_tabs_retry_after:
            return []

        try:
            tabs = browser.get_tabs()
            self._get_tabs_retry_after = 0.0
            return list(tabs or [])
        except Exception as e:
            self._get_tabs_retry_after = max(
                self._get_tabs_retry_after,
                time.time() + self.GET_TABS_FAILURE_COOLDOWN_SEC,
            )
            cdp_tab_ids = self._list_current_tab_ids_via_cdp()
            if cdp_tab_ids:
                message = f"[TabPool] browser.get_tabs() 失败，回退到 Target.getTargets 扫描: {e}"
                if _looks_like_transient_local_debug_error(e):
                    logger.debug(message)
                else:
                    self._log_get_tabs_warning(message)
                return cdp_tab_ids
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

        created = self._create_isolated_tab(final_url, background=False)
        if not created:
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "is_isolated_context": False,
                "url": final_url,
            }

        if not self._close_raw_tab(raw_tab_id):
            created_raw_id = created.get("raw_tab_id")
            self._close_raw_tab(created_raw_id)
            self._dispose_browser_context(created.get("browser_context_id"))
            self._isolated_context_by_raw_id.pop(created_raw_id, None)
            logger.warning(f"[TabPool] fallback to shared tab because original tab could not be closed: {raw_tab_id}")
            return {
                "tab": tab,
                "raw_tab_id": raw_tab_id,
                "browser_context_id": browser_context_id,
                "is_isolated_context": False,
                "url": final_url,
            }

        logger.info(f"[TabPool] auto converted tab to isolated cookie context: {domain or final_url}")
        return {
            "tab": created["tab"],
            "raw_tab_id": created["raw_tab_id"],
            "browser_context_id": created.get("browser_context_id"),
            "is_isolated_context": True,
            "url": created.get("url") or final_url,
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
                self._close_raw_tab(created.get("raw_tab_id"))
                self._dispose_browser_context(created.get("browser_context_id"))
                self._isolated_context_by_raw_id.pop(created.get("raw_tab_id"), None)
                return {"ok": False, "error": "tab_pool_full"}

            session = self._wrap_tab(
                created["tab"],
                created["raw_tab_id"],
                browser_context_id=created.get("browser_context_id"),
                is_isolated_context=True,
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
                self._close_raw_tab(created.get("raw_tab_id"))
                return {"ok": False, "error": "tab_pool_full"}

            session = self._wrap_tab(
                created["tab"],
                created["raw_tab_id"],
                browser_context_id=created.get("browser_context_id"),
                is_isolated_context=False,
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
        if self._shutdown:
            return False
        # 仅在空闲标签页常驻监听，任务执行时让位
        if session.status != TabStatus.IDLE:
            return False
        return bool(self._global_network_monitor.start_for_session(session))

    def _stop_global_monitor_for_session(self, session_id: str, reason: str = "", wait: bool = False) -> bool:
        if not self._global_network_monitor:
            return True
        return bool(self._global_network_monitor.stop_for_session(session_id, reason=reason, join=wait))

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

        tab_id = getattr(tab_ref, "tab_id", None)
        if tab_id:
            return str(tab_id)

        raw = str(tab_ref or "").strip()
        return raw or None

    def _resolve_tab_from_ref(self, tab_ref: Any) -> Optional[Any]:
        if tab_ref is None:
            return None

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
        except Exception:
            return None

    def _wrap_tab(
        self,
        tab,
        raw_tab_id: str = None,
        *,
        browser_context_id: Optional[str] = None,
        is_isolated_context: bool = False,
    ) -> TabSession:
        self._tab_counter += 1

        url = ""
        try:
            url = tab.url or ""
        except:
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

    def _should_scan(self) -> bool:
        """检查是否需要扫描新标签页"""
        return time.time() - self._last_scan_time >= self.SCAN_INTERVAL

    def _should_scan_for_query(self) -> bool:
        """读接口节流扫描，避免高频查询放大浏览器探测压力。"""
        if not self._tabs:
            return True
        return time.time() - self._last_scan_time >= self.QUERY_SCAN_MIN_INTERVAL_SEC

    def _scan_new_tabs(self):
        """扫描并添加新标签页（已持有锁）"""
        try:
            # 先占住本次扫描窗口，避免唤醒后多个等待线程重复扫描。
            self._last_scan_time = time.time()
            self._condition.release()
            try:
                with self._scan_snapshot_lock:
                    current_tabs, current_tab_ids, current_context_by_raw = (
                        self._snapshot_current_tab_targets()
                    )
            finally:
                self._condition.acquire()

            if self._shutdown:
                return

            current_tab_set = set(current_tab_ids)
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
                        logger.warning(
                            f"[{session_id}] global monitor not restarted after rebind "
                            f"until previous worker finishes stopping"
                        )
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
                    logger.warning(f"[{session_id}] 标签页已关闭但仍在忙碌，标记为错误")
                    session.mark_error("标签页已被关闭")
                    self._detach_global_monitor_for_session(session_id, reason="tab_closed")
                else:
                    logger.info(f"[{session_id}] 标签页已关闭，从池中移除")
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

                try:
                    tab = self._resolve_tab_from_ref(tab_ref)
                    if not tab:
                        continue

                    url = ""
                    try:
                        url = tab.url or ""
                    except Exception:
                        pass

                    # 本地页、浏览器内部页、空白页都不纳入标签页池。
                    if _should_skip_pool_url(url):
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

                        tab = self._resolve_tab_from_ref(tab_ref)
                        if not tab:
                            continue

                        url = ""
                        try:
                            url = tab.url or ""
                        except Exception:
                            pass

                        # 初始化时直接跳过本地页和浏览器内部页。
                        if _should_skip_pool_url(url):
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
                busy_duration = now - session.last_used_at

                if busy_duration > self.stuck_timeout:
                    task_id = session.current_task_id or ""
                    cancel_submitted = self._cancel_active_request_for_session(
                        session,
                        "stuck_timeout",
                        detail=f"busy_duration={busy_duration:.0f}s",
                    )
                    snapshot = self._describe_session(session)
                    logger.warning(
                        f"[{session.id}] stuck for {busy_duration:.0f}s, retire session "
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
            return changed

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

        task_id = str(getattr(session, "current_task_id", "") or "").strip()
        if not task_id:
            return False

        if getattr(session, "_last_cancel_request_task_id", None) == task_id:
            logger.debug(
                f"[{session.id}] duplicate cancel skipped "
                f"(task={task_id}, reason={reason}, "
                f"previous_reason={getattr(session, '_last_cancel_request_reason', '-')}, "
                f"detail={detail or '-'})"
            )
            return False

        try:
            setattr(session, "_workflow_stop_reason", reason)
            setattr(session, "_last_cancel_request_task_id", task_id)
            setattr(session, "_last_cancel_request_reason", reason)
        except Exception:
            pass

        cancel_submitted = self._submit_request_cancel(
            task_id,
            reason,
            session_id=session.id,
            detail=detail,
        )

        logger.warning(
            f"[{session.id}] 会话失效，已请求取消任务 "
            f"(task={task_id}, reason={reason}, cancel_submitted={cancel_submitted}, detail={detail or '-'})"
        )
        return cancel_submitted

    def _cleanup_unhealthy_tabs(self):
        """清理不健康的空闲标签页和错误状态的标签页"""
        to_remove = []

        for tab_id, session in list(self._tabs.items()):
            try:
                if session.is_isolated_context and session.is_healthy(allow_live_check=False):
                    session.clear_transient_disconnect()

                # 清理 ERROR 状态的标签页（包括强制释放失败的）
                if session.status == TabStatus.ERROR:
                    to_remove.append(tab_id)
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
                    to_remove.append(tab_id)
            except Exception as e:
                logger.warning(f"[TabPool] cleanup check failed for tab {tab_id}: {e}")
                to_remove.append(tab_id)

        for tab_id in to_remove:
            try:
                session = self._tabs.get(tab_id)
                if not session:
                    continue
                self._cancel_active_request_for_session(
                    session,
                    "tab_unhealthy",
                    detail=f"status={getattr(getattr(session, 'status', None), 'value', 'unknown')}",
                )
                logger.warning(f"[{tab_id}] 不健康或错误状态，从池中移除")
                self._detach_global_monitor_for_session(tab_id, reason="unhealthy")

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

                # 清理持久编号映射
                p_idx = session.persistent_index
                if p_idx and self._persistent_to_session_id.get(p_idx) == tab_id:
                    self._persistent_to_session_id.pop(p_idx, None)

                # 清理活动标签页记录
                if self._active_session_id == tab_id:
                    self._active_session_id = None

                self._tabs.pop(tab_id, None)
                self._on_session_removed(tab_id)
                self._close_raw_tabs_async(raw_ids_to_remove, reason="unhealthy")
            except Exception as e:
                logger.warning(f"[TabPool] cleanup remove failed for tab {tab_id}: {e}")

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

        if owner_map is not None and owner_key is not None and not waiters:
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
        return await asyncio.to_thread(self.acquire, task_id, timeout)

    def release(
        self,
        tab_id: str,
        clear_page: bool = False,
        check_triggers: bool = True,
        rollback_request_count: bool = False,
        expected_task_id: str = "",
    ):
        """释放标签页"""
        with self._condition:
            session = self._tabs.get(tab_id)
            if not session:
                return

            before_snapshot = self._describe_session(session)
            expected_task = str(expected_task_id or "").strip()
            current_task = str(getattr(session, "current_task_id", "") or "").strip()
            session_status = getattr(getattr(session, "status", None), "value", "")
            if expected_task:
                if current_task and current_task != expected_task:
                    logger.warning(
                        f"[{tab_id}] 跳过释放：标签页已被其他任务接管 "
                        f"(expected_task={expected_task}, current_task={current_task}, "
                        f"snapshot={before_snapshot})"
                    )
                    return
                if not current_task:
                    logger.warning(
                        f"[{tab_id}] 跳过释放：标签页 task_id 已丢失 "
                        f"(expected_task={expected_task}, status={session_status or 'unknown'}, "
                        f"snapshot={before_snapshot})"
                    )
                    return

            release_state = session._begin_release_state(
                clear_page=clear_page,
                rollback_request_count=rollback_request_count,
                force=False,
            )
            if release_state is None:
                self._condition.notify_all()
                return

        session._run_release_from_state(
            release_state,
            clear_page=clear_page,
            check_triggers=check_triggers,
            rollback_request_count=rollback_request_count,
        )

        with self._condition:
            if self._tabs.get(tab_id) is session:
                self._start_global_monitor_for_session(session)
                self._condition.notify_all()
                logger.debug(
                    f"[{tab_id}] 已释放 "
                    f"(expected_task={expected_task or '-'}, clear_page={clear_page}, "
                    f"check_triggers={check_triggers}, rollback_request_count={rollback_request_count}, "
                    f"before={before_snapshot}, after={self._describe_session(session)})"
                )

    def force_release_all(self):
        """强制释放所有标签页（调试用）"""
        with self._condition:
            pending: List[tuple[TabSession, Dict[str, Any]]] = []
            for session in self._tabs.values():
                if session.status == TabStatus.BUSY:
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
            self.release(session.id)

    def acquire_by_raw_tab_id(
        self,
        raw_tab_id: str,
        task_id: str,
        timeout: float = None,
        count_request: bool = True
    ) -> Optional[TabSession]:
        """
        根据底层浏览器标签页 ID 获取指定标签页会话。

        这用于外部已经明确知道目标标签页时，复用 TabPool 的运行时上下文，
        保持与正常工作流执行一致的监听、占用与释放行为。
        """
        raw_tab_id = str(raw_tab_id or "").strip()
        if not raw_tab_id:
            return None

        timeout = timeout or self.acquire_timeout
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
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
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
        return await asyncio.to_thread(self.acquire_by_index, persistent_index, task_id, timeout)

    def _get_sessions_for_route_domain(self, route_domain: str) -> List[TabSession]:
        target = normalize_route_domain(route_domain)
        if not target:
            return []

        matches: List[TabSession] = []
        for session in self._tabs.values():
            _cached_url, actual_domain = session.get_cached_route_snapshot()
            if route_domain_matches(target, actual_domain):
                matches.append(session)

        return matches

    def _refresh_route_snapshots_for_sessions(self, sessions: List[TabSession]) -> None:
        """Refresh live tab URL/domain snapshots; callers must not hold self._condition."""
        for session in sessions:
            try:
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
        return await asyncio.to_thread(
            self.acquire_by_route_domain,
            route_domain,
            task_id,
            timeout,
            allocation_mode,
        )




    def acquire(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """ASCII-safe fair acquire for generic request routing."""
        timeout = timeout or self.acquire_timeout
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

                    session = self._try_acquire_session_for_request(
                        self._tabs.values(),
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
        target = str(exact_url or "").strip()
        if not target:
            logger.warning("Exact tab URL is empty; cannot acquire a tab")
            return None

        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout
        refreshed_live_snapshots = False

        with self._condition:
            waiters = self._route_waiters.setdefault(f"url::{target}", deque())
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

                    if not refreshed_live_snapshots:
                        refreshed_live_snapshots = True
                        self._refresh_route_snapshots_unlocked_once()

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

                    matching_sessions = self._get_sessions_for_exact_url(target)
                    if not matching_sessions:
                        logger.warning(f"No tab matches exact URL '{target}'")
                        return None
                    session = self._try_acquire_session_for_request(
                        matching_sessions,
                        task_id,
                        route_domain=f"url::{target}",
                        defer_context="exact-url acquire",
                    )
                    if session is not None:
                        self._unregister_waiter(
                            waiters,
                            waiter_token,
                            owner_map=self._route_waiters,
                            owner_key=f"url::{target}",
                        )
                        if not self._complete_acquired_session_for_return(
                            session,
                            "acquire_by_exact_url",
                            task_id,
                            activate=self._auto_activate_on_acquire and session.id != self._active_session_id,
                        ):
                            return None
                        self._mark_allocation_cursor(session, route_domain=f"url::{target}")

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
                    owner_key=f"url::{target}",
                )

    def acquire_by_index(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """ASCII-safe fair acquire for a fixed persistent tab index."""
        timeout = timeout or self.acquire_timeout
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

        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout
        refreshed_live_snapshots = False

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

                    if not refreshed_live_snapshots:
                        refreshed_live_snapshots = True
                        self._refresh_route_snapshots_unlocked_once()

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

    def terminate_by_index(
        self,
        persistent_index: int,
        reason: str = "manual_terminate",
        clear_page: bool = True,
    ) -> Dict[str, Any]:
        """
        按标签页编号终止当前任务并释放占用。

        行为：
        1) 尝试取消该标签页 current_task 对应的请求；
        2) 若标签页忙碌，执行 force_release()；
        3) 若标签页空闲且 clear_page=True，重置到 about:blank；
        4) 成功空闲后恢复全局网络监听。
        """
        with self._condition:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}

            session = self._tabs.get(session_id)
            if not session:
                return {"ok": False, "error": "tab_not_found", "tab_index": persistent_index}

            before_snapshot = self._describe_session(session)
            task_id = session.current_task_id or ""
            was_busy = session.status == TabStatus.BUSY
            use_force_release = bool(was_busy or clear_page)
            release_state = session._begin_release_state(
                clear_page=clear_page,
                force=use_force_release,
            )
            if release_state is None:
                self._condition.notify_all()
                return {
                    "ok": False,
                    "error": "release_in_progress",
                    "tab_index": persistent_index,
                    "tab_id": session.id,
                    "status": session.status.value,
                    "reason": reason,
                }

        cancelled = False
        cancel_error = ""

        if task_id:
            try:
                from app.services.request_manager import request_manager
                cancelled = bool(request_manager.cancel_request(task_id, reason))
            except Exception as e:
                cancel_error = str(e)
                logger.debug(f"[{session.id}] 取消任务失败（忽略）: {e}")

        self._stop_global_monitor_for_session(session.id, reason=f"terminate:{reason}", wait=True)

        if use_force_release:
            session._run_force_release_from_state(
                release_state,
                clear_page=clear_page,
                check_triggers=False,
            )
        else:
            session._run_release_from_state(
                release_state,
                clear_page=False,
                check_triggers=False,
                rollback_request_count=False,
            )

        with self._condition:
            # 尽量恢复可用状态的全局监听
            if self._tabs.get(session.id) is session and session.status == TabStatus.IDLE:
                self._start_global_monitor_for_session(session)

            self._condition.notify_all()

            logger.warning(
                f"[{session.id}] 手动终止: idx=#{persistent_index}, "
                f"task={task_id or '-'}, cancelled={cancelled}, "
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
                "reason": reason,
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

        result = []
        for session in sessions:
            info = session.get_info(allow_live_when_busy=True)
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

    def shutdown(self, close_browser_tabs: bool = True):
        monitor = None
        context_ids = set()
        maintenance_executor = None
        shared_raw_tab_ids: List[str] = []
        with self._lock:
            self._shutdown = True
            self._condition.notify_all()
            monitor = self._global_network_monitor
            context_ids = set(self._isolated_context_by_raw_id.values())
            self._global_network_monitor = None
            maintenance_executor = self._maintenance_executor
            self._maintenance_executor = None
            raw_id_to_persistent = dict(self._raw_id_to_persistent)
            persistent_to_session_id = dict(self._persistent_to_session_id)
            sessions_by_id = dict(self._tabs)
            for raw_id, persistent_idx in raw_id_to_persistent.items():
                session_id = persistent_to_session_id.get(persistent_idx)
                session = sessions_by_id.get(session_id) if session_id else None
                if not close_browser_tabs:
                    continue
                if session is None or session.is_isolated_context:
                    continue
                normalized_raw_id = str(raw_id or "").strip()
                if normalized_raw_id:
                    shared_raw_tab_ids.append(normalized_raw_id)

        if monitor:
            monitor.shutdown()
        if maintenance_executor:
            maintenance_executor.shutdown(wait=False, cancel_futures=True)

        if close_browser_tabs:
            for raw_tab_id in shared_raw_tab_ids:
                self._close_raw_tab(raw_tab_id)

            for context_id in context_ids:
                self._dispose_browser_context(context_id)

        with self._lock:
            self._tabs.clear()
            self._known_tab_ids.clear()
            self._active_session_id = None  # 🆕 重置活动标签页记录
            # 🆕 清理编号映射
            self._raw_id_to_persistent.clear()
            self._persistent_to_session_id.clear()
            self._isolated_context_by_raw_id.clear()
            self._orphaned_isolated_contexts.clear()
            self._round_robin_cursor = 0
            self._route_round_robin_cursor.clear()
            self._next_persistent_index = 1
            logger.info("TabPoolManager 已关闭")
