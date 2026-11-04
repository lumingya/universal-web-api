"""``/tab`` 路由分组（由 app/api/tab_routes.py 拆出）。"""

import asyncio
import json
import queue
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.api.deps import verify_service_auth
from app.api.openai import runtime
from app.api.openai.shared import (
    MODEL_LIST_CREATED,
    SSE_HEARTBEAT_INTERVAL,
    STREAM_QUEUE_POLL_TIMEOUT,
    ChatRequest,
    _apply_response_format_if_needed,
    _build_route_model_entries_payload,
    _build_stream_headers,
    _build_tab_resolution_headers,
    _cleanup_non_stream_content,
    _cleanup_route_worker_thread,
    _consume_non_stream_sse_payload,
    _encode_response_headers,
    _extract_sse_chunk_media_items,
    _format_route_tool_calling_error,
    _get_route_tool_calling_cancel_reason,
    _get_tab_info_by_index,
    _has_audio_media,
    _iter_stream_chunks_with_optional_usage,
    _make_buffered_sse_payload_parser,
    _maybe_pack_stream_usage_chunk,
    _pack_audio_fast_return_chunks,
    _put_route_worker_queue_item,
    _resolve_strict_tab_preset,
    _route_method_guard,
    _RouteToolCallingExecutionCancelled,
    _run_tracked_route_tool_calling_worker,
    _should_fast_return_on_audio_media,
    _split_sse_done_frame,
    _unwrap_fastapi_param_value,
    logger,
    router,
)
from app.api.openai_stop import (
    apply_stop_sequences_to_text,
    build_stop_sequence_stream_state,
    filter_openai_stop_sse_chunk,
    flush_openai_stop_state,
    iter_openai_sse_payloads,
)
from app.api.stream_helpers import (
    _dedupe_media_items,
    _extract_assistant_content,
    _extract_stream_error_message,
    _is_single_choice_request,
    _pack_done,
    _pack_error,
    _pack_error_done,
)
from app.api.streaming_response import RequestStreamingResponse as StreamingResponse
from app.core.config import SSEFormatter, _request_context
from app.services.arena_direct_models import (
    build_openai_model_entries,
    get_arena_direct_catalog_for_tab,
    list_arena_direct_models,
)
from app.services.error_metadata import build_error_response, resolve_error_metadata
from app.services.request_lifecycle import (
    get_max_request_execute_time_sec,
    mark_request_hard_timeout,
    request_finalization,
    run_in_request_context,
    wait_worker_queue_item,
)
from app.services.request_manager import RequestContext, RequestStatus, watch_client_disconnect
from app.services.tool_calling import (
    build_tool_completion_response,
    complete_tool_calling_roundtrip_async,
    decode_browser_non_stream_payload,
    get_tool_calling_allow_media_postprocess,
    has_tool_calling_request,
    iter_tool_stream_chunks,
    normalize_tool_request,
    summarize_messages_for_debug,
)


def _normalize_optional_tab_index_value(value: Any) -> Optional[int]:
    value = _unwrap_fastapi_param_value(value)
    if value in (None, ""):
        return None
    return int(value)


@router.get("/tab/{tab_index}/v1/models")
async def list_models_with_tab(
    tab_index: int,
    anthropic_version: Optional[str] = Header(None, alias="anthropic-version"),
    authenticated: bool = Depends(verify_service_auth),
    _route_method: None = Depends(_route_method_guard("fixed_tab")),
):
    """为指定标签页路由提供 OpenAI 兼容模型列表接口。"""
    if tab_index < 1:
        raise HTTPException(status_code=400, detail="标签页编号必须大于 0")

    browser = runtime.get_browser(auto_connect=False)
    tab_info = _get_tab_info_by_index(browser, tab_index)
    if tab_info is None:
        raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")

    entries: List[Dict[str, Any]] = []
    try:
        from app.services.config_engine import config_engine

        catalog_preset = get_arena_direct_catalog_for_tab(config_engine, tab_info)
        if catalog_preset:
            entries = build_openai_model_entries(
                list_arena_direct_models(
                    browser,
                    catalog_config=catalog_preset.get("catalog"),
                ),
                created=MODEL_LIST_CREATED,
            )
    except Exception as e:
        logger.debug(f"读取标签页模型目录失败（已忽略）: {e}")

    if bool(tab_info.get("model_name_override_source")):
        exposed_name = str(tab_info.get("exposed_model_name") or tab_info.get("model_name_override") or "").strip()
        seen_ids = {str(e.get("id") or "").strip().lower() for e in entries}
        if exposed_name and exposed_name.lower() not in seen_ids:
            entries.append({
                "id": exposed_name,
                "object": "model",
                "type": "model",
                "created": MODEL_LIST_CREATED,
                "owned_by": "universal-web-api",
                "display_name": exposed_name,
            })

    if not entries:
        entries = [{
            "id": "web-browser",
            "object": "model",
            "type": "model",
            "created": MODEL_LIST_CREATED,
            "owned_by": "universal-web-api",
            "display_name": "web-browser",
        }]

    payload = _build_route_model_entries_payload(
        entries,
        anthropic_version=anthropic_version,
    )
    response = JSONResponse(content=payload)
    response.headers.update(_build_tab_resolution_headers(
        tab_info,
        selector="fixed",
    ))
    return response


async def _chat_with_resolved_tab(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    *,
    tab_index: int,
    resolved_headers: Optional[Dict[str, str]] = None,
):
    body = _apply_response_format_if_needed(body)
    headers = _encode_response_headers(resolved_headers)

    if has_tool_calling_request(
        messages=body.messages,
        tools=body.tools,
        functions=body.functions,
    ):
        if body.stream:
            return StreamingResponse(
                _stream_tool_calling_with_tab_index(request, body, ctx, tab_index),
                media_type="text/event-stream",
                headers=_build_stream_headers(headers),
                request_context=ctx,
            )
        response = await _non_stream_tool_calling_with_tab_index(request, body, ctx, tab_index)
        response.headers.update(headers)
        return response

    if body.stream:
        return StreamingResponse(
            _stream_with_tab_index(request, body, ctx, tab_index),
            media_type="text/event-stream",
            headers=_build_stream_headers(headers),
            request_context=ctx,
        )

    response = await _non_stream_with_tab_index(request, body, ctx, tab_index)
    response.headers.update(headers)
    return response


@router.post("/tab/{tab_index}/v1/chat/completions")
async def chat_with_tab(
    tab_index: int,
    request: Request,
    body: ChatRequest,
    preset_name: Optional[str] = Query(default=None),
    authenticated: bool = Depends(verify_service_auth),
    _route_method: None = Depends(_route_method_guard("fixed_tab")),
):
    """
    使用指定编号的标签页进行聊天

    路径参数：
    - tab_index: 持久化标签页编号（1, 2, 3...）
    """
    client_fp = runtime.cancel_storm_guard.get_client_fingerprint(request)
    await runtime.cancel_storm_guard.maybe_backoff(client_fp)

    if tab_index < 1:
        raise HTTPException(status_code=400, detail="标签页编号必须大于 0")

    browser = runtime.get_browser(auto_connect=False)
    tab_info = _get_tab_info_by_index(browser, tab_index)
    if tab_info is None:
        raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")

    requested_preset_name = str(preset_name or body.preset_name or "").strip()
    resolved_preset_name = None
    if requested_preset_name:
        preset_resolution = _resolve_strict_tab_preset(
            tab_info,
            requested_preset_name,
        )
        resolved_preset_name = preset_resolution["preset_name"]
    if resolved_preset_name != body.preset_name:
        body = body.model_copy(update={"preset_name": resolved_preset_name})

    ctx = runtime.request_manager.create_request(client_fp=client_fp)
    try:
        raw_input_len = sum(len(str(msg.get("content") or "")) for msg in body.messages if isinstance(msg, dict))
        logger.info(f"[DIAG] 接收到的原始请求 messages 总字符长度: {raw_input_len} 字符, 消息数: {len(body.messages)}")
    except Exception as e:
        logger.debug(f"[DIAG] 估算原始请求长度失败: {e}")

    runtime.request_manager.record_request_input(
        ctx,
        body.model_dump(),
        endpoint=f"/tab/{tab_index}/v1/chat/completions",
        route_domain=str((tab_info or {}).get("current_domain") or (tab_info or {}).get("route_domain") or ""),
        tab_index=tab_index,
        preset_name=resolved_preset_name,
    )
    with logger.context(ctx.request_id):
        logger.info(f"开始 (标签页 #{tab_index}, preset={resolved_preset_name or '<follow-tab/default>'})")
        resolved_headers = _build_tab_resolution_headers(
            tab_info,
            selector="fixed",
        )
        return await _chat_with_resolved_tab(
            request,
            body,
            ctx,
            tab_index=tab_index,
            resolved_headers=resolved_headers,
        )


async def _stream_with_tab_index(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    tab_index: int
):
    """使用指定标签页的流式响应"""
    request_context_token = _request_context.set(ctx.request_id)
    disconnect_task = None
    worker_thread = None
    chunk_queue = None
    fast_return_on_audio_media = _should_fast_return_on_audio_media(body)
    fast_returned_on_audio = False
    done_emitted = False

    try:
        disconnect_task = asyncio.create_task(
            watch_client_disconnect(request, ctx, check_interval=0.3)
        )

        browser = runtime.get_browser(auto_connect=False)

        runtime.request_manager.start_request(ctx)

        chunk_queue: queue.Queue = queue.Queue(maxsize=100)

        def worker():
            gen = None
            try:
                # 🔑 使用指定标签页
                gen = browser.execute_workflow_for_tab_index(
                    tab_index,
                    body.messages,
                    stream=True,
                    task_id=ctx.request_id,
                    preset_name=body.preset_name,
                    stop_checker=ctx.should_stop,
                    requested_model=body.model,
                    **({"workflow_variables": body.workflow_variables} if body.workflow_variables else {}),
                )

                for chunk in gen:
                    if ctx.should_stop():
                        cancel_reason = str(ctx.cancel_reason or "unknown")
                        if cancel_reason in {"cleanup", "client_disconnected", "coroutine_cancelled"}:
                            logger.debug(f"工作线程检测到停止: {cancel_reason}")
                        else:
                            logger.info(f"工作线程检测到取消: {cancel_reason}")
                        break
                    if not _put_route_worker_queue_item(chunk_queue, ctx, chunk):
                        logger.debug("工作线程停止入队，结束流式生产(tab_index)")
                        break

            except Exception as e:
                logger.error(f"工作线程异常: {e}")
                _put_route_worker_queue_item(chunk_queue, ctx, ("ERROR", str(e)), final=True)
            finally:
                if gen is not None:
                    try:
                        gen.close()
                    except Exception as e:
                        logger.debug(f"关闭工作流生成器失败（忽略）: {e}")
                _put_route_worker_queue_item(chunk_queue, ctx, None, final=True)

        worker_thread = threading.Thread(
            target=run_in_request_context,
            args=(ctx, worker),
            daemon=True,
        )
        # 供 TabRecovery 判定旧 worker 是否退出（setattr 规避 dataclass 字段限制）
        setattr(ctx, "worker_thread", worker_thread)
        worker_thread.start()

        # Browser workflows can spend several seconds acquiring a tab before
        # their first result chunk. Emit a valid SSE frame immediately so an
        # upstream short read timeout cannot turn one request into a retry loop.
        keepalive_id = f"chatcmpl-{ctx.request_id}"
        initial_keepalive = SSEFormatter.pack_keepalive(
            model=body.model,
            completion_id=keepalive_id,
        )
        runtime.request_manager.capture_response_chunk(ctx, initial_keepalive)
        yield initial_keepalive

        last_sse_emit_at = time.monotonic()
        request_started_at = time.monotonic()
        max_execute_time_sec = get_max_request_execute_time_sec()
        client_disconnected = False
        stop_state = build_stop_sequence_stream_state(
            body.stop,
            single_choice=_is_single_choice_request(body),
            upstream_single_choice=True,
        )

        while True:
            if mark_request_hard_timeout(
                ctx,
                request_started_at,
                max_execute_time_sec,
                label=f"tab_index={tab_index}",
            ):
                runtime.request_manager.capture_error(ctx, "请求执行超过最大绝对超时", code="absolute_request_timeout")
                ctx.mark_failed("absolute_request_timeout")
                done_emitted = True
                yield _pack_error_done("请求执行超过最大绝对超时，已强制中断", "absolute_request_timeout")
                break

            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                client_disconnected = True
                break

            try:
                chunk = await wait_worker_queue_item(
                    chunk_queue,
                    timeout=STREAM_QUEUE_POLL_TIMEOUT,
                )
            except queue.Empty:
                # 兜底：worker 的结束哨兵（None）在队列持续打满时可能被丢弃，
                # 若 worker 线程已退出且队列已排空，直接收尾避免空转到绝对超时。
                if not worker_thread.is_alive() and chunk_queue.empty():
                    logger.warning("工作线程已退出且队列为空（结束标记疑似丢失），提前收尾流式消费")
                    break
                if time.monotonic() - last_sse_emit_at >= SSE_HEARTBEAT_INTERVAL:
                    yield SSEFormatter.pack_keepalive(
                        model=body.model,
                        completion_id=keepalive_id,
                    )
                    last_sse_emit_at = time.monotonic()
                continue

            if chunk is None:
                break

            if isinstance(chunk, tuple) and chunk[0] == "ERROR":
                runtime.request_manager.capture_error(ctx, chunk[1], code="worker_error")
                ctx.mark_failed(chunk[1])
                done_emitted = True
                yield _pack_error_done(f"执行错误: {chunk[1]}", "internal_error")
                break

            outgoing_chunks = filter_openai_stop_sse_chunk(chunk, stop_state, body.model)
            saw_audio_media = False
            for outgoing_chunk in outgoing_chunks:
                emit_chunk, chunk_had_done = _split_sse_done_frame(outgoing_chunk)
                if emit_chunk:
                    runtime.request_manager.capture_response_chunk(ctx, emit_chunk)
                    yield emit_chunk
                    if fast_return_on_audio_media and _has_audio_media(_extract_sse_chunk_media_items(emit_chunk)):
                        saw_audio_media = True
                last_sse_emit_at = time.monotonic()
                error_message = _extract_stream_error_message(emit_chunk or outgoing_chunk)
                if error_message:
                    logger.error(f"流式响应返回错误事件(tab={tab_index}): {error_message}")
                    error_code = next((str(payload["error"].get("code") or "stream_error")
                                       for payload in iter_openai_sse_payloads(emit_chunk or outgoing_chunk)
                                       if isinstance(payload.get("error"), dict)), "stream_error")
                    runtime.request_manager.capture_error(ctx, error_message, code=error_code)
                    ctx.mark_failed(error_code)
                    done_chunk = _pack_done()
                    runtime.request_manager.capture_response_chunk(ctx, done_chunk)
                    done_emitted = True
                    yield done_chunk
                    break
                if chunk_had_done:
                    usage_chunk = _maybe_pack_stream_usage_chunk(body)
                    if usage_chunk:
                        runtime.request_manager.capture_response_chunk(ctx, usage_chunk)
                        yield usage_chunk
                    done_chunk = _pack_done()
                    runtime.request_manager.capture_response_chunk(ctx, done_chunk)
                    done_emitted = True
                    ctx.mark_completed()
                    ctx.request_cancel("stream_done")
                    yield done_chunk
            if done_emitted:
                break
            if stop_state.stopped:
                done_emitted = True
                ctx.request_cancel("stop_sequence")
                ctx.mark_completed()
                break
            if ctx.status == RequestStatus.FAILED:
                break
            if saw_audio_media:
                fast_returned_on_audio = True
                ctx.request_cancel("audio_media_fast_return")
                ctx.mark_completed()
                logger.info(f"流式朗读响应已取得音频，提前结束(tab={tab_index})")
                done_emitted = True
                for fast_return_chunk in _pack_audio_fast_return_chunks(body):
                    runtime.request_manager.capture_response_chunk(ctx, fast_return_chunk)
                    yield fast_return_chunk
                break
            await asyncio.sleep(0)

        if (
            not client_disconnected
            and not done_emitted
            and ctx.status != RequestStatus.FAILED
        ):
            for tail_chunk in flush_openai_stop_state(stop_state, body.model):
                runtime.request_manager.capture_response_chunk(ctx, tail_chunk)
                yield tail_chunk
            usage_chunk = _maybe_pack_stream_usage_chunk(body)
            if usage_chunk:
                runtime.request_manager.capture_response_chunk(ctx, usage_chunk)
                yield usage_chunk
            done_chunk = _pack_done()
            runtime.request_manager.capture_response_chunk(ctx, done_chunk)
            done_emitted = True
            if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
                ctx.mark_completed()
            yield done_chunk

        if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
            ctx.mark_completed()

    except GeneratorExit:
        if not ctx.is_terminal():
            ctx.request_cancel("stream_closed")
        raise
    except asyncio.CancelledError:
        ctx.request_cancel("coroutine_cancelled")
        raise

    except Exception as e:
        logger.error(f"异常: {e}")
        runtime.request_manager.capture_error(ctx, e, code="internal_error")
        ctx.mark_failed(str(getattr(e, "code", None) or e))
        done_emitted = True
        yield _pack_error(f"执行错误: {e!s}", "internal_error")
        yield _pack_done()

    finally:
        with request_finalization(ctx, request_context_token):
            await _cleanup_route_worker_thread(
                worker_thread,
                ctx,
                fast_returned_on_audio=fast_returned_on_audio,
                done_emitted=done_emitted,
            )

            if chunk_queue is not None:
                try:
                    while not chunk_queue.empty():
                        chunk_queue.get_nowait()
                except:
                    pass

            if disconnect_task:
                disconnect_task.cancel()
                try:
                    await disconnect_task
                except asyncio.CancelledError:
                    pass


async def _non_stream_with_tab_index(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    tab_index: int
) -> JSONResponse:
    """使用指定标签页的非流式响应"""
    collected_content = []
    collected_media = []
    error_data = None
    fast_return_on_audio_media = _should_fast_return_on_audio_media(body)
    parse_sse_payloads = _make_buffered_sse_payload_parser()

    async for chunk in _stream_with_tab_index(request, body, ctx, tab_index):
        if isinstance(chunk, str):
            for data in parse_sse_payloads(chunk):
                try:
                    error_data = _consume_non_stream_sse_payload(
                        data,
                        collected_content=collected_content,
                        collected_media=collected_media,
                    )
                    if error_data:
                        break

                    if fast_return_on_audio_media and _has_audio_media(collected_media):
                        ctx.mark_completed()
                        break
                except json.JSONDecodeError:
                    continue
            if error_data or (fast_return_on_audio_media and _has_audio_media(collected_media)):
                break

    if not error_data and not (fast_return_on_audio_media and _has_audio_media(collected_media)):
        for data in parse_sse_payloads.flush():
            error_data = _consume_non_stream_sse_payload(
                data,
                collected_content=collected_content,
                collected_media=collected_media,
            )
            if error_data:
                break

    error_meta = resolve_error_metadata(error_data) or resolve_error_metadata(ctx)
    if error_meta:
        return build_error_response(error_meta)

    if error_data:
        fallback_meta = resolve_error_metadata(error_data)
        if fallback_meta:
            return build_error_response(fallback_meta)
        return JSONResponse(content=error_data, status_code=500)

    full_content = apply_stop_sequences_to_text(
        _cleanup_non_stream_content("".join(collected_content)),
        body.stop,
    )
    response = SSEFormatter.pack_non_stream(
        full_content,
        model=body.model,
        media=_dedupe_media_items(collected_media),
    )
    runtime.request_manager.capture_response_payload(ctx, response)

    return JSONResponse(content=response)


def _execute_browser_non_stream_for_tab(
    browser,
    tab_index: int,
    messages: List[Dict[str, Any]],
    request_id: str,
    preset_name: Optional[str] = None,
    stop_checker=None,
    requested_model: Optional[str] = None,
    workflow_variables: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = None
    for chunk in browser.execute_workflow_for_tab_index(
        tab_index,
        messages,
        stream=False,
        task_id=request_id,
        preset_name=preset_name,
        stop_checker=stop_checker,
        allow_media_postprocess=get_tool_calling_allow_media_postprocess(),
        requested_model=requested_model,
        **({"workflow_variables": workflow_variables} if workflow_variables else {}),
    ):
        payload = chunk

    if not payload:
        raise RuntimeError("empty_browser_response")

    data = decode_browser_non_stream_payload(payload)
    if "error" in data:
        error = data.get("error") or {}
        from app.services.error_metadata import BrowserExecutionError
        raise BrowserExecutionError(error)
    return data


async def _run_tool_calling_async_for_tab(
    browser,
    tab_index: int,
    body: ChatRequest,
    request_id: str,
    stop_checker=None,
    worker_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    legacy_function_call = bool(body.functions) and not bool(body.tools)
    tools, tool_choice = normalize_tool_request(
        tools=body.tools,
        tool_choice=body.tool_choice,
        functions=body.functions,
        function_call=body.function_call,
    )

    try:
        logger.debug(
            "[tab] 请求消息摘要: "
            f"{summarize_messages_for_debug(body.messages)}"
        )
    except Exception as e:
        logger.debug(f"[tab] 请求消息摘要生成失败: {e}")

    tracked_worker_state = worker_state if isinstance(worker_state, dict) else {}

    async def _round_executor(browser_messages: List[Dict[str, str]]) -> str:
        worker_fn = lambda: _extract_assistant_content(
            _execute_browser_non_stream_for_tab(
                browser=browser,
                tab_index=tab_index,
                messages=browser_messages,
                request_id=request_id,
                preset_name=body.preset_name,
                stop_checker=stop_checker,
                requested_model=body.model,
                **({"workflow_variables": body.workflow_variables} if body.workflow_variables else {}),
            )
        )
        if isinstance(tracked_worker_state.get("ctx"), RequestContext):
            return await _run_tracked_route_tool_calling_worker(
                worker_fn,
                ctx=tracked_worker_state["ctx"],
                worker_state=tracked_worker_state,
                label=f"{request_id[:8]}-tab-round",
            )
        return await asyncio.to_thread(worker_fn)

    parsed = await complete_tool_calling_roundtrip_async(
        messages=body.messages,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=(
            False
            if legacy_function_call and body.parallel_tool_calls is None
            else body.parallel_tool_calls
        ),
        round_executor=_round_executor,
        stop_checker=stop_checker,
    )
    if not parsed.get("tool_calls"):
        parsed = dict(parsed)
        parsed["content"] = apply_stop_sequences_to_text(
            str(parsed.get("content") or ""),
            body.stop,
        )
    return build_tool_completion_response(
        body.model,
        parsed,
        legacy_function_call=legacy_function_call,
    )


async def _complete_tool_calling_with_tab_index(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    tab_index: int,
) -> Dict[str, Any]:
    disconnect_task = None
    worker_state: Dict[str, Any] = {"thread": None, "label": None, "ctx": ctx}
    try:
        disconnect_task = asyncio.create_task(
            watch_client_disconnect(request, ctx, check_interval=0.3)
        )

        browser = runtime.get_browser(auto_connect=False)
        runtime.request_manager.start_request(ctx)

        response = await _run_tool_calling_async_for_tab(
            browser,
            tab_index,
            body,
            ctx.request_id,
            ctx.should_stop,
            worker_state=worker_state,
        )
        runtime.request_manager.capture_response_payload(ctx, response)

        if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
            ctx.mark_completed()

        return response

    except _RouteToolCallingExecutionCancelled:
        cancel_reason = _get_route_tool_calling_cancel_reason(ctx)
        if cancel_reason == "absolute_request_timeout":
            runtime.request_manager.capture_error(ctx, "请求执行超过最大绝对超时", code="absolute_request_timeout")
            ctx.mark_failed("absolute_request_timeout")
            raise RuntimeError("absolute_request_timeout")
        if not ctx.should_stop():
            ctx.request_cancel(cancel_reason or "tool_calling_cancelled")
        raise asyncio.CancelledError()
    except asyncio.CancelledError:
        ctx.request_cancel("coroutine_cancelled")
        raise
    except Exception as e:
        logger.error(f"tool_calling_failed(tab={tab_index}): {e}")
        runtime.request_manager.capture_error(ctx, e, code=getattr(e, "code", "tool_calling_failed"))
        ctx.mark_failed(str(getattr(e, "code", None) or e))
        raise
    finally:
        with request_finalization(ctx):
            if disconnect_task:
                disconnect_task.cancel()
                try:
                    await disconnect_task
                except asyncio.CancelledError:
                    pass
            worker_thread = worker_state.get("thread")
            await _cleanup_route_worker_thread(worker_thread, ctx)
            worker_state["thread"] = None
            worker_state["label"] = None


async def _non_stream_tool_calling_with_tab_index(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    tab_index: int,
) -> JSONResponse:
    try:
        response = await _complete_tool_calling_with_tab_index(request, body, ctx, tab_index)
        return JSONResponse(content=response)
    except Exception as e:
        message, code = _format_route_tool_calling_error(e)
        ctx.mark_failed(message)
        runtime.request_manager.capture_error(ctx, message, code=code)
        runtime.request_manager.finish_request(ctx, success=False)
        return JSONResponse(
            content={
                "error": {
                    "message": message,
                    "type": "execution_error",
                    "code": code,
                }
            },
            status_code=500,
        )


async def _stream_tool_calling_with_tab_index(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    tab_index: int,
):
    response_task = None
    keepalive_id = f"chatcmpl-{ctx.request_id}"
    try:
        yield SSEFormatter.pack_keepalive(
            model=body.model,
            completion_id=keepalive_id,
        )
        response_task = asyncio.create_task(
            _complete_tool_calling_with_tab_index(request, body, ctx, tab_index)
        )
        while True:
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(response_task),
                    timeout=SSE_HEARTBEAT_INTERVAL,
                )
                break
            except asyncio.TimeoutError:
                yield SSEFormatter.pack_keepalive(
                    model=body.model,
                    completion_id=keepalive_id,
                )
        message = response.get("choices", [{}])[0].get("message", {}) or {}
        legacy_function_call = bool(body.functions) and not bool(body.tools)
        response_tool_calls = message.get("tool_calls") or []
        if legacy_function_call and not response_tool_calls and isinstance(message.get("function_call"), dict):
            response_tool_calls = [
                {"type": "function", "function": message["function_call"]}
            ]
        parsed = {
            "content": message.get("content"),
            "tool_calls": response_tool_calls,
        }
        for chunk in _iter_stream_chunks_with_optional_usage(
            body,
            iter_tool_stream_chunks(
                body.model,
                parsed,
                legacy_function_call=legacy_function_call,
            ),
        ):
            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                break
            yield chunk
            await asyncio.sleep(0)
    except GeneratorExit:
        if not ctx.is_terminal():
            ctx.request_cancel("stream_closed")
        raise
    except asyncio.CancelledError:
        ctx.request_cancel("coroutine_cancelled")
        raise
    except Exception as e:
        message, code = _format_route_tool_calling_error(e)
        ctx.mark_failed(message)
        runtime.request_manager.capture_error(ctx, message, code=code)
        runtime.request_manager.finish_request(ctx, success=False)
        yield _pack_error(message, code)
        yield _pack_done()
    finally:
        with request_finalization(ctx):
            if response_task is not None and not response_task.done():
                response_task.cancel()
                try:
                    await response_task
                except asyncio.CancelledError:
                    pass
