import json

from app.core.parsers.deepseek_parser import DeepSeekParser


def _sse(payload=None, *, event=""):
    lines = []
    if event:
        lines.append(f"event: {event}")
    if payload is not None:
        lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def test_search_stage_finished_status_does_not_end_stream_before_response_text():
    parser = DeepSeekParser()

    result = parser.parse_chunk(
        _sse(
            {
                "v": {
                    "response": {
                        "fragments": [{"type": "SEARCH", "content": "searching..."}],
                        "quasi_status": "FINISHED",
                    }
                }
            }
        )
    )

    assert result["content"] == ""
    assert result["done"] is False


def test_tool_search_fragment_finished_status_does_not_end_stream_before_response_text():
    parser = DeepSeekParser()
    stream = _sse(
        {
            "p": "response/fragments",
            "o": "APPEND",
            "v": [{"type": "TOOL_SEARCH", "content": ""}],
        }
    )

    result = parser.parse_chunk(
        stream
        + _sse(
            {
                "p": "response/fragments/-1/status",
                "v": "FINISHED",
            }
        )
    )

    assert result["content"] == ""
    assert result["done"] is False


def test_finish_event_is_ignored_until_visible_response_text_arrives():
    parser = DeepSeekParser()
    stream = ""

    stream += _sse(event="finish")
    early_finish = parser.parse_chunk(stream)
    assert early_finish["content"] == ""
    assert early_finish["done"] is False

    stream += _sse(
        {
            "p": "response/fragments",
            "o": "APPEND",
            "v": [{"type": "RESPONSE", "content": "final answer"}],
        }
    )
    response = parser.parse_chunk(stream)
    assert response["content"] == "final answer"
    assert response["done"] is False

    stream += _sse(event="finish")
    final_finish = parser.parse_chunk(stream)
    assert final_finish["content"] == ""
    assert final_finish["done"] is True


def test_response_finished_status_still_ends_after_response_text():
    parser = DeepSeekParser()

    result = parser.parse_chunk(
        _sse(
            {
                "v": {
                    "response": {
                        "fragments": [{"type": "RESPONSE", "content": "done answer"}],
                        "status": "FINISHED",
                    }
                }
            }
        )
    )

    assert result["content"] == "done answer"
    assert result["done"] is True
