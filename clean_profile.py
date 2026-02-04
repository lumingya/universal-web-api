#!/usr/bin/env python3
"""
clean_profile.py - 浏览器配置清理工具

功能：
- 清理浏览器缓存、临时文件
- 尽量保留登录状态和用户数据

用法：
    python clean_profile.py
    python clean_profile.py "C:\\path\\to\\profile_dir"
"""

from __future__ import annotations

import sys
import shutil
from pathlib import Path


# === 预防性路径配置 ===
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# =======================


SAFE_TO_DELETE = [
    "BrowserMetrics",
    "Crashpad",
    "GraphiteDawnCache",
    "ShaderCache",
    "GrShaderCache",
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/Service Worker/CacheStorage",
    "Default/Service Worker/ScriptCache",
]


def safe_slim_profile(profile_path: Path) -> int:
    """
    安全地清理浏览器配置文件中的垃圾数据，尽量保留登录状态。

    Args:
        profile_path: 浏览器用户数据目录（即 --user-data-dir 指向的目录）

    Returns:
        清理成功的目录项数量
    """
    profile_path = Path(profile_path)
    print(f"开始安全清理浏览器配置: {profile_path}")

    if not profile_path.exists():
        print(f"❌ 未找到配置文件目录: {profile_path}")
        return 0

    if not profile_path.is_dir():
        print(f"❌ 目标不是目录: {profile_path}")
        return 0

    count = 0
    for folder in SAFE_TO_DELETE:
        target = profile_path / folder
        if target.exists() and target.is_dir():
            try:
                shutil.rmtree(target)
                print(f"  [√] 已清理: {folder}")
                count += 1
            except PermissionError:
                print(f"  [!] 跳过（权限不足）: {folder}")
            except Exception as e:
                print(f"  [!] 跳过（{type(e).__name__}: {e}）: {folder}")

    print(f"清理完成，共处理 {count} 个项目。\n")
    return count


def _resolve_profile_dir(argv: list[str]) -> Path:
    """
    解析 profile 目录：
    - 若传入参数，使用参数作为 profile 目录
    - 否则默认使用脚本同级的 chrome_profile（保持兼容）
    """
    base_dir = Path(__file__).parent

    if len(argv) >= 2 and argv[1].strip():
        # 支持传入带引号的路径，由 shell/调用方负责去引号；这里再做一次稳妥 strip
        return Path(argv[1].strip().strip('"'))

    return base_dir / "chrome_profile"


if __name__ == "__main__":
    profile_dir = _resolve_profile_dir(sys.argv)
    safe_slim_profile(profile_dir)