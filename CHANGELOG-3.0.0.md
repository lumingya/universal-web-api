# 更新日志 (v3.0.0)

> 本版本是一次**性能重构（Layer 0）**：目标是先降内存、再降 CPU，浏览器仍保持**有头模式**，不改变任何接口与站点配置格式。
> 所有新行为都带开关，默认值即推荐值；遇到兼容问题可以逐项关闭，回到 2.10.0 的行为（见文末“回退开关”）。
> 详细设计与实测数据见 `docs/performance_refactor.md`。

## 效果速览

| 场景 | 2.10.0 | 3.0.0 |
|---|---|---|
| 流式轮询一次页面快照 | 长对话约 138 条 CDP 命令，每条都在渲染进程留下对象 | 1 次 CDP 调用，不留对象 |
| 一次请求后被钉在渲染进程里的已移除 DOM 节点 | 6007 个，只能靠定时重启浏览器释放 | 空闲回收后 7 个，不刷新页面、不掉登录 |
| 后台 / 最小化窗口里的空闲标签页 | 计时器照常运行（测试页 50 次/秒） | 冻结后 0 次/秒，被占用时 3–7 ms 恢复 |
| LMArena 160KB 长回答的累计解析耗时 | 7.5 s | 0.06 s（约 125 倍） |
| 清洗 100 万字 prompt | 23.7 ms | 1.8 ms |
| 面板单进程内存采样 | 0.76 ms | 0.012 ms |

## 主要优化

### 1. 流式监控：一次调用拿到整张页面快照

- 新增 `app/core/stream_snapshot.py`：把“选回复元素 + 判断是否仍在生成 + 深度提取文本/图片”合并成**一个页面内函数**，
  结果以 `JSON.stringify` 字符串返回。每轮轮询只需 1 次 CDP 调用，也不会产生 RemoteObject。
- 候选排序、锚点、内容节点定位、生成状态、图片探测与旧实现逐条对齐，并在真实浏览器中与旧路径逐项对照测试。
- 自动兜底：快照失败或返回格式异常时，本轮自动回退旧路径；连续 3 次无效则该次监控停用快照。使用自定义提取器的站点始终走旧路径。
- `_extract_image_info`、Arena 停止按钮探测、Arena 状态快照同样改为返回 JSON 字符串。
- 开关：`STREAM_PAGE_SNAPSHOT_ENABLED=true`。

### 2. CDP 对象卫生：渲染进程不再越用越胖

- 新增 `app/core/cdp_hygiene.py`（建立连接时自动安装，幂等）：
  - `run_js` 返回的**非节点**对象 / 数组，取值后立即 `Runtime.releaseObject`。以前这些对象从不释放，会连带已移除的 DOM 一起常驻内存，强制 GC 也回收不了。
  - `Network.enable` 自动带上缓冲上限：总计 32MB、单个资源 16MB。
  - 提供 `run_js_json` 工具函数。
- 热点路径（流式监控、Arena 监听与生图、Kimi 抓取、页面生命周期探针等）全部改为返回字符串，
  并加入静态检查测试，防止以后重新引入返回对象的 `run_js`。
- 兼容 DrissionPage 4.0.x 至 4.1.x 各版本的 `Driver.run` 签名（参数原样透传）。
- 开关：`CDP_HYGIENE_ENABLED`、`CDP_RELEASE_JS_OBJECTS`、`CDP_NETWORK_MAX_TOTAL_BUFFER_MB`、`CDP_NETWORK_MAX_RESOURCE_BUFFER_MB`。

### 3. 空闲标签页 CDP 会话回收（替代定时重启）

- 新增 `app/core/tab_pool_parts/idle_maintenance.py`：标签页空闲时断开并重建 CDP 连接，一次性释放元素查找留下的
  nodeId / objectId 及其钉住的旧 DOM。**不刷新页面、不影响登录态**，全局网络监听随后自动恢复。
- 触发条件：空闲至少 30 秒，且满足以下任一条件：
  - 自上次回收后处理了 20 个请求；
  - 距上次回收超过 30 分钟；
  - DOM 节点数达到 15 万（每 5 分钟检查一次）。
- 回收失败时自动重试：每次都先断开、清掉半初始化的驱动再完整重建，最多 3 次。仍失败时验证一次连接：
  - 连接仍可用：照常继续使用；
  - 连接已不可用：把标签页标记为错误，不再分配给请求，由标签页池清理后重新接管，**绝不会把坏掉的标签页交给请求**。
- 有了回收之后，`.env.example` 中的定时重启 `SCHEDULED_RESTART_ENABLED` 默认改为 `false`（代码默认本来就是 false）。
- 开关：`BROWSER_CDP_RECYCLE_*`。

### 4. 空闲标签页冻结

- 空闲至少 60 秒、且**用户看不到**（后台标签页或窗口已最小化）的标签页会被 `Page.setWebLifecycleState=frozen` 冻结，
  页面计时器、动画帧和页面任务全部暂停。被请求占用、执行 page_check 或保活唤醒前自动恢复，恢复耗时 3–7 ms。
- 用户正在看的标签页永远不会被冻结，也不会因为探测而收到 blur / focus 事件。
- 冻结后会核验是否真正生效，没生效就撤销并在 30 秒后重试。用户手动切回、Chrome 自动解冻等情况每 10 秒同步一次状态。
  冻结过程会随时给请求让路，请求不会因为冻结而等待。
- 并发实测：3 个浏览器 × 4 个标签页 × 8 轮，冻结与占用正面竞争，交互成功 192/192（两轮）。
- 冻结启用时，周期保活 `CMD_PERIODIC_KEEPALIVE_ENABLED` 默认关闭。
- 开关：`BROWSER_IDLE_FREEZE_ENABLED=true`、`BROWSER_IDLE_FREEZE_AFTER_SEC=60`、`BROWSER_IDLE_FREEZE_REQUIRE_HIDDEN=true`。

### 5. 网络监听减负

- 全局网络监听新增资源类型过滤 `GLOBAL_NETWORK_INTERCEPTION_RES_TYPES`（默认 `XHR,Fetch,EventSource,Document`，`*` 表示全部）。
  图片、脚本、样式不再逐个开启流式抓取和缓存响应体。工作流里的 `listen.start()` 不受这个过滤影响。
- `patch_drissionpage.py` 中流式响应体改为分块列表，读取 `fullText` 时才拼接一次并缓存。
  - 旧实现每来一块就整串重拼，是 O(n²)。
  - 追加与读取之间加锁，多线程下不丢分块。
  - `start.py` 启动时会自动升级已安装的旧版补丁。

### 6. 解析与后台开销

- **LMArena 增量解析**：已完整的行只解析一次并缓存，每次只处理新增部分，逐次输出与旧实现完全一致。
- **进程模型可选**：`BROWSER_PROCESS_MODEL=per-site` 或 `limit:N`，默认留空即 Chromium 默认行为。
  可省 10%~20% 内存，但同站点并发较多时会互相拖慢。
- **日志**：
  - `.env.example` 的 `LOG_LEVEL` 由 `DEBUG` 改为 `INFO`。
  - 可爱风格翻译改为展示时才翻译，控制台和面板共用同一次翻译结果，并对 512 字以内的消息做 LRU 缓存。
- **请求历史**：
  - 写盘按 5 秒去抖，多次更新合并为一次写入，进程退出时自动刷盘（`REQUEST_HISTORY_SAVE_DEBOUNCE_SEC`）。
  - 去掉每次写入的 `fsync`，仍保留原子替换，文件不会写坏。
  - 长 prompt 清洗改为凑够上限即停，不再整段跑正则。
- **面板统计**：
  - 默认不再读取开销很大的 USS。新口径 `PANEL_MEMORY_METRIC=fast`：Windows 用 private，Linux 用 rss-shared，数值与 USS 接近。
  - 统计缓存由 2 秒调整为 5 秒；运行中 / 排队请求数与 token 统计仍然实时读取。

## 问题修复

- **LMArena 回答偶发乱码**：旧实现对整段响应做 mojibake 修复，快照恰好截断在多字节字符中间时整段修复失败，乱码会混进回答
  （随机测试中 200 条流有 55 条出错）。新的按行增量解析不再出现这个问题。
- `.env.example` 中 `SCHEDULED_RESTART_ENABLED=true` 与代码默认值（false）不一致的问题已统一。

## 升级须知

- **已有的 `.env` 不会被改动。** 如果你的 `.env` 是从旧版 `.env.example` 复制的，建议手动修改：
  - `LOG_LEVEL=DEBUG` → `INFO`；
  - `SCHEDULED_RESTART_ENABLED=true` → `false`；
  - 如果显式写了 `CMD_PERIODIC_KEEPALIVE_ENABLED=true`，保活会周期性唤醒标签页，冻结基本失效。

  新增的开关不写也会使用默认值。
- **`config/browser_config.json` 不会被改动**。建议把 `GLOBAL_NETWORK_INTERCEPTION_LISTEN_PATTERN` 从域名收窄到真实 API 路径，进一步减少监听量。
- **面板里的内存数字口径变了**（USS → fast），与 2.10.0 的数字不能直接比较。需要对比时请用 Chrome 任务管理器或同一个外部工具。
- 冻结只针对看不到的标签页。每个标签页各占一个窗口且都没有最小化时，冻结基本不会触发，这是预期行为。
- 不要对想观察冻结效果的标签页打开开发者工具（F12）：附加新的调试会话会让 Chrome 解除冻结。

## 回退开关

| 想恢复旧行为 | 设置 |
|---|---|
| 流式监控走旧的多次调用路径 | `STREAM_PAGE_SNAPSHOT_ENABLED=false` |
| 不释放 run_js 对象、不加 Network 缓冲上限 | `CDP_HYGIENE_ENABLED=false` |
| 只去掉 Network 缓冲上限 | `CDP_NETWORK_MAX_TOTAL_BUFFER_MB=0`、`CDP_NETWORK_MAX_RESOURCE_BUFFER_MB=0` |
| 关闭空闲冻结 | `BROWSER_IDLE_FREEZE_ENABLED=false` |
| 关闭 CDP 会话回收 | `BROWSER_CDP_RECYCLE_ENABLED=false`（可配合 `SCHEDULED_RESTART_ENABLED=true`） |
| 全局监听抓取所有资源类型 | `GLOBAL_NETWORK_INTERCEPTION_RES_TYPES=*` |
| 历史记录每次立即写盘 | `REQUEST_HISTORY_SAVE_DEBOUNCE_SEC=0` |
| 面板用 USS 口径 | `PANEL_MEMORY_METRIC=uss` |

## 工程化与测试

- 新增测试：
  - `test_stream_snapshot.py`、`test_cdp_hygiene.py`；
  - `test_idle_maintenance.py`：含真实浏览器回收、有头冻结、回收失败重试与隔离；
  - `test_network_res_types.py`、`test_lmarena_incremental_parse.py`、`test_request_history_perf.py`；
  - `test_log_cute_lazy.py`、`test_panel_stats_perf.py`、`test_start_process_model.py`。
- 需要真实浏览器的用例在找不到 Chromium 时自动跳过（`tests/_real_browser.py`）；有头冻结用例需要 X 显示。
- 新增两个手动端到端脚本：
  - `tests/e2e_idle_maintenance_headed.py`：期望输出 `RESULT recycled | freed | froze | resumed`；
  - `tests/e2e_concurrency_headed.py`：多浏览器、多标签页并发压测。
- 全量测试：900 项通过。失败的用例全部是 2.10.0 基线中原本就失败的，本版本没有新增失败。
