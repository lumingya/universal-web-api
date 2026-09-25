# 信任边界说明（S2 / S3）

> 本文档是 2026-09 代码审查（`docs/review/CODE_REVIEW_REPORT.md`）中
> **S2「自定义代码非安全沙箱」** 与 **S3「解析器安装立即 import」** 的配套说明。
> 它记录的是本项目**当前实际具备**的隔离强度，以及在什么前提下这些能力是安全的。

## 一句话结论

**本项目假定「能配置命令 / 能安装解析器的人 = 能在服务器上执行任意代码的人」。**

命令引擎的 Python/JS 执行**不是安全沙箱**，它是一个**误操作防护**（guardrail）。
如果你的威胁模型里包含「不可信的人来写这些脚本」，那么当前架构无法满足，
必须换成独立低权限进程 / 容器方案。

## 具体是什么情况

### 1. 命令引擎的 Python 脚本

`app/services/command_engine_actions.py`

默认走受限模式：AST 白名单校验 + 受限 `__builtins__` + import 白名单
（`CMD_PYTHON_SANDBOX_ALLOWED_IMPORTS` 可扩展）。

**为什么这不是沙箱：**

- 脚本运行在**主服务进程内**，与浏览器控制、配置读写共享同一套权限。
- 执行上下文里注入了高权限对象（标签页会话、规则记录回调等）。
  即便直接调用被 AST 规则挡住，通过这些对象的属性链依然可能绕行。
- CPython 层面从来没有承诺过「受限 `__builtins__` 等于安全边界」。
  历史上这类方案被绕过的方式层出不穷。

`CMD_ALLOW_UNSAFE_PYTHON_COMMANDS=true` 会**完全关闭**上述限制
（直接注入完整 `__builtins__`）。默认为 `false`。

### 2. 命令引擎的 JS 脚本

JS 通过 CDP 在受控浏览器里执行，天然拥有该页面的全部能力：
读取已登录会话、发起同源请求、读写页面数据。
这**就是这个功能的用途**，不存在也不打算存在隔离。

### 3. 运行时解析器安装

`app/services/parser_manager.py`

安装 = 把源码写进 `app/core/parsers/` 并立刻 `import`，
从此以服务进程的完整权限运行。

当前的加固（见 S3）：

- 默认关闭，需显式 `PARSER_INSTALL_ENABLED=true`；
- 模块**顶层**不允许出现可执行语句（裸调用 / `for` / `while` / `with`），
  把「装上就跑」收窄为「被调用才跑」，给人工审查留出窗口；
- 记录源码 SHA-256 便于事后核对。

**这些都不是隔离。** 类方法体内可以写任何代码。

## 部署要求

| 场景 | 要求 |
| --- | --- |
| 单机自用（默认） | `APP_HOST=127.0.0.1`。保持默认即可。 |
| 对外绑定 | 必须配置 `DASHBOARD_AUTH_ENABLED=true` + 强 `DASHBOARD_AUTH_TOKEN`（启动期强校验，见 S1）。 |
| 对外绑定 + 代码执行开关 | **不允许**。`CMD_ALLOW_UNSAFE_PYTHON_COMMANDS=true` 或 `PARSER_INSTALL_ENABLED=true` 与非回环 `APP_HOST` 同时出现时，服务**拒绝启动**。 |
| 多租户 / 不可信投稿脚本 | 当前架构**不支持**。需在低权限独立进程或容器内执行，剥离浏览器与配置对象，并限制出网与资源。 |

启动期检查实现在 `app/core/http_security.py::startup_security_errors()`，
由 `main.py` 的 lifespan 调用。
隔离网络中如需临时跳过，可设 `UWA_ALLOW_INSECURE_STARTUP=true`（不推荐）。

## 相关配置项

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CMD_ALLOW_UNSAFE_PYTHON_COMMANDS` | `false` | 关闭 Python 脚本的全部限制 |
| `CMD_PYTHON_SANDBOX_ALLOWED_IMPORTS` | 空 | 在受限模式下额外放行的 import（逗号分隔） |
| `PARSER_INSTALL_ENABLED` | `false` | 允许运行时安装解析器 |
| `UWA_ALLOW_INSECURE_STARTUP` | `false` | 把启动期安全校验降级为警告 |
