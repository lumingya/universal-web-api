import threading
import time
from collections import OrderedDict

from app.core.tab_pool_parts.manager import TabPoolManager
from app.core.tab_pool_parts.session import TabSession, TabStatus
from app.utils.tab_route_groups import normalize_route_groups


class _FakeTab:
    def __init__(self, tab_id: str, url: str):
        self.tab_id = tab_id
        self.url = url

    def run_js(self, *_args, **_kwargs):
        return None


def _session(session_id: str, index: int, url: str) -> TabSession:
    return TabSession(
        session_id,
        _FakeTab(f"raw-{session_id}", url),
        last_known_url=url,
        current_domain="arena.ai",
        persistent_index=index,
    )


def _manager(sessions, *, excluded_urls=None) -> TabPoolManager:
    manager = TabPoolManager.__new__(TabPoolManager)
    manager._lock = threading.RLock()
    manager._condition = threading.Condition(manager._lock)
    manager._global_monitor_transition_lock = threading.RLock()
    manager._shutdown = False
    manager._global_network_monitor = None
    manager._tabs = {session.id: session for session in sessions}
    manager._persistent_to_session_id = {
        session.persistent_index: session.id for session in sessions
    }
    manager.acquire_timeout = 1.0
    manager.allocation_mode = "round_robin"
    manager.excluded_urls = manager._normalize_excluded_urls(excluded_urls or [])
    manager.route_groups = normalize_route_groups([
        {
            "id": "arena-image",
            "name": "Arena image",
            "route_domain": "arena.ai",
            "allocation_mode": "round_robin",
            "members": [
                {
                    "url": session.last_known_url,
                    "tab_index": session.persistent_index,
                }
                for session in sessions
            ],
        }
    ])
    manager._route_group_bindings = {}
    manager._group_waiters = {}
    manager._route_waiters = {}
    manager._index_waiters = {}
    manager._acquire_waiters = __import__("collections").deque()
    manager._waiter_counter = 0
    manager._route_round_robin_cursor = OrderedDict()
    manager._round_robin_cursor = 0
    manager._active_session_id = None
    manager._auto_activate_on_acquire = False
    manager._should_scan = lambda: False
    manager._scan_new_tabs = lambda: None
    manager._check_stuck_tabs = lambda: False
    manager._cleanup_unhealthy_tabs = lambda: None
    manager._should_defer_to_command = lambda *_args, **_kwargs: False
    manager._complete_acquired_session_for_return = lambda *_args, **_kwargs: True
    # 与 __init__ 对齐（见 TabPoolManager.__init__）：release()/terminate_by_index
    # 会访问这些懒创建资源，缺失会让用例挂在 AttributeError 上而不是断言业务行为。
    manager._maintenance_executor = None
    manager._acquire_executor = None
    manager._acquire_executor_lock = threading.Lock()
    manager._recovery_service = None
    manager._recovery_service_lock = threading.Lock()
    manager._quarantined_raw_ids = {}
    return manager


def test_route_group_can_use_members_excluded_from_domain_routing():
    first = _session("arena-1", 1, "https://arena.ai/c/image-1")
    second = _session("arena-2", 2, "https://arena.ai/c/image-2")
    manager = _manager(
        [first, second],
        excluded_urls=[first.last_known_url, second.last_known_url],
    )

    assert manager._get_sessions_for_route_domain("arena.ai") == []
    acquired = manager.acquire_by_route_group("arena-image", "req-1", timeout=0.2)

    assert acquired is not None
    assert acquired.id in {first.id, second.id}


def test_route_group_concurrent_requests_acquire_distinct_idle_members():
    first = _session("arena-1", 1, "https://arena.ai/c/image-1")
    second = _session("arena-2", 2, "https://arena.ai/c/image-2")
    manager = _manager([first, second])

    acquired_first = manager.acquire_by_route_group("arena-image", "req-1", timeout=0.2)
    acquired_second = manager.acquire_by_route_group("arena-image", "req-2", timeout=0.2)

    assert acquired_first is not None
    assert acquired_second is not None
    assert acquired_first.id != acquired_second.id


def test_route_group_snapshot_live_count_includes_busy_members():
    idle = _session("arena-idle", 1, "https://arena.ai/c/image-1")
    busy = _session("arena-busy", 2, "https://arena.ai/c/image-2")
    manager = _manager([idle, busy])
    assert busy.acquire("req-busy") is True

    group = manager.get_route_groups_snapshot()[0]

    assert group["live_member_count"] == 2
    assert group["idle_member_count"] == 1


def test_route_group_waiter_acquires_member_after_release():
    only = _session("arena-1", 1, "https://arena.ai/c/image-1")
    manager = _manager([only])
    holder = manager.acquire_by_route_group("arena-image", "holder", timeout=0.2)
    result = {}

    waiter = threading.Thread(
        target=lambda: result.setdefault(
            "session",
            manager.acquire_by_route_group("arena-image", "waiter", timeout=0.8),
        )
    )
    waiter.start()
    time.sleep(0.1)
    assert waiter.is_alive()

    assert manager.release(
        holder.id,
        check_triggers=False,
        expected_task_id="holder",
    ) is True
    waiter.join(timeout=1.0)

    assert not waiter.is_alive()
    assert result["session"] is only
    assert only.current_task_id == "waiter"


def test_route_group_binding_survives_member_url_change_until_session_reconnects():
    original = _session("arena-1", 1, "https://arena.ai/c/image-1")
    manager = _manager([original])

    assert manager._get_sessions_for_route_group("arena-image") == [original]
    original.tab.url = "https://arena.ai/c/new-conversation"
    original.last_known_url = original.tab.url
    assert manager._get_sessions_for_route_group("arena-image") == [original]

    replacement = _session("arena-2", 2, "https://arena.ai/c/image-1")
    manager._tabs = {replacement.id: replacement}
    assert manager._get_sessions_for_route_group("arena-image") == [replacement]


def test_route_group_never_falls_back_to_idle_session_outside_group():
    member = _session("arena-member", 1, "https://arena.ai/c/image")
    outsider = _session("arena-outsider", 2, "https://arena.ai/c/chat")
    manager = _manager([member, outsider])
    manager.route_groups = normalize_route_groups([{
        "id": "arena-image",
        "route_domain": "arena.ai",
        "members": [{"url": member.last_known_url, "tab_index": 1}],
    }])
    assert member.acquire("holder") is True

    acquired = manager.acquire_by_route_group("arena-image", "waiter", timeout=0.15)

    assert acquired is None
    assert outsider.status == TabStatus.IDLE
    assert outsider.current_task_id is None


def test_route_group_waiters_are_served_in_fifo_order():
    member = _session("arena-member", 1, "https://arena.ai/c/image")
    manager = _manager([member])
    holder = manager.acquire_by_route_group("arena-image", "holder", timeout=0.2)
    acquired_order = []
    acquired = {}

    def wait(task_id):
        session = manager.acquire_by_route_group("arena-image", task_id, timeout=1.5)
        acquired[task_id] = session
        if session is not None:
            acquired_order.append(task_id)

    first = threading.Thread(target=wait, args=("waiter-1",))
    second = threading.Thread(target=wait, args=("waiter-2",))
    first.start()
    time.sleep(0.03)
    second.start()

    deadline = time.time() + 0.5
    while time.time() < deadline:
        with manager._lock:
            if len(manager._group_waiters.get("arena-image", ())) >= 2:
                break
        time.sleep(0.01)

    assert manager.release(
        holder.id,
        check_triggers=False,
        expected_task_id="holder",
    ) is True
    first.join(timeout=0.8)
    assert acquired_order == ["waiter-1"]
    assert second.is_alive()

    assert manager.release(
        acquired["waiter-1"].id,
        check_triggers=False,
        expected_task_id="waiter-1",
    ) is True
    second.join(timeout=0.8)

    assert not second.is_alive()
    assert acquired_order == ["waiter-1", "waiter-2"]


def test_terminate_quarantines_only_target_member_until_owner_releases(monkeypatch):
    first = _session("arena-1", 1, "https://arena.ai/c/image-1")
    second = _session("arena-2", 2, "https://arena.ai/c/image-2")
    manager = _manager([first, second])
    manager.TERMINATION_RELEASE_WAIT_SEC = 1.0

    assert manager.acquire_by_route_group("arena-image", "req-1", timeout=0.2) is first
    assert manager.acquire_by_route_group("arena-image", "req-2", timeout=0.2) is second

    cancelled = []
    from app.services.request_manager import request_manager

    monkeypatch.setattr(
        request_manager,
        "cancel_request",
        lambda request_id, reason: cancelled.append((request_id, reason)) or True,
    )

    result = {}
    terminate_thread = threading.Thread(
        target=lambda: result.setdefault(
            "value",
            manager.terminate_by_index(
                1,
                clear_page=False,
                expected_session_id=first.id,
                expected_task_id="req-1",
            ),
        )
    )
    terminate_thread.start()

    deadline = time.time() + 0.5
    while time.time() < deadline and not first.is_termination_in_progress():
        time.sleep(0.01)

    assert first.is_termination_in_progress() is True
    assert first.status == TabStatus.BUSY
    assert first.current_task_id == "req-1"
    assert second.status == TabStatus.BUSY
    assert second.current_task_id == "req-2"
    assert first.acquire("req-3") is False

    assert manager.release(
        first.id,
        check_triggers=False,
        expected_task_id="req-1",
    ) is True
    terminate_thread.join(timeout=1.0)

    assert not terminate_thread.is_alive()
    assert cancelled == [("req-1", "manual_terminate")]
    assert result["value"]["pending"] is False
    assert result["value"]["released"] is True
    assert second.status == TabStatus.BUSY
    assert second.current_task_id == "req-2"
    assert manager.acquire_by_route_group("arena-image", "req-3", timeout=0.2) is first


def test_terminate_rejects_stale_task_ownership_without_touching_new_owner(monkeypatch):
    member = _session("arena-1", 1, "https://arena.ai/c/image-1")
    manager = _manager([member])
    assert manager.acquire_by_route_group("arena-image", "new-task", timeout=0.2) is member

    cancelled = []
    from app.services.request_manager import request_manager

    monkeypatch.setattr(
        request_manager,
        "cancel_request",
        lambda request_id, reason: cancelled.append((request_id, reason)) or True,
    )

    result = manager.terminate_by_index(
        1,
        clear_page=False,
        expected_session_id=member.id,
        expected_task_id="old-task",
    )

    assert result["ok"] is False
    assert result["error"] == "task_ownership_changed"
    assert cancelled == []
    assert member.status == TabStatus.BUSY
    assert member.current_task_id == "new-task"
    assert member.is_termination_in_progress() is False
