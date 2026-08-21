import asyncio
import contextvars
import json
import threading
import time

from starlette.responses import StreamingResponse

from app.api.anthropic_routes import _anthropic_stream_from_openai
from app.api import tab_routes
from app.services.request_manager import RequestContext
from app.services.request_lifecycle import run_tracked_blocking_call
from app.core.config import _request_context
from app.services import result_event_bridge


def test_stream_context_reset_ignores_generator_finalization_in_another_context():
    """断连时 async generator 可能在不同的 Context 中执行 finally。"""
    request_id = contextvars.ContextVar("stream_request_id", default=None)
    origin_context = contextvars.copy_context()
    token = origin_context.run(request_id.set, "req-stream-close")

    try:
        cleanup_context = contextvars.copy_context()
        cleanup_context.run(tab_routes._reset_stream_request_context, token)
        assert origin_context.run(request_id.get) == "req-stream-close"
    finally:
        origin_context.run(request_id.reset, token)


def test_stream_context_reset_restores_current_context():
    token = _request_context.set("req-stream-normal-close")

    tab_routes._reset_stream_request_context(token)

    assert _request_context.get() is None


class _TrackingAsyncIterator:
    def __init__(self):
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(60)
        raise StopAsyncIteration

    async def aclose(self):
        self.closed = True


def test_response_chunk_refreshes_idle_activity_without_hard_timeout(monkeypatch):
    ctx = RequestContext(request_id="req-test")
    ctx.mark_running()
    original_timeout_start = ctx.started_at_monotonic
    ctx.last_activity_at = 1.0

    from app.services.request_manager import request_manager

    request_manager.capture_response_chunk(ctx, ": keepalive\n\n")

    assert ctx.last_activity_at > 1.0
    assert ctx.started_at_monotonic == original_timeout_start


def test_tab_stream_headers_disable_proxy_transformations():
    from app.api.tab_routes import _build_stream_headers

    headers = _build_stream_headers()

    assert headers["Cache-Control"] == "no-cache, no-transform"
    assert headers["Connection"] == "keep-alive"
    assert headers["X-Accel-Buffering"] == "no"


def test_tab_stream_emits_initial_keepalive_before_browser_result(monkeypatch):
    """A slow tab acquisition must not make an upstream client retry forever."""
    from app.api import tab_routes
    from app.core.config import SSEFormatter

    class _Request:
        async def is_disconnected(self):
            return False

    class _Browser:
        def execute_workflow_for_tab_index(self, *args, **kwargs):
            def _workflow():
                time.sleep(0.05)
                yield 'data: {"choices": [{"delta": {"content": "ok"}}]}\n\n'
                yield "data: [DONE]\n\n"

            return _workflow()

    monkeypatch.setattr(tab_routes, "get_browser", lambda auto_connect=False: _Browser())

    async def exercise():
        ctx = RequestContext(request_id="req-initial-keepalive")
        body = tab_routes.ChatRequest(
            model="gemini.com",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
        )
        stream = tab_routes._stream_with_tab_index(_Request(), body, ctx, 3)
        first = await anext(stream)
        payload = json.loads(first.removeprefix("data: ").strip())
        assert payload["object"] == "chat.completion.chunk"
        assert payload["model"] == body.model
        assert payload["choices"][0]["delta"] == {"content": ""}
        assert payload["choices"][0]["finish_reason"] is None
        await stream.aclose()

    asyncio.run(exercise())


def test_tool_stream_emits_periodic_keepalive_while_waiting(monkeypatch):
    from app.api import chat

    class _Request:
        async def is_disconnected(self):
            return False

    async def _slow_tool_call(*args, **kwargs):
        await asyncio.sleep(0.05)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(chat, "_complete_tool_calling_with_lifecycle", _slow_tool_call)
    monkeypatch.setattr(chat, "SSE_HEARTBEAT_INTERVAL", 0.01)

    async def exercise():
        ctx = RequestContext(request_id="req-tool-keepalive")
        body = chat.ChatRequest(
            model="glm-5.3(max)",
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            tools=[{"type": "function", "function": {"name": "noop"}}],
        )
        stream = chat._stream_tool_calling_with_lifecycle(_Request(), body, ctx)
        first = await anext(stream)
        second = await anext(stream)
        first_payload = json.loads(first.removeprefix("data: ").strip())
        second_payload = json.loads(second.removeprefix("data: ").strip())
        assert first_payload["choices"][0]["delta"] == {"content": ""}
        assert second_payload["choices"][0]["delta"] == {"content": ""}
        assert first_payload["id"] == second_payload["id"]
        await stream.aclose()

    asyncio.run(exercise())


def test_tracked_worker_inherits_request_log_context():
    observed = []
    ctx = RequestContext(request_id="req-context-probe")

    def worker():
        observed.append(_request_context.get())
        return "done"

    async def exercise():
        return await run_tracked_blocking_call(
            worker,
            ctx=ctx,
            worker_state={},
            label="context-probe",
            poll_timeout=0.01,
        )

    assert asyncio.run(exercise()) == "done"
    assert observed == [ctx.request_id]
    assert _request_context.get() is None


def test_arena_prompt_rejection_is_structured_as_non_retryable_422():
    import json

    from app.api.chat import _arena_prompt_rejection_response, _pack_error

    response = _arena_prompt_rejection_response()
    assert response.status_code == 422
    assert response.headers["x-should-retry"] == "false"
    assert json.loads(response.body)["error"]["code"] == "arena_prompt_rejected"

    payload = json.loads(
        _pack_error(
            "Arena 拒绝了该提示词",
            "arena_prompt_rejected",
            error_type="invalid_request_error",
            status_code=422,
            retryable=False,
        ).split("data: ", 1)[1]
    )
    assert payload["error"]["status_code"] == 422
    assert payload["error"]["retryable"] is False


def test_arena_image_generation_error_is_structured_as_non_retryable_422():
    import json

    from app.api.chat import _arena_non_retryable_response

    response = _arena_non_retryable_response(
        "Arena 图片生成失败",
        "arena_image_generation_failed",
    )

    assert response.status_code == 422
    assert response.headers["x-should-retry"] == "false"
    error = json.loads(response.body)["error"]
    assert error["code"] == "arena_image_generation_failed"
    assert error["status_code"] == 422
    assert error["retryable"] is False


def test_result_event_dedupe_is_atomic_and_bounded(monkeypatch):
    appended = []
    append_lock = threading.Lock()

    def fake_append(event):
        time.sleep(0.01)
        with append_lock:
            appended.append(event)

    monkeypatch.setattr(result_event_bridge, "_append_event", fake_append)
    monkeypatch.setattr(result_event_bridge, "_get_dedupe_max_entries", lambda: 2)
    with result_event_bridge._DEDUPE_LOCK:
        result_event_bridge._LAST_DIGEST_BY_KEY.clear()

    event = {
        "event_id": "same-digest",
        "session_id": "session",
        "completion_id": "completion",
        "conversation_id": "conversation",
        "parser_id": "parser",
    }
    results = []

    def emit():
        results.append(result_event_bridge._dedupe_and_append(dict(event)))

    try:
        threads = [threading.Thread(target=emit) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(appended) == 1
        assert results.count(True) == 1

        for index in range(3):
            distinct = dict(event, event_id=f"digest-{index}", completion_id=str(index))
            assert result_event_bridge._dedupe_and_append(distinct)

        assert len(result_event_bridge._LAST_DIGEST_BY_KEY) == 2
    finally:
        with result_event_bridge._DEDUPE_LOCK:
            result_event_bridge._LAST_DIGEST_BY_KEY.clear()


def test_anthropic_stream_closes_upstream_after_initial_event():
    async def exercise():
        upstream = _TrackingAsyncIterator()
        response = StreamingResponse(upstream, media_type="text/event-stream")
        translated = _anthropic_stream_from_openai(response, "test-model")

        first_event = await anext(translated)
        assert "event: message_start" in first_event
        assert upstream.closed is False

        await translated.aclose()
        assert upstream.closed is True

    asyncio.run(exercise())


def test_scheduled_restart_guard_waits_for_http_and_workflow_drain(monkeypatch):
    from app.core.config import AppConfig
    from app.services.restart_guard import RestartGuard

    monkeypatch.delenv("SCHEDULED_RESTART_INTERVAL_SECONDS", raising=False)
    assert AppConfig.get_scheduled_restart_interval_seconds() == 10800

    async def exercise():
        active_work = {"count": 1}
        restart_calls = []
        guard = RestartGuard(
            enabled=True,
            interval_seconds=10800,
            drain_timeout_seconds=1,
            activity_probe=lambda: active_work["count"],
            restart_callback=lambda: restart_calls.append("restart"),
        )

        assert await guard.admit_request()
        drain_task = asyncio.create_task(guard._drain_and_restart())
        await asyncio.sleep(0)
        assert guard.is_draining
        assert restart_calls == []
        assert not await guard.admit_request()

        await guard.release_request()
        await asyncio.sleep(0.05)
        assert restart_calls == []

        active_work["count"] = 0
        assert await drain_task is True
        assert restart_calls == ["restart"]

    asyncio.run(exercise())


def test_scheduled_restart_guard_skips_cycle_when_drain_timeout_expires():
    from app.services.restart_guard import RestartGuard

    async def exercise():
        guard = RestartGuard(
            enabled=True,
            interval_seconds=10800,
            drain_timeout_seconds=0.01,
            activity_probe=lambda: 1,
            restart_callback=lambda: (_ for _ in ()).throw(AssertionError("must not restart")),
        )

        assert await guard._drain_and_restart() is False
        assert not guard.is_draining

    asyncio.run(exercise())
