# Universal Web-to-API

将任意 AI 网站转换为 OpenAI 兼容 API

---

## 简介

Universal Web-to-API 是一个工具，能够将任意 AI 聊天网站（如 ChatGPT、Grok、DeepSeek、Claude 等）转换为标准的 OpenAI 兼容 API。

**工作原理**：通过控制本地 Chrome 浏览器，自动化执行"输入消息 → 发送 → 读取回复"的流程，并将结果以 OpenAI API 格式返回。

## 特性

- **通用兼容** - 支持任意 AI 聊天网站，无需等待官方 API
- **智能识别** - AI 自动分析页面结构，无需手动配置选择器
- **流式输出** - 完整支持 SSE 流式响应，实时返回 AI 回复
- **OpenAI 兼容** - 标准 `/v1/chat/completions` 接口，可直接对接现有应用
- **可视化管理** - 内置 Dashboard，图形化编辑配置
- **隐身模式** - 模拟人类操作，降低被检测风险
- **热更新** - 配置修改无需重启服务
- **优雅取消** - 支持请求中断，快速响应客户端断开

## 快速开始

### 环境要求

- Python 3.8+
- Chrome 浏览器（推荐最新版）
- Windows / macOS / Linux

### Windows 一键启动

双击运行 `start.bat`，脚本会自动完成以下操作：

1. 创建 Python 虚拟环境
2. 安装所有依赖
3. 启动独立 Chrome 实例（不影响日常使用的 Chrome）
4. 启动 API 服务

启动完成后：

- API 地址: http://127.0.0.1:8199
- Dashboard: http://127.0.0.1:8199/dashboard
- API 文档: http://127.0.0.1:8199/docs


### 首次使用

1. 在启动的 Chrome 浏览器中打开目标 AI 网站（如 https://chatgpt.com ）
2. **完成登录**（重要！）
3. 发送 API 请求，服务会自动识别页面结构

```bash
curl http://127.0.0.1:8199/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

## 配置说明

### 环境变量 (.env)

```env
# 服务配置
APP_HOST=127.0.0.1          # 监听地址
APP_PORT=8199               # 监听端口
APP_DEBUG=true              # 调试模式
LOG_LEVEL=INFO              # 日志级别

# 认证配置
AUTH_ENABLED=false          # 是否启用认证
AUTH_TOKEN=your-secret      # Bearer Token

# 浏览器配置
BROWSER_PORT=9222           # Chrome 调试端口

# AI 分析配置（用于自动识别页面结构）
HELPER_API_KEY=xxx          # 辅助 AI 的 API Key
HELPER_BASE_URL=http://...  # 辅助 AI 的 API 地址
HELPER_MODEL=gpt-4          # 辅助 AI 模型名称
```

### 站点配置 (sites.json)

自动生成或手动编辑：

```json
{
  "chatgpt.com": {
    "selectors": {
      "input_box": "#prompt-textarea",
      "send_btn": "button[data-testid='send-button']",
      "result_container": ".agent-turn .markdown",
      "new_chat_btn": "[data-testid='create-new-chat-button']"
    },
    "workflow": [
      {"action": "CLICK", "target": "new_chat_btn", "optional": true},
      {"action": "WAIT", "target": "", "value": "0.5"},
      {"action": "FILL_INPUT", "target": "input_box"},
      {"action": "CLICK", "target": "send_btn", "optional": true},
      {"action": "STREAM_WAIT", "target": "result_container"}
    ],
    "stealth": true
  }
}
```

**选择器说明**

| 字段 | 必需 | 说明 |
|------|------|------|
| input_box | 是 | 消息输入框 |
| send_btn | 是 | 发送按钮 |
| result_container | 是 | AI 回复内容容器 |
| new_chat_btn | 否 | 新建对话按钮 |
| message_wrapper | 否 | 完整消息容器（改进抓取） |
| generating_indicator | 否 | 生成中指示器 |

**工作流动作**

| 动作 | 说明 | 参数 |
|------|------|------|
| FILL_INPUT | 填入消息 | target: 选择器名 |
| CLICK | 点击元素 | target: 选择器名 |
| STREAM_WAIT | 流式等待回复 | target: 选择器名 |
| WAIT | 等待固定时间 | value: 秒数 |
| KEY_PRESS | 模拟按键 | target: 键名 (如 Enter) |

## API 文档

### 聊天补全

```
POST /v1/chat/completions
Content-Type: application/json
Authorization: Bearer <token>  # 如果启用了认证
```

**请求体**

```json
{
  "model": "gpt-4",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "stream": true
}
```

**流式响应**

```
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":"Hello"}}]}
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":"!"}}]}
data: {"id":"chatcmpl-xxx","choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

**非流式响应**

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "choices": [{
    "message": {"role": "assistant", "content": "Hello! How can I help you?"},
    "finish_reason": "stop"
  }]
}
```

### 其他接口

| 接口 | 方法 | 说明 |
|------|------|------|
| /v1/models | GET | 获取模型列表 |
| /health | GET | 健康检查 |
| /api/config | GET/POST | 获取/保存站点配置 |
| /api/config/{domain} | DELETE | 删除站点配置 |
| /api/logs | GET | 获取实时日志 |
| /dashboard | GET | 管理界面 |

## Dashboard

访问 http://127.0.0.1:8199/dashboard 打开管理界面。

**功能**

- 配置管理：可视化编辑选择器、拖拽排序工作流、选择器测试（高亮显示）、导入/导出配置
- 实时日志：日志过滤、暂停/清除
- 系统设置：环境变量编辑、浏览器常量调整

## 已测试站点

| 站点 | 状态 | 备注 |
|------|------|------|
| ChatGPT (chatgpt.com) | ✅ | 需登录，建议启用隐身模式 |
| Grok (grok.com) | ✅ | 需 X/Twitter 账号 |
| DeepSeek (chat.deepseek.com) | ✅ | - |
| Google AI Studio | ✅ | 需 Google 账号 |
| LMArena (lmarena.ai) | ✅ | 建议启用隐身模式 |

## 架构说明

```
main.py (FastAPI)
├── HTTP 路由、请求生命周期管理、客户端断开检测
│
├── request_manager.py
│   └── FIFO 队列、全局锁、取消信号
│
├── browser_core.py
│   └── 浏览器连接、工作流执行、流式监控
│
└── config_engine.py
    └── sites.json 管理、AI 自动识别、热更新
```

## 常见问题

**Q: 提示"浏览器未连接"？**

确保 Chrome 以调试模式启动，检查端口是否为 9222（或 .env 中配置的端口）。

**Q: 首次请求很慢？**

首次访问新站点时，系统会调用 AI 分析页面结构，可能需要 10-30 秒。后续请求会使用缓存。

**Q: 回复不完整或乱码？**

检查 result_container 选择器是否正确，可在 Dashboard 中测试选择器。

**Q: 如何支持新站点？**

在 Chrome 中打开目标站点并登录，发送测试请求即可自动识别。如果识别失败，在 Dashboard 中手动配置。

**Q: 如何避免被检测？**

启用 stealth: true、避免高频请求、使用独立的 Chrome Profile、保持浏览器窗口可见。

**Q: 多个请求会冲突吗？**

不会。系统使用 FIFO 队列，同时只执行一个请求，其他请求会排队等待。

## 项目结构

```
├── main.py              # FastAPI 入口
├── browser_core.py      # 浏览器自动化
├── config_engine.py     # 配置管理
├── request_manager.py   # 并发控制
├── data_models.py       # 类型定义
├── dashboard.html       # 前端界面
├── dashboard.js         # 前端逻辑
├── start.bat            # Windows 启动脚本
├── requirements.txt     # Python 依赖
├── .env                 # 环境配置
└── sites.json           # 站点配置
```

## 依赖

- fastapi - Web 框架
- uvicorn - ASGI 服务器
- DrissionPage - 浏览器自动化
- beautifulsoup4 - HTML 解析
- pydantic - 数据验证

## 许可证

AGPL-3.0 License

## 致谢

- [DrissionPage](https://github.com/g1879/DrissionPage) - 浏览器自动化库
- [FastAPI](https://fastapi.tiangolo.com/) - Python Web 框架
- [Tailwind CSS](https://tailwindcss.com/) - CSS 框架
- [Vue.js](https://vuejs.org/) - JavaScript 框架
```
