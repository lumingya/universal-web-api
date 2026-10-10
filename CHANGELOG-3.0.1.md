# Changelog

本文件格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
以下内容均为相对 **v2.10.0** 的最终变化。

## [3.0.1] - 2026-10-10

### ⚠️ 升级须知（可能影响现有部署）

- **启动期安全自检与旧版配置兼容告警**：
  - 启动期会检查不安全配置并在控制台与日志中输出醒目告警。**为保证从旧版本在线升级的用户服务不中断、控制面板始终可打开修改配置，当前仅输出告警，不拒绝启动服务。**
  - **兼容旧版配置模板**：旧版 `.env.example` 中自带的 `AUTH_ENABLED=your-secret-here` 与 `DASHBOARD_AUTH_ENABLED=your-secret-here` 占位符会自动视作未设置（`false`），避免因缺少令牌导致接口或面板返回 500。
  - 若 `APP_HOST` 绑定到非回环地址（如 `0.0.0.0`），但未同时启用 API 认证与控制面板认证（含令牌），启动时会产生安全告警；请尽快在控制面板中完善认证，或改回 `127.0.0.1`（处于完全可信隔离网络可设置 `ALLOW_INSECURE_PUBLIC_BIND=true`）。
  - `CMD_ALLOW_UNSAFE_PYTHON_COMMANDS=true` 或 `PARSER_INSTALL_ENABLED=true` 与对外绑定同时开启时同样会在启动时给出严重安全告警。
- **CORS 默认不再放行任意来源**：从其他域名的网页直接调用接口时，需要在 `CORS_ORIGINS` 里列出允许的来源；同源访问控制面板不受影响。
- **站点配置改为按站点分文件存放**：首次启动时，`config/sites.json` 会自动迁移到 `config/sites/` 目录，原文件改名为 `sites.json.migrated-<时间>.bak`。`SITES_CONFIG_FILE` 现在只作为迁移来源；站点目录通过新增的 `SITES_CONFIG_DIR` 配置。
- **请求历史与累计统计改存 SQLite**（`config/runtime.sqlite3`）：首次启动时自动导入 `config/request_history.json` 与 `config/app_stats.json`，原文件改名为 `.migrated-<时间>.bak`。
- **最低 Python 版本统一为 3.10**（`start.py`、`start.bat` 会做环境版本校验）。
- `/v1/chat/completions` 收到 `n > 1` 时直接返回参数校验错误，不再静默只返回 1 个结果。
- `/health` 和受控浏览器的引导数据对匿名远程调用者只返回最基本的存活状态；本机直连或携带有效令牌才返回详细信息。
- 运行时安装自定义解析器默认关闭（新增开关 `PARSER_INSTALL_ENABLED`）。分发的 `config/browser_config.json` 中，响应调试抓取 `NETWORK_DEBUG_CAPTURE_ENABLED` 也改为默认 `false`。需要时请手动开启。
- **已有的 `.env` 不会被改动**。如果你的 `.env` 是从旧版复制的，建议检查：
  - `LOG_LEVEL=DEBUG` 建议调整为 `INFO`；
  - `SCHEDULED_RESTART_ENABLED` 建议关闭为 `false`（CDP 会话回收已取代定时重启）；
  - 若显式设置了 `CMD_PERIODIC_KEEPALIVE_ENABLED=true`，保活会周期性唤醒标签页，后台空闲冻结将受影响。
- **面板内存指标口径调整**：默认采用轻量采样（`PANEL_MEMORY_METRIC=fast`），与 2.10.0 的 USS 数字口径不同，不建议跨版本数值直接比对。
- **回退开关**：Layer 0 性能重构的所有新特性均提供独立回退开关（如 `STREAM_PAGE_SNAPSHOT_ENABLED`、`CDP_HYGIENE_ENABLED`、`BROWSER_IDLE_FREEZE_ENABLED`、`BROWSER_CDP_RECYCLE_ENABLED` 等，遇到兼容性问题可逐项回退）。

### ✨ Added

- **流式监控单次页面快照**（`app/core/stream_snapshot.py`）：把“选回复元素 + 判断是否仍在生成 + 深度提取文本/图片”合并为一个页面内执行函数，单次 CDP 往返即可获取整张页面快照并以 JSON 字符串返回，彻底消除 RemoteObject 泄露；带自动回退与兜底机制。
- **CDP 对象卫生与缓冲保护**（`app/core/cdp_hygiene.py`）：
  - 自动释放非节点 JS 执行结果（`Runtime.releaseObject`），杜绝游离 DOM 节点常驻渲染进程；
  - `Network.enable` 自动施加内存缓冲保护（默认总计 32MB、单个资源 16MB）；
  - 热点路径（流式监控、Arena 监听、Kimi 抓取等）全面改造为返回纯文本/JSON 字符串。
- **空闲标签页 CDP 会话轻量回收**（`app/core/tab_pool_parts/idle_maintenance.py`）：标签页空闲达到阈值时静默断开并重建 CDP 连接，一次性释放元素查找留下的 nodeId/objectId 及其钉住的旧 DOM，无需重启浏览器或刷新页面，不影响用户登录态。
- **不可见后台标签页自动冻结**：空闲且处于不可见/最小化状态的后台标签页通过 `Page.setWebLifecycleState=frozen` 冻结，暂停动画帧与计时器；被请求占用时 3~7ms 极速恢复，用户正在查看的前台标签页永不冻结。
- **站点适配器分文件存储**：每个站点一个 `config/sites/<站点>.json`，另有 `_global.json` 和 `index.json` 清单。每个站点文件带 `adapter_version`、`min_app_version` 等元数据，并用 JSON Schema v1 校验；无法解析的站点会被安全跳过，删除的站点移入 `config/sites/.trash/`。
- **站点适配器在线更新**：新增 `GET /api/adapters/updates` 与 `POST /api/adapters/updates/apply`，支持无缝热更新单站点规则配置。
- **站点健康巡检与选择器稳健度分析**：新增 `GET /api/adapters/health`（只读 DOM 探测评级）与 `GET /api/adapters/lint`（静态选择器脆弱度分析与优化建议）。
- **控制面板「维护与巡检」面板**：在站点配置页集中提供健康巡检、选择器稳健度检查和适配器在线更新操作。
- **Prometheus 指标端点**：新增 `GET /metrics`，原生提供 HTTP 请求统计、耗时直方图、并发数、协议指标与标签页池状态。仅限本机直连或携带有效令牌访问。
- **`X-Request-ID` 全链路追踪**：透传或自动生成请求唯一 ID，打通响应头、日志与请求历史记录。
- **实验性多进程模式**：支持 `UWAPI_WORKER_MODE=process`，API 接入进程与浏览器 Worker 进程物理隔离，Worker 异常退出后指数退避自动拉起。
- **环境变量登记表**（`app/core/settings_registry.py`）：集中管控 160 项环境变量规格与安全脱敏规则，自动同步 `.env.example`。
- 新增配置项：`SITES_CONFIG_DIR`、`RUNTIME_DB_PATH`、`ALLOW_INSECURE_PUBLIC_BIND`、`MEDIA_REQUIRE_AUTH`、`MEDIA_TRANSCODE_MAX_CONCURRENCY`、`MEDIA_TRANSCODE_MAX_SOURCE_MB`、`MEDIA_TRANSCODE_QUEUE_TIMEOUT_SEC`、`RESPONSES_STATE_MAX_ENTRY_MB`、`RESPONSES_STATE_MAX_TOTAL_MB`、`TAB_ACQUIRE_MAX_WAITERS`、`RESTART_PROXY_MAX_CONNECTIONS`、`RESTART_PROXY_MAX_BUFFER_MB`、`UWAPI_WORKER_MODE`、`UWAPI_WORKER_PORT`、`STREAM_PAGE_SNAPSHOT_ENABLED`、`CDP_HYGIENE_ENABLED`、`BROWSER_CDP_RECYCLE_*`、`BROWSER_IDLE_FREEZE_*`。

### ⚡ Changed

- **工作流交互与发送性能极致优化**：
  - **发送按钮稳定性等待快速通行（Fast-Path）**：当目标元素已处于可交互状态且位置稳定时，在 50ms 内直接放行点击，彻底消除长文本或新会话后无意义等满 5.5 秒上限的死循环；仅在节点脱落或不可交互时按需触发底层刷新。
  - **复合选择器直查与超时平衡**：`ElementFinder` 在查找发送按钮与分组 CSS 时优先使用完整选择器直接查询，消除拆分探测中极短超时导致的 CDP 假超时，并将拆分超时区间放宽至 0.3s~0.6s；真实页面查找耗时从 7.8 秒大幅缩减至 0.28 秒，彻底避免误跌入无效的回退选择器遍历。
- **请求历史与统计全面 SQLite 化**：从全量重写 JSON 改为增量写入 SQLite（WAL 模式），高频请求下磁盘 I/O 开销骤降。
- **LMArena 增量解析**：长回答按行流式增量解析并缓存，解析耗时从秒级骤降至毫秒级（约提升 120 倍）。
- **全局网络监听按资源类型过滤**：默认仅监听 XHR、Fetch、EventSource、Document 等关键交互，跳过无用媒体流；`patch_drissionpage.py` 分块响应改用追加列表，彻底消除 O(n²) 字符串重拼。
- **更新器按站点合并配置**：升级时根据摘要判断改动状态，保留用户定制并自动补充新版配置项。
- **控制面板前端优化**：剔除浏览器端实时编译的 407KB Tailwind 运行时，改用预编译轻量样式表（约 90KB）；`dashboard-methods.js` 拆分为 `static/js/dashboard/` 下的多个 ES 模块。
- **内部核心架构解耦与重构**：
  - 浏览器操作全部收敛至统一抽象层 `BrowserDriver`，业务逻辑与驱动解耦。
  - OpenAI Chat Completions、Responses 和 Anthropic Messages 三种协议统一走 `ChatJob` / `ChatEvent` 执行管线。
  - `TabPoolManager`、`CommandEngine`、`ConfigEngine` 职责模块化拆分。
- **平滑重启代理与等待队列加固**：增加并发连接数、缓冲上限与队列长度限制，过载时快速返回 503 重试响应；支持 chunked 与 100-continue。
- **`/v1/responses` 状态保护**：限制续接历史单条与总量内存占用，并按客户端凭据物理隔离。
- **日志与监控开销降低**：日志级别默认调整为 `INFO`；可爱风格日志采用延迟展示翻译与 LRU 缓存；长 prompt 清洗性能显著提升。
- `.env.example` 采用安全默认值，移除所有明文密钥占位符，已有 `.env` 保持不变。

### 🐛 Fixed

- 修复 `DrissionListener` 缺少 `_reuse_driver` 属性导致网络监听启动失败报错的问题（统一属性规范为 `reuse_driver` 并提供兼容别名与容错）。
- 修复部分站点（如 ChatGLM 智谱清言）发送按钮因网页改版导致原选择器失效的问题，更新选择器配置并增强通用容器回退；当核心选择器找不到时，在控制台输出醒目提示并提供本地选择器配置教程跳转链接。
- 修复 `arena.ai`（LMArena）因网页端升级排版样式（`text-left` 变为 `text-start`）及侧边栏路径升级导致直连模式与新建对话选择器失效的问题：
  - 更新直连模式模型切换按钮（`model_select_btn`）为兼容 `text-start`、`text-left` 及基础截断类的稳健复合选择器；
  - 升级 `new_chat_btn` 全面兼容新版 `/text` 与 `/text/direct` 侧边栏路径；
  - 补全多模态上传选择器（`upload_btn`: `button[aria-label="Add files"]`、`file_input`: `input[type="file"]`、`drop_zone`）与 `attachments` 传输声明；
  - 针对 Arena 不支持 `.txt` 附件的特性，将超长文本转文件上传格式适配为页面原生接受的 `pdf` 格式；
  - 完善对双栏竞技（Battle / Side-by-side）模式的端到端客户端请求支持与左侧模型流式解析（`lmarena_battle_side_left`）。
- 修复 `chatgpt.com`（ChatGPT）因界面改版导致原 ID 选择器 `#prompt-textarea` 脱靶失效的问题，升级支持 ProseMirror 富文本容器 `div.ProseMirror[contenteditable="true"], div[role="textbox"][contenteditable="true"]`；同步兼容新版动态发送按钮 `button[type="submit"], button[aria-label*="发送"], button[aria-label*="Send"]`。
- 修复 `gemini.google.com`（Gemini）新建对话在侧边栏折叠时选择器定位失败与图片上传错误问题：
  - 将 `new_chat_btn` 更新为精确定位顶栏可见图标（`css:a[href="/app"][aria-label*="发起新对话"], gem-icon-button a[aria-label*="发起新对话"], a.mdc-icon-button[aria-label*="发起新对话"], [data-test-id="side-nav-sparkle-button"]`）；
  - 优化 Gemini 多模态附件传输机制，将原生 CDP 拖放（`cdp_drop`）置为首选，彻底解决富文本剪贴板粘贴引发的 `gem-attachment-loading-error`。
- 修复 `grok.com`（Grok）附件配置缺失与流式监控误判问题：
  - 补全 `grok.com.json` 附件选择器（`file_input`、`upload_btn`、`drop_zone`）与 `attachments` 配置；
  - 修复 `StreamMonitor` 中文界面停止指示器缺失，新增 `css:button[aria-label*="停止"]`、`css:button[aria-label*="暂停"]` 识别；
  - 修复流式回复初期单字或超短输出被误判为未开始而进入硬超时退出的判定缺陷。
- 修复 `claude.ai`（Claude）因网页改版导致原回复类名 `.font-claude-response` 缺失引发提取失败的问题，升级回复容器选择器为 `div[data-testid="assistant-message"] .standard-markdown, div[data-testid="assistant-message"]`，并将流式监控模式升级为稳健的 DOM 提取模式。
- 修复 `www.doubao.com`（豆包）因网页端底层从原生 textarea 迁移至 Tiptap 富文本框架导致输入框选择器失效的问题，升级适配 `div[data-testid="chat_input_input"] div.tiptap, [data-testid="chat_input_input"] [contenteditable="true"]`。
- 优化 `chat.qwen.ai`（通义千问）多模态附件传输机制，移除无效的全局 body 拖拽配置，对齐原生剪贴板通道与附件预览选择器（`.file-card-list, img.vision-item-image`）。
- **全站点端到端实测验证**：通过 HTTP 客户端真实请求对已登录站点（Claude、AI Studio、ChatGPT、Gemini、Grok、Arena）完成覆盖「文本流式生成」、「多模态图片识别」、「长文本上下文处理」的严密实测，全站点三项测试全部 100% 满分通过，服务运行日志零错误（0 ERROR / 0 CRITICAL）。
- 修复 LMArena 流式输出多字节字符偶发乱码问题（快照截断多字节字符导致整段修复失败），改用按行增量解析保证完整性。
- 修复 Python 3.10 / 3.11 上无法启动的问题：消除嵌套引号 f-string 与 `NotRequired` 导入错误。
- 修复页面内工作流编辑器把"保存工作流"和"测试运行"请求硬编码发到 `9099` 端口导致保存失败的问题，现在动态使用服务实际监听的 `APP_PORT`。
- 修复空闲标签页回收 CDP 会话后，命令通过 `run_js_file` 注册的新文档注入脚本（含启动脚本）失效的问题；回收状态改为严格校验，避免残留失效文档根引发 `ElementLostError`。
- 修复空闲冻结的标签页解冻失败时仍被分配给请求的问题，增加状态确认与冷却撤销机制。
- 修复命令配置读取失败时误清空运行中的命令缓存和预注入脚本的问题，现在会保留上一次读取成功的配置。
- 修复工作流外部脚本在同一秒内被原子替换后缓存未及时刷新的问题（引入纳秒级元数据校验）。
- 修复环境变量值为 `inf`、`nan` 时触发 `OverflowError` 的问题，现在会回退到默认值。
- 修复旧版顶层为数组的请求历史文件无法加载，以及 `max_records=0` 时切片结果错误的问题。
- 修复访问搜索引擎主域名时误触发站点自动发现的问题。
- 修复网络事件 URL 正则在复杂表达式下可能长时间阻塞的问题（增加单次匹配超时限制）。
- 修复控制面板导入站点配置或完整备份在超大文件下无提示的问题，施加文件大小安全上限。
- 修复 `NETWORK_DEBUG_CAPTURE_ENABLED` 写成字符串 `"false"` 时反而开启响应调试抓取的问题。
- 修复 `.env.example` 与代码中 `SCHEDULED_RESTART_ENABLED` 默认值不一致的问题。

### 🔒 Security

- 新增服务端 `Origin` 校验，拦截来源不在 `CORS_ORIGINS` 里的跨源写请求。
- 完整备份导出默认去掉 `.env` 中的令牌、密钥和代理凭据。导入时会跳过这些脱敏占位值，保留目标机器上现有的密钥。备份导入/导出接口强制要求鉴权。
- `open-profile-url` 等高敏感管理接口只允许控制面板令牌访问，或未经代理转发的本机直连，并限制可打开的目标地址。
- 抓取远端资源时统一拦截内网和保留地址（防 SSRF），流式限制字节数和像素数，并把连接固定到已校验的 IP（DNS pinning）。图片比对和 C2PA 读取同样适用。
- 媒体落盘使用扩展名白名单并嗅探文件内容，拒绝保存 SVG、HTML 等主动内容。媒体响应带上 `Content-Security-Policy: sandbox` 和 `X-Content-Type-Options: nosniff`。可选开启 `MEDIA_REQUIRE_AUTH` 要求鉴权。音频转码增加并发、排队和源文件大小限制。
- 修复教程页搜索结果和目录用 `innerHTML` 拼接时的反射型 XSS。
- 运行时解析器安装默认关闭，并静态检查导入期副作用、限制模块路径。响应调试抓取默认关闭，开启时会告警，快照到期自动清理。

### 🗑️ Removed

- 不再分发单文件 `config/sites.json`（已由 `config/sites/` 目录取代，旧文件会自动迁移归档）。
- 移除浏览器端 Tailwind 运行时脚本 `static/vendor/tailwindcss.js`。
