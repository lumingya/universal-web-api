## [2.5.7] - 2026-03-05

### ✨ 命令调度与触发

- 新增命令优先级（`1-4`）与请求基准优先级（`CMD_REQUEST_PRIORITY_BASELINE`，默认 `2`）
- 触发检查按优先级排序；高于基准优先级的命令会优先于新请求抢占执行
- `request_count` 改为“按作用域聚合计数”：`all` 汇总全部标签页、`domain` 汇总同域标签页、`tab` 仅当前标签页
- `page_check` 匹配逻辑增强：优先检测页面正文，标题仅兜底；英文关键词按单词边界匹配，降低误触发

### 🖥️ 控制面板

- 命令编辑器新增“命令优先级（1-4）”配置与校验
- 手动测试命令改为在“所有空闲且匹配作用域”的标签页执行（并返回执行/跳过/失败统计）
- 配置页新增“⭐ 设为默认预设”按钮，支持按站点设置 `default_preset`
- 预设下拉区域新增“当前默认预设”显示，站点切换后自动选中默认预设

### 🔧 稳定性

- 高优先级且包含 `clear_cookies` 的命令新增同域忙碌保护，减少对同域并行会话干扰
- 周期检测调度器常驻运行，支持 `periodic_enabled`、`periodic_interval_sec`、`periodic_jitter_sec`
- 主进程退出时显式停止命令调度器线程，避免后台线程残留
- 修复 `main.py` 启动阶段中文字符串乱码；教程页改为后台非阻塞打开，避免卡在首次启动日志后不继续
- 预设读取顺序升级为“指定预设 → 站点默认预设 → 主预设 → 第一个预设”，避免默认选择不一致
- 修复 `request_count` 命令在 `tab busy/timeout` 时被错误消费的问题：超时未执行会回滚计数基线，后续会继续重试
- 优化命令触发日志文本，将 `??` 统一为 `trigger`，便于排障
- `execute_workflow` 动作新增超时控制（`timeout_sec`，默认取 `CMD_EXECUTE_WORKFLOW_TIMEOUT_SEC=45`），避免命令长时间占用标签页导致“卡死”
- 标签页扫描新增 `devtools://` / `chrome-devtools://` 过滤，修复反复 `+1` 后立即移除的抖动日志
- 请求在 `client_disconnected` 取消时会回滚 `request_count` 且跳过命令触发检查，避免断连请求误触发自动命令
- Windows 启动时切换 `SelectorEventLoopPolicy` 并过滤已知 `WinError 10054` 噪音回调，减少控制台异常栈刷屏
- 修复 DrissionPage 监听线程竞态：`Listener._loading_finished` 在驱动释放后不再调用空对象，避免 `AttributeError: 'NoneType' object has no attribute 'run'`
- 为 DrissionPage 事件循环增加稳定性兜底，单次回调异常不再导致 `_handle_event_loop` 线程退出

## [2.5.6] - 2026-03-04

### 📚 教程与文档

- `static/tutorial.html` 补充命令组章节（分组管理、手动执行、动作链执行命令组）
- 命令动作清单补充 `execute_command_group`、`release_tab_lock` 两项
- 教程内版本号同步为 `v2.5.6`

### 🔖 版本

- 项目版本更新至 `2.5.6`


## [2.4.5] - 2026-03-04

### ✨ 新增

#### ⚡ 命令系统增强
- 新增触发器 `command_result_match`（命令结果匹配条件分支）
- 新增触发器 `network_request_error`（网络请求异常拦截：URL 规则 + 状态码）
- 新增动作 `send_webhook`（外部告警/外部请求）
- 新增动作 `abort_task`（中断当前任务，可停止后续动作）

#### 🌐 网络拦截能力升级
- 网络异常拦截不再依赖 `stream_config.mode=network`
- 在 DOM 模式下也可启用 event-only 网络监听，用于状态码异常快速拦截
- 正则模式新增兜底兼容：误填通配写法（如 `*/queue/join*`）也可匹配

### 🖥️ 控制面板

- 命令编辑器新增“命令执行结果匹配”可视化配置（监听命令/目标步骤/匹配规则/期望值）
- 命令编辑器新增“网络请求异常拦截”配置（关键词或正则、状态码、命中后立即中断）
- 命令编辑器新增 Webhook 详细配置卡（URL、方法、Payload、Header、超时）
- 修复 Webhook 变量提示在 Vue 模板中的编译报错问题

### 📚 教程与文档

- README 补充自动化命令完整教程（触发器、动作、条件分支、Webhook、网络拦截独立模式）
- `static/tutorial.html` 命令章节对齐最新代码能力并新增实战示例
- 修正文档中“网络拦截模式仅非流式”的过时描述

### 🔖 版本

- 项目版本更新至 `2.4.5`


## [2.4.0] - 2026-03-02

### ✨ 新功能

#### ⚡ 自动化命令系统
- **触发器**：支持对话次数、错误次数、空闲超时、页面内容检测（如 Cloudflare 验证）四种触发条件
- **动作**：内置清除 Cookie、刷新页面、新建对话、执行 JS、等待、切换预设、导航、切换代理等动作
- **作用范围**：可限定为所有标签页、指定域名、或指定标签页编号
- **双模式**：
  - 简单模式：可视化配置触发条件和动作列表，零代码操作
  - 高级模式：直接编写 JavaScript 或 Python 脚本，完全自定义逻辑
- **后台执行**：命令在对话完成后异步执行，不阻塞正常对话流程

#### 🔀 代理自动切换（Clash 集成）
- 新增 `switch_proxy` 动作，通过 Clash API 自动切换代理节点
- 支持随机、轮询、指定节点三种切换模式
- 可配置排除关键词（如 DIRECT、REJECT）、Clash Secret、切换后刷新等
- 典型场景：每 N 次对话自动换 IP，检测到 Cloudflare 时自动切换节点

#### 📝 response_format 参数支持
- API 新增 `response_format` 参数透传
- 自动将 `json_object` / `json_schema` 转化为提示词追加到用户消息
- 提示词模板可在 `_global.response_format_hints` 中自定义
- 兼容 OpenAI 格式的 JSON 模式请求

### 🔧 改进

- **GlobalConfigManager**：新增通用 `get()`/`set()` 方法，支持存储任意扩展配置
- **命令触发钩子**：`TabSession.release()` 后自动检查命令触发条件
- **教程更新**：新增「自动化命令」完整章节，含代理切换配置说明

### 🖥️ 控制面板

- 新增「命令」Tab 页，可视化管理所有自动化命令
- 命令列表显示触发条件摘要、动作摘要、触发统计
- 支持手动测试执行命令（在空闲标签页上运行）
- 高级模式提供脚本编辑器，支持 JavaScript / Python 切换

### 📦 配置变更

- `_global` 新增 `commands` 字段存储命令列表
- `_global` 新增 `response_format_hints` 字段存储格式提示词模板

### ⚠️ 注意事项

- 代理切换功能需要 Clash 开启 External Controller（默认 9090 端口）
- 高级模式 Python 脚本在后端执行，拥有完整系统访问权限，请确保脚本来源可信
- `response_format` 转提示词效果不如原生 API，复杂 JSON 结构可能需要调整提示词

---
