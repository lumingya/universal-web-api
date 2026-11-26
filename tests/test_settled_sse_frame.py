"""A settled final SSE frame must be emitted, not buffered forever.

Some sites omit the blank-line terminator on the last frame of a stream. The
generic SSE splitter buffers whatever follows the last ``\\n\\n`` in
``_pending_sse_chunk``, which is correct for a partial frame but wrong when the
tail already contains only complete ``data:`` payloads: that text then never
reaches the consumer.

Observed symptom: a reply stopped mid-tag at ``[/wait`` or
``[waitForCompletion``, so the downstream tool-call parser saw a half-written
parameter and reported ``waitForCompletion must be a boolean``.

DeepSeek and Qwen parsers already flush at this point. These tests pin the same
behaviour for the ChatGPT parser, plus the "still growing, keep buffering" case
that must not regress.
"""
from __future__ import annotations

import pytest

from app.core.parsers.chatgpt_parser import ChatGPTParser


def _frame(payload: str) -> str:
    return "data: " + payload


def test_settled_single_json_frame_is_recognised():
    assert ChatGPTParser._is_settled_sse_frame(_frame('{"v": "hello"}')) is True


def test_settled_done_sentinel_is_recognised():
    assert ChatGPTParser._is_settled_sse_frame("data: [DONE]") is True


def test_settled_frame_with_event_line_is_recognised():
    block = "event: delta\n" + _frame('{"v": "hello"}')
    assert ChatGPTParser._is_settled_sse_frame(block) is True


def test_partial_json_frame_is_not_settled():
    assert ChatGPTParser._is_settled_sse_frame(_frame('{"v": "hel')) is False


def test_empty_block_is_not_settled():
    assert ChatGPTParser._is_settled_sse_frame("") is False
    assert ChatGPTParser._is_settled_sse_frame("   ") is False


def test_block_without_data_lines_is_not_settled():
    assert ChatGPTParser._is_settled_sse_frame("event: delta") is False


def test_blank_data_lines_alone_are_not_settled():
    assert ChatGPTParser._is_settled_sse_frame("data:") is False


def test_final_complete_frame_without_terminator_is_flushed():
    """The reported case: complete payload, no trailing blank line."""
    parser = ChatGPTParser()
    body = _frame('{"v": "hello"}') + "\n" + _frame('{"v": " world"}')

    emitted = parser._extract_complete_sse_chunk(body)

    # Before the fix both frames stayed in _pending_sse_chunk and the caller
    # saw an empty string; the tail was lost when the stream ended.
    assert emitted.strip() != ""
    assert '"hello"' in emitted
    assert '" world"' in emitted
    assert parser._pending_sse_chunk == ""


def test_partial_final_frame_is_still_buffered():
    """A frame whose payload is incomplete must not be emitted."""
    parser = ChatGPTParser()
    body = _frame('{"v": "hello"}') + "\n\n" + _frame('{"v": "wor')

    emitted = parser._extract_complete_sse_chunk(body)

    # The terminated frame goes out; the partial one waits for its tail.
    assert '"hello"' in emitted
    assert '"wor' not in emitted
    assert '"wor' in parser._pending_sse_chunk


def test_unterminated_block_with_partial_payload_stays_buffered():
    """Two data lines with no separator form one event; a bad one blocks it.

    Per the SSE spec, consecutive ``data:`` lines belong to the same event, so
    an unterminated block is only safe to flush when *every* payload in it is
    complete. This pins that the fix does not start emitting half events.
    """
    parser = ChatGPTParser()
    body = _frame('{"v": "hello"}') + "\n" + _frame('{"v": "wor')

    emitted = parser._extract_complete_sse_chunk(body)

    assert emitted == ""
    assert '"wor' in parser._pending_sse_chunk


def test_pending_partial_frame_completes_on_the_next_chunk():
    parser = ChatGPTParser()
    parser._extract_complete_sse_chunk(_frame('{"v": "hel'))

    emitted = parser._extract_complete_sse_chunk('lo"}')

    assert '"hello"' in emitted
    assert parser._pending_sse_chunk == ""


def test_normal_terminated_stream_is_unaffected():
    """Regression: a stream that does end with a blank line behaves as before."""
    parser = ChatGPTParser()
    body = _frame('{"v": "a"}') + "\n\n" + _frame('{"v": "b"}') + "\n\n"

    emitted = parser._extract_complete_sse_chunk(body)

    assert '"a"' in emitted
    assert '"b"' in emitted
    assert parser._pending_sse_chunk == ""


@pytest.mark.parametrize(
    "payload,expected",
    [
        ('{"v": "x"}', True),
        ('{"v": "x"', False),
        ('[DONE]', True),
        ('', False),
    ],
)
def test_is_settled_sse_frame_matrix(payload, expected):
    assert ChatGPTParser._is_settled_sse_frame("data: " + payload) is expected
