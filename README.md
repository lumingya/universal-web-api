<p align="center">
  <img src="./static/images/logo.svg" alt="Universal Web API logo" width="160">
</p>

# Universal Web API

📖 文档 • [English](./README.en.md) • [简体中文](./README.zh-CN.md)

当前版本：**2.9.8**（以 [`VERSION`](./VERSION) 和 [`CHANGELOG_CURRENT.md`](./CHANGELOG_CURRENT.md) 为准）

Universal Web API 是一个运行在本机的 API 桥接与调试工具。它接管浏览器中已经登录的 AI 网页（ChatGPT、DeepSeek、Gemini、Claude 等），将网页对话转换为 OpenAI / Anthropic 兼容接口，供本地客户端、酒馆、脚本和工作流调用。

它不是官方 API 的替代品，也不会创建或出售账号、额度。所有请求都会经过受控浏览器页面，网页端的登录态、限流、验证码和模型可用性仍由目标站点决定。

> ⚠️ **合规与安全**：仅使用自己有权使用的账号，并遵守目标站点服务条款。项目不提供绕过登录、验证码、付费墙或安全机制的功能。默认只建议绑定到 `127.0.0.1`，不要在没有认证和访问控制的情况下暴露到公网。

## 功能概览

- OpenAI 兼容：`/v1/chat/completions`、`/v1/models`；实验性支持 `/v1/responses`。
- Anthropic 兼容：`/v1/messages`、`/v1/messages/count_tokens`，同时接受 `Authorization` 和 `x-api-key`。
- 多种路由：自动分配、站点域名、固定标签页、精确 URL、URL 绑定预设和路由组。
- 流式 SSE、工具调用转译与参数纠错、多模态输入/媒体回传、超长文本文件粘贴。
- 标签页池生命周期管理、请求历史、日志、选择器测试和浏览器状态诊断。
- 通过 `config/sites.json`、预设和控制面板热更新站点工作流。

### 已适配站点

| 站点 | 网址 | 备注 |
| --- | --- | --- |
| ChatGPT | `chatgpt.com` | 支持长上下文与流式回复 |
| DeepSeek | `chat.deepseek.com` | 支持深度思考流提取 |
| Gemini | `gemini.google.com` | 多模态交互测试 |
| Claude | `claude.ai` | 页面交互与附件上传 |
| Kimi | `www.kimi.com` | 长上下文文件粘贴 |
| 通义千问 | `chat.qwen.ai` | 国产站点网页自动化 |
| Grok | `grok.com` | 原生网页流解析 |
| 豆包 | `www.doubao.com` | 最新页面结构适配 |
| AI Studio | `aistudio.google.com` | 开发者吞吐测试 |
| Arena AI | `arena.ai` | 盲测对比，受出口 IP 质量影响 |

未收录站点可在控制面板使用“新增站点”流程，根据 DOM 生成并逐项验证规则；这不是绕过验证码或访问控制。

### 架构简图

```mermaid
graph TD
  Client[本地客户端] --> API[FastAPI / app/api]
  API --> Pool[标签页池 / app/core/tab_pool]
  Pool --> Browser[DrissionPage 浏览器驱动]
  Browser --> Parser[网络与 DOM 解析器]
  Parser --> API
  API --> Tools[工具调用与命令引擎]
  Config[sites.json / 预设] -.-> Pool
```

## 工作原理

客户端请求 -> FastAPI 路由 (`app/api`) -> 标签页池调度 (`app/core/tab_pool`) -> DrissionPage 驱动浏览器 (`app/core/workflow`) -> 网络/DOM 解析 (`app/core/parsers`) -> 标准 JSON 或 SSE 响应。浏览器配置目录、日志、下载媒体和站点规则默认都保留在项目目录内。

## 快速开始

### 环境要求

- Python 3.10 或更高版本（启动脚本可按 `.env` 中的 `PYTHON_INSTALL_VERSION` 自动准备环境）。
- Chrome、Edge 或 Brave 等 Chromium 浏览器。建议使用项目创建的独立 `chrome_profile`，不要与日常浏览器共用配置目录。
- Windows 支持最完整；macOS/Linux 支持核心功能，文件剪贴板和窗口控制能力可能受系统限制。

### 启动

1. 从 [Releases](https://github.com/lumingya/universal-web-api/releases) 下载并解压到本地目录。路径尽量使用英文、无空格目录，避免浏览器驱动和脚本的路径兼容问题。
2. Windows 双击 `start.bat`；macOS/Linux 在项目根目录执行 `python3 start.py`。也可以直接运行 `python main.py`，但不会获得启动脚本的依赖检查、浏览器拉起和重启接管能力。
3. 首次启动会校验/安装 `requirements.txt`，启动受控浏览器，并在普通浏览器打开控制面板：`http://127.0.0.1:8199/`。
4. 在受控浏览器登录目标 AI 网站，停留在可输入消息的对话页面。控制面板和教程请用普通浏览器访问。
5. 在客户端填写 Base URL `http://127.0.0.1:8199/v1`，模型名从 `GET /v1/models` 返回值中选择。

### 从源码安装（可选）

```bash
python -m venv venv
# Windows: venv\\Scripts\\activate
# macOS/Linux: source venv/bin/activate
python -m pip install -r requirements.txt
python start.py
```

启动后可用以下地址确认服务：

```bash
curl http://127.0.0.1:8199/health
curl http://127.0.0.1:8199/v1/models
```

若启用了认证，在每个请求中加入 `-H "Authorization: Bearer YOUR_TOKEN"` 或 `-H "X-API-Key: YOUR_TOKEN"`。

## API 使用

### OpenAI Chat Completions

```bash
curl http://127.0.0.1:8199/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"YOUR_MODEL_ID","messages":[{"role":"user","content":"你好"}],"stream":false}'
```

将 `stream` 改为 `true` 即可获得 `text/event-stream`。启用认证后再增加 `-H "Authorization: Bearer YOUR_TOKEN"` 或 `-H "X-API-Key: YOUR_TOKEN"`；不要把示例占位值当成真实令牌。支持 `temperature`、`max_tokens`、`stop`、`response_format`、`tools`、`tool_choice`、`parallel_tool_calls` 和 `preset_name` 等常用字段；网页端不支持的字段会被适配或忽略。

Windows PowerShell 可用结构化对象生成 JSON，避免引号转义问题：

```powershell
$body = @{ model = 'YOUR_MODEL_ID'; messages = @(@{ role = 'user'; content = '你好' }); stream = $false } | ConvertTo-Json -Depth 10
Invoke-RestMethod 'http://127.0.0.1:8199/v1/chat/completions' -Method Post -ContentType 'application/json' -Body $body
```

### OpenAI Responses（实验性）

`POST /v1/responses` 接受 `model`、`input`、`instructions`、`tools`、`stream` 等字段，并将请求转换到同一套网页调度流程。需要与 Codex 或使用 Responses API 的客户端联调时再启用，遇到兼容性问题优先改用 Chat Completions。

### Anthropic Messages

`POST /v1/messages` 与 `POST /v1/messages/count_tokens` 兼容 Claude Code 常见请求格式。认证头可使用 `x-api-key: YOUR_TOKEN`；流式模式返回 Anthropic SSE 事件。图片可使用 Anthropic `source.type=base64` 或 URL，工具定义会转换为本地工具调用提示。

### 模型和运行状态

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 服务与浏览器基础健康检查 |
| GET | `/v1/models` | 当前标签页/预设暴露的模型目录 |
| GET | `/v1/provider/capabilities` | 协议、功能和模型能力清单 |
| GET | `/v1/provider/status` | 浏览器连接、标签页池和请求状态（不含密钥） |
| GET | `/api/pool/status` | 标签页池详细状态 |

### 路由写法

默认入口会根据模型目录自动选择标签页。需要稳定绑定时使用下列路径（路径中的域名应与站点配置一致）：

| 场景 | 示例 |
| --- | --- |
| 指定站点 | `POST /url/gemini.google.com/v1/chat/completions` |
| 指定站点和预设 | `POST /url/gemini.google.com/pro/v1/chat/completions` |
| 指定标签页 | `POST /tab/2/v1/chat/completions` |
| 指定站点+标签页 | `POST /url/gemini.google.com/v1/chat/completions?tab_index=2` |
| 精确 URL 会话 | `POST /tab-url/{url_token}/v1/chat/completions` |
| 路由组 | `POST /group/{group_id}/v1/chat/completions` |

也可以在请求体中传 `preset_name`，或在域名路由 URL 上用同名查询参数临时覆盖预设。完整参数和控制台操作见 [本地教程](./static/tutorial/index.html#quickstart)。

## 配置

复制 `.env.example` 为 `.env` 后按需修改。空值和非法数字通常会回落到默认值；修改环境变量后请重启服务。

下表的“默认/示例”会同时覆盖代码回退值与仓库模板值；如果你复制了 `.env.example`，以模板中的显式值为准。

| 变量 | 默认/示例 | 说明 |
| --- | --- | --- |
| `APP_HOST` | `127.0.0.1` | 监听地址。局域网共享才使用 `0.0.0.0`，并同时启用认证和防火墙。 |
| `APP_PORT` | `8199` | API、控制面板和教程端口。 |
| `APP_DEBUG` | `false` | `true` 开启 `/docs`、`/redoc` 和更详细错误；共享环境应关闭。 |
| `AUTH_ENABLED` / `AUTH_TOKEN` | `false` / 空 | API Bearer 或 `X-API-Key` 认证；启用时必须设置强令牌。 |
| `DASHBOARD_AUTH_ENABLED` / `DASHBOARD_AUTH_TOKEN` | 跟随 API / 空 | 控制面板与管理接口认证，可使用独立令牌。 |
| `CORS_ENABLED` / `CORS_ORIGINS` | `false` / `http://127.0.0.1:8199` | 默认关闭跨域；确需跨域时再打开，并改为明确来源列表。 |
| `BROWSER_PORT` | `9222` | 受控浏览器 DevTools 端口，只允许本机访问。 |
| `BROWSER_PATH` / `BROWSER_PROFILE_DIR` / `BROWSER_PROFILE_NAME` | 自动 / 项目目录 / `Default` | 自定义浏览器和独立用户目录。Chrome 136+ 不要直接复用系统默认 User Data。 |
| `SITES_CONFIG_FILE` | `config/sites.json` | 站点、工作流、标签页路由和预设配置。 |
| `PROXY_ENABLED` / `PROXY_ADDRESS` / `PROXY_BYPASS` | 按需 | HTTP/SOCKS5 代理及绕过列表。 |
| `PROFILE_CLEAN_ENABLED` | `false` | 是否在启动时清理受控浏览器缓存；需要保留调试数据时保持关闭。 |
| `SCHEDULED_RESTART_ENABLED` | `false` | 定时平滑重启服务。长请求较多时请合理设置排空超时。 |

站点规则、提取器、解析器、图片预设等 JSON 配置也可在控制面板中编辑。修改前建议使用设置页的备份功能，并保留 `chrome_profile`、`config`、`.env` 和 `logs` 的副本。

## 控制面板与教程

启动后打开 `/`，可以查看浏览器连接、标签页池、请求历史、日志和系统统计。完整教程位于 [`static/tutorial/index.html`](./static/tutorial/index.html)，重点章节如下：

- [快速开始](./static/tutorial/index.html#quickstart)：受控浏览器、首次登录和 Gemini 示例。
- [连接 API 与路由](./static/tutorial/index.html#connect)：Base URL、模型、域名/标签页/URL 路由和 curl 示例。
- [API 端点速查](./static/tutorial/index.html#api-reference)：健康检查、Provider 能力、OpenAI/Responses/Anthropic 端点与认证头。
- [请求监控与排障](./static/tutorial/index.html#dashboard)：查看阶段性错误、取消卡住请求和释放标签页。
- [选择器与工作流](./static/tutorial/index.html#selectors)：CSS 选择器、发送步骤、流式监听与 DOM 回退。
- [预设与标签页池](./static/tutorial/index.html#presets) / [标签页池](./static/tutorial/index.html#tabpool)：并发、分配模式、会话隔离和超时。
- [多模态、文件粘贴和工具调用](./static/tutorial/index.html#multimodal)：附件限制、媒体回传和参数自愈边界。
- [新增站点](./static/tutorial/index.html#addsite)：通过 DOM 分析生成规则并逐字段验证。
- [设置与 FAQ](./static/tutorial/index.html#settings) / [常见问题](./static/tutorial/index.html#faq)：环境变量、浏览器复用和常见错误。
- [安全边界](./static/tutorial/index.html#security-boundary)：本机监听、令牌、CORS、DevTools 端口和敏感数据处理。

## 故障排查

1. **打不开控制面板**：确认 `start.py` 进程仍在运行，检查 `APP_HOST`/`APP_PORT`，并访问 `/health`。端口被占用时改用其他端口后重启。
2. **没有可用标签页**：确认受控浏览器已启动、已登录并停留在目标站点；检查 `/v1/provider/status` 的 `browser.connected` 和 `pool.idle`。
3. **请求排队超时/429**：标签页都在忙或 `acquire_timeout` 太短。降低并发、增加标签页，或调整标签页池分配策略。
4. **首包超时但页面有回复**：站点网络格式可能变化。先查看请求监控，再测试选择器；网络监听满足条件时会回退到 DOM 解析。
5. **回复为空或立即 DONE**：更新浏览器内核，确认页面不是登录/验证码/错误页；检查选择器和 `stream` 配置，必要时关闭系统 Profile 复用。
6. **401/认证失败**：确认 `AUTH_ENABLED` 与 `AUTH_TOKEN` 成对设置，使用 `Authorization: Bearer ...` 或 `X-API-Key`；不要把 Dashboard 令牌当作 API 令牌。
7. **附件/媒体失败**：检查文件大小、后缀和 `temp`/`download_images` 权限；音频转码需要安装 `ffmpeg`。
8. **浏览器无法接管**：关闭占用 `BROWSER_PORT` 的 Chrome，或修改端口；Chrome 136+ 请使用项目独立 Profile。

提交 Issue 前请附上操作系统、Python/浏览器版本、请求路径（隐藏令牌和隐私内容）、错误日志，以及 `/v1/provider/status` 的脱敏结果。不要上传 Cookie、完整浏览器 Profile、API 密钥或聊天原文。

## 安全边界与数据处理

- 本服务是单机调试工具，不提供多租户隔离、计费、审计或高可用保证，不应直接作为公网生产网关。
- `APP_HOST=0.0.0.0` 会让 API 和管理接口监听所有网卡；至少启用两套强令牌、限制 `CORS_ORIGINS`、配置防火墙，并通过可信反向代理提供 TLS。
- DevTools 端口（默认 `9222`）可控制整个受控浏览器，必须限制为回环地址，禁止端口转发和公网暴露。
- 请求日志、媒体文件、临时附件和浏览器登录态可能包含敏感数据。按需关闭详细日志，定期清理 `logs`、`temp`、`download_images`，并保护 `chrome_profile`。
- 代理、辅助 AI、自动更新和命令引擎可能产生额外网络或本地执行行为；只配置自己信任的地址和脚本。

## 反馈、许可证与免责声明

问题可在 [Issues](https://github.com/lumingya/universal-web-api/issues) 提交，也可加入 QQ 群 **1073037753**。提交前请脱敏日志和账号信息。

本项目基于 [AGPL-3.0](./LICENSE) 开源。使用者应自行承担违反目标网站条款、账号受限、数据丢失或其他直接/间接损失的责任；维护者不提供任何可用性或账号安全保证。
