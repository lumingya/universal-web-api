# 路线图阶段 0–2 开发日志（分支 `dev/roadmap-phase0-2`）

> 用途：防止上下文压缩或沙盒重启后丢失信息。每完成一个子任务，就更新本日志，然后 commit 并 push 到本分支。
> 恢复工作时，先读「当前状态」和「§3 任务清单」，再看 `git log -5 origin/dev/roadmap-phase0-2`。
> 分析与路线图原文见 `docs/roadmap/ANALYSIS-AND-ROADMAP-2026-09.md`，R 编号以它为准。

## 当前状态（随时更新）

- main 冻结，只推本分支。本地环境（Portal）与推送链路见 §2.8。
- ✅ 阶段 0、阶段 1 完成；阶段 2 的 R2-1～R2-8 主线实现完成。
- R2-6 进程模式仍为实验性、默认 `inproc`；已补上 worker 意外退出后的自动重启与指数退避。重启窗口内的在途请求仍可能失败，切换默认前需在专用副本做长时间 soak test。
- 本轮本机验证（Python 3.13）：`tests/test_worker_mode.py` 9 passed；worker + 异常棘轮 + 启动模型组合 21 passed；Ruff 与 compileall 通过；Tailwind 真实浏览器等价性/面板冒烟 2 passed。
- ▶ 下一步：阶段 0–2 没有尚获授权的代码项。R0-3 CI、R0-4 自动发包及删除 `3.7.5` 标签仍按 §1 暂缓；R0-7 由用户发布；R0-8 的 GitHub 规则/账号设置由用户手工完成。阶段 3 或切换 worker 默认值另行确认。

## 1. 用户决策（2026-09-26，必须遵守）

| 事项 | 决定 |
| --- | --- |
| main 分支 | **冻结**。我不再推 main，所有成果只推 `dev/roadmap-phase0-2`（必要时另开 `dev/...` 子分支），由用户审阅后自己合并。 |
| 范围 | 阶段 0–2 全部完成。3.0.0 由用户统一发布，所以 **R0-7 不做**。 |
| 阶段 2 力度 | **全面落地**：驱动接口全量接管、巨型类拆分、进程拆分、前端构建切换都做。用户在本地用真实浏览器回归，发现问题我再修。 |
| R1-2 站点配置 | **彻底拆分**：运行时改用 `config/sites/<域名>.json`，首次启动时自动迁移旧的 `config/sites.json`。 |
| R1-4 解析器样本 | **先用合成样本**，在测试和夹具里标注 `synthetic`，以后再换成真实的脱敏样本。 |
| 发布与仓库操作 | **暂缓**。CI（R0-3）、草稿发布工作流（R0-4）、删除 `3.7.5` 标签、删除 `fix/review-p1-p2-b` 分支，都要等用户另行确认。用户的原话：沙盒限制太多，想先看看能不能借助他的本地环境来做。 |
| 长期规则（沿用） | 每完成一个子任务就 commit + push。沙盒里不装 playwright 或 Chromium，不跑重型浏览器 E2E。不保留 png。临时文件用完即删。配额约 128MB / 10 万个文件。 |

## 2. 本地环境桥接评估：Portal（github.com/s3hq4y/portal）

### 2.1 是什么

Portal 把用户本机的**一个文件夹**发布成公网 MCP 端点：`https://<隧道域名>/mcp/<令牌>`。有两种形态：

- **VS Code 扩展**：推荐。Releases 里有现成的 `portal-1.1.1.vsix`。
- **Electron 桌面客户端**：实验性，需要自己编译，而且没有新的原生文件工具。

隧道方面，只有 **ngrok 预留域名** 经过作者实测，Cloudflare 和自定义隧道都标为实验性。

提供的能力：

- `run_command`：前台命令，默认 120 秒超时，最长 600 秒。
- `start_command` / `read_command` / `stop_command`：后台任务，最多 4 个并发，单个最长 24 小时，日志写在工作区的 `.portal/logs/`。
- `file_transfer_info` 以及文件 HTTP API `/files/<令牌>/…`：下载、上传、打包、解包，单次上限 64MiB。
- VS Code 扩展额外提供 `read_file` / `write_file` / `apply_patch` / `list_files` 和分块上传。据该仓库 `arena.md` 记录，这批新工具提交时没有编译或测试过，所以只作辅助用。

### 2.2 可行性（已验证的部分）

- 沙盒可以直连 ngrok 和 Cloudflare 边缘：请求 `*.ngrok-free.dev` 约 0.1 秒返回 404，说明出站 HTTPS 是通的。
- 协议是 JSON-RPC 2.0 over HTTP POST（MCP Streamable HTTP，协议版本 2024-11-05），可以直接用 curl 或 Python 在 bash 里调用。
  - 先 `initialize`，拿到 `Mcp-Session-Id` 后在后续请求里带上。
  - 用 ngrok 免费域名时要加请求头 `ngrok-skip-browser-warning: 1`。
- Windows 下默认 shell 是 PowerShell 5.1，以 `-NoProfile` 启动，Portal 会自动设置 UTF-8。也可以选 `cmd`、`pwsh` 或 `bash`（Git Bash）。

### 2.3 能解锁什么（对应路线图）

- **真实浏览器 E2E**：现在排除在外的 10 个浏览器测试文件、阶段 2 的驱动接口和进程拆分回归，都能跑。
- **Windows 专属验证**：`start.bat` 快速路径、多个 Python 版本（`py -3.10` / `-3.12` / `-3.13`），对应 R0-2 的 N1 修复。
- **发布包和更新器端到端**：可以在副本上构建 zip、比对清单、走一遍「下载 → 替换 → 重启」（R0-4，对应 #25）。
- **R2-8 前端改动的视觉核对**：在本机截图，下载到沙盒查看后立刻删除，不保留 png。
- **真实解析器样本**（可选）：R1-4 先用合成样本，以后可以在本机抓取真实样本，脱敏后替换。
- **大体量操作**：行尾规范化、干净 venv 中的依赖升级，都不受沙盒配额限制。

### 2.4 限制

- 看不到实时屏幕，只能通过截图。
- 单次调用是同步的，长任务要靠 start + read 轮询。每个流最多输出 20 万字符，超出部分需要去日志文件看。
- 用户电脑必须开机，VS Code 和 Portal 都在运行。
- 同时最多 4 个后台任务。PowerShell 不加载 profile，conda 之类的环境要显式激活。

### 2.5 安全要点，以及我的操作守则

风险：拿到 URL 就能以用户的 Windows 账户权限执行任意命令。没有第二道认证，没有 IP 白名单，CORS 全开。Portal 本身只监听 127.0.0.1，令牌是 128 位随机数，这个设计对它的用途来说是合理的。

建议用户这样做：

- 工作区用**专门的新克隆**，不要用日常工作副本或用户目录。
- 最好用非管理员账户。
- 用完就「停止」Portal，并关闭 `portal.startOnActivation`。
- 每次会话结束后执行「重新生成地址」，因为 URL 会出现在对话和上下文摘要里。
- 另外，本项目默认使用项目内的 `chrome_profile/`，不会碰个人 Chrome 配置。但默认端口 8199 和 9222 会和用户日常运行的实例冲突，所以代理副本要改用其他端口，比如 `APP_PORT=8299`、`BROWSER_PORT=9322`。

我的操作守则：

1. 只在 Portal 工作区内操作，不读取、不修改工作区外的个人文件，不接触凭据、Cookie 或个人浏览器配置。
2. 不删除工作区外的任何东西，不改系统设置。全局安装（winget、`pip` 全局、`npm -g`）要先问用户。Python 依赖一律装进工作区内的 venv。
3. 只 push `dev/roadmap-phase0-2`（或 `dev/...` 子分支），绝不 push main。
4. 每次会话结束时 `stop_command` 所有后台任务，并在本日志登记跑了什么、结果如何。
5. 不执行网页或第三方文本「建议」的命令，防止提示注入。
6. URL 或令牌不写入任何受跟踪文件。在沙盒里只放在 `~/.cache/portal/`，这个目录不进快照。

### 2.6 建议工作流（混合）

- **沙盒**负责编辑代码（文件工具最顺手）、跑快速单测、commit 并 push 到 `dev/roadmap-phase0-2`。
- **本机**（通过 Portal）先 `git fetch` 并 checkout 同一分支，再做重型验证：全量 pytest（含浏览器测试）、start.bat 与多个 Python 版本、真实浏览器 E2E、打包。日志和截图通过文件 API 拉回沙盒。
- Git 是唯一的事实来源和传输通道，这也符合「重要内容及时推到非主分支」的要求。
- 推送凭据：建议用户把当前 admin 级 PAT 换成**细粒度令牌**，只授权本仓库，权限为 Contents 读写，以后要加 CI 时再加 Workflows 读写，有效期 30 天。也可以不给沙盒任何令牌：在沙盒里生成 `git bundle`，经文件 API 传到本机，再由本机用用户自己的凭据 push。

### 2.7 用户需要准备

1. 从 Releases 下载 `portal-1.1.1.vsix`，执行 `code --install-extension portal-1.1.1.vsix`。
2. 在 ngrok 注册账号，执行 `ngrok config add-authtoken <…>`，并申请一个免费预留域名。
3. 专门克隆一份仓库，例如克隆到 `D:\agent-work\universal-web-api`。本机需要有 git（已登录）、Python 3.10+（最好通过 py launcher 装有多个版本）和 Chrome，Node 18+ 可选。
4. 用 VS Code 打开这个文件夹，依次执行：
   - 「Portal：设置隧道提供方」→ 选 ngrok 预留域名；
   - 「设置 ngrok 域名」；
   - 「启动」；
   - 「复制 URL」，把 URL 发给我。
5. `.portal/` 目录由我在那份克隆的 `.git/info/exclude` 里忽略，不改仓库里的 `.gitignore`。

### 2.8 实际接入情况（2026-09-26 更新，重启或压缩后先看这里）

**Portal 工作区根目录**：`C:\Users\QIU\Desktop\useful\projects\工作区`（用户调整后的范围）。

- `普遍反代\新测试版`：用户的**日常工作副本**（main 分支，平时在 8199/9222 运行服务）。**只读，不改文件、不切分支、不占用或重启 8199/9222。**
  - 我之前在里面建的 worktree、venv、`.portal`、`.git/info/exclude` 条目和本地 dev 分支引用都已清理，恢复原状。
- `普遍反代\universal-web-api`：旧的发布解压版，不是 git 仓库，里面有 `.env`。不碰。
- `comfy-comic-studio\`、`普遍反代\AstrBot*`：其他项目。不碰。
- **我的专用目录 `普遍反代\arena-dev\`**（完全独立，可整体删除）：
  - `universal-web-api\`：独立克隆（origin = GitHub），停在 `dev/roadmap-phase0-2`，提交身份为 AI Review Agent (Arena)。**不在这里直接改代码**，只接收沙盒送来的 bundle 并 push。
  - `venv313\`（requirements、requirements-dev、ruff、playwright；不下载 Playwright 自带的浏览器，测试回退到本机 Chrome）和 `venv310\`。
  - `tools\run-tests.ps1 -Py 313|310 [-PytestArgs @(...)]`：在克隆里跑 pytest。
  - `inbox\`：接收 bundle 的中转目录。
- **推送链路（沙盒没有 GitHub 凭据）**：沙盒执行 `~/tools/ship.sh`，它会：
  1. 把 `origin/dev..dev` 打成 git bundle；
  2. 通过 Portal 文件 API 上传到 `arena-dev\inbox`；
  3. 在本机克隆里 `git pull --ff-only <bundle>`，再用用户自己的 Git Credential Manager（lumingya）执行 `git push origin dev/roadmap-phase0-2`；
  4. 沙盒 fetch 后核对远端 HEAD。

  **只推 dev 分支，绝不推 main。** 沙盒的 `.git/config` 不会进快照，ship.sh 每次都会补回 remote、身份和 upstream。
- 本机环境：Win11 26200，PowerShell 5.1，非管理员账户 QIU；Python 3.13.6、3.10.2（`py -3.10`）、3.12.12（uv）；Node 22.18；Chrome 153；C 盘剩余约 18GB。本机有 `gh` CLI 登录凭据，但发布相关操作暂缓，不要用。
- 注意事项：
  - PATH 里的 `bash` 是 WSL 的，但没有安装发行版，不可用。
  - PowerShell 下 `git ... 2>&1` 会产生 NativeCommandError 噪音，改用 `cmd /c "git ... 2>&1"`。
  - 用 `.bat` 做 python 桩时，不加 `call` 调用不会返回调用方；要编译 exe 桩（`Add-Type -OutputType ConsoleApplication`）。
  - Portal 重启（比如用户调整工作区）会杀掉所有后台任务。
- 协作规则文件 `agent_collaboration_rules.md` 是 Codex 与 Antigravity 之间的 `agent-bridge` 协议，两者自 2026-08 起空闲。**不要运行 `--read-msgs --mark-read`。**
- Portal 客户端：沙盒里的 `~/tools/portal.py`。URL 只存放在 `~/.cache/portal/url`，这个目录不进快照；沙盒重启后要从对话里重新 `seturl`，或请用户重新提供。

## 3. 任务清单（R 编号见路线图 §6）

### 阶段 0：止血与发布工程

- [x] **R0-1** 合并 `fix/review-p1-p2-b` 进 main（`29d9685`）。删除该分支暂缓。
- [x] **R0-2** N1 修复，最低 Python 统一为 3.10（`ef423b6`、`85abd38`）。实机 3.10.2 结果：693 passed / 72 skipped；3.13.6 结果：702 passed / 63 skipped。
- [ ] **R0-3** CI（GitHub Actions）——**暂缓**，等用户确认。
- [ ] **R0-4** 自动发包，以及删除 `3.7.5` 标签——**暂缓**，等用户确认。
- [x] **R0-5** 测试入库（`de48ce3`）：找回 12 个文件；tests/ 改为黑名单；新增 `pyproject.toml`（pytest markers、ruff 基线）；新增 `_playwright.py` 回退到本机浏览器；`_real_browser` 支持 Windows 和 macOS。
- [x] **R0-6** 行尾统一（`b263781`，纯行尾变更，已登记到 `.git-blame-ignore-revs`）：*.bat/*.cmd/*.ps1 检出为 CRLF，其余文本一律 LF。
- [—] **R0-7** 发布 3.0.0——不做，由用户发。
- [x] **R0-8** 已写操作指南 `docs/roadmap/REPO-SECURITY-GUIDE.md`；具体设置需要用户在 GitHub 上完成。

### 阶段 1：适配器工程化

- [x] **R1-1** Schema v1 已完成（`app/services/config/site_schema.py`）。加载时只告警；保存或导入时拒绝的逻辑放到 R1-6/后续再接入。
- [x] **R1-2** 彻底拆分已完成（见 §4）：`config/sites/` 目录、自动迁移、`index.json`、更新器逐站点合并、备份导入导出、官方对比读取 index。
- [x] **R1-3** 适配器更新通道完成：发布整包时，更新器逐站点合并（`4d291c4`）；运行时可在线检查和应用（`c7588dc`，`/api/adapters/updates[/apply]`）。面板入口留到 R2-8。
- [x] **R1-4** 金标测试框架完成（`0d75621`）：8 个此前零测试的解析器都有了合成样本，每个样本按三种方式喂入，共 28 项测试。其余已有测试的解析器可以按需补样本。
- [x] **R1-5** 协议一致性测试完成（`a7c22f0`）：用官方 openai 3.x 和 anthropic 1.x SDK 覆盖 chat.completions、Responses 与 Messages 三种接口的流式和非流式调用、422 错误映射和 models 列表，共 8 项测试。
- [x] **R1-6** 健康巡检完成（`ee42d7c`，`/api/adapters/health`）：只查询 DOM，按 broken、degraded、healthy、unreachable 分级。
- [x] **R1-7** 选择器韧性完成（`695f790`，`/api/adapters/lint`）：静态脆弱度打分，巡检时给出回退命中建议。内置配置共 193 个选择器，没有低于 70 分的。

### 阶段 2：架构演进（全面落地）

- [x] **R2-1** 完成：业务代码不再直接调用 DrissionPage，统一经由 `app/core/driver/`。
  - 第 1～4 步：`94c5dc5` 到 `3dfcce8`；收口：`73c0c84`。
  - 收口一次改写了 222 处，覆盖元素级（`as_element`）、浏览器级（`browser_driver`）、键鼠动作链、网络监听；DrissionPage 内部类统一经 `drission_internals.py` 访问。
  - 棘轮基线归零，并有断言：除驱动包外不允许任何直接调用。
  - 实机验证：`73c0c84` 在 3.13 上 1340 passed / 0 failed；3.10 上唯一失败是代理时序问题，已修复，见下方 `fa20a37`。
- [x] **R2-2**（`5568ce2`）：新增 ChatJob（协议、路由、请求 ID）与类型化 ChatEvent，执行层输出统一由共享解码器处理，并按协议计量。
  - 两个成熟的渲染状态机没有重写，详见 `docs/architecture/chat-pipeline.md`。OpenAI、Anthropic、Responses 都先翻译成它，协议层保持轻薄。
- [x] **R2-3** 三个巨型类全部完成拆分：
  - TabPoolManager：9 个 mixin（`e4c0c2d`，4567→273 行）；
  - CommandEngine：6 个 mixin（`35df9c8`，3870→643 行）；
  - ConfigEngine：6 个 mixin（`f4164da`，3321→298 行）。
  - 用 AST 核对：全部方法语法树与拆分前一致，逻辑零改动。
- [x] **R2-4**（`1f6b7d8`）：单一事实源改为自研的登记表 `app/core/settings_registry.py`，不引入 pydantic-settings 依赖，因为运行时读取仍分散在 136 处 `os.getenv`，逐步迁移到 `get_setting()` 即可。
  - 共 155 项；`.env.example` 由它生成；面板保存时按它校验；代码、登记表、面板三方一致由测试守护。
- [x] **R2-5**（`6749545`）：请求历史与累计统计改存 SQLite（WAL），增量写入，旧 JSON 自动导入。
  - 命令结果目前只存在内存里（原本就没有持久化），因此没有需要迁移的内容；如需持久化，可以直接写入同一个 RuntimeStore。
- [x] **R2-6**（`68e8985` + 本轮补齐）：`UWAPI_WORKER_MODE=process` 下由 API 进程启动并监控 worker；意外退出后自动重启，启动失败按 1–30 秒指数退避重试，正常关闭时先停监控再关闭 worker。默认仍为 `inproc`，在途请求可能在重启窗口失败；长时间 soak test 仍待用户本机确认。内部健康端点改为回环 + token 鉴权。
- [x] **R2-7**（`ea8d3cb`、`2da24c1`、`3ab111d`）：`/metrics`、`X-Request-ID` 上下文与请求历史关联、日志上下文标记均已接入；宽泛异常捕获从 1511 处收窄到 1433 处，并由只减不增的棘轮测试守护。
- [x] **R2-8** 完成：
  - 维护面板（`f922557`）；
  - Tailwind 预编译（`2e37530`）：407KB 运行时脚本换成 90KB CSS，有逐元素等价性测试；
  - 拆分 dashboard-methods.js 为 11 个文件，无损校验（`9cccde3`）；
  - 除 Vue 外 38 个脚本改为 ES 模块；
  - 新增 Node 端单测（`node --test`，已纳入 pytest）。

## 3.5 设计记录：R1-1 Schema 与 R1-2 站点配置彻底拆分（实施前定稿，改动时同步更新）

**现状调研（2026-09-26）**

- `app/services/config/engine.py` 的 `ConfigEngine` 读写 `config/sites.json`（常量 `CONFIG_FILE`，可用环境变量 `SITES_CONFIG_FILE` 覆盖）和 `config/sites.local.json`（本地覆盖），按 mtime 热重载。
- 其他引用点：
  - `app/api/system.py`：完整备份的导出和导入；
  - `app/api/config_compare_support.py`：用 `ConfigConstants.CONFIG_FILE` 的相对路径拉取 GitHub main 上的 `config/sites.json`，失败时回退缓存；
  - `app/services/arena_model_catalog.py`；
  - `app/utils/site_rules.py`（只读 sites.local.json）；
  - `scripts/upgrade_gemini_deepseek.py`；
  - `updater.py`：遇到 `SITES_CONFIG_PATH` 时调用 `merge_sites_file` 合并（同一站点优先保留本地并补入新字段，本地独有的保留，发布独有的新增）；
  - `update_preserve.py` 里的选项 `sites_config`；
  - 另有 5 个测试直接读 `config/sites.json`。
- 旧版更新器**只拷贝、不删除**：新发布包里没有 `config/sites.json` 时，用户原有文件原样保留；`config/sites/*.json` 会被当作普通文件写入。
- 旧版客户端的「与官方对比」拉不到 main 上的 `config/sites.json` 时，会回退到最近一次缓存并提示 stale，属于可接受的降级。

**定稿方案**

- 新的事实来源是 `config/sites/` 目录，环境变量 `SITES_CONFIG_DIR` 可覆盖。每个站点一个文件，全局配置是 `_global.json`。文件外层包一个信封：

  ```json
  {"schema_version": 1, "site": "<域名或 _global>", "adapter_version": "…", "min_app_version": "…",
   "last_verified": "YYYY-MM-DD", "config": { …与原 sites.json 中该节点完全相同… }}
  ```

  加载时解包 `config`，拼回与原来**完全相同**的 `sites` 字典，所以应用其他部分无需改动。
- **自动迁移**：启动时如果发现旧的 `config/sites.json`：
  - 目录为空：把旧文件拆成单站点文件；
  - 目录已有文件（通过更新器升级的场景）：按 `merge_site_records` 的语义合并，即优先保留本地并补入新字段，与现有更新语义一致；
  - 两种情况最后都把旧文件改名为 `sites.json.migrated-<时间戳>.bak`，不删除。
- **保存**：只重写内容有变化的站点文件（临时文件 + `os.replace`），保留信封里的元数据；已删除的站点移到 `config/sites/.trash/`。热重载改为基于目录签名（文件名、mtime、大小）。
- **分发与更新通道（R1-3 共用）**：
  - 仓库内的 `config/sites/index.json` 由 `scripts/build_sites_index.py` 生成，记录每个文件的 sha256、adapter_version 和 min_app_version。由测试保证它与目录内容一致。
  - 「与官方对比」和适配器更新都改为：先拉 index，再按 sha256 校验拉取单站点文件，并逐文件缓存。
- **更新器**：`config/sites/<站点>.json` 逐文件合并，语义与 `merge_sites_file` 相同。`update_preserve.py` 新增 `config/sites/` 选项；旧选项 `config/sites.json` 保留兼容。
- **备份**：导出格式不变，仍是合并后的 `sites` 字典，所以旧备份照样能导入；导入时写回单站点文件。
- **Schema（R1-1）**：`app/services/config/site_schema.json`（JSON Schema 2020-12，`jsonschema` 已在依赖中）校验信封与站点配置的关键结构，包括 selectors、workflow、stream_config、file_paste、image_extraction、request_transport。
  - 加载时校验失败只告警、不阻止启动；保存和导入时校验失败则拒绝，并给出路径级错误。
  - 未知字段放行，因为现有配置里字段很多，先保证不误伤。

## 3.6 设计记录：R1-3 运行时适配器更新（在线检查与应用）

更新器已经能在发布整包时逐站点处理（见 R1-2）。R1-3 的剩余部分是**不发整包**也能热更新站点配置：

- 服务 `app/services/adapter_updates.py`：
  - `check()`：拉取官方 main 上的 `config/sites/index.json`，与本地安装时附带的 `config/sites/index.json`（记录已安装版本的规范化摘要）以及本地文件的当前内容逐站点比较，得到以下状态：
    - `up_to_date`；
    - `update_available`：官方有变化，且用户没改过本地文件，可以安全自动应用；
    - `conflict`：官方有变化，但用户也改过本地文件，需要手动对比或合并；
    - `new_site`：官方新增的站点；
    - `local_only`：只在本地存在的站点；
    - `requires_newer_app`：站点要求的 min_app_version 高于当前应用版本。
  - `apply(sites)`：按 `file_sha256` 校验后下载官方文件，写入方式如下：
    - `update_available` 和 `new_site`：直接替换或新增；
    - `conflict`：必须显式要求，然后按 `merge_site_records` 合并（本地优先）。
    - 写入后更新本地 index.json 中对应站点的条目，再调用 `config_engine.reload_config()`。
  - 网络部分复用 `config_compare_support` 的安全抓取（`get_public_remote_resource`、大小限制与缓存）。
- API：`GET /api/adapters/updates`（检查）、`POST /api/adapters/updates/apply`（请求体 `{"sites": [...], "include_conflicts": false}`），鉴权与其他配置接口一致。
- 面板：在配置页加一个「检查站点适配器更新」入口，展示列表并支持一键应用安全更新。UI 放到后面，能在本机用 Playwright 截图核对时再做。

## 4. 进度记录

### 2026-09-26

- 合并 P3 分支进 main（`29d9685`）。唯一冲突是 `docs/review/DEVLOG-fixes-2026-09.md`，双方内容都保留了。验证结果：pytest 子集 691/63/0，compileall 通过，33 个 JS 文件 `node --check` 通过。
- 用户回答了 5 个决策问题，已记入 §1。
- 评估了 Portal，结论记入 §2：可行，推荐 VS Code 扩展加 ngrok 预留域名，采用混合工作流。
- 从 main 新建本分支，提交本日志。
- **R0-2 完成**：
  - `start.py` 的 `proc_hint` 改为不嵌套同种引号的写法；新增 `MIN_PYTHON=(3,10)`，两处自检从 3.8 提升到 3.10。
  - `start.bat` 快速路径增加 `sys.exit(sys.version_info < (3, 10))` 检查；回退路径的最低版本从 3.8 改为 3.10。
  - 新增 `tests/test_python_min_version.py`：
    - 3.10/3.11 上直接 compile；
    - 3.12+ 上先用 `ast.parse(feature_version)`，再用 tokenize 检测 PEP 701 写法（实测 feature_version 查不出这种写法）；
    - 同时校验 start.py 与 start.bat 的最低版本一致。
  - 实机 3.10 跑测试时发现 **29 个测试模块导入失败**：`app/models/schemas.py` 从 typing 导入了 3.11+ 才有的 `NotRequired`。已改为回退到 `typing_extensions`，并写入 requirements（仅 <3.11）。vermin 全仓扫描确认这是唯一一处 3.11+ 标准库依赖。
  - `tests/test_cancel_storm_regressions.py` 在 3.10 上改用 `exceptiongroup` 回移包。
  - 用真实 cmd 验证 start.bat 快速路径：3.10 和 3.13 走快速路径；旧版本（exe 桩返回 1）和没有 Python 时都回落到旧流程。
  - ruff 的 py38 目标下 E9 检查通过：旧版 Python 执行 start.py 时能看到友好提示，而不是 SyntaxError。
  - 遗留问题：ruff 报 8 个 F811（重复定义），与版本无关，留给 CI/lint 基线处理。
- 本地环境接入：握手成功，Portal 1.0.0 提供 14 个工具。建立了 worktree 和两个 venv，详见 §2.8。
- **R0-5 完成**（`de48ce3`）：
  - 从用户本地找回白名单挡住的 12 个文件，敏感信息扫描无命中；其中 9 个测试文件在沙盒里 282 passed。
  - tests/ 规则从白名单改为黑名单。
  - 新增 pyproject.toml：pytest 的 testpaths、pythonpath、3 个 marker，以及 ruff 最小基线。
  - Playwright 模块统一先 importorskip，并通过 `tests/_playwright.py` 在没有自带 Chromium 时回退到本机 Chrome/Edge。
  - conftest 给使用 real_page/headed_page 的测试自动打 `real_browser` 标记。
  - 新增 `tests/test_repo_hygiene.py`。
  - 沙盒直接跑 `python -m pytest`，不需要 ignore 列表：1068 passed / 96 skipped。
- **环境调整**：
  - 用户把 Portal 工作区改为上一级的 `工作区\`，并把 `普遍反代` 整体移了进去。移动后旧 worktree 失效，Portal 重启时后台测试也被中断。
  - 沙盒同时发生了重启：`.git/config` 丢失，因此 PAT 也不在了。
  - 处理方式：在 `arena-dev\` 新建独立克隆和两个 venv，清理 `新测试版` 里我的全部痕迹，改用 bundle 加用户本机凭据推送（`~/tools/ship.sh`，详见 §2.8）。
- **Phase 0 实机验证**（`8f83053`）：Windows 3.13 全量 1141 passed / 67 skipped / 1 failed。
  - 唯一失败：`test_workflow_page_guide.py::test_scroll_relayout_and_missing_dynamic_target_explanations`。滚动 100px 后引导图钉没有跟着移动（差值 100）。回到拆分前的 `de48ce3` 同样失败，属于早已存在的问题，留到 R2-8 前端工作时排查。
  - 另外发现并修复两处测试基础设施问题：
    - main.py 在 Windows 上设置了 Selector 事件循环，导致 Playwright 在全量测试中报 57 个 NotImplementedError，已加 `tests/_playwright.sync_playwright` 包装修复；
    - 测试脚本里的 PowerShell 变量 `$py` 与参数 `$Py` 在 PowerShell 里是同一个变量（大小写不敏感），已改名。
  - 一次全量运行卡住：卡在 Playwright `to_have_text` 等 driver 回包，而 driver 进程已不存在。py-spy 定位到了位置，但重跑未复现，暂记为偶发问题。
- **R1-1**（`93a9f94`）：新增 `site_schema.py`，包含站点、全局、信封三套 schema，报错带路径，未知字段放行；另有 9 个测试。
- **R1-2**：
  - 新增 `app/services/config/site_store.py`（SiteStore）：
    - 目录读写，只重写有变化的文件（两阶段写入）；
    - 删除的站点移入 `.trash/`；
    - 损坏文件跳过且永不覆盖，热重载时沿用上一次的配置；
    - 旧 `sites.json` 自动迁移：目录为空时直接拆分，已有发布文件时按 `merge_site_records` 合并，原文件改名为 `.migrated-*.bak`。
  - ConfigEngine 只改了四处文件 I/O（加载、变化检测、热重载、保存），热重载改为基于目录签名。
  - 仓库内置配置拆成 13 个文件，拼回后与原 sites.json **逐项一致**；adapter_version 统一为 2026.09.26，min_app_version 为 3.0.0。
  - 新增 `scripts/build_sites_index.py`：生成 `config/sites/index.json`（config_sha256 与 file_sha256）；支持 `--check`、`--bump`、`--bump-changed`。
  - 备份：导出格式不变；导入改为整体替换，失败时逐文件快照回滚。
  - 官方对比：先拉 index.json，再按 sha256 校验拉取各站点文件；没有 index（旧 main）时回退旧的单文件。
  - **更新器**：
    - 先读旧 index，再逐站点处理：新增的直接加入；用户**没改过**的（规范化摘要与旧清单一致）整体替换为新版；改过的保留本地值并补新字段；读不懂的先备份再替换。
    - 旧版的「本地优先」语义意味着发布里的站点修复永远到不了没改过配置的老用户，现在这个问题解决了。
  - update_preserve：`sites_config` 选项改为 `config/sites`；旧设置里的 `config/sites.json` 自动映射到它。
  - 面板设置项与 `.env.example` 改为 `SITES_CONFIG_DIR`；前端的回退显示路径同步更新。
  - 新增/调整的测试：
    - test_site_store（17 项）；
    - test_updater_site_files（6 项）；
    - test_config_compare_remote 新增 index 布局与 sha 不一致两项；
    - 5 个直接读 sites.json 的测试改用 `tests/_sites.py`。
  - 沙盒全量：1105 passed / 141 skipped。
- **修复**：`scripts/build_sites_index.py` 被 `.gitignore` 的 `/scripts/*` 白名单漏掉了（`git add -A scripts/` 会静默跳过被忽略的文件），导致 Windows 干净克隆中测试失败。已加入白名单并补测试（`beab65c`）。
- **R1-4**（`0d75621`）：
  - 新增 `tests/fixtures/parsers/build_synthetic.py`，按各解析器源码注释中记录的线上格式，合成 aistudio、chatgpt、claude（含 CRLF 变体）、deepseek、gemini、glm、kimi、qwen 的样本。
  - 新增 `tests/test_parser_golden.py`，每个样本按三种方式喂入：一次性、事件边界快照、每 7 字符快照。28 项首次运行全部通过。
- **R1-5**（`a7c22f0`）：
  - 新增 `tests/test_protocol_conformance.py`：假浏览器（与真实流监听一样用 SSEFormatter 打包）加 ASGITransport，官方 SDK 走完整 HTTP 路径。
  - 首次运行时流式用例失败，原因是假浏览器输出的 chunk 缺少 object/model 字段，与真实格式不符，已修正假实现。
  - anthropic 1.x 改用 `httpx2`，测试会自动适配。
  - requirements-dev.txt 增加 openai 和 anthropic。
- **R1-3 运行时部分**（`c7588dc`）：
  - 新增 `AdapterUpdater`，依赖注入，便于测试；`check()` 给出六种状态，`apply()` 默认只应用安全更新。
  - 冲突需要显式 `include_conflicts`，按本地优先合并。
  - 每个文件都按 sha256 和 Schema 校验，任何一个不通过就整体中止、不写文件；写入后更新本地安装清单并热重载。
- **R1-6**（`ee42d7c`）：新增 `adapter_health`，包含纯函数 `evaluate_preset` 和 `DrissionProbe`。
  - 输入框缺失或选择器无效判为 broken；发送按钮、回复容器缺失只判 degraded，并附出现条件提示。
  - 标签页借用方式与 `test-selector` 相同。
  - 无前缀的 `//…` 选择器按 XPath 处理，因为工作流执行器也是走 document.evaluate。
- **R1-7**（`695f790`）：新增 `selector_quality`，提供 lint 规则和回退建议，接入巡检报告与 `/api/adapters/lint`。
  - 内置配置统计：193 个选择器中，long_chain 13 个、text_match 13 个、generated_class 3 个。
- **阶段 1 实机验证**（`43325ec`）：Windows 3.13 结果 1251 passed / 1 failed；3.10 结果 1241 passed / 2 failed。
  - 失败之一是早已存在的 page_guide 滚动用例。
  - 另一个是 request_history 防抖测试，排查了两轮：
    - 第一轮以为是时序问题，把窗口从 0.5s 放宽到 2s（`43325ec`），但仍然失败；
    - 第二轮定位到真正原因：它断言「全进程没有名为 request-history-save 的线程」，而 R1-5 的一致性测试会让全局 request_manager 产生同名线程。现改为只检查本管理器自己的线程（`92f6850`）。
- **R2-7**（`ea8d3cb`）：新增 `app/services/metrics.py`：
  - 纯 ASGI 中间件，不缓冲流式响应，最后注册，因此处于最外层；
  - 标签使用路由模板，未匹配统一记为 `<unmatched>`；
  - `/metrics` 的访问规则与 `/health` 详情相同（S7）；
  - 标签页池指标只读取已连接的 `_browser_instance`，不调用 get_browser，因为那样会创建实例；
  - 测试时注意：没有浏览器的环境下 `/health` 按设计返回 503。
- **实机验证 `ea8d3cb`**：3.13 结果 1257 passed / 1 failed；3.10 结果 1248 passed / 1 failed。两个版本的唯一失败都是早已存在的 page_guide 滚动用例，防抖测试的修复已生效。
- **修复**（`3ad3837`）：页面内工作流编辑器的「保存工作流 / 测试运行」请求原本发往 `127.0.0.1:${PORT:-9099}`，而服务默认监听 APP_PORT=8199，且全仓没有任何地方设置 PORT，因此默认配置下保存会失败。这是 R2-4 梳理环境变量时发现的，现已改用 `AppConfig.get_port()`。
- **R2-4**（`1f6b7d8`）：
  - 用 AST 两遍扫描代码中读取的环境变量：
    - 第一遍自动识别转发参数的辅助函数，并记录变量名在第几个参数；
    - 第二遍解析模块常量、`os` 别名和 env 映射。
    - 共找到 166 个名字；其中 8 个是外部变量（终端检测、系统路径、内部标志），另有 4 个旧名别名。
  - 登记表共 154 项，另加 R2-5 新增的 RUNTIME_DB_PATH，现为 155 项。说明来源依次是面板的 label/desc、原 `.env.example` 的注释，以及人工补写（82 个此前完全没有文档）。
  - 核对结果：代码字面默认值与登记表 0 处不符；面板 51 项的类型、选项、默认值全部一致。
  - 重新生成的 `.env.example` 中启用的 75 个键与取值和原模板完全一致，另有 79 个可选项以注释形式写出。
- **R2-5**（`6749545`）：
  - RuntimeStore 使用 WAL 与 synchronous=NORMAL，按记录摘要做增量同步，支持 query_history。
  - RequestManager 只替换了 I/O 部分；首次启动导入两种旧格式的 JSON，原文件改名为 `.migrated-*.bak`。
  - 踩坑：RequestManager 是单例，测试会重新初始化它，旧的 `_store` 连接因此泄漏到别的测试（表现为记录数多出 2 条）；已在 `__init__` 里关闭并丢弃旧连接。
- **R2-2**（`5568ce2`）：
  - 流式 Responses 的响应体在端点函数返回之后才执行，因此需要用 `with_chat_job` 包住生成器。
  - 端到端测试确认三种协议各计数一次。
- **R2-3**（`e4c0c2d`、`35df9c8`）：
  - 拆分前排查了四类陷阱：`super()`、双下划线名称改写、类名自引用、测试对模块全局变量的 monkeypatch。
  - 用脚本自动拆分并裁剪导入，逐个比对 AST。
  - CommandEngine 的模块级常量迁到 `command_engine_common`，原文件重新导出以保持兼容；用 AST 全仓核对过，外部只导入 CommandEngine 和 command_engine 两个名字。
  - 新增 `test_class_structure`，保证 mixin 之间没有同名方法，避免 MRO 静默遮蔽。
  - `_env_scan` 改为同时扫描未提交的新文件：否则刚拆出、尚未提交的模块里读取的变量会被误判为「登记了却没人读」。
- **偶发失败排查**：在用户本机把两个可疑用例各单独重跑 5 次：
  - CDP 回收用例 5 次全部通过，是罕见竞态；
  - page_guide 滚动用例（旧版）5 次中失败 3 次。原因是引导层在 scroll 事件里用 rAF 重绘，无头 Chrome 的 rAF 会被节流，固定等待 150ms 不可靠，已改为最多轮询 3 秒（`f922557`）。
  - 3.10 在 `5568ce2` 的全量结果为 1290 passed / 0 failed，首次全绿。
- **R2-8 面板**（`f922557`）：新增 AdapterPanel，放在站点配置页的「维护与巡检」分区，含三个区块：
  - 巡检：展示状态、提示和回退建议；
  - 稳健度：只列出有问题的选择器；
  - 更新：一键应用安全更新；冲突站点需确认后按本地优先合并。

  界面测试在空白页加载本地 Vue 与组件，并使用假请求函数。
- **R2-3 ConfigEngine**（`f4164da`）：拆成 6 个 mixin；11 个模块级定义原样迁到 `engine_common`，`engine.py` 重新导出。ConfigConstants 仍是同一个类对象，测试对它的 monkeypatch 照常生效。
- **R2-1 / R2-6**：只做了现状清单和设计，未实施。原因是两者都要改动浏览器层的每条路径，必须逐步在本机做真实浏览器回归；在本轮长会话的末尾仓促重写风险过高。分阶段计划见 `docs/architecture/browser-driver-and-process-model.md`。
- **实机验证与修复**（`a212f5a` 之后）：
  - 修复后的 page_guide 用例单独重跑 5 次全部通过。
  - 维护面板界面测试 3 项中失败 1 项，暴露了组件的真实缺陷：应用更新后自动调用 checkUpdates 刷新，而它会清空 applyResult，导致「已更新 N 个站点」的提示一闪而过。
  - 已修复（`bc09cec`）：改为 `checkUpdates(keepApplyResult)`；按钮改为显式调用，避免点击事件对象被当成第一个参数传入。
- **R2-1 第 1 步**（`94c5dc5`）：
  - 统一异常：分类器合并了原来三处字符串规则，测试逐条核对一致，翻译后消息不变。
  - DrissionTabDriver 只做转发；FakeTabDriver 用于测试；新增棘轮测试。
  - 本机 3.13：1331 passed / 0 failed。
- **R2-1 第 2～4 步**（`5ec2202`、`a012838`、`3dfcce8`）：迁移前全仓核对，app/ 里没有按 DrissionPage 异常类型的 except、isinstance 或类名字符串判断，因此切换到驱动（保留原消息）不改变行为。
  - 踩坑一：驱动的 run_js 原本总是显式传 `as_expr` / `timeout`，与签名为 `run_js(script, *args)` 的测试替身不兼容；已改为只在参数与默认值不同时才传。
  - 踩坑二：棘轮原先用正则统计，会把 `driver_for_tab(tab).run_js(` 也算作直接调用，无法反映进度；已改为 AST 统计，并排除驱动接收者。
  - 踩坑三：自动插入导入时，曾把导入放到 `from __future__` 之前（运行时 SyntaxError），而 ruff 基线没有覆盖这条规则；已修正，并把 F404 加入 ruff 基线。
- **测试修复**（`1b16387`）：测试反复重新初始化 RequestManager 单例，而前序测试遗留的保存线程醒来后，会调用新测试打桩的 `_save_history`，导致防抖用例在全量中稳定失败；已改为重新初始化前先排空遗留线程。
- **R2-7 请求 ID 贯穿**（`2da24c1`）：RequestContext 新增 http_request_id，「创建」日志带上 X-Request-ID，请求历史记录新增 protocol 与 http_request_id 字段。端到端测试验证：带 X-Request-ID 调用 /v1/messages 后，能在历史中找到对应记录。
- **实机验证**：`82e266f`（驱动第 1～4 步）两个 Python 版本全绿；`2da24c1` 在 3.13 上为 1333 passed / 0 failed，3.10 上为 1324 passed / 0 failed。
- **R2-1 收口**（`73c0c84`）：
  - 用 AST 按 UTF-8 字节偏移精确改写 222 处。
  - network_monitor 里依赖 Listener 内部状态的四段逻辑，原样迁入 DrissionListener。
  - 由既有测试发现并修正了两处语义问题：
    - translated_errors 原本会翻译所有异常，导致 network_monitor 依赖的 `except TypeError`（旧版 DrissionPage 不认识 res_type 参数时的退回逻辑）失效；已改为只翻译 DrissionPage 自身的异常；
    - 驱动的 find 原本用 to_locator 把不带前缀的定位器当作 CSS，而 DrissionPage 原生语义是文本匹配；已改为原样传递，配置里的选择器由调用方自行规范化。
- **R2-8**：
  - Tailwind：确认了运行时版本（3.4.17）和注入方式（`document.head.append`），预编译 CSS 因此放在 head 末尾，保持原有层叠顺序。
  - 覆盖检查顺带发现 8 个从未生效的类名（透明度刻度之外的写法，以及 `py-0.2`），保持原样只登记。
  - 拆分：Node vm 校验 155 个方法、键顺序一致、源码逐字相同。
  - ES 模块：38 个文件都能按模块语法解析；跨文件的裸引用都能经 window 解析到。
  - 失误与改进：一次串联命令没有在测试失败时停下，带着 5 项失败提交了。这 5 项是读取原文件源码的测试，修复后并入同一个提交（修订后为 `9cccde3`）。此后改用 `~/tools/check.sh && git commit`，检查失败就不会提交。
- **修复**（`fa20a37`）：重启守护代理在连接数超限时，原本不读请求就回 503 并关闭；Windows 上会因接收缓冲区有未读数据而发 RST，503 被丢弃（本机 3.10 偶发失败的原因）。已改为先 shutdown 再读掉客户端数据，然后关闭。
- **工具**：新增 `~/tools/restore.sh`（沙盒重启后一条命令恢复环境）和 `~/tools/check.sh`（提交前检查）；`ship.sh` 在 Portal 响应超时后会反复确认远端，不再误报失败。


- **R2-6 worker 自动恢复补齐（本轮）**：API lifespan 在 worker 首次就绪后启动监控线程；worker 意外退出时立即重启，连续启动失败按 1–30 秒指数退避，关闭 API 时停止监控并关闭 worker。就绪检查携带随机令牌，健康端点也校验回环来源和令牌，避免端口复用时误认其他进程。新增崩溃—重启集成测试；Python 3.13 下 worker 9 项、worker/异常棘轮/启动模型组合 21 项均通过；Ruff、compileall 通过。
