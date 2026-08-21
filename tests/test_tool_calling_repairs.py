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


def test_strict_xml_tool_markup_preserves_arguments_losslessly():
    arguments = {"code": "  lead\ntrail  ", "options": {"maxResults": 5}}
    raw = (
        '<adapter_calls><call name="run_code">'
        '<arguments encoding="json"><![CDATA['
        + json.dumps(arguments, ensure_ascii=False)
        + ']]></arguments></call></adapter_calls>'
    )

    assert _arguments(raw) == arguments


def test_parser_does_not_execute_examples_or_incomplete_markup():
    examples = [
        "Documentation: invoke <run_code code='print(1)'/> to run code.",
        '<adapter_calls><call name="run_code"><arguments encoding="json"><![CDATA[{"code":"print(1)"}]]></arguments>',
        'Example only: {"role":"assistant","tool_calls":[{"function":{"name":"run_code","arguments":{"code":"print(1)"}}}]}',
        '<adapter_calls><note>Example</note><call name="run_code"><arguments encoding="json"><![CDATA[{"code":"print(1)"}]]></arguments></call></adapter_calls>',
    ]

    for raw in examples:
        parsed = parse_tool_response(raw, TOOLS)
        assert parsed["mode"] == "final"
        assert parsed["tool_calls"] == []


def test_parser_rejects_surrogate_arguments_before_response_serialization():
    raw = json.dumps(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {"name": "run_code", "arguments": {"code": "\ud800"}},
                }
            ],
        },
        ensure_ascii=True,
    )

    parsed = parse_tool_response(raw, TOOLS)
    assert parsed["mode"] == "final"
    assert parsed["tool_calls"] == []


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


def test_build_browser_messages_unescapes_unicode_arguments_and_avoids_double_escaping():
    from app.services.tool_calling_prompts import build_browser_messages_for_tools

    messages = [
        {"role": "user", "content": "帮我画一张绝美花嫁少女"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_test_123",
                    "type": "function",
                    "function": {
                        "name": "banana_image_generation",
                        # 模拟客户端传入已转义为 Unicode 的 JSON 字符串
                        "arguments": '{"prompt": "\\u7edd\\u7f8e\\u82b1\\u5ac1\\u5c11\\u5973", "use_sender_avatar": true}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_test_123",
            "name": "banana_image_generation",
            "content": "图片生成任务已提交",
        },
    ]

    browser_messages = build_browser_messages_for_tools(
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        parallel_tool_calls=False,
    )

    assistant_msg = next(m for m in browser_messages if m.get("role") == "assistant")
    content = assistant_msg["content"]

    # 验证 [Assistant Tool Calls] 被正确输出
    assert "[Assistant Tool Calls]" in content
    # 验证中文字符被正确还原为纯中文，没有被保留为 \\u7edd 乱码或被二次转义
    assert "绝美花嫁少女" in content
    assert "\\u7edd" not in content
    assert '\\"prompt\\"' not in content  # 不存在双重转义的双引号


def test_history_tool_calls_are_not_reparsed_as_fresh_calls():
    from app.services.tool_calling_prompts import build_browser_messages_for_tools

    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_old",
                    "type": "function",
                    "function": {"name": "run_code", "arguments": '{"code":"old"}'},
                }
            ],
        }
    ]
    history = next(
        message["content"]
        for message in build_browser_messages_for_tools(messages, TOOLS, "auto")
        if message["role"] == "assistant"
    )

    parsed = parse_tool_response(history, TOOLS)
    assert parsed["tool_calls"] == []
    assert parsed.get("parse_error") == "assistant_tool_history_echo"


def test_assistant_tool_history_label_in_normal_text_is_not_an_echo():
    raw = "[Assistant Tool Calls]\nThis heading describes the previous step."

    assert parse_tool_response(raw, TOOLS) == {
        "mode": "final",
        "content": raw,
        "tool_calls": [],
    }


def test_tool_result_sanitization_preserves_non_media_base64_data():
    from app.services.tool_calling_common import _sanitize_tool_result_content

    value = "token=" + "A" * 512
    assert _sanitize_tool_result_content(value) == value


def test_retry_context_unescapes_unicode_arguments():
    from app.services.tool_calling_validation_retry import _format_message_for_retry_context

    msg = {
        "role": "assistant",
        "content": "正在绘制中",
        "tool_calls": [
            {
                "id": "call_retry_1",
                "type": "function",
                "function": {
                    "name": "banana_image_generation",
                    "arguments": '{"prompt": "\\u7edd\\u7f8e\\u82b1\\u5ac1"}',
                },
            }
        ],
    }

    formatted = _format_message_for_retry_context(msg)
    assert "[Recent Assistant Tool Calls]" in formatted
    assert "绝美花嫁" in formatted
    assert "\\u7edd" not in formatted


def test_generate_tool_few_shot_examples_with_obfuscation():
    from app.services.tool_calling_prompts import _generate_tool_few_shot_examples

    tools = [
        {
            "type": "function",
            "function": {
                "name": "calc_score",
                "parameters": {
                    "type": "object",
                    "properties": {"score": {"type": "integer"}},
                    "required": ["score"],
                },
            },
        }
    ]

    # 测试正常模式
    examples_normal = _generate_tool_few_shot_examples(tools, obfuscate=False)
    assert len(examples_normal) > 0

    # 测试混淆模式（验证 random / _inject_zero_width_noise 正确导入且运行无报错）
    examples_obfuscated = _generate_tool_few_shot_examples(tools, obfuscate=True)
    assert len(examples_obfuscated) > 0
