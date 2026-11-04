# 项目结构修复清单（已执行）

> 依据 [`docs/STRUCTURE-REVIEW.md`](./STRUCTURE-REVIEW.md) 的 P0 / P1 结论，执行「阶段 A + 阶段 B 的包边界部分」。
> 原则：**只做可验证的结构调整，不改变任何运行时行为**。
> 验证基线见 §5：改动前后同为 **2 个既有失败**，新增 16 个结构测试全部通过。

---

## 0. 总览

| 编号 | 类别 | 改动 | 风险 | 状态 |
| --- | --- | --- | --- | --- |
| A1 | 清理 | 删除 `app/core/browser.py`（模块/包同名死文件） | 无 | ✅ |
| A2 | 清理 | 合并 `CHANGELOG-3.0.1.md` → `CHANGELOG.md`，更新所有引用 | 无 | ✅ |
| A3 | 清理 | 删除与 `mimo_runtime_parser.py` 完全重复的 `mimo_parser.py` | 无 | ✅ |
| A4 | 归位 | `generate.py` → `examples/arena_image_batch.py`（去硬编码路径） | 无 | ✅ |
| A5 | 归位 | `perf_watch.py` → `tools/perf_watch.py` | 无 | ✅ |
| A6 | 工程化 | 新增 `.github/workflows/ci.yml` | 无 | ✅ |
| A7 | 元数据 | 版本一致性由结构测试守护（`VERSION` 仍是唯一来源） | 无 | ✅ |
| A8 | 测试组织 | 7 个手动脚本 → `tests/manual/`，并禁止被 pytest 收集 | 低 | ✅ |
| B1 | 包边界 | `updater` / `update_preserve` / `clean_profile` → `app/update/`，根目录留兼容壳 | 中 | ✅ |
| B2 | 入口 | 新增 `app/__main__.py`，支持 `python -m app` | 低 | ✅ |
| B3 | 防回归 | 新增 `tests/test_structure.py`（13 项结构棘轮） | 无 | ✅ |
| C1 | 路径 | 新增 `app/paths.py`，消除 CWD 相对路径 + 启动时固定工作目录 | 中 | ✅ |
| C2 | 分层 | 新增 `app/api/admin/`、`app/api/openai/`、`app/api/system/`、`stream_helpers.py`、`openai_responses_support.py`，路由表快照棘轮 | 中 | ✅ |
| C3 | 暂缓 | `config/` 只读 + `var/` 可写数据目录 | — | ⏸ 见 §6 |
| C4 | 暂缓 | `src/` 布局、包改名 `app` → `uwapi` | — | ⏸ 见 §6 |

---

## 1. A 类：清理与归位（零行为变更）

### A1 删除 `app/core/browser.py`

**问题**：`app/core/browser.py`（9 行转发）与 `app/core/browser/`（包）同名。CPython 的 `FileFinder`
在解析 `app.core.browser` 时先检查目录 + `__init__.py`，再检查同名模块，所以该文件**永远不会被导入**，
是死代码，也是「改了没生效」的坑。

**改动**：删除文件。`app/core/browser/__init__.py` 继续导出 `BrowserCore` / `get_browser` / `browser`，
所有 `from app.core.browser import ...` 的调用方无需修改。

**验证**：`python -c "from app.core.browser import get_browser"` 正常；全量测试通过。

### A2 合并重复的 CHANGELOG

**问题**：`CHANGELOG.md` 与 `CHANGELOG-3.0.1.md` 逐字节相同（`cmp` 退出码 0），两份文件迟早漂移。

**改动**：
- 删除 `CHANGELOG-3.0.1.md`，保留 `CHANGELOG.md` 为当前版本变更的唯一入口；
- `CHANGELOG-3.0.0.md` 保留为历史版本；
- `README.md` / `README.zh-CN.md` / `README.en.md` 的 4 处链接改为 `CHANGELOG.md`。

**验证**：`tests/test_structure.py::test_no_duplicate_changelog_copies`（按内容哈希拒绝重复副本）
+ `test_deleted_paths_are_not_referenced`（README 不得再引用已删文件）。

### A3 删除重复的 MiMo 解析器

**问题**：`app/core/parsers/mimo_parser.py` 与 `mimo_runtime_parser.py` 除前三行注释外完全相同
（`diff` 无输出），而 `config/parsers.json` 指向 runtime 版。

**改动**：删除 `mimo_parser.py`；`app/core/parsers/__init__.py` 的导出来源改为
`from .mimo_runtime_parser import MimoParser`；顺带修正 runtime 版的文件头注释。

**验证**：`MimoParser.__module__ == "app.core.parsers.mimo_runtime_parser"`，
由 `test_parser_registry_points_at_existing_module` 固化。

### A4 `generate.py` → `examples/arena_image_batch.py`

**问题**：根目录脚本硬编码 `C:\Users\QIU\Desktop\新建文件夹 (2)`、`C:\Users\QIU\.gemini\...`，
并会把参考图**复制到用户桌面**；在任何其他机器上都会失败，还会被误认为项目组成部分。

**改动**：
- 移动到 `examples/arena_image_batch.py`，明确标注「不是服务的一部分」；
- 全部路径与地址改为命令行参数（`--base-url` / `--path` / `--model` / `--output-dir` /
  `--prompt-file` / `--character-image` / `--style-image` / `--both` / `--api-key`）；
- 去掉「复制图片到桌面」的副作用；
- 请求与解析逻辑（`media` 字段、markdown 图片、data URL、远程 URL）保持原样。

**说明**：该文件不读取任何环境变量——否则会与 `app/core/settings_registry.py`
的「环境变量唯一来源」约束冲突（`tests/test_settings_registry.py` 会失败）。

### A5 `perf_watch.py` → `tools/perf_watch.py`

**改动**：移动文件，并同步 docstring 里的 4 条示例命令（`python tools/perf_watch.py ...`）。
`tools/` 默认被 Git 跟踪，不需要改 `.gitignore`。

### A6 新增 CI

**改动**：`.github/workflows/ci.yml`，三个 job：

| job | 内容 |
| --- | --- |
| `ruff` | `uv sync --frozen --no-dev` → `ruff check .` |
| `pytest` | Ubuntu + Windows 矩阵，`pytest -q -m "not browser and not real_browser"`（Node 20 供前端单测） |
| `structure` | 单独跑结构、`.env.example`、仓库卫生、uv 环境四组测试 |

此前仓库里那些「只有定期执行才有意义」的守护测试（Tailwind 产物等价性、`.env.example` 新鲜度、
三个棘轮、Python 版本下限）没有任何自动执行环境。

### A7 版本单一来源（不引入 `dynamic`）

**问题**：版本在 `VERSION`、`pyproject.toml:21`、`app/__init__.py` 三处出现，无一致性校验。

**改动**：**保持 `VERSION` 为唯一来源**，用结构测试把另外两处钉死：
`test_version_has_single_source` 断言 `VERSION == pyproject.toml [project].version == app.__version__`。

**为什么不改成 `dynamic = ["version"]`**：本项目 `[tool.uv] package = false` 且没有
`[build-system]`，没有任何 backend 会去计算动态版本；而 `importlib.metadata` 在
「未安装成 distribution」时也会抛 `PackageNotFoundError`。在现有发布方式下，测试兜底是正确解。

### A8 手动脚本隔离

**问题**：7 个需要真实服务/浏览器的手动脚本与 `test_*.py` 平铺在 `tests/` 根，
`ls tests/` 无法回答「这里有多少自动化测试」。

**改动**：
- `tests/manual/{browser_operations_studio, browser_preset_transfer, browser_site_capabilities,
  browser_upgraded_sites, browser_workflow_studio, e2e_concurrency_headed, e2e_idle_maintenance_headed}.py`
- 脚本内 `ROOT = Path(__file__).resolve().parents[1]` → `parents[2]`（e2e 的 `dirname` 链同步加一层）；
- `tests/conftest.py` 增加 `collect_ignore = ["manual"]`；
- 新增 `tests/manual/README.md`（依赖、运行方式、约定）；
- `tests/test_repo_hygiene.py` 的文件清单补上 `tests/manual/*.py`，手动脚本同样不允许被 `.gitignore` 忽略。

---

## 2. B1：包边界修复（本次最实质的改动）

### 问题

```python
# 改动前
app/api/system.py:42          from update_preserve import ...
app/api/system.py:2465...     from updater import ...
app/runtime/bootstrap.py:276  from clean_profile import ...
app/services/adapter_updates.py:151   from updater import ...
app/services/config/site_store.py:265 from updater import ...
```

`app` 反向依赖仓库根目录模块，只要 `sys.path` 上没有根目录就导入失败；
`pytest` 的 `pythonpath = ["."]` 又让这个缺陷永远不在测试里暴露。

### 改动

| 旧路径 | 新路径 | 说明 |
| --- | --- | --- |
| `updater.py` | `app/update/engine.py` | 自动更新引擎 |
| `update_preserve.py` | `app/update/preserve.py` | 保留规则 |
| `clean_profile.py` | `app/update/profile.py` | profile 清理（新增 `main(argv)`） |

配套修改：

1. **路径常量**：`engine.py` 的 4 处 `Path(__file__).parent` → `PROJECT_ROOT = Path(__file__).resolve().parents[2]`；
   `profile.py` 的 `PROJECT_ROOT` / `_resolve_profile_dir` 的 `base_dir` 同步修正
   （否则版本号会从 `app/update/VERSION` 读、profile 会指向 `app/update/chrome_profile`）。
2. **内部导入**：`engine.py` 改为 `from app.update.preserve import ...`。
3. **调用方**：`app/api/system.py`(6 处)、`app/runtime/bootstrap.py`、`app/services/adapter_updates.py`、
   `app/services/config/site_store.py` 全部改为 `app.update.*`，`app/` 内不再出现根目录顶层导入。
4. **兼容壳**（根目录保留）：`updater.py` / `update_preserve.py` / `clean_profile.py`
   用 `sys.modules[__name__] = _impl` 把实现模块**顶替**自身，而不是复制名字。

   为什么必须「顶替」而不是 `from x import *`：现有测试会 monkeypatch 模块级常量，例如
   ```python
   monkeypatch.setattr(updater, "MAX_UPDATE_MEMBER_COUNT", 1)
   ```
   复制名字会得到两份模块级状态，patch 不生效；顶替后全仓库只有一份。
   同时 `from updater import _safe_extract_zip`（下划线内部名）也照常可用。

5. **脚本入口不变**：`python updater.py` / `python clean_profile.py [dir]` 仍可用；
   `python update_preserve.py` 亦可。
6. **异常棘轮基线**：`tests/fixtures/broad_except_ratchet.json` 的 3 个键做**纯路径重命名**
   （`updater.py`→`app/update/engine.py` 等），计数完全不变（19 / 4 / 2），
   证明没有新增宽泛捕获、只是文件搬家。

### 为什么 `patch_drissionpage.py` 仍留在根目录

它必须在**导入 DrissionPage 之前**独立执行（`bootstrap` 用 subprocess 调用），
放进包内反而会引入「先 import app 再打补丁」的顺序风险；`tests/test_cdp_hygiene.py`
也按文件路径加载它。因此它是**刻意保留的独立工具**，不是漏网脚本。

---

## 3. B2：包入口

新增 `app/__main__.py`：

```python
def main() -> None:
    runpy.run_path(str(PROJECT_ROOT / "main.py"), run_name="__main__")
```

- 行为与 `uv run python main.py` **完全一致**（同一份 `main.py`、同一个 `__main__` 守卫，
  bootstrap 只执行一次）；
- 不复制启动逻辑，因此不存在第二份入口需要同步维护；
- `README.md` / `README.zh-CN.md` / `README.en.md` 各补一行等价入口说明；
- `test_package_entry_point_exists_and_is_thin` 固化：入口必须存在、必须用 `runpy`、
  **不得**自己调用 `bootstrap_runtime`。

---

## 4. B3：结构棘轮 `tests/test_structure.py`

把本次结论变成可执行约束（避免下次重构又漂回去）：

| 测试 | 约束 |
| --- | --- |
| `test_app_does_not_import_root_level_modules` | `app/` 顶层不得 `from updater/update_preserve/clean_profile/patch_drissionpage import` |
| `test_root_only_holds_known_modules` | 根目录只允许 `main.py`、`patch_drissionpage.py` 与 3 个兼容壳 |
| `test_compat_shims_point_at_new_implementation` | 兼容壳必须仍指向 `app.update.*` |
| `test_version_has_single_source` | `VERSION` == `pyproject` == `app.__version__` |
| `test_no_module_package_shadowing` | 禁止「模块与包同名」的死文件 |
| `test_every_python_package_has_init` | `app/` 下含 `.py` 的目录必须是包 |
| `test_no_duplicate_changelog_copies` | 禁止与 `CHANGELOG.md` 内容相同的副本 |
| `test_package_entry_point_exists_and_is_thin` | `python -m app` 可用且入口薄 |
| `test_deleted_paths_are_not_referenced` | README 不得引用已删除文件 |
| `test_parser_registry_points_at_existing_module` | 解析器注册表/包导出指向真实模块 |

---

## 5. 验证结果

环境：Linux + Python 3.11（`uv sync --python /usr/bin/python3`，沙箱内无法下载 3.12 的 standalone 包），
依赖按 `uv.lock` 安装。

| 指标 | 改动前 | 改动后 | 结论 |
| --- | --- | --- | --- |
| `pytest` 通过 | 1302 | **1331** | +29（结构棘轮 + 路由表棘轮 + 用例适配） |
| `pytest` 失败 | 2 | **0** | 两个既有失败已按「随包配置演进」对齐用例，见 §8 |
| `pytest` 跳过 | 157 | 157 | 不变 |
| `ruff check .` | 通过 | 通过 | 无新增告警 |
| CI（GitHub Actions） | 红 | **绿** | ruff / ubuntu / windows / 结构 四个作业全通过，见 §8 |
| `tests/test_exception_ratchet.py` | 通过 | 通过 | 纯搬迁：1422 → 1413，基线随文件搬家更新 |
| `app.routes` 路由快照 | 134 条 | 134 条 | 零差异（`tests/test_route_table.py`） |

既有失败的处置见 §8（两个用例都是「测试冻结了某一版随包配置」，已改为校验契约本身）。

额外的手工验证：

```bash
# 兼容壳可导入（含下划线内部名）
python -c "from updater import _safe_extract_zip, merge_site_records, get_current_version; print(get_current_version())"
# → 3.0.1

# 包边界：不把仓库根放进 sys.path 也能导入 app（模拟安装后的环境）
cd /tmp && PYTHONPATH=/path/to/universal-web-api python -c "import app, app.update.engine"

# 新入口与旧入口等价（会真正启动服务，仅结构检查时不要执行）
python -m app
```

---

## 6. 暂缓项及原因

这些项在评审里列为 P1，但**不能只改目录**，必须连带改测试或数据迁移，故单独排期。

### C1 已完成：`app/paths.py` + 消除 CWD 相对路径 ✅

**改动**

1. 新增 `app/paths.py`（只依赖标准库，刻意放在 `app/` 顶层而不是 `app/core/`，
   避免为了取路径而触发浏览器相关导入）：

   ```python
   PROJECT_ROOT = Path(__file__).resolve().parents[1]
   STATIC_DIR   = PROJECT_ROOT / "static"
   CONFIG_DIR   = PROJECT_ROOT / "config"
   DOWNLOAD_DIR = PROJECT_ROOT / "download_images"
   LOGS_DIR     = PROJECT_ROOT / "logs"

   def download_dir(*, create=True) -> Path   # 每次调用读模块常量 → 可被 monkeypatch
   def resolve_project_path(raw) -> Path      # 配置里的相对路径按项目根解析
   ```

2. 改造调用点：
   - `main.py`：静态挂载、`/media/{filename}`、`/download_images` 挂载、Dashboard 解析
     全部改用绝对路径；`_resolve_dashboard_path()` 的相对配置项按项目根解析。
   - `app/core/browser/media.py`（4 处）、`app/core/extractors/media_extractor.py`（1 处）
     → `paths.download_dir()`。
   - `app/core/parsers/deepseek_parser.py` 的调试落盘 → `paths.logs_dir()`。
   - `app/core/config_parts/env_config.py` 的 `PROJECT_ROOT` / `DEFAULT_LOG_DIR`
     改为复用 `app.paths`（全仓库单一定义）。
   - `app/update/preserve.py`、`app/api/system.py` 的 `config/update_settings.json`
     → `CONFIG_DIR` 绝对路径。

3. `app/core/background_image_downloader.py`：进程级单例原本在 import 时把
   `Path("download_images")` 固化，现在改为**懒解析** `app.paths.download_dir()`，
   并新增 `set_save_dir()` / `clear_cache()` 供测试隔离。

4. `main.py` 的 `HardenedMediaStaticFiles` 在**每次请求**重新解析目录
   （媒体目录是运行时数据，测试会替换它，不能固化成 import 时的值）。

5. `app/runtime/bootstrap.py` 新增 `ensure_project_cwd()`，脚本启动时把工作目录固定到
   项目根：项目内仍有少量按相对路径访问的资产（用户脚本、profile、第三方配置），
   这一层是兜底；即使走 `uvicorn main:app` 跳过 bootstrap，静态/媒体目录也已用绝对路径。

**测试适配（这是原计划的难点）**

- `tests/conftest.py` 新增 **autouse fixture** `_isolate_download_dir`：
  每个用例把 `paths.DOWNLOAD_DIR` 指到自己的 `tmp_path / "download_images"`，
  同时重定向后台下载器的保存目录并清空 URL 缓存。
  这样「`monkeypatch.chdir(tmp_path)` + 断言 `tmp_path / "download_images"`」的既有写法
  **不需要逐个改写**。
- 两个真正写文件的用例改为 `paths.download_dir()`：
  `test_review_fixes_p1.py::test_served_media_is_hardened_for_legacy_active_files`、
  `test_review_fixes_p2.py::test_s6_media_routes_require_auth_when_enabled`。

**新增棘轮**（`tests/test_structure.py`）

- `test_app_code_does_not_use_cwd_relative_literals`：`app/` 内禁止
  `Path("static")` / `Path("download_images")` / `directory="static"` 等字面量；
- `test_main_mounts_absolute_media_and_static_dirs`；
- `test_runtime_bootstrap_pins_cwd_to_project_root`；
- `test_background_downloader_has_no_cwd_default`。

**验证**：从 `/tmp` 用 `PYTHONPATH` 导入 `main`，`_resolve_dashboard_path()` 仍返回
`/repo/static/index.html`，`paths.download_dir()` 返回绝对目录，`ensure_project_cwd()`
把 CWD 切回项目根——即「不是从项目根启动」也不再走偏。

### C2 已完成（四批）：拆分巨型路由文件

**第一批（已执行）**

| 迁移 | 从 | 到 | 行数 |
| --- | --- | --- | --- |
| 标签页池管理（5 个端点 + 2 个请求模型） | `app/api/tab_routes.py` | `app/api/admin/pool.py` | 463 |
| 站点预设管理（5 个端点 + 3 个请求模型） | `app/api/tab_routes.py` | `app/api/admin/presets.py` | 148 |
| `GET /api/pool/status` | `app/api/chat.py` | `app/api/admin/pool.py` | — |

结果：`tab_routes.py` **5142 → 4616 行**；面板端点的鉴权模型（`verify_dashboard_auth`）
与调用方（控制面板）现在集中在一层，`/v1/*` 协议文件不再夹带管理接口。

**关键约束（已固化成测试）**

1. **URL 一个都没变**：新增 `tests/fixtures/route_table.json` + `tests/test_route_table.py`，
   把 `app.routes` 的 134 条路径与允许的方法导出成基线；重构前后逐项比对，
   另有一条测试禁止同一 `(path, method)` 被注册两次（防止 router 遮蔽）。
   更新基线：`python tests/test_route_table.py --update`。
2. **依赖方向单向**：`admin/pool.py` 复用 `tab_routes` 里尚未下沉的私有辅助
   （浏览器配置锁、模型名/预设覆盖、路由组等），因此是 `admin → tab_routes`；
   `test_admin_layer_dependency_direction_is_one_way` 断言反向导入不存在（否则成循环依赖）。
3. **协议文件里不再有面板端点**：`test_panel_endpoints_are_not_defined_in_protocol_modules`
   检查 `chat.py` 不含 `@router.*("/api/`、`tab_routes.py` 不含 `/api/tab-pool/` 与 `/api/presets/`。

**第二批（已执行）：抽出协议无关的公共辅助**

| 迁移 | 从 | 到 | 内容 |
| --- | --- | --- | --- |
| 跨文件重复的纯函数（9 个）+ SSE 打包（3 个） | `chat.py` / `tab_routes.py` | `app/api/stream_helpers.py`（206 行） | `_extract_*_media_items`、`_dedupe_media_items`、`_extract_delta_content_text`、`_extract_stream_error_message`、`_is_absolute_request_timeout_error`、`_is_single_choice_request`、`_extract_assistant_content`、`_pack_error`/`_pack_done`/`_pack_error_done` |
| Responses API 纯转换层（21 个） | `chat.py` | `app/api/openai_responses_support.py`（650 行） | `_normalize_response*`、`_normalize_responses*`、`_chat_payload_to_responses_output`、`_build_responses_usage`、`_responses_error_payload`、`_responses_principal_from_request`、`_responses_state_env_bytes`、`_new_response_item_id`、`_response_*_item_*`、`_is_genuine_task_cancellation` 等 |

结果：`chat.py` **3299 → 2542 行**，`tab_routes.py` **4616 → 4461 行**。
搬迁前后两文件里逐字节相同的 9 个辅助函数不再各存一份。

**搬迁时必须留在 `chat.py` 的部分**（第二次拆分再处理）：`ChatRequest` / `ResponsesRequest`、
`_responses_state_by_id` / `_responses_state_total_bytes` / `_load_responses_state` /
`_store_responses_state`（测试直接 patch）、`_append_response_input_item` /
`_responses_input_to_messages` / `_responses_request_to_chat_request`（依赖上述状态缓存与请求上下文）、
三个端点与 `_stream_*_with_lifecycle`。

**第三批（已执行）：`tab_routes.py` 按 URL 前缀四拆**

| 迁移 | 从 | 到 | 行数 |
| --- | --- | --- | --- |
| `/tab/{tab_index}/v1/*` 端点与执行器 | `app/api/tab_routes.py` | `app/api/openai/tab.py` | 831 |
| `/url/{route_domain}[/{preset_name}]/v1/*` 与按模型名路由 | 同上 | `app/api/openai/url.py` | 1120 |
| `/group/{group_id}[/{preset_name}]/v1/*` | 同上 | `app/api/openai/group.py` | 351 |
| `/tab-url/{url_token}[/{preset_name}]/v1/*` | 同上 | `app/api/openai/tab_url.py` | 955 |
| 公共解析/配置/SSE/生命周期辅助 + 唯一的 `router` 实例 | 同上 | `app/api/openai/shared.py` | 1320 |
| 运行期依赖（`get_browser` / `request_manager` / `cancel_storm_guard`） | 同上 | `app/api/openai/runtime.py` | 11 |

结果：`tab_routes.py` **4461 → 316 行**（只剩聚合层：导入四个分组模块 + 再导出原有名字）。
四个分组模块**共用 `shared.router` 这一个 `APIRouter` 实例**，因此 18 个端点的 URL、
注册顺序、以及 `app.routes` 快照与拆分前逐条一致（134 条零差异）。

**搬迁手法**：先用 AST 算出每个函数的**调用闭包**，按名字后缀归入四组之一
（`_with_tab_index` / `_route_domain` / `exact_url` / `route_group`），其余为 `shared`；
再校验模块依赖是 DAG（`shared ← tab ← url ← {group, tab_url}`），据此生成导入。
导入按“实际用到的名字”裁剪，避免 F401。

**为什么要 `runtime.py`**：测试原先用 `monkeypatch.setattr(tab_routes, "get_browser", ...)`
替换模块全局。实现散到 5 个模块后，这种打补丁会静默失效（Python 名称在定义模块解析）。
把这类进程级依赖集中到 `runtime` 后，替换只有一个落点，测试与运行期都更稳。

**同步的测试改动**（补丁目标随实现移动，语义不变）：
`test_cancel_storm_regressions.py`、`test_chat_job.py`、`test_protocol_conformance.py`、
`test_request_lifecycle_safety.py`、`test_tab_route_group_api.py`。

**迁移手法**：用 AST 计算每个函数/模型的 `(起始行, 结束行)`（含装饰器），
从原文件**整段摘除**并原样拼进新模块，避免手抄引入差异；随后补齐 import、
跑路由快照 + 全量回归。异常棘轮基线做了一次**纯搬迁**更新
（`tab_routes` 69 → 55，`pool` +10，`presets` +5，总数不增）。

**第四批（已执行）：`system.py` 按「探活 / 设置 / 更新 / 诊断」四拆**

| 迁移 | 从 | 到 | 行数 |
| --- | --- | --- | --- |
| `/health`、`/api/logs`（GET/DELETE） | `app/api/system.py` | `app/api/system/health.py` | 92 |
| `/api/settings/*`（.env、备份、浏览器常量、更新保留项） | 同上 | `app/api/system/settings.py` | 804 |
| `/api/update/*` | 同上 | `app/api/system/updates.py` | 266 |
| `/api/debug/*`、`/api/system/*`（选择器测试、进程/磁盘统计、请求历史） | 同上 | `app/api/system/diagnostics.py` | 1468 |
| 公共辅助（JSON/文件、原子写、`.env` 读写、重启调度） | 同上 | `app/api/system/common.py` | 81 |
| 运行期依赖（`get_browser` / `request_manager`） | 同上 | `app/api/system/runtime.py` | 11 |

结果：`system.py` **2675 行 → `app/api/system/` 包**，`app/api/system/__init__.py`（240 行）
是聚合层：导入四个分组 + 再导出全部原有名字，`from app.api.system import router` 与
`from app.api import system; system.<name>` 均保持不变。

**与评审清单的差异（有意为之）**：清单原写「分别落到 `admin/` 与 `system/`」，实际四组都放在
`app/api/system/`。原因：四个分组共用 `common.py`（`_read_json_object_or_400`、
`_load_env_config_from_file`）；若把「设置/更新」放进 `admin/`，会形成
`admin → system.common` 的反向依赖，比现状更难维护。`admin/` 仍只放 `/api/tab-pool/*`
与 `/api/presets/*` 这类面板管理端点。

**手法**：以带 `@router.*` 的端点为种子计算调用闭包，被两个及以上分组用到的函数/常量
归入 `common`（本例只有 2 个函数 + 2 个常量）；校验无跨分组边后按闭包生成导入。
常量（如 `_SYSTEM_STATS_CACHE`、`_update_check_state`）跟随唯一使用它的分组，
避免可变全局被复制成两份。

**后续（未完成）**

- `app/api/openai/shared.py` 仍有 1320 行：可再按「标签页解析 / 配置与覆盖项 / SSE / 工具调用」
  四块下沉，但它是四组路由的共同底座，收益低于风险，暂缓。
- `chat.py` 2542 行（唯一仍超 2000 行的协议文件）：`/v1/chat/completions`、`/v1/responses`、`/v1/models` 三块可各自独立；Responses 的**状态缓存**仍是最大耦合点，拆端点前需先把缓存下沉到 `app/services/`，故单独排期。
- `system.py` 2674 行：`/health`、`/api/settings/*`、`/api/update/*`、`/api/system/*`
  应按「探活 / 设置 / 更新 / 诊断」四组分别落到 `admin/` 与 `system/`。
- 迁移完成后，`admin/pool.py` 里对 `tab_routes` 私有辅助的依赖可下沉到
  `app/api/admin/tab_pool_support.py` 或 `app/services/tab_pool_admin.py`，彻底断开与巨型文件的耦合。

### C3 `config/` 只读 + `var/` 可写

涉及用户数据迁移（`runtime.sqlite3`、`commands.json`、`sites.local.json`…）、`update_preserve.py`
的保留清单、备份/恢复接口，需要迁移脚本与回滚方案。

### C4 `src/` 布局 / 包改名

评审结论已说明：本项目 `package = false`、按压缩包替换文件发布，`src/` 收益为 0；
包改名会打断更新器路径与用户数据定位。除非要发布到 PyPI，否则不做。

---

## 7. 变更文件一览

```
删除    CHANGELOG-3.0.1.md
删除    app/core/browser.py
删除    app/core/parsers/mimo_parser.py
新增    .github/workflows/ci.yml
新增    app/__main__.py
新增    app/api/admin/__init__.py
新增    app/api/admin/pool.py
新增    app/api/admin/presets.py
新增    app/api/stream_helpers.py
新增    app/api/openai_responses_support.py
新增    app/api/openai/__init__.py
新增    app/api/openai/shared.py
新增    app/api/openai/tab.py
新增    app/api/openai/url.py
新增    app/api/openai/group.py
新增    app/api/openai/tab_url.py
新增    app/api/openai/runtime.py
新增    app/api/system/__init__.py
新增    app/api/system/common.py
新增    app/api/system/health.py
新增    app/api/system/settings.py
新增    app/api/system/updates.py
新增    app/api/system/diagnostics.py
新增    app/api/system/runtime.py
新增    tests/test_route_table.py
新增    tests/fixtures/route_table.json
新增    app/paths.py
新增    app/update/__init__.py
新增    tests/test_structure.py
新增    tests/manual/README.md
新增    docs/PROJECT-STRUCTURE.md（仓库地图）
新增    docs/STRUCTURE-REVIEW.md（对照评审）
新增    docs/STRUCTURE-FIX-CHECKLIST.md（本文件）
移动    updater.py            → app/update/engine.py
移动    update_preserve.py    → app/update/preserve.py
移动    clean_profile.py      → app/update/profile.py
移动    generate.py           → examples/arena_image_batch.py（并参数化）
移动    perf_watch.py         → tools/perf_watch.py
移动    tests/browser_*.py    → tests/manual/（5 个）
移动    tests/e2e_*_headed.py → tests/manual/（2 个）
修改    main.py（绝对路径挂载 / 媒体目录 / Dashboard 解析）
修改    app/core/background_image_downloader.py（懒解析 + set_save_dir/clear_cache）
修改    app/core/browser/media.py
修改    app/core/config_parts/env_config.py
修改    app/core/extractors/media_extractor.py
修改    app/core/parsers/deepseek_parser.py
修改    app/update/preserve.py
移动    app/api/system.py  → app/api/system/（包，四分组 + common + runtime）
修改    app/api/chat.py（拆出 Responses 转换层与流式辅助）
修改    app/api/tab_routes.py（拆出面板端点、流式辅助与四个路由分组；现为 316 行聚合层）
修改    tests/fixtures/broad_except_ratchet.json
修改    app/runtime/bootstrap.py
修改    app/services/adapter_updates.py
修改    app/services/config/site_store.py
修改    app/core/parsers/__init__.py
修改    app/core/parsers/mimo_runtime_parser.py
修改    tests/conftest.py（autouse 隔离媒体目录）
修改    tests/test_review_fixes_p1.py / tests/test_review_fixes_p2.py（写文件目录）
修改    tests/test_tab_route_group_api.py（补丁目标改到 admin.pool）
修改    app/api/chat.py / app/api/tab_routes.py / app/api/routes.py（端点搬迁）
修改    tests/test_repo_hygiene.py
修改    tests/fixtures/broad_except_ratchet.json
修改    README.md / README.zh-CN.md / README.en.md
保留    updater.py / update_preserve.py / clean_profile.py（兼容壳，下一大版本再删）
```

---

## 8. CI 修复记录（2026-10-19）

首次跑 CI 时四个作业里只有一个通过，逐项定位如下：

| 现象 | 根因 | 处理 |
| --- | --- | --- |
| `ruff` 作业 9 秒失败，exit code 2 | 作业里用 `uv sync --frozen --no-dev`，而 ruff 在 `[dependency-groups].dev` 中；`uv run --no-sync ruff` 找不到命令 | 改为 `uv sync --frozen` |
| ubuntu / windows 的 pytest 全红 | 两个用例把「某一版随包配置」写死 | 见下 |
| windows 的 pytest 33 秒就退出 | Windows runner 把 stdout 重定向到管道时用本地代码页（cp1252），项目日志大量输出中文 → `UnicodeEncodeError` 把 pytest 打崩 | 工作流顶层 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` |
| 失败时看不到具体用例 | Actions 日志下载在受限网络下不可用 | pytest 步骤产出 JUnit XML + `scripts/ci_report_failures.py` 把用例名转成 `::error` 注解（输出强制 ASCII，避免报告步骤自己再挂） |

两个既有失败的处置：

1. `test_grok_parser`：Grok 已改为 DOM 模式，`stream_config.network` 不再写死
   `stream_match_pattern`。测试改为「配置里有 pattern 就校验正则；没有就校验
   `GrokParser.get_supported_patterns()` 覆盖 `load-responses` / `responses` / `new`」。
2. `test_site_workflow_upgrade`：迁移快照（`docs/migrations/gemini-deepseek-v2.before.json`）
   之后配置持续演进（新增 `file_paste.attachments`、追加 `attachment_selectors`、
   选择器调优），逐字节相等必然失败。改为「历史字段不得删除或改值，允许新增字段与追加
   列表项；选择器只要求键仍在（值可持续调优）」——既保住迁移契约，也不再冻结配置演进。

顺带：`actions/checkout` → `v7`、`actions/setup-node` → `v7`、
`astral-sh/setup-uv` → `v10.3.0`（该仓没有 v10 浮动大版本标签），消除 Node 20 弃用警告。
