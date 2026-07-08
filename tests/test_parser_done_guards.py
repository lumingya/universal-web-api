import json

import pytest

from app.core.parsers.doubao_parser import DoubaoParser
from app.core.parsers.mimo_parser import MimoParser
from app.core.parsers.mimo_runtime_parser import MimoParser as RuntimeMimoParser
from app.core.parsers.qwen_parser import QwenParser


def _sse(payload=None, *, event=""):
    lines = []
    if event:
        lines.append(f"event: {event}")
    if payload is not None:
        lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


@pytest.mark.parametrize("parser_cls", [MimoParser, RuntimeMimoParser])
def test_mimo_finish_is_ignored_until_visible_text_arrives(parser_cls):
    parser = parser_cls()
    stream = ""

    stream += _sse({"type": "text", "content": "<think>reasoning only"}, event="message")
    thinking = parser.parse_chunk(stream)
    assert thinking["content"] == ""
    assert thinking["done"] is False

    stream += _sse({"content": "[DONE]"}, event="finish")
    early_finish = parser.parse_chunk(stream)
    assert early_finish["content"] == ""
    assert early_finish["done"] is False

    stream += _sse({"type": "text", "content": "</think>visible answer"}, event="message")
    visible = parser.parse_chunk(stream)
    assert visible["content"] == "visible answer"
    assert visible["done"] is False

    stream += _sse({"content": "[DONE]"}, event="finish")
    final_finish = parser.parse_chunk(stream)
    assert final_finish["content"] == ""
    assert final_finish["done"] is True


def test_qwen_empty_answer_finished_is_ignored_until_answer_text_arrives():
    parser = QwenParser()
    stream = ""

    stream += _sse({"choices": [{"delta": {"phase": "answer", "status": "finished"}}]})
    early_finish = parser.parse_chunk(stream)
    assert early_finish["content"] == ""
    assert early_finish["done"] is False

    stream += _sse({"choices": [{"delta": {"phase": "answer", "content": "answer"}}]})
    answer = parser.parse_chunk(stream)
    assert answer["content"] == "answer"
    assert answer["done"] is False

    stream += _sse({"choices": [{"delta": {"phase": "answer", "status": "finished"}}]})
    final_finish = parser.parse_chunk(stream)
    assert final_finish["content"] == ""
    assert final_finish["done"] is True


def test_doubao_done_only_event_is_ignored_until_visible_text_arrives():
    parser = DoubaoParser()
    stream = ""

    stream += _sse({}, event="STREAM_FINISH")
    early_finish = parser.parse_chunk(stream)
    assert early_finish["content"] == ""
    assert early_finish["done"] is False

    stream += _sse({"text": "answer"}, event="CHUNK_DELTA")
    answer = parser.parse_chunk(stream)
    assert answer["content"] == "answer"
    assert answer["done"] is False

    stream += _sse({}, event="STREAM_FINISH")
    final_finish = parser.parse_chunk(stream)
    assert final_finish["content"] == ""
    assert final_finish["done"] is True


def test_doubao_finish_frame_with_text_can_complete_immediately():
    parser = DoubaoParser()

    result = parser.parse_chunk(
        _sse(
            {
                "msg_finish_attr": {
                    "brief": "brief answer",
                }
            },
            event="SSE_REPLY_END",
        )
    )

    assert result["content"] == "brief answer"
    assert result["done"] is True
