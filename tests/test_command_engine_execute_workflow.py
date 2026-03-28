import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.command_engine_actions import CommandEngineActionsMixin
from app.services.command_engine_runtime import CommandEngineRuntimeMixin


class FakeBrowser:
    def __init__(self):
        self.calls = []

    def _execute_workflow_non_stream(
        self,
        session,
        messages,
        preset_name=None,
        stop_checker=None,
        workflow_priority=None,
    ):
        stop_before_start = bool(stop_checker and stop_checker())
        self.calls.append(
            {
                "session_id": session.id,
                "messages": list(messages),
                "preset_name": preset_name,
                "workflow_priority": workflow_priority,
                "stop_before_start": stop_before_start,
            }
        )
        if stop_before_start:
            yield 'data: {"error": {"message": "请求已取消", "type": "execution_error", "code": "cancelled"}}'
            return
        yield 'data: {"status": "ok"}'


class FakeConfigEngine:
    @staticmethod
    def get_default_preset(_domain):
        return "主预设"


class FakeTab:
    url = "https://arena.ai/"


class FakeSession:
    def __init__(self):
        self.id = "arena_2"
        self.persistent_index = 2
        self.preset_name = "主预设"
        self.current_domain = "arena.ai"
        self.tab = FakeTab()


class FakeCommandEngine(CommandEngineRuntimeMixin, CommandEngineActionsMixin):
    def __init__(self, browser):
        self._browser = browser
        self._config_engine = FakeConfigEngine()
        self._request_priority_baseline = 2
        self._lock = threading.Lock()

    def _get_browser(self):
        return self._browser

    def _get_config_engine(self):
        return self._config_engine

    @staticmethod
    def _should_follow_default_preset(preset_name):
        return not str(preset_name or "").strip()

    @staticmethod
    def _resolve_preset_name(preset_name, session=None):
        return str(preset_name or "").strip()

    @staticmethod
    def _coerce_float(value, default):
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _normalize_priority(value, default=2):
        try:
            return int(value)
        except Exception:
            return int(default)

    def _get_request_priority_baseline(self):
        return self._normalize_priority(self._request_priority_baseline, 2)

    @staticmethod
    def _get_session_domain(session):
        return str(getattr(session, "current_domain", "") or "").strip().lower()

    @staticmethod
    def _get_active_workflow_runtime(session):
        stack = getattr(session, "_workflow_runtime_stack", None) or []
        if not stack:
            return None
        runtime = stack[-1]
        return runtime if isinstance(runtime, dict) else None


class ExecuteWorkflowActionTests(unittest.TestCase):
    def test_clears_stale_interrupt_before_starting_manual_workflow(self):
        browser = FakeBrowser()
        engine = FakeCommandEngine(browser)
        session = FakeSession()
        session._workflow_stop_reason = "command_interrupt"

        result = engine._execute_workflow_action(
            {"preset_name": "切换SideBySide", "prompt": ""},
            session,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(browser.calls[0]["stop_before_start"])
        self.assertIsNone(getattr(session, "_workflow_stop_reason", None))

    def test_restores_parent_interrupt_after_nested_workflow_finishes(self):
        browser = FakeBrowser()
        engine = FakeCommandEngine(browser)
        session = FakeSession()
        session._workflow_stop_reason = "command_interrupt"
        engine.begin_workflow_runtime(session, task_id="req-outer", preset_name="外层", priority=2)

        try:
            result = engine._execute_workflow_action(
                {"preset_name": "切换SideBySide", "prompt": ""},
                session,
            )
        finally:
            engine.finish_workflow_runtime(session, aborted=True)

        self.assertTrue(result["ok"])
        self.assertFalse(browser.calls[0]["stop_before_start"])
        self.assertEqual(getattr(session, "_workflow_stop_reason", None), "command_interrupt")


if __name__ == "__main__":
    unittest.main()
