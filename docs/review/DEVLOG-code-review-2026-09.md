# 代码审查开发日志（2026-09-25 起）

> 用途：记录本轮"全仓库 Bug / 优化点审查"的进度与关键信息，防止上下文压缩或沙盒重启后丢失。
> 最终汇总报告：`docs/review/CODE_REVIEW_REPORT.md`（审查完成后生成）。

## 0. 恢复指引（上下文丢失 / 沙盒重启后先看这里）

1. `cd /home/user/universal-web-api && git pull && git log -1` 确认最新提交点。
2. 阅读本文件的「3. 进度清单」与「4. 发现列表」，从第一个未勾选的模块继续。
3. 推送凭据只写在 `.git/config`（`http.https://github.com/.extraheader`），**不得写入任何被跟踪的文件或日志**；
   若沙盒重启导致 `.git/config` 丢失，需重新向用户确认/使用会话中提供的临时 token 重新配置。
4. 沙盒约束（用户硬性规则）：
   - 每完成一个小项立即 `git add` → `git commit` → `git push origin main`；
   - 禁止安装 Playwright/Chromium、禁止重度 E2E、禁止保留 png 截图；
   - 临时文件用完即删；工作区快照上限约 128MB / 10k 文件。
   - 用户规则里提到的 `node js/build.js dev`、`node js/tests.js` **在本仓库不存在**（前端位于 `static/js`，无构建脚本），
     替代的轻量验证：`node --check <file>`（JS 语法）、`python -m unittest` / `pytest`（纯逻辑测试，跳过 `tests/browser_*.py`）。

## 1. 仓库概况

- 仓库：`lumingya/universal-web-api`，默认分支 `main`，版本文件 `VERSION=3.0.0`。
- 克隆方式：`git clone --filter=blob:none`（部分克隆，保留完整提交历史，.git ≈ 4MB）。
- 规模：323 个跟踪文件；Python 约 12.8 万行（226 个 .py），前端 JS 约 2.8MB（Vue 3 全局构建 + 自研组件）。
- 技术栈：FastAPI + Uvicorn + Pydantic v2 + DrissionPage（CDP 驱动 Chromium）；把已登录的 AI 网页转成
  OpenAI / Anthropic 兼容接口。
- 自动更新：`updater.py` 只从 **GitHub Releases** 拉包（`/releases/latest`），往 `main` 推送文档不会被自动分发。
- 超大文件（审查重点，>3000 行）：`app/api/tab_routes.py`(5142)、`app/core/tab_pool_parts/manager.py`(4512)、
  `app/services/command_engine.py`(3836)、`app/services/config/engine.py`(3341)、`app/core/extractors/media_extractor.py`(3248)、
  `app/core/browser/media.py`(3244)、`app/api/chat.py`(3242)、`app/core/network_monitor.py`(3201)、
  `app/services/command_engine_actions.py`(3166)。

## 2. 审查方法

- 静态：`python -m compileall`、pyflakes/ruff（未定义名、未使用变量、可疑比较等）、`node --check`。
- 测试：运行 `tests/test_*.py` 中的纯逻辑测试（不运行 `tests/browser_*.py` 和依赖真实浏览器的用例）。
- 人工：按模块阅读，重点关注 安全（认证/CORS/命令执行/路径穿越/SSRF/解压）、并发（锁/线程/asyncio 混用）、
  资源泄漏（无界缓存/后台任务）、错误处理、配置与文档一致性。

## 3. 进度清单

- [x] 克隆仓库、确认推送权限、建立开发日志
- [x] M1 仓库卫生与配置（.env.example / .gitignore / README / VERSION / requirements）
- [x] M2 静态分析 + 现有单元测试运行结果（排除真实浏览器；详情见 §5）
- [x] M3 安全：认证与中间件（main.py / app/api/deps.py / CORS / 管理接口）——路由依赖逐项核对，ASGITransport 进程内验证默认跨源读取及启用认证时的 401/200；不代表真实浏览器联调
- [ ] M4 安全：命令引擎（CMD_ALLOW_UNSAFE_PYTHON_COMMANDS 等）、updater、文件/路径处理、SSRF——命令/解析器与更新器初查完成，其他待查
- [ ] M5 核心请求链路：chat.py / anthropic_routes.py / streaming_response.py / request_manager.py
- [ ] M6 标签页池：tab_pool_parts/*（manager / session / recovery / idle_maintenance）
- [ ] M7 浏览器与工作流：core/browser/*、core/workflow/*、stream_monitor、network_monitor
- [ ] M8 服务层：tool_calling*、config engine、command_engine*
- [ ] M9 前端：static/js（XSS / v-html / 大文件）
- [ ] M10 汇总报告 `docs/review/CODE_REVIEW_REPORT.md`

## 4. 发现列表（按发现顺序追加；严重度：P0 严重 / P1 高 / P2 中 / P3 低）

| ID | 严重度 | 位置 | 摘要 | 状态 |
| --- | --- | --- | --- | --- |
| S1 | P1 | `app/core/config_parts/env_config.py:159-188`, `main.py:603-616`, `.env.example:21,59,66-69,134` | 管理认证默认关闭、CORS 默认 `*`；复制模板还绑定 `0.0.0.0` 且布尔占位值实际上为 false。跨源页面/局域网调用控制接口存在高风险；建议默认关闭跨源、管理令牌必选（非本机绑定时 fail-closed）、限制 Host/Origin。浏览器本地网络策略会影响可利用性。 | 进程内跨来源请求确认 200 且 `Access-Control-Allow-Origin: *`；仅启用后台认证时无令牌 401、带测试令牌 200；未运行真实浏览器 |
| S2 | P1 | `app/services/command_engine_actions.py:2427-2590,3054-3106`, `app/services/config/engine.py:3312`, `app/services/parser_manager.py:162-227`, `app/core/parsers/registry.py:101-106` | Python 命令的 AST 名单只拦截直接危险调用；别名和传入的高权限对象可绕开，解析器安装会写入并 import 用户源码。此“沙箱”不能作为不可信代码边界；隔离进程/容器并移除危险对象，或只允许可信管理者使用。JS 命令也能访问浏览器上下文。 | 静态确认；未执行攻击代码 |
| S3 | P2 | `app/services/parser_manager.py:162-227,331-345` | 解析器安装功能仅校验语法/类名，不约束代码行为，会立即 import 执行；目前未找到直接对外安装路由，是 S2 传入对象间接暴露的代码安装原语。 | 静态确认；未执行 |
| S4 | P2 | `app/api/browser_routes.py:119-129`, `start.py:836-844,905-922` | `/api/browser/open-profile-url` 不要求令牌，仅检查 `request.client.host` 回环；经本机反代/隧道/重启代理访问时后端看到的来源可能为回环，可在受控浏览器用户目录打开 URL。须使用认证和来源限制，不能仅依赖 IP。 | 静态确认；未联调 |
| S5 | P2 | `start.py:836-892,905-961,1400-1405` | 定时重启开启时的 TCP handoff proxy 无并发上限；每个连接可缓冲 64MB；仅按 Content-Length 读取，chunked/Expect 等请求兼容性不完整，错误被吞。代理后端统一回环地址还影响来源判断。 | 静态确认；仅在重启守护开启时 |
| S6 | P2 | `main.py:728-737,804-825,839-841` | `/media/{filename}`、`/download_images/*` 无认证；可读下载目录文件；media 转码由请求触发（60s，无并发/同键去重），有 CPU/存储放大风险。路径边界校验本身正确。 | 静态确认；未压测 |
| S7 | P3 | `main.py:376-406,689-698`, `app/api/system.py:789-815` | 无认证的启动引导数据返回站点目录；`/health` 暴露版本、浏览器端口/池状态、运行中的 request/tab ID、认证开关，还可能在健康检查时尝试连接浏览器。考虑仅返回简化健康状态。 | 静态确认 |
| H1 | P1 | `.env.example:7-69,100-138`, `app/core/config_parts/env_config.py:111-116,159-171` | 模板重复配置项；`AUTH_ENABLED`/`DASHBOARD_AUTH_ENABLED` 填占位值会解析为 false，且包含 `0.0.0.0`、`APP_DEBUG=true`、CORS `*`、不安全 Python 命令等危险示例；代理、隧道、版本值亦带个人环境痕迹。应重做安全模板与校验。 | 已核对 |
| H2 | P3 | `README.md:21`, `README.en.md:21`, `README.zh-CN.md:21`, `VERSION:1` | 三份 README 仍写 2.9.8 并链接不存在的 CHANGELOG_CURRENT.md；实际 VERSION=3.0.0。 | 已核对 |
| H3 | P3 | `.gitignore:52,77,102-188`, `config/marketplace_cache.json` | 已跟踪的 marketplace_cache.json 仍被 ignore；tests 白名单含 4 个不存在的用例，且没有覆盖全部已跟踪测试；scripts 忽略规则有重叠。建议清理规则及按需 `git rm --cached` 缓存。 | 已核对 |
| H4 | P3 | `main.py`, `app/core/stream_monitor.py`, `static/js/dashboard-schema.js` | 约 40 个文件混用 CRLF/LF，无 `.gitattributes`，增加噪音 diff 与跨平台维护成本；统一 EOL（须先检查各脚本依赖）。 | 已核对 |
| H5 | P3 | `assets/*.png`, `static/*.png`, `static/images/logo.svg` | 多组字节相同的图片复制到 assets/static，logo SVG 约 792KB，另有较大截图；可用单一资源引用/压缩资源。 | 已核对 |
| H6 | P3 | `requirements.txt:6-7`, `requirements-dev.txt` | FastAPI `<0.110`/Uvicorn `<0.30` 较旧，升级应配合测试与锁定依赖。FastAPI 0.109.2 要求 Starlette `>=0.36.3,<0.37`，不在 CVE-2025-62727 的 0.39–0.49.0 受影响范围，**不将该 CVE 误报于当前约束**；dev 依赖包含 Playwright，本轮禁止安装。 | 已核对 |
| B1 | P2 | `app/utils/site_discovery.py:9-18`, `config/site_rules.json:1-22`, `app/services/config/engine.py:1973-1989`, `tests/test_site_discovery.py:18-25,72-76` | 出厂站点规则没有 google.com/bing.com/baidu.com 等 `auto_discovery=false`，默认返回 true；搜索页被当未知聊天站点尝试提取/AI 分析（9 个纯逻辑用例失败），可误用通用工作流。建议补充精确域名阻止规则、保留 gemini.google.com 等子域的可用性。 | 隔离测试复现 |
| T1 | P2 | `tests/test_arena_auto_battle_command.py:71-81`, `tests/test_arena_command_persistence.py:1-15`, `tests/test_workflow_js_exec_integration.py:99-123`, `tests/test_media_stream_image_precedence.py:508-515`, `app/services/arena_model_catalog.py:18-22,128-140` | 测试硬依赖被 `.gitignore` 排除的 `config/commands*.json`、`custom_scripts/examples/arena_payload_interceptor.js`、`config/arena_model_catalog.local.json` 或不存在的 `js/arena-conversation-image-window.user.js`；干净克隆中约 64 个失败主要由缺失 fixture 引起，不能当作产品回归。应提供可分发的最小 fixture 或将这类测试标记为本地集成测试。 | 隔离测试复现 |
| T2 | P3 | `requirements-dev.txt:1-4`, `tests/test_cancel_storm_regressions.py:340-364` | 纯逻辑测试依赖 `httpx`（ASGITransport），dev requirements 未声明；初轮 1 例报 `ModuleNotFoundError`，仅补装 `httpx` 后该例通过。可拆出不含 Playwright 的 tests extra。 | 隔离测试复现 |
| H7 | P3 | `app/utils/model_routing.py:7,213`, `start.py:602,620,678,746` | 延迟注解下 5 处 `Optional` / `Any` 未导入；普通调用未必报错，但 `typing.get_type_hints` 实测分别触发 NameError，静态检查持续报 F821。 | 静态检查 + 轻量复现 |

## 5. 工作流水

- 2026-09-25：部分克隆完成；确认 token 具备 push 权限（dry-run 通过）；确认 updater 仅走 Releases；建立本日志。
- 2026-09-25 / M1：核对 `.env.example`、三份 README、requirements、ignore 规则、已跟踪缓存与换行；记录 H1–H6。同期初查 149 个 API 路由（AST 粗计，6 个未声明 Depends 的路由需逐项判定），并核实认证/CORS、Python 命令与解析器动态 import、启动代理、媒体/健康路由，记录 S1–S7；M3/M4 尚未完成。PyPI 元数据确认 FastAPI 0.109.2 限制 Starlette `<0.37.0`。本阶段只读审查，未做侵入式验证、未修改业务代码。
- 2026-09-25 / M2：生产依赖及 `ruff`/`pyflakes`/`pytest`/`httpx` 安装到工作区外 `/home/user/.venv`；**未安装 Playwright/Chromium**。Python 226 文件用 `tokenize.open` + AST 语法检查均通过（`text_filter.py` 首字节有 BOM，直接按 UTF-8 文本喂给 AST 的一次假阳性已排除）；`node --check` 静态 JS 33 文件通过。Ruff `--target-version py313 --select F821,F822,F823,E9` 报 5 个 F821（H7）；pyflakes 全量原始诊断 419 行，多为重导出/未使用变量，不等于 419 个 bug。首轮 `unittest` 57 例：34 通过、6 失败、17 错误；其中失败/错误都指向缺失本地命令和 JS 示例。仅选无私有 fixture 的两组用例 15/15 通过。
- 2026-09-25 / M2（续）：排除 3 个真实 Chromium 测试文件和两处浏览器 fixture（未启动浏览器）的 pytest 初轮：**548 passed、74 failed、9 deselected**（10.15s）。归因：64 个缺失/过期本地 fixture（T1）、9 个站点自动发现断言失败（B1）、1 个 dev 依赖缺失（T2）。仅补装 `httpx` 后 T2 对应用例通过；过滤上述已知失效用例的独立干净子集 **544 passed、30 deselected**（8.17s）。所有测试生成的临时目录及忽略的 `config/commands.json`、`config/request_history.json`、`config/app_stats.json` 均已删除；无业务代码变更。
- 2026-09-25 / M3：复核 `app/api/deps.py` 的服务与后台双令牌和路由依赖：149 个 APIRoute 中 12 个无 FastAPI 依赖；其中 6 个模型别名路由在函数内手动验服务令牌，另 6 个是 `/`、`/dashboard`、浏览器引导页、`/media/{filename}`、`/health`、`POST /api/browser/open-profile-url`。`httpx.ASGITransport` 进程内测试：默认关闭后台认证时，外来 Origin 的 CORS 预检和 `GET /api/commands` 均返回 200，后者响应允许 `Access-Control-Allow-Origin: *`；临时仅在进程环境中启用后台认证并提供合成令牌后，同一路由无令牌返回 401、带令牌返回 200。无真实浏览器或外部网络访问，未测试浏览器本地网络策略和反向代理拓扑；检查产生的忽略配置已删除。S1、S4、S6、S7 见上。
