import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.command_engine_actions import CommandEngineActionsMixin


class FakeTab:
    def __init__(self):
        self.calls = []

    def run_js(self, code):
        self.calls.append(code)
        if str(code).strip().startswith("return "):
            return "VERIFY"
        return None


class FakeSession:
    def __init__(self):
        self.tab = FakeTab()


class FakeCommandEngine(CommandEngineActionsMixin):
    pass


class CommandEngineRunJsTests(unittest.TestCase):
    def test_run_js_retries_iife_with_return_wrapper(self):
        engine = FakeCommandEngine()
        session = FakeSession()
        action = {
            "type": "run_js",
            "code": "(() => { return 'VERIFY'; })()",
        }

        result = engine._execute_action(action, session)

        self.assertEqual(result, "VERIFY")
        self.assertEqual(len(session.tab.calls), 2)
        self.assertEqual(session.tab.calls[0], action["code"])
        self.assertEqual(session.tab.calls[1], "return (() => { return 'VERIFY'; })();")

    def test_run_js_leaves_non_iife_code_unchanged(self):
        engine = FakeCommandEngine()
        session = FakeSession()
        action = {
            "type": "run_js",
            "code": "const x = 1;",
            "fail_on_falsy": False,
        }

        result = engine._execute_action(action, session)

        self.assertIsNone(result)
        self.assertEqual(session.tab.calls, ["const x = 1;"])


if __name__ == "__main__":
    unittest.main()
