from app.services.tool_calling_validation_retry import _detect_malformed_tool_payload


def test_detect_malformed_tool_payload_ignores_generic_business_xml() -> None:
    text = '<think>ok</think>\n<segment>hi</segment>\n<meme query="调皮"/>'
    assert _detect_malformed_tool_payload(text, allowed_tool_names={"web_search"}) == ""


def test_detect_malformed_tool_payload_keeps_adapter_xml_detection() -> None:
    text = '<adapter_calls><call name="web_search"></call></adapter_calls>'
    reason = _detect_malformed_tool_payload(text, allowed_tool_names={"web_search"})
    assert "XML-style tool call" in reason


def test_detect_malformed_tool_payload_keeps_declared_short_tag_compat() -> None:
    text = '<web_search query="latest ai news"/>'
    reason = _detect_malformed_tool_payload(text, allowed_tool_names={"web_search"})
    assert "XML-style tool call" in reason

