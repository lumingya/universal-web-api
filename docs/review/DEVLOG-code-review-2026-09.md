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
- [x] M4 安全：命令引擎、updater、媒体落盘/文件路径、SSRF——以 mock 响应+临时目录验证同源 SVG/HTML 落盘与响应头；直接图片请求及 DNS 校验窗口单独记风险，未访问外网/内网目标
- [x] M5 核心请求链路：chat.py / anthropic_routes.py / streaming_response.py / request_manager.py——核查认证委派、流关闭/取消、Responses 状态与请求历史，轻量复现 B2–B4
- [x] M6 标签页池：tab_pool_parts/*（manager / session / recovery / idle_maintenance）——检查锁与取消/恢复路径；用无浏览器桩验证冻结恢复失败和非有限配置值
- [x] M7 浏览器与工作流：core/browser/*、core/workflow/*、stream_monitor、network_monitor——核查控制流边界、流监听与调试持久化；复现 mtime 缓存及布尔字符串误判
- [x] M8 服务层：tool_calling*、config engine、command_engine*——实际主类验证损坏命令配置回退、校验 URL 正则回溯开销并识别未使用存储 mixin
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
| S8 | P1 | `app/core/browser/media.py:2432-2440,2644-2677,2693-2734,2829-2836`, `app/core/background_image_downloader.py:428-433`, `main.py:839-841` | 后台下载拒绝 SVG，但前台回退将 `image/svg+xml` 响应原样存为可公开访问的 `.svg`（仅要求 `Content-Type` 含 `image`、字节数 >1000，缺安全格式校验）。若上游页面/媒体地址可由攻击者操控，用户**直接导航打开**本地化 SVG 时可能在应用同源执行脚本；作为 `<img>` 加载不应等同于执行。拒绝 SVG 或安全转码为 PNG，媒体与后台统一校验；静态路由另设隔离域/CSP/attachment。 | mock 后台失败、无网络的前台响应成功写 `.svg`；进程内 GET 返回 200 `image/svg+xml`，无 CSP/attachment；未运行真实浏览器 |
| S9 | P1 | `app/core/browser/media.py:2101-2215`, `app/core/extractors/media_extractor.py:929-945,1006-1080`, `main.py:728-742,804-825` | 音视频落盘仅检查 Content-Type 包含 `audio`/`video`；未知 MIME 从远程 URL 取后缀，未校验实际内容。可控 `clip.html` 伴随 `video/custom` 响应会原样写入 `.html` 并返回 `/media/*.html`，而 FileResponse 依据后缀以 `text/html` + `inline` 发送。需攻击者控制上游媒体地址和其响应、受害者直接访问该链接；若后台认证启用，应用同源执行脚本可能读取仪表板本地令牌。仅允许已知安全音视频扩展名、实测格式并在独立域/强制下载提供。 | mock 远程响应成功落 `.html`；ASGI 进程内 GET 返回 200 `text/html; charset=utf-8` + `inline`，无 CSP；未执行脚本/真实浏览器 |
| S10 | P2 | `app/utils/image_validation.py:37-121,151-175,272-370`, `app/services/arena_gpt_image_command.py:155-175,608,642` | 图片比对/C2PA 读取第三方页面提取的 URL 时直接 `requests.get`：无公网地址/重定向校验且读取 `response.content` 无尺寸上限；data URI 和浏览器 `arrayBuffer()` 回退亦无限制。需媒体 URL 经目标站页面进入比对流程，可能触发盲 SSRF、内网请求或内存放大。统一复用安全获取器并限制响应、data URI、像素解码规模。常规**输入**图片处理 `app/utils/image_handler.py` 已有校验，不能混为一谈。 | mock 证明回环 URL 被直接交给 requests（无实际内网请求）；未测真实 SSRF/大响应 |
| S11 | P2（条件性） | `app/utils/remote_resource.py:141-185,206-245` | 公共资源抓取先用 DNS 查询验证地址，再将原始主机名传给 `requests.get()`；连接时可能重新解析，攻击者控制 DNS 时可在验证后切到私网地址。重定向逐次校验、凭据跨域剥离已实现，但不消除验证/连接间竞态。应将实际连接 IP 固定为已验证地址（保持 HTTPS SNI/Host），或在可信出网代理上强制拒绝内网段。 | mock 确认验证发生、传给 HTTP 层的仍是域名；未作真实 DNS 重绑定验证，不宣称已利用 |
| B2 | P2 | `app/services/request_manager.py:545-573` | 历史读取器试图兼容旧版 JSON **顶层数组**，但先调用 `data.get()`，在 list 上报 AttributeError 后被整体捕获，历史被置空；旧格式升级后监控历史无法恢复。先按 `isinstance(data, list)` 分支取值，再统一校验记录。 | 微型临时文件复现：`{"records":[...]}` 恢复 1 条、等价顶层 `[...]` 恢复 0 条；未触及真实历史 |
| B3 | P2 | `app/api/chat.py:93-99,539-608,1030-1076,1950-1955,2420-2425` | Responses API 默认 `store=None` 时存储完整请求历史和助手消息；只有 1h/1024 条**数量**上限，无单条/总字节配额，base64 多模态和连续会话会使内存放大。状态全局只按不可预测 response_id 索引、未按客户端隔离；ID 一旦泄露可跨调用续接历史。建议每项/总量预算、媒体内容剥离/摘要，按认证主体隔离，并在文档说明驻留期。 | 小数据两轮存储字符串长度 137→274；未运行压力测试或尝试跨用户访问 |
| B4 | P3 | `app/api/chat.py:469-475,524-528,2514-2517,2804-2813`, `app/core/config_parts/sse_formatter.py:229` | OpenAI `n` 允许 `>=1`，但请求链未据此生成多条选择；非流式统一构造单条 choices，流式将其只当 stop 处理条件。请求 `n=3` 会静默只得一条，建议验证 `n==1` 时明确拒绝其他值或实现多路执行。README 已笼统说明某些网页不支持的字段会忽略，仍应显式声明此行为。 | 构造 `n=3` 请求模型成功，打包器生成 choices 数=1；未接真实浏览器 |
| H8 | P3 | `app/api/chat.py:157-193,217-249` | 六个 Arena 错误辅助函数同名定义两次，前者随后被后者覆盖；两套提示词拒绝判断/响应构造实现不完全相同，增加维护漂移和死代码。清理重复定义并保留行为单测。 | 静态确认；无执行回归结论 |
| B5 | P2 | `app/core/tab_pool_parts/idle_maintenance.py:346-384`, `app/core/tab_pool_parts/session.py:258-278` | 冻结标签页恢复 CDP 指令报错后，`resume_if_frozen` 返回 false，却在 `finally` 清除 `_uwapi_frozen` 标志；`TabSession.acquire()` 忽略返回值并仍标记 BUSY、返回 true，可能将仍冻结的页面交给请求，后续也不再重试解冻。恢复失败应阻止交付/标为异常并保留待核实状态。 | 假 CDP 抛错的轻量桩复现：acquire=true、status=busy、frozen flag=false；未连接浏览器 |
| B6 | P3 | `app/core/tab_pool_parts/idle_maintenance.py:69-103`, `app/core/tab_pool_parts/manager.py:219` | 环境变量 `BROWSER_CDP_RECYCLE_AFTER_REQUESTS=inf` 或 DOM_NODES=inf 可通过 `float()` 后执行 `int(inf)` 抛 OverflowError，使创建标签页池失败。配置解析须检查 `math.isfinite()` 并回退默认。 | patch 进程环境复现 OverflowError；默认配置无此问题 |
| H9 | P2（优化） | `app/core/tab_pool_parts/manager.py:151-155,2882-2905,3436-3483`, `app/services/request_manager.py:2282-2335` | 获取标签页的独立线程池限制 32 个**工作线程**，但待执行任务的 `ThreadPoolExecutor` 内部队列和池的等待队列未设置总量上限；大量并发请求可占住线程/排队 Future，活跃请求也不按历史记录条数被驱逐。建议在 API 入口统一设置最大并发与等待数、早返回 429/503，并监控队列深度。 | 静态容量分析；未进行大量并发压力测试，不作为已复现的耗尽故障 |
| S12 | P2 | `config/browser_config.json:371-376`, `app/core/config_parts/browser_constants.py:86,219-253`, `app/core/network_monitor.py:1699-1804,1975-1980`, `app/core/config_parts/log_redaction.py:239-311` | 虽然代码/前端默认关闭网络响应调试抓取，**已跟踪的出厂 browser_config.json 却打开** `NETWORK_DEBUG_CAPTURE_ENABLED` 且过滤器为空；命中解析器时会把原始响应正文片段及解析预览写入 `logs/network_parser_debug`（目录上限配置 50MB）。日志净化保护部分令牌/Base64，但正常聊天文本仍可落盘；若配置中填字符串 `"false"`，`bool("false")` 也会错误地启用。应默认禁用调试抓取、要求显式确认隐私及设自动过期/脱敏，严格解析布尔值。 | 实测读取跟踪配置为 true、合成普通聊天文本不会被净化、mock 配置 `"false"` 时启用结果为 true；未使用真实会话或产生调试文件 |
| B7 | P3 | `app/core/workflow/script_loader.py:173-194`, `tests/test_workflow_script_loader.py:130-157` | JS 脚本热加载只按浮点 mtime 判断缓存命中；内容替换但 mtime 不变（恢复时间戳/低分辨率文件系统/快速覆盖）时会执行旧脚本，且测试只覆盖 mtime 改变。建议 `st_mtime_ns+size` 并对更新接口显式清缓存；极端同大小同时间需 hash。 | 临时脚本文件复现：盘上内容 `later`、缓存仍返回 `first`；临时目录已自动删除 |
| H10 | P3 | `app/core/stream_monitor.py:1706-1732,1768-1779` | `_is_arena_page`、`_arena_native_stop_present` 两次定义，前一版松散子串判断被后一版域名解析与独立检测器覆盖；清理死代码与重复维护面，保留后一版的域名边界。 | 静态确认；运行时以前一版已被覆盖为前提，不宣称当前主机校验失效 |
| H11 | P3 | `config/browser_config.json:320-369` | 已跟踪的默认浏览器配置包含多组具体 Arena 会话 URL/标签页编号，不适合作为通用模板，公开仓库中还可能泄露用户曾使用的会话标识。建议用示例占位组与本地不跟踪的覆盖文件替换，必要时清理历史。 | 静态确认 5 组路由数据；未访问这些 URL、未验证是否能读取内容 |
| B8 | P2 | `app/services/command_engine.py:1352-1378,1654-1674` | 外部改写 `commands.json` 时，短暂无效 JSON 被解析为 `[]`，热刷新直接将**上一轮有效命令缓存清空**并推进 mtime，还将原有 `run_js_file` 动作认作禁用、生成清理列表；命令临时全失效，浏览器在线时还可能撤销注入。应在错误时保留 last-known-good，区分合法空列表与解析失败，避免据此清理运行态；文件恢复后再刷新。 | 使用真实 `CommandEngine` 与临时 JSON 文件，1 条命令 → 损坏 JSON → 0 条且捕获 1 条待清理动作；**仅 mock 掉清理执行，未运行浏览器或观察在线注入** |
| H12 | P2（优化/风险） | `app/services/command_engine_results.py:637-653,655-663` | 网络异常触发器允许配置 regex URL 匹配，直接用标准库 `re.search()` 且无模式复杂度、输入长度或匹配超时限制；嵌套量词会指数回溯，频繁事件可阻塞命令检查线程。建议参考工作流已有 `regex.search(..., timeout=0.025)` 做预编译/超时，限制模式和 URL 长度，并拒绝超时规则。 | 仅 12、16、19 个 `a` 的短 URL 合成输入量测约 0.0005/0.0057/0.046s；未进行长串或压力测试，真实影响依环境/模式而异 |
| H13 | P3 | `app/services/command_engine_storage.py:22-335`, `app/services/command_engine.py:60,1340-1380,1654-1674` | `CommandEngineStorageMixin` 在项目内没有被 `CommandEngine` 继承或导入，存储/CRUD 的很多方法却与主类重复，易使后续修复只落在未运行代码中（初次试验也因此需改用生产主类验证）。建议合并、删除或明确弃用该模块并加主类覆盖测试。 | 静态类继承/调用核对，无产品运行错误主张 |

## 5. 工作流水

- 2026-09-25：部分克隆完成；确认 token 具备 push 权限（dry-run 通过）；确认 updater 仅走 Releases；建立本日志。
- 2026-09-25 / M1：核对 `.env.example`、三份 README、requirements、ignore 规则、已跟踪缓存与换行；记录 H1–H6。同期初查 149 个 API 路由（AST 粗计，6 个未声明 Depends 的路由需逐项判定），并核实认证/CORS、Python 命令与解析器动态 import、启动代理、媒体/健康路由，记录 S1–S7；M3/M4 尚未完成。PyPI 元数据确认 FastAPI 0.109.2 限制 Starlette `<0.37.0`。本阶段只读审查，未做侵入式验证、未修改业务代码。
- 2026-09-25 / M2：生产依赖及 `ruff`/`pyflakes`/`pytest`/`httpx` 安装到工作区外 `/home/user/.venv`；**未安装 Playwright/Chromium**。Python 226 文件用 `tokenize.open` + AST 语法检查均通过（`text_filter.py` 首字节有 BOM，直接按 UTF-8 文本喂给 AST 的一次假阳性已排除）；`node --check` 静态 JS 33 文件通过。Ruff `--target-version py313 --select F821,F822,F823,E9` 报 5 个 F821（H7）；pyflakes 全量原始诊断 419 行，多为重导出/未使用变量，不等于 419 个 bug。首轮 `unittest` 57 例：34 通过、6 失败、17 错误；其中失败/错误都指向缺失本地命令和 JS 示例。仅选无私有 fixture 的两组用例 15/15 通过。
- 2026-09-25 / M2（续）：排除 3 个真实 Chromium 测试文件和两处浏览器 fixture（未启动浏览器）的 pytest 初轮：**548 passed、74 failed、9 deselected**（10.15s）。归因：64 个缺失/过期本地 fixture（T1）、9 个站点自动发现断言失败（B1）、1 个 dev 依赖缺失（T2）。仅补装 `httpx` 后 T2 对应用例通过；过滤上述已知失效用例的独立干净子集 **544 passed、30 deselected**（8.17s）。所有测试生成的临时目录及忽略的 `config/commands.json`、`config/request_history.json`、`config/app_stats.json` 均已删除；无业务代码变更。
- 2026-09-25 / M3：复核 `app/api/deps.py` 的服务与后台双令牌和路由依赖：149 个 APIRoute 中 12 个无 FastAPI 依赖；其中 6 个模型别名路由在函数内手动验服务令牌，另 6 个是 `/`、`/dashboard`、浏览器引导页、`/media/{filename}`、`/health`、`POST /api/browser/open-profile-url`。`httpx.ASGITransport` 进程内测试：默认关闭后台认证时，外来 Origin 的 CORS 预检和 `GET /api/commands` 均返回 200，后者响应允许 `Access-Control-Allow-Origin: *`；临时仅在进程环境中启用后台认证并提供合成令牌后，同一路由无令牌返回 401、带令牌返回 200。无真实浏览器或外部网络访问，未测试浏览器本地网络策略和反向代理拓扑；检查产生的忽略配置已删除。S1、S4、S6、S7 见上。
- 2026-09-25 / M4：复核命令引擎受限 Python 执行上下文（含浏览器、配置对象和默认可导入 requests）、解析器动态导入及 updater；`updater.py:828-874` 已限制 ZIP 路径/符号链接/条目数/展开大小/压缩比，下载验 SHA-256 digest，失败会从备份恢复，**不将这些保护误报为缺失**。用无网络 mock 响应和自动清理的微型临时目录分别驱动 SVG 图片前台回退与音视频远程下载：获得 `.svg` 和 `.html`，经 `httpx.ASGITransport` 同源路由实际返回 `200 image/svg+xml`（无 attachment/CSP）与 `200 text/html; charset=utf-8`、`Content-Disposition: inline`（无 CSP）。`read_image_bytes` 的回环 URL 以 mock `requests.get` 证明可达调用点，**未实际连接回环/内网**；公共抓取器 mock DNS 后仍把原主机名交给 requests，重绑定仅按条件性风险报告。所有临时产物与导入生成的忽略配置已删除；未安装/运行浏览器或大文件压力测试。S2、S3、S8–S11 见上。
- 2026-09-25 / M5：阅读 OpenAI / Anthropic 接口的认证委派、SSE 响应关闭与请求生命周期：已实现断连时主动关闭异步生成器、线程工作队列限背压及绝对执行超时，未把这些保护误报为缺陷。隔离小文件测试（不运行浏览器）证实历史旧数组读入丢失（B2）；Responses 默认启用内存存储且连续两轮重复保留完整会话（B3）；构造 `n=3` 可接受但单选择输出（B4）。`chat.py` 六个 Arena 错误辅助函数重复定义（H8）。临时目录及导入生成的忽略配置已清理。
- 2026-09-25 / M6：核对 `TabPoolManager` 的获取/释放条件队列、独立 acquire executor 与取消后回收、Session 状态门禁、隔离恢复服务的超时槽、空闲冻结/CDP 回收逻辑；已存在 0.25s 取消轮询及冻结前检查页面隐藏，不把无真实浏览器实验的状态竞态当成已发生事故。仅用抛错 CDP 桩证明解冻失败仍交付会话（B5），仅用临时 env 值证明配置 inf 可使构造失败（B6）。等待队列未总量限流作为优化风险 H9 记录，不做压力测试；无截图或浏览器下载。
- 2026-09-25 / M7：查阅浏览器媒体/工作流、`flow_runtime.py` 条件与变量解析、`script_loader.py` 路径控制及缓存、DOM/网络流监听。控制流有节点/深度/字节和执行转换预算，正则有 25ms 限时，网络监听有预取容量及调试目录上限，脚本路径解析会限制在指定目录；不能笼统报告缺少这些防护。发现跟踪的 `browser_config.json` 与代码/前端默认开关不一致，默认启用原始响应调试捕获（S12），并携带具体会话 URL（H11）；轻量文件实验确认仅 mtime 缓存导致热更新可能陈旧（B7）；流监听重复方法只记录为维护问题（H10）。无真实浏览器、网络页面或 PNG 截图。
- 2026-09-25 / M8：查阅工具调用同步/异步修复轮次（默认 2、上限 5）、Schema 本地引用/形状/有限数检查及消息格式；这些已有防护不作为缺失报告。核查配置引擎 JSON 错误时热重载保留旧状态、原子保存与本地覆盖快照。命令引擎另有损坏命令文件导致运行缓存归零（B8），实际主类临时文件复现并在清理动作处用 mock 截断；**最初用未继承的 `CommandEngineStorageMixin` 实验只用于提示死代码，最终证据来自生产主类**。命令网络 URL regex 无超时（H12），短 URL 小样本显示回溯增长；存储 mixin 未使用（H13）。所有临时文件由 TemporaryDirectory 清理，无浏览器/耗内存测试。
