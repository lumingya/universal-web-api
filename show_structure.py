#!/usr/bin/env python3
"""显示项目结构并保存到文件（包含大小和注释）"""

from pathlib import Path

# 定义输出文件名
OUTPUT_FILE = '项目结构.txt'

EXCLUDE = {
    '__pycache__', '.git', 'venv', 'env', '.venv',
    'node_modules', '.idea', '.vscode', 'backup_stage7',
    'backup_old_files', 'chrome_profile', 'logs',
    OUTPUT_FILE
}

EXCLUDE_EXT = {'.pyc', '.pyo', '.log'}

# ==================== 注释配置 ====================
# 格式: "文件或目录名": "注释说明"
# 支持路径匹配: "app/api": "接口层"
COMMENTS = {
    # 根目录文件
    ".env": "🔒 环境变量 (API Key、调试开关等)",
    ".gitignore": "🚫 Git 忽略文件列表",
    "clean_profile.py": "🧹 清理脚本：重置浏览器用户数据目录",
    "main.py": "▶️ 程序主入口：启动 FastAPI 服务器",
    "requirements.txt": "📦 Python 依赖列表",
    "start.bat": "🚀 Windows 一键启动脚本",
    
    # app 目录
    "app": "🐍 Python 后端核心代码库",
    "app/api": "[接口层] 处理 HTTP 请求",
    "app/api/routes.py": "🚦 API 路由定义 (如 /v1/chat/completions)",
    
    # core 目录
    "app/core": "[核心层] 浏览器自动化与底层逻辑",
    "app/core/backup": "🗑️ 备份代码 (旧版逻辑，可忽略)",
    "app/core/extractors": "🧩 [提取策略层] 内容提取器",
    "app/core/extractors/base.py": "📜 提取器基类接口 (BaseExtractor)",
    "app/core/extractors/deep_mode.py": "🧠 深度提取模式 (JS注入、LaTeX处理)",
    "app/core/browser.py": "🌐 浏览器管理：启动Chrome、管理标签页",
    "app/core/config.py": "⚙️ 核心配置：日志格式、常量定义",
    "app/core/elements.py": "🔍 元素定位器：封装DOM查找逻辑",
    "app/core/stream_monitor.py": "📡 流式监听器：监控变化、计算Diff",
    "app/core/workflow.py": "🎬 工作流执行器：执行点击、输入等动作",
    
    # models 目录
    "app/models": "[数据模型层] 定义数据结构",
    "app/models/schemas.py": "📐 Pydantic 模型：校验请求/响应格式",
    
    # services 目录
    "app/services": "[业务逻辑层] 串联 Core 和 API",
    "app/services/config_engine.py": "💾 配置引擎：读写 sites.json",
    "app/services/request_manager.py": "🤵 请求管理器：调度浏览器、处理并发",
    
    # utils 目录
    "app/utils": "[工具层] 通用辅助函数",
    "app/utils/paste.py": "📋 剪贴板工具：处理长文本粘贴",
    
    # config 目录
    "config": "🔧 配置文件目录",
    "config/browser_config.json": "🖥️ 浏览器启动配置",
    "config/sites.json": "🗂️ 站点数据库：URL、选择器、工作流",
    
    # scripts 目录
    "scripts": "🛠️ 运维脚本目录",
    
    # static 目录
    "static": "🎨 前端静态资源 (Web UI)",
    "static/backup": "📦 备份的前端文件",
    "static/css": "💅 样式表目录",
    "static/css/dashboard.css": "控制面板样式",
    "static/js": "⚡ 前端 JavaScript",
    "static/js/components": "🧱 UI 组件库",
    "static/js/components/ConfigTab.js": "配置管理页面",
    "static/js/components/Dialogs.js": "弹窗组件",
    "static/js/components/LogsTab.js": "实时日志页面",
    "static/js/components/SettingsTab.js": "系统设置页面",
    "static/js/components/Sidebar.js": "侧边栏导航",
    "static/js/dashboard.js": "🚀 前端入口文件",
    "static/js/icons.js": "🖼️ SVG 图标数据",
    "static/index.html": "🏠 Web UI 主页入口",
    
    # tests 目录
    "tests": "🧪 单元测试目录",
    "tests/conftest.py": "Pytest 配置 (fixture)",
    "tests/test_config_engine.py": "配置引擎测试",
}


def format_size(size_bytes):
    """将字节数转换为人类可读的格式"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_dir_size(path):
    """递归计算目录大小"""
    total = 0
    try:
        for item in path.iterdir():
            if item.name in EXCLUDE or item.suffix in EXCLUDE_EXT:
                continue
            if item.is_file():
                total += item.stat().st_size
            elif item.is_dir():
                total += get_dir_size(item)
    except PermissionError:
        pass
    return total


def get_size(path):
    """获取文件或目录的大小"""
    try:
        if path.is_file():
            return path.stat().st_size
        elif path.is_dir():
            return get_dir_size(path)
    except (PermissionError, OSError):
        return 0
    return 0


def get_comment(path, root):
    """获取路径对应的注释"""
    # 计算相对路径
    try:
        rel_path = path.relative_to(root)
        rel_str = str(rel_path).replace("\\", "/")
    except ValueError:
        rel_str = path.name
    
    # 优先匹配完整路径，再匹配文件名
    if rel_str in COMMENTS:
        return COMMENTS[rel_str]
    if path.name in COMMENTS:
        return COMMENTS[path.name]
    
    return ""


def show_tree(path, file_obj, root, prefix="", is_last=True):
    """递归显示目录树，同时写入文件"""
    
    def log(text):
        print(text)
        file_obj.write(text + "\n")

    if path.name in EXCLUDE or path.suffix in EXCLUDE_EXT:
        return
    
    # 获取大小和注释
    size = get_size(path)
    size_str = format_size(size)
    comment = get_comment(path, root)
    
    # 构建输出行
    connector = "└── " if is_last else "├── "
    icon = "📁 " if path.is_dir() else "📄 "
    
    # 计算对齐（可选，让注释对齐更美观）
    name_part = f"{prefix}{connector}{icon}{path.name}"
    size_part = f"[{size_str}]"
    
    if comment:
        line = f"{name_part}  {size_part:<12} # {comment}"
    else:
        line = f"{name_part}  {size_part}"
    
    log(line)
    
    # 递归处理目录
    if path.is_dir():
        children = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name))
        children = [c for c in children if c.name not in EXCLUDE and c.suffix not in EXCLUDE_EXT]
        
        for i, child in enumerate(children):
            is_last_child = (i == len(children) - 1)
            new_prefix = prefix + ("    " if is_last else "│   ")
            show_tree(child, file_obj, root, new_prefix, is_last_child)


def main():
    root = Path(__file__).parent
    output_path = root / OUTPUT_FILE
    
    EXCLUDE.add(Path(__file__).name)

    with open(output_path, "w", encoding="utf-8") as f:
        
        root_size = get_dir_size(root)
        header = f"📁 {root.name}/  [总计: {format_size(root_size)}]  # 项目根目录"
        print(header)
        f.write(header + "\n")
        
        children = sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name))
        children = [c for c in children if c.name not in EXCLUDE and c.suffix not in EXCLUDE_EXT]
        
        for i, child in enumerate(children):
            is_last = (i == len(children) - 1)
            show_tree(child, f, root, "", is_last)
        
        # 添加图例说明
        legend = "\n" + "=" * 60 + "\n"
        legend += "📁 = 目录  |  📄 = 文件  |  # = 注释说明\n"
        legend += "=" * 60
        print(legend)
        f.write(legend + "\n")
        
        print(f"\n✅ 项目结构已保存至: {output_path}")


if __name__ == "__main__":
    main()