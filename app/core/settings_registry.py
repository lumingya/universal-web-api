"""R2-4：环境变量登记表——本应用全部可配置环境变量的唯一来源。

每一项记录名称、类型、默认值、分组和说明，并由它派生出：
- ``.env.example``：由 ``scripts/build_env_example.py`` 生成，``--check`` 用于校验是否过期；
- 控制面板保存 .env 时的类型校验（``validate_env_values``，接入 ``app/api/system.py``）；
- 测试守护（``tests/test_settings_registry.py``）：代码里读取的每个环境变量都必须在此登记，
  控制面板 ENV_CONFIG_SCHEMA 的每一项也必须在此登记且类型一致。

新增环境变量时，在这里加一行，然后运行 ``python scripts/build_env_example.py``。
运行时读取暂时保持各处原样（os.getenv / AppConfig），逐步迁移到 ``get_setting()``。

类型：bool | int | float | str | enum | secret。secret 在 .env.example 中永远留空；
enum 的取值比较不区分大小写；int/float 可带 minimum / maximum。
``default`` 为 None 表示“未设置时由代码自行决定”（例如依赖其他开关的默认值）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y"})
FALSE_VALUES = frozenset({"0", "false", "no", "off", "n"})


@dataclass(frozen=True)
class Setting:
    name: str
    type: str
    default: Any
    category: str
    description: str
    choices: Tuple[str, ...] = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    example: Optional[str] = None
    active: bool = False
    aliases: Tuple[str, ...] = ()

    @property
    def secret(self) -> bool:
        return self.type == "secret"

    def example_value(self) -> str:
        if self.secret:
            return ""
        if self.example is not None:
            return self.example
        return format_value(self.default)


CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ('version', '版本信息'),
    ('update', '更新配置'),
    ('service', '服务配置'),
    ('auth', '认证配置'),
    ('cors', 'CORS 配置'),
    ('browser', '浏览器与标签页'),
    ('tabRecovery', '标签页自动恢复'),
    ('performance', '性能与内存（详见 docs/performance_refactor.md）'),
    ('proxy', '代理配置'),
    ('dashboard', 'Dashboard 配置'),
    ('restartGuard', '服务守护'),
    ('ai', 'AI 分析配置'),
    ('toolCalling', '函数调用'),
    ('commands', '命令系统'),
    ('arena', 'Arena 与代理轮换'),
    ('media', '资源预算与本地媒体访问'),
    ('files', '配置文件路径'),
    ('logging', '日志'),
    ('startup', '启动器与进程'),
    ('dangerZone', '高危开关'),
)

# 代码会读取、但不属于本应用配置项的环境变量（终端检测、系统路径、进程内部标志）
EXTERNAL_ENV: Dict[str, str] = {
    'ANSICON': '终端颜色检测（Windows ANSICON）',
    'LOCALAPPDATA': 'Windows 用户数据目录（查找浏览器可执行文件）',
    'NO_COLOR': '终端颜色检测（no-color.org 约定）',
    'PYTHONIOENCODING': '子进程输出编码（启动器设置）',
    'PYTHONUTF8': 'Python UTF-8 模式（启动器设置）',
    'TERM_PROGRAM': '终端类型检测（VS Code 等）',
    'UWAPI_IS_RESTART': '内部标志：由重启保护在自重启时设置',
    'WT_SESSION': '终端类型检测（Windows Terminal）',
}

SETTINGS: Tuple[Setting, ...] = (
    Setting('CURRENT_VERSION', 'str', '', 'version', '旧版版本号记录：仅在缺少 VERSION 文件时由更新器读取（实际版本以 VERSION 文件为准）', example='1.01', active=True),
    Setting('AUTO_UPDATE_ENABLED', 'bool', True, 'update', '启用自动更新：开启后 start.bat 会在启动前检查并自动应用更新；关闭后仍会在服务启动后检查新版本，只提示手动更新', example='true', active=True),
    Setting('GITHUB_REPO', 'str', 'lumingya/universal-web-api', 'update', 'GitHub 仓库：自动更新和启动版本检查使用的仓库，格式为 owner/repo', active=True),
    Setting('UPDATE_PRESERVE', 'str', '', 'update', '更新时保留的文件/目录（逗号分隔）', example='chrome_profile,venv,.env.local,logs', active=True),
    Setting('APP_DEBUG', 'bool', False, 'service', '调试模式：开启 API 文档和详细错误', example='false', active=True),
    Setting('APP_HOST', 'str', '127.0.0.1', 'service', '监听地址：127.0.0.1 仅本机（推荐）；0.0.0.0 允许外部访问，此时必须同时启用控制面板认证和服务 API 认证，否则服务拒绝启动', active=True),
    Setting('APP_PORT', 'int', 8199, 'service', '监听端口', minimum=1, maximum=65535, active=True),
    Setting('LOG_LEVEL', 'enum', 'INFO', 'service', '日志级别', choices=('DEBUG', 'INFO', 'WARNING', 'ERROR'), active=True),
    Setting('PUBLIC_BASE_URL', 'str', 'http://127.0.0.1:8199', 'service', '公开访问地址：用于生成返回给客户端的可访问链接，例如图片下载地址', example='', active=True),
    Setting('ALLOW_INSECURE_PUBLIC_BIND', 'bool', False, 'auth', '仅在完全可信的隔离网络中、明确知道风险时才设为 true： 允许「非回环监听 + 未启用认证」启动（不推荐）。', example='false', active=True),
    Setting('AUTH_ENABLED', 'bool', False, 'auth', '启用服务 API 认证：保护 /v1、/url/.../v1、/tab/.../v1 等对外调用接口', example='false', active=True),
    Setting('AUTH_TOKEN', 'secret', '', 'auth', '服务 API Bearer Token：AUTH_ENABLED=true 时必须设置；可提供给项目 API 使用者', active=True),
    Setting('DASHBOARD_AUTH_ENABLED', 'bool', False, 'auth', '启用控制面板认证：保护控制面板的配置、日志、命令和标签页管理接口；未设置时兼容沿用服务 API 认证开关', example='', active=True),
    Setting('DASHBOARD_AUTH_TOKEN', 'secret', '', 'auth', '控制面板访问密钥：DASHBOARD_AUTH_ENABLED=true 时必须设置；留空时兼容沿用服务 API Bearer Token', active=True),
    Setting('UWAPI_PROXY_SECRET', 'secret', '', 'auth', '本机转发代理与服务共享的密钥：只有回环来源且携带匹配密钥时才采信 X-Forwarded-For 等转发头'),
    Setting('CORS_ENABLED', 'bool', True, 'cors', '启用 CORS', example='true', active=True),
    Setting('CORS_ORIGINS', 'str', '', 'cors', '允许的跨域源：多个用逗号分隔（如 https://app.example.com）；留空 = 不允许跨源网页调用（控制面板同源无需配置）。不推荐 *', active=True),
    Setting('AUTO_OPEN_BROWSER', 'bool', True, 'browser', '启动时浏览器里没有已打开的页面，就自动打开各站点的起始页（自重启时不打开）'),
    Setting('BROWSER_PATH', 'str', '', 'browser', '自定义浏览器路径：可选，留空时自动检测 Chrome、Edge、Brave 等浏览器'),
    Setting('BROWSER_PORT', 'int', 9222, 'browser', 'Chrome 调试端口', minimum=1024, maximum=65535, active=True),
    Setting('BROWSER_PROFILE_DIR', 'str', '', 'browser', '浏览器配置目录：留空时使用项目内的 chrome_profile 目录'),
    Setting('BROWSER_PROFILE_NAME', 'str', '', 'browser', '浏览器配置名称：例如 Default、Profile 1'),
    Setting('BROWSER_RECONNECT_QUIT_OLD_HANDLE', 'bool', False, 'browser', '重连浏览器时主动关闭旧的连接句柄'),
    Setting('BROWSER_WATCHDOG_ACTIVE_INTERVAL', 'float', 3.0, 'browser', '浏览器连接看门狗：近期有活动时的检查间隔（秒）', minimum=0.5),
    Setting('BROWSER_WATCHDOG_IDLE_INTERVAL', 'float', 5.0, 'browser', '浏览器连接看门狗：空闲时的检查间隔（秒）', minimum=0.5),
    Setting('BROWSER_WATCHDOG_RECENT_ACTIVITY_WINDOW', 'float', 60.0, 'browser', '多久（秒）以内有请求算“近期有活动”', minimum=1),
    Setting('MAX_REQUEST_EXECUTE_TIME_SEC', 'float', None, 'browser', '单个请求的最长执行时间（秒）；设置后覆盖 browser_config.json 中的同名常量', minimum=1),
    Setting('MAX_TABS', 'int', None, 'browser', '标签页池上限；设置后覆盖 browser_config.json 的 TAB_POOL_MAX_TABS', minimum=1),
    Setting('MIN_TABS', 'int', None, 'browser', '标签页池下限；设置后覆盖 browser_config.json 的 TAB_POOL_MIN_TABS', minimum=0),
    Setting('PROFILE_CLEAN_ENABLED', 'bool', False, 'browser', '启动时清理浏览器缓存：每次启动脚本时自动清理 ShaderCache、GPUCache 等垃圾缓存，保留登录态和 Cookie', example='false', active=True),
    Setting('REQUEST_ZOMBIE_TTL', 'int', 600, 'browser', '请求超过该时间（秒）仍未结束即判定为僵尸并清理', minimum=1),
    Setting('TAB_AUTO_ACTIVATE_ON_ACQUIRE', 'bool', False, 'browser', '借用标签页执行请求时自动切到前台'),
    Setting('TAB_RECOVERY_API_KEY', 'secret', '', 'tabRecovery', 'API Key：外部 API 的 Bearer Key，仅在上面的 API 地址非空时使用。'),
    Setting('TAB_RECOVERY_API_URL', 'str', '', 'tabRecovery', 'API 地址：OpenAI 兼容的 chat/completions 完整地址。⚠️ 填写外部地址后，出错标签页的截图会被发送到该外部服务。留空 = 本地模式：调用本服务自身的 /v1/chat/completions，用池中其他空闲标签页完成判断（自动复用 AUTH_TOKEN）。'),
    Setting('TAB_RECOVERY_ENABLED', 'bool', False, 'tabRecovery', '启用 AI 恢复判断：关闭时：旧工作线程退出后直接解除隔离并自动重入池，不截图、不调用任何 AI API。隔离机制本身始终生效。', example='false', active=True),
    Setting('TAB_RECOVERY_MAX_ATTEMPTS', 'int', 1, 'tabRecovery', '每标签页最大恢复次数：恢复后再次超时即视为不可恢复，永久隔离且不再调用 AI API。设为 0 表示不尝试恢复。', minimum=0, active=True),
    Setting('TAB_RECOVERY_MODEL', 'str', 'gpt-4o', 'tabRecovery', '模型名称：判断使用的模型名，需支持图片输入。', active=True),
    Setting('TAB_RECOVERY_REFRESH_ON_UNKNOWN', 'bool', True, 'tabRecovery', '无法判断时仍尝试刷新：截图、AI 调用或解析失败等「无法判断」的情况：开启=刷新后重入池，关闭=直接永久隔离。', example='true', active=True),
    Setting('TAB_RECOVERY_TIMEOUT_SEC', 'int', 120, 'tabRecovery', 'AI 判断超时：本地模式要经过完整浏览器往返，不建议设置过短。', minimum=1, active=True),
    Setting('TAB_RECOVERY_WORKER_EXIT_WAIT_SEC', 'int', 600, 'tabRecovery', '等待旧工作线程退出：超过该时长旧线程仍未退出，则永久隔离该标签页。', minimum=0, active=True),
    Setting('BROWSER_CDP_RECYCLE_AFTER_REQUESTS', 'int', 20, 'performance', '标签页累计处理多少个请求后回收一次 CDP 会话', minimum=1, active=True),
    Setting('BROWSER_CDP_RECYCLE_DOM_CHECK_SEC', 'float', 300.0, 'performance', '检查 DOM 节点数的间隔（秒）', minimum=1, example='300', active=True),
    Setting('BROWSER_CDP_RECYCLE_DOM_NODES', 'int', 150000, 'performance', '页面 DOM 节点数超过该值时触发回收', minimum=1, active=True),
    Setting('BROWSER_CDP_RECYCLE_ENABLED', 'bool', True, 'performance', '回收空闲标签页的 CDP 会话：断开并重建连接（不刷新页面），释放被钉住的旧 DOM / RemoteObject', example='true', active=True),
    Setting('BROWSER_CDP_RECYCLE_IDLE_SEC', 'float', 30.0, 'performance', '标签页空闲多久（秒）后才允许回收 CDP 会话', minimum=0, example='30', active=True),
    Setting('BROWSER_CDP_RECYCLE_INTERVAL_SEC', 'float', 1800.0, 'performance', '定期回收 CDP 会话的间隔（秒）', minimum=1, example='1800', active=True),
    Setting('BROWSER_IDLE_FREEZE_AFTER_SEC', 'float', 60.0, 'performance', '标签页空闲多久（秒）后冻结', minimum=0, example='60', active=True),
    Setting('BROWSER_IDLE_FREEZE_ENABLED', 'bool', True, 'performance', '冻结空闲标签页（Page.setWebLifecycleState=frozen），被占用或页面检查前自动恢复；显式开启周期保活会让冻结基本失效', example='true', active=True),
    Setting('BROWSER_IDLE_FREEZE_REQUIRE_HIDDEN', 'bool', True, 'performance', '只冻结用户看不到的标签页（后台标签或最小化窗口）', example='true', active=True),
    Setting('BROWSER_IDLE_MEMORY_PURGE_AFTER_SEC', 'float', 300.0, 'performance', '标签页空闲多久（秒）后才参与内存清理', minimum=0),
    Setting('BROWSER_IDLE_MEMORY_PURGE_ENABLED', 'bool', True, 'performance', '空闲时定期清理浏览器内存（不影响正在执行的工作流）'),
    Setting('BROWSER_IDLE_MEMORY_PURGE_INTERVAL_SEC', 'float', 180.0, 'performance', '内存清理的检查间隔（秒）', minimum=1),
    Setting('BROWSER_MEMORY_SAVER', 'bool', False, 'performance', '省内存模式：不再周期性唤醒后台标签页（页面检查与真实工作流仍按需唤醒）'),
    Setting('BROWSER_PROCESS_MODEL', 'str', '', 'performance', 'Chromium 进程模型（可选）：per-site = 同站点标签页共用渲染进程；limit:N = 最多 N 个渲染进程 实测省 10%~20% 内存；同一站点并发标签页较多时会互相拖慢，默认留空（Chromium 默认）', active=True),
    Setting('CDP_HYGIENE_ENABLED', 'bool', True, 'performance', 'CDP 卫生总开关：释放用完的 RemoteObject，并限制 Network 缓冲区（详见 docs/performance_refactor.md）', example='true', active=True),
    Setting('CDP_NETWORK_MAX_RESOURCE_BUFFER_MB', 'float', 16.0, 'performance', 'Network.enable 的单个资源缓冲区上限（MB，0 = 不限制）', minimum=0, example='16', active=True),
    Setting('CDP_NETWORK_MAX_TOTAL_BUFFER_MB', 'float', 32.0, 'performance', 'Network.enable 的总缓冲区上限（MB，0 = 不限制）', minimum=0, example='32', active=True),
    Setting('CDP_RELEASE_JS_OBJECTS', 'bool', True, 'performance', 'run_js 返回的对象/数组 RemoteObject 用完即释放', example='true', active=True),
    Setting('PANEL_MEMORY_METRIC', 'enum', 'fast', 'performance', '面板内存统计口径：fast（默认，开销极低；Windows 用 private，Linux 用 rss-shared）、uss（旧口径，开销大）、rss', choices=('fast', 'uss', 'rss'), active=True),
    Setting('REQUEST_HISTORY_SAVE_DEBOUNCE_SEC', 'int', 5, 'performance', '请求历史落盘去抖（秒）：窗口内的多次更新合并为一次写入，进程退出时自动刷盘', active=True),
    Setting('STREAM_PAGE_SNAPSHOT_ENABLED', 'bool', True, 'performance', '流式监控页面快照：一次 CDP 调用完成“选元素 + 生成状态 + 提取文本/图片”（默认 true）', example='true', active=True),
    Setting('PROXY_ADDRESS', 'str', 'socks5://127.0.0.1:1080', 'proxy', '代理地址：支持 socks5://、http:// 或 https://；Python 下载会通过 SOCKS 代理解析域名', example='', active=True),
    Setting('PROXY_BYPASS', 'str', 'localhost,127.0.0.1', 'proxy', '绕过代理：浏览器和 Python 下载均不走代理的地址，多个用逗号分隔', example='localhost,127.0.0.1,::1', active=True),
    Setting('PROXY_ENABLED', 'bool', False, 'proxy', '启用代理：开启后浏览器和 Python 后端的远程资源下载将共用该代理', example='false', active=True),
    Setting('DASHBOARD_ENABLED', 'bool', True, 'dashboard', '启用 Dashboard', example='true', active=True),
    Setting('DASHBOARD_FILE', 'str', 'static/index.html', 'dashboard', 'Dashboard 文件路径', active=True),
    Setting('DEBUG', 'bool', False, 'dashboard', '调试模式：开启调试接口与更详细的日志'),
    Setting('RESTART_PROXY_MAX_BUFFER_MB', 'float', 256.0, 'restartGuard', '定时重启守护代理的全局请求缓冲上限（MiB）', minimum=1, example='256', active=True),
    Setting('RESTART_PROXY_MAX_CONNECTIONS', 'int', 64, 'restartGuard', '定时重启守护代理的最大并发连接数', minimum=1, active=True),
    Setting('SCHEDULED_RESTART_DRAIN_TIMEOUT_SECONDS', 'int', 1800, 'restartGuard', '最长排空等待：到期后仍有工作流时的最大等待时间。超时后本轮不重启，以免中断工作流，并在下一周期重试。', minimum=0, active=True),
    Setting('SCHEDULED_RESTART_ENABLED', 'bool', False, 'restartGuard', '启用定时后端重启：开启后由启动器保持对外端口；后端重启期间进入的新请求会等待新实例就绪后继续执行。', example='false', active=True),
    Setting('SCHEDULED_RESTART_INTERVAL_SECONDS', 'int', 10800, 'restartGuard', '重启间隔：默认 10800 秒（3 小时）。每次成功重启后重新计时。', minimum=300, active=True),
    Setting('SCHEDULED_RESTART_TAB_STATE_POLICY', 'enum', 'preserve', 'restartGuard', '标签页状态策略：当前仅支持 preserve：不关闭浏览器，也不清理标签页。该独立设置为后续加入标签页状态清理策略预留。', choices=('preserve',), active=True),
    Setting('CANVAS_IMAGE_MAX_SIZE', 'int', 1024, 'ai', 'Canvas 图片最大边长：浏览器内下载 URL 图片时，Canvas 压缩后的最长边。默认 1024；调大可保留更多细节，但会增加返回体和内存占用。', minimum=1, active=True),
    Setting('HELPER_API_KEY', 'secret', '', 'ai', 'API Key', active=True),
    Setting('HELPER_API_PROVIDER', 'enum', 'auto', 'ai', 'API 提供商：支持 auto、openai、gemini、claude', choices=('auto', 'openai', 'gemini', 'claude'), active=True),
    Setting('HELPER_BASE_URL', 'str', '', 'ai', 'API 地址：留空则不启用外部辅助 AI。示例：http://127.0.0.1:8199/url/gemini.com/v1', example='http://127.0.0.1:8199/url/gemini.com/v1', active=True),
    Setting('HELPER_MODEL', 'str', 'gpt-4', 'ai', '模型名称', example='gemini-3.0-pro', active=True),
    Setting('MAX_HTML_CHARS', 'int', 120000, 'ai', 'HTML 最大字符数：超过会截断以节省 Token', minimum=10000, active=True),
    Setting('TOOL_CALLING_ALLOW_MEDIA_POSTPROCESS', 'bool', False, 'toolCalling', '允许媒体后处理：开启后，函数调用隐藏回合也会执行媒体二次提取、占位补偿和 Markdown 媒体注入。兼容旧行为，但更容易污染 tool payload；默认关闭。', example='false', active=True),
    Setting('TOOL_CALLING_ALLOW_PARTIAL_SUCCESS', 'bool', True, 'toolCalling', '部分工具调用校验失败时，仍返回通过校验的调用'),
    Setting('TOOL_CALLING_DEGRADE_ON_FAILURE', 'bool', False, 'toolCalling', '工具调用重试仍失败时降级为普通文本回复'),
    Setting('TOOL_CALLING_INTERNAL_RETRY_MAX', 'int', 2, 'toolCalling', '内部修复重试次数：函数调用结果校验失败时，自动修复后再次重试的次数。0 表示关闭自动修复；默认 2；最大 5。', minimum=0, maximum=5, active=True),
    Setting('TOOL_CALLING_MAX_ARGUMENT_CHARS', 'int', 50000, 'toolCalling', '单个工具调用参数的最大字符数', minimum=256, maximum=500000),
    Setting('TOOL_CALLING_MAX_ARGUMENT_DEPTH', 'int', 20, 'toolCalling', '工具调用参数 JSON 的最大嵌套深度', minimum=2, maximum=100),
    Setting('TOOL_CALLING_MAX_ARGUMENT_NODES', 'int', 4000, 'toolCalling', '工具调用参数 JSON 的最大节点数', minimum=16, maximum=50000),
    Setting('TOOL_CALLING_MAX_TOOL_RESULT_CHARS', 'int', 300000, 'toolCalling', '单条 Tool Result 上限：单条函数调用结果超过此字符数时，后端会直接返回明确错误，避免把超大结果继续塞给网页模型。默认 300000，可按需要调大。', minimum=1, active=True),
    Setting('TOOL_CALLING_PROMPT_PADDING_ENABLED', 'bool', True, 'toolCalling', '注入预填充/尾部提示词：开启后，会继续注入函数调用的预填充与尾部提示词；关闭后仅保留重试策略相关提示词。默认开启。', example='true', active=True),
    Setting('TOOL_CALLING_PROMPT_PADDING_OBFUSCATE', 'bool', False, 'toolCalling', '预填充乱序零宽：开启后，函数调用的预填充和尾部提示词会随机乱序，并插入少量零宽字符。仅影响额外 padding，不改动工具定义；默认关闭。', example='false', active=True),
    Setting('TOOL_CALLING_REJECTED_ARGUMENT_PREVIEW_CHARS', 'int', 500, 'toolCalling', '日志与错误信息中被拒参数的预览长度', minimum=0),
    Setting('TOOL_CALLING_RETRY_STRATEGY', 'enum', 'focused_repair', 'toolCalling', '重试策略：聚焦修复只发送必要的修复信息；完整上下文会把原对话和修复反馈一起发给模型。', choices=('focused_repair', 'full_context'), active=True),
    Setting('TOOL_CALLING_SANITIZE_ASSISTANT_CONTENT', 'bool', True, 'toolCalling', '解析前清洗回复：开启后，函数调用在解析 assistant 内容前会移除占位链接和尾部媒体 Markdown。推荐保持开启；只有需要完全回退旧行为时再关闭。', example='true', active=True),
    Setting('CMD_ACTIVATE_TAB_ON_COMMAND', 'bool', False, 'commands', '执行命令时把目标标签页切到前台'),
    Setting('CMD_APPEND_FILE_BASE_DIR', 'str', '', 'commands', '“追加写文件”命令允许写入的根目录；留空使用默认目录'),
    Setting('CMD_ASYNC_MAX_WORKERS', 'int', 20, 'commands', '异步命令的最大并发线程数', minimum=1),
    Setting('CMD_ENGINE_AUTO_START', 'bool', False, 'commands', '导入模块时就启动命令调度器（默认由应用生命周期负责启动和停止，仅嵌入/调试时使用）'),
    Setting('CMD_EXECUTE_WORKFLOW_TIMEOUT_SEC', 'float', 45.0, 'commands', '命令里“执行工作流”步骤的默认超时（秒）', minimum=1),
    Setting('CMD_PAGE_CHECK_FAILURE_BACKOFF_SEC', 'float', 30.0, 'commands', '页面检查失败后的退避时间（秒）', minimum=0),
    Setting('CMD_PAGE_CHECK_JS_TIMEOUT_SEC', 'float', None, 'commands', '页面检查脚本的超时（秒）；留空使用后台唤醒脚本的内置超时', minimum=0.1),
    Setting('CMD_PAGE_CHECK_REFRESH_GRACE_SEC', 'float', 2.0, 'commands', '页面刷新后多久（秒）内不做页面检查', minimum=0),
    Setting('CMD_PERIODIC_KEEPALIVE_ENABLED', 'bool', None, 'commands', '周期性保活后台标签页；留空时自动决定（开启省内存或空闲冻结时默认关闭，否则开启）'),
    Setting('CMD_PERIODIC_KEEPALIVE_INTERVAL_SEC', 'float', 20.0, 'commands', '周期保活的间隔（秒）', minimum=1),
    Setting('CMD_PERIODIC_SUMMARY_LOG_ENABLED', 'bool', False, 'commands', '周期性输出命令引擎摘要日志'),
    Setting('CMD_PYTHON_SANDBOX_ALLOWED_IMPORTS', 'str', '', 'commands', 'Python 命令沙箱额外允许导入的模块（逗号分隔）'),
    Setting('CMD_PYTHON_SANDBOX_ALLOW_REQUESTS', 'bool', True, 'commands', 'Python 命令沙箱是否允许使用 requests 发起网络请求'),
    Setting('CMD_REQUEST_PRIORITY_BASELINE', 'int', 2, 'commands', '命令触发的请求的基准优先级'),
    Setting('CMD_TAB_POOL_AUTO_REFRESH', 'bool', True, 'commands', '命令引擎定期刷新标签页池'),
    Setting('CMD_TAB_POOL_REFRESH_INTERVAL_SEC', 'float', 5.0, 'commands', '标签页池刷新间隔（秒）', minimum=0.5),
    Setting('CMD_USE_FOCUS_EMULATION_ON_COMMAND', 'bool', True, 'commands', '执行命令时启用焦点模拟（不切前台也让页面认为自己处于焦点）'),
    Setting('CMD_WAKE_TAB_BEFORE_PAGE_CHECK', 'bool', True, 'commands', '页面检查前先唤醒后台标签页'),
    Setting('CMD_WAKE_TAB_MIN_INTERVAL_SEC', 'float', 2.0, 'commands', '同一标签页两次唤醒的最小间隔（秒）', minimum=0),
    Setting('ARENA_EVENT_BRIDGE_BACKUPS', 'int', 2, 'arena', '事件桥接文件保留的轮转备份数', minimum=1),
    Setting('ARENA_EVENT_BRIDGE_COMMAND_TEXT_LIMIT', 'int', 60000, 'arena', '传给命令的结果文本最大字符数', minimum=0),
    Setting('ARENA_EVENT_BRIDGE_DEDUPE_MAX_ENTRIES', 'int', 4096, 'arena', '事件去重缓存的最大条目数', minimum=1),
    Setting('ARENA_EVENT_BRIDGE_EMIT_PARTIAL', 'bool', False, 'arena', '回复完成前是否也写出中间结果'),
    Setting('ARENA_EVENT_BRIDGE_ENABLED', 'bool', True, 'arena', '把 Arena 结果写入事件桥接文件，供外部程序或命令读取'),
    Setting('ARENA_EVENT_BRIDGE_MAX_BYTES', 'int', 52428800, 'arena', '事件桥接文件达到该大小（字节，最小 1MB）后轮转', minimum=1048576),
    Setting('ARENA_EVENT_BRIDGE_PATH', 'str', '', 'arena', '事件桥接文件路径；留空使用默认位置'),
    Setting('ARENA_EVENT_BRIDGE_TRIGGER_COMMANDS', 'bool', True, 'arena', '写出事件后是否触发匹配的命令'),
    Setting('ARENA_MODELS_CACHE_PATH', 'str', '', 'arena', 'Arena 模型列表缓存文件；留空为 scripts/arena_models_cache.json'),
    Setting('ARENA_MODEL_ALIAS_OVERRIDES_PATH', 'str', '', 'arena', 'Arena 模型别名本地覆盖文件；留空为 config/arena_model_aliases.local.json'),
    Setting('ARENA_MODEL_CATALOG_CONFIG_PATH', 'str', '', 'arena', 'Arena 模型目录本地配置文件；留空为 config/arena_model_catalog.local.json'),
    Setting('ARENA_MODEL_MAP_PATH', 'str', '', 'arena', '事件桥接使用的模型名映射文件路径（可选）'),
    Setting('CLASH_API', 'str', 'http://127.0.0.1:9097', 'arena', 'Arena 代理轮换使用的 Clash 外部控制器地址', aliases=('ARENA_CLASH_API',)),
    Setting('CLASH_PROXY_POOL', 'str', '', 'arena', '参与轮换的节点名单：JSON 数组或逗号分隔；留空使用内置默认名单', aliases=('ARENA_PROXY_POOL',)),
    Setting('CLASH_SECRET', 'secret', '1', 'arena', 'Clash 外部控制器密钥', aliases=('ARENA_CLASH_SECRET',)),
    Setting('CLASH_SELECTOR', 'str', '主代理', 'arena', 'Clash 中用于切换节点的策略组名称', aliases=('ARENA_CLASH_SELECTOR',)),
    Setting('MEDIA_REQUIRE_AUTH', 'bool', False, 'media', '/media 与 /download_images 是否需要令牌（AUTH_TOKEN / DASHBOARD_AUTH_TOKEN）或本机直连访问。 开启后，第三方聊天前端直接用 <img src> 引用生成媒体将无法加载。', example='false', active=True),
    Setting('MEDIA_TRANSCODE_MAX_CONCURRENCY', 'int', 2, 'media', '媒体转码（ffmpeg）的全局并发上限', minimum=1, active=True),
    Setting('MEDIA_TRANSCODE_MAX_SOURCE_MB', 'float', 200.0, 'media', '源文件超过该大小（MiB）时拒绝转码', minimum=1, example='200', active=True),
    Setting('MEDIA_TRANSCODE_QUEUE_TIMEOUT_SEC', 'float', 10.0, 'media', '媒体转码排队超过该时间（秒）返回 503', minimum=0, example='10', active=True),
    Setting('RESPONSES_STATE_MAX_ENTRY_MB', 'float', 8.0, 'media', 'Responses API 续接历史：单条上限（MiB）', minimum=0, example='8', active=True),
    Setting('RESPONSES_STATE_MAX_TOTAL_MB', 'float', 64.0, 'media', 'Responses API 续接历史：总量上限（MiB）', minimum=0, example='64', active=True),
    Setting('TAB_ACQUIRE_MAX_WAITERS', 'int', 128, 'media', '标签页 acquire 等待者总量上限（通用/按编号/按域名/按路由组合计），满了立即 503；0 = 不限', active=True),
    Setting('COMMANDS_CONFIG_FILE', 'str', '', 'files', '命令配置文件；留空为 config/commands.json'),
    Setting('COMMANDS_LOCAL_FILE', 'str', '', 'files', '命令本地覆盖文件；留空为 config/commands.local.json'),
    Setting('FILE_PASTE_PDF_FONT_PATH', 'str', '', 'files', '文件粘贴转 PDF 时使用的中文字体；留空自动查找'),
    Setting('PARSERS_CONFIG_FILE', 'str', '', 'files', '解析器配置文件；留空为 config/parsers.json'),
    Setting('PASTE_TEMP_CLEANUP_MIN_AGE_SECONDS', 'float', 900.0, 'files', '粘贴产生的临时文件至少保留多久（秒）才清理', minimum=0),
    Setting('RUNTIME_DB_PATH', 'str', '', 'files', '运行时数据库（请求历史、累计统计，SQLite）；留空为 config/runtime.sqlite3'),
    Setting('SITES_CONFIG_DIR', 'str', 'config/sites', 'files', '站点配置目录（每站点一个文件）', active=True),
    Setting('SITES_CONFIG_FILE', 'str', '', 'files', '旧版单文件站点配置，仅作为自动迁移来源；留空为 config/sites.json'),
    Setting('SITES_LOCAL_FILE', 'str', '', 'files', '站点本地覆盖文件；留空为 config/sites.local.json'),
    Setting('SITE_RULES_FILE', 'str', '', 'files', '站点规则文件；留空为 config/site_rules.json'),
    Setting('TEMP_CLEANUP_MIN_AGE_SECONDS', 'float', 3600.0, 'files', '其他临时文件至少保留多久（秒）才清理', minimum=0),
    Setting('LOG_BACKUP_COUNT', 'int', 5, 'logging', '日志轮转保留的备份数', minimum=1),
    Setting('LOG_DIR', 'str', '', 'logging', '日志目录；留空为项目下的 logs/'),
    Setting('LOG_FILE', 'str', '', 'logging', '日志文件路径；留空为日志目录下的默认文件'),
    Setting('LOG_MAX_BYTES', 'int', 5242880, 'logging', '单个日志文件达到该大小（字节）后轮转', minimum=1),
    Setting('PIP_MIRROR_URL', 'str', '', 'startup', '启动器安装依赖时使用的 pip 镜像地址；留空使用内置默认镜像'),
    Setting('PYTHON_INSTALL_VERSION', 'str', '3.13.6', 'startup', '启动器自动安装 Python 时使用的版本', active=True),
    Setting('UWAPI_WORKER_MODE', 'enum', 'inproc', 'startup', '进程模型（实验性）：inproc 为单进程（默认）；process 为 API 进程与持有浏览器的 worker 进程分离，worker 由 API 进程自动启动', choices=('inproc', 'process')),
    Setting('UWAPI_WORKER_ROLE', 'str', '', 'startup', '（内部）进程角色，worker 进程由 API 进程启动时设为 worker，请勿手动设置'),
    Setting('UWAPI_WORKER_URL', 'str', '', 'startup', '（内部）worker 地址；留空时由 API 进程启动 worker 后自动设置'),
    Setting('UWAPI_WORKER_TOKEN', 'secret', '', 'startup', '（内部）API 进程与 worker 之间的鉴权令牌，启动时自动生成'),
    Setting('UWAPI_WORKER_PORT', 'int', 0, 'startup', 'worker 监听的本机端口；0 表示自动选择空闲端口', minimum=0, maximum=65535),
    Setting('UWAPI_DOTENV_OVERRIDE', 'bool', False, 'startup', '为 true 时 .env 中的值覆盖进程里已有的同名环境变量'),
    Setting('UWAPI_PUBLIC_BIND_HOST', 'str', '', 'startup', '（内部）启动器传给服务进程的实际监听地址，供启动安全检查使用'),
    Setting('CMD_ALLOW_UNSAFE_PYTHON_COMMANDS', 'bool', False, 'dangerZone', '允许非沙箱 Python 命令：⚠️ 高危：开启后命令引擎会跳过 Python 脚本安全校验，以完整 __builtins__ 执行任意脚本（可读写文件、发起网络请求、执行系统命令）。仅在站点配置完全可信、且确实需要平滑鼠标轨迹/物理点击时开启。默认关闭。', example='false', active=True),
    Setting('PARSER_INSTALL_ENABLED', 'bool', False, 'dangerZone', '允许从控制面板安装第三方解析器代码（安全开关，默认关闭）'),
)


BY_NAME: Dict[str, Setting] = {setting.name: setting for setting in SETTINGS}
ALIAS_TO_NAME: Dict[str, str] = {alias: setting.name for setting in SETTINGS for alias in setting.aliases}
CATEGORY_LABELS: Dict[str, str] = dict(CATEGORIES)


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def find(name: str) -> Optional[Setting]:
    return BY_NAME.get(name) or BY_NAME.get(ALIAS_TO_NAME.get(name, ""))


def parse_value(setting: Setting, raw: Any) -> Any:
    """把 .env 中的原始值解析成登记的类型；非法时抛 ValueError（消息可直接展示给用户）。"""
    text = format_value(raw).strip() if not isinstance(raw, str) else raw.strip()
    if text == "":
        return setting.default
    kind = setting.type
    if kind == "bool":
        lowered = text.lower()
        if lowered in TRUE_VALUES:
            return True
        if lowered in FALSE_VALUES:
            return False
        raise ValueError(f"{setting.name} 应为 true/false（也接受 1/0、yes/no、on/off），实际为 {text!r}")
    if kind in ("int", "float"):
        try:
            number = float(text)
        except ValueError:
            raise ValueError(f"{setting.name} 应为数字，实际为 {text!r}") from None
        if kind == "int":
            if not number.is_integer():
                raise ValueError(f"{setting.name} 应为整数，实际为 {text!r}")
            number = int(number)
        if setting.minimum is not None and number < setting.minimum:
            raise ValueError(f"{setting.name} 不能小于 {format_value(setting.minimum)}，实际为 {text}")
        if setting.maximum is not None and number > setting.maximum:
            raise ValueError(f"{setting.name} 不能大于 {format_value(setting.maximum)}，实际为 {text}")
        return number
    if kind == "enum":
        for choice in setting.choices:
            if choice.lower() == text.lower():
                return choice
        raise ValueError(f"{setting.name} 只能是 {' / '.join(setting.choices)} 之一，实际为 {text!r}")
    return text


def validate_env_values(values: Mapping[str, Any], keys: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """校验登记过的键的取值，返回 {键: 错误信息}；未登记的键不在此处理（由调用方决定）。

    ``keys`` 限定只校验这些键（例如控制面板只校验本次新增或修改过的键，存量值原样放行）。
    """
    errors: Dict[str, str] = {}
    for key in (keys if keys is not None else values.keys()):
        setting = find(key)
        if setting is None or key not in values:
            continue
        try:
            parse_value(setting, values[key])
        except ValueError as exc:
            errors[key] = str(exc)
    return errors


def get_setting(name: str, env: Optional[Mapping[str, str]] = None) -> Any:
    """按登记的类型与默认值读取；取值非法时回退默认值（与现有各处读取的容错行为一致）。"""
    setting = find(name)
    if setting is None:
        raise KeyError(f"未登记的环境变量: {name}")
    env = os.environ if env is None else env
    for key in (setting.name, *setting.aliases):
        if key in env:
            try:
                return parse_value(setting, env[key])
            except ValueError:
                return setting.default
    return setting.default


ENV_EXAMPLE_HEADER = """# ======================================
# Universal Web-to-API 配置文件模板
# ======================================
# 复制为 .env 后按需修改；本模板的默认值即可安全地在本机直接使用。
# 布尔项只接受 true / false（也接受 1/0、yes/no、on/off）。
# 认证开关写成其它值（例如 your-secret-here）会被视为配置错误并拒绝启动。
#
# 本文件由 scripts/build_env_example.py 根据 app/core/settings_registry.py 生成，请勿手改；
# 以 “# 名称=值” 形式出现的是可选项，去掉行首的 # 即可启用，未写出的变量使用内置默认值。
"""


def render_env_example() -> str:
    parts = [ENV_EXAMPLE_HEADER]
    for key, label in CATEGORIES:
        items = [s for s in SETTINGS if s.category == key]
        if not items:
            continue
        parts.append(f"\n# ========== {label} ==========\n")
        for setting in items:
            notes = [setting.description]
            meta = [f"类型: {setting.type}"]
            if setting.choices:
                meta.append("可选: " + " / ".join(setting.choices))
            if setting.minimum is not None or setting.maximum is not None:
                low = format_value(setting.minimum) if setting.minimum is not None else ""
                high = format_value(setting.maximum) if setting.maximum is not None else ""
                meta.append(f"范围: {low}..{high}")
            if not setting.secret and setting.default not in (None, ""):
                meta.append(f"默认: {format_value(setting.default)}")
            if setting.aliases:
                meta.append("旧名: " + ", ".join(setting.aliases))
            parts.append("".join(f"# {line}\n" for line in notes) + f"# （{'；'.join(meta)}）\n")
            prefix = "" if setting.active else "# "
            parts.append(f"{prefix}{setting.name}={setting.example_value()}\n")
    return "".join(parts)
