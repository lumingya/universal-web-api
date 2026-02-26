"""
app/services/extractor_manager.py - 提取器配置管理器

职责：
- 加载 config/extractors.json
- 管理提取器配置
- 根据站点配置返回提取器实例
- 支持导入/导出提取器配置
"""

import os
import json
from app.core.config import get_logger
from typing import Dict, Optional, List, Any

from app.core.extractors import ExtractorRegistry, BaseExtractor


logger = get_logger("EXTRACT")


class ExtractorConfigManager:
    """
    提取器配置管理器
    
    负责：
    1. 加载和保存 extractors.json
    2. 根据配置动态加载提取器
    3. 为站点提供提取器实例
    """
    
    # 配置文件路径
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    CONFIG_FILE = os.path.join(_PROJECT_ROOT, "config", "extractors.json")
    
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._extractors_config: Dict[str, Dict] = {}
        self._default_id: str = "deep_mode_v1"
        self._last_mtime: float = 0.0
        
        # 加载配置
        self._load_config()
    
    def _load_config(self):
        """加载 extractors.json 配置文件"""
        if not os.path.exists(self.CONFIG_FILE):
            logger.warning(f"提取器配置文件不存在: {self.CONFIG_FILE}，使用默认配置")
            self._use_defaults()
            return
        
        try:
            self._last_mtime = os.path.getmtime(self.CONFIG_FILE)
            
            with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                self._config = json.load(f)
            
            self._extractors_config = self._config.get("extractors", {})
            self._default_id = self._config.get("default", "deep_mode_v1")
            
            # 动态加载配置中定义的提取器
            self._register_from_config()
            
            logger.debug(
                f"已加载提取器配置: {len(self._extractors_config)} 个提取器，"
                f"默认: {self._default_id}"
            )
        
        except json.JSONDecodeError as e:
            logger.error(f"提取器配置文件格式错误: {e}")
            self._use_defaults()
        
        except Exception as e:
            logger.error(f"加载提取器配置失败: {e}")
            self._use_defaults()
    
    def _use_defaults(self):
        """使用默认配置"""
        self._extractors_config = {
            "deep_mode_v1": {
                "id": "deep_mode_v1",
                "name": "深度模式 (JS注入)",
                "description": "通过 JavaScript 注入深度提取内容",
                "class": "DeepBrowserExtractor",
                "module": "app.core.extractors.deep_mode",
                "enabled": True,
                "config": {}
            }
        }
        self._default_id = "deep_mode_v1"
    
    def _register_from_config(self):
        """根据配置文件动态注册提取器"""
        for extractor_id, config in self._extractors_config.items():
            if not config.get("enabled", True):
                logger.debug(f"跳过禁用的提取器: {extractor_id}")
                continue
            
            # 检查是否已注册
            if ExtractorRegistry.exists(extractor_id):
                logger.debug(f"提取器已注册: {extractor_id}")
                continue
            
            # 尝试动态加载
            module_path = config.get("module")
            class_name = config.get("class")
            
            if module_path and class_name:
                try:
                    ExtractorRegistry.load_from_module(module_path, class_name, extractor_id)
                except Exception as e:
                    logger.warning(f"动态加载提取器失败 [{extractor_id}]: {e}")
    
    def refresh_if_changed(self):
        """检查配置文件是否变化，如果变化则重载"""
        if not os.path.exists(self.CONFIG_FILE):
            return
        
        try:
            current_mtime = os.path.getmtime(self.CONFIG_FILE)
            if current_mtime != self._last_mtime:
                logger.info("检测到提取器配置变化，重新加载")
                self._load_config()
        except Exception as e:
            logger.error(f"检查配置变化失败: {e}")
    
    # ================= 提取器获取 =================
    
    def get_extractor(self, extractor_id: Optional[str] = None) -> BaseExtractor:
        """
        获取提取器实例
        
        Args:
            extractor_id: 提取器 ID（None 则返回默认）
        
        Returns:
            提取器实例
        """
        eid = extractor_id or self._default_id
        
        try:
            return ExtractorRegistry.get(eid)
        except ValueError:
            logger.warning(f"提取器不存在: {eid}，使用默认: {self._default_id}")
            return ExtractorRegistry.get(self._default_id)
    
    def get_extractor_for_site(self, site_config: Dict) -> BaseExtractor:
        """
        根据站点配置获取提取器
        
        Args:
            site_config: 站点配置字典（包含 extractor_id 字段）
        
        Returns:
            提取器实例
        """
        extractor_id = site_config.get("extractor_id")
        return self.get_extractor(extractor_id)
    
    def get_default_id(self) -> str:
        """获取默认提取器 ID"""
        return self._default_id
    
    def set_default(self, extractor_id: str) -> bool:
        """
        设置默认提取器
        
        Args:
            extractor_id: 提取器 ID
        
        Returns:
            是否成功
        """
        if not ExtractorRegistry.exists(extractor_id):
            logger.error(f"无法设置默认提取器：{extractor_id} 不存在")
            return False
        
        self._default_id = extractor_id
        self._config["default"] = extractor_id
        self._save_config()
        
        return True
    
    # ================= 配置查询 =================
    
    def list_extractors(self) -> List[Dict[str, Any]]:
        """
        列出所有可用的提取器
        
        Returns:
            提取器信息列表
        """
        result = []
        
        # 从注册表获取（确保包含运行时注册的）
        for info in ExtractorRegistry.list_all():
            extractor_id = info["id"]
            
            # 合并配置文件中的信息
            config = self._extractors_config.get(extractor_id, {})
            
            result.append({
                "id": extractor_id,
                "name": config.get("name") or info.get("name", extractor_id),
                "description": config.get("description") or info.get("description", ""),
                "enabled": config.get("enabled", True),
                "is_default": extractor_id == self._default_id
            })
        
        return result
    
    def get_extractor_config(self, extractor_id: str) -> Optional[Dict]:
        """获取提取器配置"""
        return self._extractors_config.get(extractor_id)
    
    # ================= 导入/导出 =================
    
    def export_config(self) -> Dict[str, Any]:
        """
        导出提取器配置
        
        Returns:
            完整配置字典（可保存为 JSON）
        """
        return {
            "extractors": self._extractors_config,
            "default": self._default_id,
            "version": self._config.get("version", "1.0")
        }
    
    def import_config(self, config: Dict[str, Any]) -> bool:
        """
        导入提取器配置
        
        Args:
            config: 配置字典
        
        Returns:
            是否成功
        """
        try:
            # 验证格式
            if "extractors" not in config:
                logger.error("导入失败：缺少 extractors 字段")
                return False
            
            self._extractors_config = config["extractors"]
            self._default_id = config.get("default", "deep_mode_v1")
            self._config = config
            
            # 保存到文件
            self._save_config()
            
            # 重新注册提取器
            self._register_from_config()
            
            logger.info(f"成功导入提取器配置: {len(self._extractors_config)} 个")
            return True
        
        except Exception as e:
            logger.error(f"导入配置失败: {e}")
            return False
    
    def _save_config(self):
        """保存配置到文件"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.CONFIG_FILE), exist_ok=True)
            
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            
            self._last_mtime = os.path.getmtime(self.CONFIG_FILE)
            logger.info("提取器配置已保存")
        
        except Exception as e:
            logger.error(f"保存配置失败: {e}")


# ================= 单例 =================

extractor_manager = ExtractorConfigManager()


__all__ = ['ExtractorConfigManager', 'extractor_manager']