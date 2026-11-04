# 改造后的项目结构与改造成果（2026-10-19）

> 本文回答两个问题：**现在仓库长什么样、每个文件干什么**，以及**这轮结构改造具体改变了什么、凭什么说功能没变**。
> 改造范围仅限包边界、路径解析与 HTTP 路由分层；**URL、请求/响应格式、启动方式与业务逻辑一律未动**。
> 逐条改动记录见 `docs/STRUCTURE-FIX-CHECKLIST.md`，日常查阅用的仓库地图仍是 `docs/PROJECT-STRUCTURE.md`。

---

## 0. 一句话结论

把 4 个「什么都干」的巨型文件拆成 **按职责分层的包**，同时用**路由快照 + 三道棘轮 + 全平台 CI** 把「行为没变」钉死：

| 指标 | 改造前 | 改造后 |
| --- | --- | --- |
| 最大单文件 | `app/api/tab_routes.py` 5142 行 | `app/api/chat.py` 2542 行 |
| `app/api/` 文件数 | 18 | **36**（新增 3 个子包） |
| 超过 2000 行的协议文件 | 3 个（`tab_routes` / `chat` / `system`） | 1 个（`chat`，已单列排期） |
| 逐字节重复的辅助函数 | 9 个（`chat` 与 `tab_routes` 各一份） | 0 |
| 对外 URL | 134 条 | **134 条，逐条一致** |
| pytest | 1302 通过 / 2 失败（改动前就存在） | **1331 通过 / 0 失败** |
| CI | 无（本轮新建） | ruff + 结构 + ubuntu + **windows** 四作业全绿 |

---

## 1. 顶层结构

```text
universal-web-api/
├── main.py                     1113 行  应用入口：FastAPI 实例、中间件、静态挂载、启动流程
├── app/                                全部后端源码（见 §3）
├── config/                             随仓库分发的配置 + 运行时状态（见 §4）
├── static/                             控制面板 / 教程 / 工作流编辑器前端（Vue 3 + 原生 JS）
├── docs/                               架构、评审、路线图、专题说明（本文件也在其中）
├── tests/                              89 个 pytest 文件 + 前端 Node 单测 + fixtures
│   └── manual/                         需真实浏览器/已登录站点的手工脚本（不参与自动收集）
├── scripts/                            仓库维护脚本（生成 .env.example、sites/index.json…）
├── tools/                              本地开发工具（perf_watch.py）
├── examples/                           调用示例（arena_image_batch.py）
├── assets/                             README 效果图
├── .github/workflows/ci.yml            CI：ruff + pytest(ubuntu/windows) + 结构一致性
├── pyproject.toml                      依赖与工具配置（唯一依赖事实来源）
├── uv.lock                             uv 精确锁定版本
├── VERSION                             语义化版本（唯一来源，3.0.1）
├── .env.example                        由 scripts/build_env_example.py 生成的变量模板
├── .python-version / .gitattributes / .git-blame-ignore-revs
├── patch_drissionpage.py      972 行   在导入 DrissionPage 之前施加运行期补丁
├── updater.py                 17 行    ┐
├── update_preserve.py         17 行    ├ 兼容壳：把 import 转发到 app/update/*
└── clean_profile.py           17 行    ┘
```

---

## 2. 根目录文件

| 文件 | 作用 |
| --- | --- |
| `main.py` | 组装 FastAPI：CORS/安全中间件、`app/api/routes.py` 的 router、静态与媒体挂载、启动引导与生命周期钩子。`python main.py` 仍是唯一启动命令。 |
| `app/__main__.py` | `python -m app` 薄入口，转交 `main.py`，行为完全一致。 |
| `patch_drissionpage.py` | 独立补丁工具。**刻意留在根目录**：必须在导入 DrissionPage 之前单独执行（bootstrap 用 subprocess 调用），不能放进包内。 |
| `updater.py` / `update_preserve.py` / `clean_profile.py` | 兼容壳。用 `sys.modules` 顶替实现模块，保证模块级常量只有一份；旧文档与既有测试仍可用，新代码请直接 `from app.update...`。 |
| `pyproject.toml` | 依赖、markers、ruff 配置（`package = false`，靠 `VERSION` + `tests/test_structure.py` 守护版本单一来源）。 |
| `VERSION` / `.env.example` / `uv.lock` | 版本号 / 变量模板 / 精确锁定。 |

---

## 3. `app/` 包

### 3.1 顶层

| 路径 | 行数 | 意义 |
| --- | --- | --- |
| `app/__init__.py` | 22 | 包入口，导出 `__version__`（读 `VERSION`）。 |
| `app/__main__.py` | 24 | `python -m app` 入口。 |
| `app/paths.py` | 73 | **路径唯一来源**：`PROJECT_ROOT` / `STATIC_DIR` / `DOWNLOAD_DIR` / `CONFIG_DIR`，并提供 `ensure_project_cwd()`。改造前多处用 `Path("static")` 这类相对路径，依赖启动目录。 |
| `app/api/` | 36 文件 / 19184 行 | HTTP 路由层（见 3.2）。 |
| `app/core/` | 109 文件 / 57940 行 | 核心引擎：浏览器、标签页池、工作流、流监控、解析器、提取器（见 3.3）。 |
| `app/services/` | 61 文件 / 29572 行 | 业务服务：配置引擎、命令引擎、请求管理、工具调用、存储、指标（见 3.4）。 |
| `app/runtime/` | 1080 | 启动引导、受控浏览器拉起、进程自重启（见 3.5）。 |
| `app/update/` | 4 文件 / 1725 行 | 自更新引擎、更新保留规则、浏览器 profile 清理。 |
| `app/utils/` | 20 文件 / 4686 行 | 无状态工具：附件、图片、剪贴板、URL、远程资源安全、媒体访问控制（见 3.5）。 |
| `app/models/` | 2 文件 / 1583 行 | Pydantic 数据模型与 API Schema（主体 `schemas.py` 1511 行）。 |
| `app/worker/` | 6 文件 / 610 行 | 实验性双进程模型（API 进程 + 浏览器 worker），默认关闭。 |

### 3.2 `app/api/` —— HTTP 路由层

> 所有模块只做「协议 → 内部调用 → 协议」的翻译，不直接操作浏览器。

| 文件 | 行数 | 意义 |
| --- | --- | --- |
| `routes.py` | 38 | 路由聚合入口：把下面各 router `include_router` 成一个，`main.py` 只导入它。 |
| `chat.py` | 2542 | **核心协议文件**：`/v1/chat/completions`、实验性 `/v1/responses`、`/v1/models`（含 `/v1/v1/models` 别名）。负责 OpenAI 字段解析、多模态消息拆解、错误码映射、流式/非流式两条出口、Responses 状态缓存。 |
| `openai/__init__.py` | 18 | 四分组打包入口，`router = shared.router`。 |
| `openai/shared.py` | 1318 | 四组共用的解析/配置/SSE/生命周期辅助，并持有**唯一的 `APIRouter` 实例**（URL 与注册顺序因此不变）。 |
| `openai/tab.py` | 831 | `/tab/{tab_index}/v1/*`。 |
| `openai/url.py` | 1120 | `/url/{route_domain}[/{preset_name}]/v1/*` 与按暴露模型名路由。 |
| `openai/group.py` | 351 | `/group/{group_id}[/{preset_name}]/v1/*`。 |
| `openai/tab_url.py` | 955 | `/tab-url/{url_token}[/{preset_name}]/v1/*`。 |
| `openai/runtime.py` | 11 | 运行期依赖单点：`get_browser` / `request_manager` / `cancel_storm_guard`。 |
| `tab_routes.py` | 316 | **聚合层**：导入四分组并再导出全部原有名字，`tab_routes.<name>` 与拆分前一致。 |
| `admin/__init__.py` | 24 | admin 层说明与导出。 |
| `admin/pool.py` | 463 | 标签页池管理：`/api/tab-pool/*`、`GET /api/pool/status`。 |
| `admin/presets.py` | 148 | 站点预设管理：`/api/presets/*`。 |
| `system/__init__.py` | 240 | 系统端点聚合层（`from app.api.system import router` 保持可用）。 |
| `system/health.py` | 92 | `GET /health`、`GET/DELETE /api/logs`。 |
| `system/settings.py` | 804 | `/api/settings/*`：`.env` 读写、设置备份/恢复、浏览器常量、更新保留规则。 |
| `system/updates.py` | 266 | `/api/update/*` 与启动时更新检查。 |
| `system/diagnostics.py` | 1468 | `/api/debug/*`、`/api/system/*`：选择器调试、强制释放/取消、进程与磁盘统计、请求历史。 |
| `system/common.py` | 81 | 四组共用：JSON/文件读写、原子写、`.env` 解析、重启调度。 |
| `system/runtime.py` | 11 | `get_browser` / `request_manager` 的单一替换点。 |
| `anthropic_routes.py` | 1761 | Anthropic Messages 兼容层：`/v1/messages`、`count_tokens` 及 `/url/{domain}[/{preset}]` 变体；OpenAI 流 → Anthropic SSE 反译。 |
| `config_routes.py` | 1678 | 配置管理 API：站点/预设/图片/提取器/流式配置、工作流脚本、隔离标签页、与 main 分支配置对比。 |
| `config_route_models.py` | 220 | 上面这些接口的请求模型与预设规范化。 |
| `config_compare_support.py` | 604 | 「与官方 main 分支配置对比」的实现。 |
| `config_workflow_support.py` | 592 | 工作流编辑器的注入/消费/脚本缓存辅助。 |
| `openai_stop.py` | 891 | OpenAI `stop` 序列在流式输出中的安全截断（跨 chunk 缓冲）与 SSE 工具。 |
| `openai_responses_support.py` | 650 | **Responses 纯转换层**：响应归一化、chat 载荷 → Responses 输出、usage、错误体、response item 构造。 |
| `stream_helpers.py` | 206 | **跨协议流式辅助**：媒体项抽取/去重、增量文本与错误提取、超时判定、`_pack_error/_done`。 |
| `cmd_routes.py` | 882 | 命令系统 API：命令与分组 CRUD、启停、排序、试跑、分组执行、结果存取、图片资产上传。 |
| `deps.py` | 131 | 共享 FastAPI 依赖：`Authorization` / `X-API-Key` 提取与面板/服务鉴权。 |
| `provider.py` | 127 | `GET /v1/provider/capabilities` 与 `/status`（机器可读能力与运行状态，无密钥）。 |
| `browser_routes.py` | 188 | 受控浏览器集成：仅回环/认证可用，在指定 Chrome profile 打开 URL。 |
| `adapter_routes.py` | 81 | 站点适配器在线更新：查询、应用、健康巡检、lint。 |
| `metrics_routes.py` | 22 | `GET /metrics`，Prometheus 文本格式。 |
| `streaming_response.py` | 47 | `RequestStreamingResponse`：客户端断开时立即关闭生成器，防止标签页被长期占用。 |

### 3.3 `app/core/` —— 核心引擎

| 路径 | 行数 | 意义 |
| --- | --- | --- |
| `browser/` | 9+764+166+3225+403+2932 | 浏览器核心：连接管理、媒体处理、提示词填充、工作流混入（`media.py`/`workflow.py` 是历史大文件）。 |
| `driver/` | 52+64+458+63+129+153+35 | **驱动抽象层**：业务代码经由 `driver_for_tab` 访问浏览器，`fake.py` 供单测替代真实标签页；棘轮禁止业务层直接调 DrissionPage。 |
| `config.py` + `config_parts/` | 194 + 11 文件 | 配置兼容门面 + 日志/SSE/环境变量/浏览器常量/异常等基础件。 |
| `tab_pool.py` + `tab_pool_parts/` | 24 + 15 文件 | 标签页池：借用归还、分配策略、上下文/CDP、路由、维护、状态、会话、恢复、网络。 |
| `workflow/` | 17 文件 | 工作流引擎：v2 结构化编译器（`flow_runtime`）、执行器与各 Mixin、附件监控/上传、文本输入、模型选择、Arena 守卫与看门狗。 |
| `parsers/` | 18 文件 | 站点响应解析器（gemini / chatgpt / deepseek / claude / doubao / glm / grok / kimi / qwen / aistudio / mimo / lmarena 系列），统一 `parse_chunk` 契约。 |
| `extractors/` | 7 文件 | 内容提取器：deep / dom / hybrid / image / media + 注册中心。 |
| `stream_monitor.py` | 2889 | 流式监听核心（页面轮询 + 快照 + 观察者钩子）。 |
| `network_monitor.py` | 3207 | 网络响应拦截监听器（按 pattern 捕获流式响应）。 |
| `stream_snapshot.py` / `stream_observer.py` | 475 / 75 | 页面内快照（一次 CDP 往返）与流式观察者接口。 |
| `elements.py` | 553 | 元素查找与缓存。 |
| `page_capture/` | 6 文件 | 页面侧 fetch 传输（DeepSeek / Kimi 等 provider 专用捕获）。 |
| `page_lifecycle.py` | 310 | 后台标签页保活。 |
| `request_transport.py` | 143 | 预设级请求传输配置。 |
| `chat_job.py` | 224 | 统一的内部聊天请求 `ChatJob` 与类型化事件 `ChatEvent`。 |
| `cdp_hygiene.py` | 265 | DrissionPage 运行期 CDP 资源卫生补丁。 |
| `settings_registry.py` | 375 | **环境变量唯一来源**（`.env.example` 由它生成）。 |
| `http_security.py` | 357 | 控制面 HTTP 安全边界（CORS、鉴权、脱敏）。 |
| `background_image_downloader.py` | 767 | 后台图片下载（目录由 `paths.download_dir()` 解析）。 |
| `workflow_editor.py` | 198 | 可视化工作流编辑器的注入管理。 |

### 3.4 `app/services/` —— 业务服务层

| 路径 | 行数 | 意义 |
| --- | --- | --- |
| `request_manager.py` | 2625 | **请求生命周期管理**：排队、运行、取消、历史记录、累计统计。 |
| `request_lifecycle.py` | 559 | 请求执行期辅助：worker 线程清理、标签页退役、硬超时。 |
| `restart_guard.py` | 169 | 协作式计划重启护栏。 |
| `config_engine.py` + `config/` | 17 + 13 文件 | 配置引擎：站点目录/预设/媒体/流式/本地覆盖/迁移 + 子管理器（`engine.py` 重新导出各 mixin）。 |
| `command_engine*.py` | 9 文件 | 命令引擎：CRUD、动作执行、调度、触发、页面检查、结果。 |
| `command_result_store.py` | 225 | 命令执行结果持久化。 |
| `tool_calling*.py` | 5 文件 | OpenAI tools 适配：解析、校验重试、提示构建、公共辅助。 |
| `arena_*.py` | 9 文件 | Arena 专项：直连模型目录、生图守卫、盲测命令、代理轮换、规则匹配、过盾、监听器。 |
| `catalog_routing.py` | 425 | 动态模型目录与路由派发。 |
| `adapter_health.py` / `adapter_updates.py` | 147 / 231 | 站点适配器健康巡检与热更新。 |
| `arena_cf_solver.py` / `cf_turnstile_solver.py` | 105 / 460 | Cloudflare Turnstile / 5s 盾处理。 |
| `error_metadata.py` | 428 | 标准错误元数据与响应构建器（OpenAI/Anthropic 错误体）。 |
| `extractor_manager.py` / `parser_manager.py` | 391 / 522 | 提取器与解析器的运行期注册/安装。 |
| `metrics.py` | 214 | 无第三方依赖的 Prometheus 指标与请求 ID。 |
| `result_event_bridge.py` | 766 | 网络结果事件的旁路桥接。 |
| `selector_quality.py` | 81 | 选择器脆弱度静态检查（只给建议，不改配置）。 |
| `storage/runtime_store.py` | 206 | 运行时 SQLite 存储（请求历史、累计统计）。 |
| `sse_utils.py` / `text_filter.py` / `transport_profile_handlers.py` | 64 / 60 / 203 | SSE 工具 / 文本过滤 / 传输 profile 处理器。 |

### 3.5 `runtime` / `update` / `utils` / `models` / `worker`

| 路径 | 行数 | 意义 |
| --- | --- | --- |
| `runtime/bootstrap.py` | 344 | 导入应用代码之前的环境准备（解释器、依赖、目录、打补丁）。 |
| `runtime/browser_launcher.py` | 638 | 启动并校验受控 Chromium（调试端口、profile）。 |
| `runtime/relaunch.py` | 97 | 服务自重启（`os.execv`，不依赖退出码 3）。 |
| `update/engine.py` | 1332 | 自动更新：检查、下载、校验、原子替换、回滚。 |
| `update/preserve.py` | 234 | 更新保留清单（`.venv`、用户数据等）。 |
| `update/profile.py` | 136 | 浏览器 profile 清理。 |
| `utils/attachments.py` | 403 | 协议无关的输入附件模型（不做浏览器交互）。 |
| `utils/file_paste.py` / `paste.py` | 421 / 206 | 文件粘贴与大文本分片。 |
| `utils/image_handler.py` / `image_validation.py` | 424 / 452 | 图片处理与相似度/参考图过滤。 |
| `utils/media_access.py` / `media_safety.py` | 172 / 213 | `/media` 出口鉴权、转码预算、落盘安全。 |
| `utils/remote_resource.py` | 343 | 拉取用户提供的远端资源时的 SSRF/DNS 固定等防护。 |
| `utils/site_url.py` / `site_rules.py` / `site_discovery.py` | 256 / 184 / 72 | 站点域名归一、路由规则、自动发现准入。 |
| `utils/model_routing.py` / `tab_route_groups.py` | 284 / 106 | 模型 ID → 路由目标；路由组规范化。 |
| `utils/browser_profile_identity.py` | 195 | 解析某个标签页所属的 Chrome profile 名。 |
| `utils/human_mouse.py` / `system_clipboard.py` / `platform.py` | 605 / 110 / 41 | 人类化鼠标、原生剪贴板、平台判定。 |
| `utils/diagnostics_access.py` | 41 | 公开健康/引导接口的「详细信息」访问判定。 |
| `models/schemas.py` | 1511 | 全部 Pydantic 模型。 |
| `worker/` | 6 文件 / 610 行 | 实验性双进程：supervisor / client / protocol / RPC 路由 / 面板转发。 |

---

## 4. 其他目录

| 路径 | 内容 |
| --- | --- |
| `config/` | `sites/`（13 个站点适配器 + `index.json`）、`browser_config.json`、`extractors.json`、`parsers.json`、`image_presets.json`、`site_rules.json`、`commands.json`、运行时 `runtime.sqlite3`。 |
| `static/` | 控制面板单页（`index.html`）、`js/`（Vue 3 组件与页面）、`css/`（含预编译 Tailwind）、`tutorial/` 本地手册、`vendor/`。 |
| `docs/` | `PROJECT-STRUCTURE.md`（仓库地图）、`STRUCTURE-REVIEW.md`（对照评审）、`STRUCTURE-FIX-CHECKLIST.md`（改动清单与验证）、本文件、`architecture/`、`review/`、`roadmap/`、`migrations/`、专题说明。 |
| `tests/` | 89 个 pytest 文件 + `js/` 前端单测 + `fixtures/`（路由表、棘轮基线等）+ `manual/` 手工脚本；三个共享模块 `_sites.py` / `_env_scan.py` / `_dashboard_js.py`。 |
| `scripts/` | `build_env_example.py`、`build_sites_index.py`、`build_tailwind.py`、`bundle_workflow_studio_css.py`、`upgrade_gemini_deepseek.py`、`ci_report_failures.py`（本轮新增）。 |
| `tools/` / `examples/` / `assets/` | 性能采样脚本 / 生图批量示例 / README 截图。 |

---

## 5. 改造成果

### 5.1 拆掉四个巨型文件

| 改造前 | 改造后 | 拆分方式 |
| --- | --- | --- |
| `app/api/tab_routes.py` **5142 行** | `tab_routes.py` **316 行** + `app/api/openai/`（四分组 + shared + runtime） | 按 URL 前缀四拆，共享一个 `router` 实例 |
| `app/api/system.py` **2675 行** | `app/api/system/` 包（6 个模块，最大 1468 行） | 按「探活 / 设置 / 更新 / 诊断」四拆 + `common` |
| `app/api/chat.py` **3299 行** | **2542 行** + `openai_responses_support.py`(650) + `stream_helpers.py`(206) | 抽出纯转换层与跨协议流式辅助 |
| 面板端点内嵌在路由文件 | `app/api/admin/{pool,presets}.py` | 按「管理 vs 协议」分层 |

### 5.2 消除重复

- `chat.py` 与 `tab_routes.py` 中 **9 个逐字节相同**的纯函数 + 3 个 SSE 打包函数 → 现在只有 `stream_helpers.py` 一份（先用 AST 比对 16 个同名函数的字节内容，确认哪些真相同）。
- 校验过 `openai/` 与 `system/` 包内**没有任何可变全局被复制成两份**（`_SYSTEM_STATS_CACHE`、`_update_check_state`、`_route_round_robin_cursor`、各类锁都只有一处定义）。

### 5.3 分层与依赖方向

- **协议层不再夹带管理端点**：`/v1/*` 文件里不允许出现 `/api/tab-pool/`、`/api/presets/`（有测试固化）。
- **依赖单向**：`admin → tab_routes`（再导出层），反向导入被测试禁止。
- **运行期依赖单点**：`openai/runtime.py` 与 `system/runtime.py` 集中 `get_browser` / `request_manager` / `cancel_storm_guard`。这不只是好看——拆包后若各处 `from app.core import get_browser`，测试的 `monkeypatch` 会**静默失效**（Python 按定义模块解析名字），这是本轮踩到并修掉的真实坑。
- **聚合层保留 API**：`tab_routes.py`、`system/__init__.py` 再导出全部原名，`from app.api import tab_routes; tab_routes.foo(...)`、`from app.api.system import router` 拆分后不变。

### 5.4 兼容性保证（用户视角）

- **URL 一条没变**：`tests/fixtures/route_table.json` 冻结 134 条 `(path, methods)`，重构全程逐条比对；另有测试禁止同一 `(path, method)` 注册两次。
- **启动命令不变**：`uv run python main.py` / `python -m app` 均可用。
- **磁盘布局不变**：`config/`、`static/`、`download_images/` 位置与格式未动（CWD 无关化改造只是把路径解析集中到 `app/paths.py`，并保留同样的相对布局）。
- **对外行为不变**：所有服务器端实现逻辑是**整段搬移**（AST 定位起止行后原样拼接），不是重写。

### 5.5 工程化

- 新增 CI（`.github/workflows/ci.yml`）：ruff、结构一致性、pytest（ubuntu + windows）。
- 三道棘轮只能收紧：结构测试（包边界/路径/版本/端点归属）、路由表、异常计数（1422 → 1413）与驱动直接调用（0）。
- 修复了 CI 自身的三个坑：`ruff` 在 dev 组却被 `--no-dev` 排除；Windows 下 stdout 用 cp1252 导致中文日志把 pytest 打崩；失败用例无法在受限网络下定位（现在转成 GitHub 注解）。

---

## 6. 验证证据

| 项目 | 结果 |
| --- | --- |
| `pytest`（本地全量） | **1331 passed / 157 skipped / 0 failed** |
| `pytest -m "not browser and not real_browser"`（CI 同参数） | **1331 passed / 84 deselected** |
| `ruff check .` | 通过 |
| 路由快照 | 134 条零差异 |
| CI | ruff ✓ / 结构 ✓ / ubuntu ✓ / windows ✓ |
| 手工冒烟 | `import main` 正常，`app.routes` 136 个 path 条目（含 `/openapi.json` 等框架路由） |

---

## 7. 未完成项与原因

| 项 | 状态 | 原因 |
| --- | --- | --- |
| `chat.py` 端点级拆分（`/chat/completions`、`/responses`、`/models`） | 未做 | Responses 的**状态缓存**（`_responses_state_by_id` / `_load_responses_state` 等）被测试直接 patch 且需要请求上下文，必须先下沉到 `app/services/` 才能安全拆。 |
| `openai/shared.py` 再拆（1320 行） | 未做 | 它是四组路由的共同底座，继续拆的收益低于回归风险。 |
| C3：`config/` 只读 + `var/` 可写 | 暂缓 | 涉及用户数据迁移、备份/恢复接口与回滚方案，必须单独排期。 |
| C4：`src/` 布局、包改名 `app → uwapi` | 不做 | 项目按压缩包替换发布（`package = false`），src 布局收益为 0；改名会打断更新器路径与用户数据定位。 |
| `app/core/browser/media.py`(3225)、`tab_pool_parts`、`workflow/` 等历史大文件 | 不在本轮范围 | 它们是浏览器自动化内核，与 HTTP 分层无关；拆分需先补更强的行为快照测试。 |
