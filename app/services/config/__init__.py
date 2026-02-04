"""
app/services/config - 配置管理模块

职责：
- 导出配置引擎单例（保持向后兼容）
"""

from .engine import ConfigEngine, ConfigConstants, DEFAULT_WORKFLOW

# 创建单例
config_engine = ConfigEngine()

__all__ = ['config_engine', 'ConfigEngine', 'ConfigConstants', 'DEFAULT_WORKFLOW']