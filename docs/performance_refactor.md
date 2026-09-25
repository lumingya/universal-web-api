# 性能重构说明（Layer 0：内存优先，CPU 其次）

对应报告 `refactor_report.md`（基于 HEAD 270e814 / VERSION 2.10.0）。本轮只实现 **Layer 0（P0-1 ~ P0-7）**，
Layer 1~3（架构调整、进程拆分、替换 DrissionPage 等）不在本次范围内。浏览器仍保持**有头模式**。

所有改动都带开关，默认值即推荐值；出现兼容问题时可以逐项关闭回到旧行为。

---

## 总览

| 编号 | 内容 | 主要文件 | 开关（默认） |
|---|---|---|---|
| P0-1 | 流式轮询合并为 1 次 CDP 调用的页面内快照 | `app/core/stream_snapshot.py`、`stream_monitor.py` | `STREAM_PAGE_SNAPSHOT_ENABLED`（true） |
| P0-2 | `run_js` 不再泄漏 RemoteObject；热点路径改为返回 JSON 字符串 | `app/core/cdp_hygiene.py` 及各调用点 | `CDP_HYGIENE_ENABLED` / `CDP_RELEASE_JS_OBJECTS`（true） |
| P0-3 | 空闲标签页 CDP 会话回收；定时重启默认关闭 | `app/core/tab_pool_parts/idle_maintenance.py` | `BROWSER_CDP_RECYCLE_*`、`SCHEDULED_RESTART_ENABLED=false` |
| P0-4 | 全局监听只抓 API 类资源；Network 缓冲上限；流式响应体懒拼接 | `tab_pool_parts/network.py`、`patch_drissionpage.py` | `GLOBAL_NETWORK_INTERCEPTION_RES_TYPES`、`CDP_NETWORK_MAX_*_BUFFER_MB` |
| P0-5 | LmarenaParser 增量解析 | `app/core/parsers/lmarena_parser.py` | 无（行为等价） |
| P0-6 | 空闲且不可见的标签页冻结 | `idle_maintenance.py`、`session.py`、`command_engine.py` | `BROWSER_IDLE_FREEZE_*`（true / 60s） |
| P0-7 | 进程模型可选、日志惰性翻译、历史落盘去抖、面板统计降开销 | `start.py`、`cute_translator.py`、`request_manager.py`、`system.py` | `BROWSER_PROCESS_MODEL`、`LOG_LEVEL=INFO` 等 |

---

## P0-1 页面内快照

原 `StreamMonitor._get_snapshot_prefer_anchor` 每次轮询要做全 DOM 搜索、逐个候选元素 `describeNode/resolveNode`、
取布局、查内容节点、发送约 10KB 的深度提取脚本、生成状态最多 4 次全 DOM 搜索……会话 40 轮时约 138 条 CDP 命令/次，
而且每条命令都会在渲染进程留下 RemoteObject。

现在合并为 `stream_snapshot.py` 中的**一个页面内函数**，返回一个 `JSON.stringify` 字符串：

- 每次轮询 1 次 `Runtime.callFunctionOn`；字符串结果不产生 RemoteObject；
- 候选排序、锚点格式、内容节点定位、深度提取、生成状态、图片探测与原实现逐条对齐
  （`tests/test_stream_snapshot.py` 在真实浏览器里与旧路径逐项对照）；
- 失败保护：返回 `ok:false`、响应格式不对或抛异常时本次回退旧路径；连续 3 次无效后该监控实例停用快照；
  使用自定义提取器（非 `DeepBrowserExtractor`）时始终走旧路径。
- `_extract_image_info`、`_arena_native_stop_present`、`_ARENA_STORE_SNAPSHOT_JS` 同样改为 JSON 字符串返回。

## P0-2 RemoteObject 卫生

DrissionPage 的 `run_js` 固定 `returnByValue=False`，返回对象/数组时生成 RemoteObject 且从不释放，
V8 inspector 会一直强引用它们（连带已移除的 DOM），强制 GC 也收不回。

`cdp_hygiene.install()`（在 `connection.py` 建立连接时调用，幂等、只做运行期 monkeypatch）：

1. 包装 `parse_js_result`：**非节点**的对象/数组结果取回后立即 `Runtime.releaseObject`；
   节点结果会被包装成元素对象继续使用，不释放。
2. 包装 `Driver.run`：`Network.enable` 未显式给出缓冲上限时补上
   `maxTotalBufferSize` / `maxResourceBufferSize`（见 P0-4）。
3. 提供 `run_js_json`：页面内 `JSON.stringify` 后只返回字符串。

热点路径（stream_monitor、arena 监听/生图、kimi 抓取、page_lifecycle 探针等）已改为 JSON 字符串返回，
`tests/test_cdp_hygiene.py` 中的 `HOT_PATHS` 静态检查会阻止这些文件重新引入返回对象的 `run_js`。

> 没有采用报告里的 objectGroup + releaseObjectGroup 方案：DrissionPage 的 `_root_id`
> 和元素 objectId 不在任何 group 里，按 group 释放会误伤正在使用的元素。改为“逐个释放 + 空闲会话回收（P0-3）”兜底。

## P0-3 空闲标签页 CDP 会话回收

元素查找（`DOM.performSearch`/`resolveNode`）产生的 nodeId、objectId 挂在 CDP 会话上，只有会话断开才整体释放。
长时间使用的标签页会把旧对话的 DOM 一直钉在渲染进程里。

`idle_maintenance.recycle_session()`：对空闲标签页断开并重建 CDP 连接（**不刷新页面、不影响登录态**）：

```
maint 租约 acquire_for_command → 停止该标签页全局网络监听（等待退出）→ tab.disconnect()
→ tab._driver_init(target_id) → tab._get_document() → 重置 listener 驱动 / 会话级注入脚本标记
→ release（释放流程会恢复可见性模拟状态，全局监听随后自动重启）→ 恢复 last_used_at（维护不算“使用”）
```

触发条件（自上次回收后确有使用、且已空闲 ≥ `BROWSER_CDP_RECYCLE_IDLE_SEC`，满足任一）：

- 处理了 `BROWSER_CDP_RECYCLE_AFTER_REQUESTS` 个请求（默认 20；从未回收过的从会话创建起计数）；
- 距上次回收超过 `BROWSER_CDP_RECYCLE_INTERVAL_SEC`（默认 1800s）；
- `Memory.getDOMCounters` 节点数 ≥ `BROWSER_CDP_RECYCLE_DOM_NODES`（默认 150000，每 300s 查一次）。

重建失败时的处理：`_driver_init` 第一步就换新驱动，中途失败会留下半初始化的驱动（取不到驱动 → 所有操作
`PageDisconnectedError`；`Page.getFrameTree` / `_get_document` 失败 → `run_js` 报 `ElementLostError`、找元素空等 10s+）。
因此每次重试都从 `disconnect()` 开始完整重建，最多 `RECYCLE_REBUILD_ATTEMPTS=3` 次（间隔 0.5s/1s）。
**成功判定**不只看有没有异常：DrissionPage 的 `_get_document()` 失败时返回 False；另一线程（页面加载事件）正在读时返回 None；
读的过程中抛异常还会让 `_is_reading` 标志一直卡住，之后每次都直接返回 None——只看异常会把这些当成功，留下失效的文档根
（之后每次 `run_js` 都报 `ElementLostError`；这是真实浏览器压测中抓到的）。现在每次尝试先清掉卡住的标志，返回 False 视为失败，
返回 None 时等待并发读取完成，最后用 `run_js("return 1")` 验证标签页确实可用才算成功。仍失败时用
`run_js("return 1")` 验证连接：可用则照常释放；不可用则停掉残留驱动、从 DrissionPage 的 `ChromiumTab._TABS` 单例缓存
中移除该对象、`mark_error("cdp_recycle_failed")`。`release` 保留 ERROR 状态，该会话不会再被分配，随后由
`_cleanup_unhealthy_tabs` 按原有逻辑移出/隔离；重新扫描时同一目标会得到一个全新的标签页对象。

会话级状态的恢复：`Page.addScriptToEvaluateOnNewDocument` 注册属于 CDP 会话，回收后全部失效。
- 可见性模拟、音频捕获、Kimi 抓流：清掉“已注册”标记，释放 / 下次使用时自动重新注册；
- 命令 `run_js_file` 的新文档注入（含 `bootstrap_on_session_ready` 启动脚本）：回收成功后立即按注册表里的原文重新注册、
  更新 identifier（**不在当前页面重复执行**）；重新注册失败则清空启动脚本缓存，由命令引擎下一轮重新同步。
  否则注册表仍显示“已安装”，页面下一次刷新后这些韧性 / 监控脚本就不再注入。
- 全局网络监听：回收前停止，回收后的 release 会异步重启；监听空窗只有回收本身的约 1–1.5s，且只发生在空闲标签页上。
- 回收期间标签页处于维护占用（非工作流 BUSY），周期 page_check 会跳过这 1–1.5s，之后照常检查。

因条件不满足而跳过（监听停不下来、仍在监听、占用失败）时 60s 内不再尝试，避免挤掉冻结。
调度挂在 `TabPoolManager.run_watchdog_tick()`，在维护线程池中执行。

实测（真实 TabPoolManager + 有头 Chromium，`tests/e2e_idle_maintenance_headed.py`）：一次请求钉住 6007 个已移除的 DOM
节点，回收后降到 7 个，耗时约 0.8~1.5s，页面与全局监听均正常。

`SCHEDULED_RESTART_ENABLED` 在代码里本来默认 false，但 `.env.example` 写的是 true；已改为 false。

## P0-4 网络监听

- 全局网络监听代码默认即关闭（`GLOBAL_NETWORK_INTERCEPTION_ENABLED=False`）；本仓库的 `config/browser_config.json`
  为 arena 显式开启，未改动用户配置。
- 新增 `GLOBAL_NETWORK_INTERCEPTION_RES_TYPES`（默认 `XHR,Fetch,EventSource,Document`，`*` = 全部）：
  补丁版 DrissionPage 会对每个命中的包开启 `streamResourceContent` 并缓存响应体，放开到脚本/图片/样式会显著增加内存和 CPU。
  `tab.listen` 是全局监听与工作流监听共享的对象，让出时会把资源类型过滤恢复为“全部”，工作流里的 `listen.start()`
  也显式传 `res_type=True`，不会继承全局过滤。
- **建议**：把 `GLOBAL_NETWORK_INTERCEPTION_LISTEN_PATTERN` 从域名（如 `arena.ai`）收窄到真实 API 路径。
  请求方法未限制为仅 POST（部分站点的轮询接口是 GET）。
- `Network.enable` 缓冲上限：`CDP_NETWORK_MAX_TOTAL_BUFFER_MB=32`、`CDP_NETWORK_MAX_RESOURCE_BUFFER_MB=16`（0 = 不注入）。
  比报告建议的 16/8MB 大一档：生图接口返回的 data URI 可能超过 8MB，超过单资源上限会导致 `getResponseBody` 失败。
  **未设置 `maxPostDataSize`**：工作流/抓取依赖请求体（prompt）完整出现在 `requestWillBeSent` 里，截断会影响功能。
- `patch_drissionpage.py`：流式响应体改为分块列表，读取 `fullText` 时才 `join` 一次并缓存
  （追加与合并在同一把锁内完成：CDP 事件线程追加、监控线程读取可能同时发生，无锁时合并会丢掉并发追加的分块；
  检测标记 `_uwapi_stream_lock_v2` 会让已安装的旧版补丁自动升级）
  （旧实现每来一块就整串拼接，O(n²)）。`start.py` 启动时会执行补丁脚本，已打过旧版补丁的安装会自动升级。
- 生产环境应保持 `NETWORK_DEBUG_CAPTURE_ENABLED=false`（调试捕获会整份保存响应体）。

## P0-5 增量解析

- `LmarenaParser`：网络监听每次传入的是**不断增长的完整响应体**，旧实现每次都整段 `split` + 逐行 `json.loads`。
  现在把“已以换行结尾的完整行”的解析结果缓存下来，只解析新增部分；末尾未结束的半行每次重新解析、不入缓存；
  响应体不是上次的前缀扩展时回退为整段解析。
  - 实测 160KB 回答、600 次增长：**7.5s → 0.06s**（约 125 倍），逐次输出完全一致
    （`tests/test_lmarena_incremental_parse.py` 以旧的整段解析作为对照，80 组随机切分逐次比对）。
  - 顺带修复一个旧问题：旧实现对整段做 mojibake 修复，快照恰好截断在多字节字符中间时整段修复失败，
    乱码会混进回答（随机测试中 200 条流有 55 条出错）；完整行一定以换行结尾，按段修复不会出现这个问题。
- `_wait_for_stream_progress` / `_prepare_incremental_raw_response` 保留精确的 `startswith` 前缀判断：
  实测 160KB 前缀比较约 4µs，真正的开销在重复解析，而不是前缀比较；改成“长度 + 尾部”比较会降低正确性而几乎没有收益。

## P0-6 空闲冻结

空闲 ≥ `BROWSER_IDLE_FREEZE_AFTER_SEC`（默认 60s）、且**用户看不到**的标签页执行
`Page.setWebLifecycleState=frozen`（计时器、rAF、页面任务全部暂停）。被占用（`acquire` / `acquire_for_command`）
或 page_check / 保活唤醒（`_try_wake_tab`）时先恢复为 `active`。冻结与恢复都在同一把 lifecycle 锁里，
与并发占用不会交错。

**关键细节：** DrissionPage 在 `_driver_init` 中调用 `Emulation.setFocusEmulationEnabled(true)`，
Chromium 用“增加 capturer 计数”实现它，结果是**被控制的标签页永远被视为 visible**：后台标签页既不会被浏览器降频，
`setWebLifecycleState=frozen` 也会被静默忽略（实测冻结后计时器照常跑）。因此冻结流程是：

0. 先做**无副作用**的预判：`Browser.getWindowForTarget` 看窗口是否最小化；DevTools `/json` 列表按最近激活排序
   （DrissionPage 的 `latest_tab` 也依赖这一点），据此判断它是不是所在窗口当前显示的标签页。
   是窗口前台标签且窗口没最小化 → 直接跳过，**不切换焦点仿真**（切换会给页面发 `blur`/`focus`，
   有“聚焦即刷新”逻辑的站点会多请求一次）；
1. 否则在本会话上关闭焦点仿真；
2. 读取真实 `visibilityState`（直接调用 `Document.prototype` 上的原生 getter，绕过页面可见性模拟）；
3. 仍是 `visible` → 恢复焦点仿真，不冻结；这类“探测后发现可见”按 60s → 120s → … → 15min 指数退避，
   有新活动（请求 / 网络事件 / 唤醒）即重置；
4. `hidden` → 冻结；恢复时 `active` + 重新开启焦点仿真，请求期间 DrissionPage 的行为与原来完全一致。
5. **核验**：冻结前在页面挂一次性的标准生命周期事件 `freeze` / `resume` 监听（不可枚举属性），冻结后确认 `freeze`
   确实触发，否则撤销、30s 后重试。原因：CDP 冻结可能被 Chrome 静默忽略或在背后解除——实测发起冻结的 DevTools
   会话断开（CDP 回收）、另一会话附加（未打补丁的 DrissionPage 监听器）、用户切回标签页都会解除冻结。
   巡检每 10s 读一次标记，`resume` 触发过或文档已替换 → 记账改回“已恢复”，之后可重新冻结。

**冻结给占用让路：** `acquire` 先把状态置为 BUSY，再在 lifecycle 锁上等待解冻；冻结流程每一步（可见性轮询、核验轮询之间）
都检查状态，一旦被占用立刻收手、恢复原状，已发出 frozen 的则直接登记，由等锁的占用方立即解冻。请求不必等冻结流程走完
（单测：探测进行中 acquire 等待 < 0.25s）。

**浏览器挂后台 / 用户切回来：** Chromium 在标签页重新变为可见时（用户点回该标签、还原最小化窗口）会**自动解除**
CDP 冻结，页面立刻恢复（实测：计时器满速、真实点击正常处理，无需等我们介入）。巡检每 10s 检查一次已冻结标签页的
真实可见性（`sync_frozen_visibility`，冻结状态下同步 `run_js` 仍可执行），发现可见就把记账改回“已恢复”并重开焦点仿真，
之后再次隐藏且空闲时可重新冻结。

实测（有头 Chromium + openbox 窗口管理器）：

| 场景 | 结果 |
|---|---|
| 后台标签页 / 整个窗口最小化，空闲 | 冻结耗时 5–6 ms，计时器 50 次/s → 0 |
| 脚本 `acquire` 冻结的标签页 | 解冻 3–7 ms，+1–5 ms 真实可见；随后 `setTimeout(50)`=51 ms、rAF=1 ms、计时器满速 |
| 用户切回冻结的标签页 / 还原窗口（无 acquire） | 浏览器自动解冻，计时器满速，真实点击正常 |
| 用户正在看的空闲标签页 | 不冻结，判定 2 ms，页面收到的 blur/focus 事件：无（修复前每 60s 一对，判定 611 ms） |

并发压测（`tests/e2e_concurrency_headed.py`，2 核沙箱）：3 个独立进程 = 3 个浏览器（独立用户目录 / 端口 / 标签页池）同时跑，
每个 4 个标签页 × 8 轮，每轮先让后台标签页冻结，再 4 线程同时 acquire 并做：项目的 `cdp_precise_click`、
DrissionPage `ele.click` + `ele.input`、CDP 按键、`execCommand('insertText')`、`human_scroll`；页面事件处理全部经
`setTimeout + requestAnimationFrame` 记账。另有线程每 0.2s 强制让空闲标签页满足冻结条件并触发巡检（冻结与占用正面竞争），
第 3 个浏览器每轮整窗最小化。

| 指标 | 开启冻结 | 关闭冻结（对照） |
|---|---|---|
| 交互成功（事件全部被页面处理、isTrusted、输入未串标签页） | 192/192（两轮） | 96/96 |
| 解冻耗时 | 中位 1.4–2.3 ms，最大 48 ms | — |
| 单次完整交互 | 中位 2.1–3.2 s，最大 5.0 s | 中位 2.4–2.7 s，最大 4.7 s |
| 前台标签页：点击 → 页面 rAF 回调 | 中位 5–15 ms | 中位 5–7 ms |
| 后台标签页：点击 → 页面 rAF 回调 | 中位 90–570 ms | 中位 220–340 ms |
| 标记冻结的标签页计时器 | 0 | — |

后台标签页 rAF 慢是 Chrome 对非前台标签页渲染节流的原有行为（对照组同样如此），与冻结无关。

注意：

- **与命令 page_check 的关系**：page_check 每次检查前都先 `resume_if_frozen`（与唤醒开关无关，必定执行），检查永远在运行中的页面上进行；
  默认 `CMD_WAKE_TAB_BEFORE_PAGE_CHECK=true` 时每次唤醒都算“活动”，而冻结要求连续 60s 无活动，
  所以**被启用的周期 page_check 覆盖的标签页实际上不会被冻结**，检查行为与 2.10.0 相同；
  冻结只发生在没有 page_check 覆盖（scope / domain 不匹配、或关闭了周期检查）的空闲隐藏标签页上。
  工作流进行中的标签页（`check_while_busy_workflow`）是 BUSY，从不冻结。
  若设置 `CMD_WAKE_TAB_BEFORE_PAGE_CHECK=false`：标签页会在 60s 无活动后冻结、下一次 page_check 前被解冻，
  解冻后的第一次读取可能还是冻结前的页面内容，最多晚一个检查周期发现异常。
  另外，原有的 `_try_wake_tab` 唤醒结束时会关闭焦点仿真，后台标签页被唤醒后会被 Chrome 按后台页节流（计时器约 1 次/秒），
  这是 2.10.0 就有的行为，与冻结无关（真实浏览器对照：冻结 / 不冻结两条路径唤醒后的计时器速率一致）。
- 冻结期间页面自己的 JS 不运行，依赖页面脚本才会出现的异常（如站点轮询发现登录失效后弹出的提示）要等标签页恢复运行后才会出现：
  被 page_check 覆盖的标签页不受影响（不会被冻结）；未被覆盖的标签页会在下次被占用 / 唤醒时出现，由请求流程照常处理。
- 可见（用户正在看）的标签页永远不会被冻结；每个标签页一个独立窗口且都没最小化时，冻结基本不会触发。
- 冻结启用时，周期保活 `CMD_PERIODIC_KEEPALIVE_ENABLED` 默认改为 false；**显式设为 true 会周期性唤醒标签页，冻结基本失效**。
- 同一个 target 若被多个 DrissionPage 对象同时持有（每个都开了焦点仿真），真实可见性会一直是 visible，
  此时只是不冻结，不会出错。项目自身通过 `Chromium` 的单例标签页对象访问，不存在这个问题。

## P0-7 其他

- **进程模型**（`start.py`）：`BROWSER_PROCESS_MODEL=per-site` → `--process-per-site`；`limit:N` → `--renderer-process-limit=N`。
  报告实测省 10%~20% 内存；同站点标签页共用渲染进程后会互相拖慢，高并发时不要开。默认留空。
- **日志**：`.env.example` 的 `LOG_LEVEL` 改为 `INFO`（代码默认本来就是 INFO）。
  cute_translator 展示层翻译改为惰性：`SecureLogger` 只在 record 上打标记，handler 真正渲染时才翻译一次并缓存到 record，
  控制台和面板共用结果；翻译结果对 ≤512 字的消息做 LRU 缓存（1024 条）。
- **请求历史**：
  - 落盘去抖 `REQUEST_HISTORY_SAVE_DEBOUNCE_SEC`（默认 5s），窗口内多次更新合并为一次整文件重写；进程退出（atexit）时强制刷盘；
  - 去掉每次写入的 `fsync`（保留临时文件 + `os.replace` 原子替换，文件不会写坏）；统计文件同样处理；
  - 默认最多 200 条（`REQUEST_MONITOR_MAX_RECORDS`，代码默认已是 200）；
  - 长文本清洗不再“整段跑正则再截断”：边替换边输出，凑够上限即停止扫描；图片后面的文字照常保留，结果与旧实现的前缀一致
    （60 组随机对照）。1M 字 prompt：23.7ms → 1.8ms。
- **面板统计**：
  - 默认不再调用 `memory_full_info()`（USS 需要读 smaps / 逐页扫描）。`PANEL_MEMORY_METRIC=fast`（默认）使用
    `memory_info()` 里已有的字段：Windows 用 `private`，Linux 用 `rss - shared`，其他平台用 `rss`。单进程采样 0.76ms → 0.012ms，
    数值与 USS 接近（直接用 RSS 会把 Chrome 多进程共享内存重复累加，明显虚高）。`uss` / `rss` 可切回；
  - 统计结果缓存由 2s 调整为 5s；运行中/排队请求数与累计 token 仍然每次实时读取。

---

## 新增 / 调整的环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `STREAM_PAGE_SNAPSHOT_ENABLED` | true | P0-1 页面内快照 |
| `CDP_HYGIENE_ENABLED` | true | P0-2 总开关 |
| `CDP_RELEASE_JS_OBJECTS` | true | 释放 run_js 对象结果 |
| `CDP_NETWORK_MAX_TOTAL_BUFFER_MB` | 32 | Network.enable 总缓冲（0 = 不注入） |
| `CDP_NETWORK_MAX_RESOURCE_BUFFER_MB` | 16 | 单资源缓冲（0 = 不注入） |
| `GLOBAL_NETWORK_INTERCEPTION_RES_TYPES`（browser_config） | `XHR,Fetch,EventSource,Document` | 全局监听资源类型，`*` = 全部 |
| `BROWSER_IDLE_FREEZE_ENABLED` | true | P0-6 |
| `BROWSER_IDLE_FREEZE_AFTER_SEC` | 60（最小 15） | |
| `BROWSER_IDLE_FREEZE_REQUIRE_HIDDEN` | true | false 时连前台标签也冻结（用户会看到页面停住，不建议） |
| `BROWSER_CDP_RECYCLE_ENABLED` | true | P0-3 |
| `BROWSER_CDP_RECYCLE_IDLE_SEC` | 30（最小 10） | |
| `BROWSER_CDP_RECYCLE_AFTER_REQUESTS` | 20（0 = 不按请求数） | |
| `BROWSER_CDP_RECYCLE_INTERVAL_SEC` | 1800（0 = 不按时间） | |
| `BROWSER_CDP_RECYCLE_DOM_NODES` | 150000（0 = 不按节点数） | |
| `BROWSER_CDP_RECYCLE_DOM_CHECK_SEC` | 300 | |
| `BROWSER_PROCESS_MODEL` | 空 | `per-site` / `limit:N` |
| `REQUEST_HISTORY_SAVE_DEBOUNCE_SEC` | 5 | 0 = 不去抖 |
| `PANEL_MEMORY_METRIC` | fast | `fast` / `uss` / `rss` |
| `SCHEDULED_RESTART_ENABLED`（.env.example） | false | 原 .env.example 为 true |
| `LOG_LEVEL`（.env.example） | INFO | 原 .env.example 为 DEBUG |

## 测试

新增测试（均可在无显示环境运行；需要真实浏览器的用例在找不到 Chromium 时自动跳过，`tests/_real_browser.py`）：

- `test_stream_snapshot.py`、`test_cdp_hygiene.py`：快照与旧路径逐项对照、RemoteObject 释放、`Network.enable` 上限、懒拼接补丁；
- `test_idle_maintenance.py`：冻结/恢复/回收/调度的单元测试 + 真实浏览器回收；前台标签页不切换焦点仿真（页面不收到 blur/focus）、
  可见退避、用户切回后状态同步、冻结核验（未生效回滚 / 被外部解除后重新冻结，含真实浏览器回收场景）、冻结给占用让路、
  回收失败重试与隔离（真实 TabPoolManager：暂时失败重建成功；持续失败不再分配、重新扫描得到可用的新对象）、
  回收后命令启动 JS 文件仍在刷新时注入且不重复执行、冻结的后台标签页经 page_check 读取路径先解冻并检测到页面渲染的异常；
  有头冻结用例需要 X 显示（`xvfb-run`），否则跳过；
- `e2e_concurrency_headed.py`：手动并发压测（见 P0-6），多浏览器并发就同时起多个进程；
- `test_network_res_types.py`：资源类型解析与真实浏览器过滤（图片/脚本不再进入监听）；
- `test_lmarena_incremental_parse.py`、`test_request_history_perf.py`、`test_log_cute_lazy.py`、
  `test_panel_stats_perf.py`、`test_start_process_model.py`；
- `tests/e2e_idle_maintenance_headed.py`：手动端到端脚本（真实 TabPoolManager），期望输出
  `RESULT recycled | freed | froze | resumed`。

全量测试与改动前基线对比没有新增失败（基线中已有的失败与本次改动无关）。
