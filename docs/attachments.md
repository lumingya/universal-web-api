# 通用附件管线

## 使用位置

控制台 → **站点配置 → 输入处理 → 通用附件 / 超长输入**。

- 通用附件开关独立于超长文本处理开关。
- 配置附件数量、单文件和累计大小、允许类型、上传方式顺序、确认预算和错误节点选择器。
- 元素选择器增加可选的 `composer_root`，限制自动文件输入框查找及附件观察范围；指定 `file_input` 时优先使用指定入口。
- 超长输入的“处理策略”和“生成格式”在 UI 分开显示；仍写回旧 `temp_file_type`，兼容现有预设和编辑器。
- 类型留空只表示不加额外类型限制，**不代表网站/模型支持所有文件**。没有就绪预览的网站需要配置附件预览选择器。iframe/shadow-root 等特殊页面仍可能需要站点适配，未增加自动穿透能力。

## 配置与兼容

```json
{
  "file_paste": {
    "enabled": false,
    "threshold": 50000,
    "temp_file_type": "txt",
    "attachments": {
      "enabled": true,
      "max_count": 8,
      "max_file_mb": 20,
      "max_total_mb": 50,
      "allowed_types": ["image/*", ".pdf", ".docx", ".txt", "audio/*", "video/*"],
      "transport_order": ["file_input", "cdp_drop", "js_drop", "file_clipboard"],
      "ready_timeout": 30,
      "error_selectors": [".upload-card[data-state='error']"]
    }
  }
}
```

未配置 `attachments` 的旧预设使用运行时默认值，读取旧配置不会批量写入新字段。`allowed_types` 默认为空列表。安全上限：32 个附件、单文件 100 MiB、总量 200 MiB、确认预算 180 秒；有效值会规范化到此范围内。UI 中 MB 按 1024² 字节计算。生成的长文本文件与用户附件共享数量/字节配额。

**行为变化（有意收紧）：**

1. 工作流中的附件准备默认全部成功或明确报错，不再忽略失败图片或截断超限附件。
2. Windows 图片默认上传原文件，而非像素剪贴板。依赖旧图片粘贴行为的站点，可以显式启用并前移 `image_clipboard`；该方式可能丢失动画、透明度与元数据。
3. 投递后未确认，不再换一种方式自动重传；网站拒绝、观察器不可用均停止发送。普通网页没有幂等键，不能承诺 exactly-once。
4. 超长文本转文件失败不再悄悄降级成普通文本。PDF 只生成文本层版本；ReportLab/字体失败时不再回退到图片型 PDF。
5. 旧 `upload_signal_timeout` / `upload_signal_grace` 字段保留读取，但新的上传等待使用 `attachments.ready_timeout`，UI 不再展示两个旧入口。发送后的确认配置保留原逻辑。
6. 现有 `UPLOAD_HISTORY_IMAGES` 开关也用于选择附件来源：开启时上传所选历史中的附件；关闭时沿用“最近一条带附件的用户消息”的语义。不是仅最后一条消息。
7. 旧 `extract_images_from_messages()` 工具仍保留兼容行为，但实际工作流已经改用严格的 `AttachmentBatch`。

## 支持的输入形式

OpenAI Chat 图片保持不变：

```json
{"type":"image_url","image_url":{"url":"https://example.com/image.png"}}
```

OpenAI 文件（也接受 `file_url` HTTP(S) 来源作为本项目扩展）：

```json
{"type":"file","file":{"filename":"notes.txt","file_data":"data:text/plain;base64,aGVsbG8="}}
```

Responses 文件：

```json
{"type":"input_file","filename":"notes.txt","file_data":"data:text/plain;base64,aGVsbG8="}
```

Anthropic 文档：

```json
{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":"完整PDF的base64"}}
```

`document.source` 也支持 `url` 和 `text`；text 会生成 UTF-8 文件。`input_image`、Anthropic `image` 块会规范化为图片引用。

音频数据：

```json
{"type":"input_audio","input_audio":{"format":"wav","data":"完整音频的base64"}}
```

音视频 URL（本项目接受的扩展内容块）：

```json
{"type":"audio_url","audio_url":{"url":"https://example.com/audio.mp3"}}
{"type":"video_url","video_url":{"url":"https://example.com/video.mp4"}}
```

音频/文档等传入网站的是原文件，不自动转录、抽文本或转码。支持 URL/字节规范化不等于目标网站支持该文件。

**暂不支持：** `file_id`（尚无有权限隔离的文件登记服务）、任意本地路径、`file://`、非 base64 的 data URI。声明了附件但缺少数据会在协议边界返回 400；下载、内容校验或站点能力错误在工作流错误响应中返回对应 code。不是所有错误都能在 SSE 开始前预检。

## 实现分层

- `app/utils/attachments.py`：协议归一化、有序索引、资源准备、逐文件/总配额、请求资源 lease。
- `app/core/workflow/attachment_upload.py`：共享上传事务、限额预检和各 transport。
- `image_input.py`：保留旧入口名称的薄包装；图片与普通附件共享执行器。
- `text_input.py`：只负责长文本文件生产、输入焦点和提示语，上传交给同一个协调器。
- `attachment_monitor.py`：继续复用 DOM baseline、稳定窗口和取消；新增新鲜预览确认、可见错误节点、观察器丢失保护，等待改用 monotonic clock。

状态流程：

```text
准备 → not_dispatched（可尝试下一方式）
     → dispatched / unknown（只观察，不重传）
     → 新鲜附件预览 + 无 pending/error + 稳定窗口 → 就绪
     → 未确认 / 拒绝 / 取消 → 中止发送
```

不能仅凭 `input.files.length`、拖拽事件返回或旧同名附件判定就绪。失败后已有副作用的标签页标记错误，交给既有恢复机制。

## 安全和生命周期

- 复用受限公共 URL 下载器，不为用户附件附加浏览器凭证；继续逐跳校验远程重定向。
- HTTP 流式落盘并按实际字节限额，base64 解码前限额，图片仍做格式/像素/帧数校验。
- 普通文件不执行、不自动解压。MIME 探测仅做有限的常见格式识别，并保留后缀匹配语义；允许类型是能力配置，不是恶意文件扫描器。
- 存储采用请求目录 + 唯一附件目录，同名/重复内容不会覆盖或删除语义引用。
- 浏览器主机必须能访问上传文件的本地路径；远程 Chromium 文件传输未实现。
- 请求作用域负责准备失败/生成器退出时释放 lease；可能已投递的文件保留 15 分钟供浏览器异步读取。下次准备或旧清理任务触发时清扫过期目录；进程崩溃的孤立目录 24 小时后回收，不是实时后台定时删除。
- 图片/文档输入的错误不暴露 URL 查询参数、base64 或绝对路径。
- 输出媒体本地化和协议输出暂未重构，避免扩大回归范围。

## 测试

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium
python -m pytest -q tests/test_attachments.py tests/test_attachment_ui.py tests/test_attachment_browser.py
```

浏览器测试仅使用本地模拟 composer，不访问 AI 站点。缺少 Playwright 或浏览器时该文件会跳过；CI 若要求浏览器验证，应先安装上述依赖。

本次还在真实配置控制台确认了面板渲染，未发现 Vue 页面错误。没有验证登录态第三方站点的端到端上传，应先在测试预设试用，尤其是依赖 Windows 图片剪贴板或无标准附件预览的站点。
