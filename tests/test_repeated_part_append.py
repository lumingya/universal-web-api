"""A repeated append patch must not be applied twice.

The site re-sends the same ``append`` patch for a message part: overlapping SSE
frames, or a retry after a transient failure. Appending both copies produces a
duplicated sentence in the UI (``X X`` where the model wrote ``X`` once).

The dedupe only compares against the *immediately preceding* append to the same
part. That is deliberate: a model that genuinely repeats a word or punctuation
mark must still have it delivered.
"""
from __future__ import annotations

import pytest

from app.core.parsers.chatgpt_parser import ChatGPTParser


def _parser() -> ChatGPTParser:
    parser = ChatGPTParser()
    parser._message_parts = {}
    return parser


def _text(parser: ChatGPTParser, index: int = 0) -> str:
    return parser._message_parts.get(index, "")


def test_identical_consecutive_append_is_skipped():
    parser = _parser()
    parser._append_message_part(0, "hello")
    parser._append_message_part(0, "hello")

    assert _text(parser) == "hello"


def test_repeated_patch_does_not_duplicate_a_sentence():
    """The reported shape: one phrase appearing twice in the UI."""
    parser = _parser()
    chunk = "最新回响确认：当前仓库是 `s3hq4y"
    parser._append_message_part(0, chunk)
    parser._append_message_part(0, chunk)

    assert _text(parser) == chunk


def test_different_appends_still_accumulate():
    parser = _parser()
    parser._append_message_part(0, "hello")
    parser._append_message_part(0, " world")

    assert _text(parser) == "hello world"


def test_triple_identical_append_lands_once():
    parser = _parser()
    parser._append_message_part(0, "very ")
    parser._append_message_part(0, "very ")
    parser._append_message_part(0, "very ")

    # First one lands; the two identical repeats that follow are dropped.
    assert _text(parser) == "very "


def test_repeat_is_kept_when_separated_by_other_text():
    parser = _parser()
    parser._append_message_part(0, "ha")
    parser._append_message_part(0, "ha")
    parser._append_message_part(0, "!")
    # The previous append for this part is now "!", so "ha" lands again.
    parser._append_message_part(0, "ha")

    assert _text(parser) == "ha!ha"


def test_dedupe_is_per_part_index():
    parser = _parser()
    parser._append_message_part(0, "same")
    parser._append_message_part(1, "same")

    assert _text(parser, 0) == "same"
    assert _text(parser, 1) == "same"


def test_set_message_part_clears_dedupe_memory():
    parser = _parser()
    parser._append_message_part(0, "abc")
    parser._set_message_part(0, "xyz")
    # A wholesale replacement invalidates the previous append, so the same
    # text can be appended again afterwards.
    parser._append_message_part(0, "abc")

    assert _text(parser) == "xyzabc"


def test_empty_append_is_ignored():
    parser = _parser()
    parser._append_message_part(0, "")
    parser._append_message_part(0, "a")
    parser._append_message_part(0, "")

    assert _text(parser) == "a"


def test_reset_clears_dedupe_memory():
    parser = _parser()
    parser._append_message_part(0, "abc")
    parser.reset()
    parser._append_message_part(0, "abc")

    assert _text(parser) == "abc"


def test_dedupe_memory_tracks_raw_text_not_rendered_text():
    parser = _parser()
    parser._append_message_part(0, "a")
    parser._append_message_part(0, "b")
    # The second "a" is not the immediately preceding append ("b"), so it lands.
    parser._append_message_part(0, "a")

    assert _text(parser) == "aba"


@pytest.mark.parametrize(
    "appends,expected",
    [
        (["x"], "x"),
        (["x", "x"], "x"),
        (["x", "x", "x"], "x"),
        (["x", "y"], "xy"),
        (["x", "y", "x"], "xyx"),
        (["ab", "ab", "c"], "abc"),
        (["", "x"], "x"),
        (["x", ""], "x"),
    ],
)
def test_append_matrix(appends, expected):
    parser = _parser()
    for value in appends:
        parser._append_message_part(0, value)
    assert _text(parser) == expected
