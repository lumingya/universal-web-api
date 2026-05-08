# Universal Web API

📖 Documentation • [English](./README.md) • [简体中文](./README.zh-CN.md)

Turn any AI website you already use, such as ChatGPT, DeepSeek, Claude, or Gemini, into a standard OpenAI-compatible API for free, with full local deployment.

## Features

**Workflow-driven**
Browser automation is abstracted into visual workflows. It is highly configurable and lets you add support for new sites freely.

**Flexible request routing**
Built-in tab pooling supports routing by tab, by site, and by round-robin, so concurrent requests work naturally.

**Full multimodal extraction**
Extract text, images, audio, and video from AI web apps based on configuration, and automatically download them locally.

**Network-layer monitoring**
Intercept and inspect low-level network requests based on configuration, making it possible to capture the raw AI output stream completely.

**File paste**
Oversized text can be saved as a temporary file before sending, helping bypass input-length limits on some websites.

**Isolated cookie mode**
Create isolated cookie sessions for the same site to support concurrent calls with multiple accounts.

## Limitations

> ⚠️ **Windows only**. macOS / Linux are not supported yet.
>
> ⚠️ Requires **Python 3.10+**.

## Supported Sites

| Site | URL | Notes |
|------|-----|-------|
| ChatGPT | chatgpt.com | About 200k max single-send length |
| DeepSeek | chat.deepseek.com | Reply-reading issues in thinking mode |
| Gemini | gemini.google.com | About 30k on free accounts; no clear limit observed on Pro |
| Claude | claude.ai | Site-level parsing and adaptation supported |
| Kimi | www.kimi.com | — |
| Qwen | chat.qwen.ai | Qwen page adaptation supported |
| Grok | grok.com | — |
| Doubao | www.doubao.com | New domain adapted |
| AI Studio | aistudio.google.com | — |
| Arena AI | arena.ai | Sensitive to IP quality; see notes |

> Sites not listed here can still be adapted through AI-based page analysis. See [Add a New Site](./static/tutorial.html#add-site-guide).

## Quick Start

1. Download and extract the package from [Releases](../../releases) into a directory **without Chinese characters in the path**
2. Make sure Chrome / Edge / Brave or another Chromium-based browser is installed
3. Double-click **`start.bat`** and wait for dependency installation to finish
4. Open the dashboard at `http://127.0.0.1:8199`
5. Log in to your AI account in the browser that opens automatically
6. In any client that supports the OpenAI API, use:
   - **Base URL**: `http://127.0.0.1:8199/v1`
   - **API Key**: any value, such as `sk-any`

For detailed instructions, see the [full tutorial](./static/tutorial.html#quick-start).

## Documentation

| Document | Description |
|------|------|
| [Full Tutorial](./static/tutorial.html#quick-start) | Installation, startup, login, and dashboard overview |
| [Connect API](./static/tutorial.html#connect-api) | Common parameters, routing modes, and request examples |
| [Add a New Site](./static/tutorial.html#add-site-guide) | AI auto-recognition and manual site configuration |
| [Function Calling](./static/tutorial.html#function-calling) | Tool-calling compatibility and usage guidance |
| [Tab Pool and Presets](./static/tutorial.html#tab-pool) | Multi-tab concurrency and preset usage |
| [Core Configuration](./static/tutorial.html#selectors) | Selectors, workflow, streaming, multimodal extraction, and file paste |
| [Advanced Configuration](./static/tutorial.html#stealth-mode) | Stealth mode, AI element recognition, and environment settings |
| [Notes and Known Limits](./static/tutorial.html#faq) | Runtime limits, known issues, and special-site notes |
| [FAQ](./static/tutorial.html#faq) | Troubleshooting startup failures, timeouts, and repeated failures |
| [Parameter Reference](./static/tutorial.html#env-config) | Detailed explanation of configuration options |

## Feedback

If you run into problems, you can join the QQ group **1073037753** or open an issue in [Issues](../../issues).

## Disclaimer

This project is for learning, research, and technical discussion only. Make sure you comply with the target site's Terms of Service, and do not use it for commercial purposes or high-frequency automated requests. See the [usage notes in the tutorial](./static/tutorial.html#author-note).

## License

[AGPL-3.0](./LICENSE)
