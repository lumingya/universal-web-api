"""
app/services/config_engine.py - 兼容性代理文件

向后兼容：保持原有导入路径有效
实际实现已迁移到 app/services/config/ 模块
"""

# 从新模块导入所有内容
from app.services.config import (
    config_engine,
    ConfigEngine,
    ConfigConstants,
    DEFAULT_WORKFLOW
)

# 保持原有导出
__all__ = ['config_engine', 'ConfigEngine', 'ConfigConstants', 'DEFAULT_WORKFLOW']