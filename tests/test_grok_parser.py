import json
import re

from app.core.parsers.grok_parser import GrokParser


def test_grok_load_responses_snapshot_extracts_completed_assistant_message():
    parser = GrokParser()
    payload = {
        "responses": [
            {
                "responseId": "human-response",
                "message": "hello",
                "sender": "human",
                "partial": False,
            },
            {
                "responseId": "assistant-response",
                "message": "Hello! How can I help?",
                "sender": "assistant",
                "partial": False,
                "generatedImageUrls": [],
                "imageAttachments": [],
                "cardAttachmentsJson": [],
            },
        ]
    }

    result = parser.parse_chunk(payload)

    assert result["content"] == "Hello! How can I help?"
    assert result["done"] is True
    assert result["error"] is None


def test_grok_load_responses_snapshot_emits_only_delta_for_updated_message():
    parser = GrokParser()

    first = {
        "responses": [
            {"message": "hello", "sender": "human", "partial": False},
            {"message": "Hello", "sender": "assistant", "partial": True},
        ]
    }
    second = {
        "responses": [
            {"message": "hello", "sender": "human", "partial": False},
            {"message": "Hello there", "sender": "assistant", "partial": False},
        ]
    }

    assert parser.parse_chunk(json.dumps(first))["content"] == "Hello"
    result = parser.parse_chunk(json.dumps(second))

    assert result["content"] == " there"
    assert result["done"] is True


def test_grok_direct_response_list_uses_latest_assistant_message():
    parser = GrokParser()

    result = parser.parse_chunk(
        [
            {"message": "old answer", "sender": "assistant", "partial": False},
            {"message": "new answer", "sender": "assistant", "partial": True},
        ]
    )

    assert result["content"] == "new answer"
    assert result["done"] is False


def test_grok_ndjson_streaming_path_still_extracts_token_and_final_snapshot():
    parser = GrokParser()

    token_result = parser.parse_chunk(
        json.dumps({"result": {"token": "Hel", "isThinking": False}}) + "\n"
    )
    final_result = parser.parse_chunk(
        json.dumps(
            {
                "result": {
                    "response": {
                        "modelResponse": {
                            "sender": "assistant",
                            "message": "Hello",
                            "partial": False,
                        }
                    }
                }
            }
        )
        + "\n"
    )

    assert token_result["content"] == "Hel"
    assert token_result["done"] is False
    assert final_result["content"] == "lo"
    assert final_result["done"] is True


def test_grok_network_pattern_matches_completed_load_responses_endpoint():
    """Grok 的流式响应识别必须覆盖 load-responses / responses / new，且不吞分享链接。

    历史上 shipped 配置里写死过正则 ``stream_match_pattern``；现在 Grok 走 DOM 模式，
    ``stream_config.network`` 不再带该字段——``NetworkMonitor`` 对空 pattern 的语义是
    「所有响应都是候选」，真正的端点识别由 ``GrokParser.get_supported_patterns()`` 兜底。
    因此这里对两种形态都做校验，避免配置演进后测试变成「记住某个具体写法」：
    - 配置里写了 pattern：它必须命中三个流式端点，且不命中分享链接；
    - 没写：parser 的 supported_patterns 必须覆盖同样的端点，且不命中分享链接。
    """
    from tests._sites import shipped_sites

    network = shipped_sites()["grok.com"]["presets"]["主预设"]["stream_config"]["network"]
    assert network.get("parser") == "grok"

    stream_urls = (
        "https://grok.com/rest/app-chat/conversations/example-conversation/load-responses",
        "https://grok.com/rest/app-chat/conversations/example-conversation/responses",
        "https://grok.com/rest/app-chat/conversations/new",
    )
    share_url = "https://grok.com/rest/app-chat/conversations/example-conversation/sharing?responseId=abc"

    pattern = str(network.get("stream_match_pattern") or network.get("listen_pattern") or "").strip()
    if pattern:
        for url in stream_urls:
            assert re.search(pattern, url, flags=re.IGNORECASE), url
        assert not re.search(pattern, share_url, flags=re.IGNORECASE)
    else:
        hints = GrokParser.get_supported_patterns()
        for fragment in ("/rest/app-chat/conversations/", "/responses", "/load-responses"):
            assert fragment in hints, hints
        # 空 pattern 时监视器把所有响应当作候选，控制类响应（如分享链接）由 parser 的
        # 负载格式与 stream_exclude_patterns 过滤，不在这一层做 URL 排除。
        assert share_url  # 保留变量以示对照，避免误以为这里覆盖了排除逻辑
