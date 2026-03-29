import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.tab_pool import TabPoolManager


class FakeListen:
    listening = False

    def start(self, _pattern):
        self.listening = True

    def wait(self, timeout=None):
        _ = timeout
        return None

    def stop(self):
        self.listening = False


class FakeTab:
    def __init__(self, url: str):
        self.url = url
        self.listen = FakeListen()
        self.set = SimpleNamespace(activate=lambda: None)

    def run_js(self, _script):
        return None

    def refresh(self):
        return None

    def get(self, url: str):
        self.url = url
        return None


class FakeBrowserPage:
    def __init__(self, tabs):
        self._tabs = dict(tabs)

    def get_tabs(self):
        return list(self._tabs.keys())

    def get_tab(self, raw_tab_id):
        return self._tabs.get(raw_tab_id)


class TabPoolFairQueueTests(unittest.TestCase):
    def _make_manager(self, url: str = "https://chat.example.com/"):
        page = FakeBrowserPage({"tab-1": FakeTab(url)})
        manager = TabPoolManager(
            browser_page=page,
            max_tabs=1,
            min_tabs=1,
            idle_timeout=300,
            acquire_timeout=1,
            stuck_timeout=60,
        )
        manager.initialize()
        return manager

    @staticmethod
    def _wait_for(predicate, timeout: float = 2.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def _run_ordered_workers(self, manager, acquire_call, waiter_size):
        held = acquire_call("held", timeout=0.3)
        self.assertIsNotNone(held)

        order = []
        errors = []
        order_lock = threading.Lock()

        def worker(task_id: str):
            try:
                session = acquire_call(task_id, timeout=1.5)
                if session is None:
                    raise AssertionError(f"{task_id} failed to acquire a tab")
                with order_lock:
                    order.append(task_id)
                time.sleep(0.03)
                manager.release(session.id, check_triggers=False)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=("req-1",), daemon=True)
        t2 = threading.Thread(target=worker, args=("req-2",), daemon=True)
        t1.start()
        self.assertTrue(self._wait_for(lambda: waiter_size() == 1))
        t2.start()
        self.assertTrue(self._wait_for(lambda: waiter_size() == 2))

        manager.release(held.id, check_triggers=False)

        t1.join(timeout=2)
        t2.join(timeout=2)

        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertFalse(errors, str(errors))
        return order

    def test_generic_acquire_preserves_fifo_order(self):
        manager = self._make_manager()

        order = self._run_ordered_workers(
            manager,
            lambda task_id, timeout: manager.acquire(task_id, timeout=timeout),
            lambda: len(manager._acquire_waiters),
        )

        self.assertEqual(order, ["req-1", "req-2"])
        self.assertEqual(list(manager._acquire_waiters), [])

    def test_acquire_by_index_preserves_fifo_order(self):
        manager = self._make_manager()

        order = self._run_ordered_workers(
            manager,
            lambda task_id, timeout: manager.acquire_by_index(1, task_id, timeout=timeout),
            lambda: len(manager._index_waiters.get(1, ())),
        )

        self.assertEqual(order, ["req-1", "req-2"])
        self.assertNotIn(1, manager._index_waiters)

    def test_acquire_by_route_domain_preserves_fifo_order(self):
        manager = self._make_manager("https://chat.example.com/conversation")

        order = self._run_ordered_workers(
            manager,
            lambda task_id, timeout: manager.acquire_by_route_domain(
                "chat.example.com",
                task_id,
                timeout=timeout,
            ),
            lambda: len(manager._route_waiters.get("chat.example.com", ())),
        )

        self.assertEqual(order, ["req-1", "req-2"])
        self.assertNotIn("chat.example.com", manager._route_waiters)

    def test_timed_out_waiter_is_removed_from_queue(self):
        manager = self._make_manager()
        held = manager.acquire("held", timeout=0.3)
        self.assertIsNotNone(held)

        result = {}

        def worker():
            result["session"] = manager.acquire("timeout-task", timeout=0.15)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        self.assertTrue(self._wait_for(lambda: len(manager._acquire_waiters) == 1))
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertIsNone(result.get("session"))
        self.assertEqual(list(manager._acquire_waiters), [])

        manager.release(held.id, check_triggers=False)
        session = manager.acquire("next-task", timeout=0.3)
        self.assertIsNotNone(session)
        manager.release(session.id, check_triggers=False)


if __name__ == "__main__":
    unittest.main()
