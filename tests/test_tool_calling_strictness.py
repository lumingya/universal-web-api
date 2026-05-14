import os

from app.services import tool_calling


def _tool_schema(additional_properties=False):
    return [
        {
            "type": "function",
            "function": {
                "name": "search_docs",
                "description": "Search docs",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": additional_properties,
                },
            },
        }
    ]


def _parsed_call(arguments, tool_call_id="call_1"):
    return {
        "mode": "tool_calls",
        "content": None,
        "tool_calls": [
            {
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": "search_docs",
                    "arguments": arguments,
                },
            }
        ],
    }


def test_rejects_additional_properties_when_schema_forbids_them():
    inspection = tool_calling._inspect_tool_response(
        raw_text="",
        parsed=_parsed_call({"query": "abc", "extra": "nope"}),
        tools=_tool_schema(additional_properties=False),
        tool_choice="auto",
        parallel_tool_calls=None,
    )

    messages = [item["message"] for item in inspection["errors"]]
    assert any("arguments.extra is not allowed." in message for message in messages)
    assert not inspection["accepted_tool_calls"]


def test_rejects_duplicate_tool_calls_with_identical_arguments():
    parsed = {
        "mode": "tool_calls",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search_docs", "arguments": {"query": "abc"}},
            },
            {
                "id": "call_2",
                "type": "function",
                "function": {"name": "search_docs", "arguments": {"query": "abc"}},
            },
        ],
    }

    inspection = tool_calling._inspect_tool_response(
        raw_text="",
        parsed=parsed,
        tools=_tool_schema(),
        tool_choice="auto",
        parallel_tool_calls=True,
    )

    codes = [item["code"] for item in inspection["errors"]]
    assert "duplicate_tool_call" in codes


def test_rejects_duplicate_tool_call_ids():
    parsed = {
        "mode": "tool_calls",
        "content": None,
        "tool_calls": [
            {
                "id": "call_dup",
                "type": "function",
                "function": {"name": "search_docs", "arguments": {"query": "abc"}},
            },
            {
                "id": "call_dup",
                "type": "function",
                "function": {"name": "search_docs", "arguments": {"query": "xyz"}},
            },
        ],
    }

    inspection = tool_calling._inspect_tool_response(
        raw_text="",
        parsed=parsed,
        tools=_tool_schema(),
        tool_choice="auto",
        parallel_tool_calls=True,
    )

    codes = [item["code"] for item in inspection["errors"]]
    assert "duplicate_tool_call_id" in codes


def test_rejects_unique_items_violation():
    inspection = tool_calling._inspect_tool_response(
        raw_text="",
        parsed=_parsed_call({"query": "abc", "tags": ["x", "x"]}),
        tools=_tool_schema(),
        tool_choice="auto",
        parallel_tool_calls=None,
    )

    messages = [item["message"] for item in inspection["errors"]]
    assert any("duplicates an earlier array item" in message for message in messages)


def test_rejects_oversized_argument_payload():
    previous = os.environ.get("TOOL_CALLING_MAX_ARGUMENT_CHARS")
    os.environ["TOOL_CALLING_MAX_ARGUMENT_CHARS"] = "256"
    try:
        inspection = tool_calling._inspect_tool_response(
            raw_text="",
            parsed=_parsed_call({"query": "x" * 500}),
            tools=_tool_schema(),
            tool_choice="auto",
            parallel_tool_calls=None,
        )
    finally:
        if previous is None:
            os.environ.pop("TOOL_CALLING_MAX_ARGUMENT_CHARS", None)
        else:
            os.environ["TOOL_CALLING_MAX_ARGUMENT_CHARS"] = previous

    codes = [item["code"] for item in inspection["errors"]]
    assert "argument_shape_limit_exceeded" in codes
