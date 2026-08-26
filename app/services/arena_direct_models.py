"""Read Arena Direct's model catalog from the already-loaded page state."""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.core.config import logger
from app.utils.site_url import extract_remote_site_domain, route_domain_matches


ARENA_DIRECT_MODEL_PREFIX = "arena.ai/direct/"
ARENA_DIRECT_MODEL_CACHE_TTL = 300.0
MODEL_CATALOG_SOURCE = "arena_direct"
ARENA_MODEL_ALIAS_OVERRIDES_PATH = Path(
    os.getenv(
        "ARENA_MODEL_ALIAS_OVERRIDES_PATH",
        str(Path(__file__).resolve().parents[2] / "config" / "arena_model_aliases.local.json"),
    )
)


_ARENA_DIRECT_MODEL_EXTRACT_JS = r"""
return (() => {
    // 1. 优先从 React Fiber 提取前端真实加载的全部用户可选模型（精准排除所有隐藏/盲测/暗池模型）
    const all = Array.from(document.querySelectorAll('*'));
    let fiberModels = null;
    for (const el of all) {
        const fiberKey = Object.keys(el).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'));
        if (!fiberKey) continue;
        let cur = el[fiberKey];
        let d = 0;
        while (cur && d < 15) {
            if (cur.memoizedProps) {
                for (const [k, v] of Object.entries(cur.memoizedProps)) {
                    if (Array.isArray(v) && v.length >= 20 && v.length <= 400 && v[0] && (v[0].id && (v[0].displayName || v[0].name || v[0].publicName))) {
                        fiberModels = v;
                        break;
                    }
                }
            }
            if (fiberModels) break;
            cur = cur.return;
            d++;
        }
        if (fiberModels) break;
    }

    const seenIds = new Set();
    const results = [];

    const appendModel = (model, defaultModality) => {
        if (!model || !model.id) return;
        const idStr = String(model.id);
        const idLower = idStr.toLowerCase();
        if (seenIds.has(idLower)) return;
        seenIds.add(idLower);

        const caps = model.capabilities || {};
        const outCaps = caps.outputCapabilities || model.outputCapabilities || {};
        const isImgOut = Boolean(outCaps.image);
        const dispName = String(model.displayName || model.publicName || model.name || idStr).trim();
        const internalName = String(model.name || model.id || dispName).trim();
        const pubName = String(model.publicName || dispName || internalName).trim();

        results.push({
            arena_model_id: idStr,
            name: internalName,
            public_name: dispName,
            display_name: dispName,
            provider: String(model.provider || ''),
            organization: String(model.organization || model.provider || 'arena.ai'),
            modality: (isImgOut || defaultModality === 'image') ? 'image' : 'text'
        });
    };

    if (fiberModels && fiberModels.length > 0) {
        for (const m of fiberModels) {
            appendModel(m, 'text');
        }
    }

    // 3. 从 SSR initialModels 补充核心生图模型及在未挂载时的保底模型
    const readArray = (text, marker) => {
        const markerIndex = text.indexOf(marker);
        if (markerIndex < 0) return null;
        const start = text.indexOf('[', markerIndex + marker.length);
        if (start < 0) return null;
        let depth = 0;
        let quoted = false;
        let escaped = false;
        for (let index = start; index < text.length; index += 1) {
            const char = text[index];
            if (quoted) {
                if (escaped) escaped = false;
                else if (char === '\\') escaped = true;
                else if (char === '"') quoted = false;
                continue;
            }
            if (char === '"') quoted = true;
            else if (char === '[') depth += 1;
            else if (char === ']') {
                depth -= 1;
                if (depth === 0) return text.slice(start, index + 1);
            }
        }
        return null;
    };

    const payloadTexts = [];
    for (const script of document.scripts) {
        const source = String(script.textContent || '').trim();
        const cleanSource = source.replace(/;\s*$/, '');
        const prefix = 'self.__next_f.push(';
        if (!cleanSource.startsWith(prefix) || !cleanSource.endsWith(')')) continue;
        try {
            const payload = JSON.parse(cleanSource.slice(prefix.length, -1));
            if (Array.isArray(payload) && typeof payload[1] === 'string') {
                payloadTexts.push(payload[1]);
            }
        } catch (_) {}
    }

    for (const text of payloadTexts) {
        const rawModels = readArray(text, '"initialModels":');
        if (!rawModels) continue;
        try {
            const models = JSON.parse(rawModels);
            for (const model of models) {
                if (!model) continue;
                const effectiveName = String(model.displayName || model.publicName || model.name || '').trim();
                const nameLower = effectiveName.toLowerCase();
                const isUserSpecifiedImage = nameLower.includes('gpt-image-2') ||
                                             nameLower.includes('mona-lisa') ||
                                             nameLower.includes('luna-lisa') ||
                                             nameLower.includes('lina-alpha') ||
                                             nameLower.includes('lina-f-alpha') ||
                                             nameLower.includes('lina') ||
                                             nameLower.includes('grok-imagine') ||
                                             (nameLower.includes('grok') && nameLower.includes('image'));
                if (isUserSpecifiedImage) {
                    appendModel(model, 'image');
                } else if (!fiberModels || fiberModels.length === 0) {
                    const hasPublicRank = typeof model.rank === 'number' && model.rank < 1000;
                    const isUnrankedExplicit = (model.rank === null || typeof model.rank === 'undefined') &&
                        model.userSelectable !== false &&
                        (nameLower.includes('3.7') || nameLower.includes('glm-5') || nameLower.includes('gemini') || nameLower.includes('claude') || nameLower.includes('gpt') || nameLower.includes('qwen') || nameLower.includes('deepseek') || nameLower.includes('grok') || nameLower.includes('minimax') || nameLower.includes('mistral'));
                    if ((hasPublicRank || isUnrankedExplicit) && model.userSelectable !== false) {
                        appendModel(model, 'text');
                    }
                }
            }
        } catch (_) {}
    }

    return results;
})();
"""


_cache_lock = threading.RLock()
_refresh_lock = threading.Lock()
_cached_at = 0.0
_cached_models: List[Dict[str, Any]] = []


def build_arena_direct_model_id(arena_model_id: Any) -> str:
    clean_id = str(arena_model_id or "").strip()
    return f"{ARENA_DIRECT_MODEL_PREFIX}{clean_id}" if clean_id else ""


def parse_arena_direct_model_id(model_id: Any) -> str:
    value = str(model_id or "").strip()
    if not value.lower().startswith(ARENA_DIRECT_MODEL_PREFIX):
        return ""
    return value[len(ARENA_DIRECT_MODEL_PREFIX):].strip()


def is_arena_direct_model_id(model_id: Any) -> bool:
    return bool(parse_arena_direct_model_id(model_id))


def get_arena_direct_model_public_id(model: Any) -> str:
    if not isinstance(model, dict):
        return ""
    return str(
        model.get("display_name")
        or model.get("public_name")
        or model.get("search_name")
        or model.get("name")
        or ""
    ).strip()


def match_arena_direct_model(
    models: Any,
    requested_model: Any,
) -> Optional[Dict[str, Any]]:
    if not isinstance(models, (list, tuple, set)):
        return None
    requested_value = str(requested_model or "").strip()
    if not requested_value:
        return None
    parsed_id = parse_arena_direct_model_id(requested_value)
    candidates = {requested_value.casefold()}
    if parsed_id:
        candidates.add(parsed_id.casefold())

    # 1. 优先匹配 UUID (arena_model_id)
    for model in models or []:
        if not isinstance(model, dict):
            continue
        mid = str(model.get("arena_model_id") or "").strip().casefold()
        if mid and mid in candidates:
            return model

    # 2. 匹配标准名称 / 展示名称 / 搜索名称
    for model in models or []:
        if not isinstance(model, dict):
            continue
        for key in ("name", "public_name", "display_name", "search_name"):
            val = str(model.get(key) or "").strip().casefold()
            if val and val in candidates:
                return model

    # 3. 匹配别名
    for model in models or []:
        if not isinstance(model, dict):
            continue
        for alias in (model.get("aliases") or []):
            alias_val = str(alias or "").strip().casefold()
            if alias_val and alias_val in candidates:
                return model

    # 4. 匹配去除括号后缀的基础名 (例如 gpt-image-2 匹配 gpt-image-2 (medium))，以及 -high、-preview、尾部数字等基础名
    for model in models or []:
        if not isinstance(model, dict):
            continue
        for key in ("name", "public_name", "display_name", "search_name"):
            raw_val = str(model.get(key) or "").strip().casefold()
            if not raw_val:
                continue
            base_val = raw_val.split("(")[0].strip()
            if base_val and base_val in candidates:
                return model
            if raw_val.endswith("-high"):
                high_base = raw_val[:-5].strip()
                if high_base and high_base in candidates:
                    return model
            if raw_val.endswith("-preview"):
                prev_base = raw_val[:-8].strip()
                if prev_base and prev_base in candidates:
                    return model
            if "-" in raw_val:
                dash_base = raw_val.rsplit("-", 1)[0].strip()
                if dash_base and dash_base in candidates:
                    return model

    return None


def normalize_model_catalog_config(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}

    def _keywords(key: str) -> List[str]:
        source = raw.get(key, [])
        if isinstance(source, str):
            source = re.split(r"[\n,]+", source)
        if not isinstance(source, list):
            return []
        result: List[str] = []
        seen = set()
        for item in source:
            keyword = str(item or "").strip()
            folded = keyword.casefold()
            if not keyword or folded in seen:
                continue
            seen.add(folded)
            result.append(keyword)
        return result

    result = {
        "enabled": bool(raw.get("enabled", False)),
        "source": str(raw.get("source") or MODEL_CATALOG_SOURCE).strip() or MODEL_CATALOG_SOURCE,
        "include_keywords": _keywords("include_keywords"),
        "exclude_keywords": _keywords("exclude_keywords"),
    }
    if "modality" in raw and str(raw.get("modality") or "").strip():
        result["modality"] = str(raw.get("modality") or "").strip().lower()
    return result


def get_model_catalog_preset(config_engine: Any, domain: Any) -> Optional[Dict[str, Any]]:
    normalized_domain = str(domain or "").strip().lower().strip(".")
    if not normalized_domain:
        return None
    try:
        config_engine.refresh_if_changed()
        site = config_engine.sites.get(normalized_domain)
    except Exception:
        return None
    presets = site.get("presets") if isinstance(site, dict) else None
    if not isinstance(presets, dict):
        return None
    for preset_name, preset in presets.items():
        if not isinstance(preset, dict):
            continue
        catalog = normalize_model_catalog_config(preset.get("model_catalog"))
        if catalog["enabled"] and catalog["source"] == MODEL_CATALOG_SOURCE:
            return {
                "preset_name": str(preset_name),
                "preset": preset,
                "catalog": catalog,
            }
    return None


def resolve_model_catalog_preset_for_model(
    config_engine: Any,
    domain: Any,
    tab: Any,
    requested_model: Any,
) -> Optional[Dict[str, Any]]:
    """
    遍历指定域名的所有启用了 model_catalog 的预设，
    根据 requested_model 自动匹配最合适的预设（例如 生图模型自动路由到通用生图预设，文本模型自动路由到通用文本预设）。
    """
    normalized_domain = str(domain or "").strip().lower().strip(".")
    if not normalized_domain or not requested_model:
        return None
    try:
        config_engine.refresh_if_changed()
        site = config_engine.sites.get(normalized_domain)
    except Exception:
        return None
    presets = site.get("presets") if isinstance(site, dict) else None
    if not isinstance(presets, dict):
        return None

    # 按照预设特异性排序：显式声明了 modality 限制的专属预设优先于无模态限制的通用预设
    preset_items = list(presets.items())
    preset_items.sort(
        key=lambda item: (
            0 if bool((normalize_model_catalog_config(item[1].get("model_catalog")) if isinstance(item[1], dict) else {}).get("modality")) else 1
        )
    )

    for preset_name, preset in preset_items:
        if not isinstance(preset, dict):
            continue
        try:
            catalog = normalize_model_catalog_config(preset.get("model_catalog"))
            if not catalog["enabled"] or catalog["source"] != MODEL_CATALOG_SOURCE:
                continue
            matched_model = resolve_arena_direct_model(
                tab,
                requested_model,
                catalog_config=catalog,
            )
            if matched_model:
                return {
                    "preset_name": str(preset_name),
                    "preset": preset,
                    "catalog": catalog,
                    "matched_model": matched_model,
                }
        except Exception as e:
            logger.debug(f"匹配预设 {preset_name} 时发生异常（忽略并继续）: {e}")
            continue
    return None


def get_arena_direct_catalog_for_tab(
    config_engine: Any,
    tab: Any,
    *,
    preset_name: Any = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(tab, dict):
        return None
    status = str(tab.get("status") or "").strip().lower()
    if status not in {"idle", "busy"} or bool(tab.get("terminating")):
        return None

    current_url = str(tab.get("url") or "").strip()
    effective_preset_name = str(preset_name or tab.get("preset_name") or "").strip()
    try:
        config_engine.refresh_if_changed()
        if not effective_preset_name:
            effective_preset_name = str(
                config_engine.get_default_preset("arena.ai") or ""
            ).strip()
        preset = config_engine._get_site_data_readonly(
            "arena.ai",
            effective_preset_name or None,
        )
    except Exception:
        return None
    if not isinstance(preset, dict):
        return None

    catalog = normalize_model_catalog_config(preset.get("model_catalog"))
    if not catalog["enabled"] or catalog["source"] != MODEL_CATALOG_SOURCE:
        return None

    if not _is_arena_direct_url(current_url, catalog_preset=preset):
        return None

    return {
        "preset_name": effective_preset_name,
        "preset": preset,
        "catalog": catalog,
    }


def _is_arena_direct_url(value: Any, catalog_preset: Optional[Dict[str, Any]] = None) -> bool:
    current_url = str(value or "").strip()
    if not current_url or current_url.lower() in {"about:blank", "javascript:void(0)"}:
        return False

    try:
        actual_domain = extract_remote_site_domain(current_url) or ""
        if not route_domain_matches("arena.ai", actual_domain):
            return False
        path = str(urlparse(current_url).path or "").rstrip("/").lower()
    except Exception:
        return False

    if path in {"/direct", "/text/direct", "/image/direct", "/code/direct"} or path.startswith((
        "/direct/",
        "/text/direct/",
        "/image/direct/",
        "/code/direct/",
    )) or path.endswith("/direct"):
        return True

    if path == "/c" or path.startswith("/c/"):
        if catalog_preset is not None:
            catalog = normalize_model_catalog_config(catalog_preset.get("model_catalog"))
            return bool(catalog.get("enabled") and catalog.get("source") == MODEL_CATALOG_SOURCE)
        return True

    return False


def _filter_models(
    models: List[Dict[str, Any]],
    catalog_config: Any,
) -> List[Dict[str, Any]]:
    catalog = normalize_model_catalog_config(catalog_config)
    target_modality = catalog.get("modality", "")
    includes = [item.casefold() for item in catalog["include_keywords"]]
    excludes = [item.casefold() for item in catalog["exclude_keywords"]]
    result = []
    for model in models:
        search_str = " ".join(
            str(model.get(k) or "") for k in ("name", "public_name", "display_name", "search_name")
        ).lower()
        if any(
            k in search_str
            for k in (
                "video",
                "seedance",
                "dreamina",
                "sora",
                "veo",
                "gemini-omni",
                "kling",
                "hailuo",
                "runway",
                "pika",
                "mochi",
                "ltx",
                "vidu",
                "luma",
                "wan2",
                "wan-2",
                "wan_2",
                "wanx",
                "pixverse",
                "kandinsky",
                "happyhorse",
            )
        ):
            continue

        if target_modality:
            model_modality = str(model.get("modality") or "").strip().lower()
            if model_modality in {"image", "text"}:
                model_is_image = (model_modality == "image")
            else:
                model_is_image = any(
                    k in search_str
                    for k in (
                        "image",
                        "mona-lisa",
                        "luna-lisa",
                        "lina-alpha",
                        "lina-f-alpha",
                        "lina",
                        "silver_halide",
                        "flux",
                        "imagine",
                        "seedream",
                        "seededit",
                    )
                )
            if target_modality == "image" and not model_is_image:
                continue
            if target_modality == "text" and model_is_image:
                continue
        searchable = " ".join(
            str(model.get(key) or "")
            for key in (
                "name",
                "public_name",
                "display_name",
                "search_name",
                "provider",
                "organization",
            )
        ).casefold()
        searchable += " " + " ".join(
            str(alias or "") for alias in (model.get("aliases") or [])
        ).casefold()
        if includes and not any(keyword in searchable for keyword in includes):
            continue
        if excludes and any(keyword in searchable for keyword in excludes):
            continue
        result.append(model)
    result.sort(key=lambda m: _natural_sort_key(get_arena_direct_model_public_id(m)))
    return result


def _natural_sort_key(text: Any) -> list:
    value = str(text or "").strip()
    return [int(part) if part.isdecimal() else part.casefold() for part in re.split(r"(\d+)", value)]


def _normalize_models(raw_models: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_models, list):
        return []

    normalized: List[Dict[str, Any]] = []
    alias_overrides = _load_alias_overrides()
    seen_ids = set()
    seen_names = set()
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        arena_model_id = str(raw.get("arena_model_id") or "").strip()
        name = str(raw.get("name") or raw.get("display_name") or raw.get("public_name") or "").strip()
        if not arena_model_id or not name:
            continue
        id_key = arena_model_id.lower()
        name_key = name.lower()
        if id_key in seen_ids or name_key in seen_names:
            continue
        seen_ids.add(id_key)
        seen_names.add(name_key)
        public_name = str(raw.get("public_name") or raw.get("display_name") or name).strip()
        display_name = str(raw.get("display_name") or public_name or name).strip()
        override = (
            alias_overrides.get(name.casefold())
            or alias_overrides.get(display_name.casefold())
            or alias_overrides.get(public_name.casefold())
        )
        if not isinstance(override, dict):
            override = {}
        search_name = str(
            override.get("search_name") or display_name or public_name or name
        ).strip()
        aliases = []
        for alias in (
            name,
            public_name,
            display_name,
            search_name,
            *(override.get("aliases") or [] if isinstance(override.get("aliases"), list) else []),
        ):
            alias_text = str(alias or "").strip()
            if alias_text and alias_text.casefold() not in {item.casefold() for item in aliases}:
                aliases.append(alias_text)

        for candidate_name in (display_name, public_name, search_name, name):
            if not candidate_name:
                continue
            if "(" in candidate_name:
                base_no_paren = candidate_name.split("(")[0].strip()
                if base_no_paren and base_no_paren.casefold() not in {item.casefold() for item in aliases}:
                    aliases.append(base_no_paren)
            if candidate_name.endswith("-high"):
                base_no_high = candidate_name[:-5].strip()
                if base_no_high and base_no_high.casefold() not in {item.casefold() for item in aliases}:
                    aliases.append(base_no_high)
            if candidate_name.endswith("-preview"):
                base_no_prev = candidate_name[:-8].strip()
                if base_no_prev and base_no_prev.casefold() not in {item.casefold() for item in aliases}:
                    aliases.append(base_no_prev)
        raw_modality = str(raw.get("modality") or "").strip().lower()
        normalized.append(
            {
                "arena_model_id": arena_model_id,
                "name": name,
                "public_name": public_name or name,
                "display_name": display_name or public_name or name,
                "search_name": search_name or display_name or public_name or name,
                "aliases": aliases,
                "provider": str(raw.get("provider") or "").strip(),
                "organization": str(raw.get("organization") or raw.get("provider") or "arena.ai").strip(),
                "modality": raw_modality or "text",
            }
        )
    return normalized


def _load_alias_overrides() -> Dict[str, Dict[str, Any]]:
    try:
        payload = json.loads(ARENA_MODEL_ALIAS_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return {}
    models = payload.get("models") if isinstance(payload, dict) else None
    return models if isinstance(models, dict) else {}


def read_arena_direct_models_from_tab(tab: Any) -> List[Dict[str, Any]]:
    if tab is None:
        return []
    try:
        try:
            return _normalize_models(tab.run_js(_ARENA_DIRECT_MODEL_EXTRACT_JS, timeout=3.0))
        except TypeError:
            return _normalize_models(tab.run_js(_ARENA_DIRECT_MODEL_EXTRACT_JS))
    except Exception as e:
        logger.debug(f"从标签页读取 Arena 直连模型失败（已优雅降级）: {e}")
        return []


def _cache_snapshot() -> tuple[float, List[Dict[str, Any]]]:
    with _cache_lock:
        return _cached_at, copy.deepcopy(_cached_models)


def _replace_cache(models: Any) -> List[Dict[str, Any]]:
    global _cached_at, _cached_models
    normalized = _normalize_models(models)
    if not normalized:
        return []
    with _cache_lock:
        _cached_at = time.monotonic()
        _cached_models = copy.deepcopy(normalized)
    return copy.deepcopy(normalized)


def _session_is_idle(session: Any) -> bool:
    status = str(getattr(getattr(session, "status", None), "value", "") or "").strip().lower()
    return status in {"", "idle"}


def _arena_sessions(browser: Any) -> List[Any]:
    try:
        sessions = browser.tab_pool.get_sessions_snapshot()
    except Exception:
        return []

    from app.services.config_engine import config_engine

    result = []
    for session in sessions or []:
        status = str(getattr(getattr(session, "status", None), "value", "") or "").strip().lower()
        if status not in {"idle", "busy"}:
            continue
        try:
            current_url, _domain = session.get_cached_route_snapshot()
        except Exception:
            current_url = str(getattr(getattr(session, "tab", None), "url", "") or "")

        tab_dict = {
            "status": status,
            "url": current_url,
            "preset_name": getattr(session, "preset_name", None),
            "terminating": False,
        }
        if not get_arena_direct_catalog_for_tab(config_engine, tab_dict):
            continue
        result.append(session)
    result.sort(key=lambda item: (not _session_is_idle(item), int(getattr(item, "persistent_index", 0) or 0)))
    return result


def list_arena_direct_models(
    browser: Any,
    *,
    force: bool = False,
    catalog_config: Any = None,
) -> List[Dict[str, Any]]:
    sessions = _arena_sessions(browser)
    if not sessions:
        return []

    cached_at, cached = _cache_snapshot()
    if cached and not force and time.monotonic() - cached_at < ARENA_DIRECT_MODEL_CACHE_TTL:
        return _filter_models(cached, catalog_config)

    if not _refresh_lock.acquire(blocking=False):
        return _filter_models(cached, catalog_config)
    try:
        cached_at, cached = _cache_snapshot()
        if cached and not force and time.monotonic() - cached_at < ARENA_DIRECT_MODEL_CACHE_TTL:
            return _filter_models(cached, catalog_config)

        for session in sessions:
            if not _session_is_idle(session):
                continue
            try:
                models = read_arena_direct_models_from_tab(session.tab)
            except Exception as exc:
                logger.debug(f"Arena Direct 模型目录读取失败（尝试下一标签页）: {exc}")
                continue
            if models:
                logger.info(f"Arena Direct 模型目录已刷新: {len(models)} 个文本模型")
                return _filter_models(_replace_cache(models), catalog_config)
        return _filter_models(cached, catalog_config)
    finally:
        _refresh_lock.release()


def resolve_arena_direct_model(
    tab: Any,
    requested_model: Any,
    *,
    catalog_config: Any = None,
) -> Optional[Dict[str, Any]]:
    requested_value = str(requested_model or "").strip()
    if not requested_value:
        return None

    _cached_at_value, cached = _cache_snapshot()
    matched = match_arena_direct_model(_filter_models(cached, catalog_config), requested_value)
    if matched:
        return matched

    if tab is None:
        try:
            from app.core.browser import get_browser
            browser = get_browser(auto_connect=False)
            if browser:
                models = list_arena_direct_models(browser, catalog_config=catalog_config)
                if models:
                    return match_arena_direct_model(_filter_models(models, catalog_config), requested_value)
        except Exception:
            pass
    else:
        models = read_arena_direct_models_from_tab(tab)
        if models:
            _replace_cache(models)
            return match_arena_direct_model(_filter_models(models, catalog_config), requested_value)

    return None


def build_openai_model_entries(models: List[Dict[str, Any]], *, created: int) -> List[Dict[str, Any]]:
    entries = []
    seen_ids = set()
    for model in models or []:
        if not isinstance(model, dict):
            continue
        model_id = get_arena_direct_model_public_id(model)
        normalized_id = model_id.casefold()
        if not model_id or normalized_id in seen_ids:
            continue
        seen_ids.add(normalized_id)
        entries.append(
            {
                "id": model_id,
                "object": "model",
                "type": "model",
                "created": created,
                "owned_by": model.get("organization") or model.get("provider") or "arena.ai",
                "display_name": model.get("display_name") or model.get("public_name") or model_id,
            }
        )
    entries.sort(key=lambda e: _natural_sort_key(str(e.get("id") or "")))
    return entries


__all__ = [
    "ARENA_DIRECT_MODEL_PREFIX",
    "build_arena_direct_model_id",
    "build_openai_model_entries",
    "get_arena_direct_catalog_for_tab",
    "get_model_catalog_preset",
    "get_arena_direct_model_public_id",
    "is_arena_direct_model_id",
    "list_arena_direct_models",
    "match_arena_direct_model",
    "normalize_model_catalog_config",
    "parse_arena_direct_model_id",
    "read_arena_direct_models_from_tab",
    "resolve_arena_direct_model",
    "resolve_model_catalog_preset_for_model",
]
