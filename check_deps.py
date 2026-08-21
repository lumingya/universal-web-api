#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
依赖完整性检测脚本
退出码: 0=完整, 1=缺失
"""

import re
import sys
from pathlib import Path


def _marker_matches(marker: str) -> bool:
    """处理环境标记，支持行内注释与常见平台标记"""
    normalized = str(marker or "").strip()
    if not normalized:
        return True

    # 剥离行内注释
    normalized = normalized.split("#")[0].strip()
    if not normalized:
        return True

    # 匹配 sys_platform
    match = re.search(r"sys_platform\s*([=!]=)\s*['\"]([^'\"]+)['\"]", normalized)
    if match:
        operator, expected = match.groups()
        actual = sys.platform
        return (actual == expected) if operator == "==" else (actual != expected)

    # 匹配 platform_system
    match = re.search(r"platform_system\s*([=!]=)\s*['\"]([^'\"]+)['\"]", normalized)
    if match:
        import platform
        operator, expected = match.groups()
        actual = platform.system()
        return (actual == expected) if operator == "==" else (actual != expected)

    return True


def _parse_requirement_name(line: str) -> str:
    """提取包名：去除行内注释、版本操作符与 extras"""
    # 1. 剥离行内注释
    line = line.split("#")[0].strip()
    # 2. 剥离 extras 标记 (如 uvicorn[standard])
    line = line.split("[")[0].strip()
    # 3. 剥离 URL / Direct Reference
    line = line.split("@")[0].strip()
    # 4. 正则提取标准 PEP 508 包名
    match = re.match(r"^([A-Za-z0-9_.\-]+)", line)
    if match:
        return match.group(1).strip()
    return ""


def check_dependencies() -> bool:
    """检测 requirements.txt 中的包是否都已安装"""
    req_file = Path(__file__).parent / "requirements.txt"
    if not req_file.exists():
        print("[ERROR] requirements.txt not found")
        return False

    try:
        content = req_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = req_file.read_text(encoding="gbk")
        except Exception as e:
            print(f"[ERROR] Failed to read requirements.txt: {e}")
            return False
    except Exception as e:
        print(f"[ERROR] Failed to read requirements.txt: {e}")
        return False

    # 读取并解析 requirements.txt
    packages = []
    for line in content.splitlines():
        line = line.strip()
        # 跳过空行、注释行及 pip 参数行 (-i, --index-url, -r 等)
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        requirement, _, marker = line.partition(";")
        if marker and not _marker_matches(marker):
            continue
        pkg_name = _parse_requirement_name(requirement)
        if pkg_name:
            packages.append(pkg_name)
    
    # 包名到实际模块名的映射（处理特殊情况）
    # 键：requirements.txt 中的包名（小写）
    # 值：实际 import 时用的模块名
    name_mapping = {
        "pillow": "PIL",
        "beautifulsoup4": "bs4",
        "python-dotenv": "dotenv",
        "pywin32": "win32api",
        "pyyaml": "yaml",
        "drissionpage": "DrissionPage",  # 保持大小写
        "pysocks": "socks",
    }
    
    missing = []
    for pkg in packages:
        pkg_lower = pkg.lower()
        
        # 优先使用映射表
        if pkg_lower in name_mapping:
            module_name = name_mapping[pkg_lower]
        else:
            # 默认：小写并替换连字符
            module_name = pkg_lower.replace("-", "_")
        
        try:
            __import__(module_name)
        except Exception:
            missing.append(pkg)
    
    if missing:
        print(f"[WARN] Missing packages: {', '.join(missing)}")
        return False
    
    return True


if __name__ == "__main__":
    if check_dependencies():
        sys.exit(0)
    else:
        sys.exit(1)
