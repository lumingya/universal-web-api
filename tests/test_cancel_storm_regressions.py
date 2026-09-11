"""Real cancellation scopes used by Starlette, not only asyncio Task.cancel()."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import anyio
import pytest

from app.api import chat, tab_routes
from app.core.config import _request_context
import importlib
rm_module = importlib.import_module('app.services.request_manager')
from app.services.request_manager import RequestContext, RequestStatus


class ConnectedRequest:
    async def is_disconnected(self):
        return False


@pytest.mark.parametrize('route', ['chat', 'tab', 'domain', 'url'])
@pytest.mark.parametrize('after_output', [False, True])
def test_stop_midstream_finishes_cleanup_and_next_request_is_independent(monkeypatch, route, after_output):
    manager = tab_routes.request_manager
    guard = rm_module.CancelStormGuard()
    monkeypatch.setattr(rm_module, 'cancel_storm_guard', guard)
    finishes = []
    original_finish = manager.finish_request
    monkeypatch.setattr(manager, '_append_monitor_history', lambda *args: None)
    monkeypatch.setattr(manager, 'start_request', lambda ctx: ctx.mark_running())
    def finish(ctx, success=True):
        finishes.append(ctx.request_id)
        original_finish(ctx, success)
    monkeypatch.setattr(manager, 'finish_request', finish)
    calls = []
    released = []
    class Browser:
        def execute(self, *args, **kwargs):
            request_id = kwargs['task_id']
            calls.append(request_id)
            try:
                yield 'data: {"choices":[{"delta":{"content":"first token"}}]}\n\n'
                if request_id == 'stop-me':
                    while not kwargs['stop_checker']():
                        time.sleep(.002)
                else:
                    yield 'data: [DONE]\n\n'
            finally:
                # Keep the worker alive through response cancellation, as real CDP cleanup is.
                time.sleep(.04)
                released.append(request_id)
        execute_workflow = execute
        execute_workflow_for_tab_index = execute
        execute_workflow_for_route_domain = execute
        execute_workflow_for_exact_url = execute
    browser = Browser()
    module = chat if route == 'chat' else tab_routes
    monkeypatch.setattr(module, 'get_browser', lambda **kwargs: browser)
    body = module.ChatRequest(model='deepseek-v4', messages=[{'role':'user','content':'hello'}], stream=True)
    def stream(ctx):
        request = ConnectedRequest()
        if route == 'chat': return chat._stream_with_lifecycle(request, body, ctx)
        if route == 'tab': return tab_routes._stream_with_tab_index(request, body, ctx, 3)
        if route == 'domain': return tab_routes._stream_with_route_domain(request, body, ctx, 'chat.deepseek.com')
        return tab_routes._stream_with_exact_url(request, body, ctx, 'https://chat.deepseek.com')

    async def exercise():
        ctx = RequestContext('stop-me', client_fp='same-client')
        iterator = stream(ctx)
        previous_context = _request_context.get()
        with anyio.CancelScope() as scope:
            await anext(iterator)
            if after_output:
                while not ctx.monitor.get('has_response_text'):
                    await anext(iterator)
            scope.cancel()
            with pytest.raises(asyncio.CancelledError):
                await iterator.athrow(asyncio.CancelledError())
        assert finishes == ['stop-me']
        assert ctx.status == RequestStatus.CANCELLED
        assert not ctx.worker_thread.is_alive()
        assert released == ['stop-me']
        assert _request_context.get() == previous_context
        # No inherited cancel flag, no stale worker, no server-side retry.
        assert await guard.maybe_backoff('same-client') == 0
        next_ctx = RequestContext('next-request', client_fp='same-client')
        result = [chunk async for chunk in stream(next_ctx)]
        assert any('first token' in chunk for chunk in result)
        assert next_ctx.status == RequestStatus.COMPLETED
        assert finishes == ['stop-me', 'next-request']
        assert calls == ['stop-me', 'next-request']
        assert not next_ctx.worker_thread.is_alive()
    asyncio.run(exercise())


@pytest.mark.parametrize('failure', ['disconnect', 'send_error'])
def test_asgi_send_interruption_closes_suspended_generator_before_return(monkeypatch, failure):
    from app.api.streaming_response import RequestStreamingResponse
    from app.services.request_lifecycle import request_finalization
    finished = []
    monkeypatch.setattr(tab_routes.request_manager, 'finish_request', lambda ctx, **kw: finished.append(ctx.request_id))
    async def exercise():
        ctx = RequestContext('send-interrupted')
        closed = []
        sent = asyncio.Event()
        async def body():
            try:
                yield b'data: partial output\n\n'
                await asyncio.sleep(30)
            finally:
                ctx.request_cancel('stream_closed')
                with request_finalization(ctx):
                    await asyncio.sleep(.01)
                    closed.append(True)
        iterator = body()  # Keep a reference: the fix must not rely on garbage collection.
        async def send(message):
            if message['type'] == 'http.response.body':
                sent.set()
                if failure == 'send_error':
                    raise OSError('connection reset by peer')
                await asyncio.sleep(30)
        async def receive():
            await sent.wait()
            if failure == 'send_error': await asyncio.sleep(30)
            return {'type':'http.disconnect'}
        response = RequestStreamingResponse(iterator, media_type='text/event-stream')
        if failure == 'send_error':
            with pytest.raises(BaseExceptionGroup):
                await response({'type':'http','asgi':{'spec_version':'2.3'}}, receive, send)
        else:
            await response({'type':'http','asgi':{'spec_version':'2.3'}}, receive, send)
        assert closed == [True]
        assert finished == ['send-interrupted']
        assert iterator.ag_frame is None
    asyncio.run(exercise())


@pytest.mark.parametrize('route', ['chat', 'tab', 'domain', 'url'])
def test_tool_stream_closed_at_initial_frame_is_finalized_even_before_worker_starts(monkeypatch, route):
    module = chat if route == 'chat' else tab_routes
    finished = []
    monkeypatch.setattr(module.request_manager, 'finish_request', lambda ctx, **kw: finished.append(ctx.request_id))
    async def exercise():
        ctx = RequestContext('tool-before-start')
        body = module.ChatRequest(model='m', messages=[], tools=[{'type':'function','function':{'name':'f'}}])
        if route == 'chat': iterator = chat._stream_tool_calling_with_lifecycle(ConnectedRequest(), body, ctx)
        elif route == 'tab': iterator = tab_routes._stream_tool_calling_with_tab_index(ConnectedRequest(), body, ctx, 3)
        elif route == 'domain': iterator = tab_routes._stream_tool_calling_with_route_domain(ConnectedRequest(), body, ctx, 'example.com')
        else: iterator = tab_routes._stream_tool_calling_with_exact_url(ConnectedRequest(), body, ctx, 'https://example.com')
        await anext(iterator)
        with anyio.CancelScope() as scope:
            scope.cancel()
            await iterator.aclose()
        assert finished == ['tool-before-start']
        assert ctx.status == RequestStatus.CANCELLED
        assert not hasattr(ctx, 'worker_thread')
    asyncio.run(exercise())


def test_finalization_survives_a_second_raw_task_cancel_and_schedules_leak_check(monkeypatch):
    from app.services import request_lifecycle as lifecycle
    finished, scheduled = [], []
    monkeypatch.setattr(lifecycle.request_manager, 'finish_request', lambda ctx, **kw: finished.append(ctx.request_id))
    monkeypatch.setattr(lifecycle, 'schedule_completed_worker_retire_check', lambda *a, **k: scheduled.append((a,k)))
    async def exercise():
        release = threading.Event()
        worker = threading.Thread(target=release.wait, daemon=True)
        worker.start()
        entered = asyncio.Event()
        ctx = RequestContext('repeated-cancel')
        async def task():
            with lifecycle.request_finalization(ctx):
                entered.set()
                await lifecycle.cleanup_worker_thread_after_request(worker, ctx, join_timeout=.1)
        running = asyncio.create_task(task())
        await entered.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError): await running
        release.set(); worker.join(timeout=1)
        assert finished == ['repeated-cancel']
        assert len(scheduled) == 1
        assert scheduled[0][0][0] is worker
        assert ctx.should_stop()
    asyncio.run(exercise())


def test_circuit_blocks_bursts_before_creating_requests_and_recovers(monkeypatch):
    from fastapi import HTTPException
    from app.services.request_manager import CancelStormGuard
    guard = CancelStormGuard()
    now = [100.0]
    monkeypatch.setattr(rm_module.time, 'monotonic', lambda: now[0])
    async def exercise():
        for _ in range(4):
            assert await guard.maybe_backoff('client-a') == 0
            guard.record_cancel('client-a', reason='coroutine_cancelled')
        for _ in range(100):
            with pytest.raises(HTTPException) as exc:
                await guard.maybe_backoff('client-a')
            assert exc.value.status_code == 429
            assert exc.value.headers['Retry-After'] == '5'
        assert len(guard._cancel_records['client-a']) == 4
        assert await guard.maybe_backoff('client-b') == 0
        now[0] += 5.1
        assert await guard.maybe_backoff('client-a') == 0
    asyncio.run(exercise())


@pytest.mark.parametrize('reason', ['manual', 'manual_terminate', 'stream_done', 'stop_sequence', 'absolute_request_timeout'])
def test_manual_stops_and_failures_do_not_open_circuit(reason):
    from app.services.request_manager import CancelStormGuard
    guard = CancelStormGuard()
    for _ in range(20): guard.record_cancel('a', reason=reason)
    assert asyncio.run(guard.maybe_backoff('a')) == 0


@pytest.mark.parametrize('output', [ {'has_response_text':True}, {'has_response_media':True}, {'response_tokens':2} ])
def test_stop_after_real_output_resets_guard_and_finish_is_not_double_counted(monkeypatch, output):
    from app.services.request_manager import CancelStormGuard
    guard = CancelStormGuard()
    monkeypatch.setattr(rm_module, 'cancel_storm_guard', guard)
    manager = tab_routes.request_manager
    monkeypatch.setattr(manager, '_append_monitor_history', lambda *args: None)
    for _ in range(3): guard.record_cancel('a', reason='coroutine_cancelled')
    ctx = RequestContext('partial-output', client_fp='a', monitor=output)
    ctx.request_cancel('coroutine_cancelled')
    manager.finish_request(ctx)
    assert not guard._cancel_records
    cancelled = RequestContext('one-cancel', client_fp='a')
    cancelled.request_cancel('coroutine_cancelled')
    manager.finish_request(cancelled)
    manager.finish_request(cancelled)
    assert len(guard._cancel_records['a']) == 1


def test_keepalive_is_not_output_and_ordinary_failure_is_not_transport_cancel(monkeypatch):
    from app.services.request_manager import CancelStormGuard
    from app.core.config import SSEFormatter
    guard = CancelStormGuard()
    monkeypatch.setattr(rm_module, 'cancel_storm_guard', guard)
    manager = tab_routes.request_manager
    monkeypatch.setattr(manager, '_append_monitor_history', lambda *args: None)
    ctx = RequestContext('empty-keepalive', client_fp='a')
    manager.capture_response_chunk(ctx, SSEFormatter.pack_keepalive(model='m'))
    ctx.request_cancel('stream_closed'); manager.finish_request(ctx)
    assert len(guard._cancel_records['a']) == 1
    failed = RequestContext('selector-failure', client_fp='a')
    failed.mark_failed('selector not found'); manager.finish_request(failed, success=False)
    assert len(guard._cancel_records['a']) == 1


def test_guard_storage_is_bounded_and_fingerprint_does_not_leak_credentials():
    from app.services.request_manager import CancelStormGuard
    guard = CancelStormGuard(max_clients=8)
    for i in range(100):
        for _ in range(50): guard.record_cancel(str(i))
    assert len(guard._cancel_records) <= 8 and len(guard._blocked_until) <= 8
    assert all(len(records) <= 4 for records in guard._cancel_records.values())
    request = SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'),headers={'authorization':'Bearer secret-key-1234567890123456','user-agent':'Chatbox'})
    fp = guard.get_client_fingerprint(request)
    assert len(fp) == 24 and 'secret' not in fp and '127.0.0.1' not in fp
    request.headers['authorization'] = 'Bearer different-key-1234567890123456'
    assert guard.get_client_fingerprint(request) != fp


@pytest.mark.parametrize('route', ['chat', 'tab', 'model_name', 'domain', 'url', 'url_preset'])
def test_open_circuit_rejects_at_entry_before_browser_or_request_history(monkeypatch, route):
    from app.services.request_manager import CancelStormGuard
    from fastapi import HTTPException
    guard = CancelStormGuard()
    request = SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'),headers={})
    fp = guard.get_client_fingerprint(request)
    for _ in range(4): guard.record_cancel(fp)
    monkeypatch.setattr(chat, 'cancel_storm_guard', guard)
    monkeypatch.setattr(tab_routes, 'cancel_storm_guard', guard)
    no_browser = Mock(side_effect=AssertionError('Browser must not be accessed'))
    no_record = Mock(side_effect=AssertionError('No request record should be created'))
    monkeypatch.setattr(chat, 'get_browser', no_browser)
    monkeypatch.setattr(tab_routes, 'get_browser', no_browser)
    monkeypatch.setattr(tab_routes.request_manager, 'create_request', no_record)
    body = tab_routes.ChatRequest(model='deepseek-v4',messages=[])
    async def exercise():
        with pytest.raises(HTTPException) as exc:
            if route == 'chat': await chat.chat_completions(request, chat.ChatRequest(model='m',messages=[]), True)
            elif route == 'tab': await tab_routes.chat_with_tab(3,request,body)
            elif route == 'model_name': await tab_routes.chat_with_exposed_model_name('deepseek-v4',request,body)
            elif route == 'domain': await tab_routes.chat_with_route_domain('chat.deepseek.com',request,body)
            elif route == 'url': await tab_routes.chat_with_exact_tab_url('token',request,body)
            else: await tab_routes.chat_with_exact_tab_url_and_preset('token','主预设',request,body)
        assert exc.value.status_code == 429
        no_browser.assert_not_called(); no_record.assert_not_called()
    asyncio.run(exercise())


def test_disconnect_during_headers_finalizes_unstarted_request(monkeypatch):
    from app.api.streaming_response import RequestStreamingResponse
    finished = []
    monkeypatch.setattr(tab_routes.request_manager, 'finish_request', lambda ctx, **kw: finished.append(ctx.request_id))
    async def exercise():
        ctx = RequestContext('no-body-yet')
        entered = []
        headers_sent = asyncio.Event()
        async def body():
            entered.append(True)
            yield b'data: something\n\n'
        async def send(message):
            headers_sent.set()
            await asyncio.sleep(30)
        async def receive():
            await headers_sent.wait()
            return {'type':'http.disconnect'}
        response = RequestStreamingResponse(body(), request_context=ctx)
        await response({'type':'http'}, receive, send)
        assert not entered
        assert finished == ['no-body-yet']
        assert ctx.status == RequestStatus.CANCELLED
    asyncio.run(exercise())


def test_cleanup_exception_cannot_skip_history_or_request_context_reset(monkeypatch):
    from app.services.request_lifecycle import request_finalization
    finished = []
    monkeypatch.setattr(tab_routes.request_manager, 'finish_request', lambda ctx, **kw: finished.append(ctx.request_id))
    async def exercise():
        before = _request_context.get()
        token = _request_context.set('broken-cleanup')
        ctx = RequestContext('broken-cleanup')
        with pytest.raises(RuntimeError,match='cleanup failed'):
            with request_finalization(ctx, token):
                await asyncio.sleep(0)
                raise RuntimeError('cleanup failed')
        assert finished == ['broken-cleanup']
        assert _request_context.get() == before
    asyncio.run(exercise())


def test_http_retry_after_is_preserved_and_rejected_retries_do_not_allocate(monkeypatch):
    from fastapi import FastAPI, Request
    from httpx import ASGITransport, AsyncClient
    from app.services.request_manager import CancelStormGuard
    guard = CancelStormGuard()
    fp = guard.get_client_fingerprint(SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'), headers={'user-agent':'cancel-repro'}))
    for _ in range(4): guard.record_cancel(fp)
    monkeypatch.setattr(tab_routes, 'cancel_storm_guard', guard)
    browser = Mock(side_effect=AssertionError('No browser allocation during cooldown'))
    create = Mock(side_effect=AssertionError('No request/history entry during cooldown'))
    monkeypatch.setattr(tab_routes, 'get_browser', browser)
    monkeypatch.setattr(tab_routes.request_manager, 'create_request', create)
    app = FastAPI()
    @app.post('/v1/chat/completions')
    async def route(request: Request):
        return await tab_routes.chat_with_exposed_model_name('deepseek-v4', request, tab_routes.ChatRequest(model='deepseek-v4', messages=[]))
    async def exercise():
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'User-Agent':'cancel-repro'}) as client:
            for _ in range(50):
                response = await client.post('/v1/chat/completions',json={})
                assert response.status_code == 429
                assert 1 <= int(response.headers['Retry-After']) <= 5
                assert response.json()['detail']['error']['code'] == 'client_cancel_storm'
        browser.assert_not_called();create.assert_not_called()
    asyncio.run(exercise())


def test_cleanup_stays_bounded_when_default_executor_is_saturated():
    from concurrent.futures import ThreadPoolExecutor
    from app.services.request_lifecycle import cleanup_worker_thread_after_request
    async def exercise():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        release = threading.Event()
        occupied = asyncio.create_task(asyncio.to_thread(release.wait))
        await asyncio.sleep(0)
        worker = threading.Thread(target=lambda: time.sleep(.03), daemon=True)
        worker.start()
        try:
            leaked = await asyncio.wait_for(cleanup_worker_thread_after_request(worker, RequestContext('pool-busy'), join_timeout=.1),timeout=.3)
            assert not leaked
            assert not worker.is_alive()
        finally:
            release.set()
            await occupied
    asyncio.run(exercise())
