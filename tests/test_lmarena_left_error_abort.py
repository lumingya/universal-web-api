import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import WorkflowError
from app.core.network_monitor import NetworkMonitor, NetworkMonitorTerminalError
from app.core.parsers.lmarena_side_left_parser import LmarenaSideLeftParser
from app.core.workflow.executor import WorkflowExecutor


class DummyTab:
    pass


class FallbackStreamMonitor:
    def monitor(self, **kwargs):
        raise AssertionError("DOM fallback should not run for left stream terminal errors")


class TerminalErrorNetworkMonitor:
    def monitor(self, **kwargs):
        raise NetworkMonitorTerminalError("left stream failed")


class LmarenaLeftErrorAbortTests(unittest.TestCase):
    def test_left_parser_marks_errors_as_terminal(self):
        parser = LmarenaSideLeftParser()
        raw_response = 'ae:{"message":"Something went wrong with this response, please try again."}'

        result = parser.parse_chunk(raw_response)

        self.assertTrue(parser.should_abort_on_error())
        self.assertIn("Something went wrong", str(result.get("error")))

    def test_left_parser_treats_a3_as_terminal_error(self):
        parser = LmarenaSideLeftParser()
        raw_response = 'a3:"Failed after 3 attempts. Last error: Too Many Requests"'

        result = parser.parse_chunk(raw_response)

        self.assertTrue(parser.should_abort_on_error())
        self.assertEqual(
            "Failed after 3 attempts. Last error: Too Many Requests",
            result.get("error"),
        )
        self.assertTrue(result.get("done"))

    def test_left_parser_ignores_right_side_b3_error_frames(self):
        parser = LmarenaSideLeftParser()
        raw_response = 'b3:"Failed after 3 attempts. Last error: Too Many Requests"'

        result = parser.parse_chunk(raw_response)

        self.assertIsNone(result.get("error"))
        self.assertFalse(result.get("done"))

    def test_network_monitor_raises_terminal_error_for_left_parser(self):
        parser = LmarenaSideLeftParser()
        monitor = NetworkMonitor(
            tab=DummyTab(),
            formatter=None,
            parser=parser,
            stream_config={"network": {"listen_pattern": "http"}},
        )

        with self.assertRaises(NetworkMonitorTerminalError):
            monitor._handle_parse_result({"content": "", "done": True, "error": "left stream failed"})

    def test_executor_does_not_fallback_to_dom_on_terminal_error(self):
        executor = WorkflowExecutor(
            tab=DummyTab(),
            stream_config={"mode": "dom"},
            selectors={},
        )
        executor._stream_mode = "network"
        executor._network_monitor = TerminalErrorNetworkMonitor()
        executor._stream_monitor = FallbackStreamMonitor()

        generator = executor.execute_step(
            action="STREAM_WAIT",
            selector="unused",
            target_key="result_container",
            context={"prompt": "hello"},
        )

        with self.assertRaises(WorkflowError) as cm:
            list(generator)

        self.assertIn("stream_terminal_error:left stream failed", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
