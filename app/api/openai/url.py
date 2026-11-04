"""``/url`` 路由分组（由 app/api/tab_routes.py 拆出）。"""

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
    _build_claude_route_model_id,
    _build_route_model_entries_payload,
    _build_route_models_payload,
    _build_stream_headers,
    _build_tab_resolution_headers,
    _cleanup_non_stream_content,
    _cleanup_route_worker_thread,
    _consume_non_stream_sse_payload,
    _encode_response_headers,
    _extract_sse_chunk_media_items,
    _format_route_tool_calling_error,
    _get_pool_default_selector,
    _get_route_tool_calling_cancel_reason,
    _has_audio_media,
    _iter_stream_chunks_with_optional_usage,
    _make_buffered_sse_payload_parser,
    _maybe_pack_stream_usage_chunk,
    _normalize_model_name,
    _normalize_tab_selector,
    _pack_audio_fast_return_chunks,
    _put_route_worker_queue_item,
    _resolve_strict_domain_preset,
    _resolve_target_tab,
    _route_method_guard,
    _RouteToolCallingExecutionCancelled,
    _run_tracked_route_tool_calling_worker,
    _should_fast_return_on_audio_media,
    _split_sse_done_frame,
    _unwrap_fastapi_param_value,
    logger,
    router,
)
from app.api.openai.tab import (
    _chat_with_resolved_tab,
    _normalize_optional_tab_index_value,
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
from app.utils.site_url import normalize_route_domain, route_domain_matches


@router.get("/url/{route_domain}/models")
@router.get("/url/{route_domain}/v1/v1/models")
@router.get("/url/{route_domain}/v1/models")
async def list_models_with_route_domain(
    route_domain: str,
    tab_index: Optional[int] = Query(default=None, ge=1),
    selector: Optional[str] = Query(default=None),
    anthropic_version: Optional[str] = Header(None, alias="anthropic-version"),
    authenticated: bool = Depends(verify_service_auth),
    _route_method: None = Depends(_route_method_guard("domain")),
):
    """为域名路由提供 OpenAI 兼容模型列表接口。"""
    route_key = str(route_domain or "").strip()
    # 归一化后为空也要拒绝（例如 "..." / "."）：否则 target_route 一路是空串，
    # _list_candidate_tabs 退化成"任意标签页"，tab_index 的归属校验也会失效。
    if not route_key or not normalize_route_domain(route_key):
        raise HTTPException(status_code=400, detail="域名路由不能为空")

    browser = runtime.get_browser(auto_connect=False)
    normalized_selector = _normalize_tab_selector(
        selector,
        default=_get_pool_default_selector(browser),
    )
    tab_info = _resolve_target_tab(
        browser,
        route_domain=route_key,
        tab_index=tab_index,
        selector=normalized_selector,
    )

    if route_domain_matches(route_key, "arena.ai"):
        from app.services.config_engine import config_engine

        catalog_preset = get_arena_direct_catalog_for_tab(config_engine, tab_info)
        arena_entries = build_openai_model_entries(
            list_arena_direct_models(
                browser,
                catalog_config=(catalog_preset or {}).get("catalog"),
            ) if catalog_preset else [],
            created=MODEL_LIST_CREATED,
        )
        if arena_entries:
            payload = _build_route_model_entries_payload(
                arena_entries,
                anthropic_version=anthropic_version,
            )
            response = JSONResponse(content=payload)
            response.headers.update(
                _build_tab_resolution_headers(
                    tab_info,
                    route_domain=route_key,
                    selector=normalized_selector,
                )
            )
            return response

    payload = _build_route_models_payload(
        model_id=_build_claude_route_model_id(route_key),
        display_name=f"Claude Code route: {route_key}",
        anthropic_version=anthropic_version,
    )
    response = JSONResponse(content=payload)
    response.headers.update(
        _build_tab_resolution_headers(
            tab_info,
            route_domain=route_key,
            selector=("tab_index" if tab_index is not None else normalized_selector),
        )
    )
    return response


@router.get("/url/{route_domain}/{preset_name}/models")
@router.get("/url/{route_domain}/{preset_name}/v1/v1/models")
@router.get("/url/{route_domain}/{preset_name}/v1/models")
async def list_models_with_route_domain_and_preset(
    route_domain: str,
    preset_name: str,
    tab_index: Optional[int] = Query(default=None, ge=1),
    selector: Optional[str] = Query(default=None),
    anthropic_version: Optional[str] = Header(None, alias="anthropic-version"),
    authenticated: bool = Depends(verify_service_auth),
    _route_method: None = Depends(_route_method_guard("domain")),
):
    """为域名+预设路径风格提供 OpenAI 兼容模型列表接口。"""
    route_key = str(route_domain or "").strip()
    # 归一化后为空也要拒绝（例如 "..." / "."）：否则 target_route 一路是空串，
    # _list_candidate_tabs 退化成"任意标签页"，tab_index 的归属校验也会失效。
    if not route_key or not normalize_route_domain(route_key):
        raise HTTPException(status_code=400, detail="域名路由不能为空")
    preset_resolution = _resolve_strict_domain_preset(route_key, preset_name)

    browser = runtime.get_browser(auto_connect=False)
    normalized_selector = _normalize_tab_selector(
        selector,
        default=_get_pool_default_selector(browser),
    )
    tab_info = _resolve_target_tab(
        browser,
        route_domain=route_key,
        tab_index=tab_index,
        selector=normalized_selector,
    )

    from app.services.config_engine import config_engine

    catalog_preset = get_arena_direct_catalog_for_tab(
        config_engine,
        tab_info,
        preset_name=preset_resolution["preset_name"],
    )
    if catalog_preset:
        payload = _build_route_model_entries_payload(
            build_openai_model_entries(
                list_arena_direct_models(
                    browser,
                    catalog_config=catalog_preset["catalog"],
                ),
                created=MODEL_LIST_CREATED,
            ),
            anthropic_version=anthropic_version,
        )
    else:
        payload = _build_route_models_payload(
            model_id=_build_claude_route_model_id(f"{route_key}-{preset_resolution['preset_name']}"),
            display_name=f"Claude Code route: {route_key} / {preset_resolution['preset_name']}",
            anthropic_version=anthropic_version,
        )
    response = JSONResponse(content=payload)
    response.headers.update(
        _build_tab_resolution_headers(
            tab_info,
            route_domain=route_key,
            selector=("tab_index" if tab_index is not None else normalized_selector),
            preset_name=preset_resolution["preset_name"],
        )
    )
    return response


async def _chat_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    *,
    route_domain: str,
    route_group_id: Optional[str] = None,
    allocation_mode: Optional[str] = None,
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
                _stream_tool_calling_with_route_domain(
                    request,
                    body,
                    ctx,
                    route_domain,
                    route_group_id=route_group_id,
                    allocation_mode=allocation_mode,
                ),
                media_type="text/event-stream",
                headers=_build_stream_headers(headers),
                request_context=ctx,
            )
        response = await _non_stream_tool_calling_with_route_domain(
            request,
            body,
            ctx,
            route_domain,
            route_group_id=route_group_id,
            allocation_mode=allocation_mode,
        )
        response.headers.update(headers)
        return response

    if body.stream:
        return StreamingResponse(
            _stream_with_route_domain(
                request,
                body,
                ctx,
                route_domain,
                route_group_id=route_group_id,
                allocation_mode=allocation_mode,
            ),
            media_type="text/event-stream",
            headers=_build_stream_headers(headers),
            request_context=ctx,
        )

    response = await _non_stream_with_route_domain(
        request,
        body,
        ctx,
        route_domain,
        route_group_id=route_group_id,
        allocation_mode=allocation_mode,
    )
    response.headers.update(headers)
    return response


@router.post("/url/{route_domain}/v1/chat/completions")
async def chat_with_route_domain(
    route_domain: str,
    request: Request,
    body: ChatRequest,
    tab_index: Optional[int] = Query(default=None, ge=1),
    selector: Optional[str] = Query(default=None),
    preset_name: Optional[str] = Query(default=None),
    authenticated: bool = Depends(verify_service_auth),
    _route_method: None = Depends(_route_method_guard("domain")),
):
    """使用指定域名路由匹配的标签页进行聊天。"""
    client_fp = runtime.cancel_storm_guard.get_client_fingerprint(request)
    await runtime.cancel_storm_guard.maybe_backoff(client_fp)

    tab_index = _normalize_optional_tab_index_value(tab_index)
    selector = _unwrap_fastapi_param_value(selector)
    preset_name = _unwrap_fastapi_param_value(preset_name)

    route_key = str(route_domain or "").strip()
    # 归一化后为空也要拒绝（例如 "..." / "."）：否则 target_route 一路是空串，
    # _list_candidate_tabs 退化成"任意标签页"，tab_index 的归属校验也会失效。
    if not route_key or not normalize_route_domain(route_key):
        raise HTTPException(status_code=400, detail="域名路由不能为空")

    resolved_preset_name = str(preset_name or body.preset_name or "").strip() or None
    if resolved_preset_name != body.preset_name:
        body = body.model_copy(update={"preset_name": resolved_preset_name})

    browser = runtime.get_browser(auto_connect=False)
    normalized_selector = _normalize_tab_selector(
        selector,
        default=_get_pool_default_selector(browser),
    )

    tab_info = None
    resolved_tab_index = None
    if tab_index is not None:
        tab_info = _resolve_target_tab(
            browser,
            route_domain=route_key,
            tab_index=tab_index,
            selector=normalized_selector,
        )
        resolved_tab_index = int(tab_info.get("persistent_index") or 0)
        if resolved_tab_index < 1:
            raise HTTPException(status_code=500, detail="resolved_tab_index_invalid")

    resolved_headers = _build_tab_resolution_headers(
        tab_info,
        route_domain=route_key,
        selector=("tab_index" if tab_index is not None else normalized_selector),
    )

    ctx = runtime.request_manager.create_request(client_fp=client_fp)
    try:
        raw_input_len = sum(len(str(msg.get("content") or "")) for msg in body.messages if isinstance(msg, dict))
        logger.info(f"[DIAG] 接收到的原始请求 messages 总字符长度: {raw_input_len} 字符, 消息数: {len(body.messages)}")
    except Exception as e:
        logger.debug(f"[DIAG] 估算原始请求长度失败: {e}")

    runtime.request_manager.record_request_input(
        ctx,
        body.model_dump(),
        endpoint=f"/url/{route_key}/v1/chat/completions",
        route_domain=str((tab_info or {}).get("current_domain") or (tab_info or {}).get("route_domain") or route_key),
        tab_index=resolved_tab_index,
        preset_name=resolved_preset_name,
    )
    with logger.context(ctx.request_id):
        if tab_index is not None:
            logger.info(
                f"[ROUTE:TAB] 域名路由分发指定标签页: route={route_key}, tab_index=#{resolved_tab_index}, "
                f"tab_url={(tab_info or {}).get('url')}, model={body.model!r}, "
                f"preset={resolved_preset_name or '<follow-tab/default>'}"
            )
            return await _chat_with_resolved_tab(
                request,
                body,
                ctx,
                tab_index=resolved_tab_index,
                resolved_headers=resolved_headers,
            )

        logger.info(
            f"开始 (域名路由 {route_key} -> 动态同站点标签页, "
            f"selector={normalized_selector}, "
            f"preset={resolved_preset_name or '<follow-tab/default>'})"
        )
        return await _chat_with_route_domain(
            request,
            body,
            ctx,
            route_domain=route_key,
            allocation_mode=normalized_selector,
            resolved_headers=resolved_headers,
        )


async def chat_with_exposed_model_name(
    model_name: str,
    request: Request,
    body: ChatRequest,
    authenticated: bool = True,
):
    """使用暴露模型名匹配同名标签页，并在同名集合中轮询。"""
    client_fp = runtime.cancel_storm_guard.get_client_fingerprint(request)
    await runtime.cancel_storm_guard.maybe_backoff(client_fp)

    model_label = _normalize_model_name(model_name)
    if not model_label:
        raise HTTPException(status_code=400, detail="模型显示名称不能为空")

    browser = runtime.get_browser(auto_connect=False)
    tab_info = _resolve_target_tab(
        browser,
        model_name=model_label,
        selector="round_robin",
    )
    resolved_tab_index = int(tab_info.get("persistent_index") or 0)
    if resolved_tab_index < 1:
        raise HTTPException(status_code=500, detail="resolved_tab_index_invalid")

    resolved_headers = _build_tab_resolution_headers(
        tab_info,
        model_name=model_label,
        selector="model_name_round_robin",
    )

    ctx = runtime.request_manager.create_request(client_fp=client_fp)
    try:
        raw_input_len = sum(len(str(msg.get("content") or "")) for msg in body.messages if isinstance(msg, dict))
        logger.info(f"[DIAG] 接收到的原始请求 messages 总字符长度: {raw_input_len} 字符, 消息数: {len(body.messages)}")
    except Exception as e:
        logger.debug(f"[DIAG] 估算原始请求长度失败: {e}")

    runtime.request_manager.record_request_input(
        ctx,
        body.model_dump(),
        endpoint=f"/model-name/{model_label}/v1/chat/completions",
        route_domain=str(tab_info.get("current_domain") or tab_info.get("route_domain") or ""),
        tab_index=resolved_tab_index,
        preset_name=body.preset_name,
    )
    with logger.context(ctx.request_id):
        logger.info(
            f"开始 (模型显示名称 {model_label} -> 标签页 #{resolved_tab_index}, "
            f"preset={body.preset_name or '<follow-tab/default>'})"
        )
        return await _chat_with_resolved_tab(
            request,
            body,
            ctx,
            tab_index=resolved_tab_index,
            resolved_headers=resolved_headers,
        )


@router.post("/url/{route_domain}/{preset_name}/v1/chat/completions")
async def chat_with_route_domain_and_preset(
    route_domain: str,
    preset_name: str,
    request: Request,
    body: ChatRequest,
    tab_index: Optional[int] = Query(default=None, ge=1),
    selector: Optional[str] = Query(default=None),
    authenticated: bool = Depends(verify_service_auth),
    _route_method: None = Depends(_route_method_guard("domain")),
):
    """使用域名+预设路径风格进行聊天。路径中的预设优先级最高。"""
    route_key = str(route_domain or "").strip()
    preset_resolution = _resolve_strict_domain_preset(route_key, preset_name)
    forced_preset_name = preset_resolution["preset_name"]
    if forced_preset_name != body.preset_name:
        body = body.model_copy(update={"preset_name": forced_preset_name})

    return await chat_with_route_domain(
        route_domain=route_key,
        request=request,
        body=body,
        tab_index=tab_index,
        selector=selector,
        preset_name=forced_preset_name,
        authenticated=authenticated,
    )


async def _stream_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    route_domain: str,
    route_group_id: Optional[str] = None,
    allocation_mode: Optional[str] = None,
):
    """使用指定域名路由的流式响应"""
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

        chunk_queue = queue.Queue(maxsize=100)

        def worker():
            gen = None
            try:
                if route_group_id:
                    gen = browser.execute_workflow_for_route_group(
                        route_group_id,
                        body.messages,
                        stream=True,
                        task_id=ctx.request_id,
                        preset_name=body.preset_name,
                        stop_checker=ctx.should_stop,
                        allocation_mode=allocation_mode,
                        requested_model=body.model,
                        **({"workflow_variables": body.workflow_variables} if body.workflow_variables else {}),
                    )
                else:
                    gen = browser.execute_workflow_for_route_domain(
                        route_domain,
                        body.messages,
                        stream=True,
                        task_id=ctx.request_id,
                        preset_name=body.preset_name,
                        stop_checker=ctx.should_stop,
                        allocation_mode=allocation_mode,
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
                        logger.debug(
                            "工作线程停止入队，结束流式生产("
                            f"{'route_group=' + route_group_id if route_group_id else 'route_domain'}"
                            ")"
                        )
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
                label=(f"route_group={route_group_id}" if route_group_id else f"route_domain={route_domain}"),
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
                    route_label = f"route_group={route_group_id}" if route_group_id else f"route_domain={route_domain}"
                    logger.error(f"流式响应返回错误事件({route_label}): {error_message}")
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
                route_label = f"route_group={route_group_id}" if route_group_id else f"route_domain={route_domain}"
                logger.info(f"流式朗读响应已取得音频，提前结束({route_label})")
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


async def _non_stream_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    route_domain: str,
    route_group_id: Optional[str] = None,
    allocation_mode: Optional[str] = None,
) -> JSONResponse:
    """使用指定域名路由的非流式响应"""
    collected_content = []
    collected_media = []
    error_data = None
    fast_return_on_audio_media = _should_fast_return_on_audio_media(body)
    parse_sse_payloads = _make_buffered_sse_payload_parser()

    async for chunk in _stream_with_route_domain(
        request,
        body,
        ctx,
        route_domain,
        route_group_id=route_group_id,
        allocation_mode=allocation_mode,
    ):
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


def _execute_browser_non_stream_for_route_domain(
    browser,
    route_domain: str,
    messages: List[Dict[str, Any]],
    request_id: str,
    preset_name: Optional[str] = None,
    stop_checker=None,
    allocation_mode: Optional[str] = None,
    route_group_id: Optional[str] = None,
    requested_model: Optional[str] = None,
    workflow_variables: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = None
    execute = (
        browser.execute_workflow_for_route_group
        if route_group_id
        else browser.execute_workflow_for_route_domain
    )
    route_key = route_group_id or route_domain
    for chunk in execute(
        route_key,
        messages,
        stream=False,
        task_id=request_id,
        preset_name=preset_name,
        stop_checker=stop_checker,
        allocation_mode=allocation_mode,
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


async def _run_tool_calling_async_for_route_domain(
    browser,
    route_domain: str,
    body: ChatRequest,
    request_id: str,
    stop_checker=None,
    worker_state: Optional[Dict[str, Any]] = None,
    allocation_mode: Optional[str] = None,
    route_group_id: Optional[str] = None,
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
            "[route] 请求消息摘要: "
            f"{summarize_messages_for_debug(body.messages)}"
        )
    except Exception as e:
        logger.debug(f"[route] 请求消息摘要生成失败: {e}")

    tracked_worker_state = worker_state if isinstance(worker_state, dict) else {}

    async def _round_executor(browser_messages: List[Dict[str, str]]) -> str:
        worker_fn = lambda: _extract_assistant_content(
            _execute_browser_non_stream_for_route_domain(
                browser=browser,
                route_domain=route_domain,
                messages=browser_messages,
                request_id=request_id,
                preset_name=body.preset_name,
                stop_checker=stop_checker,
                allocation_mode=allocation_mode,
                route_group_id=route_group_id,
                requested_model=body.model,
                **({"workflow_variables": body.workflow_variables} if body.workflow_variables else {}),
            )
        )
        if isinstance(tracked_worker_state.get("ctx"), RequestContext):
            return await _run_tracked_route_tool_calling_worker(
                worker_fn,
                ctx=tracked_worker_state["ctx"],
                worker_state=tracked_worker_state,
                label=f"{request_id[:8]}-route-round",
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


async def _complete_tool_calling_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    route_domain: str,
    allocation_mode: Optional[str] = None,
    route_group_id: Optional[str] = None,
) -> Dict[str, Any]:
    disconnect_task = None
    worker_state: Dict[str, Any] = {"thread": None, "label": None, "ctx": ctx}
    try:
        disconnect_task = asyncio.create_task(
            watch_client_disconnect(request, ctx, check_interval=0.3)
        )

        browser = runtime.get_browser(auto_connect=False)
        runtime.request_manager.start_request(ctx)

        response = await _run_tool_calling_async_for_route_domain(
            browser,
            route_domain,
            body,
            ctx.request_id,
            ctx.should_stop,
            worker_state=worker_state,
            allocation_mode=allocation_mode,
            route_group_id=route_group_id,
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
        route_label = f"route_group={route_group_id}" if route_group_id else f"route_domain={route_domain}"
        logger.error(f"tool_calling_failed({route_label}): {e}")
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


async def _non_stream_tool_calling_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    route_domain: str,
    allocation_mode: Optional[str] = None,
    route_group_id: Optional[str] = None,
) -> JSONResponse:
    try:
        response = await _complete_tool_calling_with_route_domain(
            request,
            body,
            ctx,
            route_domain,
            allocation_mode=allocation_mode,
            route_group_id=route_group_id,
        )
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


async def _stream_tool_calling_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    route_domain: str,
    allocation_mode: Optional[str] = None,
    route_group_id: Optional[str] = None,
):
    response_task = None
    keepalive_id = f"chatcmpl-{ctx.request_id}"
    try:
        yield SSEFormatter.pack_keepalive(
            model=body.model,
            completion_id=keepalive_id,
        )
        response_task = asyncio.create_task(
            _complete_tool_calling_with_route_domain(
                request,
                body,
                ctx,
                route_domain,
                allocation_mode=allocation_mode,
                route_group_id=route_group_id,
            )
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
