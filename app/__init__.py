"""
app - 应用主包

子模块:
- api: API 路由
- core: 核心功能
- models: 数据模型
- services: 业务服务
- utils: 工具函数
"""

from pathlib import Path


def _load_version() -> str:
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


__version__ = _load_version()
