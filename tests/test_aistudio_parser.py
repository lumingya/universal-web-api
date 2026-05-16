import json

from app.core.parsers.aistudio_parser import AIStudioParser


def _make_aistudio_block(text: str, *, done: bool = False, thinking: bool = False):
    content_arr = ["segment", text]
    if thinking:
        while len(content_arr) <= 12:
            content_arr.append(None)
        content_arr[12] = 1

    level4 = [content_arr]
    level3 = [level4]
    level2 = [level3]
    if done:
        level2.append(1)
    level1 = [level2]
    return [level1]


def test_aistudio_parser_tolerates_incomplete_json_without_error():
    parser = AIStudioParser()

    result = parser.parse_chunk('[[[[[[\"segment\",\"半包')

    assert result["content"] == ""
    assert result["done"] is False
    assert result["error"] is None


def test_aistudio_parser_ignores_thinking_and_returns_incremental_text():
    parser = AIStudioParser()

    first_payload = json.dumps([[ _make_aistudio_block("思考中", thinking=True), _make_aistudio_block("你好") ]], ensure_ascii=False)
    second_payload = json.dumps([[ _make_aistudio_block("思考中", thinking=True), _make_aistudio_block("你好，世界", done=True) ]], ensure_ascii=False)

    first = parser.parse_chunk(first_payload)
    second = parser.parse_chunk(second_payload)

    assert first["content"] == "你好"
    assert first["done"] is False
    assert first["error"] is None

    assert second["content"] == "，世界"
    assert second["done"] is True
    assert second["error"] is None


def test_aistudio_parser_streams_visible_text_from_incomplete_raw_body():
    parser = AIStudioParser()

    first_raw = (
        '[[[[[[[null,"**Initiating Narrative Creation**",null,null,null,null,null,'
        'null,null,null,null,null,1]],"model"]]],null,[48,null,48,null,[[1,48]]],'
        'null,null,null,null,"token"],[[[[[[null,"这是一篇"]],"model"]]],null,'
        '[48,18,1502,null,[[1,48]]]'
    )
    second_raw = first_raw + (
        ',null,null,null,null,"token"],[[[[[[null,"古风短篇小说"]],"model"]]],null,'
        '[48,18,1502,null,[[1,48]]]'
    )

    first = parser.parse_chunk(first_raw)
    second = parser.parse_chunk(second_raw)

    assert first["content"] == "这是一篇"
    assert first["done"] is False
    assert first["error"] is None

    assert second["content"] == "古风短篇小说"
    assert second["done"] is False
    assert second["error"] is None
