# 代码审查开发日志（2026-09-25 起）

> 用途：记录本轮"全仓库 Bug / 优化点审查"的进度与关键信息，防止上下文压缩或沙盒重启后丢失。
> 最终汇总报告：`docs/review/CODE_REVIEW_REPORT.md`（审查完成后生成）。

## 0. 恢复指引（上下文丢失 / 沙盒重启后先看这里）

1. `cd /home/user/universal-web-api && git pull && git log -1` 确认最新提交点。
2. 阅读本文件的「3. 进度清单」与「4. 发现列表」，从第一个未勾选的模块继续。
3. 推送凭据只写在 `.git/config`（`http.https://github.com/.extraheader`），**不得写入任何被跟踪的文件或日志**；
   若沙盒重启导致 `.git/config` 丢失，需重新向用户确认/使用会话中提供的临时 token 重新配置。
4. 沙盒约束（用户硬性规则）：
   - 每完成一个小项立即 `git add` → `git commit` → `git push origin main`；
   - 禁止安装 Playwright/Chromium、禁止重度 E2E、禁止保留 png 截图；
   - 临时文件用完即删；工作区快照上限约 128MB / 10k 文件。
   - 用户规则里提到的 `node js/build.js dev`、`node js/tests.js` **在本仓库不存在**（前端位于 `static/js`，无构建脚本），
     替代的轻量验证：`node --check <file>`（JS 语法）、`python -m unittest` / `pytest`（纯逻辑测试，跳过 `tests/browser_*.py`）。

## 1. 仓库概况

- 仓库：`lumingya/universal-web-api`，默认分支 `main`，版本文件 `VERSION=3.0.0`。
- 克隆方式：`git clone --filter=blob:none`（部分克隆，保留完整提交历史，.git ≈ 4MB）。
- 规模：323 个跟踪文件；Python 约 12.8 万行（226 个 .py），前端 JS 约 2.8MB（Vue 3 全局构建 + 自研组件）。
- 技术栈：FastAPI + Uvicorn + Pydantic v2 + DrissionPage（CDP 驱动 Chromium）；把已登录的 AI 网页转成
  OpenAI / Anthropic 兼容接口。
- 自动更新：`updater.py` 只从 **GitHub Releases** 拉包（`/releases/latest`），往 `main` 推送文档不会被自动分发。
- 超大文件（审查重点，>3000 行）：`app/api/tab_routes.py`(5142)、`app/core/tab_pool_parts/manager.py`(4512)、
  `app/services/command_engine.py`(3836)、`app/services/config/engine.py`(3341)、`app/core/extractors/media_extractor.py`(3248)、
  `app/core/browser/media.py`(3244)、`app/api/chat.py`(3242)、`app/core/network_monitor.py`(3201)、
  `app/services/command_engine_actions.py`(3166)。

## 2. 审查方法

- 静态：`python -m compileall`、pyflakes/ruff（未定义名、未使用变量、可疑比较等）、`node --check`。
- 测试：运行 `tests/test_*.py` 中的纯逻辑测试（不运行 `tests/browser_*.py` 和依赖真实浏览器的用例）。
- 人工：按模块阅读，重点关注 安全（认证/CORS/命令执行/路径穿越/SSRF/解压）、并发（锁/线程/asyncio 混用）、
  资源泄漏（无界缓存/后台任务）、错误处理、配置与文档一致性。

## 3. 进度清单

- [x] 克隆仓库、确认推送权限、建立开发日志
- [ ] M1 仓库卫生与配置（.env.example / .gitignore / README / VERSION / requirements）
- [ ] M2 静态分析 + 现有单元测试运行结果
- [ ] M3 安全：认证与中间件（main.py / app/api/deps.py / CORS / 管理接口）
- [ ] M4 安全：命令引擎（CMD_ALLOW_UNSAFE_PYTHON_COMMANDS 等）、updater、文件/路径处理、SSRF
- [ ] M5 核心请求链路：chat.py / anthropic_routes.py / streaming_response.py / request_manager.py
- [ ] M6 标签页池：tab_pool_parts/*（manager / session / recovery / idle_maintenance）
- [ ] M7 浏览器与工作流：core/browser/*、core/workflow/*、stream_monitor、network_monitor
- [ ] M8 服务层：tool_calling*、config engine、command_engine*
- [ ] M9 前端：static/js（XSS / v-html / 大文件）
- [ ] M10 汇总报告 `docs/review/CODE_REVIEW_REPORT.md`

## 4. 发现列表（按发现顺序追加；严重度：P0 严重 / P1 高 / P2 中 / P3 低）

| ID | 严重度 | 位置 | 摘要 | 状态 |
| --- | --- | --- | --- | --- |
| （待 M1 起填写） | | | | |

## 5. 工作流水

- 2026-09-25：部分克隆完成；确认 token 具备 push 权限（dry-run 通过）；确认 updater 仅走 Releases；建立本日志。
