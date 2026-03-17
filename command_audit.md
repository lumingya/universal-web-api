# 命令系统审计记录

> 这个文件由多个 AI 共同维护。  
> 每次更新时，请删除自己旧的分析，只保留最新一段，避免重复内容浪费 token。  
> 当前内容基于 2026-03-17 的实际代码状态。

## Codex 最新处理记录（2026-03-17）

### 本轮确认结论

- Gemini 的 `新命令1` 是兜底恢复命令，不是主工作流的正常步骤。
- 真正需要避免的是：主工作流执行中的“正常过渡态”被 `page_check` 兜底命令误当成异常态消费。
- 单靠“busy 时一刀切不检查”不够灵活，所以这次改成了两层可配置：
  - 页面命中必须稳定一段时间才触发
  - 工作流忙碌时是否继续参与页面检查，按命令单独配置

### 本轮已修改

- `page_check` 新增 `stable_for_sec`
  - 只有页面命中持续达到该秒数，才允许触发。
  - `edge` 模式下会在“稳定命中”这一刻触发，而不是文本刚闪现就触发。
- `page_check` 新增 `check_while_busy_workflow`
  - `true`：工作流忙碌时仍允许参与周期页面检查
  - `false`：工作流忙碌时跳过周期页面检查，避免消费主工作流中的中间态
- `cmd_0020e346`（`新命令1`）已配置为：
  - `stable_for_sec = 1.5`
  - `check_while_busy_workflow = false`
  - 这让它更像真正的“异常态兜底”，而不是在工作流切换过程中抢按钮
- 命令编辑器前端已补上这两个配置项
  - `页面稳定命中（秒）`
  - `工作流忙碌时仍参与页面检查`
- 动作名称文案里，`执行预设` 已改成 `切换预设`
  - 仅修改显示名称，不改动作类型和已有配置结构

### 本轮涉及文件

- `app/services/command_engine.py`
- `app/services/command_defs.py`
- `app/api/cmd_routes.py`
- `static/js/components/commands/CommandsTabMethods.js`
- `static/js/components/commands/CommandsTabTemplate.js`
- `config/commands.json`
- `tests/test_command_engine_core.py`
- `command_audit.md`

### 前端修改

- 有前端修改。
- 本轮前端改动包括：
  - `page_check` 的两个新编辑项
  - `执行预设` -> `切换预设` 的显示文案

### 本轮验证

- `python -m unittest tests.test_command_engine_core tests.test_command_engine_runtime tests.test_cmd_routes`
- `python -m py_compile app/services/command_engine.py app/services/command_defs.py app/api/cmd_routes.py tests/test_command_engine_core.py tests/test_command_engine_runtime.py`
- `node --check static/js/components/commands/CommandsTabMethods.js`
- `node --check static/js/components/commands/CommandsTabTemplate.js`

### 备注

- 这次没有修改 Gemini 主工作流本身，只收敛了兜底命令的触发时机。
- 当前策略更符合你的原始设计：主工作流走正常路径，兜底命令只在异常态稳定存在时再补一脚。
- 额外补充：`page_check` 的“检查文本”支持 `||`（或）和 `&&`（且）。这次已把 Gemini 刷新恢复命令的检查文本从 `出了点问题` 扩成 `出了点问题 || 重试`。
