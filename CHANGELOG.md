\# 更新日志



\## v2.3 — 隐身模式反检测增强



\### 🔴 关键修复



\*\*Cloudflare 高强度盾检测绕过\*\*



\- \*\*修复 CDP 点击 pressure=0 检测\*\*：CDP `Input.dispatchMouseEvent` 的 `mousePressed` 事件默认 `force=0`，导致页面 `PointerEvent.pressure=0`（真实鼠标按下时 pressure≈0.5）。CF 高强度盾通过此差异识别自动化点击。现在所有隐身模式点击均使用 `force=0.5`。



\- \*\*修复网络监听暴露额外 CDP Session\*\*：DrissionPage 的 `tab.listen` 功能在启动时会创建独立的 WebSocket 连接并调用 `Target.attachToTarget` 建立新的 CDP Session，CF 可检测到异常的 Session 数量和 `Network.enable` 活动。新增 DrissionPage 自动补丁（`patch\_drissionpage.py`），让 Listener 复用 tab 主连接，消除额外连接和 Session。



\- \*\*消除 ele.click() 降级路径\*\*：隐身模式下所有点击路径统一使用 `cdp\_precise\_click()`，彻底移除了 `ele.click()` 和 `tab.actions.click()` 的降级回退（这两者均会触发 CF 检测）。



\### 🟡 隐身模式增强



\*\*人类化鼠标行为改进\*\*



\- \*\*按压期间微移\*\*：CDP 点击在 `mousePressed` → `mouseReleased` 之间插入 1-2 个 `mouseMoved(buttons=1)` 微移事件，模拟真实手指按压时鼠标的微小位移，消除"事件沙漠"。



\- \*\*释放坐标偏移\*\*：`mouseReleased` 的坐标允许与 `mousePressed` 有 ±1px 偏差，匹配真实人类行为。



\- \*\*移除危险的降级路径\*\*：`\_dispatch\_mouse\_move()` 不再降级到 `tab.actions.move\_to()`（测试证明 `tab.actions` 系列操作会触发 CF）。



\- \*\*空闲微漂移不规则化\*\*：`idle\_drift()` 的移动频率改为 30% 概率连续快速微动 + 70% 概率长静止，更接近真实人类的无意识手部抖动模式。



\*\*滚轮滚动改用纯 CDP\*\*



\- `human\_scroll()` 改用 `Input.dispatchMouseEvent(type='mouseWheel')` 替代 `tab.actions.scroll()`，避免 `actions` 系列的参数差异。



\### ⚪ 启动流程



\- 启动脚本（`start.bat`）在依赖安装后自动执行 DrissionPage 补丁

\- 补丁支持幂等执行（重复运行不会重复打补丁）

\- 补丁自动备份原文件，支持 `--restore` 一键恢复



\### 📋 诊断工具



\- 新增 `cf\_diag.py`：CF 检测隔离诊断脚本，逐项测试 CDP 鼠标移动、元素查找、各种点击方式是否触发 CF

\- 新增 `patch\_drissionpage.py`：DrissionPage Listener 连接复用补丁，支持自动应用和恢复



---



\### 排查过程记录



通过系统性的隔离测试定位了两个独立的检测向量：



| 检测向量 | 发现方式 | 修复方案 |

|---------|---------|---------|

| `PointerEvent.pressure=0` | 对比有/无 `force` 参数的 CDP 点击 | `mousePressed` 传 `force=0.5` |

| 额外 CDP Session | 对比 network/dom 监听模式 | 补丁 DrissionPage Listener 复用主连接 |



关键测试结论：

\- `tab.ele()`、`ele.rect`、`ele.states.is\_displayed` 均使用纯 CDP 命令，\*\*不注入 JS\*\*（`Runtime.evaluate` 调用次数 = 0）

\- DrissionPage 的 `tab.listen` 创建独立 WebSocket 连接 + `Target.attachToTarget`，这是 CF 检测的主要来源

\- 纯 CDP 操作（鼠标移动、元素查找、rect 读取）即使重复多轮也不触发 CF

