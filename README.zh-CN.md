# Universal Web API

📖 文档 • [English](./README.md) • [简体中文](./README.zh-CN.md)

将任何你常用的 AI 网站（ChatGPT, DeepSeek, Claude, Gemini 等）转换为标准的 OpenAI 兼容 API 接口，完全免费，支持本地部署。

## 特点

**工作流驱动**
将浏览器自动化操作抽象为可视化工作流，高度可配置，支持自由新增任意站点。

**灵活的请求路由**
内置标签页池，支持按标签页、按站点、按轮询三种 URL 路由方式发起请求，天然支持多请求并发。

**全模态内容提取**
根据配置提取 AI 网页中的文字、图片、音频、视频内容，并自动下载到本地。

**网络层监听**
根据配置拦截并监听底层网络请求，可完整捕获 AI 生成的原始内容流。

**文件粘贴**
将超长文本自动保存为临时文件再发送给 AI，可绕过部分网站输入框的字符长度限制。

**独立 Cookie 模式**
可为同一站点创建相互隔离的独立 Cookie 会话，实现多账号并发调用。

## 限制

> ⚠️ **仅支持 Windows 系统**，暂不支持 macOS / Linux。
>
> ⚠️ 需要 **Python 3.10+**。

## 已适配站点

| 站点 | 地址 | 备注 |
|------|------|------|
| ChatGPT | chatgpt.com | 约 200k 单次最大发送长度 |
| DeepSeek | chat.deepseek.com | 思考模式下存在读取问题 |
| Gemini | gemini.google.com | 无会员约 30k，Pro 会员尚未发现上限 |
| Claude | claude.ai | 已支持站点级解析与适配 |
| Kimi | www.kimi.com | — |
| 通义千问 | chat.qwen.ai | 已支持 Qwen 页面适配 |
| Grok | grok.com | — |
| 豆包 | www.doubao.com | 已适配新版域名 |
| AI Studio | aistudio.google.com | — |
| Arena AI | arena.ai | IP 质量敏感，详见注意事项 |
| 小米mimo | aistudio.xiaomimimo.com | - |

> 未收录的网站支持通过 AI 自动分析网页结构进行适配，详见 [新增站点指南](./static/tutorial.html#add-site-guide)。

## 快速开始

1. 从 [Releases](../../releases) 下载并解压到**无中文路径**的目录
2. 确保已安装 Chrome / Edge / Brave 等 Chromium 内核浏览器
3. 双击运行 **`start.bat`**，等待依赖自动安装完成
4. 打开控制面板 `http://127.0.0.1:8199`
5. 在自动弹出的浏览器中登录你的 AI 账号
6. 在任意支持 OpenAI API 的客户端中填入：
   - **接口地址**：`http://127.0.0.1:8199/v1`
   - **API 密钥**：任意填写（如 `sk-any`）

详细说明请查看 [完整使用文档](./static/tutorial.html#quick-start)。

## 文档

| 文档 | 说明 |
|------|------|
| [完整使用文档](./static/tutorial.html#quick-start) | 安装、启动、登录、控制面板导览 |
| [连接 API](./static/tutorial.html#connect-api) | 通用配置参数、路由方式、调用示例 |
| [新增站点](./static/tutorial.html#add-site-guide) | AI 自动识别与手动配置站点 |
| [函数调用说明](./static/tutorial.html#function-calling) | Tool Calling 兼容与使用建议 |
| [标签页池与预设系统](./static/tutorial.html#tab-pool) | 多标签并发与预设使用方式 |
| [核心功能配置](./static/tutorial.html#selectors) | 选择器、工作流、流式模式、多模态提取、文件粘贴 |
| [高级配置](./static/tutorial.html#stealth-mode) | 隐身模式、AI 元素识别、环境配置 |
| [注意事项与已知限制](./static/tutorial.html#faq) | 运行限制、已知问题、特殊站点说明 |
| [常见问题 FAQ](./static/tutorial.html#faq) | 启动失败、超时、频繁失败等排查 |
| [参数解释](./static/tutorial.html#env-config) | 所有配置项的详细说明 |

## 交流反馈

遇到问题可加 QQ 群 **1073037753** 交流反馈，或在 [Issues](../../issues) 提交问题。

## 免责声明

本项目仅供学习、研究和技术交流使用。使用前请确保遵守目标网站的服务条款，切勿用于商业用途或高频自动化请求。详见 [教程中的使用预期与维护须知](./static/tutorial.html#author-note)。

## 许可证

[AGPL-3.0](./LICENSE)
