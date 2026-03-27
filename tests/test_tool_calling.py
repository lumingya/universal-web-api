import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.tool_calling import complete_tool_calling_roundtrip


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather by city name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "minLength": 1},
                    "unit": {"type": "string", "enum": ["c", "f"]},
                },
                "required": ["city"],
                "additionalProperties": False,
            },
        },
    }
]


class FakeExecutor:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, messages):
        self.calls.append(messages)
        if not self._responses:
            raise AssertionError("unexpected extra tool-calling round")
        return self._responses.pop(0)


class ToolCallingRoundtripTests(unittest.TestCase):
    def test_retries_when_required_argument_is_missing(self):
        executor = FakeExecutor(
            [
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": {}},
                            }
                        ],
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": {"city": "Shanghai", "unit": "c"},
                                },
                            }
                        ],
                    }
                ),
            ]
        )

        parsed = complete_tool_calling_roundtrip(
            messages=[{"role": "user", "content": "查询上海天气"}],
            tools=TOOLS,
            tool_choice="required",
            parallel_tool_calls=False,
            round_executor=executor,
        )

        self.assertEqual(len(executor.calls), 2)
        repaired_call = parsed["tool_calls"][0]
        repaired_args = json.loads(repaired_call["function"]["arguments"])
        self.assertEqual(repaired_args["city"], "Shanghai")
        self.assertEqual(repaired_args["unit"], "c")

        second_round_user_messages = [
            message["content"]
            for message in executor.calls[1]
            if message.get("role") == "user"
        ]
        self.assertTrue(
            any("[Tool Execution Feedback]" in content for content in second_round_user_messages)
        )

    def test_retries_when_arguments_are_not_a_json_object(self):
        executor = FakeExecutor(
            [
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": "Shanghai",
                                },
                            }
                        ],
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": {"city": "Shanghai"},
                                },
                            }
                        ],
                    }
                ),
            ]
        )

        parsed = complete_tool_calling_roundtrip(
            messages=[{"role": "user", "content": "查询上海天气"}],
            tools=TOOLS,
            tool_choice="required",
            parallel_tool_calls=False,
            round_executor=executor,
        )

        self.assertEqual(len(executor.calls), 2)
        repaired_args = json.loads(parsed["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(repaired_args["city"], "Shanghai")

    def test_retries_when_parallel_tool_calls_are_disallowed(self):
        executor = FakeExecutor(
            [
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": {"city": "Shanghai"}},
                            },
                            {
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": {"city": "Beijing"}},
                            },
                        ],
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": {"city": "Shanghai"}},
                            }
                        ],
                    }
                ),
            ]
        )

        parsed = complete_tool_calling_roundtrip(
            messages=[{"role": "user", "content": "查询天气"}],
            tools=TOOLS,
            tool_choice="required",
            parallel_tool_calls=False,
            round_executor=executor,
        )

        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(len(parsed["tool_calls"]), 1)

    def test_raises_after_retry_budget_is_exhausted(self):
        executor = FakeExecutor(
            [
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": {}},
                            }
                        ],
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": {}},
                            }
                        ],
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": {}},
                            }
                        ],
                    }
                ),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "tool_call_validation_exhausted"):
            complete_tool_calling_roundtrip(
                messages=[{"role": "user", "content": "查询天气"}],
                tools=TOOLS,
                tool_choice="required",
                parallel_tool_calls=False,
                round_executor=executor,
            )


if __name__ == "__main__":
    unittest.main()
