import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.browser import BrowserCore


def _error_chunk(message: str) -> str:
    payload = {
        "error": {
            "message": message,
            "type": "execution_error",
            "code": "workflow_failed",
        }
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _content_chunk(content: str) -> str:
    payload = {
        "choices": [{
            "delta": {"content": content},
            "finish_reason": None,
        }]
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class FakeBrowserCore:
    def __init__(self, attempts):
        self._attempts = [list(chunks) for chunks in attempts]
        self.calls = 0
        self._should_stop_checker = lambda: False

    def _chunk_has_stream_content(self, chunk):
        return BrowserCore._chunk_has_stream_content(chunk)

    def _is_retriable_stream_terminal_error_chunk(self, chunk):
        return BrowserCore._is_retriable_stream_terminal_error_chunk(chunk)

    def _build_stream_terminal_alert_message(
        self,
        session_id,
        chunk,
        *,
        retrying,
        saw_content=False,
        attempt=0,
        max_attempts=0,
    ):
        return BrowserCore._build_stream_terminal_alert_message(
            session_id,
            chunk,
            retrying=retrying,
            saw_content=saw_content,
            attempt=attempt,
            max_attempts=max_attempts,
        )

    def _emit_stream_terminal_alert_event(self, session, chunk, *, saw_content=False):
        return None

    def _execute_workflow_stream_once(
        self,
        session,
        messages,
        preset_name=None,
        stop_checker=None,
        workflow_priority=None,
    ):
        chunks = self._attempts[self.calls]
        self.calls += 1
        for chunk in chunks:
            yield chunk


class FakeSession:
    id = "arena_1"


class BrowserStreamRetryTests(unittest.TestCase):
    def test_retries_once_for_terminal_error_without_content(self):
        fake = FakeBrowserCore(
            [
                [_error_chunk("执行失败: stream_terminal_error:left failed")],
                [_content_chunk("ok"), "data: [DONE]\n\n"],
            ]
        )

        output = list(BrowserCore._execute_workflow_stream(fake, FakeSession(), [], stop_checker=lambda: False))

        self.assertEqual(fake.calls, 2)
        self.assertEqual(output, [_content_chunk("ok"), "data: [DONE]\n\n"])

    def test_surfaces_error_after_retry_budget_exhausted(self):
        error = _error_chunk("执行失败: stream_terminal_error:left failed")
        fake = FakeBrowserCore([[error], [error]])

        output = list(BrowserCore._execute_workflow_stream(fake, FakeSession(), [], stop_checker=lambda: False))

        self.assertEqual(fake.calls, 2)
        self.assertEqual(output, [error])

    def test_does_not_retry_after_content_has_started(self):
        error = _error_chunk("执行失败: stream_terminal_error:left failed")
        content = _content_chunk("partial")
        fake = FakeBrowserCore([[content, error]])

        output = list(BrowserCore._execute_workflow_stream(fake, FakeSession(), [], stop_checker=lambda: False))

        self.assertEqual(fake.calls, 1)
        self.assertEqual(output, [content, error])

    @patch("app.core.browser.logger")
    def test_logs_alert_for_retry_and_final_failure(self, mock_logger):
        error = _error_chunk(
            "执行失败: stream_terminal_error:Failed after 3 attempts. Last error: Too Many Requests"
        )
        fake = FakeBrowserCore([[error], [error]])

        output = list(BrowserCore._execute_workflow_stream(fake, FakeSession(), [], stop_checker=lambda: False))

        self.assertEqual(output, [error])

        warning_text = "\n".join(str(call.args[0]) for call in mock_logger.warning.call_args_list)
        error_text = "\n".join(str(call.args[0]) for call in mock_logger.error.call_args_list)

        self.assertIn("[ALERT][arena_1]", warning_text)
        self.assertIn("Too Many Requests", warning_text)
        self.assertIn("(1/1)", warning_text)
        self.assertIn("[ALERT][arena_1]", error_text)
        self.assertIn("Too Many Requests", error_text)

    @patch("app.core.browser.logger")
    def test_logs_alert_when_partial_output_then_terminal_error(self, mock_logger):
        error = _error_chunk("执行失败: stream_terminal_error:left failed")
        content = _content_chunk("partial")
        fake = FakeBrowserCore([[content, error]])

        output = list(BrowserCore._execute_workflow_stream(fake, FakeSession(), [], stop_checker=lambda: False))

        self.assertEqual(output, [content, error])

        error_text = "\n".join(str(call.args[0]) for call in mock_logger.error.call_args_list)
        self.assertIn("[ALERT][arena_1]", error_text)
        self.assertIn("left failed", error_text)


if __name__ == "__main__":
    unittest.main()
