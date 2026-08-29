"""
app/services/catalog_routing.py - 通用动态模型目录与路由派发器

职责：
- 统一管理各站点的动态模型目录提供者（CatalogProvider）
- 抽象统一的 CatalogRouter，支持动态目录模型匹配、Tab 挑选及模型列表发现
- 彻底解耦 API 层（chat.py）与具体业务模型目录（如 Arena Direct）的实现细节
"""

from __future__ import annotations

import random
import sys
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Set, Tuple

from app.core.config import get_logger
from app.utils.site_url import route_domain_matches

logger = get_logger("SERVICES.CATALOG_ROUTING")


def _get_chat_or_service_attr(name: str, fallback_module: Optional[Any] = None) -> Any:
    chat_mod = sys.modules.get("app.api.chat")
    if chat_mod and hasattr(chat_mod, name):
        return getattr(chat_mod, name)
    if fallback_module and hasattr(fallback_module, name):
        return getattr(fallback_module, name)
    return None


def select_catalog_tab(
    browser: Any,
    candidates: List[Dict[str, Any]],
    preset_name: Any = None,
    cursor_prefix: str = "arena_catalog",
) -> Optional[Dict[str, Any]]:
    """Select a tab from candidates respecting the tab pool's allocation policy."""
    if not candidates:
        return None

    idle_candidates = [
        item
        for item in candidates
        if str(item.get("status") or "").strip().lower() == "idle"
    ]
    pool = idle_candidates or candidates

    try:
        allocation_mode = str(
            getattr(browser.tab_pool, "allocation_mode", "") or ""
        ).strip().lower()
    except Exception:
        allocation_mode = ""
    if allocation_mode not in {"first_idle", "round_robin", "random"}:
        allocation_mode = "first_idle"

    if allocation_mode == "round_robin":
        from app.api import tab_routes as tab_routes_api

        cursor_key = f"{cursor_prefix}::{str(preset_name or '').strip().casefold()}"
        return tab_routes_api._select_round_robin_tab(pool, cursor_key)
    if allocation_mode == "random":
        return random.choice(pool)
    return min(pool, key=lambda item: int(item.get("persistent_index") or 0))


class CatalogProvider(Protocol):
    """Protocol defining a dynamic catalog provider for model discovery and routing."""

    @property
    def name(self) -> str: ...

    @property
    def route_domain(self) -> str: ...

    def match_tab_model(
        self,
        browser: Any,
        config_engine: Any,
        tab: Dict[str, Any],
        requested_model: Any,
    ) -> Optional[Dict[str, Any]]: ...

    def collect_catalog_configs(
        self,
        config_engine: Any,
        tabs: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]: ...

    def is_alias_suppressed(
        self,
        item: Dict[str, Any],
        catalog_configs: List[Dict[str, Any]],
    ) -> bool: ...

    def list_catalog_model_entries(
        self,
        browser: Any,
        catalog_configs: List[Dict[str, Any]],
        created: int,
    ) -> List[Dict[str, Any]]: ...


class ArenaCatalogProvider:
    """Dynamic catalog provider for Arena Direct models."""

    @property
    def name(self) -> str:
        return "arena_direct"

    @property
    def route_domain(self) -> str:
        return "arena.ai"

    def match_tab_model(
        self,
        browser: Any,
        config_engine: Any,
        tab: Dict[str, Any],
        requested_model: Any,
    ) -> Optional[Dict[str, Any]]:
        import app.services.arena_direct_models as adm

        get_catalog_fn = _get_chat_or_service_attr("get_arena_direct_catalog_for_tab", adm)
        list_models_fn = _get_chat_or_service_attr("list_arena_direct_models", adm)
        match_model_fn = _get_chat_or_service_attr("match_arena_direct_model", adm)
        get_public_id_fn = _get_chat_or_service_attr("get_arena_direct_model_public_id", adm)

        catalog_info = get_catalog_fn(config_engine, tab) if callable(get_catalog_fn) else None
        catalog = catalog_info.get("catalog") if catalog_info else None
        if not isinstance(catalog, dict) or not catalog.get("enabled"):
            return None

        try:
            models = list_models_fn(browser, catalog_config=catalog, tab=tab)
        except TypeError:
            models = list_models_fn(browser, catalog_config=catalog)

        model = match_model_fn(models, requested_model)
        if not model:
            return None

        preset_name = str(catalog_info.get("preset_name") or "")
        public_model_id = get_public_id_fn(model) if callable(get_public_id_fn) else ""
        return {
            "tab": tab,
            "model": model,
            "preset_name": preset_name,
            "public_model_id": public_model_id,
            "route_domain": self.route_domain,
        }

    def collect_catalog_configs(
        self,
        config_engine: Any,
        tabs: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        import app.services.arena_direct_models as adm

        get_catalog_fn = _get_chat_or_service_attr("get_arena_direct_catalog_for_tab", adm)
        normalize_config_fn = _get_chat_or_service_attr("normalize_model_catalog_config", adm)

        catalog_presets: List[Dict[str, Any]] = []
        seen_catalog_keys: Set[Tuple[Any, ...]] = set()

        for tab in tabs or []:
            candidate = get_catalog_fn(config_engine, tab) if callable(get_catalog_fn) else None
            cat_obj = candidate.get("catalog") if isinstance(candidate, dict) else None
            if isinstance(cat_obj, dict) and cat_obj.get("enabled"):
                cat = cat_obj
            else:
                cat = None
                if route_domain_matches(self.route_domain, tab.get("route_domain") or tab.get("current_domain") or ""):
                    effective_preset_name = str(
                        tab.get("preset_name") or config_engine.get_default_preset(self.route_domain) or ""
                    ).strip()
                    try:
                        preset = config_engine._get_site_data_readonly(self.route_domain, effective_preset_name or None)
                        if isinstance(preset, dict) and callable(normalize_config_fn):
                            cat_candidate = normalize_config_fn(preset.get("model_catalog"))
                            if cat_candidate.get("enabled") and cat_candidate.get("source") == "arena_direct":
                                cat = cat_candidate
                    except Exception:
                        pass

            if cat and cat.get("enabled"):
                cat_key = (
                    cat.get("source", ""),
                    cat.get("modality", ""),
                    tuple(cat.get("include_keywords") or []),
                    tuple(cat.get("exclude_keywords") or []),
                    bool(cat.get("enable_dark_pool", False)),
                    str(cat.get("dark_pool_since") or "").strip(),
                    tuple(cat.get("dark_pool_whitelist_keywords") or []),
                    tuple(cat.get("dark_pool_blacklist_keywords") or []),
                )
                if cat_key not in seen_catalog_keys:
                    seen_catalog_keys.add(cat_key)
                    catalog_presets.append(cat)

        return catalog_presets

    def is_alias_suppressed(
        self,
        item: Dict[str, Any],
        catalog_configs: List[Dict[str, Any]],
    ) -> bool:
        if not catalog_configs or not item.get("is_route_alias"):
            return False
        item_route_domains = list(item.get("route_domains") or [])
        if item.get("route_domain"):
            item_route_domains.append(item.get("route_domain"))
        return any(route_domain_matches(self.route_domain, domain) for domain in item_route_domains)

    def list_catalog_model_entries(
        self,
        browser: Any,
        catalog_configs: List[Dict[str, Any]],
        created: int,
    ) -> List[Dict[str, Any]]:
        import app.services.arena_direct_models as adm

        list_models_fn = _get_chat_or_service_attr("list_arena_direct_models", adm)
        build_entries_fn = _get_chat_or_service_attr("build_openai_model_entries", adm)

        entries: List[Dict[str, Any]] = []
        for cat in catalog_configs:
            cat_models = list_models_fn(browser, catalog_config=cat) if callable(list_models_fn) else []
            model_entries = build_entries_fn(cat_models, created=created) if callable(build_entries_fn) else []
            for item in model_entries:
                entries.append({
                    "id": item.get("id"),
                    "owned_by": item.get("owned_by") or self.route_domain,
                    "display_name": item.get("display_name") or item.get("id"),
                })
        return entries


class CatalogRouter:
    """Universal dispatcher for dynamic catalog matching and model enumeration."""

    def __init__(self, providers: Optional[Sequence[CatalogProvider]] = None):
        self._providers: List[CatalogProvider] = list(providers or [ArenaCatalogProvider()])

    def register_provider(self, provider: CatalogProvider) -> None:
        self._providers.append(provider)

    def match_route(
        self,
        browser: Any,
        config_engine: Any,
        tabs: List[Dict[str, Any]],
        requested_model: Any,
        preset_name: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """Match a requested model against all registered catalog providers across open tabs."""
        if not browser or not tabs:
            return None

        # Check if chat API has monkeypatched match/select function
        chat_match_fn = _get_chat_or_service_attr("_match_arena_catalog_tab")
        if callable(chat_match_fn) and not getattr(chat_match_fn, "_is_default_wrapper", False):
            matched_tab_info = chat_match_fn(browser, config_engine, tabs, requested_model)
            if matched_tab_info:
                matched_tab = matched_tab_info.get("tab") or {}
                matched_model = matched_tab_info.get("model") or {}
                matched_preset = matched_tab_info.get("preset_name") or preset_name
                import app.services.arena_direct_models as adm
                get_public_id_fn = _get_chat_or_service_attr("get_arena_direct_model_public_id", adm)
                public_model_id = get_public_id_fn(matched_model) if callable(get_public_id_fn) else str(requested_model or "")
                catalog_tab_index = int(matched_tab.get("persistent_index") or 0) or None
                return {
                    "route_domain": "arena.ai",
                    "route_type": "model_catalog",
                    "model_name": public_model_id or str(requested_model or ""),
                    "matched_id": public_model_id or str(requested_model or ""),
                    "match_type": "catalog",
                    "preset_name": matched_preset,
                    "available_model_ids": [public_model_id] if public_model_id else [],
                    "catalog_tab": matched_tab,
                    "catalog_tab_index": catalog_tab_index,
                    "matched_model": matched_model,
                }
            return None

        is_url_excluded = getattr(getattr(browser, "tab_pool", None), "is_url_excluded", None)

        for provider in self._providers:
            matches: List[Dict[str, Any]] = []
            for tab in tabs:
                tab_url = str(tab.get("current_url") or tab.get("url") or "").strip()
                if callable(is_url_excluded) and is_url_excluded(tab_url):
                    continue

                matched = provider.match_tab_model(browser, config_engine, tab, requested_model)
                if matched:
                    matches.append(matched)

            if not matches:
                continue

            select_tab_fn = _get_chat_or_service_attr("_select_arena_catalog_tab")
            if callable(select_tab_fn) and select_tab_fn is not select_catalog_tab:
                selected_tab = select_tab_fn(
                    browser,
                    [entry["tab"] for entry in matches],
                    preset_name=preset_name,
                )
            else:
                selected_tab = select_catalog_tab(
                    browser,
                    [entry["tab"] for entry in matches],
                    preset_name=preset_name,
                )

            if not selected_tab:
                continue

            selected_index = int(selected_tab.get("persistent_index") or 0)
            selected_tab_id = str(selected_tab.get("id") or selected_tab.get("tab_id") or "").strip()
            target_entry = next(
                (
                    entry
                    for entry in matches
                    if entry["tab"] is selected_tab
                    or (selected_tab_id and str(entry["tab"].get("id") or entry["tab"].get("tab_id") or "").strip() == selected_tab_id)
                    or int(entry["tab"].get("persistent_index") or 0) == selected_index
                ),
                None,
            )
            if not target_entry:
                continue

            catalog_tab = target_entry["tab"]
            catalog_match = target_entry["model"]
            matched_preset_name = target_entry["preset_name"]
            public_model_id = target_entry.get("public_model_id") or ""
            catalog_tab_index = int(catalog_tab.get("persistent_index") or 0) or None

            matched_uuid = str(
                catalog_match.get("id")
                or catalog_match.get("uuid")
                or catalog_match.get("arena_model_id")
                or ""
            )
            matched_public_name = str(
                catalog_match.get("public_name") or catalog_match.get("publicName") or ""
            )
            matched_display_name = str(
                catalog_match.get("display_name")
                or catalog_match.get("displayName")
                or catalog_match.get("name")
                or ""
            )

            logger.info(
                f"[CATALOG_ROUTER:MATCH] 成功匹配动态目录模型: provider={provider.name}, "
                f"requested_model={requested_model!r}, preset={matched_preset_name!r}, "
                f"uuid={matched_uuid!r}, public_name={matched_public_name!r}, "
                f"display_name={matched_display_name!r}, public_model_id={public_model_id!r}, "
                f"target_tab_index={catalog_tab_index}, "
                f"target_tab_url={catalog_tab.get('current_url') or catalog_tab.get('url')}"
            )

            return {
                "route_domain": provider.route_domain,
                "route_type": "model_catalog",
                "model_name": public_model_id or str(requested_model or ""),
                "matched_id": public_model_id or str(requested_model or ""),
                "match_type": "catalog",
                "preset_name": matched_preset_name,
                "available_model_ids": [public_model_id] if public_model_id else [],
                "catalog_tab": catalog_tab,
                "catalog_tab_index": catalog_tab_index,
                "matched_model": catalog_match,
            }

        return None

    def collect_models_for_catalog(
        self,
        browser: Any,
        config_engine: Any,
        tabs: List[Dict[str, Any]],
        append_entry: Callable[[str, Optional[str], Optional[str]], None],
        created: int,
    ) -> None:
        """Collect models across tabs and catalog providers for the /v1/models endpoint."""
        from app.utils.model_routing import collect_route_domain_models

        # 1. 收集所有 Provider 的 Catalog 预设配置
        provider_configs: Dict[str, List[Dict[str, Any]]] = {}
        for provider in self._providers:
            configs = provider.collect_catalog_configs(config_engine, tabs)
            provider_configs[provider.name] = configs

        # 2. 输出通用 Route Domain 模型，对被 Catalog 接管的 alias 模型进行抑制
        for item in collect_route_domain_models(tabs):
            suppressed = any(
                provider.is_alias_suppressed(item, provider_configs.get(provider.name, []))
                for provider in self._providers
            )
            if suppressed:
                continue

            append_entry(
                item.get("id"),
                item.get("route_domain") or "universal-web-api",
                item.get("display_name") or item.get("id"),
            )

        # 3. 输出各 Catalog Provider 发现的动态模型列表
        for provider in self._providers:
            configs = provider_configs.get(provider.name, [])
            if not configs:
                continue
            for model_entry in provider.list_catalog_model_entries(browser, configs, created):
                append_entry(
                    model_entry.get("id"),
                    model_entry.get("owned_by") or provider.route_domain,
                    model_entry.get("display_name") or model_entry.get("id"),
                )


catalog_router = CatalogRouter()
