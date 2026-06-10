"""
app/services/config/managers.py - 配置子管理器

职责：
- 全局配置管理（元素定义）
- 图片预设管理
"""

import json
import os
import copy
from app.core.config import get_logger
from typing import Dict, List, Any, Optional

from app.models.schemas import (
    SelectorDefinition,
    get_default_selector_definitions,
    get_default_image_extraction_config
)


logger = get_logger("CFG_MGR")


# ================= 全局配置管理器 =================

class GlobalConfigManager:
    """
    全局配置管理器
    
    管理 _global 节点中的配置，包括：
    - selector_definitions: 元素定义列表
    - 以及任意扩展字段（通过 get/set 访问）
    """
    
    def __init__(self):
        self._selector_definitions: List[SelectorDefinition] = get_default_selector_definitions()
        self._extra: Dict[str, Any] = {}  # 通用扩展存储
    
    def load(self, global_section: Dict[str, Any]):
        """从 _global 节点加载配置"""
        self._extra = {}
        self._selector_definitions = get_default_selector_definitions()
        if not global_section:
            return

        if "selector_definitions" in global_section:
            defs = global_section["selector_definitions"]
            if isinstance(defs, list):
                self._selector_definitions = self._merge_selector_definitions(defs)
                logger.debug(f"已加载 {len(defs)} 个元素定义")
        
        # 加载所有非 selector_definitions 的字段到通用存储
        for key, value in global_section.items():
            if key != "selector_definitions":
                self._extra[key] = copy.deepcopy(value)
        
        if "commands" in self._extra:
            cmd_count = len(self._extra["commands"]) if isinstance(self._extra["commands"], list) else 0
            logger.debug(f"已加载 {cmd_count} 个自动化命令")
    
    def get_selector_definitions(self) -> List[SelectorDefinition]:
        """获取元素定义列表"""
        return copy.deepcopy(self._selector_definitions)
    
    def set_selector_definitions(self, definitions: List[SelectorDefinition]):
        """设置元素定义列表"""
        self._selector_definitions = self._merge_selector_definitions(definitions)

    @staticmethod
    def _normalize_selector_definition(raw_item: Any) -> Optional[SelectorDefinition]:
        if not isinstance(raw_item, dict):
            return None
        key = raw_item.get("key")
        if not isinstance(key, str):
            return None
        key = key.strip()
        if not key:
            return None

        description = raw_item.get("description", "")
        if not isinstance(description, str):
            description = str(description) if description is not None else ""

        return {
            "key": key,
            "description": description,
            "enabled": bool(raw_item.get("enabled", True)),
            "required": bool(raw_item.get("required", False)),
        }

    def _merge_selector_definitions(self, definitions: List[SelectorDefinition]) -> List[SelectorDefinition]:
        """合并内置默认字段，确保升级后新增的选择器能自动出现。"""
        merged = []
        existing_keys = set()

        for item in definitions or []:
            normalized = self._normalize_selector_definition(item)
            if not normalized:
                continue
            key = normalized["key"]
            if key in existing_keys:
                continue
            merged.append(normalized)
            existing_keys.add(key)

        for default_def in get_default_selector_definitions():
            normalized_default = self._normalize_selector_definition(default_def)
            if not normalized_default:
                continue
            key = normalized_default.get("key")
            if key not in existing_keys:
                merged.append(normalized_default)
                existing_keys.add(key)

        return merged
    
    def get_enabled_definitions(self) -> List[SelectorDefinition]:
        """获取启用的元素定义"""
        return [
            copy.deepcopy(d)
            for d in self._selector_definitions
            if isinstance(d, dict) and d.get("enabled", True)
        ]
    
    def get_fallback_selectors(self) -> Dict[str, Optional[str]]:
        """
        生成回退选择器字典
        
        基于元素定义生成，用于 SelectorValidator
        """
        fallback_map = {
            "input_box": "textarea",
            "send_btn": 'button[type="submit"]',
            "result_container": "div",
            "new_chat_btn": None,
            "message_wrapper": None,
            "generating_indicator": None,
            "upload_btn": None,
            "file_input": 'input[type="file"]',
            "drop_zone": None,
        }
        
        result = {}
        for d in self._selector_definitions:
            if not isinstance(d, dict):
                continue
            key = d.get("key")
            if not isinstance(key, str) or not key:
                continue
            result[key] = fallback_map.get(key, None)

        return result
    
    def build_prompt_selector_list(self) -> str:
        """
        生成 AI 提示词中的元素查找列表
        
        只包含 enabled=True 的元素
        """
        lines = []
        for d in self._selector_definitions:
            if not isinstance(d, dict):
                continue
            if not d.get("enabled", True):
                continue

            key = str(d.get("key") or "").strip()
            if not key:
                continue
            desc = str(d.get("description") or "")
            required = d.get("required", False)
            
            if required:
                tag = "[REQUIRED]"
            else:
                tag = "[OPTIONAL, return null if not found]"
            
            lines.append(f"- `{key}`: {desc} {tag}")
        
        return "\n".join(lines)
    
    def build_prompt_json_keys(self) -> str:
        """
        生成 AI 提示词中的 JSON 输出格式说明
        """
        lines = []
        for d in self._selector_definitions:
            if not isinstance(d, dict):
                continue
            if not d.get("enabled", True):
                continue

            key = str(d.get("key") or "").strip()
            if not key:
                continue
            desc = str(d.get("description") or "")
            lines.append(f'- `{key}`: {desc}')
        
        return "\n".join(lines)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取通用配置值"""
        return copy.deepcopy(self._extra.get(key, default))
    
    def set(self, key: str, value: Any):
        """设置通用配置值"""
        self._extra[key] = copy.deepcopy(value)

    def remove(self, key: str) -> bool:
        """删除通用配置值"""
        if key not in self._extra:
            return False
        del self._extra[key]
        return True
    
    def to_dict(self) -> Dict[str, Any]:
        """导出为字典（用于保存）"""
        result = {
            "selector_definitions": self._selector_definitions
        }
        # 合并通用存储中的所有字段
        result.update(self._extra)
        return copy.deepcopy(result)


# ================= 图片预设管理器 =================

class ImagePresetsManager:
    """
    图片提取预设管理器
    
    职责：
    - 加载预设配置文件
    - 提供站点预设查询
    - 应用预设到站点配置
    """
    
    def __init__(self, presets_file: str):
        self.presets_file = presets_file
        self.presets: Dict[str, Any] = {}
        self._load_presets()

    @staticmethod
    def _default_presets_payload() -> Dict[str, Any]:
        return {
            "_default": {
                "name": "Default",
                "description": "Built-in fallback image extraction preset",
                "image_extraction": get_default_image_extraction_config(),
            }
        }

    @staticmethod
    def _get_preset_image_config(preset_data: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(preset_data, dict):
            return None
        config = preset_data.get("image_extraction")
        return copy.deepcopy(config) if isinstance(config, dict) else None

    @staticmethod
    def _get_preset_text(preset_data: Dict[str, Any], key: str, default: str = "") -> str:
        value = preset_data.get(key, default)
        if value is None:
            return default
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        return default

    def _find_matching_preset(self, domain: str) -> Optional[tuple[str, Dict[str, Any]]]:
        domain = str(domain or "").strip()
        if not domain:
            return None

        if domain in self.presets and isinstance(self.presets[domain], dict):
            return domain, self.presets[domain]

        for preset_domain, preset_data in self.presets.items():
            if preset_domain.startswith("_") or not isinstance(preset_data, dict):
                continue
            if domain.endswith(preset_domain) or preset_domain in domain:
                logger.debug(f"使用模糊匹配预设: {domain} -> {preset_domain}")
                return preset_domain, preset_data

        return None
    
    def _use_default_presets_or_keep_existing(self, keep_existing: bool) -> None:
        if keep_existing and self.presets:
            logger.warning("图片预设加载失败，保留当前内存中的预设")
            return
        self.presets = self._default_presets_payload()

    def _load_presets(self, keep_existing_on_error: bool = False):
        """加载预设配置文件"""
        if not os.path.exists(self.presets_file):
            logger.warning(f"图片预设文件不存在: {self.presets_file}")
            self._use_default_presets_or_keep_existing(keep_existing_on_error)
            return

        try:
            with open(self.presets_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.error("预设文件格式错误: 顶层必须是对象")
                self._use_default_presets_or_keep_existing(keep_existing_on_error)
                return

            # 移除元数据
            loaded_presets = {
                str(k): v
                for k, v in data.items()
                if k != "_meta" and isinstance(v, dict)
            }
            skipped_count = len([k for k, v in data.items() if k != "_meta" and not isinstance(v, dict)])
            if skipped_count:
                logger.warning(f"已跳过 {skipped_count} 个格式无效的图片预设")
            if not loaded_presets:
                self._use_default_presets_or_keep_existing(keep_existing_on_error)
                logger.debug(f"已加载 {len(self.presets)} 个图片预设")
                return

            self.presets = loaded_presets

            logger.debug(f"已加载 {len(self.presets)} 个图片预设")
        
        except json.JSONDecodeError as e:
            logger.error(f"预设文件格式错误: {e}")
            self._use_default_presets_or_keep_existing(keep_existing_on_error)
        except Exception as e:
            logger.error(f"加载预设失败: {e}")
            self._use_default_presets_or_keep_existing(keep_existing_on_error)
    
    def get_preset(self, domain: str) -> Optional[Dict]:
        """
        获取站点的预设配置
        
        Args:
            domain: 站点域名
        
        Returns:
            预设配置字典，不存在返回 None
        """
        match = self._find_matching_preset(domain)
        if match is None:
            return None
        return self._get_preset_image_config(match[1])
    
    def list_presets(self) -> List[Dict[str, Any]]:
        """
        列出所有可用预设
        
        Returns:
            预设列表，每项包含 domain、name、description、enabled
        """
        result = []
        
        for domain, data in self.presets.items():
            if domain == "_meta":
                continue
            if not isinstance(data, dict):
                continue

            config = self._get_preset_image_config(data) or {}

            item = {
                "domain": domain,
                "name": self._get_preset_text(data, "name", domain),
                "description": self._get_preset_text(data, "description", ""),
                "enabled": config.get("enabled", False),
                "notes": self._get_preset_text(data, "notes", ""),
                "config": config
            }

            result.append(item)

        result.sort(key=lambda x: x["domain"])

        return result

    def get_preset_for_display(self, domain: str) -> Dict[str, Any]:
        """
        获取用于显示的预设信息

        Args:
            domain: 站点域名

        Returns:
            包含 available、preset_domain、config 的字典
        """
        match = self._find_matching_preset(domain)
        if match is None:
            preset_config = None
        else:
            preset_config = self._get_preset_image_config(match[1])

        if preset_config:
            matched_domain, matched_data = match

            return {
                "available": True,
                "preset_domain": matched_domain,
                "name": self._get_preset_text(
                    matched_data,
                    "name",
                    "",
                ),
                "config": preset_config
            }

        return {
            "available": False,
            "preset_domain": None,
            "name": None,
            "config": None
        }
    
    def reload(self):
        """重新加载预设文件"""
        self._load_presets(keep_existing_on_error=True)


__all__ = ['GlobalConfigManager', 'ImagePresetsManager']
