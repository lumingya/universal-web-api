<p align="center">
  <img src="./static/images/logo.svg" alt="Universal Web API logo" width="160">
</p>

# Universal Web API

📖 Documentation • [English](./README.md) • [简体中文](./README.zh-CN.md)

Connect AI websites you already use in your browser, such as ChatGPT, DeepSeek, Claude, or Gemini, to a standard local OpenAI-compatible interface for personal testing, workflow orchestration, and client integration.

## Features

**Workflow-driven**
Browser automation is abstracted into visual workflows. It is highly configurable and lets you extend support to additional sites as needed.

**Flexible request routing**
Built-in tab pooling supports routing by tab, by site, and by round-robin, so concurrent requests work naturally.

**Full multimodal extraction**
Extract text, images, audio, and video from AI web apps based on configuration, and automatically download them locally.

**Network-layer monitoring**
Observe and parse target network responses based on configuration so you can debug the output flow of adapted sites.

**File paste**
Oversized text can be staged as a temporary file before sending, which is useful on sites that handle long context better through file-style inputs. Windows keeps the native clipboard fallback, while other platforms rely on site-native upload entry points.

**Isolated cookie mode**
Create isolated cookie sessions for the same site so different browser contexts can be kept separate.

## Limitations

> ⚠️ **Windows remains the most complete path**. `start.bat` and the native file / image clipboard upload flow are preserved for Windows users.
>
> ⚠️ **macOS / Linux can now start through `python3 start.py`**. On those platforms, attachments rely on page-native `file input`, `drop zone`, or upload-button flows instead of OS-level file / image clipboard paste.
>
> ⚠️ If a target site only accepts clipboard-style attachment paste and exposes no usable upload entry point, **Windows is still recommended** for full parity.
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

> Sites not listed here can still be adapted through AI-based page analysis. See [Add a New Site](./static/tutorial/index.html#add-site-guide).

## Quick Start

1. Download and extract the package from [Releases](../../releases) into a directory **without Chinese characters in the path**
2. Make sure Chrome / Edge / Brave or another Chromium-based browser is installed
3. Start the project:
   - **Windows**: double-click **`start.bat`**
   - **macOS / Linux**: run **`python3 start.py`**
4. Wait for dependency installation and browser startup to finish
5. Open the dashboard at `http://127.0.0.1:8199`
6. Log in to your AI account in the browser that opens automatically
7. In any client that supports the OpenAI API, use:
   - **Base URL**: `http://127.0.0.1:8199/v1`
   - **API Key**: if built-in auth is disabled, use a placeholder value such as `sk-local`; if auth is enabled, it must match your configured auth token

For non-Windows deployments, prefer site configurations that expose `file_input`, `drop_zone`, or an upload button when you need image or file attachments.

For detailed instructions, see the [full tutorial](./static/tutorial/index.html#quick-start).

## Documentation

| Document | Description |
|------|------|
| [Full Tutorial](./static/tutorial/index.html#quick-start) | Installation, startup, login, and dashboard overview |
| [Connect API](./static/tutorial/index.html#connect-api) | Common parameters, routing modes, and request examples |
| [Add a New Site](./static/tutorial/index.html#add-site-guide) | AI auto-recognition and manual site configuration |
| [Function Calling](./static/tutorial/index.html#function-calling) | Tool-calling compatibility and usage guidance |
| [Tab Pool and Presets](./static/tutorial/index.html#tab-pool) | Multi-tab concurrency and preset usage |
| [Core Configuration](./static/tutorial/index.html#selectors) | Selectors, workflow, streaming, multimodal extraction, and file paste |
| [Advanced Configuration](./static/tutorial/index.html#stealth-mode) | Low-interference mode, AI element recognition, and environment settings |
| [Notes and Known Limits](./static/tutorial/index.html#faq) | Runtime limits, known issues, and special-site notes |
| [FAQ](./static/tutorial/index.html#faq) | Troubleshooting startup failures, timeouts, and repeated failures |
| [Parameter Reference](./static/tutorial/index.html#env-config) | Detailed explanation of configuration options |

## Feedback

If you run into problems, you can join the QQ group **1073037753** or open an issue in [Issues](../../issues).

## Disclaimer

This project is for learning, research, and technical discussion only. Make sure you comply with the target site's Terms of Service, and do not use it for commercial purposes or high-frequency automated requests. See the [usage notes in the tutorial](./static/tutorial/index.html#author-note).

## License

[AGPL-3.0](./LICENSE)
