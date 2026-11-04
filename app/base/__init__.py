"""app/base — 真正的叶子层：不依赖 app.core / app.services / app.api。

背景见 docs/plans/REFACTOR_PLAN-2026-10.md（分层一节）：``app.utils`` 的
``__init__.py`` 会经 ``paste.py`` 拖入 ``app.core.config`` 与
``app.core.driver``，因此它**并不是叶子**，任何 `from app.utils.X import ...`
都会把整棵 app.core 树拉进来。

这带来一个实际问题：``app/runtime/browser_launcher.py`` 需要在 app.core
可用**之前**启动浏览器，所以它一直刻意保持零 app 依赖；而
``app/core/browser/connection.py`` 也需要同样的无头探测逻辑，结果同一份
88 行实现被复制了两份。

本包就是为这类"两边都要用、但谁也不该依赖谁"的原语准备的落脚点。
添加模块时请遵守：

- 只允许标准库与可选第三方依赖（如 psutil），**不得** import app 下其它包；
- 模块保持单一职责，不要把它变成新的杂物抽屉。
"""
