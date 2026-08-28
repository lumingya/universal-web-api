"""Arena direct model catalog persistence, migration, and dark pool filtering."""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.config import logger
from app.utils.site_url import extract_remote_site_domain, route_domain_matches


ARENA_MODEL_CATALOG_CONFIG_PATH = Path(
    os.getenv(
        "ARENA_MODEL_CATALOG_CONFIG_PATH",
        str(Path(__file__).resolve().parents[2] / "config" / "arena_model_catalog.local.json"),
    )
)

ARENA_MODELS_CACHE_PATH = Path(
    os.getenv(
        "ARENA_MODELS_CACHE_PATH",
        str(Path(__file__).resolve().parents[2] / "scripts" / "arena_models_cache.json"),
    )
)

MODEL_CATALOG_SOURCE = "arena_direct"

ONLINE_PUBLIC_VISIBLE_MODELS: Set[str] = {
    "gemini-3-flash", "glm-5.1", "qwen3.5-397b-a17b", "claude-sonnet-4-5-20250929",
    "gemini-3.1-pro-preview", "qwen3.7-plus", "minimax-m3", "claude-haiku-4-5-20251001",
    "gemini-2.5-pro", "glm-5v-turbo", "grok-4.20-beta-0309-reasoning", "gpt-5.2-high",
    "gpt-5.5-instant", "gpt-5.1", "gpt-5.2", "gemini-3.6-flash", "claude-sonnet-4-6",
    "grok-4.20-multi-agent-beta-0309", "qwen3.5-max-preview", "gemini-3.5-flash-lite",
    "glm-5", "claude-sonnet-4-5-20250929-thinking-32k", "gpt-5.1-high", "gpt-5.4-mini-high",
    "glm-4.7", "qwen3-max-preview", "gpt-5-high", "kimi-k2.5-instant", "o3-2025-04-16",
    "kimi-k2-thinking-turbo", "gpt-5-chat", "qwen3-max-2025-09-23", "qwen3-235b-a22b-instruct-2507",
    "kimi-k2-0711-preview", "kimi-k2-0905-preview", "qwen3.5-122b-a10b", "minimax-m2.7",
    "qwen3-vl-235b-a22b-instruct", "mistral-large-3", "gpt-4.1-2025-04-14", "gemini-2.5-flash",
    "mistral-medium-2508", "qwen3.5-27b", "inkling-small", "qwen3-235b-a22b-no-thinking",
    "gpt-5.4-nano-high", "longcat-flash-chat", "qwen3-next-80b-a3b-instruct",
    "claude-sonnet-4-20250514-thinking-32k", "qwen3-235b-a22b-thinking-2507", "qwen3.5-flash",
    "qwen3.5-35b-a3b", "hunyuan-vision-1.5-thinking", "qwen3-vl-235b-a22b-thinking",
    "step-3.5-flash", "minimax-m2.5", "o4-mini-2025-04-16", "gpt-5-mini-high",
    "claude-sonnet-4-20250514", "qwen3-coder-480b-a35b-instruct", "minimax-m2.1-preview",
    "qwen3-30b-a3b-instruct-2507", "gpt-4.1-mini-2025-04-14", "trinity-large-preview",
    "qwen3-235b-a22b", "trinity-large-thinking", "qwen3-next-80b-a3b-thinking",
    "gemma-3-27b-it", "minimax-m1", "gemini-2.0-flash-001", "intellect-3",
    "gemma-3-12b-it", "o3-mini-high", "gemma-3-4b-it", "mistral-small-3.2",
    "qwen3-vl-30b-a3b-instruct", "gemini-2.5-flash-lite", "qwen3-30b-a3b-thinking-2507",
    "qwen3-30b-a3b", "deepseek-v3.2", "gemma-3-1b-it", "deepseek-v4-flash",
    "o3-mini-2025-01-31", "glm-4-flash", "gemini-2.0-flash-thinking-exp-01-21",
    "qwen2.5-max-preview", "claude-3.5-haiku-20241022", "claude-3.7-sonnet",
    "step-2-16k-exp", "claude-3.7-sonnet-thinking", "deepseek-r1-distill-qwen-32b",
    "deepseek-v3.1", "deepseek-v4", "claude-3.5-sonnet-20241022", "gpt-4o-2024-11-20",
    "deepseek-v4-pro-max", "claude-sonnet-5-high", "gpt-5.4-mini", "gemini-3.7-pro",
    "gemini-3.7-pro-high", "glm-5.2 (max)", "deepseek-v4-flash-20260731",
    "gpt-5.4-high", "claude-sonnet-5-search", "gemini-3.5-pro-high",
    "gemini-3.5-flash-high", "deepseek-v4-flash-high", "gemini-3.5-flash",
    "gpt-5.4-turbo", "gpt-5.4", "gemini-3.5-pro", "gemini-2.5-pro-high",
    "gemini-3-flash-high", "gemini-3-pro-high", "gemini-3-flash-search",
    "gemini-2.5-ultra", "gemini-3-pro", "gemini-2.5-flash-thinking",
    "gemini-2.5-flash-high", "gemini-3.7-flash-high", "qwen3.5-122b-a10b-thinking",
    "qwen3.5-35b-a3b-thinking", "claude-opus-4.5", "claude-opus-4.5-thinking",
    "claude-opus-4.6", "claude-opus-4.6-thinking", "claude-opus-4.1",
    "claude-opus-4.1-thinking", "claude-opus-4", "claude-opus-4-thinking",
    "o4-mini-high", "o4-mini", "o4-max", "o4-high", "o3-pro-max", "o3-pro",
    "gpt-5.3-high", "gpt-5.3-mini", "gpt-5.3", "gpt-5.2-mini", "gpt-5.2-turbo",
    "gpt-5.1-turbo", "gpt-5.1-mini", "gpt-5.1-codex-max", "gpt-5-turbo", "gpt-5-mini",
    "inkling-low", "qwen3-omni-flash", "inkling-small-low", "inkling-medium",
    "gemini-3.7-flash", "mistral-medium-3.5", "gemini-3-flash (thinking-minimal)",
    "qwen3.7-plus-preview", "grok-4.6-medium", "grok-4.5"
}

_catalog_io_lock = threading.RLock()


def _canonical_modality(value: Any) -> str:
    """Normalize Arena's page/API terminology to the catalog terminology."""
    modality = str(value or "").strip().lower()
    return {
        "webdev": "code",
        "web": "code",
        "chat": "text",
    }.get(modality, modality)


def _keywords_list(source: Any) -> List[str]:
    if isinstance(source, str):
        source = re.split(r"[\n,]+", source)
    if not isinstance(source, (list, tuple, set)):
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


def normalize_arena_model_catalog_config(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}

    result = {
        "enabled": bool(raw.get("enabled", False)),
        "source": str(raw.get("source") or MODEL_CATALOG_SOURCE).strip() or MODEL_CATALOG_SOURCE,
        "include_keywords": _keywords_list(raw.get("include_keywords")),
        "exclude_keywords": _keywords_list(raw.get("exclude_keywords")),
        "enable_dark_pool": bool(raw.get("enable_dark_pool", False)),
        "dark_pool_since": str(raw.get("dark_pool_since") or "").strip(),
        "dark_pool_whitelist_keywords": _keywords_list(raw.get("dark_pool_whitelist_keywords")),
        "dark_pool_blacklist_keywords": _keywords_list(raw.get("dark_pool_blacklist_keywords")),
    }
    if "modality" in raw and str(raw.get("modality") or "").strip():
        result["modality"] = _canonical_modality(raw.get("modality"))
    return result


def load_arena_model_catalog_data() -> Dict[str, Dict[str, Any]]:
    """加载独立配置文件，返回 { domain: { preset_name: catalog_config } }"""
    with _catalog_io_lock:
        if not ARENA_MODEL_CATALOG_CONFIG_PATH.exists():
            return {}
        try:
            content = ARENA_MODEL_CATALOG_CONFIG_PATH.read_text(encoding="utf-8").strip()
            if not content:
                return {}
            data = json.loads(content)
            if not isinstance(data, dict):
                return {}
            normalized: Dict[str, Dict[str, Any]] = {}
            for domain, presets in data.items():
                if not isinstance(presets, dict):
                    continue
                d_key = str(domain).strip().lower()
                normalized[d_key] = {}
                for preset_name, cat in presets.items():
                    p_key = str(preset_name).strip()
                    if isinstance(cat, dict):
                        normalized[d_key][p_key] = normalize_arena_model_catalog_config(cat)
            return normalized
        except Exception as e:
            logger.warning(f"读取 Arena 独立模型目录配置失败: {e}")
            return {}


def save_arena_model_catalog_data(data: Dict[str, Any]) -> bool:
    """原子保存独立配置文件"""
    with _catalog_io_lock:
        try:
            ARENA_MODEL_CATALOG_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = ARENA_MODEL_CATALOG_CONFIG_PATH.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, ARENA_MODEL_CATALOG_CONFIG_PATH)
            logger.info(f"Arena 独立模型目录配置已保存: {ARENA_MODEL_CATALOG_CONFIG_PATH}")
            return True
        except Exception as e:
            logger.error(f"保存 Arena 独立模型目录配置失败: {e}")
            return False


def get_arena_model_catalog(
    domain: str = "arena.ai",
    preset_name: Optional[str] = None,
    config_engine: Any = None,
) -> Dict[str, Any]:
    """获取指定预设的 Arena 模型目录配置。支持旧配置兼容回退。"""
    normalized_domain = str(domain or "arena.ai").strip().lower().strip(".")
    catalog_data = load_arena_model_catalog_data()
    domain_catalogs = catalog_data.get(normalized_domain, {})

    target_preset = str(preset_name or "").strip()
    if not target_preset and config_engine:
        try:
            target_preset = str(config_engine.get_default_preset(normalized_domain) or "").strip()
        except Exception:
            pass
    if not target_preset:
        target_preset = "主预设"

    if target_preset in domain_catalogs:
        return copy.deepcopy(domain_catalogs[target_preset])

    # 兼容期回退：若独立配置中无该预设，检查 config_engine 站点预设中是否有旧 model_catalog
    if config_engine:
        try:
            presets = None
            if hasattr(config_engine, "sites") and isinstance(config_engine.sites, dict):
                site = config_engine.sites.get(normalized_domain, {})
                if isinstance(site, dict):
                    presets = site.get("presets")
            if presets is None and hasattr(config_engine, "presets") and isinstance(config_engine.presets, dict):
                presets = config_engine.presets

            if isinstance(presets, dict):
                resolved_key = config_engine._resolve_preset_alias_key(target_preset, presets) if hasattr(config_engine, "_resolve_preset_alias_key") else target_preset
                preset_dict = presets.get(resolved_key)
                if isinstance(preset_dict, dict) and "model_catalog" in preset_dict:
                    return normalize_arena_model_catalog_config(preset_dict.get("model_catalog"))

            if hasattr(config_engine, "_get_site_data_readonly"):
                preset_dict = config_engine._get_site_data_readonly(normalized_domain, target_preset)
                if isinstance(preset_dict, dict) and "model_catalog" in preset_dict:
                    return normalize_arena_model_catalog_config(preset_dict.get("model_catalog"))
        except Exception:
            pass

    return normalize_arena_model_catalog_config({})


def set_arena_model_catalog(
    domain: str = "arena.ai",
    preset_name: Optional[str] = None,
    catalog_config: Any = None,
) -> Dict[str, Any]:
    """设置并原子保存指定预设的 Arena 模型目录配置"""
    normalized_domain = str(domain or "arena.ai").strip().lower().strip(".")
    target_preset = str(preset_name or "主预设").strip()
    normalized_cat = normalize_arena_model_catalog_config(catalog_config)

    with _catalog_io_lock:
        catalog_data = load_arena_model_catalog_data()
        if normalized_domain not in catalog_data:
            catalog_data[normalized_domain] = {}
        catalog_data[normalized_domain][target_preset] = normalized_cat
        if not save_arena_model_catalog_data(catalog_data):
            raise IOError(f"保存 Arena 独立模型目录配置失败: {normalized_domain} [{target_preset}]")

    return normalized_cat


def migrate_and_cleanup_sites_model_catalog(config_engine: Any) -> bool:
    """扫描 config_engine.sites，将各预设下的 model_catalog 迁移到独立文件，并从 sites.json 中清理旧字段"""
    if not config_engine or not hasattr(config_engine, "sites") or not isinstance(config_engine.sites, dict):
        return False

    with _catalog_io_lock:
        catalog_data = load_arena_model_catalog_data()
        catalog_data_changed = False
        to_cleanup = []

        for domain, site_config in config_engine.sites.items():
            if domain.startswith("_") or not isinstance(site_config, dict):
                continue
            d_key = str(domain).strip().lower().strip(".")
            if not route_domain_matches("arena.ai", d_key):
                continue

            presets = site_config.get("presets")
            if not isinstance(presets, dict):
                continue

            if d_key not in catalog_data:
                catalog_data[d_key] = {}

            for p_name, p_data in presets.items():
                if not isinstance(p_data, dict):
                    continue
                p_key = str(p_name).strip()
                if "model_catalog" in p_data:
                    old_cat = p_data.get("model_catalog")
                    # 如果独立文件没有该预设配置，则补全迁移
                    if p_key not in catalog_data[d_key]:
                        catalog_data[d_key][p_key] = normalize_arena_model_catalog_config(old_cat)
                        catalog_data_changed = True
                        logger.info(f"已迁移站点 {domain} 预设 [{p_name}] 的 model_catalog 到独立配置文件")
                    to_cleanup.append((p_data, d_key, p_key))

        if catalog_data_changed:
            if not save_arena_model_catalog_data(catalog_data):
                logger.error("保存独立模型目录配置文件失败，取消清理 sites.json 中的 model_catalog")
                return False

        # 只有在独立配置文件确认已包含该配置时，才安全清理内存与 sites.json
        sites_changed = False
        for p_data, d_key, p_key in to_cleanup:
            if d_key in catalog_data and p_key in catalog_data[d_key]:
                if "model_catalog" in p_data:
                    p_data.pop("model_catalog", None)
                    sites_changed = True

        if sites_changed:
            try:
                config_engine.save_config()
                logger.info("已从 sites.json 中清理已迁移的 model_catalog 残留字段")
            except Exception as e:
                logger.warning(f"清理 sites.json 中的 model_catalog 后保存失败: {e}")

        return catalog_data_changed or sites_changed


def get_model_catalog_preset(config_engine: Any, domain: Any) -> Optional[Dict[str, Any]]:
    """读取指定域名下已启用 model_catalog 的预设信息"""
    normalized_domain = str(domain or "").strip().lower().strip(".")
    if not normalized_domain or not route_domain_matches("arena.ai", normalized_domain):
        return None

    try:
        if hasattr(config_engine, "refresh_if_changed"):
            config_engine.refresh_if_changed()
        site = config_engine.sites.get(normalized_domain) if hasattr(config_engine, "sites") else None
    except Exception:
        site = None

    presets = site.get("presets") if isinstance(site, dict) else (
        getattr(config_engine, "presets", {}) if hasattr(config_engine, "presets") else {}
    )
    catalog_data = load_arena_model_catalog_data()
    domain_catalogs = catalog_data.get(normalized_domain, {})

    # 优先检查默认预设
    default_preset = ""
    if hasattr(config_engine, "get_default_preset"):
        try:
            default_preset = str(config_engine.get_default_preset(normalized_domain) or "").strip()
        except Exception:
            pass

    all_preset_names = []
    if default_preset:
        all_preset_names.append(default_preset)

    if isinstance(presets, dict):
        for p_name in presets.keys():
            if str(p_name) not in all_preset_names:
                all_preset_names.append(str(p_name))
    for p_name in domain_catalogs.keys():
        if str(p_name) not in all_preset_names:
            all_preset_names.append(str(p_name))

    for preset_name in all_preset_names:
        cat = domain_catalogs.get(preset_name)
        if not cat:
            # 兼容回退
            cat = get_arena_model_catalog(normalized_domain, preset_name, config_engine=config_engine)
        if cat and cat.get("enabled") and cat.get("source") == MODEL_CATALOG_SOURCE:
            preset = presets.get(preset_name, {}) if isinstance(presets, dict) else {}
            return {
                "preset_name": str(preset_name),
                "preset": preset,
                "catalog": cat,
            }

    return None


def get_arena_direct_catalog_for_tab(
    config_engine: Any,
    tab: Any,
    *,
    preset_name: Any = None,
) -> Optional[Dict[str, Any]]:
    """获取当前标签页对应的 Arena 直连目录配置"""
    if not isinstance(tab, dict):
        return None
    status = str(tab.get("status") or "").strip().lower()
    if status not in {"idle", "busy"} or bool(tab.get("terminating")):
        return None

    current_url = str(tab.get("url") or tab.get("current_url") or "").strip()
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

    catalog = get_arena_model_catalog("arena.ai", effective_preset_name, config_engine=config_engine)
    if not catalog.get("enabled") or catalog.get("source") != MODEL_CATALOG_SOURCE:
        return None

    from app.services.arena_direct_models import _is_arena_direct_url
    if not _is_arena_direct_url(current_url, catalog_preset=preset, catalog_config=catalog):
        return None

    return {
        "preset_name": effective_preset_name,
        "preset": preset,
        "catalog": catalog,
    }


# ==============================================================================
# 暗池模型判定、时间戳解析与全量缓存加载
# ==============================================================================

def parse_uuidv7_timestamp(uuid_str: str) -> Optional[float]:
    """从 UUIDv7 中解析毫秒级时间戳"""
    if not uuid_str or not isinstance(uuid_str, str):
        return None
    clean_hex = uuid_str.replace("-", "").strip().lower()
    if len(clean_hex) != 32 or clean_hex[12] != "7":
        return None
    try:
        ts_ms = int(clean_hex[:12], 16)
        if 1577836800000 <= ts_ms <= 1893456000000:
            return float(ts_ms)
    except Exception:
        pass
    return None


def parse_date_to_timestamp(date_str: str) -> Optional[float]:
    """将 YYYY-MM-DD 字符串转为 UTC 00:00:00 毫秒时间戳"""
    if not date_str or not isinstance(date_str, str):
        return None
    clean_str = date_str.strip()
    m = re.match(r"^(\d{4})[-_/](\d{1,2})[-_/](\d{1,2})$", clean_str)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            dt = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
            return dt.timestamp() * 1000.0
        except Exception:
            return None
    return None


def get_model_timestamp(model: Dict[str, Any]) -> Optional[float]:
    """获取模型入库时间戳（ms）"""
    mid = str(model.get("arena_model_id") or model.get("id") or "")
    ts = parse_uuidv7_timestamp(mid)
    if ts is not None:
        return ts

    name_str = " ".join(
        str(model.get(k) or "") for k in ("name", "displayName", "display_name", "publicName", "public_name")
    )
    m = re.search(r"202[4-7][_-]?(0[1-9]|1[0-2])[_-]?([0-3][0-9])", name_str)
    if m:
        try:
            match_full = m.group(0).replace("_", "-")
            digits = re.sub(r"\D", "", match_full)
            if len(digits) == 8:
                year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:])
                dt = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
                return dt.timestamp() * 1000.0
        except Exception:
            pass
    return None


def is_dark_pool_model(model: Dict[str, Any]) -> bool:
    """判定是否属于暗池（在前端模型选择器中隐藏或来自离线暗池缓存）"""
    if "_is_dark_pool" in model:
        return bool(model["_is_dark_pool"])
    if model.get("is_dark_pool") is not None:
        return bool(model.get("is_dark_pool"))

    disp = str(model.get("display_name") or model.get("displayName") or "").strip().lower()
    name = str(model.get("name") or "").strip().lower()
    pub = str(model.get("public_name") or model.get("publicName") or "").strip().lower()
    search_str = f"{disp} {name} {pub}"

    # 包含明确内部/测试特征的直接归为暗池
    if any(k in search_str for k in ["internal", "test", "dlp", "fireworks", "node"]):
        return True

    # 命中公开白名单的必定为明池
    for pub_name in ONLINE_PUBLIC_VISIBLE_MODELS:
        pub_lower = pub_name.lower()
        if disp == pub_lower or pub == pub_lower or name == pub_lower:
            return False

    # 来自离线暗池全量缓存且不在公开白名单的归为暗池
    if model.get("_is_offline_cache"):
        return True

    # 实时从页面抓取（或单测传入）的普通模型默认视为明池
    return False


_offline_cache_lock = threading.Lock()
_offline_cached_models: Optional[List[Dict[str, Any]]] = None


def load_arena_offline_cache_models() -> List[Dict[str, Any]]:
    """加载本地 scripts/arena_models_cache.json 中的模型并规范化"""
    global _offline_cached_models
    with _offline_cache_lock:
        if _offline_cached_models is not None:
            return _offline_cached_models

        if not ARENA_MODELS_CACHE_PATH.exists():
            _offline_cached_models = []
            return []

        try:
            with open(ARENA_MODELS_CACHE_PATH, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
            if not isinstance(raw_list, list):
                _offline_cached_models = []
                return []

            results = []
            for item in raw_list:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("id") or "").strip()
                if not mid:
                    continue

                caps = item.get("capabilities") or {}
                out_caps = caps.get("outputCapabilities") or {}
                ranks = item.get("rankByModality") or {}

                def rank_is_finite(key):
                    val = ranks.get(key)
                    return isinstance(val, (int, float)) and val < 9007199254740991

                has_text = bool(out_caps.get("text"))
                has_image = bool(out_caps.get("image")) or rank_is_finite("image")
                has_video = bool(out_caps.get("video")) or rank_is_finite("video")
                # `outputCapabilities.web` is browsing/search support, not
                # the WebDev/code modality.  Use only the webdev rank here.
                has_web = rank_is_finite("webdev")
                has_search = bool(out_caps.get("search")) or rank_is_finite("search")

                disp_name = str(item.get("displayName") or item.get("publicName") or item.get("name") or mid).strip()
                internal_name = str(item.get("name") or item.get("id") or disp_name).strip()
                name_search = f"{internal_name} {disp_name} {mid}".lower()

                is_known_image = any(k in name_search for k in [
                    "gpt-image", "mona-lisa", "luna-lisa", "lina-alpha", "lina-f-alpha", "silver_halide",
                    "flux", "seedream", "seededit", "imagine", "imagen", "z-image", "midjourney", "dall-e", "recraft", "krea"
                ])

                modality = "text"
                if has_video and not has_text and not is_known_image:
                    modality = "video"
                elif is_known_image or (has_image and not has_text):
                    modality = "image"
                elif has_web:
                    modality = "code"
                elif has_search:
                    modality = "search"
                elif has_text:
                    modality = "text"
                elif has_image:
                    modality = "image"
                elif has_video:
                    modality = "video"

                results.append({
                    "arena_model_id": mid,
                    "name": internal_name,
                    "public_name": disp_name,
                    "display_name": disp_name,
                    "provider": str(item.get("provider") or ""),
                    "organization": str(item.get("organization") or item.get("provider") or "arena.ai"),
                    "modality": modality,
                    "_is_offline_cache": True,
                })

            _offline_cached_models = results
            return results
        except Exception as e:
            logger.warning(f"读取 Arena 离线模型缓存失败: {e}")
            _offline_cached_models = []
            return []


# ==============================================================================
# 模型过滤引擎（明池 + 暗池）
# ==============================================================================

def filter_arena_catalog_models(
    models: List[Dict[str, Any]],
    catalog_config: Any,
) -> List[Dict[str, Any]]:
    """对模型列表执行明池与暗池的分别过滤与合并去重排序"""
    from app.services.arena_direct_models import (
        _canonical_modality,
        _natural_sort_key,
        get_arena_direct_model_public_id,
    )

    catalog = normalize_arena_model_catalog_config(catalog_config)
    target_modality = _canonical_modality(catalog.get("modality") or "text")
    enable_dark_pool = bool(catalog.get("enable_dark_pool", False))

    dark_since_ts = parse_date_to_timestamp(catalog.get("dark_pool_since", ""))
    dark_whitelist = [item.casefold() for item in catalog.get("dark_pool_whitelist_keywords", [])]
    dark_blacklist = [item.casefold() for item in catalog.get("dark_pool_blacklist_keywords", [])]

    pub_includes = [item.casefold() for item in catalog.get("include_keywords", [])]
    pub_excludes = [item.casefold() for item in catalog.get("exclude_keywords", [])]

    # 合并来自页面实时抓取与离线缓存的模型（按 arena_model_id 去重）
    all_candidates: List[Dict[str, Any]] = []
    seen_ids = set()
    for m in (models or []) + (load_arena_offline_cache_models() if enable_dark_pool else []):
        if not isinstance(m, dict):
            continue
        mid = str(m.get("arena_model_id") or "").strip().lower()
        if not mid or mid in seen_ids:
            continue
        seen_ids.add(mid)
        all_candidates.append(m)

    video_keywords = (
        "video", "seedance", "dreamina", "sora", "veo", "gemini-omni", "kling",
        "hailuo", "runway", "pika", "mochi", "ltx", "vidu", "luma", "wan2",
        "wan-2", "wan_2", "wanx", "pixverse", "kandinsky-video", "kandinsky_video", "happyhorse"
    )

    image_keywords = (
        "image", "mona-lisa", "luna-lisa", "lina-alpha", "lina-f-alpha", "lina",
        "silver_halide", "flux", "imagine", "seedream", "seededit", "imagen", "z-image"
    )

    result = []
    result_seen_ids = set()

    for model in all_candidates:
        search_str = " ".join(
            str(model.get(k) or "") for k in ("name", "public_name", "display_name", "search_name")
        ).lower()
        if any(k in search_str for k in video_keywords):
            continue

        model_modality = _canonical_modality(model.get("modality"))
        if target_modality:
            if target_modality in {"code", "search", "video"}:
                if model_modality != target_modality:
                    continue
            elif target_modality == "image":
                model_is_image = (model_modality == "image") or (
                    not model_modality and any(k in search_str for k in image_keywords)
                )
                if not model_is_image:
                    continue
            elif target_modality == "text":
                if model_modality and model_modality != "text":
                    continue
                if not model_modality and any(k in search_str for k in image_keywords):
                    continue
            else:
                if model_modality and model_modality != target_modality:
                    continue

        searchable = " ".join(
            str(model.get(key) or "")
            for key in (
                "name", "public_name", "display_name", "search_name", "provider", "organization"
            )
        ).casefold()
        searchable += " " + " ".join(
            str(alias or "") for alias in (model.get("aliases") or [])
        ).casefold()

        is_dark = is_dark_pool_model(model)

        if is_dark:
            # 暗池处理规则
            if not enable_dark_pool:
                continue

            # 1. 黑名单最终否决
            if dark_blacklist and any(kw in searchable for kw in dark_blacklist):
                continue

            # 2. 白名单与日期条件检查（若设置了白名单或日期，必须满足其中之一）
            if dark_whitelist or dark_since_ts is not None:
                whitelisted = bool(dark_whitelist and any(kw in searchable for kw in dark_whitelist))
                if not whitelisted:
                    if dark_since_ts is None:
                        continue
                    model_ts = get_model_timestamp(model)
                    if model_ts is None or model_ts < dark_since_ts:
                        continue
        else:
            # 明池处理规则（不受暗池规则影响）
            if pub_includes and not any(keyword in searchable for keyword in pub_includes):
                continue
            if pub_excludes and any(keyword in searchable for keyword in pub_excludes):
                continue

        mid_lower = str(model.get("arena_model_id") or "").lower()
        if mid_lower in result_seen_ids:
            continue
        result_seen_ids.add(mid_lower)
        result.append(model)

    result.sort(key=lambda m: _natural_sort_key(get_arena_direct_model_public_id(m)))
    return result


__all__ = [
    "ARENA_MODEL_CATALOG_CONFIG_PATH",
    "MODEL_CATALOG_SOURCE",
    "ONLINE_PUBLIC_VISIBLE_MODELS",
    "filter_arena_catalog_models",
    "get_arena_direct_catalog_for_tab",
    "get_arena_model_catalog",
    "get_model_catalog_preset",
    "get_model_timestamp",
    "is_dark_pool_model",
    "load_arena_model_catalog_data",
    "load_arena_offline_cache_models",
    "migrate_and_cleanup_sites_model_catalog",
    "normalize_arena_model_catalog_config",
    "parse_date_to_timestamp",
    "parse_uuidv7_timestamp",
    "save_arena_model_catalog_data",
    "set_arena_model_catalog",
]
