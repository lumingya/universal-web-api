import json

from app.core.parsers.gemini_parser import GeminiParser


def _wrap_gemini_blocks(*inners):
    parts = [")]}\'", ""]
    for inner in inners:
        outer = [["wrb.fr", None, json.dumps(inner, ensure_ascii=False)]]
        json_block = json.dumps(outer, ensure_ascii=False)
        parts.append(str(len(json_block)))
        parts.append(json_block)
    return "\n".join(parts) + "\n"


def test_gemini_parser_ignores_reasoning_only_candidates():
    parser = GeminiParser()

    content_item = [None] * 38
    content_item[0] = "rc_reasoning"
    content_item[1] = [""]
    content_item[37] = [
        [
            "**Defining the Core Elements**\n\n"
            "I've established the story's ancient Chinese setting and helpful assistant persona."
        ]
    ]
    raw = _wrap_gemini_blocks([None, None, None, None, [content_item]])

    result = parser.parse_chunk(raw)

    assert result["content"] == ""
    assert result["done"] is False
    assert result["error"] is None


def test_gemini_parser_prefers_visible_answer_over_reasoning_fallback():
    parser = GeminiParser()

    content_item = [None] * 38
    content_item[0] = "rc_answer"
    content_item[1] = ["<render template=\"手账\">你好，主人</render>"]
    content_item[37] = [
        [
            "**Defining the Core Elements**\n\n"
            "I've established the story's ancient Chinese setting and helpful assistant persona."
        ],
    ]
    raw = _wrap_gemini_blocks([None, None, None, None, [content_item]])

    result = parser.parse_chunk(raw)

    assert result["content"] == "<render template=\"手账\">你好，主人</render>"
    assert result["done"] is False
    assert result["error"] is None


def test_gemini_parser_suppresses_mixed_language_reasoning_until_visible_answer():
    parser = GeminiParser()

    reasoning_item = [None] * 38
    reasoning_item[0] = "rc_reasoning"
    reasoning_item[1] = [""]
    reasoning_item[37] = [
        [
            "I've crafted characters, setting, and plot; now I am drafting "
            "the narrative's 古风 style with title and scene details."
        ]
    ]
    reasoning_raw = _wrap_gemini_blocks([None, None, None, None, [reasoning_item]])

    answer_item = [None] * 38
    answer_item[0] = "rc_answer"
    answer_item[1] = ["## 《落花无言》\n\n第一段"]
    answer_item[37] = reasoning_item[37]
    answer_raw = _wrap_gemini_blocks([None, None, None, None, [answer_item]])

    first = parser.parse_chunk(reasoning_raw)
    second = parser.parse_chunk(answer_raw)

    assert first["content"] == ""
    assert first["done"] is False
    assert first["error"] is None

    assert second["content"] == "## 《落花无言》\n\n第一段"
    assert second["done"] is False
    assert second["error"] is None


def test_gemini_parser_ignores_structural_status_text_without_direct_answer():
    parser = GeminiParser()

    content_item = [None] * 38
    content_item[0] = "rc_reasoning"
    content_item[1] = [""]
    content_item[37] = [
        [
            "The ancient Chinese-style draft now includes title, sections, "
            "and formatting for the setting, and initial scene."
        ]
    ]
    raw = _wrap_gemini_blocks([None, None, None, None, [content_item]])

    result = parser.parse_chunk(raw)

    assert result["content"] == ""
    assert result["done"] is False
    assert result["error"] is None
