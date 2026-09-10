# 更新日志 (v2.9.9)

## 社区贡献致谢

- 特别鸣谢社区贡献者 **[@Karmel5950](https://github.com/Karmel5950)**（PR [#26](https://github.com/lumingya/universal-web-api/pull/26)）：敏锐定位并提交了针对豆包新版网页端 Shadow DOM 隔离的解决思路，直接从网络层 SSE 响应流中捕获图片 URL，有效解决了 `/v1/chat/completions` 中 `media` 恒为空的问题。在此基础上，项目团队进一步完成了多尺寸动态升级防吞、跨分包截断容错、转义兼容与自动化测试套件的完整闭环。

## 主要新增能力与架构升级

### 1. 通用附件管线重构 (Universal Attachment Pipeline)

- **统一多模态内容模型与协议归一化**：
  - 新增 `app/utils/attachments.py`，统一封装 `AttachmentBatch` 与 `AttachmentItem`。
  - 标准化支持多种多模态输入格式：OpenAI 图片（`image_url`）、OpenAI 文件（`file`）、Responses 文件（`input_file`）、Anthropic 格式文档（`document`，支持 base64/url/text）及音视频资源（`input_audio`、`audio_url`、`video_url`）。
  - 严格保留内容排列顺序与附件索引，并在请求生命周期内提供安全的资源 lease 与临时文件隔离，防止并发清理误删。

- **共享上传事务协调器**：
  - 新增 `app/core/workflow/attachment_upload.py`，统一协调调度多传输方式（`file_input`、`cdp_drop`、`js_drop`、`file_clipboard`、`image_clipboard`）。
  - 区分未投递、已投递与不确定状态，在可能存在副作用时避免盲目重试或轮换上传方式。
  - 剥离原 `image_input.py` 和 `text_input.py` 的碎片上传逻辑，图片、用户文件及超长输入生成文件共用同一个上传协调器与限额策略。

- **附件就绪监控与状态机加固**：
  - `AttachmentMonitor` 全面改用 `time.monotonic()` 避免系统时钟漂移影响超时判定。
  - 新增 `freshPreview`（新鲜预览识别）、`uploadErrorCount`（网站拒绝/失败节点实时拦截）以及隐藏节点过滤（`opacity <= 0.05` 或 `display === 'none'`），彻底杜绝透明残留 loading 节点导致的死锁超时。
  - 严格校验实际附件预览节点、无 pending/error 且满足稳定窗口后方可确认就绪，不再仅凭文件输入框赋值成功做虚假确认。

### 2. 控制台面板升级与配置兼容

- **控制台通用附件配置面板**：
  - 站点配置 → 输入处理 中新增独立的“通用附件”配置卡片，支持独立启闭、附件数量/单文件大小/累计容量配额、允许类型/扩展名、上传方式顺序与错误节点选择器。
  - 元素选择器增加可选的 `composer_root`，用于限定文件入口查找与观察范围。
  - 超长输入的“处理策略”与“生成格式”在 UI 分开展示，向后兼容现有预设与旧数据结构，旧站点无需手动迁移。
- **文档与工程规范**：
  - 新增 `docs/attachments.md` 完整说明架构分层、配置规范、API 格式与行为收紧边界。
  - 新增 `requirements-dev.txt`，补齐 Playwright 浏览器级测试依赖。

### 3. DeepSeek 网页端附件适配优化

- **选择器与容器穿透**：
  - 更新 `chat.deepseek.com` 预设选择器，补充 `upload_btn` 与 `file_input`，增强 `send_btn` 识别。
  - 优化 `root_selectors` 穿透至外层公共卡片容器（`div._77cefa5`），解决此前内层输入框范围无法探测附件列表的问题。
  - 缩短兜底等待窗口，确保异常时快速恢复放行。

## 核心修复与优化

### 1. 豆包（Doubao）Shadow DOM 适配与 SSE 流图片提取

- **解决 Shadow DOM 渲染导致的图片提取失效**：豆包新版页面将消息区渲染进 Shadow DOM，导致预设的 DOM 图片选择器（`img`）无法探测到文生图结果、导致 `/v1/chat/completions` 的 `media` 恒为空。
- **SSE 网络流原生提取**：直接从网络层 SSE 响应流的事件字段中提取生成图片 URL（覆盖 `image_ori`、`image_ori_raw`、`image_preview`、`image_preview_resize`、`image_thumb` 等字段），复用现有媒体去重、下载与回传管线，无需额外外部依赖。

### 2. 流式图片提取健壮性加固

- **跨 Chunk 多尺寸优先级升级防吞**：重构去重与优先级机制，以 TOS 路径作为全局基准，记录当前最优尺寸优先级；当流式传输先到达低清缩略图（`image_thumb`）、后到达高清原图（`image_ori`）时，自动触发版本升级并下发原图，杜绝低清图抢先导致高清原图被吞噬。
- **TCP 分包与 CDP 轮询跨包截断容错**：引入 SSE 块边界与未闭合片段暂存缓冲，杜绝网络分包或轮询快照恰好截断在 URL 字符中间时导致的匹配脱靶与图片丢失。
- **JSON 特殊转义兼容**：正则扩展支持转义斜杠（`\/`）并在提取后执行反转义处理，防止因 JSON 序列化转义导致正则失效或下游 400 报错。
- **解析器状态重置**：`reset()` 时同步清空流式图片已见优先级映射与待拼接分块缓冲区，确保会话切换与重新生成时行为幂等。

## 工程化与测试

- **通用附件全量测试套件（50 项通过）**：
  - `tests/test_attachments.py`（42 项）：覆盖输入归一化、大小/数量限额、图片内容校验、文件 lease 生命周期与请求级清理。
  - `tests/test_attachment_ui.py`（2 项）：覆盖 Vue 控制台面板配置保存、读写与动态交互。
  - `tests/test_attachment_browser.py`（6 项）：基于真实 Chromium 浏览器与本地模拟 Composer，覆盖页面即时清空、上传回执丢失、错误预览、长期 pending 与 input 赋值无真实预览等边界场景。
- **豆包解析器流式图片提取专项单元测试**：
  - 在 `tests/test_doubao_parser_images.py` 中补充针对基础图片提取、跨 chunk 尺寸优先级动态升级、跨包截断拼接恢复、转义斜杠反转义、状态重置清理的全量测试覆盖。
