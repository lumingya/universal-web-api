# R2-1 BrowserDriver 接口与 R2-6 进程模型：设计与分阶段计划

> 状态：设计定稿，尚未实施。两项改动都会触及浏览器层的每一条路径，必须在用户本机用真实浏览器逐阶段回归。
> 为此本文把工作拆成可以独立合并、独立回滚的小步。现状数据来自 2026-09-27 对 `dev/roadmap-phase0-2` 的统计。

## 1. 为什么要做

- **许可证风险**：DrissionPage 的许可只允许非商业学习使用，而且违规即自动终止（路线图 §4）。只要浏览器调用散落在 50 个文件里，替换它的成本就高到做不了。
- **可测试性**：现在测试浏览器行为，只能起真实 Chrome，或者针对每个调用点单独打桩。有了统一接口，就可以用一个内存假驱动覆盖大部分逻辑。
- **稳定性**：浏览器卡死、崩溃会拖垮整个 API 进程（重启守护的存在就是证据）。进程拆分以后，浏览器侧出问题只需要重启 worker。

## 2. 现状清单（直接调用 DrissionPage API 的位置）

| 类别 | 调用数 | 说明 |
|------|------:|------|
| `run_js` / `run_js_loaded` | 163 | 页面内脚本：取快照、输入、点击、观察者等 |
| `run_cdp` / `_run_cdp` | 55 | CDP 命令：Input.*、Page.*、Runtime.*、Network.* |
| `ele` / `eles` | 30 | 元素查找（DrissionPage 定位语法） |
| `actions.*` / `input()` | 33 | 键鼠动作链、文本输入 |
| `listen.*` | 13 | 网络监听（集中在 `network_monitor.py`） |
| `from DrissionPage` | 7 个文件 | 类型与异常的直接导入 |

共涉及 **50 个文件**。调用最集中的是：

- `workflow/text_input.py`（34 处）
- `arena_gpt_image_command.py`（19 处）
- `command_engine_actions.py`（17 处）
- `browser/media.py`、`workflow/executor_actions.py`、`workflow/executor_interaction.py`（各 15 处）
- `media_extractor.py`、`workflow/executor.py`（各 13 处）
- `human_mouse.py`（10 处）

## 3. R2-1：接口设计

```python
class TabDriver(Protocol):           # 一个标签页（对应现在的 DrissionPage tab 对象）
    tab_id: str
    url: str
    def run_js(self, script: str, *args, timeout: float | None = None, as_expr: bool = False) -> Any: ...
    def run_cdp(self, method: str, **params) -> dict: ...
    def find(self, locator: str, timeout: float = 0) -> ElementHandle | None: ...
    def find_all(self, locator: str, timeout: float = 0) -> list[ElementHandle]: ...
    def navigate(self, url: str, timeout: float | None = None) -> None: ...
    def reload(self, ignore_cache: bool = False) -> None: ...
    def screenshot(self, path: str | None = None, full_page: bool = False) -> bytes: ...
    def listen(self, patterns: list[str]) -> NetworkListener: ...        # 流式响应捕获
    def input_text(self, text: str) -> None: ...                         # 键盘输入（CDP Input.insertText 等）
    def mouse(self) -> MouseDriver: ...                                  # 点击、拖动、滚动

class BrowserDriver(Protocol):       # 浏览器进程（对应现在的 Chromium / BrowserCore 连接）
    def tabs(self) -> list[TabDriver]: ...
    def new_tab(self, url: str | None = None, *, context_id: str | None = None) -> TabDriver: ...
    def close_tab(self, tab_id: str) -> None: ...
    def create_context(self) -> str: ...     # 站点独立 Cookie（Target.createBrowserContext）
    def dispose_context(self, context_id: str) -> None: ...
```

- 定位语法沿用现有规则，与 `ElementFinder` 和 `adapter_health.to_locator` 一致：
  - `css:`、`xpath:`、`tag:`、`@属性` 原样传递；
  - 以 `/` 或 `(` 开头的按 XPath 处理；
  - 其余按 CSS 处理。

  DrissionPage 实现可以直接透传；其他实现在接口层做转换。
- 异常统一为三类：`DriverDisconnected`、`ContextLost`（例如 “Cannot find context with specified id”）、`DriverTimeout`，替代现在各处对 DrissionPage 异常文本的字符串匹配。

## 4. R2-1：分阶段迁移（每一步独立提交，并在本机做真实浏览器回归）

1. **接口与 DrissionPage 实现**：新增 `app/core/driver/`，包含 `base.py`（Protocol 与异常）、`drission.py`（包装现有 tab 对象，只转发调用、翻译异常）和 `fake.py`（内存假驱动，供单测使用）。
   - `TabSession` 增加 `driver` 属性（懒创建）。
   - 这一步不改任何调用点，行为零变化。
2. **只读路径**：`adapter_health`、`stream_snapshot`、`media_extractor` 的快照读取。这些只查询、不交互，回归风险最低。
3. **CDP 集中点**：`human_mouse`、`browser_profile_identity`、`tab_pool_parts/pool_contexts` 的 run_cdp。
4. **交互路径**：`workflow/text_input`、`executor_actions`、`executor_interaction`、`attachment_upload`。
   - 这是最关键的输入、点击、上传链路，每个子文件单独一步。
   - 本机要跑 `test_text_input_atomic`、`test_attachment_browser`、真实浏览器用例，并对常用站点做手工冒烟。
5. **网络监听**：`network_monitor` 的 `listen.*`。流式捕获依赖它，最后迁移。
6. **收口**：新增测试，保证 `app/` 下除 `app/core/driver/drission.py` 以外不再直接导入 DrissionPage，也不再直接调用 `.run_js(` 或 `.run_cdp(`，即便未来加新代码也不会回退。
   - 完成后，替换为 Playwright（CDP 连接模式）或直接 CDP 的实现，只需要新写一个驱动文件。

## 5. R2-6：进程模型

目标：API 进程（FastAPI 与协议转换）与浏览器 worker 进程（BrowserCore、TabPool、工作流执行）分离。

```
客户端 ─HTTP→ API 进程（uvicorn）──本地 IPC──→ 浏览器 worker（单进程，持有 CDP 连接与标签页池）
                  ▲                              │
                  └──── 流式事件（ChatEvent）─────┘
```

- **IPC**：优先用本机回环 HTTP 或 WebSocket（worker 监听 127.0.0.1 的随机端口，令牌通过环境变量传递）。与 multiprocessing Queue 相比，它跨平台行为一致，Windows 上也不受 spawn 限制，还能单独调试 worker。
  - 流式输出直接传 R2-2 的 `ChatEvent`，这正是 R2-2 把事件类型化的原因之一。
- **边界**：API 进程只保留协议转换、鉴权、指标、配置读写；与执行相关的一切（`get_browser()`、`request_manager` 的执行部分、`command_engine` 的调度）都进入 worker。
- **生命周期**：由 `start.py` 拉起两个进程。worker 崩溃时，API 进程对进行中的请求返回 503，并请求重启 worker，不影响 API 进程本身，也不丢配置。
  - 现有的「定时重启守护」可以简化为只重启 worker。
- **分阶段**：
  1. 先在同一进程内，把执行入口收敛到一个可以序列化的调用面：输入是 `ChatJob` 加 `ChatRequest`，输出是 `ChatEvent` 流。
  2. 把这个调用面实现为 worker 的 HTTP 端点，API 进程通过配置开关 `UWAPI_WORKER_MODE=inproc|process` 选择；默认仍是进程内。
  3. 本机长时间回归通过后，再把默认改为 process。
- **依赖**：R2-1 的第 1、2 步完成后再做更稳妥，因为 worker 内部的浏览器调用最好先经过统一接口。

## 6. 需要用户配合的地方

- 每个迁移步骤合并前，在本机跑一遍真实浏览器用例；工作流相关步骤还需要对常用站点手工冒烟。改动不涉及账号，只需要已登录的受控浏览器。
- R2-6 切换默认之前，建议先以 process 模式日常使用一周。
