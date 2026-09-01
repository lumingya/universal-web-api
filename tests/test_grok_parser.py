import json
import re
from pathlib import Path

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
    config = json.loads(Path("config/sites.json").read_text(encoding="utf-8"))
    pattern = config["grok.com"]["presets"]["主预设"]["stream_config"]["network"][
        "stream_match_pattern"
    ]

    assert re.search(
        pattern,
        "https://grok.com/rest/app-chat/conversations/example-conversation/load-responses",
        flags=re.IGNORECASE,
    )
    assert re.search(
        pattern,
        "https://grok.com/rest/app-chat/conversations/example-conversation/responses",
        flags=re.IGNORECASE,
    )
    assert re.search(
        pattern,
        "https://grok.com/rest/app-chat/conversations/new",
        flags=re.IGNORECASE,
    )
    assert not re.search(
        pattern,
        "https://grok.com/rest/app-chat/conversations/example-conversation/sharing?responseId=abc",
        flags=re.IGNORECASE,
    )
