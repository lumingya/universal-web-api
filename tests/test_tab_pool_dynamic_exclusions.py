import threading
import time
from collections import OrderedDict, deque

from app.core.tab_pool_parts.manager import TabPoolManager
from app.core.tab_pool_parts.session import TabStatus
from app.utils.site_url import normalize_exact_tab_url


class FakeSession:
    def __init__(self, session_id, persistent_index, url, domain):
        self.id = session_id
        self.persistent_index = persistent_index
        self.url = url
        self.domain = domain
        self.status = TabStatus.IDLE
        self.current_task_id = ""

    def get_cached_route_snapshot(self):
        return self.url, self.domain

    def is_healthy(self, allow_live_check=False):
        return True

    def acquire(self, task_id):
        self.status = TabStatus.BUSY
        self.current_task_id = task_id
        return True


def _build_manager(sessions, excluded_urls):
    manager = TabPoolManager.__new__(TabPoolManager)
    manager._lock = threading.RLock()
    manager._condition = threading.Condition(manager._lock)
    manager._tabs = {session.id: session for session in sessions}
    manager.excluded_urls = [
        normalize_exact_tab_url(item) or item
        for item in excluded_urls
    ]
    manager.acquire_timeout = 0.05
    manager.allocation_mode = "first_idle"
    manager._shutdown = False
    manager._waiter_counter = 0
    manager._acquire_waiters = deque()
    manager._active_session_id = None
    manager._auto_activate_on_acquire = False
    manager._round_robin_cursor = 0
    manager._route_round_robin_cursor = OrderedDict()
    manager._last_scan_time = time.time()

    manager._check_stuck_tabs = lambda: False
    manager._cleanup_unhealthy_tabs = lambda: None
    manager._complete_acquired_session_for_return = lambda *_args, **_kwargs: True
    manager._should_defer_to_command = lambda *_args, **_kwargs: False
    return manager


def test_dynamic_acquire_skips_excluded_url():
    excluded = FakeSession("arena_1", 1, "https://arena.ai/c/excluded", "arena.ai")
    allowed = FakeSession("arena_2", 2, "https://arena.ai/c/allowed", "arena.ai")
    manager = _build_manager(
        [excluded, allowed],
        ["https://arena.ai/c/excluded"],
    )

    acquired = manager.acquire("req-test", timeout=0.05)

    assert acquired is allowed
    assert excluded.status == TabStatus.IDLE
    assert allowed.current_task_id == "req-test"


def test_route_domain_candidates_skip_excluded_but_exact_url_candidates_keep_it():
    excluded = FakeSession("arena_1", 1, "https://arena.ai/c/excluded", "arena.ai")
    allowed = FakeSession("arena_2", 2, "https://arena.ai/c/allowed", "arena.ai")
    manager = _build_manager(
        [excluded, allowed],
        ["https://arena.ai/c/excluded"],
    )

    assert manager._get_sessions_for_route_domain("arena.ai") == [allowed]
    assert manager._get_sessions_for_exact_url("https://arena.ai/c/excluded") == [excluded]


def test_tab_pool_query_scans_current_cdp_target_urls_without_live_tab_url_reads():
    class FakeTab:
        tab_id = "raw-1"

        @property
        def url(self):
            raise AssertionError("tab.url should not be read when CDP target URL is available")

    class FakeBrowser:
        def __init__(self):
            self.tab = FakeTab()

        def _run_cdp(self, command, **_kwargs):
            assert command == "Target.getTargets"
            return {
                "targetInfos": [
                    {
                        "targetId": "raw-1",
                        "type": "page",
                        "url": "https://chatglm.cn/main/alltoolsdetail",
                        "browserContextId": "ctx-1",
                    }
                ]
            }

        def get_tab(self, raw_tab_id):
            assert raw_tab_id == "raw-1"
            return self.tab

    manager = TabPoolManager.__new__(TabPoolManager)
    manager.page = FakeBrowser()
    manager.max_tabs = 5
    manager._tabs = {}
    manager._lock = threading.RLock()
    manager._condition = threading.Condition(manager._lock)
    manager._scan_snapshot_lock = threading.Lock()
    manager._initialized = True
    manager._shutdown = False
    manager._last_scan_time = 0.0
    manager._get_tabs_retry_after = 0.0
    manager._last_get_tabs_warning_at = 0.0
    manager._known_tab_ids = set()
    manager._raw_id_to_persistent = {}
    manager._persistent_to_session_id = {}
    manager._isolated_context_by_raw_id = {}
    manager._orphaned_isolated_contexts = {}
    manager._preserved_error_session_ids = set()
    manager._tab_counter = 0
    manager._next_persistent_index = 1
    manager._active_session_id = None
    manager._maintenance_executor = None
    manager._global_network_monitor = None
    manager._global_network_enabled = False
    manager.preserve_error_tabs = False
    manager.excluded_urls = []

    manager._is_site_independent_cookie_enabled = lambda _domain: False
    manager._start_global_monitor_for_session = lambda _session: None
    manager._detach_global_monitor_for_session = lambda *_args, **_kwargs: None
    manager._cleanup_orphaned_isolated_contexts = lambda *_args, **_kwargs: None
    manager._cleanup_unhealthy_tabs = lambda: None
    manager._on_session_removed = lambda _session_id: None

    tabs = manager.get_tabs_with_index()

    assert len(tabs) == 1
    assert tabs[0]["url"] == "https://chatglm.cn/main/alltoolsdetail"
    assert tabs[0]["current_domain"] == "chatglm.cn"


def test_tab_pool_query_removes_tab_when_current_cdp_url_becomes_invalid():
    class FakeTab:
        tab_id = "raw-1"

    class FakeBrowser:
        def __init__(self, url):
            self.url = url
            self.tab = FakeTab()

        def _run_cdp(self, command, **_kwargs):
            assert command == "Target.getTargets"
            return {
                "targetInfos": [
                    {
                        "targetId": "raw-1",
                        "type": "page",
                        "url": self.url,
                        "browserContextId": "ctx-1",
                    }
                ]
            }

        def get_tab(self, raw_tab_id):
            assert raw_tab_id == "raw-1"
            return self.tab

    manager = TabPoolManager.__new__(TabPoolManager)
    manager.page = FakeBrowser("https://chatglm.cn/main/alltoolsdetail")
    manager.max_tabs = 5
    manager._tabs = {}
    manager._lock = threading.RLock()
    manager._condition = threading.Condition(manager._lock)
    manager._scan_snapshot_lock = threading.Lock()
    manager._initialized = True
    manager._shutdown = False
    manager._last_scan_time = 0.0
    manager._get_tabs_retry_after = 0.0
    manager._last_get_tabs_warning_at = 0.0
    manager._known_tab_ids = set()
    manager._raw_id_to_persistent = {}
    manager._persistent_to_session_id = {}
    manager._isolated_context_by_raw_id = {}
    manager._orphaned_isolated_contexts = {}
    manager._preserved_error_session_ids = set()
    manager._tab_counter = 0
    manager._next_persistent_index = 1
    manager._active_session_id = None
    manager._maintenance_executor = None
    manager._global_network_monitor = None
    manager._global_network_enabled = False
    manager.preserve_error_tabs = False
    manager.excluded_urls = []

    manager._is_site_independent_cookie_enabled = lambda _domain: False
    manager._start_global_monitor_for_session = lambda _session: None
    manager._detach_global_monitor_for_session = lambda *_args, **_kwargs: None
    manager._cleanup_orphaned_isolated_contexts = lambda *_args, **_kwargs: None
    manager._on_session_removed = lambda _session_id: None
    manager._close_raw_tabs_async = lambda *_args, **_kwargs: None

    assert len(manager.get_tabs_with_index()) == 1

    manager.page.url = "about:blank"

    assert manager.get_tabs_with_index() == []
