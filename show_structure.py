#!/usr/bin/env python3
"""显示项目结构并保存到文件（包含大小和注释）"""

from pathlib import Path

# 定义输出文件名
OUTPUT_FILE = '项目结构.txt'

EXCLUDE = {
    '__pycache__', '.git', 'venv', 'env', '.venv',
    'node_modules', '.idea', '.vscode', '.vs', 'backup_stage7',
    'backup_old_files', 'chrome_profile', 'logs',
    'download_images', 'image',  # 新增：忽略图片文件夹
    OUTPUT_FILE
}

EXCLUDE_EXT = {'.pyc', '.pyo', '.log'}

# ==================== 注释配置 ====================
# 格式: "文件或目录名": "注释说明"
# 支持路径匹配: "app/api": "接口层"
COMMENTS = {
    # ==================== 根目录文件 ====================
    ".env": "🔒 环境变量 (API Key、调试开关等)",
    ".gitignore": "🚫 Git 忽略文件列表",
    "LICENSE": "📜 开源许可证",
    "README.md": "📖 项目说明文档",
    "VERSION": "🏷️ 版本号文件",
    "clean_profile.py": "🧹 清理脚本：重置浏览器用户数据目录",
    "main.py": "▶️ 程序主入口：启动 FastAPI 服务器",
    "requirements.txt": "📦 Python 依赖列表",
    "start.bat": "🚀 Windows 一键启动脚本",
    "updater.py": "🔄 自动更新器：检查版本、拉取更新",
    "git提交.py": "📤 Git 提交辅助脚本：自动化 add/commit/push",
    "参数解释.md": "📝 配置参数说明文档",

    # ==================== app 目录 ====================
    "app": "🐍 Python 后端核心代码库",

    # ---------- app/api: 接口层 ----------
    "app/api": "[接口层] 处理 HTTP 请求",
    "app/api/__init__.py": "模块初始化",
    "app/api/routes.py": "🚦 API 路由汇总：注册所有子路由到 FastAPI",
    "app/api/chat.py": "💬 聊天接口：处理 /v1/chat/completions 请求，支持流式/非流式",
    "app/api/config_routes.py": "🔧 配置接口：站点配置的 CRUD API (增删改查)",
    "app/api/system.py": "🖥️ 系统接口：健康检查、日志查询、系统状态等",
    "app/api/tab_routes.py": "📑 标签页接口：标签池管理 (创建/销毁/状态查询)",

    # ---------- app/core: 核心层 ----------
    "app/core": "[核心层] 浏览器自动化与底层逻辑",
    "app/core/__init__.py": "模块初始化：导出核心组件",

    # extractors: 提取策略层
    "app/core/extractors": "🧩 [提取策略层] 从 AI 网页提取回复内容",
    "app/core/extractors/__init__.py": "模块初始化：注册所有提取器",
    "app/core/extractors/base.py": "📜 提取器基类接口 (BaseExtractor)，定义统一的提取方法签名",
    "app/core/extractors/deep_mode.py": "🧠 深度提取模式：通过 JS 注入提取完整内容，支持 LaTeX/代码块处理",
    "app/core/extractors/dom_mode.py": "🌳 DOM 提取模式：直接解析页面 DOM 元素获取文本",
    "app/core/extractors/hybrid_mode.py": "🔀 混合提取模式：结合 DOM + 深度模式，自动择优",
    "app/core/extractors/image_extractor.py": "🖼️ 图片提取器：提取回复中的图片 (Base64/URL)",
    "app/core/extractors/registry.py": "📋 提取器注册中心：根据站点配置自动匹配提取策略",

    # parsers: 站点解析器
    "app/core/parsers": "🔍 [站点解析器层] 各 AI 站点的专用内容解析",
    "app/core/parsers/__init__.py": "模块初始化：注册所有解析器",
    "app/core/parsers/base.py": "📜 解析器基类：定义通用解析接口",
    "app/core/parsers/aistudio_parser.py": "🤖 Google AI Studio 专用解析器",
    "app/core/parsers/chatgpt_parser.py": "🤖 ChatGPT 专用解析器",
    "app/core/parsers/deepseek_parser.py": "🤖 DeepSeek 专用解析器",
    "app/core/parsers/gemini_parser.py": "🤖 Gemini 专用解析器",
    "app/core/parsers/lmarena_parser.py": "🤖 LM Arena 专用解析器",
    "app/core/parsers/registry.py": "📋 解析器注册中心：根据 URL 自动匹配解析器",

    # workflow: 工作流
    "app/core/workflow": "🎬 [工作流层] 分步执行浏览器操作",
    "app/core/workflow/__init__.py": "模块初始化",
    "app/core/workflow/executor.py": "⚙️ 工作流执行器：按顺序执行 Action 列表",
    "app/core/workflow/image_input.py": "🖼️ 图片输入处理：上传图片到 AI 对话框",
    "app/core/workflow/text_input.py": "⌨️ 文本输入处理：输入文本到对话框 (支持粘贴/模拟键入)",

    # core 其他文件
    "app/core/browser.py": "🌐 浏览器管理器：启动/连接 Chrome、创建/管理标签页",
    "app/core/config.py": "⚙️ 核心配置：日志格式、超时时间、常量定义",
    "app/core/elements.py": "🔍 元素定位器：封装 CSS/XPath 查找、等待元素出现",
    "app/core/network_monitor.py": "🌐 网络监听器：拦截 XHR/Fetch 请求，捕获 SSE 流",
    "app/core/stream_monitor.py": "📡 流式监听器：监控 DOM 变化，计算文本 Diff 实现流式输出",
    "app/core/tab_pool.py": "🏊 标签池管理器：预创建标签页，复用连接，提升并发性能",
    "app/core/workflow.py": "🎬 工作流引擎 (主文件)：编排点击、输入、等待等操作",
    "app/core/workflow_editor.py": "✏️ 工作流编辑器：可视化编辑工作流步骤 (后端支持)",

    # ---------- app/models: 数据模型层 ----------
    "app/models": "[数据模型层] 定义数据结构",
    "app/models/__init__.py": "模块初始化：导出所有模型",
    "app/models/schemas.py": "📐 Pydantic 模型：定义请求体/响应体的数据格式和校验规则",

    # ---------- app/services: 业务逻辑层 ----------
    "app/services": "[业务逻辑层] 串联 Core 和 API",
    "app/services/__init__.py": "模块初始化",

    # services/config: 配置管理
    "app/services/config": "🔧 配置引擎模块：站点配置的读取/写入/校验",
    "app/services/config/__init__.py": "模块初始化：导出配置引擎",
    "app/services/config/engine.py": "⚙️ 配置引擎核心：解析 sites.json，管理站点生命周期",
    "app/services/config/managers.py": "👔 配置管理器：处理配置的增删改查和版本管理",
    "app/services/config/processors.py": "🔄 配置处理器：校验、迁移、合并配置数据",

    "app/services/config_engine.py": "💾 配置引擎入口：对外暴露的简化接口 (Facade 模式)",
    "app/services/extractor_manager.py": "🧩 提取器管理器：根据站点选择并调用合适的内容提取器",
    "app/services/request_manager.py": "🤵 请求管理器：调度浏览器标签、处理并发请求队列",

    # ---------- app/utils: 工具层 ----------
    "app/utils": "[工具层] 通用辅助函数",
    "app/utils/__init__.py": "模块初始化",
    "app/utils/paste.py": "📋 剪贴板工具：模拟 Ctrl+V 粘贴长文本",
    "app/utils/file_paste.py": "📎 文件粘贴工具：处理文件拖拽/粘贴上传",
    "app/utils/image_handler.py": "🖼️ 图片处理工具：格式转换、压缩、Base64 编解码",
    "app/utils/similarity.py": "📊 文本相似度工具：比较文本差异 (用于流式 Diff 计算)",

    "app/__init__.py": "模块初始化",

    # ==================== config 目录 ====================
    "config": "🔧 配置文件目录",
    "config/browser_config.json": "🖥️ 浏览器启动配置 (端口、User-Agent、窗口大小等)",
    "config/extractors.json": "🧩 提取器配置：各站点使用的提取模式映射",
    "config/image_presets.json": "🖼️ 图片预设配置：压缩参数、尺寸限制等",
    "config/sites.json": "🗂️ 站点数据库：URL、CSS 选择器、工作流步骤定义",

    # ==================== scripts 目录 ====================
    "scripts": "🛠️ 运维脚本目录",

    # ==================== static 目录 ====================
    "static": "🎨 前端静态资源 (Web UI 控制面板)",

    # css
    "static/css": "💅 样式表目录",
    "static/css/dashboard.css": "🎨 控制面板样式：布局、主题、响应式适配",

    # js
    "static/js": "⚡ 前端 JavaScript 代码",

    # js/components
    "static/js/components": "🧱 UI 组件库 (模块化拆分)",

    # js/components/panels: 配置面板
    "static/js/components/panels": "📊 配置子面板：各功能区的独立配置 UI",
    "static/js/components/panels/ExtractorPanel.js": "🧩 提取器配置面板：选择/切换提取模式",
    "static/js/components/panels/FilePastePanel.js": "📎 文件粘贴配置面板：设置文件上传行为",
    "static/js/components/panels/ImageConfigPanel.js": "🖼️ 图片配置面板：图片处理参数设置",
    "static/js/components/panels/SelectorPanel.js": "🎯 选择器配置面板：CSS/XPath 选择器编辑",
    "static/js/components/panels/StreamConfigPanel.js": "📡 流式配置面板：流式输出参数调整",
    "static/js/components/panels/WorkflowPanel.js": "🎬 工作流配置面板：编辑操作步骤",

    # js/components/shared: 共享组件
    "static/js/components/shared": "🔗 共享组件：可复用的基础 UI 组件",
    "static/js/components/shared/CollapsiblePanel.js": "📂 折叠面板组件：可展开/收起的内容容器",

    # js/components 其他
    "static/js/components/ConfigTab.js": "🔧 配置管理页面：站点配置的可视化编辑",
    "static/js/components/Dialogs.js": "💬 弹窗组件：确认框、输入框、提示框",
    "static/js/components/ExtractorTab.js": "🧩 提取器管理页面：提取器状态监控和配置",
    "static/js/components/LogsTab.js": "📋 实时日志页面：WebSocket 推送的日志流",
    "static/js/components/SettingsTab.js": "⚙️ 系统设置页面：全局参数配置",
    "static/js/components/Sidebar.js": "📌 侧边栏导航：页面切换菜单",
    "static/js/components/TabPoolTab.js": "🏊 标签池管理页面：查看/操作预创建的标签页",

    # js 其他
    "static/js/dashboard.js": "🚀 前端入口文件：初始化 App、路由、WebSocket",
    "static/js/icons.js": "🖼️ SVG 图标数据：内联图标资源",
    "static/js/workflow-editor-inject.js": "✏️ 工作流编辑器注入脚本：在目标页面注入可视化编辑器",

    # static 其他
    "static/index.html": "🏠 Web UI 主页入口 (SPA)",
    "static/tutorial.html": "📚 使用教程页面：功能说明和操作指南",
    "static/backup": "📦 备份的前端文件 (旧版本)",

    # ==================== tests 目录 ====================
    "tests": "🧪 单元测试目录",
    "tests/conftest.py": "🔩 Pytest 配置：公共 Fixture 定义",
    "tests/test_config_engine.py": "🧪 配置引擎测试：验证读写/校验逻辑",
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