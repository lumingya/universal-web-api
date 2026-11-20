from app.core.parsers.deepseek_parser import DeepSeekParser


def test_snapshot_updates_current_fragment_type_before_pathless_delta():
    parser = DeepSeekParser()
    first_raw = (
        'data: {"v":{"response":{"fragments":[{"type":"THINK","content":"thought"},{"type":"RESPONSE","content":"answer"}]}}}\n\n'
    )

    first = parser.parse_chunk(first_raw)
    assert first["content"] == "answer"

    patch_raw = (
        first_raw
        + 'data: {"p":"response/fragments/0/content","o":"APPEND","v":" more"}\n\n'
    )
    assert parser.parse_chunk(patch_raw)["content"] == ""

    snapshot_raw = patch_raw + (
        'data: {"p":"response/fragments","v":[{"type":"THINK","content":"thought more"},{"type":"RESPONSE","content":"answer"}]}\n\n'
    )
    snapshot = parser.parse_chunk(snapshot_raw)
    assert snapshot["content"] == ""

    pathless_response = parser.parse_chunk(snapshot_raw + 'data: {"v":" still visible"}\n\n')
    assert pathless_response["content"] == " still visible"
