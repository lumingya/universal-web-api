from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


UPDATE_SETTINGS_FILE = Path("config") / "update_settings.json"


def _option(
    option_id: str,
    pattern: str,
    label: str,
    description: str,
    category: str,
    default: bool = False,
) -> Dict[str, Any]:
    return {
        "id": option_id,
        "pattern": pattern,
        "label": label,
        "description": description,
        "category": category,
        "default": default,
    }


UPDATE_PRESERVE_OPTIONS: List[Dict[str, Any]] = [
    _option("env_file", ".env", ".env", "环境变量与服务启动参数", "核心配置"),
    _option("config_dir", "config", "config/", "整个配置目录", "核心配置"),
    _option("sites_config", "config/sites.json", "config/sites.json", "站点默认配置与预设定义", "核心配置"),
    _option("commands_config", "config/commands.json", "config/commands.json", "命令定义与默认命令内容", "核心配置"),
    _option("browser_config", "config/browser_config.json", "config/browser_config.json", "浏览器常量配置", "核心配置"),
    _option("extractors_config", "config/extractors.json", "config/extractors.json", "提取器配置", "核心配置"),
    _option("image_presets_config", "config/image_presets.json", "config/image_presets.json", "图片预设配置", "核心配置"),
    _option("sites_local", "config/sites.local.json", "config/sites.local.json", "本地默认预设覆盖", "本地覆盖", True),
    _option("commands_local", "config/commands.local.json", "config/commands.local.json", "本地命令启用状态与分组覆盖", "本地覆盖", True),
    _option("chrome_profile", "chrome_profile", "chrome_profile/", "Chrome 用户目录、登录态与 Cookie", "运行数据", True),
    _option("venv", "venv", "venv/", "Python 虚拟环境", "运行数据", True),
    _option("logs", "logs", "logs/", "日志目录", "运行数据", True),
    _option("image", "image", "image/", "图片输出目录", "运行数据", True),
    _option("download_images", "download_images", "download_images/", "下载图片目录", "运行数据"),
    _option("output", "output", "output/", "运行产物输出目录", "运行数据"),
    _option("app_dir", "app", "app/", "整个后端代码目录", "后端代码"),
    _option("app_api", "app/api", "app/api/", "后端 API 路由", "后端代码"),
    _option("app_core", "app/core", "app/core/", "核心运行逻辑", "后端代码"),
    _option("app_models", "app/models", "app/models/", "数据模型与 Schema", "后端代码"),
    _option("app_services", "app/services", "app/services/", "配置与命令服务逻辑", "后端代码"),
    _option("app_utils", "app/utils", "app/utils/", "后端工具函数", "后端代码"),
    _option("main_py", "main.py", "main.py", "服务入口文件", "后端代码"),
    _option("static_dir", "static", "static/", "整个前端静态资源目录", "前端资源"),
    _option("static_index", "static/index.html", "static/index.html", "前端主页面模板", "前端资源"),
    _option("static_js", "static/js", "static/js/", "前端脚本目录", "前端资源"),
    _option("static_css", "static/css", "static/css/", "前端样式目录", "前端资源"),
    _option("static_vendor", "static/vendor", "static/vendor/", "前端第三方依赖目录", "前端资源"),
    _option("static_tutorial", "static/tutorial.html", "static/tutorial.html", "前端教程页", "前端资源"),
    _option(
        "static_tutorial_overview",
        "static/tutorial-dashboard-overview.png",
        "static/tutorial-dashboard-overview.png",
        "教程总览图片",
        "前端资源",
    ),
    _option(
        "static_workflow_visualization",
        "static/workflow-visualization.png",
        "static/workflow-visualization.png",
        "工作流说明图片",
        "前端资源",
    ),
    _option("assets_dir", "assets", "assets/", "项目资源目录", "前端资源"),
    _option("gitignore", ".gitignore", ".gitignore", "Git 忽略规则", "脚本与文档"),
    _option("readme", "README.md", "README.md", "项目说明文档", "脚本与文档"),
    _option("changelog", "CHANGELOG.md", "CHANGELOG.md", "更新日志", "脚本与文档"),
    _option("version_file", "VERSION", "VERSION", "版本号文件", "脚本与文档"),
    _option("license", "LICENSE", "LICENSE", "许可证文件", "脚本与文档"),
    _option("requirements", "requirements.txt", "requirements.txt", "Python 依赖清单", "脚本与文档"),
    _option("start_bat", "start.bat", "start.bat", "Windows 启动脚本", "脚本与文档"),
    _option("updater_py", "updater.py", "updater.py", "更新脚本本体", "脚本与文档", True),
    _option("update_preserve_py", "update_preserve.py", "update_preserve.py", "更新白名单定义脚本", "脚本与文档"),
    _option("check_deps", "check_deps.py", "check_deps.py", "依赖检查脚本", "脚本与文档"),
    _option("clean_profile", "clean_profile.py", "clean_profile.py", "用户目录清理脚本", "脚本与文档"),
    _option("patch_drission", "patch_drissionpage.py", "patch_drissionpage.py", "DrissionPage 补丁脚本", "脚本与文档"),
    _option("show_structure", "show_structure.py", "show_structure.py", "项目结构导出脚本", "脚本与文档"),
    _option("command_audit", "command_audit.md", "command_audit.md", "命令审查文档", "脚本与文档"),
    _option("git_submit_doc", "git提交.py", "git提交.py", "本地 Git 提交脚本", "脚本与文档"),
    _option("params_doc", "参数解释.md", "参数解释.md", "参数说明文档", "脚本与文档"),
    _option("review_doc", "项目审查报告.md", "项目审查报告.md", "项目审查报告", "脚本与文档"),
    _option("structure_doc", "项目结构.txt", "项目结构.txt", "项目结构文本", "脚本与文档"),
    _option("git_dir", ".git", ".git/", "Git 元数据目录", "开发文件", True),
    _option("pycache", "__pycache__", "__pycache__/", "Python 缓存目录", "开发文件", True),
    _option("pyc", "*.pyc", "*.pyc", "Python 编译缓存文件", "开发文件", True),
    _option("backup_dirs", "backup_*", "backup_*/", "更新前备份目录", "开发文件", True),
]

INTERNAL_ALWAYS_PRESERVE: List[str] = [
    str(UPDATE_SETTINGS_FILE).replace("\\", "/"),
    "config/app_stats.json",
]
LEGACY_PATTERN_ALIASES = {
    "sites.local.json": "config/sites.local.json",
    "commands.local.json": "config/commands.local.json",
}


def get_update_preserve_options() -> List[Dict[str, Any]]:
    return [dict(item) for item in UPDATE_PRESERVE_OPTIONS]


def get_default_update_preserve_patterns() -> List[str]:
    return [item["pattern"] for item in UPDATE_PRESERVE_OPTIONS if item.get("default")]


def normalize_update_preserve_patterns(patterns: Any) -> List[str]:
    allowed = {item["pattern"] for item in UPDATE_PRESERVE_OPTIONS}
    normalized: List[str] = []
    for raw in patterns or []:
        value = str(raw or "").strip().replace("\\", "/")
        value = LEGACY_PATTERN_ALIASES.get(value, value)
        if not value or value not in allowed or value in normalized:
            continue
        normalized.append(value)
    return normalized


def load_update_preserve_settings(settings_path: Path | None = None) -> Dict[str, Any]:
    path = settings_path or UPDATE_SETTINGS_FILE
    selected = get_default_update_preserve_patterns()

    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            selected = normalize_update_preserve_patterns(data.get("selected_patterns", []))
        except Exception:
            selected = get_default_update_preserve_patterns()

    return {
        "selected_patterns": selected,
        "options": get_update_preserve_options(),
        "settings_path": str(path).replace("\\", "/"),
    }


def save_update_preserve_settings(
    selected_patterns: Any,
    settings_path: Path | None = None,
) -> Dict[str, Any]:
    path = settings_path or UPDATE_SETTINGS_FILE
    normalized = normalize_update_preserve_patterns(selected_patterns)
    payload = {
        "selected_patterns": normalized,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    return {
        "selected_patterns": normalized,
        "settings_path": str(path).replace("\\", "/"),
    }


def build_effective_preserve_patterns(selected_patterns: Any) -> List[str]:
    normalized = normalize_update_preserve_patterns(selected_patterns)
    for item in INTERNAL_ALWAYS_PRESERVE:
        if item not in normalized:
            normalized.append(item)
    return normalized
