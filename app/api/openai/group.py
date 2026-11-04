"""``/group`` 路由分组（由 app/api/tab_routes.py 拆出）。"""

from typing import Any, Dict, List, Optional

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.api.deps import verify_service_auth
from app.api.openai import runtime
from app.api.openai.shared import (
    MODEL_LIST_CREATED,
    ChatRequest,
    _build_route_model_entries_payload,
    _build_tab_resolution_headers,
    _read_browser_config,
    _resolve_strict_domain_preset,
    _route_method_guard,
    logger,
    router,
)
from app.api.openai.url import (
    _chat_with_route_domain,
)
from app.services.arena_direct_models import (
    build_openai_model_entries,
    get_arena_direct_catalog_for_tab,
    list_arena_direct_models,
)
from app.utils.site_url import normalize_exact_tab_url, route_domain_matches
from app.utils.tab_route_groups import (
    normalize_route_group_id,
    normalize_route_groups,
    route_group_member_key,
    route_groups_by_id,
)


def _get_route_groups_from_config(config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    payload = config if isinstance(config, dict) else _read_browser_config()
    tab_pool_config = payload.get("tab_pool") if isinstance(payload, dict) else {}
    if not isinstance(tab_pool_config, dict):
        tab_pool_config = {}
    return normalize_route_groups(tab_pool_config.get("route_groups"))


def _get_tab_pool_route_groups(tab_pool: Any, config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    try:
        if hasattr(tab_pool, "get_route_groups_snapshot"):
            return tab_pool.get_route_groups_snapshot()
        route_groups = getattr(tab_pool, "route_groups", None)
        if route_groups is not None:
            return normalize_route_groups(route_groups)
    except Exception:
        pass
    return _get_route_groups_from_config(config)


def _resolve_route_group(browser: Any, group_id: str) -> Dict[str, Any]:
    normalized_id = normalize_route_group_id(group_id)
    if not normalized_id:
        raise HTTPException(status_code=400, detail="标签页路由组 ID 无效")

    groups = browser.tab_pool.get_route_groups_snapshot()
    group = route_groups_by_id(groups).get(normalized_id)
    if not group:
        raise HTTPException(status_code=404, detail=f"标签页路由组 '{normalized_id}' 不存在")

    runtime_group = next((
        item
        for item in groups
        if isinstance(item, dict)
        and normalize_route_group_id(item.get("id") or item.get("group_id"))
        == normalized_id
    ), None)
    if runtime_group:
        for key in (
            "live_member_count",
            "idle_member_count",
            "busy_member_count",
            "live_tab_indices",
        ):
            if key in runtime_group:
                group[key] = runtime_group[key]
    return group


def _collect_models_for_route_group(
    browser: Any,
    group: Dict[str, Any],
    preset_name: Optional[str] = None,
    anthropic_version: Optional[str] = None,
) -> Dict[str, Any]:
    """为路由组收集模型条目：
    1. 优先提取对应预设中的 Model Catalog 模型（如 Arena Direct 模型列表）；
    2. 提取组内成员标签页的自定义 exposed_model_name；
    3. 若均无具体模型，回退返回 group ID 聚合名称。
    """
    resolved_preset = str(preset_name or group.get("preset_name") or "").strip() or None
    route_domain = str(group.get("route_domain") or "").strip()

    group_tabs: List[Dict[str, Any]] = []
    try:
        tabs_snapshot = browser.tab_pool.get_tabs_with_index()
        raw_members = group.get("members") or []
        group_member_keys = {route_group_member_key(m) for m in raw_members if m}
        for t in tabs_snapshot:
            t_url = normalize_exact_tab_url(str(t.get("url") or ""))
            t_token = str(t.get("url_route_token") or "").strip().lower()
            t_idx = int(t.get("persistent_index") or 0)
            if (
                f"{t_token}::#{t_idx}" in group_member_keys
                or f"{t_token}::#*" in group_member_keys
                or f"{t_url}::#{t_idx}" in group_member_keys
                or f"{t_url}::#*" in group_member_keys
            ):
                group_tabs.append(t)
        if not group_tabs and route_domain:
            group_tabs = [
                t for t in tabs_snapshot
                if route_domain_matches(route_domain, t.get("current_domain") or t.get("route_domain") or "")
            ]
    except Exception as e:
        logger.debug(f"读取路由组成员标签页失败（已忽略）: {e}")
        group_tabs = []

    if not route_domain and group_tabs:
        first_domain = str(group_tabs[0].get("current_domain") or group_tabs[0].get("route_domain") or "").strip()
        if first_domain:
            route_domain = first_domain

    catalog_entries: List[Dict[str, Any]] = []
    try:
        from app.services.config_engine import config_engine

        if route_domain and route_domain_matches(route_domain, "arena.ai"):
            catalog_preset = None
            for t in group_tabs:
                catalog_preset = get_arena_direct_catalog_for_tab(
                    config_engine,
                    t,
                    preset_name=resolved_preset,
                )
                if catalog_preset:
                    break
            if not catalog_preset:
                catalog_preset = get_arena_direct_catalog_for_tab(
                    config_engine,
                    {
                        "status": "idle",
                        "url": f"https://{route_domain}/c",
                        "current_domain": route_domain,
                        "route_domain": route_domain,
                    },
                    preset_name=resolved_preset,
                )
            if catalog_preset:
                catalog_entries = build_openai_model_entries(
                    list_arena_direct_models(
                        browser,
                        catalog_config=catalog_preset.get("catalog"),
                    ),
                    created=MODEL_LIST_CREATED,
                )
    except Exception as e:
        logger.debug(f"构建路由组模型目录失败（已忽略）: {e}")

    custom_entries: List[Dict[str, Any]] = []
    seen_ids = {str(e.get("id") or "").strip().lower() for e in catalog_entries}
    for t in group_tabs:
        if bool(t.get("model_name_override_source")):
            exp_name = str(t.get("exposed_model_name") or t.get("model_name_override") or "").strip()
            if exp_name and exp_name.lower() not in seen_ids:
                seen_ids.add(exp_name.lower())
                custom_entries.append({
                    "id": exp_name,
                    "object": "model",
                    "type": "model",
                    "created": MODEL_LIST_CREATED,
                    "owned_by": "universal-web-api",
                    "display_name": exp_name,
                })

    all_entries = catalog_entries + custom_entries
    if not all_entries:
        all_entries = [{
            "id": group["id"],
            "object": "model",
            "type": "model",
            "created": MODEL_LIST_CREATED,
            "owned_by": "universal-web-api",
            "display_name": str(group.get("name") or group["id"]),
        }]

    return _build_route_model_entries_payload(
        all_entries,
        anthropic_version=anthropic_version,
    )


@router.get("/group/{group_id}/v1/models")
async def list_models_with_route_group(
    group_id: str,
    anthropic_version: Optional[str] = Header(None, alias="anthropic-version"),
    authenticated: bool = Depends(verify_service_auth),
    _route_method: None = Depends(_route_method_guard("route_group")),
):
    browser = runtime.get_browser(auto_connect=False)
    group = _resolve_route_group(browser, group_id)
    payload = _collect_models_for_route_group(
        browser,
        group,
        preset_name=None,
        anthropic_version=anthropic_version,
    )
    response = JSONResponse(content=payload)
    response.headers.update(_build_tab_resolution_headers(
        None,
        route_domain=group.get("route_domain") or "",
        selector=group.get("allocation_mode") or "round_robin",
        route_group=group["id"],
        route_group_live_member_count=group.get("live_member_count"),
        route_group_idle_member_count=group.get("idle_member_count"),
        route_group_busy_member_count=group.get("busy_member_count"),
    ))
    return response


@router.get("/group/{group_id}/{preset_name}/v1/models")
async def list_models_with_route_group_and_preset(
    group_id: str,
    preset_name: str,
    anthropic_version: Optional[str] = Header(None, alias="anthropic-version"),
    authenticated: bool = Depends(verify_service_auth),
    _route_method: None = Depends(_route_method_guard("route_group")),
):
    browser = runtime.get_browser(auto_connect=False)
    group = _resolve_route_group(browser, group_id)
    route_domain = str(group.get("route_domain") or "").strip()
    preset_resolution = (
        _resolve_strict_domain_preset(route_domain, preset_name)
        if route_domain
        else {"domain": "", "preset_name": preset_name}
    )
    payload = _collect_models_for_route_group(
        browser,
        group,
        preset_name=preset_resolution["preset_name"],
        anthropic_version=anthropic_version,
    )
    response = JSONResponse(content=payload)
    response.headers.update(_build_tab_resolution_headers(
        None,
        route_domain=route_domain,
        selector=group.get("allocation_mode") or "round_robin",
        preset_name=preset_resolution["preset_name"],
        route_group=group["id"],
        route_group_live_member_count=group.get("live_member_count"),
        route_group_idle_member_count=group.get("idle_member_count"),
        route_group_busy_member_count=group.get("busy_member_count"),
    ))
    return response


@router.post("/group/{group_id}/v1/chat/completions")
async def chat_with_route_group(
    group_id: str,
    request: Request,
    body: ChatRequest,
    preset_name: Optional[str] = Query(default=None),
    authenticated: bool = Depends(verify_service_auth),
    _route_method: None = Depends(_route_method_guard("route_group")),
):
    client_fp = runtime.cancel_storm_guard.get_client_fingerprint(request)
    await runtime.cancel_storm_guard.maybe_backoff(client_fp)

    browser = runtime.get_browser(auto_connect=False)
    group = _resolve_route_group(browser, group_id)
    route_domain = str(group.get("route_domain") or "").strip()
    resolved_preset_name = str(
        preset_name or group.get("preset_name") or body.preset_name or ""
    ).strip() or None
    if resolved_preset_name:
        if route_domain:
            resolved_preset_name = _resolve_strict_domain_preset(
                route_domain,
                resolved_preset_name,
            )["preset_name"]
        else:
            # 路由组的 route_domain 是可选字段（normalize_route_groups 允许留空，
            # 组内成员选取也完全不依赖它），此前只要带预设就直接 400，
            # 等于让"配了预设但没填域名"的组彻底不可用。没有域名时无法做站点级
            # 校验，交给下游按实际命中的标签页所属站点解析（组成员本来也可能跨站）。
            logger.debug(
                f"路由组 '{group['id']}' 未配置站点域名，预设 "
                f"'{resolved_preset_name}' 改由命中的标签页按其站点解析"
            )
    if resolved_preset_name != body.preset_name:
        body = body.model_copy(update={"preset_name": resolved_preset_name})

    ctx = runtime.request_manager.create_request(client_fp=client_fp)
    try:
        raw_input_len = sum(
            len(str(msg.get("content") or ""))
            for msg in body.messages if isinstance(msg, dict)
        )
        logger.info(
            f"[DIAG] 接收到的原始请求 messages 总字符长度: {raw_input_len} 字符, "
            f"消息数: {len(body.messages)}"
        )
    except Exception as e:
        logger.debug(f"[DIAG] 估算原始请求长度失败: {e}")

    runtime.request_manager.record_request_input(
        ctx,
        body.model_dump(),
        endpoint=f"/group/{group['id']}/v1/chat/completions",
        route_domain=route_domain,
        route_group=group["id"],
        preset_name=resolved_preset_name,
    )
    resolved_headers = _build_tab_resolution_headers(
        None,
        route_domain=route_domain,
        selector=group.get("allocation_mode") or "round_robin",
        preset_name=resolved_preset_name or "",
        route_group=group["id"],
        route_group_live_member_count=group.get("live_member_count"),
        route_group_idle_member_count=group.get("idle_member_count"),
        route_group_busy_member_count=group.get("busy_member_count"),
    )
    with logger.context(ctx.request_id):
        logger.info(
            f"开始 (标签页路由组 {group['id']} -> 组内动态标签页, "
            f"selector={group.get('allocation_mode') or 'round_robin'}, "
            f"preset={resolved_preset_name or '<follow-tab/default>'})"
        )
        return await _chat_with_route_domain(
            request,
            body,
            ctx,
            route_domain=route_domain,
            route_group_id=group["id"],
            allocation_mode=group.get("allocation_mode") or "round_robin",
            resolved_headers=resolved_headers,
        )


@router.post("/group/{group_id}/{preset_name}/v1/chat/completions")
async def chat_with_route_group_and_preset(
    group_id: str,
    preset_name: str,
    request: Request,
    body: ChatRequest,
    authenticated: bool = Depends(verify_service_auth),
    _route_method: None = Depends(_route_method_guard("route_group")),
):
    return await chat_with_route_group(
        group_id=group_id,
        request=request,
        body=body,
        preset_name=preset_name,
        authenticated=authenticated,
    )
