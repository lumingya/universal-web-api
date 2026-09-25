# 依赖升级计划（审查项 H6）

> 状态：**计划**，未修改 `requirements.txt`。编写日期 2026-09-25，分支 `fix/review-p1-p2-b`。
> 审查原文：「`requirements.txt` 的 FastAPI/Uvicorn 约束偏旧；未证实特定 CVE 适用于当前依赖范围。使用锁定依赖与回归测试制定升级计划。」

## 1. 现状

| 包 | requirements.txt 约束 | 当前解析到的版本 | 最新（PyPI，2026-09） |
|---|---|---|---|
| fastapi | `>=0.100.0,<0.110.0` | 0.109.2 | 0.141.1 |
| starlette（fastapi 间接依赖） | 未直接约束（fastapi 0.109 锁 `<0.37`） | 0.36.3 | 1.7.0 |
| uvicorn[standard] | `>=0.23.0,<0.30.0` | 0.29.0 | 0.54.0 |
| pydantic | `>=2.0.0,<3.0.0` | 2.13.4 | — |
| python-multipart | 未安装 | — | — |

其余依赖（DrissionPage、requests、Pillow、regex……）都是开区间或宽区间，已经解析到较新版本，这次不动。

## 2. 已知公告是否适用（核对结论）

| 公告 | 影响范围 | 对当前依赖是否适用 |
|---|---|---|
| CVE-2025-62727 / GHSA-7f5h-v6xp-fcq8：`FileResponse` / `StaticFiles` Range 头二次复杂度 DoS（CVSS 7.5） | starlette `>=0.39.0,<0.49.1` | **不适用**：当前是 0.36.3，早于引入该问题的版本。**注意：升级时如果停在 0.39–0.49.0 就会中招**，下限必须 ≥ 0.49.1。 |
| CVE-2025-54121：大文件 multipart 表单转存磁盘时阻塞事件循环 | starlette `<0.47.2` | 实际**不适用**：项目没安装 `python-multipart`，也没有 `UploadFile`/`Form` 端点，Starlette 无法解析 multipart 表单。 |
| CVE-2024-47874：multipart 表单无大小限制导致 DoS | starlette `<0.40.0` | 同上，没有 multipart 解析能力，**实际不适用**。 |

结论与审查一致：目前**没有已证实可被利用的 CVE**。升级的主要价值是：(a) 避免长期停在无人维护的版本线；(b) 以后如果有人加文件上传端点，不会立刻踩中上面的 multipart 问题。

## 3. 升级试验（已做，环境已清理）

在临时 venv（`--system-site-packages`，只覆盖 web 栈）装 **fastapi 0.141.1 + starlette 1.7.0 + uvicorn 0.54.0**，跑与 CI 相同的非浏览器测试集：

- **750 项：686 通过 / 63 跳过 / 1 失败**（基线是当前锁定版本下 687 通过）。
- 唯一失败：`tests/test_cancel_storm_regressions.py::test_asgi_send_interruption_closes_suspended_generator_before_return[send_error]`。
  原因：新版 Starlette 的 `collapsing_task_group` 直接抛出原始 `OSError`，不再包进 `ExceptionGroup`。
  测试后面的清理断言（生成器被关闭、`finish_request` 恰好调用一次）在新版下**全部成立**，业务行为没有回归，只是测试对异常类型的断言太严。
  → 本提交已把断言放宽为 `pytest.raises((BaseExceptionGroup, OSError))`，新旧两个版本都能通过。
- 用 `-W default::DeprecationWarning` 跑一遍，没有来自 fastapi/starlette/uvicorn 的弃用告警；代码里也没有用 `on_event`、`regex=`、`HTTP_422_UNPROCESSABLE_ENTITY` 这类在新版中被弃用的写法。

## 4. 建议步骤（分阶段，每步单独 PR）

1. **引入锁定文件**（不改变解析结果）：用 `pip-compile`（或 `uv pip compile`）从 `requirements.txt` 生成 `requirements.lock`，CI 和发布包用 lock 安装；`requirements.txt` 仍保留宽约束，给源码用户用。
2. **升级 web 栈**：
   ```
   fastapi>=0.115.0,<0.142.0
   starlette>=0.49.1          # 明确下限，排除 CVE-2025-62727 的影响区间
   uvicorn[standard]>=0.30.0,<0.55.0
   ```
   然后跑完整回归：`/tmp/runtests.sh` 等价的非浏览器测试集，加上作者本地的浏览器 E2E（`tests/browser_*.py`，需要 Chromium，本分支不跑）。
3. **重点人工冒烟**（自动化测试覆盖不到的地方）：
   - SSE 流式响应，以及客户端中途断开时的取消（`app/api/streaming_response.py`、`RequestStreamingResponse`），
   - `StaticFiles` / `FileResponse` 的 Range 请求（`/media/*` 音视频拖动进度条），
   - 重启代理 / handoff（S4/S5，`restart_proxy`）在新版 uvicorn 下的连接接管，
   - Windows 打包版（`pywin32`、uvicorn 的 `--reload` 不使用）。
4. **以后如果要加上传端点**：同时显式加 `python-multipart>=0.0.18`，并在端点上设置请求体大小上限。

## 5. 回退

只有 `requirements.txt` / lock 文件的改动，回退就是还原这两个文件后重装依赖；代码层面不需要任何兼容分支（试验已证明当前代码在新旧版本下都能工作）。
