# 目录结构评审：与 Python 官方推荐对照

> 评审对象：`universal-web-api` @ `5c6c3ee`（`VERSION` 3.0.1）
> 规模基线（评审时）：`app/` 223 个 Python 文件 / 114,172 行；`app/api` 18 文件 18.4k 行，`app/core` 111 文件 58.3k 行，`app/services` 61 文件 29.6k 行；`static/js` 45 个 JS，`tests/` 94 个 py（81 个 `test_*`），`docs/` 22 个 md。
>
> **执行状态（2026-10）**：阶段 A 与 B1/B2 已落地并通过全量回归，见
> [`docs/STRUCTURE-FIX-CHECKLIST.md`](./STRUCTURE-FIX-CHECKLIST.md)；C1（路径）后续补齐，C2–C4 有意暂缓（同文档 §6 说明原因）。

---

## 0. 结论摘要

**总体判断：这是一个「产品形态正确、工程细节欠账」的目录结构。** 按「应用型项目」的标准它是合格甚至偏优秀的（分层清晰、领域划分合理、有兼容门面、有架构文档与棘轮测试）；按「Python 官方/社区推荐」的严格口径，它在**包边界、入口、元数据单一来源、测试组织、模块粒度**五项上有明确差距，其中三项是「官方文档里点名的反模式」。

| 维度 | 现状 | 官方/社区基准 | 评分 |
| --- | --- | --- | --- |
| 顶层布局（应用 vs 库） | flat layout + `app/` 包 | 应用可用 flat（Real Python 明确「Applications → Flat」）；库才要求 src | **8 / 10** |
| 依赖与打包元数据 | `pyproject.toml` + `uv.lock` + `.python-version` | PEP 621 + uv 实践，本项目做法是标准答案 | **9 / 10** |
| 包边界（能否独立导入） | ❌ 包内反向 import 根目录模块 | 包必须自洽；`python -m pkg` 应可用 | **3 / 10** |
| 入口点 | 只有 `main.py` 脚本 | 薄 `__main__.py` + `[project.scripts]` | **3 / 10** |
| 版本单一来源 | 三处（`VERSION` / `pyproject` / `__init__`） | PyPA：单一来源或 automated test 兜底 | **5 / 10** |
| 路由分层 | 协议与面板管理接口混在同一批文件 | FastAPI：按 router + prefix/tags，admin 单独包 | **4 / 10** |
| 模块粒度 | 18 个文件 > 1000 行，最大 5142 行 | 「一个模块一个职责」，社区经验线 500–1000 行 | **3 / 10** |
| 配置 / 运行时数据分离 | 分发配置与用户状态同住 `config/` | 代码只读 + 独立可写数据目录 | **4 / 10** |
| 测试组织 | 94 个文件平铺 | pytest：tests 在包外 + 镜像结构 + 手动脚本隔离 | **5 / 10** |
| 文档组织 | 22 个 md，现行与历史混放 | `architecture/ guides/ adr/ history/` | **6 / 10** |
| CI | ❌ 无 `.github/workflows` | 棘轮测试与产物校验缺执行环境 | **2 / 10** |
| 渐进重构策略 | 兼容门面 + 分阶段拆分日志 | 大型遗留代码的推荐做法 | **9 / 10** |

**一句话**：**不要为了「像官方」而改成 src layout**（对本项目收益为 0、成本极高，见 §1.1）；真正该做的是把**根目录那 7 个脚本收进包**、**加 `__main__.py` + console script**、**把 `VERSION` 收成单一来源**、**拆掉 3 个 5000 行级文件**、**把 `config/` 拆成只读配置 + 可写数据目录**。这 5 项完成后，结构评分可以从 5.4 提到 8.0 左右。

---

## 1. 先把「官方推荐」说准

「Python 官方」并不是一份单一的目录规范；真正有约束力的是下面 6 份文档，它们各自回答不同问题。

### 1.1 PyPA：src layout vs flat layout

[Python Packaging User Guide · src layout vs flat layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/) 的论证逻辑是：**src layout 的全部收益都来自「必须先安装才能 import」**，因此它解决的问题是：

1. 防止误用「开发中的源码副本」而不是已安装包；
2. 让 editable install 只暴露「本来就该被导入」的文件；
3. 让测试跑在「用户实际会拿到的包」上。

反过来说——**如果你的项目根本不打算被安装成 distribution，这三条收益就是 0**。同一个页面明确写了 flat layout 的两个前提：「不需要安装即可运行」以及「项目根目录就是工作目录」。

[Real Python · Python Application Layouts](https://realpython.com/python-application-layouts/) 给了一张更直白的表：**Applications → Flat；Libraries → src/**；「Growing past about ten modules」时才考虑 src。

**本项目的实际形态**（有证据）：

- `pyproject.toml` 里 `[tool.uv] package = false`，并且**没有 `[build-system]`** —— 这个项目**从不构建 wheel**，只被 `uv run python main.py` 原地执行；
- 发布方式是 GitHub Release 压缩包解压后替换文件（`updater.py` + `update_preserve.py`），不是 `pip install`；
- 运行时需要 `static/`、`config/`、`chrome_profile/`、`download_images/` 与代码**同级同目录**（`main.py:990-1015` 用 `Path("static")`、`Path("download_images")` 挂载）。

**判定：src layout 对本项目「不适用」，不是「不合理」。** 强行迁移到 `src/` 会同时打断：更新器的文件路径替换、`update_preserve` 的保留规则、用户 `chrome_profile/` 的相对定位、以及 README 里唯一一条启动命令。这是「为满足规范而制造回归」的典型场景。

**但官方文档里可迁移的那部分要吸收**：flat layout 之所以被允许，前提是「根目录下没有一堆散落的 Python 文件」。本仓库根目录有 7 个 `.py`，正好踩在这个前提上——见 §3 的 P0-2。

### 1.2 pytest：测试放在包外（这条必须遵守）

[pytest · Good Integration Practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html)：

- 推荐结构是 `pyproject.toml` + 包目录 + **独立的 `tests/`**；
- 「Generally, but especially if you use the default import mode `prepend`, it is **strongly** suggested to use a `src` layout」；
- 新项目建议 `--import-mode=importlib`。

本项目 **tests 在包外 ✅**，但用 `pythonpath = ["."]`（prepend 语义）+ 未启用 `importlib` 模式 —— 这恰好是 pytest 点名的组合。它的副作用不是「测试跑不起来」，而是**掩盖包边界问题**：因为 `.` 在 `sys.path` 上，`from updater import ...` 这种「包反向依赖根目录」的写法在测试里永远通过，一打包或换个 CWD 就失败。

### 1.3 FastAPI：`app` 包 + routers 子包 + 独立的 internal/admin

[FastAPI · Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/) 给的结构是：

```
app/
├── __init__.py
├── main.py
├── dependencies.py
├── routers/{__init__,items,users}.py
└── internal/{__init__,admin.py}.py
```

要点有三条：**（a）每个子目录都要有 `__init__.py`；（b）按资源拆 router，用 `prefix` / `tags` / `dependencies` 收拢公共属性；（c）管理类端点单独放 `internal/`。**

本项目 (a) ✅ 做得很彻底（连 `tab_pool_parts/` 都有）；(b) ❌ 形似神不似——router 是按**历史代码文件**切分的，不是按资源；(c) ❌ 不存在 admin 层，`/api/pool/status` 在 `chat.py:3292`，14 个 `/api/` 面板端点在 `tab_routes.py` 里。

### 1.4 Python 官方文档：`__main__.py` 与入口

[`__main__` — Top-level code environment](https://docs.python.org/3/library/__main__.html)：`__main__.py` 用于「让包本身可以被 `python -m 包名` 调用」；社区共识（Real Python 同页引述）是**入口文件要薄**，只做 `from .cli import main; main()`，再配合 `pyproject.toml` 的 `[project.scripts]` 生成命令行。

本项目：**没有 `__main__.py`，没有 `[project.scripts]`，`python -m app` 直接失败**；`main.py` 1100 行，同时承担「引导、中间件、路由、静态挂载、媒体转码（含 ffmpeg）、异常处理、启动页编排」7 件事。

### 1.5 PyPA：版本单一来源

[Single-sourcing the Project Version](https://packaging.python.org/en/latest/discussions/single-source-version/) 给的选项是：VCS tag / 写死在 `pyproject.toml` / `dynamic = ["version"]` + backend 从代码读；并指出「也可以接受手工重复录入，但**必须**用自动化测试保证不漂移」。

本项目的版本有 3 个副本，**没有任何测试保证一致**：

| 位置 | 内容 |
| --- | --- |
| `VERSION` | `3.0.1` |
| `pyproject.toml:21` | `version = "3.0.1"` |
| `app/__init__.py` | `_load_version()` 读 `VERSION` |

（注意：`[tool.uv] package = false` 时 `importlib.metadata.version()` 不可用，所以「改成读 metadata」这条捷径在这里**行不通**，正确做法是 `dynamic = ["version"]`。）

### 1.6 uv：应用型项目

uv 的 `[tool.uv] package = false` 语义就是「这不是一个要被构建安装的包，只是一个跑在源码上的应用」。本项目用法正确 ✅，也说明不应套用「库」的目录规范。

### 1.7 对照总表

| 官方条目 | 出处 | 本项目 | 判定 |
| --- | --- | --- | --- |
| 应用可用 flat layout | PyPA / Real Python | flat + `app/` | ✅ 符合 |
| 根目录不应散落 Python 文件 | PyPA（flat 的前提） | 根目录 7 个 `.py` | ❌ 违反 |
| 可导入代码须自洽、不反向依赖根目录 | 通用 | `app` 反向 import 根模块 | ❌ 违反 |
| tests 在包外 | pytest | `tests/` | ✅ 符合 |
| 新项目用 `importlib` import mode | pytest | `pythonpath = ["."]` | ⚠️ 部分 |
| 子包都有 `__init__.py` | FastAPI | 全覆盖 | ✅ 符合 |
| router 按资源 + prefix/tags | FastAPI | 按历史文件切分 | ⚠️ 部分 |
| admin 端点单独成层 | FastAPI | 散落 | ❌ 违反 |
| 薄 `__main__.py` + entry point | Python docs | 无 | ❌ 违反 |
| 版本单一来源 | PyPA | 3 处无校验 | ❌ 违反 |
| 依赖唯一来源 `pyproject.toml` | PEP 621 / uv | ✅ 且删掉了 requirements.txt | ✅ 优秀 |
| 生成物与源码同步由测试守护 | 通用 | `.env.example` 有 `--check` 测试 | ✅ 优秀 |

---

## 2. 已经做对的地方（不要动）

1. **依赖治理是教科书级**：`pyproject.toml` 唯一事实来源、`uv.lock` 锁定、`.python-version` 固定解释器、明确拒绝 `requirements.txt`/`pip`，`tests/test_uv_environment.py` 守护。这比大多数同类项目干净。
2. **测试在包外 + 分层标记**：`browser` / `real_browser` / `local_fixture` 三种 marker，`conftest.py` 在缺本地私有资源时 **skip 而非 fail** —— 这是「干净克隆也能跑」的正确设计。
3. **领域分包而非技术分桶**：`api / core / services / models / runtime / utils / worker` 的划分是「按职责」，不是 `controllers/ helpers/ misc/` 这类垃圾桶；`core` 内部再按 `driver / browser / tab_pool / workflow / parsers / extractors / page_capture` 对齐了**真实的概念边界**。
4. **配置数据驱动**：站点适配器是一个站点一个 JSON（`config/sites/<domain>.json`）+ 版本与 SHA-256 清单（`index.json`）+ Schema 校验（`site_schema.py`）。「新增一个站点通常不写 Python」是很好的架构决策，也让 `config/` 的职责非常明确。
5. **兼容门面 + 分阶段拆分**：`app/core/config.py` → `config_parts/`、`tab_pool.py` → `tab_pool_parts/`、`services/config_engine.py` → `services/config/`，并且拆分在 `docs/` 里有记录（R2-3/R2-8）。对 11 万行、无类型的遗留代码，这比一次性大重写正确得多。
6. **棘轮测试（ratchet）**：`test_driver_ratchet.py`（禁止绕过驱动层）、`test_exception_ratchet.py`（禁止裸 `except`）、`test_repo_hygiene.py`。用具名基线文件（`tests/fixtures/*_ratchet.json`）阻止「新增违规」，是渐进治理的标准手法。
7. **生成物有校验**：`tests/test_settings_registry.py` 用 `build_env_example.py --check` + `git check-ignore` 同时保证 `.env.example` 与登记表一致、且脚本本身没被误 ignore。这个细节水平很高。
8. **文档分层已经起步**：`docs/architecture/`、`docs/review/`、`docs/roadmap/`、`docs/migrations/` 的划分方向是对的。

---

## 3. 问题清单

按「改动成本 / 收益」排序，不是按严重度绝对值。

### P0-1 包边界破损：`app/` 反向依赖根目录模块　✅ 已修复（B1）

**现象**

```python
# app/api/system.py:42
from update_preserve import load_update_preserve_settings, save_update_preserve_settings
# app/api/system.py:2465 / 2493 / 2602 / 2645
from updater import compare_versions, get_current_version, ...
# app/runtime/bootstrap.py:276
from clean_profile import safe_slim_profile
# app/services/adapter_updates.py:151
from updater import merge_site_records
# app/services/config/site_store.py:265
from updater import merge_site_records
```

**影响**

- `app` 不是自洽的包：只要 `sys.path` 上没有被仓库根目录占位，`import app.api.system` 就抛 `ModuleNotFoundError`。这意味着 `python -m app`、打包、在别的项目里复用、静态分析工具从包外运行全部不可用。
- 被 `pytest` 的 `pythonpath = ["."]` 完全掩盖（见 §1.2），所以**永远不会在测试里暴露**。
- 名字还会污染顶层命名空间：`updater`、`clean_profile` 是极易与第三方包撞名的一般性名字。

**建议**：把 4 个被 import 的根模块移入包内，按职责放：

```
app/update/{__init__,engine.py,preserve.py,profile.py}   # 自更新相关
```

根目录只保留一个 3 行的 `main.py`（或彻底改为 `__main__.py` + console script）。迁移时为了不破坏老用户的「下载新压缩包覆盖旧目录」，**被删的根文件要保留一个空壳并 `from app.update import *`**，下个大版本再删。

---

### P0-2 根目录 7 个 `.py` 混居，且包含个人脚本　✅ 已修复（A4/A5/B1）

**现象**

| 文件 | 性质 |
| --- | --- |
| `main.py` | 唯一入口 ✅ |
| `updater.py` (47 KB) / `update_preserve.py` (11 KB) / `clean_profile.py` / `patch_drissionpage.py` (39 KB) | 运行时依赖的模块，**应该在包内**（见 P0-1） |
| `perf_watch.py` | 开发期采样脚本，应在 `tools/` |
| `generate.py` (6.7 KB) | 作者个人批处理示例，**硬编码** `C:\Users\QIU\Desktop\新建文件夹 (2)`、`C:\Users\QIU\.gemini\...`，且依赖不存在的 `prompt.txt` |

`generate.py` 是明确的仓库卫生问题：它出现在 `ls` 的第一屏，会让新读者以为它是项目的一部分；硬编码的 Windows 路径在任何其他机器上都会失败。

**建议**：运行期模块 → `app/update/`；工具 → `tools/`（并在 `.gitignore` 里像 `scripts/` 那样白名单）；`generate.py` 删除，或改写为 `examples/arena_image_batch.py`（路径从 argv/env 读）并加进 `.gitignore` 的例外。

---

### P0-3 没有 CI 目录　✅ 已修复（A6）

`.github/` 不存在。而仓库里有大量「只有定期执行才有意义」的守护测试：`test_tailwind_equivalence.py`（CSS 产物是否与源码同步）、`test_settings_registry.py`（`.env.example` 是否过期）、`test_repo_hygiene.py`、三个棘轮测试、`test_python_min_version.py`。**这些测试目前只能靠作者本地记得跑。**

**建议**：`.github/workflows/ci.yml`（`uv sync --no-install-project` → `uv run pytest -m "not browser and not real_browser"` → `uv run ruff check .`）+ 一个 `nightly.yml` 跑带浏览器的用例。这是「结构」层面收益最高、风险最低的一条。

---

### P1-1 `app/core/browser.py` 是死文件（模块与包同名）　✅ 已修复（A1）

```
app/core/browser.py          # 9 行，转发到包
app/core/browser/__init__.py # 同样转发
```

CPython 的 `FileFinder` 在解析 `app.core.browser` 时**先检查目录 + `__init__.py`，再检查同名模块文件**，所以 `browser.py` 永远不会被加载（已实测：`import app.core.browser` 命中的是 `browser/__init__.py`）。

这是**导入了顺序敏感、且实际无作用**的文件，也是新人最容易改错的地方（改了没生效）。同类但**不冲突**的组合有 `core/config.py` vs `core/config_parts/`、`core/tab_pool.py` vs `core/tab_pool_parts/`、`services/config_engine.py` vs `services/config/` —— 这些名字不同，安全，属于合理的「门面 + 实现」模式。

**建议**：直接删除 `app/core/browser.py`（零风险，已无引用价值）。

---

### P1-2 两套路径约定并存：`PROJECT_ROOT` 与 CWD 相对路径　✅ 已修复（C1）

`PROJECT_ROOT` 在 `app/` 里出现 62 次（正确做法），同时有 **24 处** CWD 相对字面量：

```
main.py:990   if Path("static").exists(): app.mount("/static", StaticFiles(directory="static"))
main.py:994   download_images_dir = Path("download_images")
main.py:432-434  candidates.append(Path("static") / ...)
app/core/browser/media.py:2077/2431/2853/3025  save_dir = Path("download_images")
app/core/extractors/media_extractor.py:2621    save_dir = Path("download_images")
app/core/config_parts/env_config.py:229        os.getenv("DASHBOARD_FILE", "static/index.html")
```

**影响**：从任意其他目录启动（`uv run --directory`、systemd、计划任务、IDE 配置错 CWD）会静默降级——静态目录不挂载、控制面板 404、图片写到别处。这类 bug 极难定位，因为「用 `uv run python main.py` 从根目录跑」永远不触发。

**建议**：加 `app/core/paths.py`，只暴露 `PROJECT_ROOT` / `STATIC_DIR` / `DATA_DIR` / `DOWNLOAD_DIR` 常量；`ruff` 规则或一个简单测试（`grep` 断言 `Path("static")` 之类不出现）防止回潮。**这条和 P1-3 可以一起做**。

---

### P1-3 运行时数据与分发配置同住 `config/`　⏸ 暂缓（C3）

`config/` 现在是两种性质混在一起：

| 性质 | 例子 | 是否入库 |
| --- | --- | --- |
| 随仓库分发的**只读**数据 | `sites/*.json`、`browser_config.json`、`extractors.json`、`parsers.json`、`image_presets.json`、`site_rules.json` | 入库 |
| 用户**可写**运行时状态 | `runtime.sqlite3`、`commands.json`、`commands.local.json`、`command_results.local.json`、`sites.local.json`、`app_stats.json`、`model_name_overrides.local.json`、`arena_model_aliases.local.json`、`marketplace_cache.json` | `.gitignore` 里 14 条分散规则 |

**影响**：

- 「哪些文件能删」没有结构上的答案，只能读 `.gitignore`；
- 程序目录只读时（macOS 的 `/Applications`、Windows 的 `C:\Program Files`、容器镜像只读挂载）**直接崩**，因为所有写操作都落在 `config/`；
- 更新器必须在 `update_preserve.py` 里逐条列运行时文件（现在列了 9 条），漏一个就丢数据；
- 备份/恢复（`/api/settings/backup`）要手工维护同一份清单。

**建议**：

```
config/                 # 只读，随包分发（模板语义）
└── sites/ ...
var/                    # 可写，整目录 gitignore，支持 UWAPI_DATA_DIR 覆盖
├── runtime.sqlite3
├── commands.json
├── local/ (sites.local.json 等)
├── logs/
├── download_images/
└── chrome_profile/
```

迁移时保留旧路径读取 + 一次性搬迁（复用 `engine_migrations.py` 已有的迁移框架），`update_preserve.py` 的清单可以缩成「保留 `var/`」一行。

---

### P1-4 18 个文件超过 1000 行，其中 3 个超过 3000 行　🟡 部分修复（C2 第一批）

```
5142  app/api/tab_routes.py
3299  app/api/chat.py
3254  app/core/extractors/media_extractor.py
3227  app/core/browser/media.py
3207  app/core/network_monitor.py
3167  app/services/command_engine_actions.py
2932  app/core/browser/workflow.py
2889  app/core/stream_monitor.py
2791  app/core/workflow/executor_actions.py
2674  app/api/system.py
2625  app/services/request_manager.py
2123  app/core/workflow/text_input.py
1912  app/core/workflow/executor_send.py
1761  app/api/anthropic_routes.py
1678  app/api/config_routes.py
1561  app/core/config_parts/cute_translator.py
1552  app/core/workflow/executor.py
1511  app/models/schemas.py
```

「1000 行」不是官方规范（PEP 8 不规定文件长度），但项目自己的实践已经证明拆分有效：`tab_pool_parts/`、`command_engine_*.py`、`tool_calling_*.py` 都是同一套 Mixin 拆分法的产物。**说明团队缺的不是能力，而是把同一标准用到路由层。**

`app/api/tab_routes.py` 5142 行里混了 4 类东西：`/tab/{i}/v1/*`、`/url/{domain}[/{preset}]/v1/*`、`/group/{id}/v1/*`、`/tab-url/{token}/v1/*` 的转发实现 + `/api/tab-pool/*` + `/api/presets/*` + 流式执行封装。

**建议**（按 FastAPI 的 router 语义重排，不改 URL）：

```
app/api/
├── openai/{completions.py,responses.py,models.py,stop.py}
├── anthropic/{messages.py,count_tokens.py,translate.py}
├── admin/{pool.py,presets.py,config.py,settings.py,logs.py,update.py,debug.py,metrics.py,adapters.py,commands.py}
└── routes.py          # 仅 include_router
```

---

### P1-5 面板管理端点没有独立层　🟡 部分修复（C2 第一批，`app/api/admin/`）

| 端点 | 现在在哪 |
| --- | --- |
| `GET /api/pool/status` | `chat.py:3292` |
| `/api/tab-pool/*`、`/api/presets/*` | `tab_routes.py` |
| `/api/settings/*`（备份、环境、浏览器常量） | `system.py` |
| `/api/config/*`、`/api/sites/*` | `config_routes.py` |

对比 FastAPI 官方的 `app/internal/admin.py`，这里是明确的反例：**同一个 `system.py` 里既有 `/health`（公开探活）又有 `/api/settings/env`（读写 `.env`）和 `/api/update/switch`（换版本）**，权限模型完全不同却住在一个文件、一个 router、一个 `tags` 里。这也让「哪些端点需要面板令牌」只能逐个函数看。

**建议**：`app/api/admin/` 统一挂 `prefix="/api"`, `dependencies=[Depends(verify_dashboard_auth)]`，把认证从「每个函数里写一遍」上提为 router 级依赖——这正是 FastAPI 文档给出的用法。

---

### P1-6 缺少 `__main__.py` / `[project.scripts]` / `create_app()` 工厂　🟡 部分修复（B2，工厂仍待做）

`main.py` 1100 行的模块顶层就在建 `app = FastAPI(...)`，并且 `if __name__ == "__main__"` 分支里做 `bootstrap_runtime()`。这带来三个结构后果：

- 无法 `python -m app`，也无法用 console script 安装成命令；
- **无法在测试里构造一个「干净 app」**（没有 `create_app()` 工厂），只能 `import main`，而 `import main` 会连带执行日志初始化、静态挂载、中间件注册；
- 入口逻辑（bootstrap）与 ASGI 应用构造耦合，`uvicorn` 直接加载 `main:app` 时会**跳过 bootstrap**，只能靠 `main.py` 脚本模式启动。

**建议**

```
app/__main__.py      # 3 行：from app.cli import main; main()
app/cli.py           # bootstrap_runtime() + uvicorn.run(create_app())
app/factory.py       # create_app() -> FastAPI（中间件、路由、静态、异常处理）
pyproject.toml       # [project.scripts] universal-web-api = "app.cli:main"
```

---

### P2-1 测试目录零分层　🟡 部分修复（A8，仅隔离手动脚本）

94 个 py 平铺在 `tests/` 根，其中至少 7 个是**手动脚本**而非 pytest 用例：

```
tests/browser_operations_studio.py   "Live-app UI smoke regression ... requires local :8000"
tests/browser_preset_transfer.py     "requires local :8000 server"
tests/browser_site_capabilities.py   "Optional live-app capability smoke test"
tests/browser_upgraded_sites.py      "requires local :8000"
tests/browser_workflow_studio.py     "Run: python tests/browser_workflow_st..."
tests/e2e_concurrency_headed.py      "手动端到端并发压测"
tests/e2e_idle_maintenance_headed.py "手动端到端检查"
```

它们不以 `test_` 开头所以不会被收集，但和 `test_*.py` 混在一起，导致 `ls tests/` 无法回答「这里有多少测试」。

**建议**

```
tests/
├── unit/          # 纯逻辑，无浏览器、无网络
├── contract/      # 协议一致性（openai/anthropic SDK 当客户端）
├── integration/   # ASGI + 临时 SQLite
├── ui/            # Playwright，合成页面
├── manual/        # 手动脚本，加 pytest.ini 的 norecursedirs
└── fixtures/ _helpers.py conftest.py
```

并启用 `addopts = ["--import-mode=importlib"]`（pytest 官方对新项目的建议）。注意：`tests/` 里目前有大量 `ROOT / "static"` 式断言，移动文件前先统一路径辅助。

---

### P2-2 `_parts/` / `config_parts/` 是「重构过渡期命名」

`config_parts/` 11 个文件（含 `__init__`）里，5 个是纯日志/展示层（`log_collector`、`log_formatters`、`log_redaction`、`secure_logger`、`cute_translator`），剩下 `env_config`、`browser_constants`、`exceptions`、`sse_formatter`、`message_validator` 各属不同关注点。`tab_pool_parts/` 18 个文件其实是完整生命周期（acquire/allocation/routing/tabs/contexts/sessions/status/maintenance/recovery/network/clipboard）。

`_parts` 后缀不表达领域，只表达「从大文件里拆出来的」。拆分完成后应去掉这个后缀：

```
app/core/observability/{logger.py,redaction.py,formatters.py,collector.py,cute.py}
app/core/tab_pool/{manager.py,acquire.py,allocation.py,routing.py,tabs.py,
                   contexts.py,sessions.py,status.py,maintenance.py,recovery.py,
                   network.py,clipboard.py}
```

（`cute_translator.py` 1561 行、96 KB 的「喵化日志文案表」，建议移出 `app/` 到 `assets/i18n/` 或 `data/log_phrases.py`——它是纯数据 + 正则表，不是基础设施。）

---

### P2-3 重复文件与重复内容　🟡 部分修复（A2/A3）

| 现象 | 证据 |
| --- | --- |
| `CHANGELOG.md` 与 `CHANGELOG-3.0.1.md` **字节完全相同** | `cmp` 返回一致 |
| `app/core/parsers/mimo_parser.py` 与 `mimo_runtime_parser.py` **除首 3 行注释外完全相同** | `diff` 无输出 |
| `assets/` 与 `static/` 各存一份 README 截图 | `assets/ai-web-preview.png` + `static/tutorial-dashboard-overview.png` 等 |
| 选择器定义同时存在于 `config/sites/_global.json` 与 `static/js/dashboard-schema.js` | 两处 `DEFAULT_SELECTOR_DEFINITIONS` |

前三项直接删/合并。第四项是真正的双份维护（改一处忘一处），建议前端在启动时从 `/api/settings/selector-definitions` 拉取，`dashboard-schema.js` 只留 fallback。

---

### P2-4 `static/` 混合了「运行时必需」与「文档/调试」

```
static/                       # 运行时
├── index.html (59 KB)        # 控制面板入口
├── js/ css/ images/ vendor/
├── tutorial.html + tutorial/ # 用户手册（文档）
├── controlled-browser-guide.html
├── selector-practice.html    # 调试工具
├── lifecycle-probe.html      # 调试工具
├── tutorial-dashboard-overview.png / workflow-visualization.png / 截图.png
└── 拦截请求发生器.txt          # 控制台片段
```

`static/` 被整体 `app.mount("/static", StaticFiles(directory="static"))` 暴露，也就是说**截图、调试页、中文名 txt 全部可通过 HTTP 访问**。这不是安全问题（无敏感信息），但违反「静态目录 = 应用资产」的直觉，也让前端产物目录难以独立打包/缓存。

**建议**：`web/`（运行时：`index.html` + `js/ css/ images/ vendor/`）与 `docs/site/`（tutorial、practice、probe、截图、txt 片段）。若担心破坏 URL，可保留 `/static` 挂载点不变、只改磁盘布局 + 增加一个 `/docs-static` 挂载。

---

### P2-5 `app/utils/` 是杂项桶

20 个文件里至少 4 类不该叫 utils：

- 浏览器相关：`browser_profile_identity.py`、`human_mouse.py`、`paste.py`、`file_paste.py`、`system_clipboard.py`
- 安全：`media_access.py`、`media_safety.py`、`remote_resource.py`、`diagnostics_access.py`
- 站点：`site_rules.py`、`site_url.py`、`site_discovery.py`、`tab_route_groups.py`、`model_routing.py`
- 媒体：`image_handler.py`、`image_validation.py`、`media_*`

**建议**：`utils/` 只留真正无领域含义的（`platform.py`、`similarity.py`、`attachments.py`）；其余下沉到 `core/browser/`（粘贴/鼠标/剪贴板）、`core/security/`（访问控制）、`services/sites/`（站点规则与路由）。

### P2-6 `app/models/schemas.py` 1511 行，混装三套协议

OpenAI 消息、Anthropic 消息、站点配置、预设字段白名单放在一个文件里。**建议** `models/{openai.py,anthropic.py,config.py,common.py}`，`schemas.py` 保留为门面（与现有兼容策略一致）。

### P2-7 `docs/` 现行文档与历史日志混放

`docs/` 下 22 个 md：`architecture/`(3)、`review/`(3)、`roadmap/`(3)、`migrations/`(3)、`plans/`(1)、`examples/`(2)，根下 7 个专题 md。

`DEVLOG-*.md`（几百行的过程日志）、`CODE_REVIEW_REPORT.md`（一次性报告）、`ANALYSIS-AND-ROADMAP-2026-09.md`（已过期路线图）与「用户要看的 `attachments.md` / `workflow-studio.md`」混在同一层。

**建议**：

```
docs/
├── architecture/   # 现行设计（chat-pipeline、browser-driver、exception-policy）
├── guides/         # 面向用户/贡献者（uv、attachments、site-studio、workflow-studio、operations-studio）
├── adr/            # 决策记录（扁平化、SQLite、驱动层、进程模型）
├── history/        # review/ roadmap/ plans/ DEVLOG
└── assets/         # 截图与示意图（从 assets/ 与 static/*.png 迁入）
```

---

## 4. 目标结构

### 4.1 「最小改动版」（推荐先做到这里，2–3 周）

```
universal-web-api/
├── pyproject.toml            # + dynamic version, + [project.scripts], + importlib mode
├── uv.lock  VERSION  LICENSE  CHANGELOG.md
├── main.py                   # 仅 3 行：from app.cli import main; main()
├── app/
│   ├── __init__.py           # 唯一 __version__ 来源
│   ├── __main__.py           # 3 行
│   ├── cli.py                # bootstrap + uvicorn
│   ├── factory.py            # create_app()
│   ├── api/
│   │   ├── openai/  anthropic/  admin/
│   │   └── routes.py
│   ├── core/
│   │   ├── driver/  browser/  tab_pool/  workflow/
│   │   ├── parsers/  extractors/  page_capture/
│   │   ├── observability/    # 原 config_parts 的日志部分
│   │   └── paths.py          # PROJECT_ROOT / STATIC_DIR / DATA_DIR
│   ├── services/{config,commands,storage,update}/  ...
│   ├── models/{openai,anthropic,config,common}.py
│   ├── runtime/  utils/  worker/
│   └── update/               # 原根目录 updater.py 等
├── config/                   # 只读分发配置
├── var/                      # 可写运行时（整目录 ignore）
├── web/                      # 前端（原 static 运行时部分）
├── docs/{architecture,guides,adr,history,assets}/
├── tools/                    # 原根目录维护脚本
├── tests/{unit,contract,integration,ui,manual,fixtures}/
└── .github/workflows/ci.yml
```

### 4.2 「理想版」（可选，需要产品决策）

如果将来要**被别人 `pip install`、或作为库被复用**，再引入 src：

```
src/universal_web_api/...
```

届时的前置条件是：包完全自洽（P0-1 已解）、无 CWD 相对路径（P1-2 已解）、数据目录可配置（P1-3 已解）、`create_app()` 工厂存在（P1-6 已解）。**这四条不满足就上 src，只会把问题藏得更深。**

---

## 5. 迁移路线

### 阶段 A：低风险清理（已执行 ✅）

1. 删 `app/core/browser.py`（死文件）、`CHANGELOG-3.0.1.md`（与 `CHANGELOG.md` 重复）、`app/core/parsers/mimo_parser.py`（与 runtime 版重复）。
2. `generate.py` 移出根目录（或删除）；`perf_watch.py` → `tools/`。
3. 新增 `.github/workflows/ci.yml`（`pytest -m "not browser and not real_browser"` + `ruff check`）。
4. `VERSION` 与 `pyproject.toml` 建立单一来源：`dynamic = ["version"]` + backend 读 `app/__init__.py.__version__`，并加一条一致性测试。
5. `tests/manual/` 收走 7 个手动脚本 + `norecursedirs`。

> 全部是「删除/移动 + 加 CI」，不动业务逻辑，可一次性合并。

### 阶段 B：包边界与入口（B1/B2 已执行 🟡，其余待做）

1. `updater.py` / `update_preserve.py` / `clean_profile.py` → `app/update/`；根目录留 3 行转发壳（一个版本周期后删）。
2. `app/factory.py` 抽出 `create_app()`；`app/cli.py` + `app/__main__.py`；`[project.scripts]`。
3. `app/core/paths.py` 统一路径，消除 24 处 CWD 相对字面量（可先用测试兜住，再逐个替换）。
4. `config/` → 只读；新增 `var/` + `UWAPI_DATA_DIR`，写迁移脚本（复用 `engine_migrations.py` 框架），`update_preserve` 清单收敛为 `var/`。
5. 路由按 §P1-4 / P1-5 重排（**URL 一律不变**，只改文件与 router 组合）。

> 每一项都应先补一条「结构断言测试」（例如「`app` 内不得出现顶层 `from updater import`」「`Path(\"static\")` 不得出现」），再动代码——这与仓库已有的棘轮风格一致。

### 阶段 C：可选重构（按需）

- `app` → `uwapi` 改名。**成本极高**：`update_preserve` 路径、用户 `chrome_profile/`、外部脚本、文档、`docs/` 里的所有示例都要同步；建议**除非要发布到 PyPI，否则不做**。
- `src/` 布局：只在「确实要被安装」时做。
- TypeScript / 前端构建：当前「无构建、Vue 全局构建 + 预编译 Tailwind」是**有意的取舍**（离线可用、更新器只替换文件）。除非前端规模再翻倍，否则不建议引入。

---

## 6. 明确「不要做」的清单

| 不要做 | 原因 |
| --- | --- |
| 为了「符合 PyPA 推荐」整体迁到 `src/` | 项目 `package = false`、不构建 wheel；收益 0，破坏更新器/路径/启动命令 |
| 一次性把 `app` 改名成 `uwapi` | 路径被更新器、用户数据、文档、脚本硬引用；应无限期延后 |
| 引入前端构建链（Vite/TS）「因为更现代」 | 现行「零构建 + 预编译」让 `git pull` 即可运行、更新器只需替换文件；引入后 CI 与发布流程复杂度翻倍 |
| 把 `config/sites/` 挪进包内 | 站点适配器是**数据**，需要在线更新与用户覆盖；进包后既不能热更也不能保留本地覆盖 |
| 用 `importlib.metadata` 取代 `VERSION` | `package = false` 时没有 distribution metadata，运行时会抛 `PackageNotFoundError` |
| 把 `static/` 全部搬进 `app/` 包 | 会被当作 package data，打包语义错位；且 FastAPI 挂载需要磁盘路径 |

---

## 7. 可执行清单（可直接转 issue）

- [x] **A1** 删除 `app/core/browser.py`（模块/包同名死文件）
- [x] **A2** 合并 `CHANGELOG-3.0.1.md` → `CHANGELOG.md`
- [x] **A3** 删除重复的 `app/core/parsers/mimo_parser.py`（保留 runtime 版）
- [x] **A4** `generate.py` 移出根目录或删除（含硬编码 `C:\Users\QIU\...`）
- [x] **A5** `perf_watch.py` → `tools/`
- [x] **A6** 新增 `.github/workflows/ci.yml`
- [x] **A7** 版本单一来源（`dynamic = ["version"]` + 一致性测试）
- [x] **A8** `tests/manual/` + `norecursedirs`
- [x] **B1** `updater/update_preserve/clean_profile` → `app/update/`（保留转发壳）
- [x] **B2** `app/__main__.py`（`python -m app`）；`create_app()` 工厂与 `[project.scripts]` 仍待做（`package = false` 下 console script 无法安装）
- [ ] **B3** `app/core/paths.py`，消灭 24 处 CWD 相对路径
- [ ] **B4** `config/`（只读）与 `var/`（可写）分离 + 迁移脚本
- [x] **B5**（部分）`app/api/admin/` 已建立：`/api/tab-pool/*`、`/api/pool/status`、`/api/presets/*` 已迁入；`openai/`、`anthropic/` 目录仍待建
- [x] **B6**（部分）`tab_routes.py` 5142→4616；`chat.py` 去掉全部 `/api/` 端点；`system.py` 待拆
- [ ] **B7** `core/config_parts` → `core/observability/`；`*_parts/` 去后缀
- [ ] **C1** `static/` → `web/` + `docs/site/`
- [ ] **C2** `docs/` 按 `architecture/guides/adr/history/assets` 重排
- [ ] **C3** `app/utils/` 按领域下沉
- [ ] **C4** `models/schemas.py` 拆协议

---

## 附：参考

- PyPA · [src layout vs flat layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- PyPA · [Single-sourcing the Project Version](https://packaging.python.org/en/latest/discussions/single-source-version/)
- PyPA · [Writing your pyproject.toml](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
- pytest · [Good Integration Practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html)
- FastAPI · [Bigger Applications — Multiple Files](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- Python docs · [`__main__` — Top-level code environment](https://docs.python.org/3/library/__main__.html)
- Real Python · [Python Application Layouts](https://realpython.com/python-application-layouts/)
- setuptools · [Package Discovery（flat vs src）](https://setuptools.pypa.io/en/latest/userguide/package_discovery.html)
- 本仓库 · [`docs/PROJECT-STRUCTURE.md`](./PROJECT-STRUCTURE.md)（现状逐文件说明）
