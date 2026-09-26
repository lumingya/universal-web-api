"""R2-2：统一内部请求（ChatJob）、类型化事件（ChatEvent）与按协议标注的端到端验证。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core import chat_job
from app.core.chat_job import (
    PROTOCOL_ANTHROPIC_MESSAGES,
    PROTOCOL_OPENAI_CHAT,
    PROTOCOL_OPENAI_RESPONSES,
    events_from_openai_chunk,
    iter_chat_events,
    iter_openai_sse_payloads,
    job_for,
    use_chat_job,
    with_chat_job,
)


def test_events_from_openai_chunk_covers_all_kinds():
    chunk = {
        "choices": [{
            "index": 0,
            "delta": {
                "reasoning_content": "想一想",
                "content": "你好",
                "tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "f", "arguments": "{}"}}],
                "media": [{"type": "image", "url": "https://example.com/a.png"}],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5},
    }
    kinds = [(event.kind, event.text) for event in events_from_openai_chunk(chunk)]
    assert kinds == [("reasoning", "想一想"), ("text", "你好"), ("tool_call", ""), ("media", ""), ("finish", ""), ("usage", "")]
    error = events_from_openai_chunk({"error": {"message": "boom", "type": "execution_error"}})
    assert error[0].kind == "error" and error[0].text == "boom"
    assert events_from_openai_chunk("not a dict") == []


async def _collect(iterator):
    return [item async for item in iterator]


async def _chunks(parts):
    for part in parts:
        yield part


def test_sse_decoder_handles_split_bytes_crlf_comments_and_tail():
    frames = (
        ': keepalive\r\n\r\n'
        'data: {"choices":[{"delta":{"content":"世界"}}]}\r\n\r\n'
        'data: [DONE]\n\n'
        'data: {"choices":[{"delta":{"content":"尾"}}]}'  # 没有结尾空行的最后一帧
    ).encode("utf-8")
    cut = frames.index("世".encode("utf-8")) + 1  # 在一个汉字的 UTF-8 字节中间切开
    payloads = asyncio.run(_collect(iter_openai_sse_payloads(_chunks([frames[:cut], frames[cut:cut + 7], frames[cut + 7:]]))))
    assert [p["choices"][0]["delta"]["content"] for p in payloads] == ["世界", "尾"]


def test_sse_formatter_output_roundtrips_to_events():
    from app.core.config_parts.sse_formatter import SSEFormatter

    stream = [SSEFormatter.pack_chunk("你好", model="m"), SSEFormatter.pack_chunk("！", model="m"), SSEFormatter.pack_finish("m")]
    events = asyncio.run(_collect(iter_chat_events(_chunks(stream))))
    assert "".join(e.text for e in events if e.kind == "text") == "你好！"
    assert events[-1].kind == "finish" and events[-1].data["finish_reason"] == "stop"


def test_decoder_closes_upstream_iterator():
    closed = []

    class Upstream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'

        async def aclose(self):
            closed.append(True)

    async def consume_one():
        iterator = iter_openai_sse_payloads(Upstream())
        await iterator.__anext__()
        await iterator.aclose()

    asyncio.run(consume_one())
    assert closed == [True]


def test_anthropic_adapter_uses_the_shared_decoder():
    from app.api import anthropic_routes

    assert anthropic_routes._iter_openai_stream_chunks is iter_openai_sse_payloads


def test_job_context_is_scoped():
    assert chat_job.current_protocol() == PROTOCOL_OPENAI_CHAT
    with use_chat_job(job_for(PROTOCOL_ANTHROPIC_MESSAGES, model="m", route_domain="x.example", preset=None)) as job:
        assert chat_job.current_protocol() == PROTOCOL_ANTHROPIC_MESSAGES
        assert job.routing == {"route_domain": "x.example"}
    assert chat_job.current_chat_job() is None
    with pytest.raises(ValueError):
        job_for("gemini.generate")

    seen = []

    async def body():
        seen.append(chat_job.current_protocol())
        yield "a"
        seen.append(chat_job.current_protocol())
        yield "b"

    async def run():
        items = [item async for item in with_chat_job(job_for(PROTOCOL_OPENAI_RESPONSES), body())]
        return items, chat_job.current_chat_job()

    items, after = asyncio.run(run())
    assert items == ["a", "b"] and seen == [PROTOCOL_OPENAI_RESPONSES] * 2 and after is None


# --------------------------------------------------------------------------- 端到端：三种协议各自打标签


class _FakeBrowser:
    def execute(self, *args, **kwargs):
        from app.core.config_parts.sse_formatter import SSEFormatter

        yield SSEFormatter.pack_chunk("好", model="web-browser")
        yield SSEFormatter.pack_finish(model="web-browser")

    execute_workflow = execute
    execute_workflow_for_tab_index = execute
    execute_workflow_for_route_domain = execute
    execute_workflow_for_exact_url = execute


def test_each_protocol_is_tagged_and_counted(monkeypatch):
    httpx = pytest.importorskip("httpx")
    import main
    from app.api import chat, tab_routes
    from app.services.metrics import CHAT_REQUESTS

    for name in ("AUTH_ENABLED", "AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    browser = _FakeBrowser()
    monkeypatch.setattr(chat, "get_browser", lambda **kwargs: browser)
    monkeypatch.setattr(tab_routes, "get_browser", lambda **kwargs: browser)

    def count(protocol):
        return CHAT_REQUESTS.value(protocol=protocol, status="completed")

    before = {p: count(p) for p in (PROTOCOL_OPENAI_CHAT, PROTOCOL_ANTHROPIC_MESSAGES, PROTOCOL_OPENAI_RESPONSES)}
    message = [{"role": "user", "content": "hi"}]

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8199", timeout=30) as client:
            r1 = await client.post("/v1/chat/completions", json={"model": "deepseek-v4", "messages": message})
            r2 = await client.post("/v1/messages", json={"model": "deepseek-v4", "max_tokens": 64, "messages": message})
            r3 = await client.post("/v1/responses", json={"model": "deepseek-v4", "input": "hi", "stream": True})
            return r1, r2, r3

    r1, r2, r3 = asyncio.run(run())
    assert r1.status_code == r2.status_code == r3.status_code == 200, (r1.text[:200], r2.text[:200], r3.text[:200])
    assert json.loads(r2.text)["type"] == "message"
    assert "response.completed" in r3.text
    for protocol in before:
        assert count(protocol) == before[protocol] + 1, protocol
