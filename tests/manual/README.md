# 手动 / 真实浏览器脚本

这个目录里的脚本**不是 pytest 用例**：它们需要正在运行的服务（默认 `:8000` 或 `:8199`）、
真实的 Chromium，以及已登录的站点。它们默认不被收集（`tests/conftest.py` 的
`collect_ignore`），需要手动执行。

| 脚本 | 依赖 | 说明 |
| --- | --- | --- |
| `browser_operations_studio.py` | Playwright + 本地服务 | 标签页池 / 请求监控页的合成数据 UI 回归 |
| `browser_preset_transfer.py` | Playwright + `:8000` | 预设导入导出的界面回归（拦截写入请求） |
| `browser_site_capabilities.py` | Playwright + AI Studio 站点 | 站点能力卡冒烟测试 |
| `browser_upgraded_sites.py` | Playwright + `:8000` | 升级后的随仓库站点配置冒烟测试 |
| `browser_workflow_studio.py` | Playwright + `:8000` | Workflow Studio 注入回归 |
| `e2e_concurrency_headed.py` | DrissionPage + 有头 Chromium | 空闲冻结/回收下的并发点击输入压测 |
| `e2e_idle_maintenance_headed.py` | DrissionPage + 有头 Chromium | 真实 `TabPoolManager` 的空闲回收检查 |

约定：

- 统一从仓库根目录运行，例如 `uv run python tests/manual/browser_workflow_studio.py`；
- 脚本内的 `ROOT` 已按「本文件位于 `tests/manual/`」修正为 `parents[2]`；
- 需要真实站点登录态，**不要在 CI 里跑**；改动 UI 后请在本地手动执行相关脚本。

需要被自动收集的回归测试请放在 `tests/` 根目录并命名为 `test_*.py`。
