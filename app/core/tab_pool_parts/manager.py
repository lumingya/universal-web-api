"""标签页池管理器。

R2-3：原 4500 行的单个类按职责拆成 mixin（pool_*.py），这里只保留类属性、__init__ 与 shutdown；
对外接口（TabPoolManager 的全部方法与属性）保持不变。
"""

import os
import threading
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app.core.config import BrowserConstants, logger
from app.utils.tab_route_groups import normalize_route_groups

from .idle_maintenance import IdleMaintenanceConfig
from .network import _GlobalNetworkInterceptionManager
from .recovery import TabQuarantineEntry, TabRecoveryService
from .session import TabSession
from .pool_config import TabPoolConfigMixin
from .pool_contexts import TabPoolContextMixin
from .pool_tabs import TabPoolTabsMixin
from .pool_sessions import TabPoolSessionsMixin
from .pool_allocation import TabPoolAllocationMixin
from .pool_maintenance import TabPoolMaintenanceMixin
from .pool_acquire import TabPoolAcquireMixin
from .pool_routing import TabPoolRoutingMixin
from .pool_status import TabPoolStatusMixin


class TabPoolManager(
    TabPoolConfigMixin,
    TabPoolContextMixin,
    TabPoolTabsMixin,
    TabPoolSessionsMixin,
    TabPoolAllocationMixin,
    TabPoolMaintenanceMixin,
    TabPoolAcquireMixin,
    TabPoolRoutingMixin,
    TabPoolStatusMixin,
):
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
    # A cancelled HTTP request must not leave its worker blocked in a 60s
    # acquire.  Polling this frequently keeps cancellation cleanup bounded
    # without adding a second notification channel from RequestManager.
    ACQUIRE_CANCEL_POLL_SEC = 0.25
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
        auto_remember_url_presets: bool = False,
        model_name_overrides: Optional[Dict[str, Any]] = None,
        preset_overrides: Optional[Dict[str, Any]] = None,
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
        self.auto_remember_url_presets = self._to_bool(auto_remember_url_presets, False)
        self.model_name_overrides = self._normalize_model_name_overrides(model_name_overrides)
        self.preset_overrides = self._normalize_preset_overrides(preset_overrides)
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
        # H9：所有 acquire 等待者（通用 / 按编号 / 按域名 / 按路由组）的总量上限，0 = 不限
        try:
            self._max_acquire_waiters = max(0, int(os.getenv("TAB_ACQUIRE_MAX_WAITERS", "128") or 128))
        except ValueError:
            self._max_acquire_waiters = 128
        self._queue_full_rejections: "OrderedDict[str, float]" = OrderedDict()

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
        self._idle_maintenance_config = IdleMaintenanceConfig.from_env()
        self._global_network_monitor: Optional[_GlobalNetworkInterceptionManager] = None
        if self._global_network_enabled:
            self._global_network_monitor = _GlobalNetworkInterceptionManager(
                get_session_fn=self._get_session_for_monitor_snapshot,
                is_shutdown_fn=lambda: self._shutdown,
                listen_pattern=self._global_network_listen_pattern,
                wait_timeout=self._global_network_wait_timeout,
                retry_delay=self._global_network_retry_delay,
                res_types=BrowserConstants.get("GLOBAL_NETWORK_INTERCEPTION_RES_TYPES"),
            )
            try:
                from app.services.arena_tab_listener import register_arena_tab_listener
                register_arena_tab_listener(self._global_network_monitor)
            except Exception as e:
                logger.debug(f"[TabPool] 注册 Arena 扩展监听器失败（忽略）: {e}")

        logger.debug(
            f"TabPoolManager 初始化 (max={max_tabs}, stuck_timeout={self.stuck_timeout}s, "
            f"allocation_mode={self.allocation_mode}, preserve_error_tabs={self.preserve_error_tabs})"
        )
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
