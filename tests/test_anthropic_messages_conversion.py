"""`_anthropic_messages_to_openai` 的表征测试。

Anthropic 消息格式 → OpenAI 消息格式的转换器（CC 59 / 132 行，其中 126 行是那个
大循环）。重构前它在 `tests/test_attachments.py` 里只有 1 处引用。

它是纯函数（无 I/O、无全局状态），所以可以直接喂消息数组断言输出。测试按循环体
内的实际分段组织：

1. 内容为非列表的两种快路径（字符串 / 其它类型）
2. thinking 块被丢弃
3. tool_use（assistant）→ tool_calls
4. tool_result（user）→ role="tool" 消息，以及不能跟随时退化成 user 文本
5. 普通块走 `_anthropic_block_to_openai_part`，失败则兜底序列化
6. 按 role 组装输出：assistant / user / 其它
"""

import pytest

from app.api.anthropic_routes import _anthropic_messages_to_openai

# --------------------------------------------------------------------------
# 1. 内容为非列表的两种快路径
# --------------------------------------------------------------------------


@pytest.mark.parametrize("messages", [None, [], "", 0, {}, False, ()])
def test_falsy_input_returns_empty(messages):
    """`messages or []` 先做真值判断，所以所有假值都退化成空列表。"""
    assert _anthropic_messages_to_openai(messages) == []


def test_truthy_string_is_iterated_character_by_character():
    """真值字符串会被当序列逐字符遍历——每个字符都不是 dict，于是全被跳过。"""
    assert _anthropic_messages_to_openai("x") == []
    assert _anthropic_messages_to_openai("abc") == []


@pytest.mark.parametrize("messages", [42, 1.5, object(), True])
def test_truthy_non_iterable_raises(messages):
    """真值但不可迭代的输入会抛 TypeError——函数没有对输入类型做守卫。

    这是个既成事实（调用方总是传数组），测试把它钉住以免重构时误改语义。
    """
    with pytest.raises(TypeError):
        _anthropic_messages_to_openai(messages)


def test_string_content_is_passed_through():
    got = _anthropic_messages_to_openai([{"role": "user", "content": "hello"}])
    assert got == [{"role": "user", "content": "hello"}]


def test_role_is_lowered_and_stripped():
    got = _anthropic_messages_to_openai([{"role": "  USER  ", "content": "hi"}])
    assert got == [{"role": "user", "content": "hi"}]


def test_missing_role_defaults_to_user():
    got = _anthropic_messages_to_openai([{"content": "hi"}])
    assert got == [{"role": "user", "content": "hi"}]


def test_empty_string_content_kept_only_for_user_and_assistant():
    """空串内容：user / assistant 保留，其它 role 丢弃。"""
    assert _anthropic_messages_to_openai([{"role": "user", "content": ""}]) == [{"role": "user", "content": ""}]
    assert _anthropic_messages_to_openai([{"role": "assistant", "content": ""}]) == [{"role": "assistant", "content": ""}]
    assert _anthropic_messages_to_openai([{"role": "system", "content": ""}]) == []


def test_non_dict_entries_are_skipped():
    assert _anthropic_messages_to_openai(["x", 1, None, [], {"role": "user", "content": "ok"}]) == [
        {"role": "user", "content": "ok"}
    ]


def test_non_list_content_is_serialized():
    """content 不是 str 也不是 list 时走 `_serialize_content_value`。"""
    got = _anthropic_messages_to_openai([{"role": "user", "content": 42}])
    assert got == [{"role": "user", "content": "42"}]


def test_serialize_result_empty_falls_back_to_role_check():
    """序列化结果为空时，是否保留取决于 role 是否为 user / assistant。

    `content=None` 序列化成空串，但 role 是 user，所以仍保留一条空内容消息。
    """
    assert _anthropic_messages_to_openai([{"role": "user", "content": None}]) == [
        {"role": "user", "content": ""}
    ]
    assert _anthropic_messages_to_openai([{"role": "system", "content": None}]) == []


def test_dict_content_is_serialized():
    got = _anthropic_messages_to_openai([{"role": "user", "content": {"a": 1}}])
    assert got == [{"role": "user", "content": '{"a": 1}'}]


# --------------------------------------------------------------------------
# 2. thinking 块被丢弃
# --------------------------------------------------------------------------


@pytest.mark.parametrize("block_type", ["thinking", "redacted_thinking", " THINKING "])
def test_thinking_blocks_are_dropped(block_type):
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [
            {"type": block_type, "thinking": "secret reasoning", "signature": "base64sig"},
            {"type": "text", "text": "visible"},
        ]}
    ])
    assert got == [{"role": "assistant", "content": "visible"}]


def test_message_with_only_thinking_is_dropped_entirely():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "x"}]}
    ])
    assert got == []


def test_unknown_block_type_keeps_fallback_serialization():
    """未知类型**不**丢弃，而是兜底序列化成文本——与 thinking 的处理不同。"""
    got = _anthropic_messages_to_openai([
        {"role": "user", "content": [{"type": "mystery", "data": "keepme"}]}
    ])
    assert got == [{"role": "user", "content": '{"type": "mystery", "data": "keepme"}'}]


# --------------------------------------------------------------------------
# 3. tool_use（assistant）
# --------------------------------------------------------------------------


def test_tool_use_becomes_tool_call():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "x"}},
        ]}
    ])
    assert got == [{
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "tu_1",
            "type": "function",
            "function": {"name": "search", "arguments": '{"q": "x"}'},
        }],
    }]


def test_tool_use_without_id_gets_generated_one():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [{"type": "tool_use", "name": "search", "input": {}}]}
    ])
    call = got[0]["tool_calls"][0]
    assert call["id"].startswith("call_")
    assert len(call["id"]) == len("call_") + 12


def test_tool_use_non_dict_input_becomes_empty_object():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1", "name": "s", "input": "raw"}]}
    ])
    assert got[0]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_tool_use_ignored_for_non_assistant_role():
    """tool_use 块只在 role 为 assistant 时被识别，否则走普通块处理。"""
    got = _anthropic_messages_to_openai([
        {"role": "user", "content": [{"type": "tool_use", "id": "tu_1", "name": "search", "input": {}}]}
    ])
    assert got and "tool_calls" not in got[0]


def test_tool_call_preserves_unicode_in_arguments():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "搜索", "input": {"关键词": "中文"}},
        ]}
    ])
    args = got[0]["tool_calls"][0]["function"]["arguments"]
    assert "中文" in args and "\\u" not in args


def test_assistant_with_both_text_and_tool_call():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [
            {"type": "text", "text": "thinking out loud"},
            {"type": "tool_use", "id": "tu_1", "name": "search", "input": {}},
        ]}
    ])
    assert got[0]["content"] == "thinking out loud"
    assert len(got[0]["tool_calls"]) == 1


# --------------------------------------------------------------------------
# 4. tool_result（user）
# --------------------------------------------------------------------------


def test_tool_result_alone_is_dropped_without_preceding_tool_call():
    """tool_result 需要前面有过 assistant 的 tool_calls，否则 OpenAI 侧无法悬挂，
    会退化成 user 文本。"""
    got = _anthropic_messages_to_openai([
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "result text"},
        ]}
    ])
    assert [m["role"] for m in got] == ["user"]
    assert "result text" in got[0]["content"]


def test_tool_result_following_assistant_tool_call():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "search", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "the answer"},
        ]},
    ])
    assert [m["role"] for m in got] == ["assistant", "tool"]
    assert got[1]["tool_call_id"] == "tu_1"
    assert got[1]["name"] == "search"
    assert got[1]["content"] == "the answer"


def test_tool_result_unknown_id_gets_default_name():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1", "name": "search", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "nope", "content": "x"}]},
    ])
    # 未知 id 无法与前面的 tool_call 对应，退化成 user 文本
    assert [m["role"] for m in got] == ["assistant", "user"]


def test_tool_result_error_prefix():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1", "name": "s", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "boom", "is_error": True},
        ]},
    ])
    assert got[1]["content"].startswith("[tool_result_error]")
    assert "boom" in got[1]["content"]


def test_tool_result_multiline_text_is_joined():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1", "name": "s", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": [
            {"type": "text", "text": "line1"},
            {"type": "text", "text": "line2"},
        ]}]},
    ])
    assert got[1]["content"] == "line1\nline2"


def test_tool_result_with_image_attaches_marker_and_media():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1", "name": "screenshot", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": [
            {"type": "text", "text": "caption"},
            {"type": "image_url", "image_url": {"url": "https://x/a.png"}},
        ]}]},
    ])
    tool_msg = got[1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["content"].endswith("[tool_result_image_attached_below]")
    # 图片被挂到随后的 user 消息里
    user_msg = got[2]
    assert user_msg["role"] == "user"
    assert any(p.get("type") == "image_url" for p in user_msg["content"])
    assert "[Tool Result Media: screenshot (tu_1)]" in user_msg["content"][0]["text"]


def test_tool_result_ignored_for_non_user_role():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "x"},
        ]}
    ])
    assert got == [{"role": "assistant", "content": '{"type": "tool_result", "tool_use_id": "tu_1", "content": "x"}'}]


# --------------------------------------------------------------------------
# 5. 普通块与兜底
# --------------------------------------------------------------------------


def test_text_blocks_are_concatenated():
    got = _anthropic_messages_to_openai([
        {"role": "user", "content": [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]}
    ])
    assert got == [{"role": "user", "content": "ab"}]


def test_mixed_text_and_image_keeps_part_list():
    got = _anthropic_messages_to_openai([
        {"role": "user", "content": [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": "https://x/a.png"}},
        ]}
    ])
    content = got[0]["content"]
    assert isinstance(content, list) and len(content) == 2
    assert content[0] == {"type": "text", "text": "look"}


def test_non_dict_blocks_are_skipped():
    got = _anthropic_messages_to_openai([
        {"role": "user", "content": ["junk", 1, None, {"type": "text", "text": "ok"}]}
    ])
    assert got == [{"role": "user", "content": "ok"}]


def test_empty_block_list_drops_the_message():
    assert _anthropic_messages_to_openai([{"role": "user", "content": []}]) == []
    assert _anthropic_messages_to_openai([{"role": "system", "content": []}]) == []


def test_text_block_without_text_field_collapses_to_empty_and_is_dropped():
    """`{"type": "text"}` 缺 text 字段，转换出空文本，归一化后整体为空串，
    而空串内容在 user 分支会被丢弃——所以整条消息消失。"""
    assert _anthropic_messages_to_openai([{"role": "user", "content": [{"type": "text"}]}]) == []


# --------------------------------------------------------------------------
# 6. 按 role 组装输出
# --------------------------------------------------------------------------


def test_assistant_empty_content_without_tools_is_dropped():
    """空的 content 列表会被丢弃；但空的 content **字符串**会保留。

    两条路径判定不同：字符串走快路径（`if content or role in {...}`，assistant
    命中所以保留），列表走组装路径（`if content not in ("", [])`，空串被丢）。
    """
    assert _anthropic_messages_to_openai([{"role": "assistant", "content": []}]) == []
    assert _anthropic_messages_to_openai([{"role": "assistant", "content": ""}]) == [
        {"role": "assistant", "content": ""}
    ]


def test_assistant_empty_content_with_tools_becomes_none():
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1", "name": "s", "input": {}}]}
    ])
    assert got[0]["content"] is None


def test_other_roles_are_preserved():
    got = _anthropic_messages_to_openai([{"role": "system", "content": "you are helpful"}])
    assert got == [{"role": "system", "content": "you are helpful"}]


def test_message_order_is_preserved():
    got = _anthropic_messages_to_openai([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ])
    assert [m["role"] for m in got] == ["system", "user", "assistant", "user"]


def test_tool_name_registry_is_populated_across_messages():
    """tool_name_by_id 跨消息累积，所以后登记的 id 也能解析出工具名。"""
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "first", "input": {}}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "b", "name": "second", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b", "content": "r2"}]},
    ])
    tool_msgs = [m for m in got if m["role"] == "tool"]
    assert [(m["tool_call_id"], m["name"]) for m in tool_msgs] == [("b", "second")]


def test_tool_result_must_match_the_immediately_preceding_tool_call():
    """tool_result 只能挂在**紧邻的上一条** assistant tool_call 上。

    这里 tool_result 指向更早的 id "a"，但上一条 assistant 登记的是 "b"，
    于是无法悬挂，退化成 user 文本（工具名仍从注册表里查得到）。
    """
    got = _anthropic_messages_to_openai([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "first", "input": {}}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "b", "name": "second", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "r1"}]},
    ])
    assert [m["role"] for m in got] == ["assistant", "assistant", "user"]
    assert "first" in got[2]["content"]
    assert "r1" in got[2]["content"]
