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
from app.core.config import get_logger
from typing import Dict, Optional, List, Any
from urllib.parse import urljoin
from app.core.parsers import ParserRegistry
from app.models.schemas import (
    SiteConfig,
    WorkflowStep,
    SelectorDefinition,
    get_default_image_extraction_config,
    get_default_file_paste_config,
    get_default_site_advanced_config,
    get_default_attachment_monitor_config,
    get_default_send_confirmation_config,
)
from app.services.extractor_manager import extractor_manager
from app.services.parser_manager import parser_manager
from app.utils.site_rules import derive_site_card_id, get_site_rule
from app.core.request_transport import (
    get_default_request_transport_config,
    normalize_request_transport_config,
)
from .managers import GlobalConfigManager, ImagePresetsManager
from .processors import HTMLCleaner, SelectorValidator, AIAnalyzer


logger = get_logger("CFG_ENG")


# ================= 常量配置 =================

class ConfigConstants:
    """配置引擎常量"""
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    CONFIG_FILE = os.getenv("SITES_CONFIG_FILE", os.path.join(_PROJECT_ROOT, "config", "sites.json"))
    SITES_LOCAL_FILE = os.getenv("SITES_LOCAL_FILE", os.path.join(_PROJECT_ROOT, "config", "sites.local.json"))
    COMMANDS_FILE = os.getenv("COMMANDS_CONFIG_FILE", os.path.join(_PROJECT_ROOT, "config", "commands.json"))
    COMMANDS_LOCAL_FILE = os.getenv("COMMANDS_LOCAL_FILE", os.path.join(_PROJECT_ROOT, "config", "commands.local.json"))
    IMAGE_PRESETS_FILE = os.path.join(_PROJECT_ROOT, "config", "image_presets.json")
    
    MAX_HTML_CHARS = int(os.getenv("MAX_HTML_CHARS", "120000"))
    TEXT_TRUNCATE_LENGTH = 80
    
    AI_MAX_RETRIES = 3
    AI_RETRY_BASE_DELAY = 1.0
    AI_RETRY_MAX_DELAY = 10.0
    AI_REQUEST_TIMEOUT = 120

# ================= 预设常量 =================

DEFAULT_PRESET_NAME = "主预设"

# 预设内包含的配置字段（用于迁移和校验）
PRESET_FIELDS = [
    "selectors", "workflow", "stream_config",
    "image_extraction", "file_paste", "stealth",
    "extractor_id", "extractor_verified"
]

# 默认工作流
DEFAULT_WORKFLOW: List[WorkflowStep] = [
    {"action": "CLICK", "target": "new_chat_btn", "optional": True, "value": None},
    {"action": "WAIT", "target": "", "optional": False, "value": "0.5"},
    {"action": "FILL_INPUT", "target": "input_box", "optional": False, "value": None},
    {"action": "CLICK", "target": "send_btn", "optional": True, "value": None},
    {"action": "KEY_PRESS", "target": "Enter", "optional": True, "value": None},
    {"action": "STREAM_WAIT", "target": "result_container", "optional": False, "value": None}
]

def get_default_stream_config() -> Dict[str, Any]:
    """获取默认流式配置"""
    return {
        "mode": "dom",              # dom / network
        "request_transport": get_default_request_transport_config(),
        "hard_timeout": 300,        # 全局硬超时（秒）
        "send_confirmation": get_default_send_confirmation_config(),
        "attachment_monitor": get_default_attachment_monitor_config(),
        
        # 网络监听配置（可选）
        "network": None
    }


def get_default_network_config() -> Dict[str, Any]:
    """获取默认网络监听配置"""
    return {
        "listen_pattern": "",           # URL 匹配模式（必填）
        "stream_match_mode": "keyword", # 流目标匹配模式（keyword / regex）
        "stream_match_pattern": "",     # 流目标匹配表达式（为空时回退到 listen_pattern）
        "parser": "",                   # 解析器 ID（必填）
        "silence_threshold": 3.0,       # 静默超时（秒）
        "response_interval": 0.5        # 轮询间隔（秒）
    }


_SITE_STARTUP_JS_PATTERNS = (
    re.compile(r"""location\.(?:assign|replace)\(\s*['"]([^'"]+)['"]\s*\)""", re.IGNORECASE),
    re.compile(r"""location\.href\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE),
)

# ================= 配置引擎主类 =================

class ConfigEngine:
    """配置引擎主类"""
    
    def __init__(self):
        self.config_file = ConfigConstants.CONFIG_FILE
        self.local_sites_file = ConfigConstants.SITES_LOCAL_FILE
        self.last_mtime = 0.0
        self.last_local_mtime = 0.0
        self.sites: Dict[str, SiteConfig] = {}
        self._global_default_presets: Dict[str, str] = {}
        self._local_default_presets: Dict[str, str] = {}
        self._local_sites_payload: Dict[str, Any] = {}
        
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
        self._migrate_to_presets()
        self.migrate_site_configs()
        self._cleanup_preset_residuals()
        self._apply_local_site_overrides()
        
        logger.debug(f"配置引擎已初始化，已加载 {len(self.sites)} 个站点配置")
    
    # ================= 配置加载与保存 =================
    
    def _load_config(self):
        """初始化加载配置文件"""
        if not os.path.exists(self.config_file):
            logger.info(f"配置文件 {self.config_file} 不存在，将创建新文件")
            self._apply_local_site_overrides()
            return
        
        try:
            self.last_mtime = os.path.getmtime(self.config_file)
            
            with open(self.config_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return
                
                data = json.loads(content)
                
                # 提取并加载 _global
                if "_global" in data:
                    self.global_config.load(data.pop("_global"))
            
                # 过滤内部键
                self.sites = {
                    k: v for k, v in data.items() 
                    if not k.startswith('_')
                }
                self._refresh_global_default_presets_from_sites()
                self._apply_local_site_overrides()
                logger.debug(f"已加载配置文件: {self.config_file} (mtime: {self.last_mtime})")
        
        except json.JSONDecodeError as e:
            logger.error(f"配置文件格式错误: {e}")
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
    
    def refresh_if_changed(self):
        """检查文件是否变化，如果变化则重载"""
        if not os.path.exists(self.config_file) and not os.path.exists(self.local_sites_file):
            return

        try:
            current_mtime = os.path.getmtime(self.config_file) if os.path.exists(self.config_file) else 0.0
            current_local_mtime = os.path.getmtime(self.local_sites_file) if os.path.exists(self.local_sites_file) else 0.0
            if current_mtime != self.last_mtime or current_local_mtime != self.last_local_mtime:
                logger.debug(f"⚡ 检测到配置文件变化 (new mtime: {current_mtime})")
                self.reload_config()
        except Exception as e:
            logger.error(f"检查文件变化失败: {e}")

    def reload_config(self):
        """重新加载配置（Hot Reload）"""
        if not os.path.exists(self.config_file):
            logger.warning("重载失败：配置文件不存在")
            return

        try:
            mtime = os.path.getmtime(self.config_file)
            
            with open(self.config_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    data = {}
                else:
                    data = json.loads(content)
            
            # 提取并加载 _global
            if "_global" in data:
                self.global_config.load(data.pop("_global"))
                self.validator.fallback_selectors = self.global_config.get_fallback_selectors()
        
            # 过滤内部键
            self.sites = {
                k: v for k, v in data.items() 
                if not k.startswith('_')
            }
            self.last_mtime = mtime
            self._refresh_global_default_presets_from_sites()
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
        """保存配置文件（原子写入版）"""
        tmp_file = self.config_file + ".tmp"
        
        try:
            self._prune_default_preset_maps()
            persisted_sites = {}
            for domain, site in self.sites.items():
                if domain.startswith('_') or not isinstance(site, dict):
                    continue

                site_copy = copy.deepcopy(site)
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
            
            # 步骤 1：写入临时文件
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(full_config, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            
            # 步骤 2：原子替换
            os.replace(tmp_file, self.config_file)
            
            # 更新时间戳
            if os.path.exists(self.config_file):
                self.last_mtime = os.path.getmtime(self.config_file)

            if not self._save_local_site_overrides():
                return False
            
            logger.info(f"配置已保存: {self.config_file}")
            return True
        
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
            return False

    def _load_local_site_overrides(self) -> Dict[str, str]:
        """加载本地站点覆盖配置，并保留未识别字段。"""
        if not os.path.exists(self.local_sites_file):
            self.last_local_mtime = 0.0
            self._local_sites_payload = {}
            return {}

        try:
            with open(self.local_sites_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)

            self.last_local_mtime = os.path.getmtime(self.local_sites_file)
            self._local_sites_payload = data if isinstance(data, dict) else {}
            defaults = self._local_sites_payload.get("default_presets", {})
            if not isinstance(defaults, dict):
                return {}

            return {
                str(domain).strip(): str(preset).strip()
                for domain, preset in defaults.items()
                if str(domain).strip() and str(preset).strip()
            }
        except json.JSONDecodeError as e:
            logger.error(f"本地站点覆盖配置格式错误: {e}")
            self._local_sites_payload = {}
            return {}
        except Exception as e:
            logger.error(f"加载本地站点覆盖配置失败: {e}")
            self._local_sites_payload = {}
            return {}

    def _prune_default_preset_maps(self) -> None:
        active_domains = {
            domain for domain, site in self.sites.items()
            if not domain.startswith('_') and isinstance(site, dict)
        }
        self._global_default_presets = {
            domain: preset
            for domain, preset in self._global_default_presets.items()
            if domain in active_domains and str(preset or "").strip()
        }
        self._local_default_presets = {
            domain: preset
            for domain, preset in self._local_default_presets.items()
            if domain in active_domains and str(preset or "").strip()
        }

    def _refresh_global_default_presets_from_sites(self) -> None:
        next_defaults: Dict[str, str] = {}
        for domain, site in self.sites.items():
            if domain.startswith('_') or not isinstance(site, dict):
                continue
            resolved = self._resolve_default_preset_name(site)
            if resolved:
                next_defaults[domain] = resolved
        self._global_default_presets = next_defaults

    def _get_persisted_default_preset(self, domain: str, site: Dict[str, Any]) -> Optional[str]:
        presets = site.get("presets", {})
        if not isinstance(presets, dict) or not presets:
            self._global_default_presets.pop(domain, None)
            return None

        candidate = str(self._global_default_presets.get(domain, "") or "").strip()
        if candidate and candidate in presets:
            return candidate

        resolved = self._resolve_default_preset_name(site)
        if resolved:
            self._global_default_presets[domain] = resolved
            return resolved

        self._global_default_presets.pop(domain, None)
        return None

    def _sync_site_default_preset_state(self, domain: str, site: Dict[str, Any]) -> bool:
        presets = site.get("presets", {})
        if not isinstance(presets, dict) or not presets:
            self._global_default_presets.pop(domain, None)
            self._local_default_presets.pop(domain, None)
            if "default_preset" in site:
                del site["default_preset"]
                return True
            return False

        persisted_default = self._get_persisted_default_preset(domain, site)

        local_default = str(self._local_default_presets.get(domain, "") or "").strip()
        if local_default and local_default not in presets:
            self._local_default_presets.pop(domain, None)
            local_default = ""

        if local_default and persisted_default and local_default == persisted_default:
            self._local_default_presets.pop(domain, None)
            local_default = ""

        effective_default = local_default or persisted_default
        if not effective_default:
            effective_default = self._resolve_default_preset_name(site)

        if effective_default and site.get("default_preset") != effective_default:
            site["default_preset"] = effective_default
            return True

        if not effective_default and "default_preset" in site:
            del site["default_preset"]
            return True

        return False

    def _apply_local_site_overrides(self):
        """将本地默认预设选择覆盖到当前站点配置。"""
        self._local_default_presets = self._load_local_site_overrides()
        applied = 0
        for domain, site in self.sites.items():
            if not isinstance(site, dict):
                continue
            self._sync_site_default_preset_state(domain, site)
            local_default = self._local_default_presets.get(domain)
            if local_default and site.get("default_preset") == local_default:
                applied += 1

        if applied > 0:
            logger.debug(f"已应用 {applied} 个本地默认预设覆盖")

    def _save_local_site_overrides(self) -> bool:
        """保存本地站点覆盖配置。"""
        tmp_file = self.local_sites_file + ".tmp"
        self._prune_default_preset_maps()
        defaults = {}
        for domain, preset_name in self._local_default_presets.items():
            site = self.sites.get(domain)
            if not isinstance(site, dict):
                continue
            presets = site.get("presets", {})
            preset_value = str(preset_name or "").strip()
            if isinstance(presets, dict) and preset_value and preset_value in presets:
                defaults[domain] = preset_value

        payload = dict(self._local_sites_payload or {})
        if os.path.exists(self.local_sites_file):
            try:
                with open(self.local_sites_file, "r", encoding="utf-8-sig") as f:
                    latest_payload = json.load(f)
                if isinstance(latest_payload, dict):
                    payload.update(latest_payload)
            except Exception:
                pass
        payload["default_presets"] = defaults

        try:
            os.makedirs(os.path.dirname(self.local_sites_file), exist_ok=True)
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_file, self.local_sites_file)
            self.last_local_mtime = os.path.getmtime(self.local_sites_file)
            self._local_sites_payload = payload
            return True
        except Exception as e:
            logger.error(f"保存本地站点覆盖配置失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
            return False

    def _load_commands_file(self) -> List[Dict[str, Any]]:
        """加载独立命令配置文件"""
        commands_file = ConfigConstants.COMMANDS_FILE
        if not os.path.exists(commands_file):
            return []

        try:
            with open(commands_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                data = data.get("commands", [])

            if isinstance(data, list):
                return data

            logger.warning(f"命令配置文件格式无效: {commands_file}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"命令配置文件格式错误: {e}")
            return []
        except Exception as e:
            logger.error(f"加载命令配置失败: {e}")
            return []

    def _save_commands_file(self, commands: List[Dict[str, Any]]) -> bool:
        """保存独立命令配置文件"""
        commands_file = ConfigConstants.COMMANDS_FILE
        tmp_file = commands_file + ".tmp"

        try:
            os.makedirs(os.path.dirname(commands_file), exist_ok=True)
            payload = {"commands": commands}

            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_file, commands_file)
            logger.info(f"命令配置已保存: {commands_file}")
            return True
        except Exception as e:
            logger.error(f"保存命令配置失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
            return False

    @staticmethod
    def _merge_commands(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """保留已有命令，仅追加不存在的命令"""
        merged = list(existing or [])
        seen_ids = {
            str(cmd.get("id", "")).strip()
            for cmd in merged
            if isinstance(cmd, dict) and str(cmd.get("id", "")).strip()
        }
        seen_names = {
            str(cmd.get("name", "")).strip()
            for cmd in merged
            if isinstance(cmd, dict) and str(cmd.get("name", "")).strip()
        }

        for cmd in incoming or []:
            if not isinstance(cmd, dict):
                continue

            command_id = str(cmd.get("id", "")).strip()
            command_name = str(cmd.get("name", "")).strip()

            if command_id and command_id in seen_ids:
                continue
            if command_name and command_name in seen_names:
                continue

            merged.append(cmd)
            if command_id:
                seen_ids.add(command_id)
            if command_name:
                seen_names.add(command_name)

        return merged

    def _migrate_global_commands(self):
        """将旧版 _global.commands 迁移到独立文件"""
        legacy_commands = self.global_config.get("commands", [])
        existing_commands = self._load_commands_file()

        if not isinstance(legacy_commands, list):
            legacy_commands = []

        merged_commands = self._merge_commands(existing_commands, legacy_commands)

        if legacy_commands or not os.path.exists(ConfigConstants.COMMANDS_FILE):
            self._save_commands_file(merged_commands)

        if self.global_config.remove("commands"):
            self._save_config()
    # ================= 预设系统核心方法 =================
    
    def _migrate_to_presets(self):
        """
        将旧格式（扁平）站点配置迁移为预设格式
        
        旧格式: { "selectors": {...}, "workflow": [...], ... }
        新格式: { "presets": { "主预设": { "selectors": {...}, ... } } }
        """
        migrated_count = 0
        
        for domain in list(self.sites.keys()):
            if domain.startswith('_'):
                continue
            
            site_config = self.sites[domain]
            
            # 已经是预设格式，跳过
            if "presets" in site_config:
                continue
            
            # 将所有已知配置字段提取到主预设中
            preset_data = {}
            remaining = {}
            
            for key, value in site_config.items():
                if key in PRESET_FIELDS:
                    preset_data[key] = value
                else:
                    # 未知字段也放入预设（保留用户自定义数据）
                    preset_data[key] = value
            
            # 构建新格式
            self.sites[domain] = {
                "default_preset": DEFAULT_PRESET_NAME,
                "presets": {
                    DEFAULT_PRESET_NAME: preset_data
                }
            }
            
            migrated_count += 1
            logger.debug(f"迁移站点配置: {domain} → 预设格式")
        
        if migrated_count > 0:
            self._save_config()
            logger.info(f"✅ 已迁移 {migrated_count} 个站点配置为预设格式")
    
    
    def _cleanup_preset_residuals(self):
        """
        清理站点配置中预设外的残留字段
        
        当站点已有 presets 结构时，顶层不应再有 selectors/workflow/file_paste 等字段。
        这些残留通常由旧版 bug 或手动编辑产生。
        """
        cleaned_count = 0
        default_fixed_count = 0
        
        for domain in list(self.sites.keys()):
            if domain.startswith('_'):
                continue
            
            site_config = self.sites[domain]
            
            # 只处理已有 presets 结构的站点
            if "presets" not in site_config:
                continue
            
            # 找出预设外的残留字段
            residual_keys = []
            for key in list(site_config.keys()):
                if key == "presets":
                    continue
                if key in PRESET_FIELDS:
                    residual_keys.append(key)
            
            # 删除残留
            for key in residual_keys:
                del site_config[key]
                cleaned_count += 1
                logger.debug(f"清理残留: {domain}.{key}")

            if self._normalize_site_default_preset(domain, site_config):
                default_fixed_count += 1
                logger.debug(f"修正默认预设: {domain} -> {site_config.get('default_preset')}")

        if cleaned_count > 0 or default_fixed_count > 0:
            self._save_config()
            logger.info(
                f"✅ 已清理 {cleaned_count} 个预设外残留字段，"
                f"修正 {default_fixed_count} 个站点默认预设"
            )

    def _resolve_default_preset_name(self, site: Dict[str, Any]) -> Optional[str]:
        """解析站点有效默认预设名（不修改原对象）"""
        presets = site.get("presets", {})
        if not presets:
            return None

        configured_default = site.get("default_preset")
        if isinstance(configured_default, str):
            configured_default = configured_default.strip()
        else:
            configured_default = None

        if configured_default and configured_default in presets:
            return configured_default

        if DEFAULT_PRESET_NAME in presets:
            return DEFAULT_PRESET_NAME

        return next(iter(presets))

    def _normalize_site_default_preset(self, domain: str, site: Dict[str, Any]) -> bool:
        """
        规范化站点 default_preset 字段

        Returns:
            是否发生修改
        """
        return self._sync_site_default_preset_state(domain, site)
    
    def _get_site_data(self, domain: str, preset_name: str = None) -> Optional[Dict]:
        """
        获取指定站点的预设配置数据（可变引用）
        
        查找顺序:
        1. 指定的 preset_name
        2. 默认预设 "主预设"
        3. 第一个可用预设
        
        Args:
            domain: 站点域名
            preset_name: 预设名称，None 则使用默认
            
        Returns:
            预设配置字典的引用（可直接修改），或 None
        """
        if domain not in self.sites:
            return None
        
        site = self.sites[domain]
        presets = site.get("presets", {})
        
        if not presets:
            return None
        
        resolved_default = self._resolve_default_preset_name(site)
        target = preset_name or resolved_default
        resolved_target = self._resolve_preset_alias_key(target, presets)
        if resolved_target != target:
            logger.debug(f"预设别名命中: '{target}' -> '{resolved_target}'")
            target = resolved_target

        # 1. 尝试精确匹配
        if target and target in presets:
            return presets[target]

        # 2. 回退到站点默认预设
        if resolved_default and resolved_default in presets:
            logger.debug(f"预设 '{target}' 不存在，回退到站点默认预设 '{resolved_default}'")
            return presets[resolved_default]

        # 3. 回退到主预设
        if DEFAULT_PRESET_NAME in presets:
            logger.debug(f"预设 '{target}' 不存在，回退到 '{DEFAULT_PRESET_NAME}'")
            return presets[DEFAULT_PRESET_NAME]

        # 4. 使用第一个可用预设
        first_key = next(iter(presets))
        logger.warning(f"默认预设不存在，使用第一个预设: '{first_key}'")
        return presets[first_key]

    @staticmethod
    def _resolve_preset_alias_key(target: Any, presets: Dict[str, Any]) -> str:
        """兼容历史命名：允许不带“预设_”前缀的名字命中真实预设键。"""
        normalized = str(target or "").strip()
        if not normalized or not isinstance(presets, dict):
            return normalized

        if normalized in presets:
            return normalized

        candidates = []
        if normalized.startswith("预设_"):
            stripped = normalized[len("预设_"):].strip()
            if stripped:
                candidates.append(stripped)
        else:
            candidates.append(f"预设_{normalized}")

        for candidate in candidates:
            if candidate in presets:
                return candidate

        return normalized
    
    def _get_site_data_readonly(self, domain: str, preset_name: str = None) -> Optional[Dict]:
        """获取预设配置的深拷贝（只读用途）"""
        data = self._get_site_data(domain, preset_name)
        if data is None:
            return None
        return copy.deepcopy(data)
    
    def list_presets(self, domain: str) -> List[str]:
        """获取指定站点的所有预设名称"""
        self.refresh_if_changed()
        
        if domain not in self.sites:
            return []
        
        site = self.sites[domain]
        presets = site.get("presets", {})
        return list(presets.keys())

    def get_default_preset(self, domain: str) -> Optional[str]:
        """获取指定站点的默认预设名称（已解析回退）"""
        self.refresh_if_changed()

        site = self.sites.get(domain)
        if not site:
            return None

        return self._resolve_default_preset_name(site)

    def _extract_startup_url_from_script(self, script: str) -> str:
        text = str(script or "").strip()
        if not text:
            return ""

        for pattern in _SITE_STARTUP_JS_PATTERNS:
            match = pattern.search(text)
            if match:
                return str(match.group(1) or "").strip()
        return ""

    def _normalize_startup_url(self, domain: str, raw_url: str) -> str:
        normalized_domain = str(domain or "").strip().lower().strip(".")
        text = str(raw_url or "").strip()
        if not normalized_domain:
            return text
        if not text:
            return f"https://{normalized_domain}"
        if text.startswith(("http://", "https://")):
            return text
        return urljoin(f"https://{normalized_domain}", text)

    def _infer_site_startup_url(self, domain: str, site: Dict[str, Any]) -> str:
        preset_name = self._resolve_default_preset_name(site)
        preset_data = self._get_site_data_readonly(domain, preset_name)
        workflow = preset_data.get("workflow", []) if isinstance(preset_data, dict) else []

        if isinstance(workflow, list):
            for step in workflow:
                if str((step or {}).get("action") or "").strip().upper() != "JS_EXEC":
                    continue
                startup_url = self._extract_startup_url_from_script((step or {}).get("value"))
                if startup_url:
                    return self._normalize_startup_url(domain, startup_url)

        return self._normalize_startup_url(domain, "")

    def get_site_catalog_entry(self, domain: str, fallback_order: int = 0) -> Optional[Dict[str, Any]]:
        self.refresh_if_changed()

        site = self.sites.get(domain)
        if not isinstance(site, dict) or str(domain or "").startswith("_"):
            return None

        rule = get_site_rule(domain)
        startup_url = rule.get("startup_url") or self._infer_site_startup_url(domain, site)
        display_name = str(rule.get("display_name") or domain).strip() or str(domain)
        card_id = str(rule.get("card_id") or derive_site_card_id(domain)).strip() or derive_site_card_id(domain)
        guide_priority = rule.get("guide_priority")
        if not isinstance(guide_priority, int):
            guide_priority = int(fallback_order)

        entry = {
            "domain": str(domain),
            "display_name": display_name,
            "url": self._normalize_startup_url(domain, startup_url),
            "card_id": card_id,
            "guide_priority": guide_priority,
        }

        if isinstance(rule.get("route_aliases"), list):
            entry["route_aliases"] = [str(item) for item in rule.get("route_aliases", [])]
        if "stealth_default" in rule:
            entry["stealth_default"] = bool(rule.get("stealth_default"))

        return entry

    def list_site_catalog(self) -> List[Dict[str, Any]]:
        self.refresh_if_changed()

        ordered_domains = [
            domain for domain, site in self.sites.items()
            if not domain.startswith("_") and isinstance(site, dict)
        ]
        entries: List[Dict[str, Any]] = []

        for index, domain in enumerate(ordered_domains):
            entry = self.get_site_catalog_entry(domain, fallback_order=index)
            if entry:
                entries.append(entry)

        entries.sort(key=lambda item: (int(item.get("guide_priority", 0)), str(item.get("domain") or "")))
        return entries

    def get_site_advanced_config(self, domain: str) -> Dict[str, Any]:
        """获取站点级高级配置。"""
        self.refresh_if_changed()

        site = self.sites.get(domain)
        if not site:
            return get_default_site_advanced_config()

        raw_config = site.get("advanced") if isinstance(site, dict) else None
        if not isinstance(raw_config, dict):
            raw_config = {}

        return {
            **get_default_site_advanced_config(),
            **copy.deepcopy(raw_config),
        }

    def set_site_advanced_config(self, domain: str, config: Dict[str, Any]) -> bool:
        """设置站点级高级配置。"""
        self.refresh_if_changed()

        site = self.sites.get(domain)
        if not site:
            logger.warning(f"站点不存在: {domain}")
            return False

        normalized = {
            **get_default_site_advanced_config(),
            **copy.deepcopy(config or {}),
        }
        normalized["independent_cookies"] = bool(normalized.get("independent_cookies", False))
        normalized["independent_cookies_auto_takeover"] = bool(
            normalized.get("independent_cookies_auto_takeover", False)
        )

        site["advanced"] = normalized
        return self._save_config()

    def set_default_preset(self, domain: str, preset_name: str) -> bool:
        """设置指定站点的默认预设"""
        self.refresh_if_changed()

        site = self.sites.get(domain)
        if not site:
            return False

        presets = site.get("presets", {})
        if preset_name not in presets:
            logger.warning(f"默认预设设置失败，预设不存在: {domain}/{preset_name}")
            return False

        persisted_default = self._get_persisted_default_preset(domain, site)
        if persisted_default == preset_name:
            self._local_default_presets.pop(domain, None)
        else:
            self._local_default_presets[domain] = preset_name
        site["default_preset"] = preset_name
        if not self._save_local_site_overrides():
            return False
        logger.info(f"✅ 站点 {domain} 默认预设已设置为: '{preset_name}'（仅本地覆盖）")
        return True
    
    def create_preset(self, domain: str, new_name: str, 
                      source_name: str = None) -> bool:
        """
        创建新预设（克隆自现有预设）
        
        Args:
            domain: 站点域名
            new_name: 新预设名称
            source_name: 要克隆的源预设名称，None 则克隆主预设
        
        Returns:
            是否成功
        """
        self.refresh_if_changed()
        
        if domain not in self.sites:
            logger.warning(f"站点不存在: {domain}")
            return False
        
        site = self.sites[domain]
        presets = site.get("presets", {})
        
        if new_name in presets:
            logger.warning(f"预设已存在: {new_name}")
            return False
        
        # 获取源预设
        source = source_name or DEFAULT_PRESET_NAME
        source_data = presets.get(source)
        
        if not source_data:
            # 尝试第一个可用预设
            if presets:
                source = next(iter(presets))
                source_data = presets[source]
            else:
                logger.warning(f"没有可克隆的源预设")
                return False
        
        # 深拷贝创建新预设
        presets[new_name] = copy.deepcopy(source_data)
        self._normalize_site_default_preset(domain, site)
        self._save_config()
        
        logger.info(f"✅ 站点 {domain} 创建预设: '{new_name}' (克隆自 '{source}')")
        return True

    @staticmethod
    def _build_preset_rename_map(old_name: Any, new_name: Any) -> Dict[str, str]:
        old_raw = str(old_name or "").strip()
        new_raw = str(new_name or "").strip()
        if not old_raw or not new_raw:
            return {}

        def _strip_prefix(value: str) -> str:
            if value.startswith("预设_"):
                return value[len("预设_"):].strip()
            return value

        def _add_prefix(value: str) -> str:
            return value if value.startswith("预设_") else f"预设_{value}"

        mapping: Dict[str, str] = {}
        old_plain = _strip_prefix(old_raw)
        new_plain = _strip_prefix(new_raw)
        old_prefixed = _add_prefix(old_raw)
        new_prefixed = _add_prefix(new_raw)

        for src, dst in (
            (old_raw, new_raw),
            (old_plain, new_plain),
            (old_prefixed, new_prefixed),
        ):
            src_text = str(src or "").strip()
            dst_text = str(dst or "").strip()
            if src_text and dst_text:
                mapping[src_text] = dst_text

        return mapping

    @staticmethod
    def _command_targets_domain(command: Dict[str, Any], domain: str) -> bool:
        normalized_domain = str(domain or "").strip().lower()
        if not normalized_domain or not isinstance(command, dict):
            return False

        trigger = command.get("trigger", {}) or {}
        command_domain = str(trigger.get("domain", "") or "").strip().lower()
        if not command_domain:
            return False

        return (
            command_domain == normalized_domain
            or command_domain.endswith(f".{normalized_domain}")
            or normalized_domain.endswith(f".{command_domain}")
        )

    def _rename_preset_references_in_commands(
        self,
        domain: str,
        rename_map: Dict[str, str],
    ) -> int:
        if not rename_map:
            return 0

        commands = self._load_commands_file()
        if not commands:
            return 0

        updated = 0
        for command in commands:
            if not self._command_targets_domain(command, domain):
                continue

            actions = command.get("actions", [])
            if not isinstance(actions, list):
                continue

            for action in actions:
                if not isinstance(action, dict):
                    continue
                current_preset = str(action.get("preset_name", "") or "").strip()
                replacement = rename_map.get(current_preset)
                if not replacement or replacement == current_preset:
                    continue
                action["preset_name"] = replacement
                updated += 1

        if updated > 0:
            self._save_commands_file(commands)

        return updated

    def _rename_preset_references_in_active_tabs(
        self,
        domain: str,
        rename_map: Dict[str, str],
    ) -> int:
        if not rename_map:
            return 0

        try:
            from app.core import browser as browser_module

            instance = getattr(browser_module, "_browser_instance", None)
            if instance is None:
                return 0

            pool = getattr(instance, "_tab_pool", None)
            if pool is None or not hasattr(pool, "get_sessions_snapshot"):
                return 0

            updated = 0
            for session in pool.get_sessions_snapshot():
                session_domain = str(getattr(session, "current_domain", "") or "").strip().lower()
                if session_domain != str(domain or "").strip().lower():
                    continue

                current_preset = str(getattr(session, "preset_name", "") or "").strip()
                replacement = rename_map.get(current_preset)
                if not replacement or replacement == current_preset:
                    continue

                session.preset_name = replacement
                updated += 1

            return updated
        except Exception as e:
            logger.debug(f"同步活动标签页预设引用失败（忽略）: {e}")
            return 0
    
    def delete_preset(self, domain: str, preset_name: str) -> bool:
        """
        删除预设（不允许删除最后一个预设）
        
        Args:
            domain: 站点域名
            preset_name: 要删除的预设名称
        
        Returns:
            是否成功
        """
        self.refresh_if_changed()
        
        if domain not in self.sites:
            return False
        
        site = self.sites[domain]
        presets = site.get("presets", {})
        
        if preset_name not in presets:
            logger.warning(f"预设不存在: {preset_name}")
            return False
        
        if len(presets) <= 1:
            logger.warning(f"不能删除最后一个预设")
            return False
        
        del presets[preset_name]
        self._normalize_site_default_preset(domain, site)
        self._save_config()
        
        logger.info(f"✅ 站点 {domain} 删除预设: '{preset_name}'")
        return True
    
    def rename_preset(self, domain: str, old_name: str, new_name: str) -> bool:
        """重命名预设"""
        self.refresh_if_changed()
        
        if domain not in self.sites:
            return False
        
        site = self.sites[domain]
        presets = site.get("presets", {})

        resolved_old_name = self._resolve_preset_alias_key(old_name, presets)
        if resolved_old_name not in presets:
            return False
        
        if new_name in presets:
            logger.warning(f"预设名已存在: {new_name}")
            return False

        rename_map = self._build_preset_rename_map(resolved_old_name, new_name)
        default_preset = site.get("default_preset")
        local_default = str(self._local_default_presets.get(domain, "") or "").strip()

        # 保持顺序：创建有序副本
        new_presets = {}
        for key, value in presets.items():
            if key == resolved_old_name:
                new_presets[new_name] = value
            else:
                new_presets[key] = value

        site["presets"] = new_presets
        if default_preset == resolved_old_name:
            site["default_preset"] = new_name
        if local_default:
            local_replacement = rename_map.get(local_default)
            if local_replacement:
                self._local_default_presets[domain] = local_replacement
        self._normalize_site_default_preset(domain, site)
        if not self._save_config():
            return False

        updated_command_refs = self._rename_preset_references_in_commands(domain, rename_map)
        updated_tab_refs = self._rename_preset_references_in_active_tabs(domain, rename_map)
        
        logger.info(
            f"站点 {domain} 重命名预设: '{resolved_old_name}' → '{new_name}' "
            f"(命令引用同步 {updated_command_refs} 处, 活动标签页同步 {updated_tab_refs} 处)"
        )
        return True

    # ================= 预设级 Getter/Setter =================
    
    def get_preset_selectors(self, domain: str, preset_name: str = None) -> Dict:
        """获取指定预设的选择器配置"""
        data = self._get_site_data_readonly(domain, preset_name)
        return data.get("selectors", {}) if data else {}
    
    def set_preset_selectors(self, domain: str, selectors: Dict, 
                             preset_name: str = None) -> bool:
        """设置指定预设的选择器配置"""
        self.refresh_if_changed()
        data = self._get_site_data(domain, preset_name)
        if data is None:
            return False
        data["selectors"] = selectors
        self._save_config()
        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 选择器已更新")
        return True
    
    def get_preset_workflow(self, domain: str, preset_name: str = None) -> List:
        """获取指定预设的工作流配置"""
        data = self._get_site_data_readonly(domain, preset_name)
        return data.get("workflow", DEFAULT_WORKFLOW) if data else DEFAULT_WORKFLOW
    
    def set_preset_workflow(self, domain: str, workflow: List, 
                            preset_name: str = None) -> bool:
        """设置指定预设的工作流配置"""
        self.refresh_if_changed()
        data = self._get_site_data(domain, preset_name)
        if data is None:
            return False
        data["workflow"] = workflow
        self._save_config()
        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 工作流已更新")
        return True    
    # ================= 站点配置管理 =================
    
    def list_sites(self) -> Dict[str, Any]:
        """获取所有站点配置（过滤内部键）"""
        self.refresh_if_changed()
        
        return {
            domain: config 
            for domain, config in self.sites.items() 
            if not domain.startswith('_')
        }
    
    def get_site_config(self, domain: str, html_content: str, 
                        preset_name: str = None) -> Optional[SiteConfig]:
        """
        获取站点配置（缓存 + AI 分析）
        
        Args:
            domain: 站点域名
            html_content: 页面 HTML（用于 AI 分析未知站点）
            preset_name: 预设名称，None 则使用默认预设
        """
        self.refresh_if_changed()

        if domain in self.sites:
            config = self._get_site_data(domain, preset_name)
            
            if config is None:
                logger.warning(f"站点 {domain} 无可用预设")
                return None
            
            # 补充缺失字段
            changed = False
            if "workflow" not in config:
                config["workflow"] = DEFAULT_WORKFLOW
                changed = True
            
            if "image_extraction" not in config:
                config["image_extraction"] = get_default_image_extraction_config()
                changed = True
                            
            if "file_paste" not in config:
                config["file_paste"] = get_default_file_paste_config()
                changed = True
            
            if changed:
                self._save_config()
            
            used_preset = preset_name or self.get_default_preset(domain) or DEFAULT_PRESET_NAME
            logger.debug(f"使用缓存配置: {domain} [预设: {used_preset}]")
            return copy.deepcopy(config)
        
        logger.info(f"🔍 未知域名 {domain}，启动 AI 识别...")
        
        clean_html = self.html_cleaner.clean(html_content)
        selectors = self.ai_analyzer.analyze(clean_html)
        
        if selectors:
            selectors = self.validator.validate(selectors)
            
            new_preset: SiteConfig = {
                "selectors": selectors,
                "workflow": DEFAULT_WORKFLOW,
                "stealth": self._guess_stealth(domain),
                "stream_config": copy.deepcopy(get_default_stream_config()),
                "image_extraction": get_default_image_extraction_config(),
                "file_paste": get_default_file_paste_config()
            }
            
            self.sites[domain] = {
                "default_preset": DEFAULT_PRESET_NAME,
                "presets": {
                    DEFAULT_PRESET_NAME: new_preset
                }
            }
            self._save_config()
            
            logger.info(f"✅ 配置已生成并保存: {domain}")
            return copy.deepcopy(new_preset)
        
        logger.warning(f"⚠️  AI 分析失败，使用通用回退配置: {domain}")
        fallback_selectors = self.global_config.get_fallback_selectors()
        
        fallback_preset: SiteConfig = {
            "selectors": fallback_selectors,
            "workflow": DEFAULT_WORKFLOW,
            "stealth": False,
            "stream_config": copy.deepcopy(get_default_stream_config()),
            "image_extraction": get_default_image_extraction_config(),
            "file_paste": get_default_file_paste_config()
        }
        
        self.sites[domain] = {
            "default_preset": DEFAULT_PRESET_NAME,
            "presets": {
                DEFAULT_PRESET_NAME: fallback_preset
            }
        }
        self._save_config()
        
        return copy.deepcopy(fallback_preset)
    
    def delete_site_config(self, domain: str) -> bool:
        """删除指定站点配置"""
        self.refresh_if_changed()
        
        if domain in self.sites:
            del self.sites[domain]
            self._save_config()
            logger.info(f"已删除配置: {domain}")
            return True
        return False
    
    def _guess_stealth(self, domain: str) -> bool:
        """Guess whether stealth mode should default to enabled."""
        rule = get_site_rule(domain)
        if "stealth_default" in rule:
            enabled = bool(rule.get("stealth_default"))
            if enabled:
                logger.info(f"检测到默认启用隐身模式的域名: {domain}")
            return enabled
        return False
    
    def migrate_site_configs(self):
        """迁移旧版站点配置，补充各预设中缺失的字段"""
        migrated_count = 0
        default_image_config = get_default_image_extraction_config()
        default_file_paste = get_default_file_paste_config()
        obsolete_stream_keys = {
            "silence_threshold",
            "initial_wait",
            "enable_wrapper_search",
            "rerender_wait",
            "content_shrink_tolerance",
        }
        obsolete_network_keys = {"first_response_timeout"}
        
        for domain, site_config in self.sites.items():
            if domain.startswith("_"):
                continue
            
            presets = site_config.get("presets", {})
            
            for preset_name, preset_data in presets.items():
                if "image_extraction" not in preset_data:
                    preset_data["image_extraction"] = copy.deepcopy(default_image_config)
                    migrated_count += 1
                    logger.debug(f"迁移: {domain}/{preset_name} (添加 image_extraction)")
                else:
                    normalized_image_config = self._validate_image_config(preset_data.get("image_extraction", {}))
                    if normalized_image_config != preset_data.get("image_extraction"):
                        preset_data["image_extraction"] = normalized_image_config
                        migrated_count += 1
                        logger.debug(f"迁移: {domain}/{preset_name} (规范化 image_extraction)")
                
                if "file_paste" not in preset_data:
                    preset_data["file_paste"] = default_file_paste.copy()
                    migrated_count += 1
                    logger.debug(f"迁移: {domain}/{preset_name} (添加 file_paste)")

                stream_config = preset_data.get("stream_config")
                if isinstance(stream_config, dict):
                    removed_stream = False
                    for key in list(obsolete_stream_keys):
                        if key in stream_config:
                            del stream_config[key]
                            removed_stream = True

                    network_config = stream_config.get("network")
                    removed_network = False
                    if isinstance(network_config, dict):
                        for key in list(obsolete_network_keys):
                            if key in network_config:
                                del network_config[key]
                                removed_network = True

                    if removed_stream or removed_network:
                        migrated_count += 1
                        logger.debug(f"迁移: {domain}/{preset_name} (清理废弃的流式配置字段)")
        
        if migrated_count > 0:
            self._save_config()
            logger.info(f"已迁移 {migrated_count} 个预设配置")
        
        return migrated_count
    
    # ================= 图片配置管理 =================
    
    
    def get_site_image_config(self, domain: str, preset_name: str = None) -> Dict:
        """获取站点的图片提取配置"""
        self.refresh_if_changed()
        
        default_config = get_default_image_extraction_config()
        
        data = self._get_site_data(domain, preset_name)
        if data is None:
            return copy.deepcopy(default_config)
        
        image_config = data.get("image_extraction", {})
        return self._validate_image_config(image_config)
    
    def set_site_image_config(self, domain: str, config: Dict, 
                              preset_name: str = None) -> bool:
        """设置站点的图片提取配置"""
        self.refresh_if_changed()
        
        data = self._get_site_data(domain, preset_name)
        if data is None:
            logger.warning(f"站点或预设不存在: {domain}/{preset_name}")
            return False
        
        validated = self._validate_image_config(config)
        
        data["image_extraction"] = validated
        self._save_config()
        
        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 多模态提取配置已更新")
        return True
    
    def _validate_image_config(self, config: Dict) -> Dict:
        """验证并规范化多模态提取配置"""
        default = get_default_image_extraction_config()
        result = copy.deepcopy(default)
        
        if not config:
            return result
        
        raw_modalities = config.get("modalities")
        if isinstance(raw_modalities, dict):
            normalized_modalities = copy.deepcopy(result.get("modalities") or {})
            for key in ("image", "audio", "video"):
                if key in raw_modalities:
                    normalized_modalities[key] = bool(raw_modalities[key])
            result["modalities"] = normalized_modalities

        legacy_enabled = None
        if "enabled" in config:
            legacy_enabled = bool(config["enabled"])
            result["enabled"] = legacy_enabled

        if (
            legacy_enabled is not None
            and not isinstance(raw_modalities, dict)
        ):
            result["modalities"]["image"] = legacy_enabled
        elif legacy_enabled is not None and isinstance(raw_modalities, dict):
            if not legacy_enabled:
                for key in ("image", "audio", "video"):
                    result["modalities"][key] = False
            elif not any(bool(result["modalities"].get(key)) for key in ("image", "audio", "video")):
                result["modalities"]["image"] = True
        
        if "selector" in config and config["selector"]:
            result["selector"] = str(config["selector"]).strip()
            if not result["selector"]:
                result["selector"] = "img"

        if "audio_selector" in config and config["audio_selector"]:
            result["audio_selector"] = str(config["audio_selector"]).strip()
            if not result["audio_selector"]:
                result["audio_selector"] = default["audio_selector"]

        if "video_selector" in config and config["video_selector"]:
            result["video_selector"] = str(config["video_selector"]).strip()
            if not result["video_selector"]:
                result["video_selector"] = default["video_selector"]
        
        if "container_selector" in config:
            val = config["container_selector"]
            result["container_selector"] = str(val).strip() if val else None
        
        if "debounce_seconds" in config:
            try:
                val = float(config["debounce_seconds"])
                result["debounce_seconds"] = max(0, min(val, 30))
            except (ValueError, TypeError):
                pass
        
        if "wait_for_load" in config:
            result["wait_for_load"] = bool(config["wait_for_load"])
        
        if "load_timeout_seconds" in config:
            try:
                val = float(config["load_timeout_seconds"])
                result["load_timeout_seconds"] = max(1, min(val, 60))
            except (ValueError, TypeError):
                pass
        
        if "download_blobs" in config:
            result["download_blobs"] = bool(config["download_blobs"])
        
        if "max_size_mb" in config:
            try:
                val = int(config["max_size_mb"])
                result["max_size_mb"] = max(1, min(val, 100))
            except (ValueError, TypeError):
                pass
        
        if "mode" in config:
            val = str(config["mode"]).lower()
            if val in ("all", "first", "last"):
                result["mode"] = val

        if "audio_capture_enabled" in config:
            result["audio_capture_enabled"] = bool(config["audio_capture_enabled"])

        if "audio_capture_mute_playback" in config:
            result["audio_capture_mute_playback"] = bool(config["audio_capture_mute_playback"])

        if "audio_capture_preload_enabled" in config:
            result["audio_capture_preload_enabled"] = bool(config["audio_capture_preload_enabled"])

        if "audio_capture_reload_before_workflow" in config:
            result["audio_capture_reload_before_workflow"] = bool(config["audio_capture_reload_before_workflow"])

        if "audio_capture_preserve_graph" in config:
            result["audio_capture_preserve_graph"] = bool(config["audio_capture_preserve_graph"])

        if "audio_capture_terminal_settle_seconds" in config:
            try:
                val = float(config["audio_capture_terminal_settle_seconds"])
                result["audio_capture_terminal_settle_seconds"] = max(0.0, min(val, 5.0))
            except (ValueError, TypeError):
                pass

        if "audio_trigger_selector" in config:
            result["audio_trigger_selector"] = str(config["audio_trigger_selector"] or "").strip()

        if "audio_trigger_labels" in config:
            raw_labels = config.get("audio_trigger_labels")
            if isinstance(raw_labels, (list, tuple)):
                labels = [
                    str(item).strip()
                    for item in raw_labels
                    if str(item).strip()
                ]
                if labels:
                    result["audio_trigger_labels"] = labels

        if "audio_capture_max_wait_seconds" in config:
            try:
                val = float(config["audio_capture_max_wait_seconds"])
                result["audio_capture_max_wait_seconds"] = max(1.0, min(val, 120.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_min_wait_seconds" in config:
            try:
                val = float(config["audio_capture_min_wait_seconds"])
                result["audio_capture_min_wait_seconds"] = max(0.2, min(val, 30.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_hard_max_wait_seconds" in config:
            try:
                val = float(config["audio_capture_hard_max_wait_seconds"])
                result["audio_capture_hard_max_wait_seconds"] = max(1.0, min(val, 180.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_estimated_chars_per_second" in config:
            try:
                val = float(config["audio_capture_estimated_chars_per_second"])
                result["audio_capture_estimated_chars_per_second"] = max(1.0, min(val, 20.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_wait_padding_seconds" in config:
            try:
                val = float(config["audio_capture_wait_padding_seconds"])
                result["audio_capture_wait_padding_seconds"] = max(0.0, min(val, 10.0))
            except (ValueError, TypeError):
                pass

        network_capture = dict(result.get("audio_network_capture") or {})
        raw_network_capture = config.get("audio_network_capture")
        if isinstance(raw_network_capture, dict):
            if "enabled" in raw_network_capture:
                network_capture["enabled"] = bool(raw_network_capture["enabled"])
            if "timeout_seconds" in raw_network_capture:
                try:
                    val = float(raw_network_capture["timeout_seconds"])
                    network_capture["timeout_seconds"] = max(0.1, min(val, 15.0))
                except (ValueError, TypeError):
                    pass
            if "transport" in raw_network_capture:
                val = str(raw_network_capture["transport"] or "").strip()
                if val in {"page_websocket_probe"}:
                    network_capture["transport"] = val
            if "extractor" in raw_network_capture:
                val = str(raw_network_capture["extractor"] or "").strip()
                if val in {"voicegenie_ogg_pages"}:
                    network_capture["extractor"] = val
            if "settle_seconds" in raw_network_capture:
                try:
                    val = float(raw_network_capture["settle_seconds"])
                    network_capture["settle_seconds"] = max(0.05, min(val, 5.0))
                except (ValueError, TypeError):
                    pass
            if "url_patterns" in raw_network_capture:
                raw_patterns = raw_network_capture.get("url_patterns")
                if isinstance(raw_patterns, (list, tuple)):
                    patterns = [
                        str(item).strip()
                        for item in raw_patterns
                        if str(item).strip()
                    ]
                    if patterns:
                        network_capture["url_patterns"] = patterns

        # 兼容旧平铺字段，最终统一收口到新对象
        if "audio_network_capture_enabled" in config:
            network_capture["enabled"] = bool(config["audio_network_capture_enabled"])

        if "audio_network_capture_timeout_seconds" in config:
            try:
                val = float(config["audio_network_capture_timeout_seconds"])
                network_capture["timeout_seconds"] = max(0.1, min(val, 15.0))
            except (ValueError, TypeError):
                pass

        if "audio_network_url_patterns" in config:
            raw_patterns = config.get("audio_network_url_patterns")
            if isinstance(raw_patterns, (list, tuple)):
                patterns = [
                    str(item).strip()
                    for item in raw_patterns
                    if str(item).strip()
                ]
                if patterns:
                    network_capture["url_patterns"] = patterns

        result["audio_network_capture"] = network_capture

        if "audio_capture_poll_seconds" in config:
            try:
                val = float(config["audio_capture_poll_seconds"])
                result["audio_capture_poll_seconds"] = max(0.05, min(val, 5.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_silence_seconds" in config:
            try:
                val = float(config["audio_capture_silence_seconds"])
                result["audio_capture_silence_seconds"] = max(0.2, min(val, 30.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_activity_threshold" in config:
            try:
                val = float(config["audio_capture_activity_threshold"])
                result["audio_capture_activity_threshold"] = max(0.0001, min(val, 0.2))
            except (ValueError, TypeError):
                pass

        if "audio_capture_activity_silence_seconds" in config:
            try:
                val = float(config["audio_capture_activity_silence_seconds"])
                result["audio_capture_activity_silence_seconds"] = max(0.2, min(val, 10.0))
            except (ValueError, TypeError):
                pass

        result["enabled"] = any(
            bool((result.get("modalities") or {}).get(key))
            for key in ("image", "audio", "video")
        )
        
        return result
        # ================= 文件粘贴配置管理 =================
    
    def get_site_file_paste_config(self, domain: str, preset_name: str = None) -> dict:
        """获取站点的文件粘贴配置"""
        self.refresh_if_changed()
        
        default_config = get_default_file_paste_config()
        
        data = self._get_site_data(domain, preset_name)
        if data is None:
            return default_config
        
        file_paste_config = data.get("file_paste", {})
        
        result = default_config.copy()
        result.update(file_paste_config)
        
        return result
    
    def set_site_file_paste_config(self, domain: str, config: dict, 
                                    preset_name: str = None) -> bool:
        """设置站点的文件粘贴配置"""
        self.refresh_if_changed()
        
        data = self._get_site_data(domain, preset_name)
        if data is None:
            logger.warning(f"站点或预设不存在: {domain}/{preset_name}")
            return False
        
        validated = self._validate_file_paste_config(config)
        
        data["file_paste"] = validated
        self._save_config()
        
        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 文件粘贴配置已更新")
        return True
    
    def get_all_file_paste_configs(self) -> dict:
        """获取所有站点的文件粘贴配置（使用各站点当前默认预设）"""
        self.refresh_if_changed()
        
        default_config = get_default_file_paste_config()
        result = {}
        
        for domain in self.sites:
            if domain.startswith('_'):
                continue
            
            data = self._get_site_data(domain)
            if data is None:
                continue
            
            file_paste = data.get("file_paste", {})
            merged = default_config.copy()
            merged.update(file_paste)
            result[domain] = merged
        
        return result
    
    def _validate_file_paste_config(self, config: dict) -> dict:
        """验证并规范化文件粘贴配置"""
        default = get_default_file_paste_config()
        result = default.copy()
        
        if not config:
            return result
        
        if "enabled" in config:
            result["enabled"] = bool(config["enabled"])
        
        if "threshold" in config:
            try:
                val = int(config["threshold"])
                result["threshold"] = max(1000, min(val, 10000000))
            except (ValueError, TypeError):
                pass
        
        if "hint_text" in config:
            val = str(config["hint_text"]).strip()
            # 限制长度，避免过长的引导文本
            result["hint_text"] = val[:500] if val else ""
        
        return result
    # ================= 图片预设管理 =================
    
    def list_image_presets(self):
        """列出所有可用的图片配置预设"""
        return self.image_presets.list_presets()

    def get_image_preset(self, domain: str):
        """获取指定站点的预设信息"""
        return self.image_presets.get_preset_for_display(domain)

    def apply_image_preset(self, domain: str, preset_domain: str):
        """将预设配置应用到站点"""
        preset_config = self.image_presets.get_preset(preset_domain)
        
        if not preset_config:
            raise ValueError(f"找不到预设: {preset_domain}")

        return self.set_site_image_config(domain, preset_config)
    
    def reload_presets(self):
        """重新加载图片预设"""
        self.image_presets.reload()
    
    # ================= 提取器管理 =================
    
    def get_site_extractor(self, domain: str, preset_name: str = None):
        """获取站点的提取器实例"""
        self.refresh_if_changed()
        
        data = self._get_site_data(domain, preset_name)
        if data is not None:
            return extractor_manager.get_extractor_for_site(data)
        
        return extractor_manager.get_extractor()
    
    def set_site_extractor(self, domain: str, extractor_id: str, 
                           preset_name: str = None) -> bool:
        """为站点设置提取器"""
        self.refresh_if_changed()
        
        data = self._get_site_data(domain, preset_name)
        if data is None:
            logger.warning(f"站点或预设不存在: {domain}/{preset_name}")
            return False
        
        from app.core.extractors import ExtractorRegistry
        if not ExtractorRegistry.exists(extractor_id):
            logger.error(f"提取器不存在: {extractor_id}")
            return False
        
        data["extractor_id"] = extractor_id
        data["extractor_verified"] = False
        self._save_config()
        
        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 已绑定提取器: {extractor_id}")
        return True
    
    def set_site_extractor_verified(self, domain: str, verified: bool = True, 
                                     preset_name: str = None) -> bool:
        """设置站点提取器验证状态"""
        self.refresh_if_changed()
        
        data = self._get_site_data(domain, preset_name)
        if data is None:
            return False
        
        data["extractor_verified"] = verified
        self._save_config()
        
        return True
    
    # 🆕 ================= 流式配置管理 =================
    
    def get_site_stream_config(self, domain: str, preset_name: str = None) -> Dict[str, Any]:
        """
        获取站点的流式配置
        
        Args:
            domain: 站点域名
            preset_name: 预设名称
        
        Returns:
            完整的流式配置（包含默认值）
        """
        self.refresh_if_changed()
        
        default_config = get_default_stream_config()
        
        data = self._get_site_data(domain, preset_name)
        if data is None:
            return copy.deepcopy(default_config)
        
        stream_config = data.get("stream_config", {})
        
        # 合并默认值
        result = copy.deepcopy(default_config)
        
        # 更新顶层字段
        for key in ["mode", "hard_timeout"]:
            if key in stream_config:
                result[key] = stream_config[key]

        if isinstance(stream_config.get("request_transport"), dict):
            result["request_transport"] = normalize_request_transport_config(
                stream_config.get("request_transport")
            )

        # 处理 send_confirmation 配置
        if isinstance(stream_config.get("send_confirmation"), dict):
            result["send_confirmation"].update(stream_config["send_confirmation"])

        # 处理 attachment_monitor 配置
        if isinstance(stream_config.get("attachment_monitor"), dict):
            result["attachment_monitor"].update(stream_config["attachment_monitor"])
        
        # 处理 network 配置
        if stream_config.get("network"):
            network_default = get_default_network_config()
            network_config = stream_config["network"]
            
            result["network"] = network_default.copy()
            for key in [
                "listen_pattern",
                "stream_match_mode",
                "stream_match_pattern",
                "parser",
                "silence_threshold",
                "response_interval",
            ]:
                if key in network_config:
                    result["network"][key] = network_config[key]
        
        return result
    
    def set_site_stream_config(self, domain: str, config: Dict[str, Any], 
                                preset_name: str = None) -> bool:
        """
        设置站点的流式配置
        
        Args:
            domain: 站点域名
            config: 流式配置（部分或完整）
            preset_name: 预设名称
        
        Returns:
            是否成功
        """
        self.refresh_if_changed()
        
        data = self._get_site_data(domain, preset_name)
        if data is None:
            logger.warning(f"站点或预设不存在: {domain}/{preset_name}")
            return False
        
        # 验证并规范化配置
        validated = self._validate_stream_config(config)
        
        data["stream_config"] = validated
        self._save_config()
        
        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 流式配置已更新 (mode={validated.get('mode')})")
        return True
    
    def _validate_stream_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        验证并规范化流式配置
        
        Args:
            config: 原始配置
        
        Returns:
            规范化后的配置
        """
        result = get_default_stream_config()
        
        if not config:
            return result
        
        mode_explicitly_set = False

        # 验证 mode
        if "mode" in config:
            mode = str(config["mode"]).lower()
            if mode in ("dom", "network"):
                result["mode"] = mode
                mode_explicitly_set = True

        # 验证数值字段
        for key in ["hard_timeout"]:
            if key in config:
                try:
                    val = float(config[key])
                    if key == "hard_timeout":
                        result[key] = max(10, min(val, 600))
                except (ValueError, TypeError):
                    pass

        # 验证 send_confirmation 配置
        if isinstance(config.get("send_confirmation"), dict):
            result["send_confirmation"] = self._validate_send_confirmation_config(
                config["send_confirmation"]
            )

        if isinstance(config.get("request_transport"), dict):
            result["request_transport"] = normalize_request_transport_config(
                config["request_transport"]
            )

        # 验证 attachment_monitor 配置
        if isinstance(config.get("attachment_monitor"), dict):
            result["attachment_monitor"] = self._validate_attachment_monitor_config(
                config["attachment_monitor"]
            )
        
        # 验证 network 配置
        if config.get("network"):
            network_config = self._validate_network_config(config["network"])
            if network_config:
                result["network"] = network_config
                # 仅在调用方没有显式指定 mode 时，才根据 network 配置自动切到 network。
                # 这样切回 DOM 时可以保留 parser/listen_pattern，而不会被后端强制改回 network。
                if (
                    not mode_explicitly_set
                    and network_config.get("parser")
                    and network_config.get("listen_pattern")
                ):
                    result["mode"] = "network"
        
        return result

    def _validate_send_confirmation_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """验证发送成功判定配置。"""
        result = get_default_send_confirmation_config()

        if not isinstance(config, dict):
            return result

        numeric_ranges = {
            "post_click_observe_window": (0.0, 15.0),
            "pre_retry_probe_window": (0.0, 5.0),
            "retry_observe_window": (0.0, 15.0),
            "attachment_observe_window": (0.0, 30.0),
        }
        for key, (minimum, maximum) in numeric_ranges.items():
            if key not in config:
                continue
            try:
                value = float(config[key])
            except (TypeError, ValueError):
                continue
            result[key] = max(minimum, min(value, maximum))

        bool_fields = [
            "trust_network_activity",
            "trust_generating_indicator",
            "trust_send_disabled_with_input_shrink",
        ]
        for key in bool_fields:
            if key not in config:
                continue
            raw_value = config[key]
            if isinstance(raw_value, str):
                lowered = raw_value.strip().lower()
                if lowered in ("1", "true", "yes", "on"):
                    result[key] = True
                    continue
                if lowered in ("0", "false", "no", "off"):
                    result[key] = False
                    continue
                continue
            result[key] = bool(raw_value)

        sensitivity = str(config.get("attachment_sensitivity") or "").strip().lower()
        if sensitivity in {"low", "medium", "high"}:
            result["attachment_sensitivity"] = sensitivity

        return result

    def _validate_attachment_monitor_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """验证附件上传/发送前判定规则。"""
        result = get_default_attachment_monitor_config()

        if not isinstance(config, dict):
            return result

        list_fields = [
            "root_selectors",
            "attachment_selectors",
            "pending_selectors",
            "busy_text_markers",
            "send_button_disabled_markers",
        ]
        for key in list_fields:
            raw_value = config.get(key)
            if raw_value is None:
                continue
            if not isinstance(raw_value, list):
                continue
            cleaned = []
            for item in raw_value:
                value = str(item or "").strip()
                if value and value not in cleaned:
                    cleaned.append(value)
            result[key] = cleaned

        numeric_ranges = {
            "idle_timeout": (0.5, 60.0),
            "hard_max_wait": (1.0, 300.0),
        }
        for key, (minimum, maximum) in numeric_ranges.items():
            if key not in config:
                continue
            try:
                value = float(config[key])
            except (TypeError, ValueError):
                continue
            result[key] = max(minimum, min(value, maximum))

        bool_fields = [
            "require_attachment_present",
            "continue_once_on_unconfirmed_send",
        ]
        for key in bool_fields:
            if key not in config:
                continue
            raw_value = config[key]
            if isinstance(raw_value, str):
                lowered = raw_value.strip().lower()
                if lowered in ("1", "true", "yes", "on"):
                    result[key] = True
                    continue
                if lowered in ("0", "false", "no", "off"):
                    result[key] = False
                    continue
                continue
            result[key] = bool(raw_value)

        return result
    
    def _validate_network_config(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        验证网络监听配置
        
        Args:
            config: 原始网络配置
        
        Returns:
            规范化后的配置，无效则返回 None
        """
        if not config:
            return None
        
        result = get_default_network_config()
        
        # listen_pattern（必填）
        if "listen_pattern" in config:
            pattern = str(config["listen_pattern"]).strip()
            if pattern:
                result["listen_pattern"] = pattern

        if "stream_match_mode" in config:
            match_mode = str(config["stream_match_mode"]).strip().lower()
            if match_mode in {"keyword", "regex"}:
                result["stream_match_mode"] = match_mode

        if "stream_match_pattern" in config:
            result["stream_match_pattern"] = str(config["stream_match_pattern"]).strip()

        # parser（必填，需验证存在性）
        if "parser" in config:
            parser_id = str(config["parser"]).strip()
            if parser_id:
                # 验证解析器是否存在
                if ParserRegistry.exists(parser_id):
                    result["parser"] = parser_id
                else:
                    logger.warning(f"解析器不存在: {parser_id}")
                    # 仍然保存，允许后续添加解析器
                    result["parser"] = parser_id
        
        # 验证数值字段
        for key in ["silence_threshold", "response_interval"]:
            if key in config:
                try:
                    val = float(config[key])
                    if key == "silence_threshold":
                        result[key] = max(0.5, min(val, 30))
                    elif key == "response_interval":
                        result[key] = max(0.1, min(val, 5))
                except (ValueError, TypeError):
                    pass
        
        # 检查是否有有效配置
        if not result["listen_pattern"] or not result["parser"]:
            return None
        
        return result
    
    def list_available_parsers(self) -> List[Dict[str, str]]:
        """
        列出所有可用的响应解析器
        
        Returns:
            解析器信息列表
        """
        return parser_manager.list_parsers()

    def get_extractor_manager(self):
        """获取提取器管理器实例"""
        return extractor_manager

    def get_parser_manager(self):
        """获取解析器管理器实例"""
        return parser_manager
    
    # ================= 元素定义管理 =================
    
    def get_selector_definitions(self) -> List[SelectorDefinition]:
        """获取元素定义列表"""
        return self.global_config.get_selector_definitions()
    
    def set_selector_definitions(self, definitions: List[SelectorDefinition]):
        """设置元素定义列表并保存"""
        self.global_config.set_selector_definitions(definitions)
        
        # 更新验证器的回退选择器
        self.validator.fallback_selectors = self.global_config.get_fallback_selectors()
        
        # 保存配置
        self._save_config()
        
        logger.info(f"元素定义已更新: {len(definitions)} 个")


__all__ = ['ConfigEngine', 'ConfigConstants', 'DEFAULT_WORKFLOW', 'DEFAULT_PRESET_NAME']
