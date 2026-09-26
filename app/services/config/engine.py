"""
app/services/config/engine.py - 配置引擎主类

职责：
- 配置文件读写
- 站点配置管理
- 配置缓存与热重载
- 图片配置、提取器管理
"""

import json
import os
import copy
import re
import threading
import time
from app.core.config import get_logger
from typing import Dict, Optional, List, Any, Set, Union, Callable
from urllib.parse import urljoin
from app.core.parsers import ParserRegistry
from app.models.schemas import (
    SiteConfig,
    WorkflowStep,
    SelectorDefinition,
    ADVANCED_FIELDS,
    PRESET_ADVANCED_FIELDS,
    SITE_ADVANCED_FIELDS,
    get_default_image_extraction_config,
    get_default_file_paste_config,
    get_default_prompt_padding_config,
    get_default_site_advanced_config,
    get_default_attachment_monitor_config,
    get_default_send_confirmation_config,
    get_modality_policy,
    get_enabled_modalities,
    is_modality_enabled,
    normalize_modalities_config,
    normalize_modality_policy,
)
from app.services.extractor_manager import extractor_manager
from app.services.parser_manager import parser_manager
from app.utils.site_rules import derive_site_card_id, get_site_rule
from app.utils.site_discovery import automatic_discovery_allowed, specific_chat_selectors, selectors_match_chat_html
from app.core.request_transport import (
    get_default_request_transport_config,
    normalize_request_transport_config,
)
from .cache import ConfigCache
from .site_store import SiteStore
from .managers import GlobalConfigManager, ImagePresetsManager
from .processors import HTMLCleaner, SelectorValidator, AIAnalyzer


# R2-3：模块级定义迁到 engine_common，这里重新导出以保持兼容（外部与测试都从 engine 导入这些名字）
from .engine_common import (  # noqa: E402,F401
    ConfigConstants,
    DEFAULT_PRESET_NAME,
    DEFAULT_WORKFLOW,
    PRESET_FIELDS,
    _MISSING,
    _SITE_STARTUP_JS_PATTERNS,
    _coerce_bool,
    _merge_config_patch,
    get_default_network_config,
    get_default_stream_config,
    logger,
)
from .engine_local_overrides import ConfigLocalOverridesMixin  # noqa: E402
from .engine_migrations import ConfigMigrationsMixin  # noqa: E402
from .engine_presets import ConfigPresetsMixin  # noqa: E402
from .engine_sites import ConfigSitesMixin  # noqa: E402
from .engine_media import ConfigMediaMixin  # noqa: E402
from .engine_stream import ConfigStreamMixin  # noqa: E402

# ================= 配置引擎主类 =================

class ConfigEngine(
    ConfigLocalOverridesMixin,
    ConfigMigrationsMixin,
    ConfigPresetsMixin,
    ConfigSitesMixin,
    ConfigMediaMixin,
    ConfigStreamMixin,
):
    """配置引擎主类"""
    def __init__(self):
        self.config_file = ConfigConstants.CONFIG_FILE  # 旧版单文件，仅用于自动迁移
        self.sites_dir = ConfigConstants.SITES_DIR
        self.site_store = SiteStore(self.sites_dir, self.config_file)
        self.local_sites_file = ConfigConstants.SITES_LOCAL_FILE
        self._io_lock = threading.RLock()
        self.last_mtime = 0.0
        self.last_signature: tuple = ()
        self.last_local_mtime = 0.0
        self.sites: Dict[str, SiteConfig] = {}
        self._global_default_presets: Dict[str, str] = {}
        self._local_default_presets: Dict[str, str] = {}
        self._local_sites_payload: Dict[str, Any] = {}
        self._cache = ConfigCache(ttl=5.0)

        # 子管理器
        self.global_config = GlobalConfigManager()
        self.image_presets = ImagePresetsManager(ConfigConstants.IMAGE_PRESETS_FILE)

        # 加载配置
        self._load_config()
        self._migrate_global_commands()

        # 处理器
        self.html_cleaner = HTMLCleaner()
        self.validator = SelectorValidator(self.global_config.get_fallback_selectors())
        self.ai_analyzer = AIAnalyzer(self.global_config)

        # 迁移旧配置（顺序重要：先转预设格式，再补缺失字段，最后清理残留）
        self._migrate_loaded_config()
        self._apply_local_site_overrides()

        logger.debug(f"配置引擎已初始化，已加载 {len(self.sites)} 个站点配置")
    def _load_config(self):
        with self._io_lock:
            return self._load_config_locked()
    def _load_config_locked(self):
        """初始化加载站点配置（config/sites/ 目录；发现旧版 sites.json 时自动迁移）"""
        try:
            data = self.site_store.load()
            self.last_signature = self.site_store.signature()
            if data is None:
                logger.info(f"站点配置目录 {self.sites_dir} 为空，将在首次保存时创建")
                self._apply_local_site_overrides()
                return
            self.last_mtime = time.time()

            # 提取并加载 _global；缺失时重置为默认全局配置。
            global_section = data.pop("_global", {})
            self.global_config.load(global_section if isinstance(global_section, dict) else {})

            # 过滤内部键
            self.sites = {
                k: v for k, v in data.items()
                if not k.startswith('_')
            }
            self._refresh_global_default_presets_from_sites()
            self._apply_local_site_overrides()
            logger.debug(f"已加载站点配置: {self.sites_dir}（{len(self.sites)} 个站点）")

        except Exception as e:
            logger.error(f"加载配置失败: {e}")
    def refresh_if_changed(self):
        """检查文件是否变化，如果变化则重载"""
        if not self.site_store.exists() and not os.path.exists(self.local_sites_file):
            return

        try:
            current_signature = self.site_store.signature()
            current_local_mtime = os.path.getmtime(self.local_sites_file) if os.path.exists(self.local_sites_file) else 0.0
            if current_signature != self.last_signature or current_local_mtime != self.last_local_mtime:
                logger.debug("⚡ 检测到站点配置变化，热重载")
                self.reload_config()
        except Exception as e:
            logger.error(f"检查文件变化失败: {e}")
    def reload_config(self):
        with self._io_lock:
            return self._reload_config_locked()
    def _reload_config_locked(self):
        """重新加载配置（Hot Reload）"""
        self._cache.invalidate()

        if not self.site_store.exists():
            logger.warning("重载失败：站点配置目录为空")
            return

        try:
            previous = {"_global": self.global_config.to_dict(), **self.sites}
            data = self.site_store.load(previous=previous)
            if data is None:
                logger.warning("重载失败：站点配置目录为空")
                return
            signature = self.site_store.signature()

            # 提取并加载 _global；缺失时重置为默认全局配置。
            global_section = data.pop("_global", {})
            self.global_config.load(global_section if isinstance(global_section, dict) else {})
            self.validator.fallback_selectors = self.global_config.get_fallback_selectors()

            # 过滤内部键
            self.sites = {
                k: v for k, v in data.items()
                if not k.startswith('_')
            }
            self.last_signature = signature
            self.last_mtime = time.time()
            self._refresh_global_default_presets_from_sites()
            self._migrate_loaded_config()
            self._apply_local_site_overrides()
            logger.debug(f"✅ 配置已热重载 (Sites: {len(self.sites)})")

        except json.JSONDecodeError as e:
            logger.error(f"❌ 重载配置失败（JSON格式错误），保留旧配置: {e}")
        except Exception as e:
            logger.error(f"❌ 重载配置失败: {e}")
    def save_config(self):
        """公开的保存方法（供 API 调用）"""
        return self._save_config()
    def _save_config(self) -> bool:
        with self._io_lock:
            return self._save_config_locked()
    def _save_config_locked(self) -> bool:
        """保存站点配置（只重写有变化的站点文件，每个文件原子替换）"""
        local_snapshot: Optional[tuple[bool, bytes, Dict[str, Any], Dict[str, str]]] = None
        local_overrides_written = False
        default_maps_snapshot = (
            copy.deepcopy(self._global_default_presets),
            copy.deepcopy(self._local_default_presets),
        )

        try:
            self._prune_default_preset_maps()
            persisted_sites = {}
            for domain, site in self.sites.items():
                if domain.startswith('_') or not isinstance(site, dict):
                    continue

                site_copy = copy.deepcopy(site)
                from app.utils.site_url import route_domain_matches
                if route_domain_matches("arena.ai", domain):
                    try:
                        from app.services.arena_model_catalog import load_arena_model_catalog_data
                        cat_data = load_arena_model_catalog_data()
                        d_key = str(domain).strip().lower().strip(".")
                        domain_cat = cat_data.get(d_key, {})
                    except Exception:
                        domain_cat = None

                    presets_dict = site_copy.get("presets")
                    if isinstance(presets_dict, dict):
                        for p_key, p_val in presets_dict.items():
                            if isinstance(p_val, dict) and "model_catalog" in p_val:
                                if domain_cat is not None and str(p_key).strip() in domain_cat:
                                    p_val.pop("model_catalog", None)
                    # 同步清理内存中的残留
                    mem_presets = site.get("presets")
                    if isinstance(mem_presets, dict):
                        for p_key, p_val in mem_presets.items():
                            if isinstance(p_val, dict) and "model_catalog" in p_val:
                                if domain_cat is not None and str(p_key).strip() in domain_cat:
                                    p_val.pop("model_catalog", None)

                persisted_default = self._get_persisted_default_preset(domain, site_copy)
                if persisted_default:
                    site_copy["default_preset"] = persisted_default
                else:
                    site_copy.pop("default_preset", None)
                persisted_sites[domain] = site_copy

            # 构建完整配置（包含 _global）
            full_config = {
                "_global": self.global_config.to_dict(),
                **persisted_sites
            }

            local_snapshot = self._snapshot_local_site_overrides_locked()
            if local_snapshot is None:
                self._global_default_presets, self._local_default_presets = default_maps_snapshot
                return False
            if not self._save_local_site_overrides():
                self._restore_local_site_overrides_locked(local_snapshot)
                self._global_default_presets, self._local_default_presets = default_maps_snapshot
                return False
            local_overrides_written = True

            written = self.site_store.save(full_config)
            self.last_signature = self.site_store.signature()
            self.last_mtime = time.time()

            self._cache.invalidate()
            logger.info(f"配置已保存: {self.sites_dir}（重写 {len(written)} 个站点文件）")
            return True

        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            if local_overrides_written:
                self._restore_local_site_overrides_locked(local_snapshot)
            self._global_default_presets, self._local_default_presets = default_maps_snapshot
            return False
    def _migrate_loaded_config(self):
        """Run migrations that apply to freshly loaded site config data."""
        self._migrate_to_presets()
        self.migrate_site_configs()
        self._migrate_site_advanced_to_presets()
        self._cleanup_preset_residuals()
        try:
            from app.services.arena_model_catalog import migrate_and_cleanup_sites_model_catalog
            migrate_and_cleanup_sites_model_catalog(self)
        except Exception as exc:
            logger.warning(f"Arena 模型目录自动迁移失败: {exc}")


__all__ = ['ConfigEngine', 'ConfigConstants', 'DEFAULT_WORKFLOW', 'DEFAULT_PRESET_NAME']
