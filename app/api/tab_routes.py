"""
app/api/tab_routes.py - 标签页路由

职责：
- /api/tab-pool/tabs - 获取标签页列表
- /tab/{index}/v1/chat/completions - 指定标签页的聊天接口
- /url/{domain}/v1/chat/completions - 按域名路由选择标签页的聊天接口
"""

import json
import random
import re
import time
import asyncio
import queue
import threading
from pathlib import Path
from typing import Optional, Any, Dict, List

from fastapi import APIRouter, Request, HTTPException, Header, Depends, Query
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from app.core.config import AppConfig, get_logger, SSEFormatter
from app.core import get_browser
from app.services.request_manager import (
    request_manager,
    RequestContext,
    RequestStatus,
    watch_client_disconnect
)
from app.services.tool_calling import (
    build_tool_completion_response,
    complete_tool_calling_roundtrip,
    has_tool_calling_request,
    iter_tool_stream_chunks,
    normalize_tool_request,
    summarize_messages_for_debug,
)
from app.utils.site_url import normalize_route_domain, route_domain_matches

logger = get_logger("API.TAB")

router = APIRouter()
FOLLOW_DEFAULT_PRESET = "__DEFAULT__"
STREAM_QUEUE_POLL_TIMEOUT = 0.5
SSE_HEARTBEAT_INTERVAL = 15.0
TAB_POOL_ALLOCATION_OPTIONS = [
    {"value": "first_idle", "label": "优先空闲"},
    {"value": "round_robin", "label": "轮询"},
]
TAB_SELECTOR_OPTIONS = {"first_idle", "round_robin", "random"}
_route_round_robin_cursor: Dict[str, int] = {}
_route_round_robin_lock = threading.Lock()


def _read_browser_config() -> Dict[str, Any]:
    config_path = Path("config/browser_config.json")
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _write_browser_config(payload: Dict[str, Any]) -> None:
    config_path = Path("config/browser_config.json")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(str(config_path) + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.flush()
    tmp_path.replace(config_path)


def _extract_stream_error_message(chunk: Any) -> str:
    if not isinstance(chunk, str) or not chunk.startswith("data: "):
        return ""
    try:
        data_str = chunk[6:].strip()
        if not data_str or data_str == "[DONE]":
            return ""
        data = json.loads(data_str)
        error = data.get("error")
        if not isinstance(error, dict):
            return ""
        return str(error.get("message") or "").strip()
    except Exception:
        return ""


def _extract_chunk_media_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    media_items: List[Dict[str, Any]] = []

    top_level_media = data.get("media")
    if isinstance(top_level_media, list):
        media_items.extend(item for item in top_level_media if isinstance(item, dict))

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        delta = choices[0].get("delta", {})
        if isinstance(delta, dict):
            delta_media = delta.get("media")
            if isinstance(delta_media, list):
                media_items.extend(item for item in delta_media if isinstance(item, dict))

    return media_items


def _dedupe_media_items(media_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()

    for item in media_items or []:
        media_type = str(item.get("media_type") or "").strip().lower()
        ref = str(item.get("url") or item.get("data_uri") or "").strip()
        key = (media_type, ref)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped


def _cleanup_non_stream_content(content: str) -> str:
    placeholder_pattern = re.compile(
        r"^\s*https?://(?:[\w.-]+\.)?googleusercontent\.com/(?:image_generation_content|generated_music_content)/\d+\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    cleaned = placeholder_pattern.sub("", content or "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _normalize_tab_selector(value: str, default: str = "first_idle") -> str:
    selector = str(value or "").strip().lower()
    if selector in TAB_SELECTOR_OPTIONS:
        return selector
    return default


def _get_tab_info_by_index(browser, tab_index: int) -> Optional[Dict[str, Any]]:
    tabs = browser.tab_pool.get_tabs_with_index()
    for item in tabs:
        if int(item.get("persistent_index") or 0) == int(tab_index):
            return item
    return None


def _list_candidate_tabs(browser, route_domain: str = "") -> List[Dict[str, Any]]:
    tabs = browser.tab_pool.get_tabs_with_index()
    target = normalize_route_domain(route_domain)
    if not target:
        return tabs

    result: List[Dict[str, Any]] = []
    for item in tabs:
        actual_domain = str(item.get("current_domain") or item.get("route_domain") or "").strip()
        if actual_domain and route_domain_matches(target, actual_domain):
            result.append(item)
    return result


def _select_round_robin_tab(candidates: List[Dict[str, Any]], cursor_key: str) -> Dict[str, Any]:
    ordered = sorted(candidates, key=lambda item: int(item.get("persistent_index") or 0))
    if not ordered:
        raise HTTPException(status_code=404, detail="没有可用标签页")

    with _route_round_robin_lock:
        last_index = _route_round_robin_cursor.get(cursor_key, -1)
        next_pos = 0
        for idx, item in enumerate(ordered):
            current_index = int(item.get("persistent_index") or 0)
            if current_index > last_index:
                next_pos = idx
                break
        else:
            next_pos = 0

        chosen = ordered[next_pos]
        _route_round_robin_cursor[cursor_key] = int(chosen.get("persistent_index") or 0)
        return chosen


def _resolve_target_tab(
    browser,
    *,
    route_domain: str = "",
    tab_index: Optional[int] = None,
    selector: str = "first_idle",
) -> Dict[str, Any]:
    target_route = normalize_route_domain(route_domain)

    if tab_index is not None:
        tab_info = _get_tab_info_by_index(browser, int(tab_index))
        if tab_info is None:
            raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")
        actual_domain = str(tab_info.get("current_domain") or tab_info.get("route_domain") or "").strip()
        if target_route and not route_domain_matches(target_route, actual_domain):
            raise HTTPException(
                status_code=400,
                detail=f"标签页 #{tab_index} 不属于域名路由 '{target_route}'",
            )
        return tab_info

    candidates = _list_candidate_tabs(browser, target_route)
    if not candidates:
        if target_route:
            raise HTTPException(status_code=404, detail=f"域名路由 '{target_route}' 没有匹配的标签页")
        raise HTTPException(status_code=404, detail="没有匹配的标签页")

    idle_candidates = [
        item for item in candidates
        if str(item.get("status") or "").strip().lower() == "idle"
    ]
    pool = idle_candidates or candidates
    selector = _normalize_tab_selector(selector)

    if selector == "random":
        return random.choice(pool)
    if selector == "round_robin":
        cursor_key = target_route or "__all__"
        return _select_round_robin_tab(pool, cursor_key)

    return sorted(pool, key=lambda item: int(item.get("persistent_index") or 0))[0]


def _build_tab_resolution_headers(
    tab_info: Optional[Dict[str, Any]],
    *,
    route_domain: str = "",
    selector: str = "",
) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if not tab_info:
        return headers

    tab_index = int(tab_info.get("persistent_index") or 0)
    if tab_index > 0:
        headers["X-Resolved-Tab-Index"] = str(tab_index)

    tab_id = str(tab_info.get("id") or "").strip()
    if tab_id:
        headers["X-Resolved-Tab-Id"] = tab_id

    current_url = str(tab_info.get("url") or "").strip()
    if current_url:
        headers["X-Resolved-Tab-Url"] = current_url

    current_domain = str(tab_info.get("current_domain") or tab_info.get("route_domain") or route_domain or "").strip()
    if current_domain:
        headers["X-Resolved-Route-Domain"] = current_domain

    if selector:
        headers["X-Tab-Selection-Mode"] = selector

    return headers


def _build_stream_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    if extra:
        headers.update(extra)
    return headers

# ================= 请求模型 =================

class ChatRequest(BaseModel):
    """聊天请求模型"""
    model: str = Field(default="gpt-3.5-turbo")
    messages: list = Field(...)
    stream: Optional[bool] = Field(default=False)
    temperature: Optional[float] = Field(default=0.7, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    tools: Optional[list] = Field(default=None)
    tool_choice: Optional[Any] = Field(default=None)
    parallel_tool_calls: Optional[bool] = Field(default=None)
    functions: Optional[list] = Field(default=None)
    function_call: Optional[Any] = Field(default=None)
    preset_name: Optional[str] = Field(default=None)


class TabPoolConfigRequest(BaseModel):
    """标签页池配置更新请求。"""
    allocation_mode: str = Field(default="first_idle")


# ================= 认证依赖 =================

async def verify_auth(authorization: Optional[str] = Header(None)) -> bool:
    """验证 Bearer Token"""
    if not AppConfig.is_auth_enabled():
        return True

    if not AppConfig.AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="服务配置错误")

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = authorization.replace("Bearer ", "").strip()

    if token != AppConfig.get_auth_token():
        raise HTTPException(
            status_code=401,
            detail="认证令牌无效",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return True


# ================= 标签页池 API =================

@router.get("/api/tab-pool/tabs")
async def get_tab_pool_tabs(authenticated: bool = Depends(verify_auth)):
    """
    获取所有标签页及其持久编号和预设信息
    
    返回格式：
    {
        "tabs": [
            {
                "persistent_index": 1,
                "id": "gpt_1",
                "url": "https://chatgpt.com/",
                "status": "idle",
                "route_prefix": "/url/chatgpt.com",
                "tab_route_prefix": "/tab/1",
                "domain_route_prefix": "/url/chatgpt.com",
                "preset_name": null,
                "available_presets": ["主预设", "无临时聊天"]
            },
            ...
        ],
        "count": 3
    }
    """
    try:
        browser = get_browser(auto_connect=False)
        tabs = browser.tab_pool.get_tabs_with_index()
        pool_status = browser.tab_pool.get_status()
        
        # 🆕 为每个标签页附加可用预设列表
        try:
            from app.services.config_engine import config_engine
            for tab_info in tabs:
                domain = tab_info.get("current_domain", "")
                if domain:
                    tab_info["available_presets"] = config_engine.list_presets(domain)
                    default_preset = config_engine.get_default_preset(domain)
                    tab_info["default_preset"] = default_preset
                    tab_info["effective_preset_name"] = tab_info.get("preset_name") or default_preset
                    tab_info["is_using_default_preset"] = not bool(tab_info.get("preset_name"))
                else:
                    tab_info["available_presets"] = []
                    tab_info["default_preset"] = None
                    tab_info["effective_preset_name"] = tab_info.get("preset_name")
                    tab_info["is_using_default_preset"] = not bool(tab_info.get("preset_name"))
        except Exception as e:
            logger.debug(f"获取预设列表失败: {e}")
            for tab_info in tabs:
                tab_info["available_presets"] = []
                tab_info["default_preset"] = None
                tab_info["effective_preset_name"] = tab_info.get("preset_name")
                tab_info["is_using_default_preset"] = not bool(tab_info.get("preset_name"))
        
        return {
            "tabs": tabs,
            "count": len(tabs),
            "allocation_mode": pool_status.get("allocation_mode", "first_idle"),
            "allocation_mode_options": TAB_POOL_ALLOCATION_OPTIONS,
        }
    except Exception as e:
        logger.error(f"获取标签页列表失败: {e}")
        return {
            "tabs": [],
            "count": 0,
            "error": str(e),
            "allocation_mode": "first_idle",
            "allocation_mode_options": TAB_POOL_ALLOCATION_OPTIONS,
        }


@router.put("/api/tab-pool/config")
async def update_tab_pool_config(
    body: TabPoolConfigRequest,
    authenticated: bool = Depends(verify_auth)
):
    """更新标签页池运行模式并持久化到 browser_config.json。"""
    allocation_mode = str(body.allocation_mode or "").strip().lower()
    if allocation_mode not in {"first_idle", "round_robin"}:
        raise HTTPException(status_code=400, detail="invalid_allocation_mode")

    try:
        config = _read_browser_config()
        tab_pool_config = config.get("tab_pool") or {}
        if not isinstance(tab_pool_config, dict):
            tab_pool_config = {}
        tab_pool_config["allocation_mode"] = allocation_mode
        config["tab_pool"] = tab_pool_config
        _write_browser_config(config)

        try:
            from app.core.config import BrowserConstants
            if hasattr(BrowserConstants, "reload"):
                BrowserConstants.reload()
        except Exception as reload_error:
            logger.warning(f"热重载浏览器常量失败: {reload_error}")

        pool_synced = False
        try:
            browser = get_browser(auto_connect=False)
            browser.tab_pool.apply_runtime_config(allocation_mode=allocation_mode)
            pool_synced = True
        except Exception as sync_error:
            logger.warning(f"同步运行中标签页池配置失败: {sync_error}")

        return {
            "success": True,
            "message": "标签页池分配模式已更新",
            "allocation_mode": allocation_mode,
            "allocation_mode_options": TAB_POOL_ALLOCATION_OPTIONS,
            "pool_synced": pool_synced,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新标签页池配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ================= 指定标签页的聊天 API =================

@router.get("/tab/{tab_index}/v1/models")
async def list_models_with_tab(
    tab_index: int,
    authenticated: bool = Depends(verify_auth)
):
    """为指定标签页路由提供 OpenAI 兼容模型列表接口。"""
    if tab_index < 1:
        raise HTTPException(status_code=400, detail="标签页编号必须大于 0")

    try:
        browser = get_browser(auto_connect=False)
        session = browser.tab_pool.acquire_by_index(
            tab_index,
            task_id=f"models_tab_{tab_index}_{int(time.time() * 1000)}",
            timeout=0.1,
        )
        if session is None:
            raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不可用或不存在")
        browser.tab_pool.release(session.id)
    except HTTPException:
        raise
    except Exception as e:
        logger.debug(f"标签页模型列表校验失败（忽略）: {e}")

    return {
        "object": "list",
        "data": [
            {
                "id": "web-browser",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "universal-web-api"
            }
        ]
    }


@router.get("/url/{route_domain}/v1/models")
async def list_models_with_route_domain(
    route_domain: str,
    tab_index: Optional[int] = Query(default=None, ge=1),
    selector: str = Query(default="first_idle"),
    authenticated: bool = Depends(verify_auth)
):
    """为域名路由提供 OpenAI 兼容模型列表接口。"""
    route_key = str(route_domain or "").strip()
    if not route_key:
        raise HTTPException(status_code=400, detail="域名路由不能为空")

    browser = get_browser(auto_connect=False)
    normalized_selector = _normalize_tab_selector(selector, default="first_idle")
    tab_info = _resolve_target_tab(
        browser,
        route_domain=route_key,
        tab_index=tab_index,
        selector=normalized_selector,
    )

    payload = {
        "object": "list",
        "data": [
            {
                "id": "web-browser",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "universal-web-api"
            }
        ]
    }
    response = JSONResponse(content=payload)
    response.headers.update(
        _build_tab_resolution_headers(
            tab_info,
            route_domain=route_key,
            selector=("tab_index" if tab_index is not None else normalized_selector),
        )
    )
    return response


@router.get("/url/{route_domain}/{preset_name}/v1/models")
async def list_models_with_route_domain_and_preset(
    route_domain: str,
    preset_name: str,
    tab_index: Optional[int] = Query(default=None, ge=1),
    selector: str = Query(default="first_idle"),
    authenticated: bool = Depends(verify_auth)
):
    """为域名+预设路径风格提供 OpenAI 兼容模型列表接口。"""
    _ = str(preset_name or "").strip()
    return await list_models_with_route_domain(
        route_domain=route_domain,
        tab_index=tab_index,
        selector=selector,
        authenticated=authenticated,
    )


async def _chat_with_resolved_tab(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    *,
    tab_index: int,
    resolved_headers: Optional[Dict[str, str]] = None,
):
    headers = resolved_headers or {}

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
            )
        response = await _non_stream_tool_calling_with_tab_index(request, body, ctx, tab_index)
        response.headers.update(headers)
        return response

    if body.stream:
        return StreamingResponse(
            _stream_with_tab_index(request, body, ctx, tab_index),
            media_type="text/event-stream",
            headers=_build_stream_headers(headers),
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
    authenticated: bool = Depends(verify_auth)
):
    """
    使用指定编号的标签页进行聊天
    
    路径参数：
    - tab_index: 持久化标签页编号（1, 2, 3...）
    """
    if tab_index < 1:
        raise HTTPException(status_code=400, detail="标签页编号必须大于 0")

    resolved_preset_name = str(preset_name or body.preset_name or "").strip() or None
    if resolved_preset_name != body.preset_name:
        body = body.model_copy(update={"preset_name": resolved_preset_name})
    
    ctx = request_manager.create_request()
    with logger.context(ctx.request_id):
        logger.info(f"开始 (标签页 #{tab_index}, preset={resolved_preset_name or '<follow-tab/default>'})")
        browser = get_browser(auto_connect=False)
        tab_info = _get_tab_info_by_index(browser, tab_index)
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


@router.post("/url/{route_domain}/v1/chat/completions")
async def chat_with_route_domain(
    route_domain: str,
    request: Request,
    body: ChatRequest,
    tab_index: Optional[int] = Query(default=None, ge=1),
    selector: str = Query(default="first_idle"),
    preset_name: Optional[str] = Query(default=None),
    authenticated: bool = Depends(verify_auth)
):
    """使用指定域名路由匹配的标签页进行聊天。"""
    route_key = str(route_domain or "").strip()
    if not route_key:
        raise HTTPException(status_code=400, detail="域名路由不能为空")

    resolved_preset_name = str(preset_name or body.preset_name or "").strip() or None
    if resolved_preset_name != body.preset_name:
        body = body.model_copy(update={"preset_name": resolved_preset_name})

    browser = get_browser(auto_connect=False)
    normalized_selector = _normalize_tab_selector(selector, default="first_idle")
    tab_info = _resolve_target_tab(
        browser,
        route_domain=route_key,
        tab_index=tab_index,
        selector=normalized_selector,
    )
    resolved_headers = _build_tab_resolution_headers(
        tab_info,
        route_domain=route_key,
        selector=("tab_index" if tab_index is not None else normalized_selector),
    )
    resolved_tab_index = int(tab_info.get("persistent_index") or 0)
    if resolved_tab_index < 1:
        raise HTTPException(status_code=500, detail="resolved_tab_index_invalid")

    ctx = request_manager.create_request()
    with logger.context(ctx.request_id):
        logger.info(
            f"开始 (域名路由 {route_key} -> 标签页 #{resolved_tab_index}, "
            f"selector={'tab_index' if tab_index is not None else normalized_selector}, "
            f"preset={resolved_preset_name or '<follow-tab/default>'})"
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
    selector: str = Query(default="first_idle"),
    authenticated: bool = Depends(verify_auth)
):
    """使用域名+预设路径风格进行聊天。路径中的预设优先级最高。"""
    forced_preset_name = str(preset_name or "").strip() or None
    if forced_preset_name != body.preset_name:
        body = body.model_copy(update={"preset_name": forced_preset_name})

    return await chat_with_route_domain(
        route_domain=route_domain,
        request=request,
        body=body,
        tab_index=tab_index,
        selector=selector,
        preset_name=forced_preset_name,
        authenticated=authenticated,
    )


async def _stream_with_tab_index(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    tab_index: int
):
    """使用指定标签页的流式响应"""
    disconnect_task = None
    worker_thread = None
    chunk_queue = None

    try:
        disconnect_task = asyncio.create_task(
            watch_client_disconnect(request, ctx, check_interval=0.3)
        )

        browser = get_browser(auto_connect=False)

        request_manager.start_request(ctx)

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
                )

                for chunk in gen:
                    if ctx.should_stop():
                        cancel_reason = str(ctx.cancel_reason or "unknown")
                        if cancel_reason in {"cleanup", "client_disconnected", "coroutine_cancelled"}:
                            logger.debug(f"工作线程检测到停止: {cancel_reason}")
                        else:
                            logger.info(f"工作线程检测到取消: {cancel_reason}")
                        break
                    chunk_queue.put(chunk)

            except Exception as e:
                logger.error(f"工作线程异常: {e}")
                chunk_queue.put(("ERROR", str(e)))
            finally:
                if gen is not None:
                    try:
                        gen.close()
                    except Exception as e:
                        logger.debug(f"关闭工作流生成器失败（忽略）: {e}")
                chunk_queue.put(None)

        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()

        last_sse_emit_at = time.monotonic()

        while True:
            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                break

            try:
                chunk = await asyncio.to_thread(
                    chunk_queue.get,
                    timeout=STREAM_QUEUE_POLL_TIMEOUT,
                )
            except queue.Empty:
                if time.monotonic() - last_sse_emit_at >= SSE_HEARTBEAT_INTERVAL:
                    yield SSEFormatter.pack_comment("keepalive")
                    last_sse_emit_at = time.monotonic()
                continue

            if chunk is None:
                break

            if isinstance(chunk, tuple) and chunk[0] == "ERROR":
                ctx.mark_failed(chunk[1])
                yield _pack_error(f"执行错误: {chunk[1]}", "internal_error")
                break

            yield chunk
            last_sse_emit_at = time.monotonic()
            error_message = _extract_stream_error_message(chunk)
            if error_message:
                logger.warning(f"流式响应返回错误事件(tab={tab_index}): {error_message}")
                ctx.mark_failed(error_message)
                break
            await asyncio.sleep(0)

        if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
            ctx.mark_completed()

    except asyncio.CancelledError:
        ctx.request_cancel("coroutine_cancelled")
        raise

    except Exception as e:
        logger.error(f"异常: {e}")
        ctx.mark_failed(str(e))
        yield _pack_error(f"执行错误: {str(e)}", "internal_error")

    finally:
        if worker_thread and worker_thread.is_alive():
            ctx.request_cancel("cleanup")
            worker_thread.join(timeout=2.0)

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

        request_manager.finish_request(ctx, success=(ctx.status == RequestStatus.COMPLETED))


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

    async for chunk in _stream_with_tab_index(request, body, ctx, tab_index):
        if isinstance(chunk, str):
            if chunk.startswith("data: [DONE]"):
                continue

            if chunk.startswith("data: "):
                try:
                    data_str = chunk[6:].strip()
                    if not data_str:
                        continue
                    data = json.loads(data_str)

                    if "error" in data:
                        error_data = data
                        break

                    collected_media.extend(_extract_chunk_media_items(data))

                    if "choices" in data and data["choices"]:
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            collected_content.append(content)

                except json.JSONDecodeError:
                    continue

    if error_data:
        return JSONResponse(content=error_data, status_code=500)

    full_content = _cleanup_non_stream_content("".join(collected_content))
    response = SSEFormatter.pack_non_stream(
        full_content,
        model=body.model,
        media=_dedupe_media_items(collected_media),
    )

    return JSONResponse(content=response)


async def _stream_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    route_domain: str
):
    """使用指定域名路由的流式响应"""
    disconnect_task = None
    worker_thread = None
    chunk_queue = None

    try:
        disconnect_task = asyncio.create_task(
            watch_client_disconnect(request, ctx, check_interval=0.3)
        )

        browser = get_browser(auto_connect=False)

        request_manager.start_request(ctx)

        chunk_queue = queue.Queue(maxsize=100)

        def worker():
            gen = None
            try:
                gen = browser.execute_workflow_for_route_domain(
                    route_domain,
                    body.messages,
                    stream=True,
                    task_id=ctx.request_id,
                    preset_name=body.preset_name,
                    stop_checker=ctx.should_stop,
                )

                for chunk in gen:
                    if ctx.should_stop():
                        cancel_reason = str(ctx.cancel_reason or "unknown")
                        if cancel_reason in {"cleanup", "client_disconnected", "coroutine_cancelled"}:
                            logger.debug(f"工作线程检测到停止: {cancel_reason}")
                        else:
                            logger.info(f"工作线程检测到取消: {cancel_reason}")
                        break
                    chunk_queue.put(chunk)

            except Exception as e:
                logger.error(f"工作线程异常: {e}")
                chunk_queue.put(("ERROR", str(e)))
            finally:
                if gen is not None:
                    try:
                        gen.close()
                    except Exception as e:
                        logger.debug(f"关闭工作流生成器失败（忽略）: {e}")
                chunk_queue.put(None)

        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()

        last_sse_emit_at = time.monotonic()

        while True:
            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                break

            try:
                chunk = await asyncio.to_thread(
                    chunk_queue.get,
                    timeout=STREAM_QUEUE_POLL_TIMEOUT,
                )
            except queue.Empty:
                if time.monotonic() - last_sse_emit_at >= SSE_HEARTBEAT_INTERVAL:
                    yield SSEFormatter.pack_comment("keepalive")
                    last_sse_emit_at = time.monotonic()
                continue

            if chunk is None:
                break

            if isinstance(chunk, tuple) and chunk[0] == "ERROR":
                ctx.mark_failed(chunk[1])
                yield _pack_error(f"执行错误: {chunk[1]}", "internal_error")
                break

            yield chunk
            last_sse_emit_at = time.monotonic()
            error_message = _extract_stream_error_message(chunk)
            if error_message:
                logger.warning(f"流式响应返回错误事件(route_domain={route_domain}): {error_message}")
                ctx.mark_failed(error_message)
                break
            await asyncio.sleep(0)

        if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
            ctx.mark_completed()

    except asyncio.CancelledError:
        ctx.request_cancel("coroutine_cancelled")
        raise

    except Exception as e:
        logger.error(f"异常: {e}")
        ctx.mark_failed(str(e))
        yield _pack_error(f"执行错误: {str(e)}", "internal_error")

    finally:
        if worker_thread and worker_thread.is_alive():
            ctx.request_cancel("cleanup")
            worker_thread.join(timeout=2.0)

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

        request_manager.finish_request(ctx, success=(ctx.status == RequestStatus.COMPLETED))


async def _non_stream_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    route_domain: str
) -> JSONResponse:
    """使用指定域名路由的非流式响应"""
    collected_content = []
    collected_media = []
    error_data = None

    async for chunk in _stream_with_route_domain(request, body, ctx, route_domain):
        if isinstance(chunk, str):
            if chunk.startswith("data: [DONE]"):
                continue

            if chunk.startswith("data: "):
                try:
                    data_str = chunk[6:].strip()
                    if not data_str:
                        continue
                    data = json.loads(data_str)

                    if "error" in data:
                        error_data = data
                        break

                    collected_media.extend(_extract_chunk_media_items(data))

                    if "choices" in data and data["choices"]:
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            collected_content.append(content)

                except json.JSONDecodeError:
                    continue

    if error_data:
        return JSONResponse(content=error_data, status_code=500)

    full_content = _cleanup_non_stream_content("".join(collected_content))
    response = SSEFormatter.pack_non_stream(
        full_content,
        model=body.model,
        media=_dedupe_media_items(collected_media),
    )

    return JSONResponse(content=response)


def _execute_browser_non_stream_for_tab(
    browser,
    tab_index: int,
    messages: List[Dict[str, Any]],
    request_id: str,
    preset_name: Optional[str] = None,
    stop_checker=None,
) -> Dict[str, Any]:
    payload = None
    for chunk in browser.execute_workflow_for_tab_index(
        tab_index,
        messages,
        stream=False,
        task_id=request_id,
        preset_name=preset_name,
        stop_checker=stop_checker,
    ):
        payload = chunk

    if not payload:
        raise RuntimeError("empty_browser_response")

    data = json.loads(payload)
    if "error" in data:
        error = data.get("error") or {}
        raise RuntimeError(str(error.get("message") or "browser_execution_failed"))
    return data


def _execute_browser_non_stream_for_route_domain(
    browser,
    route_domain: str,
    messages: List[Dict[str, Any]],
    request_id: str,
    preset_name: Optional[str] = None,
    stop_checker=None,
) -> Dict[str, Any]:
    payload = None
    for chunk in browser.execute_workflow_for_route_domain(
        route_domain,
        messages,
        stream=False,
        task_id=request_id,
        preset_name=preset_name,
        stop_checker=stop_checker,
    ):
        payload = chunk

    if not payload:
        raise RuntimeError("empty_browser_response")

    data = json.loads(payload)
    if "error" in data:
        error = data.get("error") or {}
        raise RuntimeError(str(error.get("message") or "browser_execution_failed"))
    return data


def _extract_assistant_content(response: Dict[str, Any]) -> str:
    try:
        return str(
            response.get("choices", [])[0]
            .get("message", {})
            .get("content", "")
            or ""
        )
    except Exception:
        return ""


def _run_tool_calling_sync_for_tab(
    browser,
    tab_index: int,
    body: ChatRequest,
    request_id: str,
    stop_checker=None,
) -> Dict[str, Any]:
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

    parsed = complete_tool_calling_roundtrip(
        messages=body.messages,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=body.parallel_tool_calls,
        round_executor=lambda browser_messages: _extract_assistant_content(
            _execute_browser_non_stream_for_tab(
                browser=browser,
                tab_index=tab_index,
                messages=browser_messages,
                request_id=request_id,
                preset_name=body.preset_name,
                stop_checker=stop_checker,
            )
        ),
        stop_checker=stop_checker,
    )
    return build_tool_completion_response(body.model, parsed)


def _run_tool_calling_sync_for_route_domain(
    browser,
    route_domain: str,
    body: ChatRequest,
    request_id: str,
    stop_checker=None,
) -> Dict[str, Any]:
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

    parsed = complete_tool_calling_roundtrip(
        messages=body.messages,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=body.parallel_tool_calls,
        round_executor=lambda browser_messages: _extract_assistant_content(
            _execute_browser_non_stream_for_route_domain(
                browser=browser,
                route_domain=route_domain,
                messages=browser_messages,
                request_id=request_id,
                preset_name=body.preset_name,
                stop_checker=stop_checker,
            )
        ),
        stop_checker=stop_checker,
    )
    return build_tool_completion_response(body.model, parsed)


async def _complete_tool_calling_with_tab_index(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    tab_index: int,
) -> Dict[str, Any]:
    disconnect_task = None
    try:
        disconnect_task = asyncio.create_task(
            watch_client_disconnect(request, ctx, check_interval=0.3)
        )

        browser = get_browser(auto_connect=False)
        request_manager.start_request(ctx)

        response = await asyncio.to_thread(
            _run_tool_calling_sync_for_tab,
            browser,
            tab_index,
            body,
            ctx.request_id,
            ctx.should_stop,
        )

        if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
            ctx.mark_completed()

        return response

    except asyncio.CancelledError:
        ctx.request_cancel("coroutine_cancelled")
        raise
    except Exception as e:
        logger.error(f"tool_calling_failed(tab={tab_index}): {e}")
        ctx.mark_failed(str(e))
        raise
    finally:
        if disconnect_task:
            disconnect_task.cancel()
            try:
                await disconnect_task
            except asyncio.CancelledError:
                pass
        request_manager.finish_request(ctx, success=(ctx.status == RequestStatus.COMPLETED))


async def _complete_tool_calling_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    route_domain: str,
) -> Dict[str, Any]:
    disconnect_task = None
    try:
        disconnect_task = asyncio.create_task(
            watch_client_disconnect(request, ctx, check_interval=0.3)
        )

        browser = get_browser(auto_connect=False)
        request_manager.start_request(ctx)

        response = await asyncio.to_thread(
            _run_tool_calling_sync_for_route_domain,
            browser,
            route_domain,
            body,
            ctx.request_id,
            ctx.should_stop,
        )

        if not ctx.should_stop() and ctx.status == RequestStatus.RUNNING:
            ctx.mark_completed()

        return response

    except asyncio.CancelledError:
        ctx.request_cancel("coroutine_cancelled")
        raise
    except Exception as e:
        logger.error(f"tool_calling_failed(route_domain={route_domain}): {e}")
        ctx.mark_failed(str(e))
        raise
    finally:
        if disconnect_task:
            disconnect_task.cancel()
            try:
                await disconnect_task
            except asyncio.CancelledError:
                pass
        request_manager.finish_request(ctx, success=(ctx.status == RequestStatus.COMPLETED))


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
        return JSONResponse(
            content={
                "error": {
                    "message": f"执行错误: {e}",
                    "type": "execution_error",
                    "code": "tool_calling_failed",
                }
            },
            status_code=500,
        )


async def _non_stream_tool_calling_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    route_domain: str,
) -> JSONResponse:
    try:
        response = await _complete_tool_calling_with_route_domain(request, body, ctx, route_domain)
        return JSONResponse(content=response)
    except Exception as e:
        return JSONResponse(
            content={
                "error": {
                    "message": f"执行错误: {e}",
                    "type": "execution_error",
                    "code": "tool_calling_failed",
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
    try:
        response = await _complete_tool_calling_with_tab_index(request, body, ctx, tab_index)
        message = response.get("choices", [{}])[0].get("message", {}) or {}
        parsed = {
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls") or [],
        }
        for chunk in iter_tool_stream_chunks(body.model, parsed):
            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                break
            yield chunk
            await asyncio.sleep(0)
    except Exception as e:
        yield _pack_error(f"执行错误: {str(e)}", "tool_calling_failed")


async def _stream_tool_calling_with_route_domain(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext,
    route_domain: str,
):
    try:
        response = await _complete_tool_calling_with_route_domain(request, body, ctx, route_domain)
        message = response.get("choices", [{}])[0].get("message", {}) or {}
        parsed = {
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls") or [],
        }
        for chunk in iter_tool_stream_chunks(body.model, parsed):
            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                break
            yield chunk
            await asyncio.sleep(0)
    except Exception as e:
        yield _pack_error(f"执行错误: {str(e)}", "tool_calling_failed")


# ================= 预设管理 API =================

class PresetRequest(BaseModel):
    """预设操作请求"""
    preset_name: str = Field(..., min_length=1, max_length=50)


class CreatePresetRequest(BaseModel):
    """创建预设请求"""
    new_name: str = Field(..., min_length=1, max_length=50)
    source_name: Optional[str] = Field(default=None)


class RenamePresetRequest(BaseModel):
    """重命名预设请求"""
    old_name: str = Field(..., min_length=1, max_length=50)
    new_name: str = Field(..., min_length=1, max_length=50)

class SetDefaultPresetRequest(BaseModel):
    """设置默认预设请求"""
    preset_name: str = Field(..., min_length=1, max_length=50)


class TerminateTabRequest(BaseModel):
    """终止标签页当前任务请求"""
    reason: str = Field(default="manual_terminate_from_tab_pool", max_length=120)
    clear_page: bool = Field(default=True)


@router.put("/api/tab-pool/tabs/{tab_index}/preset")
async def set_tab_preset(
    tab_index: int,
    body: PresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """为指定标签页设置预设"""
    try:
        browser = get_browser(auto_connect=False)
        
        preset_value = None if body.preset_name == FOLLOW_DEFAULT_PRESET else body.preset_name
        
        success = browser.tab_pool.set_tab_preset(tab_index, preset_value)
        
        if success:
            preset_label = "跟随站点默认预设" if preset_value is None else body.preset_name
            return {"success": True, "message": f"标签页 #{tab_index} 已切换到预设: {preset_label}"}
        else:
            raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置标签页预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tab-pool/tabs/{tab_index}/terminate")
async def terminate_tab_task(
    tab_index: int,
    body: TerminateTabRequest,
    authenticated: bool = Depends(verify_auth)
):
    """按标签页编号终止当前任务并释放占用。"""
    if tab_index < 1:
        raise HTTPException(status_code=400, detail="标签页编号必须大于 0")

    try:
        browser = get_browser(auto_connect=False)
        result = browser.tab_pool.terminate_by_index(
            tab_index,
            reason=(body.reason or "manual_terminate_from_tab_pool"),
            clear_page=bool(body.clear_page),
        )
        if not result.get("ok"):
            if result.get("error") == "tab_not_found":
                raise HTTPException(status_code=404, detail=f"标签页 #{tab_index} 不存在")
            raise HTTPException(status_code=400, detail=result.get("error", "terminate_failed"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"终止标签页任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/presets/{domain}")
async def get_site_presets(
    domain: str,
    authenticated: bool = Depends(verify_auth)
):
    """获取指定站点的所有预设"""
    try:
        from app.services.config_engine import config_engine
        presets = config_engine.list_presets(domain)
        default_preset = config_engine.get_default_preset(domain)
        return {"domain": domain, "presets": presets, "default_preset": default_preset}
    except Exception as e:
        logger.error(f"获取预设列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/presets/{domain}")
async def create_site_preset(
    domain: str,
    body: CreatePresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """为站点创建新预设（克隆自现有预设）"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.create_preset(domain, body.new_name, body.source_name)
        
        if success:
            return {"success": True, "message": f"预设 '{body.new_name}' 已创建"}
        else:
            raise HTTPException(status_code=400, detail="创建失败（预设已存在或站点不存在）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/presets/{domain}/rename")
async def rename_site_preset(
    domain: str,
    body: RenamePresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """重命名指定预设"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.rename_preset(domain, body.old_name, body.new_name)

        if success:
            return {
                "success": True,
                "message": f"预设 '{body.old_name}' 已重命名为 '{body.new_name}'",
            }
        else:
            raise HTTPException(status_code=400, detail="重命名失败（预设不存在或新名称已存在）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重命名预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/api/presets/{domain}/default")
async def set_site_default_preset(
    domain: str,
    body: SetDefaultPresetRequest,
    authenticated: bool = Depends(verify_auth)
):
    """设置站点默认预设（本地覆盖）"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.set_default_preset(domain, body.preset_name)

        if success:
            return {
                "success": True,
                "message": f"默认预设已设置为 '{body.preset_name}'（本地覆盖）",
                "domain": domain,
                "default_preset": body.preset_name
            }
        else:
            raise HTTPException(status_code=400, detail="设置失败（站点或预设不存在）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置默认预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/presets/{domain}/{preset_name}")
async def delete_site_preset(
    domain: str,
    preset_name: str,
    authenticated: bool = Depends(verify_auth)
):
    """删除指定预设（不能删除最后一个）"""
    try:
        from app.services.config_engine import config_engine
        success = config_engine.delete_preset(domain, preset_name)
        
        if success:
            return {"success": True, "message": f"预设 '{preset_name}' 已删除"}
        else:
            raise HTTPException(status_code=400, detail="删除失败（预设不存在或是最后一个）")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除预设失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _pack_error(message: str, code: str = "error") -> str:
    """打包 SSE 错误"""
    data = {
        "id": f"chatcmpl-error-{int(time.time() * 1000)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "web-browser",
        "choices": [{
            "index": 0,
            "delta": {"content": f"[错误] {message}"},
            "finish_reason": None
        }],
        "error": {
            "message": message,
            "type": "execution_error",
            "code": code
        }
    }
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
