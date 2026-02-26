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
    """
    
    def __init__(self):
        self._selector_definitions: List[SelectorDefinition] = get_default_selector_definitions()
    
    def load(self, global_section: Dict[str, Any]):
        """从 _global 节点加载配置"""
        if not global_section:
            return
        
        if "selector_definitions" in global_section:
            defs = global_section["selector_definitions"]
            if isinstance(defs, list):
                self._selector_definitions = defs
                logger.debug(f"已加载 {len(defs)} 个元素定义")
    
    def get_selector_definitions(self) -> List[SelectorDefinition]:
        """获取元素定义列表"""
        return copy.deepcopy(self._selector_definitions)
    
    def set_selector_definitions(self, definitions: List[SelectorDefinition]):
        """设置元素定义列表"""
        self._selector_definitions = definitions
    
    def get_enabled_definitions(self) -> List[SelectorDefinition]:
        """获取启用的元素定义"""
        return [d for d in self._selector_definitions if d.get("enabled", True)]
    
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
        }
        
        result = {}
        for d in self._selector_definitions:
            key = d["key"]
            result[key] = fallback_map.get(key, None)
        
        return result
    
    def build_prompt_selector_list(self) -> str:
        """
        生成 AI 提示词中的元素查找列表
        
        只包含 enabled=True 的元素
        """
        lines = []
        for d in self._selector_definitions:
            if not d.get("enabled", True):
                continue
            
            key = d["key"]
            desc = d["description"]
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
            if not d.get("enabled", True):
                continue
            
            key = d["key"]
            desc = d["description"]
            lines.append(f'- `{key}`: {desc}')
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """导出为字典（用于保存）"""
        return {
            "selector_definitions": self._selector_definitions
        }


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
    
    def _load_presets(self):
        """加载预设配置文件"""
        if not os.path.exists(self.presets_file):
            logger.warning(f"图片预设文件不存在: {self.presets_file}")
            self.presets = {"_default": get_default_image_extraction_config()}
            return
        
        try:
            with open(self.presets_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 移除元数据
            self.presets = {k: v for k, v in data.items() if k != "_meta"}
            
            logger.debug(f"已加载 {len(self.presets)} 个图片预设")
        
        except json.JSONDecodeError as e:
            logger.error(f"预设文件格式错误: {e}")
            self.presets = {"_default": get_default_image_extraction_config()}
        except Exception as e:
            logger.error(f"加载预设失败: {e}")
            self.presets = {"_default": get_default_image_extraction_config()}
    
    def get_preset(self, domain: str) -> Optional[Dict]:
        """
        获取站点的预设配置
        
        Args:
            domain: 站点域名
        
        Returns:
            预设配置字典，不存在返回 None
        """
        # 精确匹配
        if domain in self.presets:
            preset = self.presets[domain]
            return preset.get("image_extraction")
        
        # 模糊匹配
        for preset_domain, preset_data in self.presets.items():
            if preset_domain.startswith('_'):
                continue
            
            if domain.endswith(preset_domain) or preset_domain in domain:
                logger.debug(f"使用模糊匹配预设: {domain} -> {preset_domain}")
                return preset_data.get("image_extraction")
        
        return None
    
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
            
            config = data.get("image_extraction", {})
            
            item = {
                "domain": domain,
                "name": data.get("name", domain),
                "description": data.get("description", ""),
                "enabled": config.get("enabled", False),
                "notes": data.get("notes", ""),
                "is_special": domain.startswith("_"),
                "config": config
            }
            
            result.append(item)
        
        # 排序：特殊预设在后
        result.sort(key=lambda x: (x["is_special"], x["domain"]))
        
        return result
    
    def get_preset_for_display(self, domain: str) -> Dict[str, Any]:
        """
        获取用于显示的预设信息
        
        Args:
            domain: 站点域名
        
        Returns:
            包含 available、preset_domain、config 的字典
        """
        preset_config = self.get_preset(domain)
        
        if preset_config:
            matched_domain = None
            for preset_domain in self.presets.keys():
                if preset_domain == domain or (domain.endswith(preset_domain) and not preset_domain.startswith('_')):
                    matched_domain = preset_domain
                    break
            
            return {
                "available": True,
                "preset_domain": matched_domain or domain,
                "name": self.presets.get(matched_domain or domain, {}).get("name", ""),
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
        self._load_presets()


__all__ = ['GlobalConfigManager', 'ImagePresetsManager']