"""Rewritten streaming snapshots must not re-emit delivered text.

The SSE consumer is append-only: once a chunk is emitted it can never be
retracted. Both the generic snapshot helper and the ChatGPT-specific delta
computation used to assume the raw snapshot only ever grows, so a snapshot
that was rewritten in place (different middle, or shorter) produced duplicate
output -- whole sentences and commands appearing twice in the UI.

These tests pin the delivery baseline:

* ``ResponseParser._prepare_incremental_raw_response`` must not call ``reset()``
  on a non-prefix snapshot; it must hand the snapshot through so the parser can
  decide what is genuinely new.
* ``ChatGPTParser._compute_delta`` must compare against what was delivered, not
  against the previous snapshot.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from app.core.parsers.base import ResponseParser
from app.core.parsers.chatgpt_parser import ChatGPTParser


class _RecordingParser(ResponseParser):
    """Minimal parser that records reset() calls and echoes the delta."""

    def __init__(self) -> None:
        self.resets = 0
        self._last_raw_length = 0
        self._last_raw_response = ""

    def parse_chunk(self, raw_response: str) -> Dict[str, Any]:
        new_data = self._prepare_incremental_raw_response(raw_response)
        return {"content": new_data, "done": False, "error": None}

    def reset(self) -> None:
        self.resets += 1


def test_prefix_extension_returns_only_the_tail():
    parser = _RecordingParser()
    assert parser.parse_chunk("abc")["content"] == "abc"
    assert parser.parse_chunk("abcdef")["content"] == "def"
    assert parser.resets == 0


def test_identical_snapshot_returns_nothing():
    parser = _RecordingParser()
    parser.parse_chunk("abc")
    assert parser.parse_chunk("abc")["content"] == ""
    assert parser.resets == 0


def test_rewritten_snapshot_does_not_reset_the_parser():
    """A non-prefix snapshot must reach the parser with state intact."""
    parser = _RecordingParser()
    parser.parse_chunk("hello world")
    result = parser.parse_chunk("hello WORLD")

    # Before the fix this path called reset(), which made every parser replay
    # its whole state and re-emit text the consumer already had.
    assert parser.resets == 0
    assert result["content"] == "hello WORLD"


def test_shorter_snapshot_does_not_reset_the_parser():
    parser = _RecordingParser()
    parser.parse_chunk("a much longer body")
    result = parser.parse_chunk("short")
    assert parser.resets == 0
    assert result["content"] == "short"


# --------------------------------------------------------------------------- #
# ChatGPTParser delta: baseline is what was delivered, not the last snapshot
# --------------------------------------------------------------------------- #


def _delta(parser: ChatGPTParser, current: str) -> str:
    return parser._compute_delta(parser._delivered_content, current)


def test_chatgpt_first_delta_returns_everything():
    parser = ChatGPTParser()
    assert _delta(parser, "hello") == "hello"
    assert parser._delivered_content == "hello"


def test_chatgpt_append_emits_only_the_growth():
    parser = ChatGPTParser()
    assert _delta(parser, "hello") == "hello"
    assert _delta(parser, "hello world") == " world"
    assert parser._delivered_content == "hello world"


def test_chatgpt_unchanged_snapshot_emits_nothing():
    parser = ChatGPTParser()
    _delta(parser, "hello")
    assert _delta(parser, "hello") == ""
    assert parser._delivered_content == "hello"


def test_chatgpt_in_place_rewrite_emits_nothing():
    """A rewrite that is not an append must be silent, not duplicated."""
    parser = ChatGPTParser()
    _delta(parser, "A B")
    # The site rewrote the middle: "A B" -> "A' B". The old implementation
    # sliced at the longest common prefix and emitted "A' B" again, so the UI
    # showed the command twice.
    assert _delta(parser, "A' B") == ""
    # The consumer cannot retract, so the baseline stays at what it holds.
    assert parser._delivered_content == "A B"


def test_chatgpt_rewrite_then_append_stays_silent():
    """After a rewrite the baseline is stale; further growth is not appended.

    This is the deliberate trade-off: emitting ``more`` would splice text onto
    a baseline the consumer no longer matches, producing the duplication this
    fix removes. Silence keeps the stream consistent.
    """
    parser = ChatGPTParser()
    _delta(parser, "A B")
    assert _delta(parser, "A' B") == ""
    assert _delta(parser, "A' B more") == ""
    assert parser._delivered_content == "A B"


def test_chatgpt_append_after_identical_rewrite_still_works():
    """A rewrite that reproduces the delivered text is not a divergence."""
    parser = ChatGPTParser()
    _delta(parser, "hello")
    # Same content, and the next snapshot genuinely extends it.
    assert _delta(parser, "hello world") == " world"


def test_chatgpt_shorter_snapshot_emits_nothing():
    parser = ChatGPTParser()
    _delta(parser, "long delivered text")
    assert _delta(parser, "short") == ""
    assert parser._delivered_content == "long delivered text"


def test_chatgpt_reset_clears_delivered_baseline():
    parser = ChatGPTParser()
    _delta(parser, "hello")
    parser.reset()
    assert parser._delivered_content == ""
    assert _delta(parser, "hello") == "hello"


def test_chatgpt_delta_is_an_instance_method():
    """The baseline lives on the instance, so this cannot stay a staticmethod."""
    assert not isinstance(ChatGPTParser.__dict__.get("_compute_delta"), staticmethod)


@pytest.mark.parametrize(
    "previous,current,expected",
    [
        ("", "abc", "abc"),
        ("abc", "abcdef", "def"),
        ("abc", "abc", ""),
        ("abc", "xyz", ""),
        ("abcdef", "abc", ""),
    ],
)
def test_chatgpt_delta_matrix(previous, current, expected):
    parser = ChatGPTParser()
    parser._delivered_content = previous
    assert parser._compute_delta(previous, current) == expected


# --------------------------------------------------------------------------- #
# Cross-parser regression matrix: Qwen / DeepSeek / Gemini
# Proves dropping reset() on a rewritten snapshot prevents duplicate emissions
# and preserves normal incremental streaming across other parsers.
# --------------------------------------------------------------------------- #

from app.core.parsers.deepseek_parser import DeepSeekParser
from app.core.parsers.gemini_parser import GeminiParser
from app.core.parsers.qwen_parser import QwenParser


def test_deepseek_rewritten_snapshot_does_not_duplicate_delivered_fragment():
    """When an earlier SSE header/THINK fragment changes, RESPONSE is not re-sent."""
    parser = DeepSeekParser()
    snap1 = (
        'event: update_session\ndata: {"updated_at": 100}\n\n'
        'data: {"p":"response/fragments","v":['
        '{"id":1,"type":"THINK","content":"plan v1"},'
        '{"id":2,"type":"RESPONSE","content":"Hello"}'
        ']}\n\n'
    )
    snap2 = (
        'event: update_session\ndata: {"updated_at": 200}\n\n'
        'data: {"p":"response/fragments","v":['
        '{"id":1,"type":"THINK","content":"plan v2"},'
        '{"id":2,"type":"RESPONSE","content":"Hello world"}'
        ']}\n\n'
    )

    first = parser.parse_chunk(snap1)
    second = parser.parse_chunk(snap2)

    assert first["content"] == "Hello"
    assert second["content"] == " world"
    assert first["content"] + second["content"] == "Hello world"


def test_gemini_rewritten_snapshot_does_not_duplicate_cumulative_answer():
    """When the cumulative StreamGenerate envelope changes prefix, _last_len holds."""
    parser = GeminiParser()
    snap1 = (
        ')]}\'\n\n90\n'
        '[["wrb.fr", null, "[null, [\\"c_1\\", \\"r_1\\"], null, null, [[\\"rc_1\\", [\\"你好\\"]]]]"]]\n'
    )
    snap2 = (
        ')]}\'\n\n93\n'
        '[["wrb.fr", null, "[null, [\\"c_2\\", \\"r_2\\"], null, null, [[\\"rc_1\\", [\\"你好，世界！\\"]]]]"]]\n'
    )

    first = parser.parse_chunk(snap1)
    second = parser.parse_chunk(snap2)

    assert first["content"] == "你好"
    assert second["content"] == "，世界！"
    assert first["content"] + second["content"] == "你好，世界！"


def test_qwen_rewritten_snapshot_does_not_duplicate_accumulated_reasoning():
    """When Qwen's envelope is rewritten, _accumulated_reasoning is not reset."""
    parser = QwenParser()
    snap1 = (
        'data: {"response.created": {"chat_id": "c_1", "response_id": "r_1"}}\n\n'
        'data: {"choices": [{"delta": {"role": "assistant", "content": "", '
        '"phase": "thinking_summary", "extra": {"summary_thought": {"content": ["第一步思考"]}}}}]}\n\n'
    )
    snap2 = (
        'data: {"response.created": {"chat_id": "c_2", "response_id": "r_2"}}\n\n'
        'data: {"choices": [{"delta": {"role": "assistant", "content": "", '
        '"phase": "thinking_summary", "extra": {"summary_thought": {"content": ["第一步思考\\n\\n第二步思考"]}}}}]}\n\n'
        'data: {"choices": [{"delta": {"role": "assistant", "content": "你好，世界！", '
        '"phase": "answer", "status": "finished"}}]}\n\n'
    )

    first = parser.parse_chunk(snap1)
    second = parser.parse_chunk(snap2)

    assert first["reasoning_content"] == "第一步思考"
    assert second["reasoning_content"] == "\n\n第二步思考"
    assert first["reasoning_content"] + second["reasoning_content"] == "第一步思考\n\n第二步思考"
    assert second["content"] == "你好，世界！"
    assert second["done"] is True
