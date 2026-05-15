from app.core.parsers.grok_parser import GrokParser


def _line(payload: str) -> str:
    return payload + "\n"


def test_grok_parser_streams_final_tokens_and_marks_done_on_model_response():
    parser = GrokParser()

    chunk1 = (
        _line('{"result":{"token":"Thinking about your request","isThinking":true,"messageTag":"header"}}')
        + _line('{"result":{"token":"Hello","isThinking":false,"messageTag":"final"}}')
        + _line('{"result":{"token":" world","isThinking":false,"messageTag":"final"}}')
    )

    result1 = parser.parse_chunk(chunk1)

    assert result1["content"] == "Hello world"
    assert result1["done"] is False
    assert result1["images"] == []

    chunk2 = chunk1 + _line(
        '{"result":{"token":"","isThinking":false,"isSoftStop":true}}'
    ) + _line(
        '{"result":{"modelResponse":{"sender":"ASSISTANT","partial":false,"message":"Hello world"}}}'
    )

    result2 = parser.parse_chunk(chunk2)

    assert result2["content"] == ""
    assert result2["done"] is True
    assert result2["images"] == []


def test_grok_parser_falls_back_to_model_response_and_extracts_images():
    parser = GrokParser()

    body = _line(
        '{"result":{"modelResponse":{"sender":"ASSISTANT","partial":false,"message":"Image ready","generatedImageUrls":["https://assets.grok.com/users/demo/generated/abc/image.jpg"]}}}'
    )

    result = parser.parse_chunk(body)

    assert result["content"] == "Image ready"
    assert result["done"] is True
    assert len(result["images"]) == 1
    assert result["images"][0]["media_type"] == "image"
    assert result["images"][0]["kind"] == "url"
    assert result["images"][0]["url"].endswith("/generated/abc/image.jpg")


def test_grok_parser_media_generation_state_detects_pending_image_jobs():
    parser = GrokParser()

    raw = _line(
        '{"result":{"progressReport":{"category":"PROGRESS_REPORT_CATEGORY_IMAGE_GENERATION","state":"PROGRESS_REPORT_STATUS_PENDING","message":"Generating image"}}}'
    )

    state = parser.get_media_generation_state(raw_response=raw)

    assert state["pending"] is True
    assert state["media_type"] == "image"
    assert state["wait_timeout_seconds"] == 120.0


def test_grok_parser_supports_nested_response_payload_shape():
    parser = GrokParser()

    raw = _line(
        '{"result":{"response":{"token":"你好","isThinking":false,"messageTag":"final","responseId":"r1"}}}'
    ) + _line(
        '{"result":{"response":{"modelResponse":{"sender":"ASSISTANT","partial":false,"message":"你好，世界"}}}}'
    )

    result = parser.parse_chunk(raw)

    assert result["content"] == "你好，世界"
    assert result["done"] is True


def test_grok_parser_accepts_last_json_line_without_trailing_newline():
    parser = GrokParser()

    raw = (
        _line('{"result":{"response":{"token":"你好","isThinking":false,"messageTag":"final"}}}')
        + '{"result":{"response":{"modelResponse":{"sender":"ASSISTANT","partial":false,"message":"你好"}}}}'
    )

    result = parser.parse_chunk(raw)

    assert result["content"] == "你好"
    assert result["done"] is True
