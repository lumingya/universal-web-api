# 更新日志

## 2026-06-09

fix:
- OpenAI 流式响应在收到 `[DONE]` 后会立即结束外层监听并快速清理 worker，不再继续等待尾部结束标记；对应 chat 与 tab 路由均避免了 worker 卡住时的尾部延迟。
- 请求监控历史修复空响应误判：`stop_sequence` / `stream_done` 这类正常结束不再被记录成失败，同时流式响应文本捕获改用累计字符数，避免长输出下反复扫描分片列表。
- 请求监控分站点成功率统计会把路由别名归并到站点主域名，例如 `gemini.com` 归入 `gemini.google.com`，避免前端模型/路由别名被误显示为独立站点。
- 标签页路由匹配改用缓存快照和锁外刷新，减少域名/URL 路由选择时对 live tab URL 的重复读取和锁内阻塞。
- 标签页默认路由选择从完整排序候选池改为直接取最小 `persistent_index`，保留 idle 优先语义并减少大池候选选择开销。
- 配置页、日志轮询和请求历史轮询补齐过期响应保护与并发防护，避免慢请求覆盖当前站点/预设、后台轮询堆积或静默刷新错误打扰前台 UI。
- Dashboard 全量配置加载增加请求世代保护，初始化、刷新状态、保存 token 或导入后并发触发 `/api/config` 时，旧响应/旧失败不再覆盖当前 `sites/currentDomain` 或提前关闭最新 loading。
- Request Monitor 已加载详情的历史记录在后续轮询合并时不再用旧缓存覆盖后端新鲜的状态、耗时、摘要和 token 字段，只保留详情文本/载荷缓存。
- Request Monitor 派生 view 的长文本签名改为覆盖完整字符串的轻量哈希，避免详情文本长度和首尾相同但中间内容变化时复用旧缓存导致抽屉显示 stale 内容。
- 请求历史 revision 纳入状态、摘要、错误、token 和详情文本轻量指纹，late enrichment 更新响应/错误内容后会正确让 `if_revision` 失效；`include_detail=true` 也不再被列表 revision 短路为空结果。
- 请求历史从旧文件或外部写入读到无序记录时，会先对完整历史兜底排序再按 `limit` / 200 条上限截取，避免启动加载或 `limit=1` 这类小窗口把旧记录误当最新记录返回给 Request Monitor。
- Commands 面板命令列表加载增加请求世代保护，保存/删除/启停/手动刷新并发触发时旧响应不再覆盖新列表，也不会提前关闭最新请求的 loading 状态。
- 日志清空会推进日志轮询 generation 并重置本地 seq/timestamp，清空前已在途的旧 `pollLogs` 响应返回后会被丢弃，不再把旧日志拼回空列表。
- 日志清空协议增加 `cleared` 标记和 clear epoch，其他已打开页面使用旧 `after_seq` 轮询时会同步清空本地旧日志，避免多页面下清空操作只影响当前页面。
- 日志脱敏会在大文本预检早退前先遮蔽裸 `sk-` API key、JWT 与 URL 基础认证凭据，避免长普通日志中夹带独立 token 时被预检跳过。
- 文件日志轮转在 Windows 上遇到 `app.log` 被其他线程/进程短暂占用时，会延后重试并继续写入当前日志文件；即使轮转异常从 `emit/doRollover` 进入 logging 的默认 `handleError` 路径，也不再把 `PermissionError: [WinError 32]` traceback 刷到控制台。
- 共享 JSON 原子写入、`.env` 原子写入和系统配置回滚恢复在 Windows 上遇到短暂文件占用时会退避重试，降低杀毒/编辑器占用导致保存或备份导入失败的概率；持续失败仍保留旧文件并清理临时文件。
- 图片预设运行中 reload 遇到 JSON 损坏、顶层格式错误或读取异常时会保留当前内存中的有效预设，不再把一次临时坏写降级为内置默认预设。
- ConfigTab 初始化/切换预设时不再为了补齐 `file_paste` / `prompt_padding` 直接写入只读或冻结的预设对象；可写配置仍会原位补齐，冻结对象则使用本地可变草稿兜底，避免 watcher 崩溃导致配置页不可用。
- DOM 图片检测只统计有效、归一化、去重后的远程图片 URL，空 `src`、`data:`、`blob:` 与重复图片不再误触发图片模式等待和后台预取。
- 后台图片预下载增加 URL 规范化、10MB 默认大小上限和终态前连接关闭，避免大小写 URL 重复缓存、大图预取拖垮磁盘/网络，以及失败终态早于资源释放。
- 后台图片下载器 shutdown 会立即唤醒 queued / downloading 的等待者并返回 `downloader_shutdown`，不再让收尾路径白等到超时；worker 收到关闭信号后仍会继续清理 `.part` 临时文件，避免关闭竞态留下半截文件。
- 可视化工作流编辑器销毁时会同步移除拖拽和菜单相关的全局 document 监听，避免重复注入/销毁后残留闭包和额外主线程回调。
- 命令引擎支持通过 `CMD_ENGINE_AUTO_START=false` 禁用 import 时自动启动周期线程，测试环境默认关闭，修复 pytest 结束后后台线程向已关闭日志流写入导致的 logging error。
- 受跟踪阻塞 worker 在请求取消后若稍后自然结束，会自动清理 `worker_state` 中的线程和标签，避免陈旧线程引用误导后续清理、诊断或标签页退休逻辑。
- 手动命令测试与命令组执行的收尾路径拆分为请求完成记录和标签页释放两个独立阶段，避免请求历史/统计收尾异常阻止标签页释放。
- 手动命令测试在空闲标签快照通过 scope 预判后，acquire 到实际标签页时会再次复查 scope，避免快照过期或标签页状态切换后误执行命令。
- 浏览器后端实际只产出单路 choice 的流式请求，即使客户端传入 `n > 1`，命中 stop sequence 后也会立即标记停止并发送终止帧，避免外层继续等待上游自然结束。
- Anthropic Messages 的 `stop_sequences` 会同步下传到内部 OpenAI 兼容请求，并在外层命中 stop 时关闭底层 SSE body iterator，避免 Anthropic 包装层截断后内部 worker/标签页继续生成到自然结束。
- chat 与 tab 普通流式路径在 worker 通过队列返回错误时，也会像异常捕获路径一样发送 error chunk 后补发 `[DONE]`；tab 固定标签、域名路由和精确 URL 路由还会把该分支标记为已终止，清理阶段走快速 worker join，不再误等 5 秒慢清理。
- chat 与 tab 非流式聚合会在内部流结束后 flush 未以空行终止的 SSE 尾帧，避免上游最后一个 split/unterminated delta 被遗漏导致最终文本为空或缺尾。
- ChatGPT 解析器新增 SSE 事件 pending buffer，JSON delta 或 `[DONE]` 被网络分片切开时会等完整事件后再解析；尾部缺少空行的 `[DONE]` 也会被接受，不再丢失跨 chunk 的文本或结束标记。
- Tool Calling 非流式响应解码会识别带前导空白、注释 keepalive 或 `event:` 行的 SSE 包装，并忽略带 padding 的 `[DONE]`，避免标准 SSE 包装响应被误当作裸 JSON 导致工具调用失败。
- NetworkMonitor 命中目标流 4xx/5xx 状态后会在初始 body 探测等待前直接终止工作流，403/422 等空响应体错误不再被延迟或误判为目标流正文未就绪。
- NetworkMonitor 全局硬超时不再 `break` 后伪装成正常完成；超时会抛出 `NetworkMonitorTimeout` 交给上层回退/错误路径，最终日志也会按 done、流捕获完成、静默或取消分别记录真实结束原因。
- Gemini 网络解析遇到前端渲染提示词产生的 `<render>` 输出包装尚未闭合时，会标记为 render 输出未闭合；NetworkMonitor 在后续静默时会回退 DOM 补齐，不再把半截 `<render template="...">`、提示词内标签或重复的局部文本当成成功响应写入请求历史。
- tab 路由音频媒体 fast-return 清理会在保持请求 `completed` 的同时显式标记 worker 停止原因，历史归一化也会把 `audio_media_fast_return` 视为正常完成，避免空文本音频响应被误判为 `empty_response`。
- 命令 CRUD 保存不再使用已合并运行态统计的命令列表，避免 `last_triggered` / `trigger_count` 随编辑、分组、启停、排序等操作被写回命令配置。
- 命令创建、重排、更新和删除在底层保存失败时会返回 500 `命令保存失败`，不再把创建失败包装成 `command: null` 成功响应，也不再把已有命令的保存失败误报为 404。
- 预设重命名在配置保存失败时会回滚内存中的预设列表、站点默认预设和本地默认预设覆盖，避免返回失败后当前进程继续读到未落盘的新预设名。
- 默认预设切换在本地覆盖保存失败时会回滚站点默认预设和 `_local_default_presets`，避免保存失败后 UI/API 读到未持久化的默认预设。
- `/api/config` 全量站点保存如果落盘失败或保存过程抛错，会回滚 `config_engine.sites`、全局/本地默认预设映射和本地覆盖 payload，避免 API 返回 500 后当前进程继续显示未落盘的新配置。
- 可视化 Workflow 编辑器保存 `workflow/selectors` 时，如果配置落盘失败或保存过程抛错，会恢复内存中的旧预设配置，避免 API 返回失败但当前进程继续使用未持久化的新工作流。
- 站点配置读取自动补全缺失默认字段时，如果配置保存失败会回滚内存中的预设配置，仅向当前请求返回补全副本；未知站点 AI/回退配置保存失败也会明确记录警告，不再误报已保存。
- 配置保存顺序调整为先确认 `sites.local.json` 本地覆盖写入成功，再原子替换主 `sites.json`，避免本地覆盖保存失败时 API 返回失败但主配置已经落盘的部分提交状态。
- 站点配置保存若主 `sites.json` 替换失败，或本地覆盖保存函数报告失败但已写入 `sites.local.json`，会恢复保存前的本地覆盖文件和内存 payload/default preset 映射，避免返回失败后重启仍应用部分默认预设覆盖。
- 命令配置保存顺序调整为先确认 `commands.local.json` 本地状态覆盖写入成功，再替换主 `commands.json`，避免命令 CRUD / 分组操作返回失败但主命令配置已经落盘。
- 命令配置保存若主 `commands.json` 替换失败，或本地状态保存函数报告失败但已写入 `commands.local.json`，会恢复保存前的本地状态文件，避免返回失败后重启仍应用部分启用/分组状态。
- 站点和命令配置保存若无法读取本地覆盖/状态文件的回滚快照，会直接中止保存，不再在缺少可靠回滚点时继续写入主配置或本地覆盖文件。
- 站点和命令配置主文件或本地覆盖/状态文件原子替换成功后，即使后续 mtime 元数据刷新失败也会视为保存已提交并保留写入结果，不再把已落盘成功误报为失败并触发内存或本地覆盖回滚。
- 站点和命令配置回滚恢复本地覆盖/状态文件后，即使 mtime 刷新失败也会继续恢复内存中的本地 payload/default preset 或保留已恢复文件，避免回滚文件已成功但运行态仍停留在失败写入后的状态。
- 运行时解析器 `parsers.json` 和提取器 `extractors.json` 原子替换成功后，即使 mtime 元数据刷新失败也会视为保存已提交，避免安装解析器、设置默认提取器或导入提取器配置把已落盘结果误报为失败。
- 运行时解析器安装器保存 `parsers.json`、解析器源码以及失败回滚恢复旧源码时改用同目录临时文件、fsync 与原子替换，避免安装/覆盖失败时损坏旧配置或留下半截 parser 文件。
- 提取器配置保存改用共享原子 JSON 写入；默认提取器切换和提取器配置导入在保存失败时会回滚内存状态并返回失败，API 也会把保存失败明确报为 500，不再误报成功或把磁盘错误误导成“提取器不存在”。
- 配置市场补齐缺失的 `AppConfig` 市场配置 getter，恢复本地市场/远程市场 API 路径可用；本地投稿清单与公共索引缓存写入改用共享原子 JSON 写入，避免投稿或缓存刷新中断时把旧市场文件截断成半写 JSON；缓存时间戳改用 timezone-aware UTC 写法，消除 Python 3.13 弃用警告。
- TabPool 浏览器配置写入复用共享原子 JSON 写入 helper，保持同目录临时文件、fsync 和替换行为一致，避免后续维护出现半写配置或临时文件清理差异。
- 运行时解析器和多模态提取器生成 UTC 时间戳时改用 timezone-aware 写法，保持 `Z` 后缀语义并消除 Python 3.13 `datetime.utcnow()` 弃用警告。
- 完整配置备份导入会先预校验所有待导入 section，再开始写入任何配置文件；如果后续 section 格式无效，会在落盘前直接返回 400，避免导入失败却已经部分覆盖 `sites` / `commands` 等配置。
- 完整配置备份导入在写入后续配置文件失败时会按导入前快照回滚已经写入的文件，并把 `BrowserConstants.reload()`、配置重载和命令刷新延后到全部写入成功后执行，避免返回 500 后留下半导入或运行态/磁盘不一致状态。
- 更新保留白名单 `config/update_settings.json` 保存改用轻量原子 JSON 写入，避免保存中断或替换失败时截断已有更新保留配置，同时不让 `updater.py` 导入该模块时加载完整应用配置。
- 命令引擎批量启停相关注释中的 mojibake 已修复为正常中文，避免后续维护和审计时误读命令组/启用状态逻辑。
- 配置引擎文件清理尾随空白，恢复 `git diff --check` 验证干净输出，减少后续补丁审阅噪音。
- 精确 URL 聊天路由会把 API 层已解析的标签编号传入执行层，执行前校验该标签仍匹配 URL；若标签已跳转会明确失败，不再二次 URL 轮询选中另一标签导致响应头/历史记录与实际执行标签错配。
- RequestManager 状态统计、运行请求列表、僵尸清理、完成日志和请求历史记录改用 `RequestContext` 一致快照读取，避免 status/tab_id/时间字段在并发结束或取消时被无锁组合读取成不一致状态。
- RequestManager 取消请求时会立即写入/刷新请求历史，debug force-release、命令中断或卡死 worker 不再必须等执行协程回到 finally 才能在 Request Monitor 看到取消记录；后续正常 finish 到达时仍会替换同一条历史记录。
- RequestManager 旧请求清理不再在回收到足够历史容量后提前停止扫描，同一批里的后续超时 queued / running 请求也会继续标记失败并写入历史，避免僵尸请求残留占位。
- Dashboard token 保存、清除、备份恢复和跨标签页变更会同步刷新响应式 token 状态，Sidebar 认证提示不再依赖整页重载才更新。
- 网络错误触发命令的扫描路径不再为了寻找首个匹配事件而 deep-copy 整个标签页网络事件队列，只在最终注入 interrupt context 时复制命中事件，降低高频网络错误下的 CPU 与内存抖动。
- 命令结果事件的布尔匹配检查不再复制命中事件，调度阶段也避免重复复制同一事件；事件注入 interrupt context 时仍保持隔离副本，减少条件触发轮询中的无用深拷贝。
- 命令引擎工作流插队队列的写入、取出、去重和清空改为短锁保护，并会跳过畸形 pending 项，避免并发触发命令时 `pending_interrupts` / `pending_interrupt_ids` 状态不同步或脏数据打断工作流恢复。
- chat、固定标签、域名路由和精确 URL 路由的流式绝对超时分支会发送 error 后补发 `[DONE]`，避免 OpenAI SSE 客户端在超时错误后继续等待终止帧。
- Commands 编辑器加载执行预设选项时增加请求世代、编辑命令引用和域名校验，快速切换命令/域名后旧 `/api/presets/*` 响应不再覆盖当前预设列表或重置当前命令 action 的 `preset_name`。
- 增量 parser 处理完整 raw body 快照时会校验新快照是否仍为上一快照的前缀延续；遇到同长度替换或更长但非前缀的新响应会重置解析状态并按新快照全量解析，避免复用 parser 时丢掉新响应正文或只解析尾巴。

change:
- 请求统计与历史落盘改为合并调度，每类最多一个后台保存 worker，减少高并发请求结束时的短线程风暴；长时间未启动的 `QUEUED` 请求也会按 TTL 清理并写入历史。
- 请求历史追加从“每次追加后全量排序再截断”改为按现有排序键二分插入并裁剪上限，减少高频请求完成时的重复排序开销。
- 请求历史读取复用已维护的有序列表，只有检测到历史记录被外部打乱时才兜底排序，降低请求监控轮询和详情查询的重复排序成本。
- Tool Calling JSON 候选提取与修复路径改为更线性的扫描方式，降低大文本/多候选解析时的重复切片和异常重试开销。
- Request Monitor 的摘要统计减少重复计算，日志页在非前台时降低轮询频率，并保留前台实时性。
- 配置页高级配置保存引入保存序号、请求超时和上下文快照；过期请求会静默丢弃，不再回滚或关闭当前操作的 loading 状态。
- 手动命令测试不再按标签页数量裸开线程，改为复用命令引擎受限后台执行器，避免大批空闲标签页同时测试时产生线程突增。
- 网络监听发送后预取响应缓存改为有界 FIFO，并用 O(1) 弹出消费，避免噪声网络页面在发送等待阶段堆积大量 response 对象。
- NetworkMonitor 对 `response.stream.chunks` / `_stream.chunks` 增加实例级增量合并缓存，流式响应持续追加时只拼接新增 chunk，降低长回答网络监听的重复全量 join 与临时字符串分配。
- Gemini 解析器生成图片 URL 提取改为扫描上一段尾巴与新增响应片段，避免长流每次增长都全量正则扫描；媒体状态探测也不再提前消费已发现图片 URL。
- AI Studio 解析器 fallback 可见文本抽取改为增量扫描，只保留可能跨 chunk 的未完成正则片段和已拼接文本，避免结构化解析暂未出正文时每次增长都全量扫描 raw body。
- Grok 解析器会在增量消费 NDJSON 时缓存图片生成 pending 状态，`get_media_generation_state()` 不再为每次网络监听轮询重新解析完整 raw body；图片 URL 到达后会清空 pending 状态。
- ChatGPT 解析器会在增量 SSE 事件消费时缓存图片生成 pending 状态，`get_media_generation_state()` 不再重放完整 SSE 历史，降低网络监听媒体等待阶段的重复 JSON 解析。
- ChatGPT 解析器会缓存已排序拼接的 message parts 与按长度排序的 content references，仅在 parts/reference patch 或 snapshot 变化后重建可见文本基底，并在 `reset()` 时释放上一轮完整 raw body 快照，降低长流式回答的重复排序/join、引用替换排序和跨轮大文本驻留。
- NetworkMonitor 媒体生成状态缓存改用 raw body 长度、哈希和首尾片段组成的轻量签名判断命中，不再为了跳过重复 `get_media_generation_state()` 而额外保存上一轮完整流式 body。
- NetworkMonitor 最后一次流式解析的诊断缓存不再保存完整 raw body，仅保留首尾预览、长度和签名，避免长流结束后实例继续持有整段响应文本。
- NetworkMonitor 清理监听时会同步重置 parser 状态，释放解析器内部 `_last_raw_response`、pending buffer 和累积文本，避免同一 monitor/parser 实例在请求结束后继续驻留上一轮长流式响应。
- OpenAI / Anthropic stop sequence 流式过滤缓存最长 stop 序列长度；无 stop sequence 的普通 SSE 分块先用字符串快路径跳过 `[DONE]` 深解析，减少长输出逐块拆帧开销。
- `/v1/responses` 流式兼容层改为直接消费内部 chat SSE 并即时转换 `response.output_text.delta` / `response.function_call_arguments.delta`，不再先等待完整非流式响应，降低长输出首 token 延迟和完整结果驻留内存。
- Anthropic 流式转换补充 standalone `response.function_call_arguments.done` 覆盖，确保只收到终态函数参数事件时仍会输出 `tool_use` block 与参数增量。
- 命令引擎处理工作流插队命令时不再每轮复制并排序整个待处理队列，改为线性挑选下一项并清理同 key 残留，降低多命令插队时的重复分配和排序开销。
- 命令结果事件与网络事件队列改为原地追加并按上限裁剪，消费 token 历史也改为原地去重、追加、裁剪，保留读取时深拷贝隔离，减少高频命令结果、外部事件和网络事件记录时的列表复制和切片分配。
- 后台图片下载元数据 LRU 裁剪改为只扫描并收集本次需要移除的少量 key，不再为每次溢出复制整个下载注册表。
- Tool Calling 校验重试的紧凑上下文构造不再复制完整消息列表，也不再为反向遍历额外构造 `enumerate` 列表；JSON 对象候选提取改为按打开括号顺序回填闭合位置，保持候选顺序同时去掉 span 排序。
- Dashboard 系统统计刷新增加单次刷新锁，并让项目 CPU/内存统计共用同一份项目进程快照，避免缓存过期时多标签轮询重复扫描浏览器进程树。
- Dashboard 系统进程快照会一次性预收集主进程、Python 子进程、浏览器根进程及其子进程，CPU 与内存统计共用同一份进程列表/PID 集，避免刷新期间重复遍历浏览器进程树。
- `/api/system/stats` 在缓存仍新鲜时会直接返回缓存快照，不再把每次 Dashboard 轮询都投递到默认 executor；缓存过期时仍沿用原有线程内合并刷新。
- 日志增量读取在 `after_seq` 已追平最新序号时直接空返回；若前端游标高于当前进程序号，会按重启后的日志时代错位返回当前缓冲日志，避免浏览器未刷新时漏掉启动日志。
- Dashboard 磁盘占用缓存刷新失败或返回异常 `0 MB` 时会保留上一次成功值，并使用短失败 TTL，避免瞬时 IO/权限问题让监控显示跳变并反复触发递归扫盘。
- Dashboard 磁盘占用缓存过期但已有成功值时会先返回旧值并启动单个后台刷新 worker，不再让 `/api/system/stats` 同步等待递归扫盘；首次无缓存时仍同步计算以保留初始显示。
- 系统统计缓存增加统一测试隔离重置入口，一次性清理系统统计、项目进程快照、项目内存、磁盘占用和后台刷新 worker 标记，避免缓存/后台状态串扰导致统计回归测试或后续刷新误命中旧数据。
- Dashboard 系统统计轮询在页面隐藏时会清掉 15 秒 interval，页面恢复可见后重建轮询并立即刷新一次，减少后台标签页 JS timer 唤醒以及对进程快照和后端缓存刷新线程的持续触发。
- Dashboard 系统统计刷新会复用同一个在途请求 Promise，手动刷新或可见性恢复撞上轮询时不再立即返回旧缓存，也不会额外打一次 `/api/system/stats`。
- Dashboard 日志轮询在页面隐藏时会清掉 1 秒 interval，恢复可见后重建轮询并立即补拉；若恢复时已有请求在途，会记录 pending 并在当前请求结束后补一次，避免后台 JS timer 空转和可见恢复漏刷新。
- Request Monitor 历史轮询在页面隐藏时会清掉 3 秒 interval，恢复可见且当前停留在监控页时重建轮询并立即按 revision 补拉，避免后台标签页持续空唤醒。
- Dashboard 版本切换状态轮询从固定 2 秒 interval 改为请求完成后自调度 timeout，首轮立即查询，慢请求或服务重启断连时不再反复触发空 tick。
- TabPool 面板自动刷新在页面隐藏时会清掉 1 秒 interval，恢复可见后重建并静默补拉一次；手动关闭自动刷新也会立即停表，避免隐藏页或关闭开关后仍有空转 timer。
- 可视化工作流编辑器的本地 bridge 轮询从固定 250ms interval 改为自调度退避：有动作时保持 250ms 快响应，连续空闲时逐步退到最高 2 秒，减少编辑器打开但空闲时的控制台 POST 空轮询。
- 可视化工作流编辑器 direct test 请求的 abort timeout 改为 `finally` 统一清理，HTTP 失败、CSP/network fallback 或异常重抛时都不会残留延迟触发的 timer。
- TabPool 面板会为 `/api/tab-pool/tabs` 响应生成轻量签名，轮询数据完全不变时只收敛错误状态，不再替换 `tabs` 数组或更新时间戳触发整组卡片重渲染。
- TabPool 面板在 `/api/tab-pool/tabs` 响应签名完全不变时连 `lastUpdate` 也保持不变，避免自动刷新每秒仅更新时间文本就触发整组件 dirty/re-render。
- TabPool 面板自动刷新在响应签名命中时不再反复翻 loading 状态，成功但无变化的轮询只做静默收敛，手动刷新仍保留显式加载提示。
- Commands 面板会为 `/api/commands` 响应生成签名，命令列表完全不变时跳过 `commands` 归一化和数组替换，减少刷新按钮或操作后重复加载带来的重渲染。
- Commands 面板在命令签名命中时不再重复裁剪选择集、同步折叠/来源选择器状态或清理拖拽态，避免同一份命令列表每次轮询都制造新响应式引用。
- Request Monitor 历史列表信任后端最新优先顺序，前端只做线性有序检查，检测到乱序时才复制并排序，减少轮询后重复 computed 时的排序开销。
- Request Monitor 轮询会把当前 `revision` 作为 `if_revision` 传给 `/api/system/request-history`；服务端命中相同 revision 时直接返回 `not_modified`，跳过历史记录复制和预览字段重建。
- RequestManager 会缓存未变化历史切片的 revision，Request Monitor 连续 `if_revision` 轮询命中同一批记录时不再重复哈希最多 200 条长 prompt/response 文本。
- RequestManager 在请求写入/刷新历史后会释放 `RequestContext.monitor` 中已合并的大型 `payload` / `response_payload` / `response_parts` 引用，只保留历史刷新所需的精简 prompt、response、token 和错误字段，降低终态请求在内存队列中滞留时的大文本驻留。
- 请求历史列表接口生成非详情响应时不再先深拷贝完整 prompt/response/error_stack 详情文本，而是直接投影预览字段并仅复制嵌套统计小字典，减少 Request Monitor 轮询下的大文本复制和临时内存分配。
- 请求历史单条详情查询在历史已维护有序时改为直接反向扫描，不再为每次详情请求复制整段历史；无序旧记录仍排序兜底并保持最新重复 id 优先。
- Request Monitor 历史刷新在已有请求 in-flight 时不再直接吞掉新刷新，而是合并成一次 pending 补拉；慢请求、切入监控页或恢复可见时不会等到下一轮 3 秒 interval 才补齐最新历史。
- Request Monitor 历史记录派生 view 增加有界签名缓存；相同历史记录在轮询或 repeated computed 时复用格式化结果，详情已加载记录也通过轻量头尾签名复用，摘要、状态、耗时、token 或详情文本变化时仍会即时重建；派生 view 不再序列化或持有 `payload` / `response_payload` 大载荷，减少详情缓存后的渲染 CPU 与内存驻留。
- Request Monitor 派生 view 进一步剥离 `prompt` / `response` / `error_stack` 完整正文，只保留列表预览与元数据，避免 260 条缓存长期挂住大文本；详情抽屉会按需回查原始记录。
- 预设配置局部 patch 合并改为只复制被修改路径和补丁新值，不再为更新一个嵌套字段深拷贝整个 workflow/selectors/脚本配置大对象，保留嵌套 dict 深合并语义。
- 命令排序接口在计算出的最终顺序与当前顺序一致时直接返回成功，不再重复深拷贝、写盘和刷新命令本地状态文件。
- 命令组手动执行成功路径复用已做过的预览计划，避免 `preview_command_group()` 与 `execute_command_group()` 连续重复加载命令和计算候选；执行阶段仍保留 scope 复查。
- 手动命令测试与命令组执行选择空闲标签页时优先使用轻量 session 快照，仅在快照不可用时回退完整 `get_status()`，减少调试命令触发时的状态对象构造。
- 命令组列表统计改用轻量命令快照，不再通过 `list_commands()` 合并运行态统计，减少命令组面板和重命名校验读取时的深拷贝与无关字段合并。
- 命令更新接口恢复密钥占位符时改用单条原始配置快照，不再通过展示型 `get_command()` 合并运行态统计，减少 PUT `/api/commands/{id}` 的深拷贝与无关字段参与。
- 命令详情读取 `get_command(id)` 改为只复制命中的单条命令并只合并该命令运行态统计，不再为了详情、手动测试或命令检查构造整张展示列表。
- 命令列表合并运行态统计时只复制当前命令 id 命中的统计项，不再为 `/api/commands` 每次刷新深拷贝整张历史运行态统计表。
- 标签池默认 `first_idle` acquire 改为线性挑选最低 `persistent_index` 的可用会话，不再先为普通、域名和精确 URL 默认分配路径构造全量排序列表；随机和轮询模式仍保留原有排序语义。
- tab 路由 `round_robin` 选择改为单次线性扫描下一个 `persistent_index`，候选标签无序时仍保持按编号轮转，避免每次动态路由都复制并排序候选列表。
- 标签池列表接口不再为了读取 `allocation_mode` 额外构造完整 `get_status()`，并按唯一域名缓存预设列表/默认预设查询，减少 Dashboard 常驻轮询下的重复快照和配置查询。
- 动态域名聊天路由不再在真正 acquire 前调用 `_list_candidate_tabs()` 做候选预检，避免一次请求先全量扫描标签页又进入原子 acquire，也避免缓存快照未刷新时提前误报 404；默认 selector 读取改用轻量 `allocation_mode` 属性。
- 站点标签页创建把浏览器 `new_tab()` / 初始导航移出标签池锁，仅保留扫描、容量检查和注册在锁内；若锁外创建期间池容量被并发占满，会关闭刚创建的 raw tab/隔离 context，避免慢启动阻塞 acquire/release 且不泄漏资源。
- chat 与 tab 路由的流式队列消费改用事件循环内的 `get_nowait()` + 短 sleep 轮询，不再为空队列等待调用 `asyncio.to_thread(queue.get)`，减少慢流/静默流并发时对默认 executor 线程的占用。
- 流式 worker 队列空读等待从固定短间隔轮询改为有界自适应退避，保留 chunk 到达时的 `get_nowait()` 快路径，同时降低大量空闲 SSE 请求的事件循环唤醒噪声。
- 命令触发检查与命令组候选读取改用原始轻量命令快照，仅命令列表展示路径合并运行态统计，减少触发轮询时的深拷贝和运行态字典合并开销。
- BrowserWatchdog 标签池日志改用轻量摘要，只统计总数、空闲/忙碌数量和前几个标签状态，不再为诊断日志构造完整 tab URL、route token 和会话详情。
- 精确 URL 路由执行阶段复用已解析标签编号，避免 API 入口解析后执行层再次扫描同 URL 标签池；保留执行前 URL 匹配校验以维持严格路由语义。
- TabPool 配置读取不再等待浏览器配置写锁，更新接口保留写者串行化但去掉外层重复锁，降低 Dashboard 标签列表轮询被慢磁盘写入阻塞的概率。
- RequestManager 状态接口复制请求上下文引用后只读取轻量状态摘要，只有运行中请求计算 duration；`is_locked`、运行请求列表和当前请求查询也复用运行态轻量判断，减少 Dashboard/health 高频轮询下对终态请求的完整快照构造。
- 文本剪贴板粘贴路径抽出统一 helper，只在读取/写入/恢复剪贴板和发送粘贴快捷键期间持有全局剪贴板锁，把 DOM settle 等待移出锁外，减少多标签并发输入互相阻塞。

test:
- 新增并通过流式 `[DONE]` worker 卡住快速返回、非流式聚合未终止 SSE 尾帧 flush、RequestManager 保存调度合并、历史追加/读取有序裁剪、QUEUED 僵尸清理、请求状态与历史记录一致快照读取、请求历史 revision 缓存复用、无序请求历史按完整历史排序后再 limit、文本剪贴板锁外 settle 等待、网络流 chunks 增量合并缓存、NetworkMonitor 空 body HTTP 错误快失败、流式 worker 队列空读退避、Responses 真流式文本与工具调用增量转发、请求监控路由别名归并、Dashboard 配置加载请求世代保护、Request Monitor 有序快路径与服务端 `if_revision` not-modified 快路径、历史详情缓存合并保留新鲜列表字段、请求历史内容 revision 与详情请求不短路、Request Monitor 历史 in-flight pending 补拉、Request Monitor 历史隐藏页停表与恢复补拉、Request Monitor 派生 view 缓存命中/失效、详情记录复用和大载荷不序列化/不驻留、Dashboard 系统统计隐藏页停表与恢复可见刷新、Dashboard 日志隐藏页停表与 pending 补拉、Dashboard 版本切换状态自调度轮询、可视化工作流编辑器 direct test timeout 清理、日志 clear epoch 与多页面同步清空、日志独立 token 预检前脱敏、默认标签页选择、图片 URL 归一化/误判过滤、后台图片下载大小上限与注册表裁剪、命令引擎测试环境禁用自动调度、命令重排跳过无变化保存、命令路由保存失败 500、命令组列表轻量读取、命令更新占位符恢复配置快照、命令运行态统计仅展示不持久化、BrowserWatchdog 轻量标签池摘要、精确 URL 路由复用已解析标签并校验 URL、TabPool 配置读写锁拆分、TabPool 隐藏页/关闭自动刷新停表、WorkflowPanel bridge 空闲退避、命令面板请求世代保护与相同响应跳过重渲染、日志清空丢弃旧轮询响应、音频 fast-return 完成记录与 worker 停止语义、手动命令释放兜底、手动命令 acquire 后 scope 复查、命令组执行复用预览计划、手动命令空闲标签快照选择、标签池 first-idle acquire 线性选择、标签池列表预设查询缓存、动态域名聊天跳过候选预检、站点标签页锁外创建与满池清理、流式队列等待不占默认 executor、worker 错误 SSE `[DONE]` 收尾、系统统计缓存命中无 executor 跳转、日志增量读取快路径与重启游标恢复、TabPool 后台轮询暂停与相同响应跳过重渲染、版本切换状态轮询防重入、`n > 1` 单上游 choice stop sequence 早停、Anthropic stop_sequences 下传与底层流关闭传播、SSE `[DONE]` 精确识别、stop 序列状态缓存、命令插队队列排序、命令事件队列裁剪与消费 token 历史裁剪、Tool Calling 重试上下文与 JSON 候选顺序、系统统计刷新合并、系统进程树快照预收集、磁盘统计缓存失败保留旧值与后台刷新等回归测试。
- 新增并通过后台图片下载器 pending 计数 O(1) 与 shutdown 立即唤醒等待者、WorkflowEditor 销毁后全局监听清理的回归测试。
- 新增并通过站点配置读取自动补全保存失败回滚测试。
- 新增并通过本地覆盖保存失败时主站点配置不落盘测试。
- 新增并通过命令本地状态保存失败时主命令配置不落盘测试。
- 新增并通过 Gemini 生成图片 URL 增量扫描、跨 chunk 边界保留和媒体状态不消费图片结果测试。
- 新增并通过 AI Studio fallback 可见文本增量扫描、跨 chunk 匹配保留和相同 body 不重复输出测试。
- 新增并通过 Grok 媒体生成状态缓存读取和图片到达后清空 pending 状态测试。
- 新增并通过 ChatGPT 媒体生成状态缓存读取和文本增量行为保持测试。
- 新增并通过 ChatGPT SSE JSON delta、`[DONE]` 跨 chunk 分片缓冲和无尾部空行 `[DONE]` 兼容测试。
- 新增并通过 ChatGPT message parts 未变化时复用已拼接缓存、content references 未变化时复用已排序缓存、`reset()` 释放上一轮 raw body 快照的回归测试。
- 新增并通过 NetworkMonitor 媒体生成状态缓存使用轻量 raw body 签名、不驻留完整 body 且能识别中段变化的回归测试。
- 新增并通过 NetworkMonitor 最后一次流式解析结果只保留 raw body 预览、长度和签名而不驻留完整 body 的回归测试。
- 新增并通过 NetworkMonitor cleanup 会重置 parser raw snapshot 并清空自身最后一次流式解析缓存的回归测试。
- 新增并通过图片预设 reload 坏文件保留现有预设、系统统计进程快照预收集和并发刷新合并的回归测试。
- 新增并通过 Tool Calling 非流响应 event-wrapped SSE 解码测试。
- 新增并通过运行时解析器配置与源码原子写失败保留旧文件、失败回滚使用原子写恢复旧源码测试。
- 新增并通过提取器配置原子写失败保留旧文件、默认提取器切换保存失败回滚、配置导入保存失败回滚测试。
- 新增并通过配置市场本地默认列表、投稿清单和远程缓存原子写失败保留旧文件测试。
- 新增并通过 TabPool 浏览器配置原子写失败保留旧文件测试。
- 新增并通过完整配置备份导入在存在无效 section 时不会提前写入有效 section 的回归测试。
- 新增并通过完整配置备份导入后续文件写失败时回滚已写文件且不刷新运行态和浏览器常量测试。
- 新增并通过浏览器常量保存写失败时不热重载、不同步运行态标签池测试。
- 新增并通过更新保留白名单保存替换失败时保留旧文件测试。
- 新增并通过请求历史列表非详情响应不深拷贝详情大文本、同时保持嵌套 token 统计副本隔离的回归测试。
- 新增并通过命令详情读取只复制命中命令、只合并命中运行态统计且不构造完整命令列表的回归测试。
- 新增并通过命令主配置替换失败或本地状态保存报告失败时恢复旧 `commands.local.json` 的回归测试。
- 新增并通过站点主配置替换失败或本地覆盖保存报告失败时恢复旧 `sites.local.json` 及本地覆盖内存状态的回归测试。
- 新增并通过 `/api/config` 全量保存失败或保存异常时回滚站点配置内存状态的回归测试。
- 新增并通过 Workflow 编辑器保存工作流失败或抛错时回滚 `workflow/selectors` 内存状态的回归测试。
- 新增并通过站点/命令本地覆盖快照读取失败时中止保存且不触碰主配置的回归测试。
- 新增并通过站点/命令主配置及本地覆盖/状态文件替换成功但 mtime 刷新失败时仍按保存成功提交的回归测试。
- 新增并通过站点/命令本地覆盖/状态回滚恢复成功但 mtime 刷新失败时仍完成恢复的回归测试。
- 新增并通过运行时解析器/提取器配置替换成功但 mtime 刷新失败时仍按保存成功提交的回归测试。
- 新增并通过取消请求立即写入历史、后续 finish 刷新同一条历史而不重复记录的回归测试。
- 新增并通过预设配置局部 patch 合并不会深拷贝未修改大分支、仍复制被修改路径的回归测试。
- 新增并通过 RequestManager 启动加载无序历史文件时先排序全量记录再截取 200 条的回归测试。
- 新增并通过 tab 路由 `round_robin` 在无序候选标签下仍按 `persistent_index` 顺序轮转且无需预排序的回归测试。
- 新增并通过 Request Monitor 长详情文本中段变化会让派生 view 缓存失效、TabPool 相同响应不更新 `lastUpdate` 的前端回归测试。
- 新增并通过 Request Monitor 列表态不再缓存 `prompt` / `response` / `error_stack` 全文、详情抽屉仍可从原始记录读取全文的回归测试。
- 新增并通过 Commands 面板在相同响应下不再反复改写选择集/折叠/来源状态的回归测试。
- 新增并通过 TabPool 面板在相同响应下不再反复翻 loading 状态的回归测试。
- 新增并通过 RequestManager 超额历史清理继续扫描后续超时请求、Dashboard token 状态保存/清除/备份恢复/storage 事件即时刷新的回归测试。
- 新增并通过网络错误触发命令扫描未命中事件不 deep-copy、命中事件仍以隔离副本注入 interrupt context 的回归测试。
- 新增并通过命令结果事件布尔匹配不复制命中事件、调度阶段仍返回隔离副本的回归测试。
- 新增并通过流式绝对超时分支必须发 error + `[DONE]` 的协议回归测试。
- 新增并通过 Commands 预设选项旧响应不覆盖当前编辑命令和预设列表的前端 VM 回归测试。
- 新增并通过 Claude、DeepSeek、Qwen、GLM、Mimo、Grok、ChatGPT 等增量 parser 在 raw body 缩短、同长度替换和更长非前缀替换时都会重置并解析新响应的回归测试。
- 新增并通过 NetworkMonitor 硬超时必须抛出超时、流捕获完成日志不能误写为检测到 done 的回归测试。
- 新增并通过 RequestManager 写入历史后释放上下文大 payload，且压缩后的上下文仍可在 late failure 到达时刷新同一条历史记录的回归测试。
- 新增并通过文件日志轮转遇到 Windows 文件占用时延后轮转、继续写入且不输出 logging error，以及轮转异常进入 `handleError` 兜底路径时仍不刷 traceback 的回归测试。
- 新增并通过 RequestManager 高频状态接口只使用轻量状态摘要、tab 固定标签/域名/精确 URL 流式 worker 异常时进入快速 done 清理的回归测试。
- 新增并通过共享 JSON 原子写、`.env` 原子写和系统配置回滚恢复遇到 Windows 文件占用时退避重试的回归测试。
- 新增并通过命令引擎 pending workflow interrupt 队列锁内去重、畸形项清理和优先级取出不完整排序的回归测试。
- 新增并通过 Gemini 以未闭合 `<render>` 开头时标记 render 输出包装未闭合、NetworkMonitor 遇到半截 render 输出静默时回退 DOM 补齐而不是正常完成的回归测试。
- 新增并通过 ConfigTab 在冻结预设对象、缺失 `file_paste` / `prompt_padding` 或冻结嵌套配置时不抛错，且普通可写预设仍原位补齐可编辑小节的前端 VM 回归测试。
- 当前验证通过 `python -m pytest -q --durations=10`（499 passed）、`tests/*.test.js` 全量 Node VM 回归、关键 Python 文件 `py_compile`、前端关键 JS `node --check`、UTF-8 guard 与 `git diff --check`。

## 2026-06-08

fix:
- 配置全量保存现在会保留被安全列表隐藏的 localhost / 127.0.0.1 等本地站点，并改为按 host 精确过滤，避免误删本地配置或误伤 `notlocalhost.com`。
- 单预设配置保存默认改为合并已有字段；只有显式 `replace=true` 才完整替换，避免保存 `selectors` / `workflow` 时丢失 `stream_config`、`image_extraction`、`file_paste`、`advanced` 等配置。
- 配置接口的非法 JSON / 非对象请求统一返回 400，不再落入 500。
- 图片配置保存补齐 `canvas_export_mime`、`canvas_export_quality`、`src_allow_patterns` 校验与保留，避免高级图片提取配置被重置。
- 图片预设文件缺失、损坏或含无效条目时会使用结构正确的 fallback，并跳过坏条目继续加载。
- 域名+预设路由和 query/body `preset_name` 会在进入执行器前严格校验并解析别名，找不到预设时直接返回 404。
- 创建预设时显式指定的源预设不存在不再静默改用第一个预设；删除预设支持历史 `预设_` 别名解析。
- 实时网络异常命令修复：工作流中命中 403 / CF 盾等 `network_request_error` 时会进入高优先级命令插队队列，不再直接取消当前请求；命令处理后可回到原工作流当前步骤继续执行，避免“过盾成功但工作流已主动中断”。

change:
- `.env` 加载默认不再覆盖已存在环境变量，方便测试、部署脚本和容器注入配置路径。
- 站点级与预设级高级配置拆分更明确：独立 Cookie 保持站点级，输入框稳定等待、URL 切换等待、发送确认等可作为预设级覆盖。
- 页面侧能力拆分到 `app/core/page_capture/`：Kimi fetch 抓流与 DeepSeek 页面直发不再内嵌在工作流执行器或 request_transport 核心文件中，后续同类站点可通过统一 registry 扩展。

test:
- 补充并通过配置引擎、配置路由、预设合并、图片预设 fallback、域名预设校验的针对性行为验证；通过 `compileall`、`node --check` 和 `git diff --check`。

## 2026-06-06

new:
- 域名路由支持动态同站点标签页分配：未指定 `tab_index` 时，`/url/{domain}/v1/chat/completions` 会把 `selector` 作为标签页池分配模式传入真实工作流，覆盖普通流式、非流式与 Tool Calling 路径。

change:
- 路由响应头诊断增强：动态域名路由在尚未解析到具体标签页前也会返回 `X-Requested-Route-Domain` 与 `X-Tab-Selection-Mode`；固定标签页、精确 URL 和预设路径继续返回对应 `X-Resolved-*` 头。
- 教程与 README 更新：补齐精确 URL 路由、URL 绑定预设、随机/轮询分配、请求历史、调试取消与强制释放等实际使用流程，并明确受控浏览器与普通控制台窗口的分工。

test:
- 新增 API 层域名路由单测，覆盖 `selector=random/round_robin` 的入口传递、固定 `tab_index` 分支、无候选标签页错误路径，以及普通/流式/Tool Calling 分发链路。

## 2026-06-02

new:
- 新增 Codex 本地 `agent_bridge` MCP 专用接口配置，可通过工具层直接读取/发送 agent-bridge 消息并更新前后端状态；CLI 仍保留为兜底路径。

change:
- 日志展示表达统一优化：控制台/Web/文件日志新增 `#001` 风格请求短追踪号，并把常见旧前缀展示归一到 `[ROUTE]`、`[POOL]`、`[INPUT]`、`[MONIT]`、`[PAGE]` 等结构化标签；Web 展开原文仍保留原始日志。
- Cute Mode 话术继续收敛：分块完成、富文本粘贴重试、发送重试、附件补发、低熵页面预热等路径统一为小鹿语气并保留关键参数。

fix:
- 日志脱敏继续加固：输入快照不再输出头尾明文，只记录长度和短指纹；工具结果与统一日志脱敏支持 `+`、`/`、`=` 结尾及折行的长 base64/data URI。
- 安全日志性能与可读性优化：大文本脱敏增加低成本预检，深层 logger 名增加最终长度兜底，避免日志列再次被超长缩写撑开。
- 文件粘贴路径日志降敏：临时文件日志改为显示 `temp/<filename>`，避免暴露本机绝对路径；媒体结果记录异常改为忽略并继续流式解析。
- 网络监听清理修复：补齐 CDP interception 状态默认值，并恢复 `tab.listen.stop()` 优先释放，避免停止监听时因缺失 `_cdp_session_listening` 抛出属性错误。
- 标签页释放竞态修复：`force_release()` / `release()` 清理完成前不再提前暴露 `IDLE`，并避免清理收尾覆盖并发写入的 `ERROR/CLOSED` 状态；`TabPoolManager.release()`、`terminate_by_index()`、`force_release_all()` 的浏览器 I/O 移出全局池锁。

## 2026-05-31

fix:
- 标签页池内存与状态修复：watchdog 现在会每轮清理 `ERROR` / 不健康页签；标签页关闭路径改为先停止全局网络监听再移除 session；`release()` / `force_release()` 不再把已标记的 `ERROR` 页签错误恢复为 `IDLE`。
- 浏览器保活脚本清理修复：可见性模拟注入状态改为绑定到底层 tab，并在工作流释放、强制释放和恢复路径中清理，避免 isolated context 换绑后跳过注入或污染后续页面。
- 网络与流式监听缓存释放：停止监听时清空 DrissionPage 残留包队列和原始响应体缓存；工作流结束后显式释放 NetworkMonitor / StreamMonitor 的多模态结果引用，降低长时间运行时大响应体和图片数据常驻内存的风险。
- 后台图片下载缓存加上限：图片下载注册表改为有界 LRU 元数据缓存，避免长期代理大量图片 URL 时 `_entries` 无限增长。
- 线程资源约束优化：标签页清理和命令会话 evict 改为受限维护线程池，并延长全局网络监听停止等待窗口，减少高并发异常关闭时的裸线程堆积和监听线程竞态。

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
