# 死代码系统扫描（2026-10-20）

步骤 14 手工发现 `model_selection.py`（350 行）是死代码之后，对全仓做了一次
系统扫描，避免同类问题只靠"撞见"。本文档记录方法、结果与**一个待决问题**。

## 方法

单靠文本搜索会大量误报（相对导入 `from .engine_common import X` 不含模块全
路径），因此用两步：

1. **AST 导入图 + 入度分析**：解析 `app/` 下 247 个模块的真实 import（含
   相对导入与 `from . import X as _X` 别名形式），统计每个模块的入度；
   入度为 0 的模块即"没有任何模块 import 它"。
2. **运行时实证**：导入 `app.runtime.bootstrap` + `app.__main__` 后检查
   `sys.modules`，确认这些模块是否真的没被加载。

第一步的已知误报来源（本次逐个排除）：
- `from . import X as _X` 形式的别名导入；
- **字符串形式的动态导入**（命令引擎的沙箱白名单用字符串登记模块名，
  如 `app.services.arena_cf_solver`、`app.services.arena_proxy_rotation`）；
- 条件导入（如 `main.py` 仅在 `UWAPI_WORKER_ROLE=worker` 时导入
  `app.worker.rpc_routes`）。

## 结果

### 已删除（步骤 14）

| 模块 | 行数 | 证据 |
| --- | --- | --- |
| `app/core/workflow/model_selection.py` | 350 | 零引用；注册表派发器零调用方；无动态导入；运行时从未加载 |

### 已排除（确认在用）

| 模块 | 行数 | 为何不是死代码 |
| --- | --- | --- |
| `app.core.page_capture.kimi_fetch_capture` | 379 | `page_capture/__init__.py` 用 `from . import X as _X` 惰性加载 |
| `app.core.page_capture.deepseek_request_transport` | 424 | 同上 |
| `app.services.arena_cf_solver` | 109 | 字符串登记在 `command_engine_actions.py` 的沙箱白名单 |
| `app.services.arena_proxy_rotation` | 154 | 同上 |
| `app.worker.rpc_routes` | 135 | `main.py:985` 在 worker 模式下导入 |
| `app.api.admin.pool` / `presets` | 464 / 148 | 路由注册时被发现并加载（运行时实证已加载） |

## ⚠️ 待决：`app/services/arena_gpt_image_command.py`（894 行）

**证据（与 `model_selection.py` 同强度）**

1. 全仓 `.py` 文件中**零引用**（排除自身）；
2. 运行时导入 `bootstrap` + `__main__` 后**从未加载**；
3. 无 `if __name__ == "__main__"`、无 `main()`、无 `argparse`——**没有入口**；
4. git 历史上随基线提交 `a99281d` 引入，此后只被步骤 1b 的 import 排序碰过。

**为什么没有直接删除**

它不是一个抽象层，而是一个**命令处理脚本**：函数签名是
`run(ctx: dict, *, single_mode: bool = False)`，`ctx` 里取 `tab` / `logger` /
`session` / `command_ui` / `end_command_loop`，且硬编码引用了具体命令
（`cmd_15d91034`、命令名 `ARENA自动盲测不翻牌 -gpt image 2 - 单`）。

这很像是一次**盲测实验脚本**被顺手提交进了服务层。矛盾之处在于：

- 说它是死代码：确实无人引用、无法运行；
- 说它是资产：它可能是用户保存/导出的自定义命令脚本，删掉就找不回来了
  （虽然 git 历史里还有）。

与 `arena_cf_solver` / `arena_proxy_rotation` 对比更能说明问题：那两个是
"给沙箱脚本用的库"，被字符串登记在白名单里；而这个是"沙箱脚本本身"，
却**不在任何加载路径上**。

**它对指标的"贡献"不小**（都在死代码上）：

- broad_except 基线：**30 处**（单文件最高之一）
- CC ≥ 30 的函数：**2 个**（`run` CC 50、`_wait_for_gpt_image2` CC 32）

**建议**（需要你拍板，任选其一）

| 方案 | 做法 | 适用判断 |
| --- | --- | --- |
| A 删除 | `git rm`，文档同步更新 | 确认是一次性实验，不再需要 |
| B 移到 `tools/` | 保留代码、脱离服务层，从所有 `app/` 扫描口径中移除 | 想留着当手动工具 |
| C 接上命令引擎 | 把它登记进沙箱加载路径 | 确认它**应该**能用而目前是坏的 |

选 B 的话，指标会立刻好看（broad_except −30、CC≥30 函数 −2），但那只是
统计口径变化，不代表代码变健康——真正的收益是让 `app/` 的扫描结果反映
真实的服务层。


## 决策落地（步骤 19，commit `d041f75`）

`app/services/arena_gpt_image_command.py` 的三个选项里，选了 **B：移到 `tools/`**，
已执行完毕，新路径 `tools/arena_gpt_image_blind_test.py`。

理由摘要（详见 `git show d041f75`）：命令沙箱是 `exec(script, globals_dict)`，脚本来源是
运行时 `commands.json` 里的代码字符串，并不加载 `app/` 下的 `.py`；该文件硬编码的命令 ID
`cmd_15d91034` 在全仓只出现在它自己体内；无 `__main__`、无 `main()`、无 argparse，没有任何入口。

选"移动"而非"删除"，是因为它与步骤 14 删掉的 `model_selection.py` 性质不同：后者是
**未接线的抽象层**（投机性设计），这个是**命令脚本形态的用户产物**（`run(ctx)` 契约、
Arena 盲测参考实现）。删产物不该由重构代劳。

直接效果：`app/` 下 −894 行；宽泛捕获基线 1404 → **1374**；CC ≥ 30 的函数少 2 个。
一行 `git revert` 即可退回。
