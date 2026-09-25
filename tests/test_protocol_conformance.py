"""R1-5：协议一致性测试——用官方 openai / anthropic Python SDK 作为客户端。

浏览器层换成固定输出的假实现，请求经 httpx.ASGITransport 直接进入 main.app（不监听端口、不启动浏览器）。
验证的是 SDK 眼中的协议形状：非流式/流式响应能被官方 SDK 正确解析，错误码映射到 SDK 的异常类型。
没有安装 openai / anthropic 时自动跳过（二者在 requirements-dev.txt 中）。
"""

from __future__ import annotations

import asyncio

import pytest

httpx = pytest.importorskip("httpx")

ANSWER_PARTS = ["你好", "，", "世界！"]
ANSWER = "".join(ANSWER_PARTS)


class _FakeBrowser:
    """假浏览器层：和真实的流监听一样，用 SSEFormatter 打包 OpenAI 风格的 chunk，最后 pack_finish。"""

    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        from app.core.config_parts.sse_formatter import SSEFormatter

        self.calls.append({"args": args, "kwargs": kwargs})
        for part in ANSWER_PARTS:
            yield SSEFormatter.pack_chunk(part, model="web-browser")
        yield SSEFormatter.pack_finish(model="web-browser")

    execute_workflow = execute
    execute_workflow_for_tab_index = execute
    execute_workflow_for_route_domain = execute
    execute_workflow_for_exact_url = execute


@pytest.fixture
def app_with_fake_browser(monkeypatch):
    import main
    from app.api import chat, tab_routes

    for name in ("AUTH_ENABLED", "AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    browser = _FakeBrowser()
    monkeypatch.setattr(chat, "get_browser", lambda **kwargs: browser)
    monkeypatch.setattr(tab_routes, "get_browser", lambda **kwargs: browser)
    return main.app, browser


def _http_client(app, module=None):
    """``module`` 为 SDK 要求的 HTTP 库：openai 用 httpx；anthropic 1.x 起改用 httpx2（接口相同）。"""
    module = module or httpx
    return module.AsyncClient(transport=module.ASGITransport(app=app), base_url="http://127.0.0.1:8199", timeout=30)


# --------------------------------------------------------------------------- OpenAI SDK


def _openai(app):
    openai = pytest.importorskip("openai")
    return openai, openai.AsyncOpenAI(base_url="http://127.0.0.1:8199/v1", api_key="test-key", http_client=_http_client(app))


def test_openai_sdk_chat_completion_non_stream(app_with_fake_browser):
    app, browser = app_with_fake_browser
    _, client = _openai(app)

    async def run():
        async with client:
            return await client.chat.completions.create(
                model="deepseek-v4", messages=[{"role": "user", "content": "说你好"}]
            )

    completion = asyncio.run(run())
    assert completion.object == "chat.completion"
    assert completion.choices[0].message.role == "assistant"
    assert completion.choices[0].message.content == ANSWER
    assert completion.choices[0].finish_reason == "stop"
    assert browser.calls, "请求没有到达（假）浏览器层"


def test_openai_sdk_chat_completion_stream(app_with_fake_browser):
    app, _ = app_with_fake_browser
    _, client = _openai(app)

    async def run():
        async with client:
            stream = await client.chat.completions.create(
                model="deepseek-v4", messages=[{"role": "user", "content": "说你好"}], stream=True
            )
            chunks = [chunk async for chunk in stream]
        return chunks

    chunks = asyncio.run(run())
    assert chunks and all(chunk.object == "chat.completion.chunk" for chunk in chunks)
    text = "".join(choice.delta.content or "" for chunk in chunks for choice in chunk.choices)
    assert text == ANSWER
    finish = [choice.finish_reason for chunk in chunks for choice in chunk.choices if choice.finish_reason]
    assert finish and finish[-1] == "stop"


def test_openai_sdk_maps_validation_error_to_422(app_with_fake_browser):
    app, _ = app_with_fake_browser
    openai, client = _openai(app)

    async def run():
        async with client:
            await client.chat.completions.create(
                model="deepseek-v4", messages=[{"role": "user", "content": "hi"}], n=2
            )

    with pytest.raises(openai.UnprocessableEntityError) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 422
    assert "n=1" in str(exc.value)


def test_openai_sdk_lists_models(app_with_fake_browser):
    app, _ = app_with_fake_browser
    _, client = _openai(app)

    async def run():
        async with client:
            page = await client.models.list()
            return [model.id async for model in page]

    ids = asyncio.run(run())
    assert ids and all(isinstance(model_id, str) and model_id for model_id in ids)


# --------------------------------------------------------------------------- Anthropic SDK


def _anthropic(app):
    anthropic = pytest.importorskip("anthropic")
    try:
        import httpx2 as http_module  # anthropic>=1.x
    except ImportError:  # 旧版 anthropic 仍基于 httpx
        http_module = httpx
    client = anthropic.AsyncAnthropic(
        base_url="http://127.0.0.1:8199", api_key="test-key", http_client=_http_client(app, http_module)
    )
    return anthropic, client


def test_anthropic_sdk_messages_non_stream(app_with_fake_browser):
    app, browser = app_with_fake_browser
    _, client = _anthropic(app)

    async def run():
        async with client:
            return await client.messages.create(
                model="deepseek-v4", max_tokens=256, messages=[{"role": "user", "content": "说你好"}]
            )

    message = asyncio.run(run())
    assert message.type == "message" and message.role == "assistant"
    text = "".join(block.text for block in message.content if block.type == "text")
    assert text == ANSWER
    assert message.stop_reason == "end_turn"
    assert browser.calls


def test_anthropic_sdk_messages_stream(app_with_fake_browser):
    app, _ = app_with_fake_browser
    _, client = _anthropic(app)

    async def run():
        async with client:
            async with client.messages.stream(
                model="deepseek-v4", max_tokens=256, messages=[{"role": "user", "content": "说你好"}]
            ) as stream:
                deltas = [text async for text in stream.text_stream]
                final = await stream.get_final_message()
        return deltas, final

    deltas, final = asyncio.run(run())
    assert "".join(deltas) == ANSWER
    assert final.stop_reason == "end_turn"
    assert "".join(block.text for block in final.content if block.type == "text") == ANSWER


# --------------------------------------------------------------------------- OpenAI Responses API


def test_openai_sdk_responses_non_stream(app_with_fake_browser):
    app, _ = app_with_fake_browser
    _, client = _openai(app)

    async def run():
        async with client:
            return await client.responses.create(model="deepseek-v4", input="说你好")

    response = asyncio.run(run())
    assert response.object == "response"
    assert response.status == "completed"
    assert response.output_text == ANSWER


def test_openai_sdk_responses_stream(app_with_fake_browser):
    app, _ = app_with_fake_browser
    _, client = _openai(app)

    async def run():
        async with client:
            stream = await client.responses.create(model="deepseek-v4", input="说你好", stream=True)
            return [event async for event in stream]

    events = asyncio.run(run())
    types = [event.type for event in events]
    assert types[0] == "response.created" and types[-1] == "response.completed"
    deltas = "".join(event.delta for event in events if event.type == "response.output_text.delta")
    assert deltas == ANSWER
    assert events[-1].response.output_text == ANSWER
