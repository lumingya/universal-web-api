from app.core.parsers.glm_parser import GLMParser


def test_glm_parser_does_not_finish_on_think_or_tool_finish_before_text():
    parser = GLMParser()

    raw = (
        'data: {"parts":[{"content":[{"type":"think","think":"思考中"}],"status":"finish"}],"status":"init"}\n\n'
        'data: {"parts":[{"content":[{"type":"tool_calls","tool_calls":{"name":"finish","arguments":"{}"}}],"status":"finish"}],"status":"init"}\n\n'
        'data: {"parts":[{"content":[{"type":"text","text":"# 《青云志·烟雨长安"}],"status":"init"}],"status":"init"}\n\n'
    )

    result = parser.parse_chunk(raw)

    assert result["content"] == "# 《青云志·烟雨长安"
    assert result["done"] is False
    assert result["error"] is None


def test_glm_parser_finishes_when_visible_text_part_finishes():
    parser = GLMParser()

    first_raw = 'data: {"parts":[{"content":[{"type":"text","text":"你好"}],"status":"init"}],"status":"init"}\n\n'
    second_raw = first_raw + 'data: {"parts":[{"content":[{"type":"text","text":"你好，世界"}],"status":"finish"}],"status":"init"}\n\n'

    first = parser.parse_chunk(first_raw)
    second = parser.parse_chunk(second_raw)

    assert first["content"] == "你好"
    assert first["done"] is False
    assert second["content"] == "，世界"
    assert second["done"] is True
