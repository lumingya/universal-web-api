import json

from app.services.tool_calling_parse import parse_tool_response
from app.services.tool_calling_validation_retry import (
    _validate_tool_arguments_against_schema,
)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "options": {
                        "type": "object",
                        "properties": {"maxResults": {"type": "integer"}},
                    },
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    }
]


def _arguments(raw: str):
    parsed = parse_tool_response(raw, TOOLS)
    assert parsed["mode"] == "tool_calls"
    return json.loads(parsed["tool_calls"][0]["function"]["arguments"])


def test_tolerant_tool_markup_preserves_unescaped_code_operators():
    raw = (
        '<adapter_calls><call name="run_code"><arg name="code">'
        "if (a < b && c > d)"
        "</arg></call></adapter_calls>"
    )

    assert _arguments(raw) == {"code": "if (a < b && c > d)"}


def test_tolerant_tool_markup_recovers_unclosed_tags_and_schema_casing():
    raw = (
        '<adapter_calls><call name="run_code"><arg name="code">print(1)</arg>'
        '<arg name="options"><maxResults>5</maxResults></arg>'
    )

    assert _arguments(raw) == {
        "code": "print(1)",
        "options": {"maxResults": 5},
    }


def test_json_repair_recovers_truncated_llm_tool_payload():
    raw = (
        '{"mode":"tool_calls","tool_calls":[{"function":{'
        '"name":"run_code","arguments":{"code":"print(1)",}}}'
    )

    assert _arguments(raw) == {"code": "print(1)"}


def test_jsonschema_handles_defs_and_pattern_properties_with_error_paths():
    schema = {
        "$defs": {"positive": {"type": "integer", "minimum": 1}},
        "type": "object",
        "patternProperties": {"^count_[a-z]+$": {"$ref": "#/$defs/positive"}},
        "additionalProperties": False,
    }

    assert _validate_tool_arguments_against_schema(
        {"count_ok": 2}, schema, "arguments"
    ) == []
    errors = _validate_tool_arguments_against_schema(
        {"count_bad": 0}, schema, "arguments"
    )
    assert errors and errors[0].startswith("arguments.count_bad ")
