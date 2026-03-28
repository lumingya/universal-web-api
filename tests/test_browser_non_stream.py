import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.browser import BrowserCore


class FakeFormatter:
    @staticmethod
    def pack_non_stream(content):
        return {"content": content}


class FakeBrowserCore:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False
        self.formatter = FakeFormatter()

    def _execute_workflow_stream(
        self,
        session,
        messages,
        preset_name=None,
        stop_checker=None,
        workflow_priority=None,
    ):
        try:
            for chunk in self._chunks:
                yield chunk
        finally:
            self.closed = True


class BrowserNonStreamTests(unittest.TestCase):
    def test_returns_error_payload_and_closes_inner_stream(self):
        fake = FakeBrowserCore(
            ['data: {"error": {"message": "boom", "code": "workflow_failed"}}']
        )

        gen = BrowserCore._execute_workflow_non_stream(fake, None, [])
        try:
            payload = next(gen)
            data = json.loads(payload)
            self.assertEqual(data["error"]["message"], "boom")
        finally:
            gen.close()

        self.assertTrue(fake.closed)

    def test_collects_content_for_successful_non_stream_response(self):
        fake = FakeBrowserCore(
            [
                'data: {"choices": [{"delta": {"content": "hello"}}]}',
                'data: {"choices": [{"delta": {"content": " world"}}]}',
                "data: [DONE]",
            ]
        )

        payload = next(BrowserCore._execute_workflow_non_stream(fake, None, []))
        data = json.loads(payload)

        self.assertEqual(data["content"], "hello world")
        self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()
