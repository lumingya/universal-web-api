"""标签页 OpenAI 兼容路由的公共实现（原 app/api/tab_routes.py 的主体）。

保存四组路由共用的解析/配置/流式/工具调用辅助，以及共享的 ``router`` 实例。
"""

import json
import queue
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.params import Param
from pydantic import BaseModel, Field, field_validator

from app.api.openai import runtime
from app.api.openai_stop import iter_openai_sse_payloads
from app.api.stream_helpers import (
    _extract_chunk_media_items,
    _extract_delta_content_text,
    _is_absolute_request_timeout_error,
    _pack_done,
)
from app.core.config import SSEFormatter, _request_context, atomic_write_json, get_logger
from app.services.request_lifecycle import (
    TrackedWorkerExecutionCancelled,
    cleanup_worker_thread_after_request,
    put_worker_queue_item,
    run_tracked_blocking_call,
)
from app.services.request_manager import RequestContext, RequestStatus
from app.utils.site_url import (
    encode_tab_url_route_token,
    extract_remote_site_domain,
    get_canonical_route_domain,
    normalize_exact_tab_url,
    normalize_route_domain,
    route_domain_matches,
    tab_url_matches,
)
from app.utils.tab_route_groups import normalize_route_group_id

router = APIRouter()
logger = get_logger("API.TAB")
MODEL_LIST_CREATED = int(time.time())

HEADER_VALUE_QUOTE_SAFE = ":/?#[]@!$&'()*+,;=%-._~"

FOLLOW_DEFAULT_PRESET = "__DEFAULT__"

STREAM_QUEUE_POLL_TIMEOUT = 0.5

SSE_HEARTBEAT_INTERVAL = 5.0

TAB_POOL_ALLOCATION_OPTIONS = [
    {"value": "first_idle", "label": "优先空闲"},
    {"value": "round_robin", "label": "轮询"},
    {"value": "random", "label": "随机"},
]

TAB_ROUTE_METHOD_OPTIONS = [
    {"value": "domain", "label": "站点域名路由"},
    {"value": "route_group", "label": "标签页路由组"},
    {"value": "fixed_tab", "label": "固定标签页路由"},
    {"value": "exact_url", "label": "标签页 URL 路由"},
    {"value": "exact_url_preset", "label": "URL 绑定预设路由"},
]

DEFAULT_TAB_ROUTE_METHODS = {"domain", "route_group", "fixed_tab", "exact_url", "exact_url_preset"}

TAB_SELECTOR_OPTIONS = {"first_idle", "round_robin", "random"}

_route_round_robin_cursor: Dict[str, int] = {}

_route_round_robin_lock = threading.Lock()

_browser_config_lock = threading.RLock()

_model_name_overrides_lock = threading.RLock()

_preset_overrides_lock = threading.RLock()

MODEL_NAME_OVERRIDES_PATH = Path("config/model_name_overrides.local.json")

PRESET_OVERRIDES_PATH = Path("config/preset_overrides.local.json")


def _reset_stream_request_context(request_context_token: Any) -> None:
    """Reset request logging context when the streaming generator owns its Context.

    Starlette can finalize a disconnected streaming response from a separate
    Context. ContextVar tokens cannot be reset there, but that Context has no
    request value to restore.
    """
    try:
        _request_context.reset(request_context_token)
    except ValueError as exc:
        if "different Context" not in str(exc):
            raise
        logger.debug("流式请求上下文已在其他 Context 中结束，跳过重置")


def _format_rfc3339_timestamp(value: Any) -> str:
    try:
        timestamp = int(value or MODEL_LIST_CREATED)
    except Exception:
        timestamp = MODEL_LIST_CREATED
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


def _build_claude_route_model_id(_value: Any) -> str:
    # Claude filters gateway model IDs that contain provider names like "glm".
    return "claude-sonnet-4-5"


def _build_route_models_payload(
    *,
    model_id: str,
    display_name: str,
    anthropic_version: Optional[str] = None,
) -> Dict[str, Any]:
    entry = {
        "id": model_id,
        "object": "model",
        "type": "model",
        "created": MODEL_LIST_CREATED,
        "owned_by": "universal-web-api",
        "display_name": display_name or model_id,
    }
    if anthropic_version:
        item = {
            "type": "model",
            "id": entry["id"],
            "display_name": entry["display_name"],
            "created_at": _format_rfc3339_timestamp(entry["created"]),
        }
        return {
            "data": [item],
            "has_more": False,
            "first_id": item["id"],
            "last_id": item["id"],
        }
    return {
        "object": "list",
        "data": [entry],
    }


def _build_route_model_entries_payload(
    entries: List[Dict[str, Any]],
    *,
    anthropic_version: Optional[str] = None,
) -> Dict[str, Any]:
    if anthropic_version:
        data = [
            {
                "type": "model",
                "id": str(item.get("id") or ""),
                "display_name": str(item.get("display_name") or item.get("id") or ""),
                "created_at": _format_rfc3339_timestamp(item.get("created")),
            }
            for item in entries
            if str(item.get("id") or "").strip()
        ]
        return {
            "data": data,
            "has_more": False,
            "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None,
        }
    return {"object": "list", "data": entries}


def _unwrap_fastapi_param_value(value: Any) -> Any:
    if isinstance(value, Param):
        return value.default
    return value


def _encode_response_header_value(value: Any) -> str:
    text = "" if value is None else str(value)
    # 判据用 ASCII 而不是 latin-1：latin-1 可编码 U+0080–U+00FF（如预设名 "Café"），
    # 于是这些字符会以裸字节进响应头，按 UTF-8 解析响应头的客户端
    # （Node/undici、Go 等）会拿到乱码甚至解码失败。非 ASCII 一律百分号编码。
    needs_quote = (not text.isascii()) or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)
    if not needs_quote:
        return text
    return quote(text, safe=HEADER_VALUE_QUOTE_SAFE)


def _encode_response_headers(headers: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    if not headers:
        return {}
    return {str(key): _encode_response_header_value(value) for key, value in headers.items()}


def _get_tab_pool_allocation_mode(tab_pool: Any) -> str:
    mode = str(getattr(tab_pool, "allocation_mode", "") or "").strip() or "first_idle"
    valid_modes = {"first_idle", "round_robin", "random"}
    return mode if mode in valid_modes else "first_idle"


def _get_config_allocation_mode(tab_pool_config: Any) -> str:
    source = tab_pool_config if isinstance(tab_pool_config, dict) else {}
    mode = str(source.get("allocation_mode") or "").strip().lower() or "first_idle"
    valid_modes = {"first_idle", "round_robin", "random"}
    return mode if mode in valid_modes else "first_idle"


def _attach_preset_info_to_tabs(
    tabs: List[Dict[str, Any]],
    config_engine: Any,
    auto_remember_url_presets: bool = True
) -> None:
    preset_cache: Dict[str, tuple[List[str], Optional[str]]] = {}
    preset_overrides = _read_preset_overrides() if auto_remember_url_presets else {"urls": {}}
    for tab_info in tabs:
        domain = str(tab_info.get("current_domain") or tab_info.get("route_domain") or "").strip()
        normalized_domain = normalize_route_domain(domain) or domain
        canonical_domain = get_canonical_route_domain(normalized_domain) or ""
        candidate_domains = [
            item for item in (normalized_domain, canonical_domain)
            if item and item not in {""}
        ]
        candidate_domains = list(dict.fromkeys(candidate_domains))
        preset_name = tab_info.get("preset_name")
        override_source = str(tab_info.get("preset_override_source") or "").strip()
        current_url = str(tab_info.get("url") or "").strip()
        url_key = normalize_exact_tab_url(current_url)
        if auto_remember_url_presets and not override_source and url_key and url_key in preset_overrides.get("urls", {}):
            override_source = "url"

        if not candidate_domains:
            tab_info["preset_route_domain"] = ""
            tab_info["preset_domain_route_prefix"] = ""
            tab_info["available_presets"] = []
            tab_info["default_preset"] = None
            tab_info["effective_preset_name"] = preset_name
            tab_info["preset_override_source"] = override_source
            tab_info["is_using_default_preset"] = not bool(preset_name)
            continue

        resolved_domain = candidate_domains[0]
        available_presets: List[str] = []
        default_preset: Optional[str] = None
        for candidate_domain in candidate_domains:
            if candidate_domain not in preset_cache:
                try:
                    preset_cache[candidate_domain] = (
                        config_engine.list_presets(candidate_domain),
                        config_engine.get_default_preset(candidate_domain),
                    )
                except Exception as e:
                    logger.debug(f"读取标签页预设失败: {candidate_domain}: {e}")
                    preset_cache[candidate_domain] = ([], None)
            candidate_presets, candidate_default = preset_cache[candidate_domain]
            if candidate_presets or candidate_default:
                resolved_domain = candidate_domain
                available_presets = candidate_presets
                default_preset = candidate_default
                break
            if candidate_domain == candidate_domains[0]:
                available_presets = candidate_presets
                default_preset = candidate_default

        tab_info["preset_route_domain"] = resolved_domain
        tab_info["preset_domain_route_prefix"] = f"/url/{resolved_domain}" if resolved_domain else ""
        tab_info["available_presets"] = available_presets
        tab_info["default_preset"] = default_preset
        tab_info["effective_preset_name"] = preset_name or default_preset
        tab_info["preset_override_source"] = override_source
        tab_info["is_using_default_preset"] = not bool(preset_name)


async def _cleanup_route_worker_thread(
    worker_thread: Optional[threading.Thread],
    ctx: RequestContext,
    *,
    fast_returned_on_audio: bool = False,
    done_emitted: bool = False,
) -> bool:
    if not isinstance(worker_thread, threading.Thread) or not worker_thread.is_alive():
        return False

    if fast_returned_on_audio:
        ctx.mark_worker_stop_requested("audio_media_fast_return")
        ctx.mark_completed()
        return await cleanup_worker_thread_after_request(
            worker_thread,
            ctx,
            completed=True,
            retire_reason="worker_audio_fast_return_timeout",
            completed_join_timeout=0.2,
        )
    elif ctx.status == RequestStatus.COMPLETED:
        return await cleanup_worker_thread_after_request(
            worker_thread,
            ctx,
            completed=True,
            retire_reason="worker_cleanup_timeout",
            completed_join_timeout=0.2,
        )
    else:
        return await cleanup_worker_thread_after_request(
            worker_thread,
            ctx,
            completed=False,
            cancel_reason="cleanup",
            join_timeout=5.0,
            retire_reason="worker_cleanup_timeout",
        )


def _put_route_worker_queue_item(
    chunk_queue: queue.Queue,
    ctx: RequestContext,
    item: Any,
    *,
    final: bool = False,
) -> bool:
    return put_worker_queue_item(
        chunk_queue,
        ctx,
        item,
        final=final,
        poll_timeout=0.01 if ctx.should_stop() else STREAM_QUEUE_POLL_TIMEOUT,
    )


def _read_browser_config() -> Dict[str, Any]:
    config_path = Path("config/browser_config.json")
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8-sig") as f:
        return json.load(f)


def _write_browser_config_unlocked(payload: Dict[str, Any]) -> None:
    atomic_write_json(Path("config/browser_config.json"), payload)


def _write_browser_config(payload: Dict[str, Any]) -> None:
    with _browser_config_lock:
        _write_browser_config_unlocked(payload)


def _iter_sse_payloads(chunk: Any) -> List[Dict[str, Any]]:
    return iter_openai_sse_payloads(chunk)


def _make_buffered_sse_payload_parser():
    buffer = ""

    def parse(chunk: Any) -> List[Dict[str, Any]]:
        nonlocal buffer
        if not isinstance(chunk, str) or not chunk:
            return []

        buffer += chunk.replace("\r\n", "\n").replace("\r", "\n")
        payloads: List[Dict[str, Any]] = []
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            if not frame.strip():
                continue
            payloads.extend(_iter_sse_payloads(frame + "\n\n"))
        return payloads

    def flush() -> List[Dict[str, Any]]:
        nonlocal buffer
        tail = buffer
        buffer = ""
        if not tail.strip():
            return []
        return _iter_sse_payloads(tail + "\n\n")

    parse.flush = flush  # type: ignore[attr-defined]
    return parse


def _consume_non_stream_sse_payload(
    data: Dict[str, Any],
    *,
    collected_content: List[str],
    collected_media: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if "error" in data:
        return data

    media_items = _extract_chunk_media_items(data)
    collected_media.extend(media_items)

    if "choices" in data and data["choices"]:
        delta = data["choices"][0].get("delta", {})
        content = delta.get("content", "")
        content_text = _extract_delta_content_text(content)
        if content_text:
            collected_content.append(content_text)
    return None


def _extract_sse_chunk_media_items(chunk: Any) -> List[Dict[str, Any]]:
    media_items: List[Dict[str, Any]] = []
    for payload in _iter_sse_payloads(chunk):
        media_items.extend(_extract_chunk_media_items(payload))
    return media_items


def _has_audio_media(media_items: List[Dict[str, Any]]) -> bool:
    return any(
        str(item.get("media_type") or "").strip().lower() == "audio"
        for item in media_items or []
        if isinstance(item, dict)
    )


def _should_fast_return_on_audio_media(body: "ChatRequest") -> bool:
    text = " ".join(
        str(value or "").strip().lower()
        for value in (
            getattr(body, "preset_name", None),
            getattr(body, "model", None),
        )
        if value
    )
    if not text:
        return False
    markers = ("朗读", "语音朗读", "read aloud", "text-to-speech", "tts", "voice")
    return any(marker in text for marker in markers)


def _pack_audio_fast_return_chunks(body: "ChatRequest") -> List[str]:
    chunks: List[str] = []
    finish_chunk = SSEFormatter.pack_finish(model=body.model)
    emit_finish, _finish_had_done = _split_sse_done_frame(finish_chunk)
    if emit_finish:
        chunks.append(emit_finish)
    usage_chunk = _maybe_pack_stream_usage_chunk(body)
    if usage_chunk:
        chunks.append(usage_chunk)
    chunks.append(_pack_done())
    return chunks


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


def _normalize_enabled_route_methods(value: Any) -> List[str]:
    if not isinstance(value, list):
        return [item["value"] for item in TAB_ROUTE_METHOD_OPTIONS]

    normalized: List[str] = []
    seen = set()
    for item in value:
        method = str(item or "").strip().lower()
        if method in DEFAULT_TAB_ROUTE_METHODS and method not in seen:
            seen.add(method)
            normalized.append(method)

    if not normalized:
        return [item["value"] for item in TAB_ROUTE_METHOD_OPTIONS]
    return normalized


def _normalize_excluded_urls(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []

    normalized: List[str] = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        normalized_text = normalize_exact_tab_url(text) or text
        if not normalized_text or normalized_text in seen:
            continue
        seen.add(normalized_text)
        normalized.append(normalized_text)
    return normalized


def _normalize_model_name(value: Any) -> str:
    return str(value or "").strip()


def _normalize_model_name_key(value: Any) -> str:
    return _normalize_model_name(value).lower()


def _normalize_model_name_overrides(value: Any) -> Dict[str, Dict[str, str]]:
    payload = value if isinstance(value, dict) else {}
    normalized: Dict[str, Dict[str, str]] = {"sites": {}, "urls": {}}

    sites = payload.get("sites") if isinstance(payload, dict) else {}
    if isinstance(sites, dict):
        for key, model_name in sites.items():
            route_key = normalize_route_domain(key)
            display_name = _normalize_model_name(model_name)
            if route_key and display_name:
                normalized["sites"][route_key] = display_name

    urls = payload.get("urls") if isinstance(payload, dict) else {}
    if isinstance(urls, dict):
        for key, model_name in urls.items():
            url_key = normalize_exact_tab_url(str(key or "").strip())
            display_name = _normalize_model_name(model_name)
            if url_key and display_name:
                normalized["urls"][url_key] = display_name

    return normalized


def _get_legacy_model_name_overrides_from_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, str]]:
    payload = config if isinstance(config, dict) else _read_browser_config()
    tab_pool_config = payload.get("tab_pool") if isinstance(payload, dict) else {}
    if not isinstance(tab_pool_config, dict):
        tab_pool_config = {}
    return _normalize_model_name_overrides(tab_pool_config.get("model_name_overrides"))


def _read_model_name_overrides_unlocked() -> Dict[str, Dict[str, str]]:
    if MODEL_NAME_OVERRIDES_PATH.exists():
        try:
            with open(MODEL_NAME_OVERRIDES_PATH, encoding="utf-8-sig") as f:
                return _normalize_model_name_overrides(json.load(f))
        except Exception as e:
            logger.warning(f"读取模型显示名称本地配置失败: {e}")
            return {"sites": {}, "urls": {}}

    return _get_legacy_model_name_overrides_from_config()


def _read_model_name_overrides() -> Dict[str, Dict[str, str]]:
    with _model_name_overrides_lock:
        return _read_model_name_overrides_unlocked()


def _write_model_name_overrides_unlocked(overrides: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    normalized = _normalize_model_name_overrides(overrides)
    atomic_write_json(MODEL_NAME_OVERRIDES_PATH, normalized)
    return normalized


def _get_tab_pool_model_name_overrides(tab_pool: Any, config: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, str]]:
    try:
        overrides = getattr(tab_pool, "model_name_overrides", None)
        if overrides is not None:
            return _normalize_model_name_overrides(overrides)
    except Exception:
        pass
    return _read_model_name_overrides()


def _sync_tab_pool_model_name_overrides(overrides: Dict[str, Dict[str, str]]) -> bool:
    try:
        from app.core.config import BrowserConstants
        if hasattr(BrowserConstants, "reload"):
            BrowserConstants.reload()
    except Exception as reload_error:
        logger.warning(f"热重载浏览器常量失败: {reload_error}")

    try:
        browser = runtime.get_browser(auto_connect=False)
        browser.tab_pool.apply_runtime_config(model_name_overrides=overrides)
        return True
    except Exception as sync_error:
        logger.warning(f"同步模型显示名称配置失败: {sync_error}")
        return False


def _normalize_preset_overrides(value: Any) -> Dict[str, Dict[str, str]]:
    payload = value if isinstance(value, dict) else {}
    normalized = {"urls": {}}
    urls = payload.get("urls") if isinstance(payload, dict) else {}
    if isinstance(urls, dict):
        for key, preset_name in urls.items():
            url_key = normalize_exact_tab_url(str(key or "").strip())
            p_name = str(preset_name or "").strip()
            if url_key and p_name:
                normalized["urls"][url_key] = p_name
    return normalized


def _get_legacy_preset_overrides_from_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, str]]:
    payload = config if isinstance(config, dict) else _read_browser_config()
    tab_pool_config = payload.get("tab_pool") if isinstance(payload, dict) else {}
    if not isinstance(tab_pool_config, dict):
        tab_pool_config = {}
    return _normalize_preset_overrides(tab_pool_config.get("preset_overrides"))


def _read_preset_overrides_unlocked() -> Dict[str, Dict[str, str]]:
    if PRESET_OVERRIDES_PATH.exists():
        try:
            with open(PRESET_OVERRIDES_PATH, encoding="utf-8-sig") as f:
                return _normalize_preset_overrides(json.load(f))
        except Exception as e:
            logger.warning(f"读取预设本地配置失败: {e}")
            return {"urls": {}}

    return _get_legacy_preset_overrides_from_config()


def _read_preset_overrides() -> Dict[str, Dict[str, str]]:
    with _preset_overrides_lock:
        return _read_preset_overrides_unlocked()


def _write_preset_overrides_unlocked(overrides: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    normalized = _normalize_preset_overrides(overrides)
    atomic_write_json(PRESET_OVERRIDES_PATH, normalized)
    return normalized


def _sync_tab_pool_preset_overrides(overrides: Dict[str, Dict[str, str]]) -> bool:
    try:
        browser = runtime.get_browser(auto_connect=False)
        browser.tab_pool.apply_runtime_config(preset_overrides=overrides)
        return True
    except Exception as sync_error:
        logger.warning(f"同步预设记忆配置失败: {sync_error}")
        return False


def _get_auto_remember_url_presets_from_config(config: Optional[Dict[str, Any]] = None) -> bool:
    payload = config if isinstance(config, dict) else _read_browser_config()
    tab_pool_config = payload.get("tab_pool") if isinstance(payload, dict) else {}
    if not isinstance(tab_pool_config, dict):
        tab_pool_config = {}
    return _coerce_bool(tab_pool_config.get("auto_remember_url_presets"), False)


def _get_tab_pool_auto_remember_url_presets(tab_pool: Any, config: Optional[Dict[str, Any]] = None) -> bool:
    try:
        flag = getattr(tab_pool, "auto_remember_url_presets", None)
        if flag is not None:
            return bool(flag)
    except Exception:
        pass
    return _get_auto_remember_url_presets_from_config(config)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_enabled_route_methods_from_config(config: Optional[Dict[str, Any]] = None) -> List[str]:
    payload = config if isinstance(config, dict) else _read_browser_config()
    tab_pool_config = payload.get("tab_pool") if isinstance(payload, dict) else {}
    if not isinstance(tab_pool_config, dict):
        tab_pool_config = {}
    return _normalize_enabled_route_methods(tab_pool_config.get("enabled_route_methods"))


def _get_excluded_urls_from_config(config: Optional[Dict[str, Any]] = None) -> List[str]:
    payload = config if isinstance(config, dict) else _read_browser_config()
    tab_pool_config = payload.get("tab_pool") if isinstance(payload, dict) else {}
    if not isinstance(tab_pool_config, dict):
        tab_pool_config = {}
    return _normalize_excluded_urls(tab_pool_config.get("excluded_urls"))


def _get_enabled_route_methods(config: Optional[Dict[str, Any]] = None) -> List[str]:
    payload = config if isinstance(config, dict) else _read_browser_config()
    tab_pool_config = payload.get("tab_pool") if isinstance(payload, dict) else {}
    if not isinstance(tab_pool_config, dict):
        tab_pool_config = {}
    return _normalize_enabled_route_methods(tab_pool_config.get("enabled_route_methods"))


def _route_method_guard(method: str):
    """把路由方式开关做成 FastAPI 依赖，而不是在处理函数体里内联校验。

    /url/{d}/{preset}/... 会在 Python 层直接调用 chat_with_route_domain，
    app/api/chat.py 的 arena catalog 分支、anthropic_routes 也一样。若把校验写在
    函数体里，这些内部委派会一并被拦下——关掉"站点域名路由"会连普通的
    /v1/chat/completions 都 403。依赖只在 HTTP 进入该条路由时执行，内部调用不触发，
    拦截面正好落在用户在面板上看到的那几条前缀上。
    """
    async def _guard() -> None:
        _ensure_route_method_enabled(method)

    return _guard


def _ensure_route_method_enabled(method: str) -> None:
    """拦截被关闭的寻址方式。

    enabled_route_methods 此前只被读写和回显，没有任何路由分支消费它：
    面板上取消勾选"固定标签页路由"后，/tab/1/v1/chat/completions 照样 200，
    对外暴露服务时会给出"已关掉"的错觉。读配置失败时保持放行（fail-open），
    不因为配置文件问题把正常路由全部打死。
    """
    normalized = str(method or "").strip().lower()
    if not normalized:
        return
    try:
        enabled = _get_enabled_route_methods()
    except Exception as e:
        logger.debug(f"读取已启用路由方式失败（放行）: {e}")
        return
    if normalized not in enabled:
        label = next(
            (item["label"] for item in TAB_ROUTE_METHOD_OPTIONS if item["value"] == normalized),
            normalized,
        )
        raise HTTPException(status_code=403, detail=f"路由方式已在标签页池设置中关闭: {label}")


def _get_tab_pool_excluded_urls(tab_pool: Any, config: Optional[Dict[str, Any]] = None) -> List[str]:
    try:
        excluded_urls = getattr(tab_pool, "excluded_urls", None)
        if excluded_urls is not None:
            return _normalize_excluded_urls(excluded_urls)
    except Exception:
        pass
    return _get_excluded_urls_from_config(config)


def _get_preserve_error_tabs_from_config(config: Optional[Dict[str, Any]] = None) -> bool:
    payload = config if isinstance(config, dict) else _read_browser_config()
    tab_pool_config = payload.get("tab_pool") if isinstance(payload, dict) else {}
    if not isinstance(tab_pool_config, dict):
        tab_pool_config = {}
    return _coerce_bool(tab_pool_config.get("preserve_error_tabs"), False)


def _get_tab_pool_preserve_error_tabs(tab_pool: Any, config: Optional[Dict[str, Any]] = None) -> bool:
    try:
        value = getattr(tab_pool, "preserve_error_tabs", None)
        if value is not None:
            return _coerce_bool(value, False)
    except Exception:
        pass
    return _get_preserve_error_tabs_from_config(config)


def _tab_item_is_excluded(item: Dict[str, Any], excluded_urls: List[str]) -> bool:
    return bool(_get_tab_item_exclusion_url(item, excluded_urls))


def _get_tab_item_exclusion_url(item: Dict[str, Any], excluded_urls: List[str]) -> str:
    if not excluded_urls:
        return ""
    actual_url = str(item.get("url") or "").strip()
    if not actual_url:
        return ""
    for excluded_url in excluded_urls:
        if tab_url_matches(excluded_url, actual_url):
            return excluded_url
    return ""


def _get_pool_default_selector(browser) -> str:
    """当路由接口未显式传 selector 时，跟随标签页池当前分配模式。"""
    try:
        return _get_tab_pool_allocation_mode(browser.tab_pool)
    except Exception as e:
        logger.debug(f"读取标签页池默认分配模式失败，回退 first_idle: {e}")
        return "first_idle"


def _get_tab_info_by_index(browser, tab_index: int) -> Optional[Dict[str, Any]]:
    tabs = browser.tab_pool.get_tabs_with_index()
    for item in tabs:
        if int(item.get("persistent_index") or 0) == int(tab_index):
            return item
    return None


def _get_tabs_by_exact_url(browser, exact_url: str) -> List[Dict[str, Any]]:
    target = normalize_exact_tab_url(exact_url)
    if not target:
        return []

    matches: List[Dict[str, Any]] = []
    for item in browser.tab_pool.get_tabs_with_index():
        actual_url = str(item.get("url") or "").strip()
        if tab_url_matches(target, actual_url):
            matches.append(item)
    return matches


def _get_tabs_by_url_route_token(browser, url_token: str) -> List[Dict[str, Any]]:
    target = str(url_token or "").strip().lower()
    if not target:
        return []

    matches: List[Dict[str, Any]] = []
    for item in browser.tab_pool.get_tabs_with_index():
        item_token = str(item.get("url_route_token") or "").strip().lower()
        if item_token and item_token == target:
            matches.append(item)
            continue
        actual_url = str(item.get("url") or "").strip()
        if encode_tab_url_route_token(actual_url) == target:
            matches.append(item)
    return matches


def _get_tabs_by_exposed_model_name(browser, model_name: str) -> List[Dict[str, Any]]:
    target = _normalize_model_name_key(model_name)
    if not target:
        return []

    # Model-name routing is still dynamic routing.  Keep it consistent with
    # domain routing so tabs explicitly excluded from the pool can never be
    # selected just because they expose the same model name.
    excluded_urls = _get_tab_pool_excluded_urls(browser.tab_pool)
    matches: List[Dict[str, Any]] = []
    for item in browser.tab_pool.get_tabs_with_index():
        exposed_name = _normalize_model_name_key(item.get("exposed_model_name"))
        if (
            exposed_name
            and exposed_name == target
            and not _tab_item_is_excluded(item, excluded_urls)
        ):
            matches.append(item)
    return matches


def _list_candidate_tabs(browser, route_domain: str = "") -> List[Dict[str, Any]]:
    tabs = browser.tab_pool.get_tabs_with_index()
    target = normalize_route_domain(route_domain)
    excluded_urls = _get_tab_pool_excluded_urls(browser.tab_pool)
    if not target:
        # 无域名约束时同样要过滤"已从动态路由排除"的标签页，
        # 否则这条分支会成为绕过 excluded_urls 的后门。
        return [item for item in tabs if not _tab_item_is_excluded(item, excluded_urls)]

    result: List[Dict[str, Any]] = []
    for item in tabs:
        actual_domain = str(item.get("current_domain") or item.get("route_domain") or "").strip()
        if (
            actual_domain
            and route_domain_matches(target, actual_domain)
            and not _tab_item_is_excluded(item, excluded_urls)
        ):
            result.append(item)
    return result


def _select_round_robin_tab(candidates: List[Dict[str, Any]], cursor_key: str) -> Dict[str, Any]:
    if not candidates:
        raise HTTPException(status_code=404, detail="没有可用标签页")

    with _route_round_robin_lock:
        last_index = _route_round_robin_cursor.get(cursor_key, -1)
        chosen: Optional[Dict[str, Any]] = None
        chosen_index: Optional[int] = None
        wrap_chosen: Optional[Dict[str, Any]] = None
        wrap_index: Optional[int] = None

        for item in candidates:
            current_index = _tab_persistent_index(item)
            if wrap_index is None or current_index < wrap_index:
                wrap_chosen = item
                wrap_index = current_index
            if current_index > last_index and (chosen_index is None or current_index < chosen_index):
                chosen = item
                chosen_index = current_index

        if chosen is None:
            chosen = wrap_chosen
            chosen_index = wrap_index
        if chosen is None or chosen_index is None:
            raise HTTPException(status_code=404, detail="没有可用标签页")
        _route_round_robin_cursor[cursor_key] = int(chosen.get("persistent_index") or 0)
        return chosen


def _tab_persistent_index(item: Dict[str, Any]) -> int:
    return int(item.get("persistent_index") or 0)


def _resolve_target_tab(
    browser,
    *,
    route_domain: str = "",
    exact_url: str = "",
    url_token: str = "",
    model_name: str = "",
    tab_index: Optional[int] = None,
    selector: str = "first_idle",
) -> Dict[str, Any]:
    target_route = normalize_route_domain(route_domain)
    target_exact_url = normalize_exact_tab_url(exact_url)
    target_url_token = str(url_token or "").strip().lower()
    target_model_name = _normalize_model_name_key(model_name)

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
        actual_url = str(tab_info.get("url") or "").strip()
        if target_exact_url and not tab_url_matches(target_exact_url, actual_url):
            raise HTTPException(
                status_code=400,
                detail="指定标签页与 URL 路由不匹配",
            )
        actual_url_token = str(tab_info.get("url_route_token") or "").strip().lower()
        if target_url_token and (
            actual_url_token != target_url_token
            and encode_tab_url_route_token(actual_url) != target_url_token
        ):
            raise HTTPException(
                status_code=400,
                detail="指定标签页与 URL 路由不匹配",
            )
        if target_model_name:
            actual_model_name = _normalize_model_name_key(tab_info.get("exposed_model_name"))
            if actual_model_name != target_model_name:
                raise HTTPException(
                    status_code=400,
                    detail="指定标签页与模型显示名称不匹配",
                )
        return tab_info

    # exact_url / url_token 两条分支的解析结果会作为 resolved_tab_index 硬绑定传给
    # execute_workflow_for_exact_url（下游只认这一个编号），所以必须和 model / domain
    # 分支一样优先选空闲标签页；否则同 URL 开了多个标签页时，轮询游标可能挑中正忙的
    # 那个，请求白等 60 秒超时，而旁边空闲的标签页全程没人用。
    if target_exact_url:
        matches = _get_tabs_by_exact_url(browser, target_exact_url)
        if not matches:
            raise HTTPException(status_code=404, detail="URL 路由没有匹配的已打开标签页")
        idle_matches = [
            item for item in matches
            if str(item.get("status") or "").strip().lower() == "idle"
        ]
        return _select_round_robin_tab(idle_matches or matches, f"exact_url::{target_exact_url}")

    if target_url_token:
        matches = _get_tabs_by_url_route_token(browser, target_url_token)
        if not matches:
            raise HTTPException(status_code=404, detail="URL 路由没有匹配的已打开标签页")
        idle_matches = [
            item for item in matches
            if str(item.get("status") or "").strip().lower() == "idle"
        ]
        return _select_round_robin_tab(idle_matches or matches, f"url_token::{target_url_token}")

    if target_model_name:
        matches = _get_tabs_by_exposed_model_name(browser, target_model_name)
        if not matches:
            raise HTTPException(status_code=404, detail="模型显示名称没有匹配的已打开标签页")
        idle_matches = [
            item for item in matches
            if str(item.get("status") or "").strip().lower() == "idle"
        ]
        return _select_round_robin_tab(idle_matches or matches, f"model::{target_model_name}")

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

    return min(pool, key=_tab_persistent_index)


def _build_tab_resolution_headers(
    tab_info: Optional[Dict[str, Any]],
    *,
    route_domain: str = "",
    exact_url: str = "",
    model_name: str = "",
    selector: str = "",
    preset_name: str = "",
    route_group: str = "",
    route_group_live_member_count: Optional[int] = None,
    route_group_idle_member_count: Optional[int] = None,
    route_group_busy_member_count: Optional[int] = None,
) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    requested_route_domain = str(route_domain or "").strip()
    requested_exact_url = str(exact_url or "").strip()
    requested_model_name = _normalize_model_name(model_name)
    requested_route_group = normalize_route_group_id(route_group)

    if requested_route_group:
        headers["X-Requested-Route-Group"] = requested_route_group
        headers["X-Resolved-Route-Group"] = requested_route_group
        if route_group_live_member_count is not None:
            headers["X-Route-Group-Live-Member-Count"] = str(
                max(int(route_group_live_member_count), 0)
            )
        if route_group_idle_member_count is not None:
            headers["X-Route-Group-Idle-Member-Count"] = str(
                max(int(route_group_idle_member_count), 0)
            )
        if route_group_busy_member_count is not None:
            headers["X-Route-Group-Busy-Member-Count"] = str(
                max(int(route_group_busy_member_count), 0)
            )
        if requested_route_domain:
            headers["X-Resolved-Route-Domain"] = (
                normalize_route_domain(requested_route_domain) or requested_route_domain
            )

    if requested_route_domain:
        headers["X-Requested-Route-Domain"] = (
            normalize_route_domain(requested_route_domain) or requested_route_domain
        )

    if requested_exact_url:
        headers["X-Requested-Exact-Url"] = normalize_exact_tab_url(requested_exact_url) or requested_exact_url

    if requested_model_name:
        headers["X-Requested-Model-Name"] = requested_model_name

    if selector:
        headers["X-Tab-Selection-Mode"] = selector

    if preset_name:
        headers["X-Resolved-Preset-Name"] = preset_name

    if not tab_info:
        return _encode_response_headers(headers)

    tab_index = int(tab_info.get("persistent_index") or 0)
    if tab_index > 0:
        headers["X-Resolved-Tab-Index"] = str(tab_index)

    tab_id = str(tab_info.get("id") or "").strip()
    if tab_id:
        headers["X-Resolved-Tab-Id"] = tab_id

    current_url = str(tab_info.get("url") or "").strip()
    if current_url:
        headers["X-Resolved-Tab-Url"] = current_url

    if exact_url:
        headers["X-Resolved-Exact-Url"] = normalize_exact_tab_url(exact_url) or exact_url

    current_domain = str(tab_info.get("current_domain") or tab_info.get("route_domain") or route_domain or "").strip()
    if current_domain:
        headers["X-Resolved-Route-Domain"] = current_domain

    exposed_model_name = _normalize_model_name(tab_info.get("exposed_model_name"))
    if exposed_model_name:
        headers["X-Resolved-Model-Name"] = exposed_model_name

    return _encode_response_headers(headers)


def _build_stream_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    if extra:
        headers.update(_encode_response_headers(extra))
    return headers


def _get_tab_config_domain(tab_info: Dict[str, Any]) -> str:
    domain = str(tab_info.get("current_domain") or tab_info.get("route_domain") or "").strip()
    if domain:
        return domain

    url = str(tab_info.get("url") or "").strip()
    try:
        return extract_remote_site_domain(url) or ""
    except Exception:
        return ""


def _resolve_strict_tab_preset(tab_info: Dict[str, Any], preset_name: str) -> Dict[str, str]:
    requested = str(preset_name or "").strip()
    if not requested:
        raise HTTPException(status_code=400, detail="预设名称不能为空")

    raw_domain = _get_tab_config_domain(tab_info)
    if not raw_domain:
        raise HTTPException(status_code=400, detail="URL 路由已匹配标签页，但无法解析站点域名")

    # 标签页记的是页面真实主机名（可能是 www. 前缀或 site_rules 里的别名），
    # 而预设是按配置里的站点域名存的。这里和 _resolve_strict_domain_preset、
    # /api/tab-pool/tabs 的预设回显保持一致，补上 canonical 域名候选，
    # 否则面板上显示"有这个预设"、走 /tab/{i}?preset_name=... 却 404。
    candidate_domains = [
        item for item in (
            normalize_route_domain(raw_domain) or raw_domain,
            get_canonical_route_domain(raw_domain) or "",
        ) if item
    ]
    candidate_domains = list(dict.fromkeys(candidate_domains)) or [raw_domain]

    try:
        from app.services.config_engine import config_engine

        domain = candidate_domains[0]
        preset_map: Dict[str, bool] = {}
        resolved = requested
        for candidate_domain in candidate_domains:
            candidate_presets = config_engine.list_presets(candidate_domain)
            candidate_preset_map = {str(name): True for name in candidate_presets}
            candidate_resolved = config_engine._resolve_preset_alias_key(requested, candidate_preset_map)
            if candidate_preset_map and candidate_resolved in candidate_preset_map:
                domain = candidate_domain
                preset_map = candidate_preset_map
                resolved = candidate_resolved
                break
            if not preset_map:
                domain = candidate_domain
                preset_map = candidate_preset_map
                resolved = candidate_resolved
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"校验 URL 绑定预设失败: {e}")
        raise HTTPException(status_code=500, detail=f"校验预设失败: {e}")

    if not preset_map:
        raise HTTPException(status_code=404, detail=f"URL 路由对应站点 '{domain}' 没有可用预设")

    if resolved not in preset_map:
        raise HTTPException(status_code=404, detail=f"URL 路由对应站点 '{domain}' 找不到预设: {requested}")

    return {
        "domain": domain,
        "preset_name": resolved,
    }


def _resolve_strict_domain_preset(route_domain: str, preset_name: str) -> Dict[str, str]:
    requested = str(preset_name or "").strip()
    if not requested:
        raise HTTPException(status_code=400, detail="预设名称不能为空")

    raw_domain = normalize_route_domain(route_domain) or str(route_domain or "").strip()
    if not raw_domain:
        raise HTTPException(status_code=400, detail="域名路由不能为空")
    canonical_domain = get_canonical_route_domain(raw_domain) or ""
    candidate_domains = [
        item for item in (raw_domain, canonical_domain)
        if item
    ]
    candidate_domains = list(dict.fromkeys(candidate_domains))

    try:
        from app.services.config_engine import config_engine

        domain = candidate_domains[0]
        preset_map: Dict[str, bool] = {}
        resolved = requested
        for candidate_domain in candidate_domains:
            candidate_presets = config_engine.list_presets(candidate_domain)
            candidate_preset_map = {str(name): True for name in candidate_presets}
            candidate_resolved = config_engine._resolve_preset_alias_key(requested, candidate_preset_map)
            if candidate_preset_map and candidate_resolved in candidate_preset_map:
                domain = candidate_domain
                preset_map = candidate_preset_map
                resolved = candidate_resolved
                break
            if not preset_map:
                domain = candidate_domain
                preset_map = candidate_preset_map
                resolved = candidate_resolved
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"校验域名路由预设失败: {e}")
        raise HTTPException(status_code=500, detail=f"校验预设失败: {e}")

    if not preset_map:
        raise HTTPException(status_code=404, detail=f"域名路由对应站点 '{domain}' 没有可用预设")

    if resolved not in preset_map:
        raise HTTPException(status_code=404, detail=f"域名路由对应站点 '{domain}' 找不到预设: {requested}")

    return {
        "domain": domain,
        "preset_name": resolved,
    }


class ChatRequest(BaseModel):
    """聊天请求模型"""
    workflow_variables: Dict[str, Any] = Field(default_factory=dict)

    @field_validator('workflow_variables')
    @classmethod
    def validate_workflow_variables(cls, value):
        from app.core.workflow.flow_runtime import FlowValidationError, validate_inputs
        try:
            return validate_inputs(value)
        except FlowValidationError as exc:
            raise ValueError(str(exc)) from exc

    model: str = Field(default="未知")
    messages: list = Field(...)
    stream: Optional[bool] = Field(default=False)
    temperature: Optional[float] = Field(default=0.7, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    n: Optional[int] = Field(default=1, ge=1)
    response_format: Optional[dict] = Field(default=None)
    stop: Optional[Any] = Field(default=None)
    tools: Optional[list] = Field(default=None)
    tool_choice: Optional[Any] = Field(default=None)
    parallel_tool_calls: Optional[bool] = Field(default=None)
    functions: Optional[list] = Field(default=None)
    function_call: Optional[Any] = Field(default=None)
    preset_name: Optional[str] = Field(default=None)
    stream_options: Optional[dict] = Field(default=None)


def _apply_response_format_if_needed(body: ChatRequest) -> ChatRequest:
    if bool(getattr(body, "_response_format_applied", False)):
        return body

    response_format = body.response_format
    if not isinstance(response_format, dict):
        return body
    format_type = str(response_format.get("type") or "text").strip().lower() or "text"
    if format_type == "text":
        return body

    try:
        from app.api.chat import _apply_response_format

        updated = body.model_copy(
            update={
                "messages": _apply_response_format(body.messages, response_format),
            }
        )
        setattr(updated, "_response_format_applied", True)
        return updated
    except Exception as e:
        logger.debug(f"response_format 转化失败（已忽略）: {e}")
        return body


def _maybe_pack_stream_usage_chunk(body: ChatRequest) -> Optional[str]:
    try:
        from app.api.chat import _maybe_pack_stream_usage_chunk as _pack_usage

        return _pack_usage(body)
    except Exception as e:
        logger.debug(f"stream_options.include_usage 转化失败（已忽略）: {e}")
        return None


def _split_sse_done_frame(chunk: Any) -> tuple[str, bool]:
    try:
        from app.api.chat import _split_sse_done_frame as _split_done

        return _split_done(chunk)
    except Exception:
        return str(chunk or ""), False


def _iter_stream_chunks_with_optional_usage(body: ChatRequest, chunks):
    try:
        from app.api.chat import _iter_stream_chunks_with_optional_usage as _iter_chunks
    except Exception as e:
        logger.debug(f"stream_options.include_usage 流式分块处理失败（已回退）: {e}")
        yield from chunks
        return

    yield from _iter_chunks(body, chunks)


class _RouteToolCallingExecutionCancelled(Exception):
    """Raised when a route-bound tool-calling worker is still running after cancellation."""


def _get_route_tool_calling_cancel_reason(ctx: RequestContext) -> str:
    reason = str(ctx.cancel_reason or "").strip()
    return reason or "tool_calling_cancelled"


def _format_route_tool_calling_error(error: Any) -> tuple[str, str]:
    if _is_absolute_request_timeout_error(error):
        return "请求执行超过最大绝对超时，已强制中断", "absolute_request_timeout"
    return f"执行错误: {error}", str(getattr(error, "code", None) or "tool_calling_failed")


async def _run_tracked_route_tool_calling_worker(
    worker_fn,
    *,
    ctx: RequestContext,
    worker_state: Dict[str, Any],
    label: str,
) -> Any:
    try:
        return await run_tracked_blocking_call(
            worker_fn,
            ctx=ctx,
            worker_state=worker_state,
            label=label,
            poll_timeout=STREAM_QUEUE_POLL_TIMEOUT,
        )
    except TrackedWorkerExecutionCancelled as e:
        raise _RouteToolCallingExecutionCancelled(
            str(e) or _get_route_tool_calling_cancel_reason(ctx)
        )
