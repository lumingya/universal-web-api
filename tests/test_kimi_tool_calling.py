import json
import unittest

from app.core.parsers.kimi_parser import KimiParser
from app.services.tool_calling import parse_tool_response


COMMAND = (
    'Get-ChildItem -Path "$env:USERPROFILE\\Desktop" | '
    "Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize"
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "exec",
            "description": "Run a command",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    }
]


def _build_assistant_text() -> str:
    payload = {
        "role": "assistant",
        "content": "Inspecting the desktop.",
        "tool_calls": [
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "arguments": {
                        "command": COMMAND,
                    },
                },
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_frame_bytes(assistant_text: str) -> bytes:
    payload = {
        "op": "set",
        "mask": "block.text",
        "eventOffset": 7,
        "block": {
            "id": "1",
            "parentId": "",
            "text": {"content": assistant_text},
        },
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return bytes([0]) + len(payload_bytes).to_bytes(4, byteorder="big", signed=False) + payload_bytes


def _escape_all_bytes(raw: bytes) -> str:
    return "".join(f"\\u00{byte:02x}" for byte in raw)


class KimiToolCallingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = KimiParser()
        self.assistant_text = _build_assistant_text()

    def test_parser_preserves_escaped_tool_json_from_binary_frame(self) -> None:
        result = self.parser.parse_chunk(_build_frame_bytes(self.assistant_text))
        parsed_message = json.loads(result["content"])

        self.assertEqual(
            parsed_message["tool_calls"][0]["function"]["arguments"]["command"],
            COMMAND,
        )

        parsed_tool_response = parse_tool_response(result["content"], TOOLS)
        self.assertEqual(parsed_tool_response["mode"], "tool_calls")
        self.assertEqual(len(parsed_tool_response["tool_calls"]), 1)

        normalized_args = json.loads(
            parsed_tool_response["tool_calls"][0]["function"]["arguments"]
        )
        self.assertEqual(normalized_args["command"], COMMAND)

    def test_parser_decodes_u00_escaped_textual_stream(self) -> None:
        raw_frame = _build_frame_bytes(self.assistant_text)
        self.assertGreater(len(raw_frame), 128)

        escaped_stream = _escape_all_bytes(raw_frame)
        result = self.parser.parse_chunk(escaped_stream)
        parsed_message = json.loads(result["content"])

        self.assertEqual(parsed_message["content"], "Inspecting the desktop.")
        self.assertEqual(
            parsed_message["tool_calls"][0]["function"]["arguments"]["command"],
            COMMAND,
        )

    def test_tool_call_parser_repairs_unescaped_quotes_inside_command_string(self) -> None:
        bad_payload = (
            '{"role":"assistant","content":null,"tool_calls":['
            '{"type":"function","function":{"name":"exec","arguments":'
            '{"command":"Get-ChildItem -Path "C:\\Users\\QIU\\Desktop""}}}]}'
        )

        parsed_tool_response = parse_tool_response(bad_payload, TOOLS)

        self.assertEqual(parsed_tool_response["mode"], "tool_calls")
        self.assertEqual(len(parsed_tool_response["tool_calls"]), 1)

        normalized_args = json.loads(
            parsed_tool_response["tool_calls"][0]["function"]["arguments"]
        )
        self.assertEqual(
            normalized_args["command"],
            'Get-ChildItem -Path "C:\\Users\\QIU\\Desktop"',
        )


if __name__ == "__main__":
    unittest.main()
