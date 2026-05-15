from app.services import tool_calling


def _tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "write_to_file",
                "description": "Write a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ask_followup_question",
                "description": "Ask a follow-up question",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "follow_up": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                },
                                "required": ["text"],
                            },
                        },
                    },
                    "required": ["question", "follow_up"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_docs",
                "description": "Search docs",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        },
    ]


def test_parses_xml_tool_call_with_cdata_content():
    text = """
<adapter_calls>
  <call name="write_to_file">
    <arg name="path"><![CDATA[notes.txt]]></arg>
    <arg name="content"><![CDATA[# Title
```xml
<adapter_calls><call name="demo"></call></adapter_calls>
```]]></arg>
  </call>
</adapter_calls>
""".strip()

    parsed = tool_calling.parse_tool_response(text, _tools())

    assert parsed["mode"] == "tool_calls"
    assert len(parsed["tool_calls"]) == 1
    arguments = tool_calling._decode_tool_arguments(parsed["tool_calls"][0])
    assert arguments == {
        "path": "notes.txt",
        "content": "# Title\n```xml\n<adapter_calls><call name=\"demo\"></call></adapter_calls>\n```",
    }


def test_parses_project_xml_nested_array_parameters():
    text = """
<adapter_calls>
  <call name="ask_followup_question">
    <arg name="question"><![CDATA[Which approach do you prefer?]]></arg>
    <arg name="follow_up">
      <item><text><![CDATA[Option A]]></text></item>
      <item><text><![CDATA[Option B]]></text></item>
    </arg>
  </call>
</adapter_calls>
""".strip()

    parsed = tool_calling.parse_tool_response(text, _tools())

    assert parsed["mode"] == "tool_calls"
    assert len(parsed["tool_calls"]) == 1
    arguments = tool_calling._decode_tool_arguments(parsed["tool_calls"][0])
    assert arguments == {
        "question": "Which approach do you prefer?",
        "follow_up": [{"text": "Option A"}, {"text": "Option B"}],
    }


def test_ignores_tool_call_examples_inside_markdown_code_fences():
    text = """
```xml
<adapter_calls>
  <call name="search_docs">
    <arg name="query">tool parser</arg>
  </call>
</adapter_calls>
```
Do not execute the example above.
""".strip()

    parsed = tool_calling.parse_tool_response(text, _tools())

    assert parsed["mode"] == "final"
    assert parsed["tool_calls"] == []


def test_keeps_legacy_self_closing_xml_fallback():
    text = '<search_docs query="tool parser" />'

    parsed = tool_calling.parse_tool_response(text, _tools())

    assert parsed["mode"] == "tool_calls"
    assert len(parsed["tool_calls"]) == 1
    arguments = tool_calling._decode_tool_arguments(parsed["tool_calls"][0])
    assert arguments == {"query": "tool parser"}


def test_keeps_legacy_wrapper_xml_compatibility():
    text = """
<tool_calls>
  <invoke name="search_docs">
    <parameter name="query"><![CDATA[legacy syntax]]></parameter>
  </invoke>
</tool_calls>
""".strip()

    parsed = tool_calling.parse_tool_response(text, _tools())

    assert parsed["mode"] == "tool_calls"
    assert len(parsed["tool_calls"]) == 1
    arguments = tool_calling._decode_tool_arguments(parsed["tool_calls"][0])
    assert arguments == {"query": "legacy syntax"}
