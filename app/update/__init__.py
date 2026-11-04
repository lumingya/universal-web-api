"""app/update - 自更新、更新保留规则与浏览器配置清理。

子模块：
- engine.py:   自动更新引擎（原根目录 ``updater.py``）
- preserve.py: 更新时保留的本地文件规则（原根目录 ``update_preserve.py``）
- profile.py:  chrome_profile 清理（原根目录 ``clean_profile.py``）

根目录仍保留同名兼容壳，供旧文档、旧脚本与 ``python updater.py`` 使用。
"""

from app.update.preserve import (
    build_effective_preserve_patterns,
    get_default_update_preserve_patterns,
    load_update_preserve_settings,
    save_update_preserve_settings,
)

__all__ = [
    "build_effective_preserve_patterns",
    "get_default_update_preserve_patterns",
    "load_update_preserve_settings",
    "save_update_preserve_settings",
]
