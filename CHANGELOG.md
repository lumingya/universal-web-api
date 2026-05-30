# 更新日志

## 2026-05-30

change:
- Python 脚本安全沙箱限制：高级 Python 脚本默认受限执行，AST 拦截危险操作（如 `open`、`eval`、`exec`、`os`、`subprocess`、`sys` 等及 dunder 逃逸路径），仅保留 `json`、`time`、`requests`、`urllib.parse` 白名单。支持通过环境变量 `CMD_ALLOW_UNSAFE_PYTHON_COMMANDS=true` 恢复未受限执行行为。
- 文件追加写入路径边界限制：`append_file` 默认只允许写入至 `data/command_outputs` 目录下，并引入路径边界检查，防止通过 `../` 或绝对路径进行目录遍历逃逸。可通过 `CMD_APPEND_FILE_BASE_DIR` 自定义安全目录。
- 日志脱敏与安全强化：新增统一脱敏函数，自动对 `Authorization`、`Cookie`、`token`、`password`、`secret` 等敏感项进行遮蔽；网络解析 debug 快照写盘前同样会对 `raw_body`、`URL`、`content_preview`、`parser_debug`、`error` 进行脱敏。
- 附件监控安全混淆：废除固定明文的 `window.__ATTACHMENT_MONITOR__` 主入口，改用每个执行器随机生成的非枚举 window key，并在新版本注入时清理旧入口。
- 发送重试冷却限制：发送新增 `retry_cooldown_window` 机制，默认冷却时间为 `1.5s`，避免页面慢清空或慢进入生成态时因二次点击导致重复发送；前端配置面板同步新增“最小冷却窗”输入项。
- 日志拆分与格式优化：拆分文件/控制台/Web 的日志格式化，文件日志不再套用控制台前缀并修复双重时间戳；控制台和 Web 端支持多行缩进和超长展示截断。
- 线程安全 Logger 单例：为 `get_logger()` 加了线程安全单例注册表，避免并发场景下重复构造 `SecureLogger` 以及 `handlers.clear()` 的竞争风险。

fix:
- Claude / OpenAI 工具调用兼容性修复：修复流式 `tool_calls.index` 处理、Anthropic 增量工具流及 SSE 分包缓冲问题。保障 `tool_result` 顺序无误、孤儿回退与图片多模态数据不丢失，并在失败降级时仍正常保留 `tool_calls` 闭合协议。
- XML 与路径解析修复：提升工具解析对 XML 缺失 wrapper 标签的自愈能力，支持直子参数（如 `<invoke><path>...</path></invoke>`）解析，保障 schema string 精度保真，并修复 Windows 路径下的反斜杠解析问题。
- 标签页并发与调度优化：优化全局网络监听交接等待逻辑；残留 worker 超时后正常标记 tab 状态为 `ERROR`，且命令恢复能正常交回调度器，避免 `ERROR` tab 延迟释放并被错误改回 `IDLE`。
- 并发轮询与性能提升：异步命令使用 `ThreadPoolExecutor` 限制线程数（默认上限 20，支持使用 `CMD_ASYNC_MAX_WORKERS` 调整）；等待标签页时优先使用 TabPool condition 唤醒，消除了 50ms 空转轮询；退出应用时同步执行命令引擎的 shutdown。
- 内存与资源释放保障：在工作流结束和可视化编辑器测试结束时显式清理附件监控，降低常驻 Tab 残留 Observer / DOM 引用的内存泄漏风险。
- 多模态提取机制优化：完善音频/WebSocket payload 上限、Blob 流式限量读取限制；Canvas 默认使用 JPEG 格式并允许调整质量；补充录音/浏览器 TTS 的熔断限制，并在网络流未完成（not done）时降级 fallback；支持图片盲等配置。
- 前端旧版配置兼容：优化前端配置转换，兼容旧的 `modalities: { image: true, audio: true }` 配置并正确映射为新的策略对象，避免在 UI 中被误判为 disabled。

## 2026-05-28

new:
- 新增 `/v1/responses` 兼容入口，允许只支持 Responses wire API 的 Codex 直接接入当前项目。

change:
- Responses 请求现在会复用现有 `/v1/chat/completions` 执行链，并把 `input`、`instructions`、`tools`、`tool_choice`、`text.format` 等字段转换为现有聊天请求格式。
- Responses 流式模式改为输出 `response.created`、`response.output_item.added`、`response.output_text.delta`、`response.function_call_arguments.done`、`response.completed` 等 SSE 事件，方便 Codex 按 Responses 协议消费。
- Responses 工具调用流补充 `response.function_call_arguments.delta`、`response_id` 和 in-progress 到 done 的事件过渡，工具调用历史里的 `function_call_output` 也会更准确地转回现有工具结果消息。
- `/v1/models` 现在会同时兼容 OpenAI 和 Anthropic/Claude Code 风格：接受 `Authorization` 或 `x-api-key` 认证，并在携带 `anthropic-version` 头时返回 Anthropic 风格模型列表。
- Claude/Anthropic 兼容层现在会把非流式错误转换为 Anthropic 风格错误体，并为 `/v1/messages`、`/v1/messages/count_tokens` 与流式响应补充 `request-id` 头，便于 Claude Code 网关诊断。

fix:
- 修复项目仅暴露 `/v1/chat/completions` 时无法直接作为 Codex provider 使用的问题。

## 2026-05-23

fix:
- 工具调用校验模块补全参数长度限制辅助函数导入，修复 `_get_max_tool_argument_chars` 缺失导致的 `tool_calling_failed`。
- 工具调用 XML 解析模块补全 adapter / legacy 标签常量导入，修复 `_PREFERRED_XML_WRAPPER_TAG` 等缺失名称导致的运行时崩溃。
- 工具调用提示词模块补全 `Tuple` 类型导入，避免运行时解析类型注解时触发 `NameError`。
- 工具调用 JSON 解析与校验逻辑现在会正确接受 `{"content":"...","tool_calls":[]}` 这类结构化最终回复，不再误判为畸形工具载荷并进入多轮内部重试。

## 2026-05-22

fix:
- 自动更新恢复默认 TLS 证书链与主机名校验，移除宽松 SSL 下载逻辑，降低更新链路被中间人劫持的风险。
- 自动更新在合并 `sites.json` / `commands.json` 时，配置解析失败不再按空配置继续覆盖，避免异常情况下清空本地数据。
- 自动更新前新增项目快照备份与失败回滚，覆盖核心代码与静态资源，降低解压或写入中断导致的不可恢复损坏。
- 标签页池的全局网络监听停止流程避开持锁等待，减少监听线程退出时拖住整个池管理器的卡死风险。
- 标签页池为会话增加最后已知 URL 缓存，后台健康检查与孤立上下文探测在 `BUSY` 状态下不再并发读取 `tab.url`，降低 CDP / WebSocket 冲突概率。
- 过期的孤立浏览器上下文现在会在宽限期后主动释放，不再只从 orphan 记录中移除，减少 Chrome 后台上下文泄漏。
- DOM 模式下移除与前台轮询并发的 event-only 网络监听线程，避免同一 DrissionPage/CDP 连接被多线程同时使用。
- 插件市场路由把同步阻塞调用切到 FastAPI 线程池，减少事件循环被同步网络 I/O 卡住的问题。
- 工具调用解析与校验补全缺失导入，修复 `html.unescape`、`math.isfinite`、`math.isclose` 及参数修复/深度限制路径上的运行时崩溃。

## 2026-05-21

new:
- 函数调用新增两个可选开关：预填充乱序零宽、注入预填充/尾部提示词。
- 函数调用的额外预填充与尾部提示词支持随机乱序和零宽字符注入。

change:
- 设置面板和环境配置 schema 已同步新增上述两个开关。
- 函数调用提示词拼装逻辑已按开关拆分，关闭注入后只保留重试策略提示词。
- 教程页补充了这两个开关的说明。

fix:
- 刷新前端脚本版本号，避免浏览器继续加载旧版设置页。
