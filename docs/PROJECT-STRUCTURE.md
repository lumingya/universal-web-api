# Universal Web API 目录结构与文件说明

> 依据仓库当前 `main`（`VERSION` = 3.0.1）整理。本文按「顶层 → `app/` 各子包 → 配置 / 前端 / 文档 / 测试 / 脚本 / 运行时产物」的顺序说明每个目录和文件的作用。
>
> 一句话定位：这是一个**本机运行的 API 桥接器**——把已经登录好的 AI 网页（ChatGPT、Gemini、DeepSeek、Claude…）包装成 OpenAI / Anthropic 兼容接口。所以代码天然分成两条线：**HTTP 协议层**（`app/api`、`app/models`、`app/services`）和**浏览器自动化层**（`app/core`、`app/runtime`）。

---

## 1. 顶层目录总览

```text
universal-web-api/
├── main.py                     # 应用入口：FastAPI 实例、中间件、静态挂载、启动流程
├── app/                        # 全部后端源码（FastAPI + 浏览器自动化）
│   ├── __main__.py             # `python -m app` 薄入口（转交 main.py，行为一致）
│   ├── paths.py                # 路径唯一来源（PROJECT_ROOT / STATIC_DIR / DOWNLOAD_DIR / CONFIG_DIR）
│   ├── api/                    # HTTP 路由层（OpenAI / Anthropic / 控制面板接口）
│   ├── core/                   # 核心引擎（浏览器、标签页池、工作流、解析器、提取器）
│   ├── models/                 # Pydantic 数据模型 / 请求响应 Schema
│   ├── runtime/                # 启动引导：环境校验、拉起浏览器、自重启
│   ├── services/               # 业务服务（配置引擎、命令引擎、工具调用、存储、指标…）
│   ├── utils/                  # 无状态工具函数（图片、剪贴板、URL、安全校验…）
│   ├── update/                 # 自更新引擎、更新保留规则、profile 清理
│   └── worker/                 # 实验性进程模型（API 进程 + 浏览器 worker 进程）
├── config/                     # 随仓库分发的配置 + 运行时生成的状态文件
│   ├── sites/                  # 每站点一个适配器 JSON（内置站点配置）
│   ├── browser_config.json     # 浏览器行为常量（延时、超时、节流）
│   ├── extractors.json         # 内容提取器注册表
│   ├── parsers.json            # 网站响应解析器注册表
│   ├── image_presets.json      # 图片提取预设库
│   └── site_rules.json         # 站点级规则（隐身默认值、路由别名、是否自动发现）
├── static/                     # 控制面板 / 教程 / 工作流编辑器前端（Vue 3 + 原生 JS）
│   ├── index.html              # 控制面板单页入口
│   ├── js/, css/, images/      # 前端资源
│   └── tutorial/               # 本地使用手册
├── docs/                       # 架构、审查、路线图、专题修复说明
├── tests/                      # pytest 回归测试 + 前端 Node 单测 + fixtures
│   └── manual/                 # 需要真实服务/浏览器的手动脚本（不参与自动收集）
├── scripts/                    # 仓库维护脚本（生成 .env.example、sites/index.json、Tailwind CSS…）
├── tools/                      # 本地开发/性能工具（perf_watch.py）
├── examples/                   # 调用示例（arena_image_batch.py，非服务运行所需）
├── assets/                     # README 用的效果截图
├── .github/workflows/ci.yml    # CI：ruff + pytest（Ubuntu/Windows）+ 结构一致性
├── pyproject.toml              # 依赖与工具配置（唯一依赖事实来源）
├── uv.lock                     # uv 精确锁定版本
├── VERSION                     # 版本号（3.0.1），运行时被 app/__init__.py 读取
├── .env.example                # 环境变量模板（由 settings_registry 生成）
├── updater.py                  # 兼容壳 → app/update/engine.py（保留给旧脚本与测试）
├── update_preserve.py          # 兼容壳 → app/update/preserve.py
├── clean_profile.py            # 兼容壳 → app/update/profile.py
└── patch_drissionpage.py       # 独立补丁工具（必须在导入 DrissionPage 之前运行）
```

### 运行时自动生成的目录（不进 Git）

| 路径 | 作用 | 忽略原因 |
| --- | --- | --- |
| `chrome_profile/` | 受控 Chromium 的独立用户配置目录，保存各站点登录态 | 含 cookie/密码，绝对不能提交 |
| `logs/` | 文件日志（滚动） | 运行时产物 |
| `download_images/` | 从网页抓到的图片/媒体，经 `/download_images` 对外提供 | 运行时产物 |
| `.venv/` | uv 创建的虚拟环境 | 体积大、可重建 |
| `config/runtime.sqlite3` | 请求历史与累计统计（3.0.1 起从 JSON 迁移到 SQLite） | 运行时数据 |
| `config/commands.json`、`commands.local.json`、`command_results.local.json` | 命令引擎的用户数据 | 个人配置 |
| `config/sites.local.json` | 用户对内置站点配置的本地覆盖 | 个人配置 |
| `config/*.local.json`、`config/app_stats.json`、`marketplace_cache.json` | 本地覆盖 / 缓存 / 统计 | 运行时数据 |
| `backup_*/`、`*.bak`、`config/sites/.trash/` | 自动更新与配置迁移的备份 | 可再生成 |

`.gitignore` 同时用白名单方式保护 `scripts/`（只跟踪 5 个维护脚本）与 `tests/`（默认全部跟踪，个人脚本用 `local_*` 命名）。

---

## 1.1 结构改造速查（2026-10-19）

### 巨型文件拆分前后

| 模块 | 改造前 | 改造后 | 现在的构成 |
| --- | --- | --- | --- |
| 标签页路由 | `app/api/tab_routes.py` **5142 行** | **316 行**聚合层 | `app/api/openai/{tab,url,group,tab_url}.py` + `shared.py` + `runtime.py` |
| 系统与运维 | `app/api/system.py` **2675 行** | `app/api/system/` 包（6 个模块） | `health` / `settings` / `updates` / `diagnostics` / `common` / `runtime` |
| 核心聊天 | `app/api/chat.py` **3299 行** | **2542 行** | 端点 + 生命周期 + Responses 状态缓存；纯转换与流式辅助已外移 |
| 面板管理 | 内嵌在 `tab_routes.py` | `app/api/admin/` | `pool.py`（`/api/tab-pool/*`、`/api/pool/status`）、`presets.py`（`/api/presets/*`） |

新增的公共模块：`app/api/stream_helpers.py`（跨协议流式辅助，206 行）、
`app/api/openai_responses_support.py`（Responses 纯转换层，650 行）。

### 新增目录的定位

| 路径 | 一句话作用 |
| --- | --- |
| `app/api/admin/` | **面板管理端点**：标签页池与站点预设的增删改查；依赖方向单向 `admin → tab_routes`（后者现在是再导出层）。 |
| `app/api/openai/` | **标签页 OpenAI 兼容路由**：按 URL 前缀分成 `/tab/`、`/url/`、`/group/`、`/tab-url/` 四个模块，共用 `shared.router`（因此 URL 与注册顺序不变）；`runtime.py` 是测试替换浏览器/请求管理器的唯一落点。 |
| `app/api/system/` | **系统与运维端点**：探活（`/health`、`/api/logs`）、设置（`/api/settings/*`）、更新（`/api/update/*`）、诊断（`/api/debug/*`、`/api/system/*`）；`common.py` 放四组共用的文件/`.env`/重启辅助。 |
| `app/paths.py` | 路径唯一来源（`PROJECT_ROOT` / `STATIC_DIR` / `DOWNLOAD_DIR` / `CONFIG_DIR`），运行时不再依赖当前工作目录。 |
| `app/runtime/` | 启动引导：环境预检、拉起受控浏览器、进程自重启。 |
| `app/update/` | 自更新引擎、更新保留规则、profile 清理（根目录三个 `.py` 只是 `sys.modules` 兼容壳）。 |
| `tests/manual/` | 需要真实浏览器/已登录站点的手工脚本，默认不被 pytest 收集。 |
| `tools/`、`examples/` | 性能采样工具与可运行示例，非服务运行所需。 |

### 一致性保证

- `tests/fixtures/route_table.json` 冻结 `app.routes` 的 134 条 URL（方法集合逐条比对），
  拆分全程零差异；另有测试禁止同一 `(path, method)` 被注册两次。
- 三个结构棘轮：`tests/test_structure.py`（包边界/路径/版本）、`test_route_table.py`（URL）、
  `test_exception_ratchet.py` + `test_driver_ratchet.py`（宽泛异常与直接驱动调用只许减少）。
- 全量测试 **1331 passed / 157 skipped / 0 failed**，CI（ubuntu + windows）同上。

---

## 2. 顶层文件

| 文件 | 作用 |
| --- | --- |
| `main.py` | **应用入口**。定义 `FastAPI` 实例；注册 CORS、认证、媒体访问、请求 ID、异常处理等中间件；挂载 `/static`、`/download_images`；实现 `/`（控制面板）、`/dashboard`、`/media/{filename}`、受控浏览器引导数据接口；`include_router(api_router)` 汇总全部 API；`lifespan` 中完成浏览器连接、标签页池初始化、看门狗启动；作为脚本运行时先调用 `bootstrap_runtime()`。 |
| `app/__init__.py` | 从 `VERSION` 文件读取 `__version__`。 |
| `app/paths.py` | 全项目路径的唯一来源（绝对路径）。需要目录时调用 `download_dir()` / `logs_dir()`（每次读模块常量，便于测试替换）；`resolve_project_path()` 把配置里的相对路径按项目根解析。 |
| `pyproject.toml` | 项目元数据 + 运行依赖 + `dev` 依赖组 + `uv` / `pytest` / `ruff` 配置。依赖只用 uv 管理，不再有 `requirements.txt`。 |
| `uv.lock` | 全量精确锁定（含哈希），`uv sync` 依据它建 `.venv`。 |
| `.python-version` | 固定 `3.12`，uv 首次运行时自动下载对应解释器（项目声明最低支持 3.10）。 |
| `VERSION` | 语义化版本号，`app/__init__.py` 与更新器都读它。 |
| `.env.example` | 全部环境变量的带注释模板（约 26 KB）。由 `scripts/build_env_example.py` 依据 `app/core/settings_registry.py` 生成，**不要手改**。 |
| `.gitattributes` | 换行符归一（避免 Windows CRLF 污染 diff）、把二进制/大文件标为 binary。 |
| `.git-blame-ignore-revs` | 让 `git blame` 跳过纯格式化提交（如 ruff/黑化批量提交）。 |
| `README.md` / `README.zh-CN.md` / `README.en.md` | 中文主 README、简体中文版、英文版：功能、安装（uv）、API 用法、环境变量表。 |
| `CHANGELOG.md` | 当前版本的变更记录（唯一入口；`VERSION` 指向的版本）。 |
| `CHANGELOG-3.0.0.md` | 历史版本（3.0.0）的变更说明，保留为归档。 |
| `LICENSE` | AGPL-3.0。 |
| `tailwind.config.js` | Tailwind 3.4.17 配置（控制面板与各 Studio 页面的设计 token、`content` 扫描范围）。 |
| `patch_drissionpage.py` | 给 `DrissionPage` 打补丁：监听器流式捕获、断线恢复、内部对象兼容。启动时由 bootstrap 调用；也可单独 `python patch_drissionpage.py` 管理。 |
| `updater.py` / `update_preserve.py` / `clean_profile.py` | **兼容壳**：把 `import` 转发到 `app/update/*`（用 `sys.modules` 顶替实现模块，保证模块级常量只有一份）。旧文档与既有测试仍可用；新代码请直接导入 `app.update.*`。 |
| `patch_drissionpage.py` | 独立补丁工具（**刻意**不放进包内：必须在导入 DrissionPage 之前单独执行；bootstrap 用 subprocess 调用，`tests/test_cdp_hygiene.py` 按路径加载）。 |
| `tools/perf_watch.py` | 只读性能采样脚本，用于对比性能改造前后浏览器进程与服务进程的 CPU/内存。 |
| `examples/arena_image_batch.py` | Arena 生图批量调用示例（路径与地址全部走命令行参数，非服务运行所需）。 |
| `assets/` | README 引用效果图（`ai-web-preview.png`、`前端效果.png`、`可视化.png` 等）。 |

---

## 3. `app/` 包

### 3.1 请求管线（先建立全局印象）

```text
客户端 POST /v1/chat/completions
   └─ app/api/chat.py            协议解析、鉴权、参数规范化
       └─ app/services/request_manager.py   排队、生命周期、取消、历史记录
           ├─ app/core/tab_pool*            选一个已登录的标签页（按站点/预设/路由组/URL）
           ├─ app/core/workflow/*           在工作流引擎里“填字 → 上传附件 → 点发送”
           ├─ app/core/stream_monitor.py    轮询页面/网络，边生成边取内容
           ├─ app/core/parsers/*            把各站点私有流格式解析成统一增量文本
           └─ app/core/extractors/*         生成结束后做 DOM/深度提取兜底
       └─ app/services/tool_calling.py      把文本里的工具调用转成 OpenAI tools 结构
   └─ SSE（text/event-stream）或 JSON 返回
```

`app/services/config/`（配置引擎）与 `app/services/command_engine*.py`（命令引擎）是横切的旁路系统：控制面板改的东西最终都落到它们身上。

---

### 3.2 `app/api/` —— HTTP 路由层

> 所有模块都只做「协议 → 内部调用 → 协议」的翻译，不直接操作浏览器。

| 文件 | 作用 |
| --- | --- |
| `routes.py` | **路由聚合入口**：把下面所有子路由 `include_router` 成一个 `router`，`main.py` 只导入它。 |
| `deps.py` | 共享 FastAPI 依赖：从 `Authorization` / `X-API-Key` 提取令牌、校验 API 与面板认证。 |
| `chat.py` | **核心聊天 API**：`POST /v1/chat/completions`、实验性 `POST /v1/responses`、`GET /v1/models`（及 `/v1/v1/models` 兼容别名）。负责 OpenAI 字段解析（`temperature`/`max_tokens`/`stop`/`response_format`/`tools`/`preset_name`）、多模态消息拆解、错误码映射、流式与非流式两条出口。约 2.5k 行，是最重要的协议文件。Responses 的纯转换逻辑已下沉到 `openai_responses_support.py`。 |
| `openai/` | **标签页 OpenAI 兼容路由的四分组**：`tab.py`（`/tab/{i}/v1/*`）、`url.py`（`/url/{domain}[/{preset}]/v1/*` 与按模型名路由）、`group.py`（`/group/{id}[/{preset}]/v1/*`）、`tab_url.py`（`/tab-url/{token}[/{preset}]/v1/*`）；`shared.py` 是四组共用的解析/配置/SSE/生命周期辅助并持有唯一的 `router` 实例；`runtime.py` 集中 `get_browser` / `request_manager` / `cancel_storm_guard` 三个可替换的运行期依赖。 |
| `openai_responses_support.py` | Responses API 的**纯转换层**（无 I/O、无状态）：`_normalize_response*` / `_normalize_responses*`、chat 载荷 → Responses 输出、usage 组装、错误体、response item 构造。从 `chat.py` 拆出，便于单测与复用。 |
| `stream_helpers.py` | **跨协议流式辅助**：媒体项抽取/去重、增量文本与错误消息提取、请求超时判定、`ChatRequest` 单选判定，以及 `_pack_error` / `_pack_done` / `_pack_error_done` 三个 SSE 打包函数。`chat.py` 与 `tab_routes.py` 共用同一份实现。 |
| `openai_stop.py` | 纯逻辑模块：OpenAI 风格 `stop` 序列在**流式**输出中的安全截断（跨 chunk 缓冲、防止半截序列泄漏），以及 SSE 解析/打包辅助。被 `chat.py` 与测试复用。 |
| `anthropic_routes.py` | **Anthropic Messages 兼容层**：`/v1/messages`、`/v1/messages/count_tokens`，含 `/url/{domain}[/{preset}]/v1/messages` 变体；同时接受 `Authorization` 与 `x-api-key`；把 Anthropic 请求转成内部消息、把 OpenAI 流式事件反译成 Anthropic SSE 事件。 |
| `tab_routes.py` | **标签页路由聚合层**（316 行）：只是把 `app/api/openai/` 下的四个分组模块导入进来并再导出原有名字，因此 `from app.api import tab_routes; tab_routes.<name>` 与拆分前完全一致。 |
| `config_routes.py` | **配置管理 API**：读取/保存 `sites` 配置与预设、站点高级配置、图片配置与图片预设、提取器与解析器、文件粘贴配置、流式/网络监听配置、工作流脚本读写、隔离标签页创建、与主分支配置对比。 |
| `config_route_models.py` | `config_routes.py` 用的 Pydantic 请求模型与预设规范化辅助。 |
| `config_compare_support.py` | 「与官方 main 分支配置对比」的实现辅助（拉取远端配置、计算差异）。 |
| `config_workflow_support.py` | 工作流编辑器的辅助函数（注入脚本、消费前端动作、脚本缓存）。 |
| `system/` | **系统与运维 API 包**：`health.py`（`/health`、`/api/logs`）、`settings.py`（`/api/settings/*`：.env 读写、备份/恢复、浏览器常量、更新保留规则）、`updates.py`（`/api/update/*` 与启动时更新检查）、`diagnostics.py`（`/api/debug/*`、`/api/system/*`：选择器调试、强制释放/取消、进程/磁盘统计、请求历史）、`common.py`（文件/`.env`/重启公共辅助）、`runtime.py`（`get_browser`、`request_manager`）。`__init__.py` 为聚合层并再导出原有名字。 |
| `browser_routes.py` | 受控浏览器集成接口：仅回环/认证可用，`POST /api/browser/open-profile-url` 在**指定 Chrome 配置**里打开 URL。 |
| `cmd_routes.py` | 命令系统 API：命令与分组 CRUD、启停、排序、重命名、`/test` 试跑、分组执行、执行结果存取、图片资产上传；对 `secret` 字段做脱敏与占位符还原。 |
| `provider.py` | 机器可读的能力与状态：`GET /v1/provider/capabilities`（支持的协议/特性/模型能力）、`GET /v1/provider/status`（浏览器连接、标签页池、请求状态，不含密钥）。 |
| `adapter_routes.py` | 站点适配器在线更新：查询可用更新、应用更新、健康巡检、lint。 |
| `metrics_routes.py` | `GET /metrics`，Prometheus 文本格式指标（无第三方依赖实现）。 |
| `streaming_response.py` | `RequestStreamingResponse`：客户端断开时**及时关闭**异步生成器（防止请求堆积、标签页被长期占用）。 |
| `admin/` | **面板管理端点层**（对应 FastAPI 的 `internal/admin.py` 约定）：`pool.py` 承载 `/api/tab-pool/*` 与 `GET /api/pool/status`，`presets.py` 承载 `/api/presets/*`。只搬代码不改 URL；依赖方向 `admin → tab_routes`（单向，复用尚未下沉的辅助函数）。 |

---

### 3.3 `app/core/` —— 核心引擎

`core/__init__.py` 只做门面导出（`get_browser`、`ElementFinder`、日志、异常、`SSEFormatter` 等），方便 `from app.core import ...`。

#### 3.3.1 浏览器与元素

| 文件 | 作用 |
| --- | --- |
| `browser.py` | 9 行兼容门面，转发到 `app/core/browser/` 包。 |
| `browser/__init__.py` | 导出 `BrowserCore` / `get_browser` / `browser`。 |
| `browser/main.py` | `BrowserCore` 定义与单例 `get_browser()` / 惰性代理 `_LazyBrowser`。 |
| `browser/connection.py` | **连接**：连接本地调试端口、判断 headless/windowless 异常、加载标签页池/预设/模型名覆盖配置、重连与降级。 |
| `browser/workflow.py` | **工作流主逻辑**（约 2.9k 行）：打开/切换页面、等待就绪、发送消息、判定生成完成、错误重试。 |
| `browser/media.py` | **媒体能力**（约 3.2k 行）：图片/文件上传、下载、blob 处理、转码缓存、媒体访问控制对接。 |
| `browser/prompt.py` | 提示词拼装：系统提示、工具调用提示、超长文本分段、附件描述等。 |
| `elements.py` | `ElementFinder` / `CachedElement`：选择器查找、元素缓存（带 TTL）、失效重试。 |
| `page_lifecycle.py` | 让后台标签页“看起来是活跃页”的 best-effort 处理（避免站点暂停流）。 |
| `cdp_hygiene.py` | DrissionPage / CDP 资源卫生补丁：清理悬挂 listener、避免 target 泄漏。 |
| `network_monitor.py` | **网络响应监听器**（约 3.2k 行）：挂接 CDP Network 事件、按 URL 模式截获 SSE/JSON、超时与可恢复异常分类、可选抓包落盘。 |
| `stream_monitor.py` | **流式监听核心**：轮询页面状态、检测新文本/图片、判定“生成中/结束”、与观察者协作。 |
| `stream_observer.py` | 观察者与钩子接口，供站点特殊逻辑（如 Arena 翻牌、生图）挂载。 |
| `stream_snapshot.py` | 「一次 CDP 往返拿到页面快照」的高频轮询优化结构。 |
| `request_transport.py` | 预设级「请求传输」配置：决定用页面 UI 还是页面内 `fetch` 转发。 |
| `http_security.py` | 控制面 HTTP 安全边界：可信本机判断、Origin/Host 校验、防 DNS rebinding。 |
| `settings_registry.py` | **全部环境变量的唯一登记表**（类型、默认值、分组、说明）。`.env.example` 与设置页都由它派生。 |

#### 3.3.2 `core/driver/` —— 浏览器驱动抽象层（R2-1）

业务代码不再直接 `import DrissionPage`，而是走这一层，便于替换驱动与测试。

| 文件 | 作用 |
| --- | --- |
| `base.py` | 驱动接口（标签页、导航、执行 JS、元素查找、事件监听等协议）。 |
| `drission.py` | 基于 DrissionPage 的实现：**只做转发与异常翻译**，不改变行为。 |
| `drission_internals.py` | 访问 DrissionPage 私有内部对象的**唯一入口**（隔离未来版本变动）。 |
| `fake.py` | 内存假驱动，单测无需真实浏览器。 |
| `locators.py` | 定位语法统一规则（`css:` / `xpath:` / 文本 / 属性组合），供查找器与健康巡检共用。 |
| `errors.py` | 驱动层统一异常（超时、失联、目标关闭…）。 |

#### 3.3.3 `core/config.py` + `core/config_parts/` —— 配置与基础设施

`core/config.py` 是**兼容门面**，实现全部拆到 `config_parts/`，老代码 `from app.core.config import X` 仍然有效。

| 文件 | 作用 |
| --- | --- |
| `config.py` | 门面：重导出所有公共符号。 |
| `config_parts/env_config.py` | `.env` 加载、`AppConfig`、`PROJECT_ROOT`、日志目录、原子写 JSON、路径/端口/host 判定。 |
| `config_parts/browser_constants.py` | `BrowserConstants`：从 `config/browser_config.json` 读延时、超时、节流、缓存年龄等。 |
| `config_parts/exceptions.py` | `BrowserError` / `BrowserConnectionError` / `ElementNotFoundError` / `WorkflowError` 等异常层级。 |
| `config_parts/sse_formatter.py` | `SSEFormatter`：统一 `data:` 帧、`[DONE]`、心跳与错误帧。 |
| `config_parts/message_validator.py` | `MessageValidator`：消息角色/内容结构校验与修复。 |
| `config_parts/log_collector.py` | `LogCollector`：环形缓冲，供应控制面板 `/api/logs` 实时查看。 |
| `config_parts/log_redaction.py` | 日志脱敏（令牌、cookie、URL 参数）。 |
| `config_parts/log_formatters.py` | 控制台彩色格式化器、安全滚动文件 Handler。 |
| `config_parts/secure_logger.py` | `SecureLogger` 与 `get_logger()` 工厂、全局 `logger`。 |
| `config_parts/cute_translator.py` | 展示层“喵化”日志翻译（`LOG_*_CUTE_MODE` 控制），纯表驱动，可关。 |

#### 3.3.4 `core/tab_pool.py` + `core/tab_pool_parts/` —— 标签页池

`tab_pool.py` 只是门面，`TabPoolManager` 由多个 Mixin 组成（R2-3 拆分）。

| 文件 | 作用 |
| --- | --- |
| `tab_pool.py` | 兼容门面：导出 `TabPoolManager`、`TabSession`、`TabStatus`、剪贴板锁、全局网络管理器。 |
| `tab_pool_parts/manager.py` | `TabPoolManager` 主体：组合下列 Mixin，持有池状态。 |
| `tab_pool_parts/session.py` | `TabSession`（一个标签页的运行时视图）与 `TabStatus`（idle/busy/quarantined…）。 |
| `tab_pool_parts/pool_acquire.py` | 借用/归还：等待队列、按编号/按原始 ID 借用、释放。 |
| `tab_pool_parts/pool_allocation.py` | 分配顺序与游标、全局网络监听在借出/归还时的交接。 |
| `tab_pool_parts/pool_routing.py` | 按域名、路由组、精确 URL 借标签页。 |
| `tab_pool_parts/pool_tabs.py` | 标签页创建、扫描发现、初始化与刷新。 |
| `tab_pool_parts/pool_config.py` | 池配置解析：排除 URL、模型名/预设覆盖、动态路由候选。 |
| `tab_pool_parts/pool_contexts.py` | 浏览器上下文与 CDP：独立 Cookie 上下文、目标快照、强制重载、上下文恢复。 |
| `tab_pool_parts/pool_sessions.py` | 会话封装/移除、请求取消、隔离区（quarantine）与恢复服务对接。 |
| `tab_pool_parts/pool_status.py` | 状态查询与管理操作：编号列表、设置预设/模型名、看门狗摘要、按编号终止。 |
| `tab_pool_parts/pool_maintenance.py` | 看门狗、卡死检测、空闲维护、内存清理、不健康标签页清理。 |
| `tab_pool_parts/idle_maintenance.py` | 空闲标签页维护策略（P0-3/P0-6）。 |
| `tab_pool_parts/recovery.py` | 错误标签页隔离 + 用页面内 AI 辅助恢复的流程。 |
| `tab_pool_parts/network.py` | `_GlobalNetworkInterceptionManager` / `_GlobalNetworkWorker`：全局网络监听的线程与单例。 |
| `tab_pool_parts/clipboard.py` | 全局剪贴板锁（多标签页并发粘贴时串行化）。 |
| `tab_pool_parts/_arena_snapshot.py` | Arena 专用页面快照（盲测双栏结构）。 |
| `tab_pool_parts/_utils.py` | 池内共用小工具：跳过 URL 的前缀/片段、瞬时调试错误识别、健康缓存 TTL。 |

#### 3.3.5 `core/workflow/` —— 工作流执行器

| 文件 | 作用 |
| --- | --- |
| `executor.py` | `WorkflowExecutor` 主体（组合下列 Mixin）：执行一条工作流步骤序列。 |
| `executor_actions.py` | 隐身动作 Mixin：延时、拟人鼠标轨迹、滚动、点击/输入的低层动作。 |
| `executor_interaction.py` | 页面交互 Mixin：元素定位、等待可交互、条件判断。 |
| `executor_send.py` | 发送确认与附件流程 Mixin：点击发送、判定“真的发出去了”、失败重试。 |
| `executor_request_transport.py` | 页面内 `fetch` 传输 Mixin（绕过 UI 直接发请求的站点）。 |
| `flow_runtime.py` | 结构化工作流 v2 的**受限编译器 + 运行时**：`if/steps/case/else/fallback` 等控制流，请求级变量；不使用 `eval`。 |
| `preview.py` | 工作流预览：走生产编译器但**无副作用**（不起浏览器），供编辑器即时校验。 |
| `script_loader.py` | 工作流里 JavaScript 脚本的加载与安全执行辅助。 |
| `model_selection.py` | 通用模型选择/切换协议（点开模型菜单 → 选目标项 → 校验）。 |
| `text_input.py` | 文本输入：contenteditable / textarea 差异处理、逐段输入、粘贴兜底。 |
| `input_chunking.py` | 超长提示词的分段规划与顺序发送。 |
| `image_input.py` | 图片入口的向后兼容包装，实际统一走附件协调器。 |
| `attachment_upload.py` | 文件传输通道（`input[type=file]`、拖拽区、剪贴板）与保守的上传事务编排。 |
| `attachment_monitor.py` | 上传状态监视：区分“真的上传完成”与页面文案误判。 |
| `error_handlers.py` | 错误与重试恢复处理器注册中心（按错误类型找恢复策略）。 |
| `arena_direct_guard.py` | Arena Direct 模式意外跳到 battle 页面时的检测与自动恢复。 |
| `arena_send_watchdog.py` | Arena 专用：首条匹配流请求的发送确认闸门。 |
| `__init__.py` | 包导出与文档。 |

#### 3.3.6 `core/parsers/` —— 站点响应解析器

每个站点一种私有流格式。`base.py` 定义增量解析接口（喂 chunk、取增量文本/思考内容/图片/结束标记），`registry.py` 按 URL 模式 + 站点选解析器，`config/parsers.json` 可在运行时安装/启停解析器。

| 文件 | 解析对象 |
| --- | --- |
| `base.py` | 解析器基类与统一事件结构 |
| `registry.py` | 注册中心（模式匹配、动态安装） |
| `chatgpt_parser.py` | ChatGPT SSE |
| `claude_parser.py` | Claude SSE |
| `deepseek_parser.py` | DeepSeek SSE（含深度思考） |
| `gemini_parser.py` | Gemini `StreamGenerate` |
| `qwen_parser.py` | 通义千问 SSE |
| `glm_parser.py` | ChatGLM SSE |
| `grok_parser.py` | Grok NDJSON 流 |
| `kimi_parser.py` | Kimi Connect 流 |
| `doubao_parser.py` | 豆包 SSE（含图片帧） |
| `aistudio_parser.py` | Google AI Studio |
| `mimo_parser.py` / `mimo_runtime_parser.py` | 小米 MiMo（后者为运行时安装版） |
| `lmarena_parser.py` | Arena 基础解析 |
| `lmarena_battle_side_parser.py` | Arena battle 模式 |
| `lmarena_side_left_parser.py` | Arena 双栏左侧 |
| `lmarena_image_side_left_parser.py` / `..._right_parser.py` | Arena 生图双栏左右 |

#### 3.3.7 `core/extractors/` —— 内容提取器（生成结束后的兜底/增强）

| 文件 | 作用 |
| --- | --- |
| `base.py` | 提取器抽象基类 |
| `dom_mode.py` | 直接按选择器读 DOM |
| `deep_mode.py` | 注入 JS 深度遍历，支持 LaTeX/表格 |
| `hybrid_mode.py` | 自动判断复杂度选策略（未完全成熟） |
| `image_extractor.py` | 图片提取（`img`、blob、背景图） |
| `media_extractor.py` | 多模态提取（图片/音视频/附件链接），体量最大 |
| `registry.py` | 提取器注册中心，对应 `config/extractors.json` |

#### 3.3.8 `core/page_capture/` —— 页面侧发起请求并捕获流

用于某些站点：在页面上下文里 `fetch`，借登录态直接拿流，而不是点 UI。

| 文件 | 作用 |
| --- | --- |
| `base.py` | 页面侧捕获契约 |
| `request_transport.py` | 页面侧请求传输注册表 |
| `deepseek_request_transport.py` | DeepSeek 页面内补全传输 |
| `kimi_fetch_capture.py` | Kimi `fetch` 流捕获 |
| `registry.py` | provider 级注册表 |

#### 3.3.9 `core/` 其它文件

| 文件 | 作用 |
| --- | --- |
| `chat_job.py` | 内部统一聊天任务 `ChatJob` 与类型化事件 `ChatEvent`（R2-2），把 HTTP 协议与浏览器执行解耦。 |
| `background_image_downloader.py` | 后台图片下载队列（不阻塞请求返回）。 |
| `workflow_editor.py` | 可视化工作流编辑器的注入与生命周期管理（向目标页面注入 `workflow-editor-inject.js`）。 |

---

### 3.4 `app/models/`

| 文件 | 作用 |
| --- | --- |
| `schemas.py` | 全部 Pydantic 模型与常量：聊天消息/多模态 part、工具调用、配置更新请求、预设字段白名单（`ADVANCED_FIELDS` 等）、站点高级配置结构。约 1.4k 行。 |
| `__init__.py` | 包导出。 |

---

### 3.5 `app/runtime/` —— 启动引导

| 文件 | 作用 |
| --- | --- |
| `bootstrap.py` | `bootstrap_runtime()`：在导入 DrissionPage 等重依赖**之前**完成环境校验（解释器/`.venv`）、自动更新检查、DrissionPage 补丁、配置清理、拉起受控浏览器。 |
| `browser_launcher.py` | 启动并校验受控 Chromium：端口、用户数据目录、headless/window 检测、失败重试。 |
| `relaunch.py` | `restart_service()`：服务自重启（保留浏览器与标签页），用于设置变更/定时守护。 |

---

### 3.6 `app/services/` —— 业务服务层

#### 3.6.1 请求与流

| 文件 | 作用 |
| --- | --- |
| `request_manager.py` | **请求生命周期管理器 v2**：排队、并发限制、状态统计、取消、历史记录写入、与标签页池/执行器协作。 |
| `request_lifecycle.py` | 执行生命周期辅助：worker 清理、标签页退役、请求收尾上下文。 |
| `result_event_bridge.py` | 可选“网络结果旁路事件”桥（流式结果同时推给监听者）。 |
| `sse_utils.py` | 轻量 SSE 帧解析（`data:` 字段）、错误信息提取。 |
| `stream...`（在 core） | 见 3.3.1。 |

#### 3.6.2 配置引擎 `services/config/`

| 文件 | 作用 |
| --- | --- |
| `engine.py` | `ConfigEngine` 主体，组合下列 Mixin；对外是唯一配置读写入口。 |
| `engine_common.py` | Mixin 共用模块级定义（文件路径、锁、工具）。 |
| `engine_sites.py` | 站点目录、起始页推断、站点高级配置、选择器定义。 |
| `engine_presets.py` | 预设解析、创建、改名、删除及引用同步（路由组、标签页）。 |
| `engine_stream.py` | 提取器、流式/网络监听配置及其校验、解析器列表。 |
| `engine_media.py` | 图片提取、文件粘贴、提示词填充配置、图片预设。 |
| `engine_migrations.py` | 历史配置迁移、命令文件读写。 |
| `engine_local_overrides.py` | `config/sites.local.json` 本地覆盖与默认预设状态。 |
| `site_store.py` | 站点配置目录存储：`config/sites/<domain>.json` 的原子读写、回收站、哈希校验。 |
| `site_schema.py` | 站点适配器 Schema v1 的校验与版本兼容。 |
| `cache.py` | 配置读取的内存缓存（避免频繁读盘）。 |
| `managers.py` / `processors.py` | 子管理器与配置后处理器（规范化、默认值补齐）。 |
| `services/config_engine.py` | 兼容代理：老代码 `from app.services.config_engine import config_engine`。 |

#### 3.6.3 命令引擎 `services/command_engine*.py`

命令引擎 = 「在浏览器里按规则自动跑 JS / 执行分组任务」的自动化子系统，控制面板「命令」页对应它。

| 文件 | 作用 |
| --- | --- |
| `command_engine.py` | 主体类与门面。 |
| `command_engine_common.py` | Mixin 共用对象。 |
| `command_engine_crud.py` | 命令/分组增删改查、分组执行与预览。 |
| `command_engine_store.py` | `commands.json` 读写与规范化、运行统计合并、命名去重、`run_js_file` 清理。 |
| `command_engine_scheduler.py` | 周期调度：保活、标签页池刷新、周期检查循环。 |
| `command_engine_triggers.py` | 触发判定：请求计数、网络事件、优先级、忙碌判断、导航事件分发。 |
| `command_engine_actions.py` | 动作执行（约 3.2k 行）：JS 执行、网络请求、页面动作、沙箱允许导入白名单。 |
| `command_engine_results.py` | 执行结果记录与查询。 |
| `command_engine_runtime.py` | 运行时状态与循环驱动。 |
| `command_engine_pages.py` | 标签页交互：监听挂起/恢复、会话状态、焦点模拟、唤醒节流。 |
| `command_engine_page_check.py` | 页面内容检查：表达式解析、文本匹配、快照求值、锁存。 |
| `command_defs.py` | 命令结构定义（字段、默认值、动作类型）。 |
| `command_result_store.py` | 结果持久化（`config/command_results.local.json`）的 CRUD。 |
| `transport_profile_handlers.py` | 「HTTP Request Transport Profile」处理器注册与实现。 |

> `.gitignore` 忽略了 `config/commands.json` 与 `command_results.local.json`——命令是用户数据，不随仓库分发。

#### 3.6.4 解析器 / 提取器 / 工具调用

| 文件 | 作用 |
| --- | --- |
| `parser_manager.py` | 运行时解析器安装管理（`config/parsers.json`，热加载）。 |
| `extractor_manager.py` | 提取器配置管理（`config/extractors.json`）。 |
| `tool_calling.py` | OpenAI 兼容工具调用适配器：把 tools 定义注入提示、解析模型输出、生成 `tool_calls`。 |
| `tool_calling_prompts.py` | 工具调用提示词构造。 |
| `tool_calling_parse.py` | 增量解析（流式 JSON 修复、部分参数）。 |
| `tool_calling_validation_retry.py` | 参数校验（JSON Schema）与失败重试。 |
| `tool_calling_common.py` | 共用小工具。 |
| `text_filter.py` | 响应文本清洗（去掉站点水印/多余空白）。 |
| `selector_quality.py` | 选择器脆弱度静态检查与修复建议（只建议，不自动改）。 |
| `catalog_routing.py` | 动态模型目录与路由派发（`/v1/models` 的模型 → 标签页/预设映射）。 |
| `error_metadata.py` | 标准异常元数据与错误响应构建（OpenAI 格式）。 |

#### 3.6.5 存储与可观测性

| 文件 | 作用 |
| --- | --- |
| `storage/runtime_store.py` | SQLite 存储：请求历史、累计统计（`config/runtime.sqlite3`，WAL）。 |
| `metrics.py` | 无依赖 Prometheus 指标注册与请求 ID 生成。 |
| `restart_guard.py` | 协作式定时重启守卫：等正在跑的工作流排空再重启。 |

#### 3.6.6 安全与适配器

| 文件 | 作用 |
| --- | --- |
| `adapter_health.py` | 站点适配器健康巡检：只读 DOM，不输入不点击。 |
| `adapter_updates.py` | 站点适配器在线更新（无需发整包即可热更站点配置）。 |
| `cf_turnstile_solver.py` | Cloudflare Turnstile / 5 秒盾的通用自动处理。 |
| `arena_cf_solver.py` | 上述能力的 Arena 兼容适配层。 |

#### 3.6.7 Arena 专属

| 文件 | 作用 |
| --- | --- |
| `arena_model_catalog.py` | Arena Direct 模型目录持久化、迁移、暗池过滤。 |
| `arena_direct_models.py` | 从已加载页面状态读取 Arena Direct 模型表。 |
| `arena_rule_service.py` | Arena 规则匹配与候选记录。 |
| `arena_tab_listener.py` | Arena 翻牌探测与结果桥接扩展监听。 |
| `arena_image_generation.py` | 浏览器生图的 Arena 守卫。 |
| `arena_image_stream_observer.py` | Arena 生图流式监控观察者。 |
| `arena_gpt_image_command.py` | GPT Image 2 C2PA 检测的盲测运行器。 |
| `arena_proxy_rotation.py` | Clash 代理轮换（Arena 等对出口 IP 敏感的任务共用）。 |

---

### 3.7 `app/utils/` —— 无状态工具

| 文件 | 作用 |
| --- | --- |
| `attachments.py` | 协议中立的输入附件模型（不做浏览器动作）。 |
| `image_handler.py` | 图片编解码、缩放、格式转换、base64/data URL。 |
| `image_validation.py` | 图片比对、特征签名、参考图过滤。 |
| `media_access.py` | `/media`、`/download_images` 的访问控制与转码资源预算。 |
| `media_safety.py` | 媒体落盘与同源提供的安全策略（白名单扩展名、CSP、nosniff）。 |
| `remote_resource.py` | 抓取用户提供的远端资源时的 SSRF 防护。 |
| `file_paste.py` / `paste.py` | 文件粘贴 / 文本粘贴到页面（contenteditable 兼容）。 |
| `system_clipboard.py` | 平台原生剪贴板适配（Windows/macOS/Linux）。 |
| `human_mouse.py` | 拟人鼠标轨迹与点击。 |
| `platform.py` | 轻量平台判断（Windows 路径、窗口控制等）。 |
| `similarity.py` | 文本相似度（判定回复是否变化/重复）。 |
| `model_routing.py` | OpenAI 风格模型 id → 路由域目标。 |
| `site_url.py` | 识别真实远端站点与路由域名的 URL 辅助。 |
| `site_rules.py` | 站点级规则加载（路由、起始页、默认值共用）。 |
| `site_discovery.py` | 保守的站点自动准入判断（显式配置不受限）。 |
| `tab_route_groups.py` | 路由组的规范化与索引。 |
| `browser_profile_identity.py` | 解析某标签页归属的 Chrome 配置显示名。 |
| `diagnostics_access.py` | 健康/引导接口「详细信息」的访问判定（本机或已认证）。 |

---

### 3.8 `app/update/` —— 自更新与配置清理

| 文件 | 作用 |
| --- | --- |
| `engine.py` | 自动更新引擎（原根目录 `updater.py`）：查询 GitHub Releases、下载资产、安全解压（防路径穿越/符号链接/压缩炸弹）、替换文件、失败回滚、版本检查复用。`PROJECT_ROOT = parents[2]`。 |
| `preserve.py` | 更新时需要**保留**的本地文件/目录（`.env`、`chrome_profile/`、`config/*.local.json`、`runtime.sqlite3` 等）。 |
| `profile.py` | `chrome_profile/` 清理（原根目录 `clean_profile.py`），提供 `main(argv)` 供 `python -m app.update.profile` 使用。 |
| `__init__.py` | 导出保留规则相关函数。 |

### 3.9 `app/worker/` —— 实验性进程模型（默认关闭）

| 文件 | 作用 |
| --- | --- |
| `supervisor.py` | API 进程负责启动、监控、关闭 worker 进程。 |
| `client.py` | API 侧远程浏览器代理（process 模式下 `get_browser()` 返回它）。 |
| `forwarding.py` | 把 `/api/*` 面板请求整体透传给 worker（纯 ASGI，流式）。 |
| `rpc_routes.py` | worker 提供给 API 的内部接口（仅 `UWAPI_WORKER_ROLE=worker` 时注册）。 |
| `protocol.py` | 两进程之间的协议约定（消息格式、错误码）。 |
| `__init__.py` | 开关与文档。 |

---

## 4. `config/` —— 配置与适配器

| 路径 | 作用 |
| --- | --- |
| `sites/` | **内置站点适配器目录**，每站点一个 JSON（chatgpt / gemini / claude / deepseek / grok / qwen / kimi / doubao / glm / arena / aistudio / mimo）。每个文件含：`schema_version`、`adapter_version`、`min_app_version`、`config.presets.<预设>.selectors`（输入框、发送按钮、结果容器、生成中指示器、新对话按钮、上传按钮…）、模型选择脚本、附件/流式/网络监听设置。 |
| `sites/_global.json` | 所有站点共享的 `selector_definitions`（选择器键的语义、是否必填）。 |
| `sites/index.json` | 适配器清单：文件名、版本、最低应用版本、配置与文件 SHA-256（供在线更新与完整性校验）。生成/校验脚本：`scripts/build_sites_index.py`。 |
| `site_rules.json` | 站点级轻量规则：`stealth_default`（默认隐身）、`route_aliases`（域名别名）、`auto_discovery`（是否允许自动纳入），以及一组明确禁止自动发现的域名（google/bing 等）。 |
| `browser_config.json` | 浏览器行为常量：连接超时、隐身/动作延时区间、页面交互节流（并发数、最小间隔、稳定采样）、元素超时、缓存年龄、流式检查间隔、日志可爱模式开关。可在面板「设置 → 浏览器常量」热改。 |
| `extractors.json` | 提取器注册表：id、名称、类与模块路径、启用状态、参数（如 `enable_latex`）。 |
| `parsers.json` | 解析器注册表（运行时安装的解析器会写在这里），如 MiMo 的 `**/open-apis/bot/chat**` 匹配模式。 |
| `image_presets.json` | 图片提取预设：按站点给出选择器、容器、去抖、等待加载、是否下载 blob、大小上限、提取模式。 |
| 本地覆盖（不跟踪） | `sites.local.json`（站点配置本地覆盖）、`commands.json` / `commands.local.json`、`app_stats.json`、`model_name_overrides.local.json`、`arena_model_aliases.local.json`、`runtime.sqlite3`。 |

---

## 5. `static/` —— 前端（控制面板 / 教程 / 工作流编辑器）

### 5.1 入口页面

| 文件 | 作用 |
| --- | --- |
| `index.html` | 控制面板单页（服务根路径 `/`）。先内联脚本恢复暗色模式防闪烁，再按顺序加载 Vue、Schema、State、methods、组件、`dashboard.js`。 |
| `tutorial.html` | 教程页跳转壳。 |
| `tutorial/index.html` | 本地使用手册（`/tutorial/`）：快速开始、站点配置、工作流、命令、FAQ；`#quickstart` 等锚点被 README 引用。 |
| `controlled-browser-guide.html` | 首次启动「受控浏览器使用引导」页：打开哪些站点、如何登录、常见问题。 |
| `selector-practice.html` | 选择器练习/调试页，配合面板的选择器工作台。 |
| `lifecycle-probe.html` | 页面生命周期探针（验证后台标签页是否被节流/冻结）。 |
| `workflow-visualization.png`、`tutorial-dashboard-overview.png`、`截图.png` | 文档/教程配图。 |
| `拦截请求发生器.txt` | 可粘贴到浏览器控制台的脚本片段：按严格/宽松模式捕获目标站点的请求，用于生成解析器/路由配置。 |

### 5.2 `static/js/`

| 路径 | 作用 |
| --- | --- |
| `dashboard.js` | 创建 Vue app：组装 state / computed / methods / 组件，挂载到 `#app`。 |
| `dashboard-state.js` | 响应式数据、常量、localStorage 键（`dashboard_token`、站点缓存）、鉴权令牌读取。 |
| `dashboard-schema.js` | 前端 Schema：默认选择器定义、浏览器常量元数据、环境变量分组与文案。 |
| `dashboard-methods.js` | methods 组装器：把 `dashboard/methods-*.js` 各自挂到 `window.DashboardMethodParts` 后合并。 |
| `dashboard/shared.js` | 方法组共用的常量与辅助函数（请求封装、导入大小上限等）。 |
| `dashboard/methods-api.js` | 与后端 API 的交互封装。 |
| `dashboard/methods-core.js` | 初始化、标签页切换、全局状态刷新。 |
| `dashboard/methods-editing.js` | 配置编辑与保存队列。 |
| `dashboard/methods-logs.js` | 日志轮询/过滤/暂停。 |
| `dashboard/methods-media.js` | 图片/文件配置与媒体预览。 |
| `dashboard/methods-selectors.js` | 选择器测试与选择器工作台。 |
| `dashboard/methods-settings.js` | 环境变量设置、备份恢复、更新保留。 |
| `dashboard/methods-tools.js` | 工具面板（调试、强制释放、诊断）。 |
| `dashboard/methods-transfer.js` | 预设导入/导出、配置迁移。 |
| `dashboard/methods-versions.js` | 版本检查与自动更新。 |
| `components/` | 各 Tab 的 Vue 组件：`HomeTab`（首页概览）、`ConfigTab`（站点配置，最大）、`ExtractorTab`、`TabPoolTab`（标签页池）、`RequestMonitorTab`（请求监控）、`CommandsTab`、`LogsTab`、`SettingsTab`、`Sidebar`、`Dialogs`（JSON 预览/确认框）、`OnboardingGuide`（首次引导）。 |
| `components/commands/` | 命令页的 computed / methods / template 三件套。 |
| `components/panels/` | 配置页的可折叠面板：`WorkflowPanel`、`SelectorPanel`、`StreamConfigPanel`、`ImageConfigPanel`、`FilePastePanel`、`PromptPaddingPanel`、`AdapterPanel`、`ExtractorPanel`、`PresetTransfer`。 |
| `components/shared/CollapsiblePanel.js` | 通用折叠容器。 |
| `icons.js` | 内联 SVG 图标表（`$icons`）。 |
| `workflow-studio.js` | **Workflow Studio**：零依赖树编辑器 Web Component（节点图形、分支泳道、检查器、测试面板），CSS 内联打包。 |
| `workflow-editor-inject.js` | 注入到目标 AI 页面的可视化编辑器 v2（配置加载、元素定位、浮层 UI）。 |
| `workflow-page-guide.js` | 页面优先的引导式工作流编辑（以页面元素为入口，树仍是权威数据）。 |
| `tutorial-page.js` / `tutorial-i18n.js` | 教程页逻辑与中英文文案。 |
| `vendor/vue.global.prod.js` | Vue 3 生产构建（离线可用，无 CDN 依赖）。 |

### 5.3 `static/css/`

`tailwind.css`（预编译产物）、`tailwind.input.css`（源码输入）、`dashboard.css` / `dashboard-dark.css` / `dashboard-migration.css` / `dashboard-reference.css`（面板主题与迁移样式）、`operations-studio.css`（标签页池/请求监控）、`site-studio.css`（站点配置）、`workflow-studio.css`（工作流编辑器）、`adapter-panel.css`、`onboarding-guide.css`、`tutorial.css`。

> 修改样式后需运行 `uv run python scripts/build_tailwind.py`（`tests/test_tailwind_equivalence.py` 会校验产物与源码一致）。

### 5.4 `static/images/`

`logo.svg`、`readme-wordmark.svg`、`controlled-browser-guide-bg.jpg`。

---

## 6. `docs/` —— 文档

| 路径 | 内容 |
| --- | --- |
| `uv.md` | 为什么只用 uv、如何同步/升级/加依赖。 |
| `architecture/browser-driver-and-process-model.md` | R2-1 驱动接口与 R2-6 进程模型的设计与阶段计划。 |
| `architecture/chat-pipeline.md` | 聊天请求从 HTTP 到浏览器的完整管线。 |
| `architecture/exception-policy.md` | 异常处理规范（只捕获具体异常、棘轮测试约束）。 |
| `site-studio.md` | 站点配置页的信息架构与「按需配置」设计。 |
| `operations-studio.md` | 标签页池与请求监控页设计。 |
| `workflow-studio.md` | 通用工作流与 Workflow Studio 使用说明。 |
| `attachments.md` | 通用附件管线（文件/图片/超长输入）。 |
| `attachment-and-cancellation-fix.md` / `plans/attachment-monitor-evidence.md` | 附件误判与“取消后立刻重发”问题的核验与修复记录。 |
| `request-cancellation.md` | 客户端中止输出时的服务端收尾（AnyIO 取消语义）。 |
| `performance_refactor.md` | 性能重构 Layer 0（内存优先）。 |
| `SECURITY-TRUST-BOUNDARIES.md` | 信任边界：自定义代码不是安全沙箱、解析器安装即 import 的风险。 |
| `review/CODE_REVIEW_REPORT.md` | 2026-09 全仓库代码审查报告（安全问题 S1–S13、H 项等）。 |
| `review/DEPENDENCY_UPGRADE_PLAN.md` | 依赖升级计划。 |
| `review/DEVLOG-*.md` | 审查与修复过程的开发日志。 |
| `roadmap/ANALYSIS-AND-ROADMAP-2026-09.md` | 现状分析与后续路线图（R0–R2 批次）。 |
| `roadmap/DEVLOG-phase0-2.md` | 阶段 0–2 的实施日志。 |
| `roadmap/REPO-SECURITY-GUIDE.md` | 仓库/Release 安全加固清单（谁能改 Release 就能在所有用户机器上执行代码）。 |
| `migrations/*` | Gemini/DeepSeek 配置迁移说明与迁移前快照。 |
| `examples/*.json` | 工作流示例（多分支 case、状态感知流程）。 |

---

## 7. `tests/` —— 测试

### 7.0 目录约定

- `tests/test_*.py`：自动收集的回归测试；
- `tests/manual/`：需要真实服务（`:8000` / `:8199`）与浏览器的**手动脚本**，由 `conftest.py`
  的 `collect_ignore` 排除，说明见 `tests/manual/README.md`；
- `tests/fixtures/`：解析器黄金样本与棘轮基线。

### 7.1 测试基础设施

| 文件 | 作用 |
| --- | --- |
| `conftest.py` | 注册 `browser` / `real_browser` / `local_fixture` 标记；静态识别依赖本地私有资源的测试，缺资源时 skip 而非失败。 |
| `_dashboard_js.py` | 把拆分后的 `dashboard/methods-*.js` 按 `index.html` 顺序拼成完整源码（JS 断言与 Node 执行）。 |
| `_env_scan.py` | 静态扫描代码里读取的环境变量名，保证都在 `settings_registry` 中登记。 |
| `_playwright.py` | Playwright 浏览器启动辅助（无 Chromium 时回退本机 Chrome/Edge，再不行 skip）。 |
| `_real_browser.py` | 启动一次性 Chromium 做真浏览器测试。 |
| `_sites.py` | 把 `config/sites/*.json` 拼回旧的 `sites.json` 结构，测试无需关心存储布局。 |
| `js/dashboard.test.mjs` | 前端 Node 单测（`node --test`），由 `test_js_unit.py` 转发执行。 |

### 7.2 测试主题（按文件）

- **协议与端点**：`test_protocol_conformance.py`（用官方 openai/anthropic SDK 当客户端）、`test_chat_job.py`、`test_metrics.py`、`test_tab_route_group_api.py`、`test_command_navigation_dispatch.py`。
- **请求生命周期**：`test_cancel_storm_regressions.py`、`test_request_lifecycle_safety.py`、`test_request_history_perf.py`、`test_runtime_store.py`。
- **浏览器与驱动**：`test_driver.py`、`test_driver_ratchet.py`（禁止绕过驱动层直连 DrissionPage）、`test_browser_process_model.py`、`test_browser_watchdog.py`、`test_browser_profile_identity.py`、`test_cdp_hygiene.py`、`test_idle_maintenance.py`、`test_browser_operations_studio.py`、`test_browser_upgraded_sites.py`、`test_browser_site_capabilities.py`。
- **解析器 / 提取器**：`test_parser_golden.py`（fixtures 黄金样本）、`test_lmarena_incremental_parse.py`、`test_doubao_parser_images.py`、`test_grok_parser.py`、`test_stream_monitor_image_detection.py`、`test_stream_snapshot.py`、`test_selector_quality.py`。
- **配置系统**：`test_site_schema.py`、`test_site_store.py`、`test_site_discovery.py`、`test_settings_registry.py`、`test_config_manager_safety.py`、`test_config_compare_remote.py`、`test_preset_transfer.py`、`test_site_workflow_upgrade.py`。
- **工作流与输入**：`test_workflow_multi_case.py`、`test_workflow_flow_runtime.py`、`test_workflow_script_loader.py`、`test_workflow_click_verification.py`、`test_workflow_js_exec_integration.py`、`test_input_chunking.py`、`test_text_input_atomic.py`、`test_attachments.py`、`test_attachment_browser.py`、`test_media_*`。
- **前端**：`test_js_unit.py`、`test_adapter_panel_ui.py`、`test_attachment_ui.py`、`test_commands_tab_result_races.py`、`test_config_tab_save_queue.py`、`test_operations_studio_ui.py`、`test_site_studio_ui.py`、`test_workflow_studio_integration.py`、`test_tailwind_build.py` / `test_tailwind_equivalence.py`。
- **工程约束（棘轮）**：`test_exception_ratchet.py`（禁止裸 `except`）、`test_repo_hygiene.py`（仓库卫生）、`test_python_min_version.py`、`test_uv_environment.py`、`test_class_structure.py`、`test_review_fixes_p1/p2/p3.py`。
- **更新与安全**：`test_updater_safe_extract.py`、`test_updater_site_files.py`、`test_update_check_reuse.py`、`test_adapter_updates.py`、`test_adapter_health.py`、`test_worker_mode.py`。
- **结构与工程约束**：`test_structure.py`（包边界、根目录允许清单、版本一致性、入口、无同名遮蔽、无重复 CHANGELOG…）。
- **手动/E2E 脚本**（不收集为单测）：`tests/manual/` 下的 `e2e_*_headed.py`、`browser_*.py`。
- `fixtures/`：解析器黄金样本（各站点 SSE 片段）、`broad_except_ratchet.json` / `driver_ratchet.json`（棘轮基线）、`vendor/tailwindcss-play-3.4.17.js`（离线构建用）。

---

## 8. `scripts/` —— 维护脚本（仅 5 个被跟踪）

| 文件 | 作用 |
| --- | --- |
| `build_env_example.py` | 依据 `app/core/settings_registry.py` 生成 `.env.example`。 |
| `build_sites_index.py` | 生成/校验 `config/sites/index.json`（版本 + SHA-256）。 |
| `build_tailwind.py` | 用 Node/npx 编译 `static/css/tailwind.css`（固定 Tailwind 3.4.17）。 |
| `bundle_workflow_studio_css.py` | 把 `workflow-studio.css` 内联进 `workflow-studio.js`（离线单文件）。 |
| `upgrade_gemini_deepseek.py` | 显式、可重复执行的随仓库配置升级（Gemini 保留四预设，DeepSeek 收敛为「主预设」），**不是**启动迁移。 |

`scripts/` 其余内容（`scripts/custom/`、缓存等）按 `.gitignore` 白名单策略只留在本地。

### 8.1 `tools/` 与 `examples/`

| 路径 | 作用 |
| --- | --- |
| `tools/perf_watch.py` | 只读性能采样（psutil 读进程信息，不连 CDP），对比两个版本的 CPU/内存。 |
| `examples/arena_image_batch.py` | 通过本项目的 OpenAI 兼容接口批量生图；参数：`--base-url`、`--path`、`--model`、`--output-dir`、`--prompt-file`、`--character-image`、`--style-image`、`--both`。 |

### 8.2 `.github/workflows/ci.yml`

三个 job：`ruff`、`pytest`（Ubuntu + Windows，`-m "not browser and not real_browser"`）、
`structure`（结构棘轮 + `.env.example` 新鲜度 + 仓库卫生 + uv 环境）。

---

## 9. 关键调用链与阅读顺序建议

### 9.1 一次非流式请求的完整路径

1. 客户端 → `main.py`（中间件：认证、CORS、请求 ID）→ `app/api/routes.py`。
2. `app/api/chat.py`：解析 OpenAI 请求 → 规范化模型名/预设 → `ChatJob`。
3. `app/services/request_manager.py`：入队、并发控制、注册取消。
4. `app/core/tab_pool_parts/pool_routing.py` + `pool_acquire.py`：按模型/站点/预设选标签页。
5. `app/core/workflow/executor*.py`：切模型 → 输入文本/附件 → 发送 → 等待。
6. `app/core/network_monitor.py` + `app/core/parsers/*`（或 `stream_monitor`）增量收集文本。
7. `app/core/extractors/*`：结束时补全/兜底提取。
8. `app/services/tool_calling*.py`（若带 tools）→ `app/api/chat.py` 组装响应。
9. `app/services/storage/runtime_store.py` 记录历史；标签页归还池中。

流式请求在第 6 步之后直接通过 `SSEFormatter` / `RequestStreamingResponse` 边收边发。

### 9.2 想快速上手读代码，建议顺序

```text
main.py
 → app/api/routes.py → app/api/chat.py（先看 /v1/chat/completions）
 → app/services/request_manager.py
 → app/core/tab_pool.py（门面）→ tab_pool_parts/manager.py → pool_routing.py
 → app/core/browser/main.py → browser/workflow.py
 → app/core/workflow/executor.py
 → app/core/network_monitor.py → app/core/parsers/base.py + 某一个站点解析器
 → app/services/config/engine.py（理解配置从哪来）
 → app/services/tool_calling.py（工具调用）
 → config/sites/<站点>.json（理解一个站点是怎么被描述的）
 → static/index.html → static/js/dashboard.js（理解面板）
```

### 9.3 目录职责速记

| 目录 | 一句话 |
| --- | --- |
| `app/api` | 对外协议（OpenAI / Anthropic / 面板 REST） |
| `app/core` | 「怎么操作浏览器」：驱动、池、工作流、监听、解析、提取 |
| `app/services` | 「业务规则」：配置、命令、工具调用、请求生命周期、存储、指标 |
| `app/utils` | 可复用纯函数 |
| `app/runtime` | 进程启动与自重启 |
| `app/worker` | 可选的进程拆分（API ↔ 浏览器 worker） |
| `config` | 站点适配器 + 可热改的行为参数 |
| `static` | 控制面板与编辑器（无构建步骤，Vue 全局构建） |
| `tests` | 回归 + 架构棘轮（防止退化） |
| `docs` | 设计、审查、迁移、路线图 |
