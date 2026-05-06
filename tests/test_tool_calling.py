import asyncio
import json
import os
import unittest

from app.services import tool_calling as tc


SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search",
        "description": "search docs",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup",
        "description": "lookup record",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
}


class ToolCallingTests(unittest.TestCase):
    def test_system_prompt_includes_examples(self):
        prompt = tc._build_tool_system_prompt([SEARCH_TOOL], "auto", None)
        self.assertIn("Concrete examples:", prompt)
        self.assertIn("Tool call example:", prompt)
        self.assertIn('"name": "search"', prompt)

    def test_tool_result_image_placeholders_are_shortened(self):
        raw = (
            "before [图片: 385ECD79A605068752A50B63013EB98E.jpg] "
            "data:image/png;base64," + ("A" * 600) + " "
            "[CQ:image,file=abc,url=https://example.com/signed-image.jpg?token=" + ("x" * 120) + "] after"
        )
        cleaned = tc._prepare_tool_result_content("search", raw)
        self.assertIn("before", cleaned)
        self.assertIn("after", cleaned)
        self.assertNotIn("385ECD79A605068752A50B63013EB98E.jpg", cleaned)
        self.assertNotIn("data:image/png;base64", cleaned)
        self.assertNotIn("[CQ:image", cleaned)
        self.assertLess(len(cleaned), 140)

    def test_additional_properties_are_stripped(self):
        parsed = tc.complete_tool_calling_roundtrip(
            messages=[{"role": "user", "content": "find docs"}],
            tools=[SEARCH_TOOL],
            tool_choice="auto",
            parallel_tool_calls=None,
            round_executor=lambda _msgs: json.dumps(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": {"query": "hello", "thought": "need search"},
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
        args = json.loads(parsed["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(args, {"query": "hello"})

    def test_parallel_partial_success_returns_valid_subset(self):
        parsed = tc.complete_tool_calling_roundtrip(
            messages=[{"role": "user", "content": "search and lookup"}],
            tools=[SEARCH_TOOL, LOOKUP_TOOL],
            tool_choice="auto",
            parallel_tool_calls=True,
            round_executor=lambda _msgs: json.dumps(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": {"query": "alpha"},
                            },
                        },
                        {
                            "type": "function",
                            "function": {
                                "name": "lookup",
                                "arguments": {"id": "oops"},
                            },
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        )
        self.assertEqual(len(parsed["tool_calls"]), 1)
        self.assertEqual(parsed["tool_calls"][0]["function"]["name"], "search")

    def test_validation_exhaustion_degrades_to_final_text(self):
        old_retry = os.environ.get("TOOL_CALLING_INTERNAL_RETRY_MAX")
        try:
            os.environ["TOOL_CALLING_INTERNAL_RETRY_MAX"] = "0"
            parsed = tc.complete_tool_calling_roundtrip(
                messages=[{"role": "user", "content": "must use tool"}],
                tools=[LOOKUP_TOOL],
                tool_choice="required",
                parallel_tool_calls=None,
                round_executor=lambda _msgs: json.dumps(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "lookup",
                                    "arguments": {"id": "bad"},
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
        finally:
            if old_retry is None:
                os.environ.pop("TOOL_CALLING_INTERNAL_RETRY_MAX", None)
            else:
                os.environ["TOOL_CALLING_INTERNAL_RETRY_MAX"] = old_retry

        self.assertEqual(parsed["mode"], "final")
        self.assertEqual(parsed["tool_calls"], [])
        self.assertEqual(
            parsed["content"],
            "Sorry, I ran into tool-call parsing issues. "
            "Please rephrase or provide more specific details.",
        )

    def test_truncated_json_payload_is_closed_and_parsed(self):
        parsed = tc.parse_tool_response(
            (
                '{"role":"assistant","content":null,"tool_calls":'
                '[{"type":"function","function":{"name":"search","arguments":{"query":"hello"}}]'
            ),
            [SEARCH_TOOL],
        )
        self.assertEqual(parsed["mode"], "tool_calls")
        self.assertEqual(len(parsed["tool_calls"]), 1)
        self.assertEqual(parsed["tool_calls"][0]["function"]["name"], "search")

    def test_truncated_nested_arguments_are_closed_and_decoded(self):
        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "search",
                "arguments": '{"query":"hello","filters":{"lang":"zh"',
            },
        }
        args = tc._decode_tool_arguments(tool_call)
        self.assertEqual(
            args,
            {
                "query": "hello",
                "filters": {"lang": "zh"},
            },
        )

    def test_async_roundtrip_supports_async_executor(self):
        async def _executor(_msgs):
            return json.dumps(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": {"query": "async"},
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            )

        parsed = asyncio.run(
            tc.complete_tool_calling_roundtrip_async(
                messages=[{"role": "user", "content": "async search"}],
                tools=[SEARCH_TOOL],
                tool_choice="auto",
                parallel_tool_calls=None,
                round_executor=_executor,
            )
        )
        self.assertEqual(parsed["tool_calls"][0]["function"]["name"], "search")


if __name__ == "__main__":
    unittest.main()
