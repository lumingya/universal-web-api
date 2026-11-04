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
from app.core.driver import driver_for_tab
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
    const pathname = (typeof window !== 'undefined' && window.location && window.location.pathname) ? String(window.location.pathname).toLowerCase() : '';
    const isImagePage = pathname.includes('/image');
    const isCodePage = pathname.includes('/code');
    const defaultPageModality = isImagePage ? 'image' : (isCodePage ? 'code' : 'text');

    // 1. 优先从 React Fiber 提取前端真实加载的可用模型
    const all = Array.from(document.querySelectorAll('*'));
    let fiberModels = null;
    for (const el of all) {
        const fiberKey = Object.keys(el).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'));
        if (!fiberKey) continue;
        let cur = el[fiberKey];
        let d = 0;
        while (cur && d < 25) {
            if (cur.memoizedProps) {
                for (const [k, v] of Object.entries(cur.memoizedProps)) {
                    if (Array.isArray(v) && v.length >= 30 && v.length <= 300 && v[0] && (v[0].id && (v[0].displayName || v[0].name || v[0].publicName))) {
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
        const ranks = model.rankByModality || {};
        const rankIsFinite = (key) => (
            typeof ranks[key] === 'number' &&
            Number.isFinite(ranks[key]) &&
            ranks[key] < Number.MAX_SAFE_INTEGER
        );
        const hasText = Boolean(outCaps.text);
        const hasImage = Boolean(outCaps.image) || rankIsFinite('image');
        const hasVideo = Boolean(outCaps.video) || rankIsFinite('video');
        // `outputCapabilities.web` means the model can browse/use web data;
        // it is not the WebDev/code modality.  Only the modality rank marks
        // a model as code-capable for catalog filtering.
        const hasWebDev = rankIsFinite('webdev') || Boolean(outCaps.web);
        const hasSearch = Boolean(outCaps.search) || rankIsFinite('search');

        const dispName = String(model.displayName || model.publicName || model.name || idStr).trim();
        const internalName = String(model.name || model.id || dispName).trim();
        const pubName = String(model.publicName || dispName || internalName).trim();
        const nameSearch = `${internalName} ${dispName} ${pubName} ${idStr}`.toLowerCase();

        const isKnownImageModel = nameSearch.includes('gpt-image') ||
                                  nameSearch.includes('mona-lisa') ||
                                  nameSearch.includes('luna-lisa') ||
                                  nameSearch.includes('lina-alpha') ||
                                  nameSearch.includes('lina-f-alpha') ||
                                  nameSearch.includes('silver_halide') ||
                                  nameSearch.includes('flux') ||
                                  nameSearch.includes('imagen') ||
                                  nameSearch.includes('seedream') ||
                                  nameSearch.includes('seededit') ||
                                  nameSearch.includes('z-image') ||
                                  nameSearch.includes('midjourney') ||
                                  nameSearch.includes('dall-e') ||
                                  nameSearch.includes('recraft') ||
                                  nameSearch.includes('krea') ||
                                  (nameSearch.includes('grok') && nameSearch.includes('image')) ||
                                  (nameSearch.includes('imagine') && !nameSearch.includes('video'));

        let modality = defaultModality === 'image' ? 'image' : (defaultModality === 'code' ? 'code' : 'text');
        if (hasVideo && !hasText && !isKnownImageModel) {
            modality = 'video';
        } else if (isKnownImageModel || (hasImage && defaultModality === 'image') || (hasImage && !hasText)) {
            modality = 'image';
        } else if (defaultModality === 'code' || (hasWebDev && !hasText)) {
            modality = 'code';
        } else if (hasText) {
            modality = 'text';
        } else if (hasSearch) {
            modality = 'search';
        } else if (hasWebDev) {
            modality = 'code';
        } else if (hasImage) {
            modality = 'image';
        } else if (hasVideo) {
            modality = 'video';
        }

        results.push({
            arena_model_id: idStr,
            name: internalName,
            public_name: dispName,
            display_name: dispName,
            provider: String(model.provider || ''),
            organization: String(model.organization || model.provider || 'arena.ai'),
            modality
        });
    };

    const isModelAvailable = (model) => {
        if (!model || model.userSelectable === false) return false;
        const ranks = model.rankByModality || {};
        const rankValues = Object.values(ranks);
        const hasVisibleRank = rankValues.some((rank) => (
            typeof rank === 'number' &&
            Number.isFinite(rank) &&
            rank < Number.MAX_SAFE_INTEGER
        ));
        const hasPublicRank = typeof model.rank === 'number' &&
            Number.isFinite(model.rank) && model.rank < 1000;
        const hasNoAvailabilityMetadata = rankValues.length === 0 &&
            typeof model.rank === 'undefined';
        // Arena can keep a user-selectable model at MAX_SAFE_INTEGER for a
        // modality while still exposing that modality as a valid capability.
        // userSelectable is the authoritative visibility signal in that case.
        const hasCapabilityMetadata = Object.values(
            model.capabilities?.outputCapabilities || model.outputCapabilities || {}
        ).some(Boolean);
        return hasVisibleRank || hasPublicRank || hasNoAvailabilityMetadata ||
            (model.userSelectable === true && hasCapabilityMetadata);
    };

    if (fiberModels && fiberModels.length > 0) {
        for (const m of fiberModels) {
            if (isModelAvailable(m)) appendModel(m, defaultPageModality);
        }
    } else {
        // 2. 仅在未挂载 React Fiber 时才从 SSR initialModels 提取保底
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
                    const isUserSpecifiedImage = nameLower.includes('gpt-image') ||
                                                 nameLower.includes('mona-lisa') ||
                                                 nameLower.includes('luna-lisa') ||
                                                 nameLower.includes('lina-alpha') ||
                                                 nameLower.includes('lina-f-alpha') ||
                                                 nameLower.includes('silver_halide') ||
                                                 nameLower.includes('flux') ||
                                                 nameLower.includes('imagen') ||
                                                 nameLower.includes('seedream') ||
                                                 nameLower.includes('seededit') ||
                                                 nameLower.includes('z-image') ||
                                                 nameLower.includes('midjourney') ||
                                                 nameLower.includes('dall-e') ||
                                                 nameLower.includes('recraft') ||
                                                 nameLower.includes('krea') ||
                                                 nameLower.includes('grok-imagine') ||
                                                 (nameLower.includes('grok') && nameLower.includes('image'));
                    if (isUserSpecifiedImage && isModelAvailable(model)) {
                        appendModel(model, 'image');
                    } else if (isModelAvailable(model)) {
                        appendModel(model, defaultPageModality);
                    }
                }
            } catch (_) {}
        }
    }

    return results;
})();
"""


_cache_lock = threading.RLock()
_refresh_lock = threading.Lock()
_cached_at = 0.0
_cached_models: List[Dict[str, Any]] = []


def _canonical_modality(value: Any) -> str:
    """Normalize Arena's page/API terminology to the catalog terminology."""
    modality = str(value or "").strip().lower()
    return {
        "webdev": "code",
        "web": "code",
        "chat": "text",
    }.get(modality, modality)


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
    from app.services.arena_model_catalog import normalize_arena_model_catalog_config
    return normalize_arena_model_catalog_config(value)


def get_model_catalog_preset(config_engine: Any, domain: Any) -> Optional[Dict[str, Any]]:
    from app.services.arena_model_catalog import get_model_catalog_preset as _get_preset
    return _get_preset(config_engine, domain)


def get_arena_direct_catalog_for_tab(
    config_engine: Any,
    tab: Any,
    *,
    preset_name: Any = None,
) -> Optional[Dict[str, Any]]:
    from app.services.arena_model_catalog import get_arena_direct_catalog_for_tab as _get_tab_catalog
    return _get_tab_catalog(config_engine, tab, preset_name=preset_name)


def _is_arena_direct_url(
    value: Any,
    catalog_preset: Optional[Dict[str, Any]] = None,
    catalog_config: Optional[Dict[str, Any]] = None,
) -> bool:
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
        catalog = catalog_config
        if catalog is None and catalog_preset is not None:
            catalog = normalize_model_catalog_config(catalog_preset.get("model_catalog"))
        if catalog is not None:
            return bool(catalog.get("enabled") and catalog.get("source") == MODEL_CATALOG_SOURCE)
        return True

    return False


def _filter_models(
    models: List[Dict[str, Any]],
    catalog_config: Any,
) -> List[Dict[str, Any]]:
    from app.services.arena_model_catalog import filter_arena_catalog_models
    return filter_arena_catalog_models(models, catalog_config)


def _natural_sort_key(text: Any) -> list:
    value = str(text or "").strip()
    return [int(part) if part.isdecimal() else part.casefold() for part in re.split(r"(\d+)", value)]


# 名字里出现这些关键词、且没有显式 modality 时判定为生图模型。
_MODEL_IMAGE_KEYWORDS = (
    "gpt-image",
    "mona-lisa",
    "luna-lisa",
    "lina-alpha",
    "lina-f-alpha",
    "silver_halide",
    "flux",
    "seedream",
    "seededit",
    "imagen",
    "z-image",
    "midjourney",
    "dall-e",
    "recraft",
    "krea",
    "grok-imagine",
)


def _model_identity(raw: Dict[str, Any]):
    """取出 (arena_model_id, name)；任一为空表示该条目无效，返回 (None, None)。

    name 的回退链是 name -> display_name -> public_name。
    """
    arena_model_id = str(raw.get("arena_model_id") or "").strip()
    name = str(raw.get("name") or raw.get("display_name") or raw.get("public_name") or "").strip()
    if not arena_model_id or not name:
        return None, None
    return arena_model_id, name


def _first_non_empty(*values) -> str:
    """取第一个为真的值并 strip。"""
    for value in values:
        if value:
            return str(value).strip()
    return ""


def _model_override_for(alias_overrides, name: str, display_name: str, public_name: str) -> Dict[str, Any]:
    """按 name -> display_name -> public_name 的顺序查别名覆盖，查不到返回 {}。"""
    override = (
        alias_overrides.get(name.casefold())
        or alias_overrides.get(display_name.casefold())
        or alias_overrides.get(public_name.casefold())
    )
    return override if isinstance(override, dict) else {}


def _add_alias_once(aliases: List[str], text: str) -> None:
    """去空白后非空、且大小写不重复才追加。"""
    alias_text = str(text or "").strip()
    if alias_text and alias_text.casefold() not in {item.casefold() for item in aliases}:
        aliases.append(alias_text)


def _model_aliases(
    name: str,
    public_name: str,
    display_name: str,
    search_name: str,
    override: Dict[str, Any],
) -> List[str]:
    """收集别名：四个名字 + 覆盖配置里的额外别名，再加上三种派生形式。

    派生对 (display_name, public_name, search_name, name) 依次做：
    取括号前的部分、去掉 `-high` 后缀、去掉 `-preview` 后缀。
    """
    aliases: List[str] = []
    extra = override.get("aliases")
    base_candidates = (name, public_name, display_name, search_name)
    if isinstance(extra, list):
        base_candidates = (*base_candidates, *extra)
    for alias in base_candidates:
        _add_alias_once(aliases, alias)

    for candidate_name in (display_name, public_name, search_name, name):
        if not candidate_name:
            continue
        if "(" in candidate_name:
            _add_alias_once(aliases, candidate_name.split("(")[0].strip())
        if candidate_name.endswith("-high"):
            _add_alias_once(aliases, candidate_name[:-5].strip())
        if candidate_name.endswith("-preview"):
            _add_alias_once(aliases, candidate_name[:-8].strip())
    return aliases


def _model_modality(raw: Dict[str, Any], name: str, display_name: str, public_name: str, arena_model_id: str) -> str:
    """判定模态：优先用显式值（经规范化），否则按名字关键词兜底，最后取 text。"""
    raw_modality = _canonical_modality(raw.get("modality"))
    if raw_modality:
        return raw_modality
    name_check = f"{name} {display_name} {public_name} {arena_model_id}".lower()
    if any(keyword in name_check for keyword in _MODEL_IMAGE_KEYWORDS):
        return "image"
    return "text"


def _build_model_record(
    raw: Dict[str, Any],
    arena_model_id: str,
    name: str,
    alias_overrides: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """组装一条规范化后的模型记录。"""
    public_name = _first_non_empty(raw.get("public_name"), raw.get("display_name"), name)
    display_name = _first_non_empty(raw.get("display_name"), public_name, name)
    override = _model_override_for(alias_overrides, name, display_name, public_name)
    search_name = _first_non_empty(override.get("search_name"), display_name, public_name, name)
    return {
        "arena_model_id": arena_model_id,
        "name": name,
        "public_name": public_name or name,
        "display_name": _first_non_empty(display_name, public_name, name),
        "search_name": _first_non_empty(search_name, display_name, public_name, name),
        "aliases": _model_aliases(name, public_name, display_name, search_name, override),
        "provider": _first_non_empty(raw.get("provider")),
        "organization": _first_non_empty(
            raw.get("organization"), raw.get("provider"), "arena.ai"
        ),
        "modality": _model_modality(raw, name, display_name, public_name, arena_model_id),
    }


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
        arena_model_id, name = _model_identity(raw)
        if arena_model_id is None:
            continue
        id_key = arena_model_id.lower()
        name_key = name.lower()
        if id_key in seen_ids or name_key in seen_names:
            continue
        seen_ids.add(id_key)
        seen_names.add(name_key)
        normalized.append(_build_model_record(raw, arena_model_id, name, alias_overrides))
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
            return _normalize_models(driver_for_tab(tab).run_js(_ARENA_DIRECT_MODEL_EXTRACT_JS, timeout=3.0))
        except TypeError:
            return _normalize_models(driver_for_tab(tab).run_js(_ARENA_DIRECT_MODEL_EXTRACT_JS))
    except Exception as e:
        logger.debug(f"从标签页读取 Arena 直连模型失败（已优雅降级）: {e}")
        return []


_session_cached_models: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}


def _get_session_cache_key(session_or_tab: Any) -> str:
    if session_or_tab is None:
        return ""
    if isinstance(session_or_tab, dict):
        idx = session_or_tab.get("persistent_index")
        tid = session_or_tab.get("tab_id")
        return f"idx_{idx}" if idx is not None else (f"id_{tid}" if tid else "")
    idx = getattr(session_or_tab, "persistent_index", None)
    sid = getattr(session_or_tab, "id", None)
    return f"idx_{idx}" if idx is not None else (f"id_{sid}" if sid else "")


def _session_cache_snapshot(key: str) -> tuple[float, List[Dict[str, Any]]]:
    if not key:
        return 0.0, []
    with _cache_lock:
        if key in _session_cached_models:
            ts, models = _session_cached_models[key]
            return ts, copy.deepcopy(models)
    return 0.0, []


def _replace_session_cache(key: str, models: Any) -> List[Dict[str, Any]]:
    normalized = _normalize_models(models)
    if not key:
        return normalized
    with _cache_lock:
        _session_cached_models[key] = (time.monotonic(), copy.deepcopy(normalized))
    return copy.deepcopy(normalized)


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
    raw_status = getattr(session, "status", None)
    status = str(getattr(raw_status, "value", None) or raw_status or "").strip().lower()
    return status in {"", "idle"}


def _arena_sessions(browser: Any) -> List[Any]:
    try:
        sessions = browser.tab_pool.get_sessions_snapshot()
    except Exception:
        return []

    from app.services.config_engine import config_engine

    result = []
    for session in sessions or []:
        raw_status = getattr(session, "status", None)
        status = str(getattr(raw_status, "value", None) or raw_status or "").strip().lower()
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
    tab: Any = None,
) -> List[Dict[str, Any]]:
    # 指定单标签页时执行严格标签页隔离读取与缓存
    if tab is not None:
        target_session = None
        target_tab_obj = None
        if hasattr(tab, "tab"):
            target_session = tab
            target_tab_obj = getattr(tab, "tab", None)
        elif hasattr(tab, "run_js"):
            target_tab_obj = tab
        elif isinstance(tab, dict):
            target_tab_obj = tab.get("tab")
            if browser and hasattr(browser, "tab_pool"):
                try:
                    p_idx = tab.get("persistent_index")
                    t_id = tab.get("tab_id")
                    sessions = browser.tab_pool.get_sessions_snapshot()
                    for s in sessions or []:
                        if p_idx is not None and getattr(s, "persistent_index", None) == p_idx:
                            target_session = s
                            target_tab_obj = getattr(s, "tab", None)
                            break
                        if t_id and getattr(s, "id", None) == t_id:
                            target_session = s
                            target_tab_obj = getattr(s, "tab", None)
                            break
                except Exception:
                    pass

        key = _get_session_cache_key(target_session or tab)
        cached_at, cached = _session_cache_snapshot(key)
        if cached and not force and time.monotonic() - cached_at < ARENA_DIRECT_MODEL_CACHE_TTL:
            filtered = _filter_models(cached, catalog_config)
            if filtered:
                return filtered

        if target_tab_obj is not None:
            if target_session is None or _session_is_idle(target_session):
                try:
                    models = read_arena_direct_models_from_tab(target_tab_obj)
                    if models:
                        _replace_session_cache(key, models)
                        return _filter_models(models, catalog_config)
                except Exception as exc:
                    logger.debug(f"从目标标签页读取 Arena 直连模型失败: {exc}")

        if cached:
            return _filter_models(cached, catalog_config)
        return []

    sessions = _arena_sessions(browser)
    if not sessions:
        return []

    cached_at, cached = _cache_snapshot()
    if cached and not force and time.monotonic() - cached_at < ARENA_DIRECT_MODEL_CACHE_TTL:
        filtered = _filter_models(cached, catalog_config)
        if filtered:
            return filtered

    if not _refresh_lock.acquire(blocking=False):
        return _filter_models(cached, catalog_config)
    try:
        cached_at, cached = _cache_snapshot()
        if cached and not force and time.monotonic() - cached_at < ARENA_DIRECT_MODEL_CACHE_TTL:
            filtered = _filter_models(cached, catalog_config)
            if filtered:
                return filtered

        all_extracted_models: List[Dict[str, Any]] = []
        for session in sessions:
            if not _session_is_idle(session):
                continue
            try:
                models = read_arena_direct_models_from_tab(session.tab)
                if models:
                    s_key = _get_session_cache_key(session)
                    _replace_session_cache(s_key, models)
                    all_extracted_models.extend(models)
            except Exception as exc:
                logger.debug(f"Arena Direct 模型目录读取失败（尝试下一标签页）: {exc}")
                continue

        if all_extracted_models:
            merged_models = list(cached) + all_extracted_models
            updated = _replace_cache(merged_models)
            logger.info(f"Arena Direct 模型目录已刷新: {len(updated)} 个模型")
            return _filter_models(updated, catalog_config)
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
            _cached_at_val, current_cached = _cache_snapshot()
            merged = list(current_cached) + list(models)
            updated = _replace_cache(merged)
            return match_arena_direct_model(_filter_models(updated, catalog_config), requested_value)

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
]
