import json
import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.command_engine import CommandEngine


class DummySession:
    id = "arena_1"
    current_task_id = "req-123"


class CommandEngineExternalResultEventTests(unittest.TestCase):
    def _make_engine(self):
        engine = CommandEngine.__new__(CommandEngine)
        engine._lock = threading.Lock()
        engine._command_result_events = {}
        return engine

    def test_emit_external_result_event_records_queue_entry(self):
        engine = self._make_engine()
        session = DummySession()

        event = engine.emit_external_command_result_event(
            session,
            source_command_id="evt_stream_terminal_error",
            source_command_name="ARENA_STREAM_TERMINAL_ALERT",
            summary="目标流告警：检测到限流终止（Too Many Requests），当前工作流将报错结束",
            result="Failed after 3 attempts. Last error: Too Many Requests",
            informative=True,
            mode="external_alert",
            group_name="arena_commands",
            trigger_commands=False,
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["source_command_id"], "evt_stream_terminal_error")
        self.assertEqual(event["source_command_name"], "ARENA_STREAM_TERMINAL_ALERT")
        self.assertEqual(event["mode"], "external_alert")
        self.assertTrue(event["informative"])
        self.assertEqual(event["task_id"], "req-123")

        queue = engine._command_result_events[session.id]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["summary"], event["summary"])

    def test_arena_notify_command_listens_to_stream_terminal_event(self):
        commands_path = ROOT / "config" / "commands.json"
        commands = json.loads(commands_path.read_text(encoding="utf-8"))["commands"]

        notify_cmd = next(cmd for cmd in commands if cmd.get("id") == "cmd_arena_notify_qq")
        command_ids = list(notify_cmd.get("trigger", {}).get("command_ids", []))

        self.assertIn("evt_stream_terminal_error", command_ids)


if __name__ == "__main__":
    unittest.main()
