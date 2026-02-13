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
import logging
from typing import Dict, Optional, List, Any
from app.core.parsers import ParserRegistry
from app.models.schemas import (
    SiteConfig,
    WorkflowStep,
    SelectorDefinition,
    get_default_image_extraction_config,
    get_default_file_paste_config
)
from app.services.extractor_manager import extractor_manager
from app.core.parsers import ParserRegistry
from .managers import GlobalConfigManager, ImagePresetsManager
from .processors import HTMLCleaner, SelectorValidator, AIAnalyzer


logger = logging.getLogger('config_engine')
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s [Config] %(message)s', datefmt='%H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ================= 常量配置 =================

class ConfigConstants:
    """配置引擎常量"""
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    CONFIG_FILE = os.getenv("SITES_CONFIG_FILE", os.path.join(_PROJECT_ROOT, "config", "sites.json"))
    IMAGE_PRESETS_FILE = os.path.join(_PROJECT_ROOT, "config", "image_presets.json")
    
    MAX_HTML_CHARS = int(os.getenv("MAX_HTML_CHARS", "120000"))
    TEXT_TRUNCATE_LENGTH = 80
    
    AI_MAX_RETRIES = 3
    AI_RETRY_BASE_DELAY = 1.0
    AI_RETRY_MAX_DELAY = 10.0
    AI_REQUEST_TIMEOUT = 120
    
    STEALTH_DOMAINS = ['lmarena.ai', 'poe.com', 'you.com', 'chatgpt.com']


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
        "hard_timeout": 300,        # 全局硬超时（秒）
        "silence_threshold": 2.5,   # 静默超时（秒）
        "initial_wait": 30.0,       # 初始等待（秒）
        "enable_wrapper_search": True,
        
        # 网络监听配置（可选）
        "network": None
    }


def get_default_network_config() -> Dict[str, Any]:
    """获取默认网络监听配置"""
    return {
        "listen_pattern": "",           # URL 匹配模式（必填）
        "parser": "",                   # 解析器 ID（必填）
        "first_response_timeout": 5.0,  # 首次响应超时（秒）
        "silence_threshold": 3.0,       # 静默超时（秒）
        "response_interval": 0.5        # 轮询间隔（秒）
    }

# ================= 配置引擎主类 =================

class ConfigEngine:
    """配置引擎主类"""
    
    def __init__(self):
        self.config_file = ConfigConstants.CONFIG_FILE
        self.last_mtime = 0.0
        self.sites: Dict[str, SiteConfig] = {}
        
        # 子管理器
        self.global_config = GlobalConfigManager()
        self.image_presets = ImagePresetsManager(ConfigConstants.IMAGE_PRESETS_FILE)
        
        # 加载配置
        self._load_config()
        
        # 处理器
        self.html_cleaner = HTMLCleaner()
        self.validator = SelectorValidator(self.global_config.get_fallback_selectors())
        self.ai_analyzer = AIAnalyzer(self.global_config)
        
        # 迁移旧配置
        self.migrate_site_configs()
        
        logger.debug(f"配置引擎已初始化，已加载 {len(self.sites)} 个站点配置")
    
    # ================= 配置加载与保存 =================
    
    def _load_config(self):
        """初始化加载配置文件"""
        if not os.path.exists(self.config_file):
            logger.info(f"配置文件 {self.config_file} 不存在，将创建新文件")
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
                logger.debug(f"已加载配置文件: {self.config_file} (mtime: {self.last_mtime})")
        
        except json.JSONDecodeError as e:
            logger.error(f"配置文件格式错误: {e}")
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
    
    def refresh_if_changed(self):
        """检查文件是否变化，如果变化则重载"""
        if not os.path.exists(self.config_file):
            return

        try:
            current_mtime = os.path.getmtime(self.config_file)
            if current_mtime != self.last_mtime:
                logger.info(f"⚡ 检测到配置文件变化 (new mtime: {current_mtime})")
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
            logger.info(f"✅ 配置已热重载 (Sites: {len(self.sites)})")
            
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
            # 构建完整配置（包含 _global）
            full_config = {
                "_global": self.global_config.to_dict(),
                **self.sites
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
    
    # ================= 站点配置管理 =================
    
    def list_sites(self) -> Dict[str, Any]:
        """获取所有站点配置（过滤内部键）"""
        self.refresh_if_changed()
        
        return {
            domain: config 
            for domain, config in self.sites.items() 
            if not domain.startswith('_')
        }
    
    def get_site_config(self, domain: str, html_content: str) -> Optional[SiteConfig]:
        """获取站点配置（缓存 + AI 分析）"""
        self.refresh_if_changed()

        if domain in self.sites:
            config = self.sites[domain]
            
            if "workflow" not in config:
                config["workflow"] = DEFAULT_WORKFLOW
                self.sites[domain] = config
                self._save_config()
            
            if "image_extraction" not in config:
                config["image_extraction"] = get_default_image_extraction_config()
                self.sites[domain] = config
                self._save_config()
                            
            if "file_paste" not in config:
                config["file_paste"] = get_default_file_paste_config()
                self.sites[domain] = config
                self._save_config()
            
            logger.debug(f"使用缓存配置: {domain}")
            return copy.deepcopy(config)
        
        logger.info(f"🔍 未知域名 {domain}，启动 AI 识别...")
        
        clean_html = self.html_cleaner.clean(html_content)
        selectors = self.ai_analyzer.analyze(clean_html)
        
        if selectors:
            selectors = self.validator.validate(selectors)
            
            new_config: SiteConfig = {
                "selectors": selectors,
                "workflow": DEFAULT_WORKFLOW,
                "stealth": self._guess_stealth(domain),
                "stream_config": {
                    "silence_threshold": 2.5,
                    "initial_wait": 30.0,
                    "enable_wrapper_search": True
                },
                "image_extraction": get_default_image_extraction_config()
            }
            
            self.sites[domain] = new_config
            self._save_config()
            
            logger.info(f"✅ 配置已生成并保存: {domain}")
            return copy.deepcopy(new_config)
        
        logger.warning(f"⚠️  AI 分析失败，使用通用回退配置: {domain}")
        fallback_selectors = self.global_config.get_fallback_selectors()
        
        fallback_config: SiteConfig = {
            "selectors": fallback_selectors,
            "workflow": DEFAULT_WORKFLOW,
            "stealth": False,
            "stream_config": {
                "silence_threshold": 2.5,
                "initial_wait": 30.0,
                "enable_wrapper_search": True
            },
            "image_extraction": get_default_image_extraction_config()
        }
        
        self.sites[domain] = fallback_config
        self._save_config()
        
        return copy.deepcopy(fallback_config)
    
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
        """推测是否需要隐身模式"""
        for stealth_domain in ConfigConstants.STEALTH_DOMAINS:
            if stealth_domain in domain:
                logger.info(f"检测到需要隐身模式的域名: {domain}")
                return True
        return False
    
    def migrate_site_configs(self):
        """迁移旧版站点配置，补充缺失的 image_extraction 字段"""
        migrated_count = 0
        default_image_config = get_default_image_extraction_config()
        
        for domain, site_config in self.sites.items():
            if domain == "_global":
                continue
            
            if "image_extraction" not in site_config:
                site_config["image_extraction"] = default_image_config.copy()
                migrated_count += 1
                logger.debug(f"迁移站点配置: {domain} (添加 image_extraction)")
            
            if "file_paste" not in site_config:
                site_config["file_paste"] = get_default_file_paste_config()
                migrated_count += 1
                logger.debug(f"迁移站点配置: {domain} (添加 file_paste)")
        
        if migrated_count > 0:
            self._save_config()
            logger.info(f"已迁移 {migrated_count} 个站点配置")
        
        return migrated_count
    
    # ================= 图片配置管理 =================
    
    def get_site_image_config(self, domain: str) -> Dict:
        """获取站点的图片提取配置"""
        self.refresh_if_changed()
        
        default_config = get_default_image_extraction_config()
        
        if domain not in self.sites:
            return default_config
        
        site_config = self.sites[domain]
        image_config = site_config.get("image_extraction", {})
        
        result = default_config.copy()
        result.update(image_config)
        
        return result
    
    def set_site_image_config(self, domain: str, config: Dict) -> bool:
        """设置站点的图片提取配置"""
        self.refresh_if_changed()
        
        if domain not in self.sites:
            logger.warning(f"站点不存在: {domain}")
            return False
        
        validated = self._validate_image_config(config)
        
        self.sites[domain]["image_extraction"] = validated
        self._save_config()
        
        logger.info(f"站点 {domain} 图片提取配置已更新")
        return True
    
    def _validate_image_config(self, config: Dict) -> Dict:
        """验证并规范化图片提取配置"""
        default = get_default_image_extraction_config()
        result = default.copy()
        
        if not config:
            return result
        
        if "enabled" in config:
            result["enabled"] = bool(config["enabled"])
        
        if "selector" in config and config["selector"]:
            result["selector"] = str(config["selector"]).strip()
            if not result["selector"]:
                result["selector"] = "img"
        
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
        
        return result
        # ================= 文件粘贴配置管理 =================
    
    def get_site_file_paste_config(self, domain: str) -> dict:
        """获取站点的文件粘贴配置"""
        self.refresh_if_changed()
        
        default_config = get_default_file_paste_config()
        
        if domain not in self.sites:
            return default_config
        
        site_config = self.sites[domain]
        file_paste_config = site_config.get("file_paste", {})
        
        result = default_config.copy()
        result.update(file_paste_config)
        
        return result
    
    def set_site_file_paste_config(self, domain: str, config: dict) -> bool:
        """设置站点的文件粘贴配置"""
        self.refresh_if_changed()
        
        if domain not in self.sites:
            logger.warning(f"站点不存在: {domain}")
            return False
        
        validated = self._validate_file_paste_config(config)
        
        self.sites[domain]["file_paste"] = validated
        self._save_config()
        
        logger.info(f"站点 {domain} 文件粘贴配置已更新 (enabled={validated.get('enabled')}, threshold={validated.get('threshold')})")
        return True
    
    def get_all_file_paste_configs(self) -> dict:
        """获取所有站点的文件粘贴配置"""
        self.refresh_if_changed()
        
        default_config = get_default_file_paste_config()
        result = {}
        
        for domain in self.sites:
            if domain.startswith('_'):
                continue
            site_config = self.sites[domain]
            file_paste = site_config.get("file_paste", {})
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
    
    def get_site_extractor(self, domain: str):
        """获取站点的提取器实例"""
        self.refresh_if_changed()
        
        if domain in self.sites:
            site_config = self.sites[domain]
            return extractor_manager.get_extractor_for_site(site_config)
        
        return extractor_manager.get_extractor()
    
    def set_site_extractor(self, domain: str, extractor_id: str) -> bool:
        """为站点设置提取器"""
        self.refresh_if_changed()
        
        if domain not in self.sites:
            logger.warning(f"站点不存在: {domain}")
            return False
        
        from app.core.extractors import ExtractorRegistry
        if not ExtractorRegistry.exists(extractor_id):
            logger.error(f"提取器不存在: {extractor_id}")
            return False
        
        self.sites[domain]["extractor_id"] = extractor_id
        self.sites[domain]["extractor_verified"] = False
        self._save_config()
        
        logger.info(f"站点 {domain} 已绑定提取器: {extractor_id}")
        return True
    
    def set_site_extractor_verified(self, domain: str, verified: bool = True) -> bool:
        """设置站点提取器验证状态"""
        self.refresh_if_changed()
        
        if domain not in self.sites:
            return False
        
        self.sites[domain]["extractor_verified"] = verified
        self._save_config()
        
        return True
    
    # 🆕 ================= 流式配置管理 =================
    
    def get_site_stream_config(self, domain: str) -> Dict[str, Any]:
        """
        获取站点的流式配置
        
        Args:
            domain: 站点域名
        
        Returns:
            完整的流式配置（包含默认值）
        """
        self.refresh_if_changed()
        
        default_config = get_default_stream_config()
        
        if domain not in self.sites:
            return default_config
        
        site_config = self.sites[domain]
        stream_config = site_config.get("stream_config", {})
        
        # 合并默认值
        result = default_config.copy()
        
        # 更新顶层字段
        for key in ["mode", "hard_timeout", "silence_threshold", 
                    "initial_wait", "enable_wrapper_search"]:
            if key in stream_config:
                result[key] = stream_config[key]
        
        # 处理 network 配置
        if stream_config.get("network"):
            network_default = get_default_network_config()
            network_config = stream_config["network"]
            
            result["network"] = network_default.copy()
            result["network"].update(network_config)
        
        return result
    
    def set_site_stream_config(self, domain: str, config: Dict[str, Any]) -> bool:
        """
        设置站点的流式配置
        
        Args:
            domain: 站点域名
            config: 流式配置（部分或完整）
        
        Returns:
            是否成功
        """
        self.refresh_if_changed()
        
        if domain not in self.sites:
            logger.warning(f"站点不存在: {domain}")
            return False
        
        # 验证并规范化配置
        validated = self._validate_stream_config(config)
        
        self.sites[domain]["stream_config"] = validated
        self._save_config()
        
        logger.info(f"站点 {domain} 流式配置已更新 (mode={validated.get('mode')})")
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
        
        # 验证 mode
        if "mode" in config:
            mode = str(config["mode"]).lower()
            if mode in ("dom", "network"):
                result["mode"] = mode
        
        # 验证数值字段
        for key in ["hard_timeout", "silence_threshold", "initial_wait"]:
            if key in config:
                try:
                    val = float(config[key])
                    if key == "hard_timeout":
                        result[key] = max(10, min(val, 600))
                    elif key == "silence_threshold":
                        result[key] = max(0.5, min(val, 30))
                    elif key == "initial_wait":
                        result[key] = max(5, min(val, 120))
                except (ValueError, TypeError):
                    pass
        
        # 验证布尔字段
        if "enable_wrapper_search" in config:
            result["enable_wrapper_search"] = bool(config["enable_wrapper_search"])
        
        # 验证 network 配置
        if config.get("network"):
            network_config = self._validate_network_config(config["network"])
            if network_config:
                result["network"] = network_config
                # 如果有有效的 network 配置，自动设置 mode
                if network_config.get("parser") and network_config.get("listen_pattern"):
                    result["mode"] = "network"
        
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
        for key in ["first_response_timeout", "silence_threshold", "response_interval"]:
            if key in config:
                try:
                    val = float(config[key])
                    if key == "first_response_timeout":
                        result[key] = max(1, min(val, 30))
                    elif key == "silence_threshold":
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
        return ParserRegistry.list_all()
    
    def get_extractor_manager(self):
        """获取提取器管理器实例"""
        return extractor_manager
    
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


__all__ = ['ConfigEngine', 'ConfigConstants', 'DEFAULT_WORKFLOW']