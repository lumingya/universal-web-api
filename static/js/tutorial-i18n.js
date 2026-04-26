(function () {
    const translations = {
        meta: {
            htmlLang: 'en',
            pageTitle: 'Universal Web-to-API Guide'
        },
        ui: {
            navTitle: '📑 Navigation',
            pageHeaderTitle: 'Universal Web-to-API Guide (v2.6.9)',
            projectLinkLabel: 'Project Link',
            projectLinkDescription: 'Need the source code, release notes, or the issue tracker? Start here.',
            projectLinkButton: 'Open Repository',
            languageLabel: 'Language',
            themeLight: 'Light Mode',
            themeDark: 'Dark Mode',
            hamburgerOpenLabel: 'Open navigation menu',
            hamburgerCloseLabel: 'Close navigation menu',
            siteCardHint: 'Click to copy the URL, then open it in the controlled browser',
            siteCardCopied: 'URL copied. Paste it into the controlled browser',
            siteCardCopyFailed: 'Copy failed. Please copy it manually and open it in the controlled browser',
            siteCardEmpty: 'No site list was loaded. Use the current dashboard config as the source of truth.'
        },
        nav: {
            'quick-start': '🚀 Quick Start',
            'dashboard-tour': '🖥️ Dashboard Tour',
            'add-site-guide': '🆕 Adding a Site',
            'connect-api': '🔌 Connect API',
            'function-calling': '🧰 Function Calling',
            'tab-pool': '🗂️ Tab Pool',
            'presets': '🎛️ Presets',
            'selectors': '🔍 Selectors',
            'extractors': '🧩 Extractors',
            'image-extraction': '🎞️ Multimodal Extraction',
            'response-detection': '🌊 Response Detection',
            'workflow': '🎬 Workflow',
            'file-paste': '📄 File Attach',
            'stealth-mode': '🛡️ Stealth Mode',
            'commands': '⚡ Automation Commands',
            'ai-recognition': '🎯 AI Recognition',
            'env-config': '⚙️ Environment Settings',
            'browser-config': '🌐 Browser Constants',
            'config-manage': '💾 Config Management',
            'faq': '❓ FAQ',
            'author-note': '⚠️ Notes From the Author'
        },
        sections: {}
    };

    translations.sections['quick-start'] = `
        <h2>🚀 Quick Start</h2>
        <p>Welcome to Web-to-API. This project turns AI chat websites into an OpenAI-compatible API that you can run locally.</p>

        <div class="highlight-box">
            <p><strong>Recommended reading order:</strong> read the main workflow of this page first, then come back to the author note at the end. That section explains the maintenance scope, support expectations, and the most effective feedback channels.</p>
        </div>

        <div class="info-box">
            <p><strong>📌 Core reminder:</strong> after startup the script opens a controlled browser window automatically. <strong style="color: var(--highlight-border);">Keep that browser window open</strong>, because it is the foundation of the service. The tutorial can stay open in your normal browser; <strong style="color: var(--highlight-border);">when the script is about to run, the controlled browser should contain nothing except the target site.</strong></p>
        </div>

        <div class="config-group">
            <h4><span class="icon">1️⃣</span> Recommended first-time flow</h4>
            <ol>
                <li>After you run <code>start.bat</code>, first tell the two browser windows apart: the <strong>controlled browser used by the script</strong>, and the <strong>regular browser that shows this tutorial or that you use every day</strong>.</li>
                <li>By default the controlled browser uses the project's <code>chrome_profile/</code>. On a first launch it is usually a blank, signed-out browser profile. If you did not already have a browser open, the script may also open a separate browser window for this tutorial, so it can look like two browsers appeared at once.</li>
                <li>The window you should actually operate next is the <strong>controlled browser</strong>. Open the site you want to use there. For Gemini, a good starting URL is <code>https://gemini.google.com/</code>. Before the script starts handling requests, make sure that controlled browser contains only the target site, not search pages, tutorial pages, mail, or any unrelated tabs.</li>
                <li>Sign in on that site normally inside the controlled browser, and make sure you have reached the chat page.</li>
                <li>For Gemini, make sure the left sidebar is expanded instead of collapsed. That makes it easier to match the later site configuration and API connection steps.</li>
                <li>Once the site is open and signed in, come back to this tutorial and continue with the <strong>Connect API</strong> section below. Fill your client using the <code>Base URL</code>, <code>API Key</code>, and model examples there.</li>
            </ol>
        </div>

        <div class="note">
            <p><strong>💡 Want to reuse an existing login state?</strong> Close Chrome first, then copy the browser user directory you want to reuse into the project's <code>chrome_profile/</code>, or point <code>BROWSER_PROFILE_DIR</code> in <code>.env</code> to a copied profile directory. At minimum, bring over <code>Local State</code> and the profile folder you want, such as <code>Default</code> or <code>Profile 1</code>, and keep the folder name aligned with <code>BROWSER_PROFILE_NAME</code>. After that, the next launch will use your existing login state directly. The full explanation is in the <strong>Browser Configuration</strong> section later in this page.</p>
        </div>

        <div class="note">
            <p><strong>Dashboard entry:</strong></p>
            <a href="/" class="btn" target="_blank">Open Dashboard</a>
            <p style="margin-top: 10px;">Dashboard URL: <code>http://127.0.0.1:8199/</code>. You can edit configs and inspect logs there.</p>
        </div>

        <h3>✅ Supported sites</h3>
        <p>The following sites already have built-in adaptation and can usually be used directly:</p>
        <p style="margin-top: -6px; color: var(--desc-color);"><strong>Tip:</strong> the site cards below now <strong>copy the URL to your clipboard</strong> instead of opening in the current tutorial browser. Paste the copied URL into the <strong>controlled browser</strong>.</p>

        <div class="site-grid" id="siteGrid"></div>

        <div class="note">
            <p><strong>Sites highlighted in the current docs:</strong> ChatGPT, DeepSeek, Gemini, Claude, Kimi, Qwen, Grok, Doubao, AI Studio, and Arena AI.</p>
            <p style="margin-bottom: 0;">The site cards below refresh from the current runtime config after the page loads. If you changed a domain or startup URL locally, trust the cards and the dashboard over any older screenshots.</p>
        </div>

        <div class="highlight-box">
            <p><strong>⚠️ About arena.ai:</strong> this site is highly sensitive to IP quality. A good IP can keep chatting for a long time, while a poor IP may get blocked immediately. The most important factors are not just IP purity, but also traffic history and sharing level. If your IP quality is weak, Cloudflare challenges can still appear even with network interception disabled. That behavior comes from the site itself, not from this project.</p>
        </div>

        <h3>🆕 For unsupported sites</h3>
        <p>If your target site is not listed above, the system can analyze the page automatically with a helper AI. See the <strong>AI Recognition</strong> section below.</p>
    `;

    translations.sections['dashboard-tour'] = `
        <h2>🖥️ Dashboard Tour</h2>
        <p>The dashboard is not just a settings page. It is the visual control center of the whole project.</p>

        <div class="config-group">
            <h4><span class="icon">📚</span> Sidebar</h4>
            <ul>
                <li>Check browser status, auth status, and total site count.</li>
                <li>Search sites and switch between them quickly.</li>
                <li>Add sites, import configs, and export configs.</li>
                <li>Switch between Sites, Tabs, Logs, Commands, and Settings.</li>
            </ul>
        </div>

        <div class="config-group">
            <h4><span class="icon">🧭</span> Site Configuration</h4>
            <ul>
                <li><strong>Selectors</strong>: where the input box, send button, and result container are.</li>
                <li><strong>Workflow</strong>: the order of actions, such as opening a new chat, filling text, or pressing Enter.</li>
                <li><strong>Response Detection</strong>: how the system decides the reply is finished.</li>
                <li><strong>Multimodal Extraction / File Attach</strong>: useful for returning image, audio, and video assets, plus long-text attachment scenarios.</li>
                <li><strong>Presets</strong>: split one site into different configs for chat, vision, long text, code, and more.</li>
            </ul>
        </div>

        <div class="config-group">
            <h4><span class="icon">🗂️</span> Tabs, Logs, Settings, and Extractor Troubleshooting</h4>
            <ul>
                <li><strong>Tab Pool</strong>: manage tab indexes, states, routes, and assigned presets.</li>
                <li><strong>Extractors</strong>: they are mainly configured as preset fields right now. When the page clearly shows an answer but extraction is wrong, use the extractor section later in this guide to debug it.</li>
                <li><strong>Logs</strong>: locate whether a problem happened before sending, during waiting, or during extraction. If raw DEBUG text feels too dense, you can enable cute <code>INFO / DEBUG</code> log phrasing in settings.</li>
                <li><strong>Commands</strong>: define triggers and automated recovery actions for failures, periodic checks, and operational workflows.</li>
                <li><strong>Settings</strong>: manage environment values, browser constants, AI recognition, and update rules.</li>
            </ul>
        </div>

        <div class="info-box">
            <p><strong>Suggested reading order:</strong> for first-time use, start with the site config page and the tab pool. When debugging failures, go to logs first. When adapting a new site, read <strong>Settings → AI Recognition</strong>.</p>
        </div>

        <div class="note">
            <p><strong>📸 Dashboard overview:</strong> <code>static/tutorial-dashboard-overview.png</code></p>
            <p style="margin-bottom: 0;">This screenshot works well as a visual map of the control panel.</p>
            <img class="doc-image" src="/static/tutorial-dashboard-overview.png" alt="Dashboard overview screenshot">
        </div>
    `;

    translations.sections['add-site-guide'] = `
        <h2>🆕 Adding a Site</h2>
        <p>There are two main paths: <strong>automatic recognition</strong> and <strong>manual configuration</strong>. For a first attempt, automatic recognition is usually the fastest path.</p>

        <h3>Path A: AI-based automatic recognition</h3>
        <ol>
            <li>Open the <a href="/">dashboard</a> → Settings → Environment, then fill in <code>HELPER_API_KEY</code>, <code>HELPER_BASE_URL</code>, and <code>HELPER_MODEL</code>.</li>
            <li>Open the target site in the controlled browser and stay on the real chat page. Before sending the request, make sure the controlled browser contains nothing except the target site.</li>
            <li>Send the <strong>first real API request</strong> to that site.</li>
            <li>If the domain is still missing from <code>config/sites.json</code>, the backend reads the page HTML, asks the helper AI to analyze it, and writes a generated preset into the site config.</li>
        </ol>

        <div class="highlight-box">
            <p><strong>Key point:</strong> automatic recognition starts on the <strong>first real request to an unknown domain</strong>. The <strong>Add Site</strong> button only creates an empty shell.</p>
        </div>

        <h3>Path B: manual configuration</h3>
        <ol>
            <li>Click <strong>Add Site</strong>.</li>
            <li>Enter a domain such as <code>chat.example.com</code>.</li>
            <li>Open the main preset and fill in the three core selectors first.</li>
            <li>Create the shortest possible workflow and test it step by step.</li>
            <li>Save the config and make one real API call.</li>
        </ol>

        <h3>Minimum working configuration</h3>
        <table>
            <tr><th>Key</th><th>Purpose</th><th>Priority</th></tr>
            <tr><td><code>input_box</code></td><td>Chat input field</td><td>✅</td></tr>
            <tr><td><code>send_btn</code></td><td>Send button</td><td>✅</td></tr>
            <tr><td><code>result_container</code></td><td>Container that holds the AI reply</td><td>✅</td></tr>
            <tr><td><code>new_chat_btn</code></td><td>New-chat button</td><td>Optional</td></tr>
            <tr><td><code>message_wrapper</code></td><td>Outer container for one message</td><td>Optional</td></tr>
        </table>

        <pre><code>[
  { "action": "CLICK", "target": "new_chat_btn", "optional": true, "value": null },
  { "action": "WAIT", "target": "", "optional": false, "value": 0.5 },
  { "action": "FILL_INPUT", "target": "input_box", "optional": false, "value": null },
  { "action": "CLICK", "target": "send_btn", "optional": true, "value": null },
  { "action": "KEY_PRESS", "target": "Enter", "optional": true, "value": null },
  { "action": "STREAM_WAIT", "target": "result_container", "optional": false, "value": null }
]</code></pre>

        <h3>Recommended debug order</h3>
        <ol>
            <li>Test <code>input_box</code></li>
            <li>Test <code>send_btn</code></li>
            <li>Test <code>result_container</code></li>
            <li>Run the shortest workflow</li>
            <li>Only then tune stream thresholds, extractors, multimodal extraction, and file attach</li>
        </ol>
    `;

    translations.sections['connect-api'] = `
        <h2>🔌 Connect API</h2>
        <p>This project exposes an <strong>OpenAI-compatible</strong> API.</p>

        <div class="config-box">
            <p><strong>⚙️ Common client settings:</strong></p>
            <ul>
                <li><strong>Provider</strong>: choose <code>OpenAI</code>, <code>OpenAI Compatible</code>, or <code>Custom</code>.</li>
                <li><strong>Base URL</strong>:
                    <ul>
                        <li>Default automatic routing: <code>http://127.0.0.1:8199/v1</code></li>
                        <li>Fixed domain route: <code>http://127.0.0.1:8199/url/gemini.com/v1</code></li>
                        <li>Fixed tab route: <code>http://127.0.0.1:8199/tab/1/v1</code></li>
                        <li>Some clients need the full path: <code>http://127.0.0.1:8199/v1/chat/completions</code></li>
                    </ul>
                </li>
                <li><strong>API Key</strong>: any value is fine, for example <code>sk-any</code>.</li>
                <li><strong>Model</strong>: the real model depends on the website you opened, so this field can usually be any placeholder value.</li>
            </ul>
        </div>

        <div class="success-box">
            <p><strong>✅ Test flow:</strong> once the site is open and logged in, enter the URL and provider settings in your client and you can start testing immediately.</p>
        </div>

        <h3>Routing quick reference</h3>
        <table>
            <tr><th>Need</th><th>Recommended endpoint</th><th>Notes</th></tr>
            <tr>
                <td>Let the system choose an idle tab</td>
                <td><code>http://127.0.0.1:8199/v1/chat/completions</code></td>
                <td>Use this when you do not care which site tab handles the request</td>
            </tr>
            <tr>
                <td>Always use Gemini tabs</td>
                <td><code>http://127.0.0.1:8199/url/gemini.com/v1/chat/completions</code></td>
                <td>Matches one available Gemini-related tab</td>
            </tr>
            <tr>
                <td>Always use a specific tab</td>
                <td><code>http://127.0.0.1:8199/tab/2/v1/chat/completions</code></td>
                <td>Useful when that tab already has a dedicated role or preset</td>
            </tr>
            <tr>
                <td>Use one site with a forced preset</td>
                <td><code>http://127.0.0.1:8199/url/gemini.com/pro/v1/chat/completions</code></td>
                <td>Useful when one site has multiple modes such as chat, pro, music, or video, and also works well as a direct Base URL</td>
            </tr>
        </table>

        <h3>Three ways to specify a preset</h3>
        <p>The chat endpoints now support <code>preset_name</code> directly. This does <strong>not</strong> change the site's default preset. It only affects the current request.</p>

        <p><strong>Option A: put it directly in the path</strong></p>
        <pre><code>curl "http://127.0.0.1:8199/url/gemini.com/pro/v1/chat/completions" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}"</code></pre>

        <p><strong>Option B: put it in the URL query</strong></p>
        <pre><code>curl "http://127.0.0.1:8199/url/gemini.com/v1/chat/completions?preset_name=pro" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}"</code></pre>

        <p><strong>Option C: put it in the JSON body</strong></p>
        <pre><code>{
  "model": "any",
  "messages": [
    { "role": "user", "content": "Hello" }
  ],
  "stream": false,
  "preset_name": "pro"
}</code></pre>

        <div class="info-box">
            <p><strong>Priority rule:</strong> if all three are provided, the order is <strong>path preset &gt; URL query &gt; JSON body</strong>.</p>
        </div>

        <div class="note">
            <p><strong>💡 Practical advice:</strong> if your client lets you customize the Base URL, the cleanest choice is usually <code>http://127.0.0.1:8199/url/gemini.com/pro/v1</code>. If that is inconvenient, fall back to <code>?preset_name=pro</code> or a JSON-body field.</p>
        </div>

        <h3>HTTP methods and full route syntax</h3>
        <table>
            <tr><th>Method</th><th>Route</th><th>Purpose</th></tr>
            <tr>
                <td><code>GET</code></td>
                <td><code>/v1/models</code></td>
                <td>Default model-list endpoint for OpenAI-compatible client checks</td>
            </tr>
            <tr>
                <td><code>POST</code></td>
                <td><code>/v1/chat/completions</code></td>
                <td>Default chat endpoint with automatic tab allocation</td>
            </tr>
            <tr>
                <td><code>GET</code></td>
                <td><code>/url/{domain}/v1/models</code></td>
                <td>Domain-routed model list, can use <code>selector</code> or <code>tab_index</code></td>
            </tr>
            <tr>
                <td><code>POST</code></td>
                <td><code>/url/{domain}/v1/chat/completions</code></td>
                <td>Domain-routed chat, can use <code>selector</code>, <code>tab_index</code>, and <code>preset_name</code></td>
            </tr>
            <tr>
                <td><code>GET</code></td>
                <td><code>/url/{domain}/{preset_name}/v1/models</code></td>
                <td>Domain + preset model-list route, useful when you want to use it directly as a Base URL</td>
            </tr>
            <tr>
                <td><code>POST</code></td>
                <td><code>/url/{domain}/{preset_name}/v1/chat/completions</code></td>
                <td>Domain + preset chat route, where the preset in the path has the highest priority</td>
            </tr>
            <tr>
                <td><code>GET</code></td>
                <td><code>/tab/{index}/v1/models</code></td>
                <td>Fixed-tab model-list endpoint</td>
            </tr>
            <tr>
                <td><code>POST</code></td>
                <td><code>/tab/{index}/v1/chat/completions</code></td>
                <td>Fixed-tab chat endpoint, can use <code>preset_name</code></td>
            </tr>
        </table>

        <p><strong>Supported query parameters:</strong></p>
        <ul>
            <li><code>selector=first_idle</code>: prefer an idle tab. This is the default.</li>
            <li><code>selector=round_robin</code>: rotate across matching site tabs.</li>
            <li><code>selector=random</code>: randomly choose one matching site tab.</li>
            <li><code>tab_index=2</code>: on a domain route, lock the request to one specific tab.</li>
            <li><code>preset_name=pro</code>: force one preset for the current request only.</li>
        </ul>

        <div class="highlight-box">
            <p><strong>⚠️ Route compatibility note:</strong> the server now supports putting the preset directly into the path, such as <code>/url/gemini.com/pro/v1/chat/completions</code>. If your client checks <code>/models</code> first, you can also use <code>/url/gemini.com/pro/v1/models</code>.</p>
        </div>

        <h3>Full examples</h3>
        <pre><code># 1. Default model list
curl http://127.0.0.1:8199/v1/models

# 2. Default chat endpoint
curl http://127.0.0.1:8199/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}"

# 3. Domain-routed model list
curl "http://127.0.0.1:8199/url/gemini.com/v1/models?selector=round_robin"

# 4. Domain-routed chat
curl "http://127.0.0.1:8199/url/gemini.com/v1/chat/completions?selector=first_idle" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}"

# 5. Domain route + fixed tab
curl "http://127.0.0.1:8199/url/gemini.com/v1/chat/completions?tab_index=2" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Continue the previous turn\"}],\"stream\":false}"

# 6. Domain + preset model list
curl http://127.0.0.1:8199/url/gemini.com/pro/v1/models

# 7. Domain + preset chat
curl http://127.0.0.1:8199/url/gemini.com/pro/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}"

# 8. Fixed-tab model list
curl http://127.0.0.1:8199/tab/2/v1/models

# 9. Fixed-tab chat
curl http://127.0.0.1:8199/tab/2/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}"

# 10. Fixed-tab chat with a forced preset
curl "http://127.0.0.1:8199/tab/2/v1/chat/completions?preset_name=pro" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Continue the previous turn\"}],\"stream\":false}"</code></pre>

        <h3>💡 About login state</h3>
        <ul>
            <li><strong>Recommended</strong>: log into the website so the API can inherit your account permissions, such as Plus benefits or history.</li>
            <li><strong>Optional</strong>: if the site allows chatting without login, you can use it directly.</li>
        </ul>

        <div class="note">
            <p><strong>⚠️ About SillyTavern:</strong> the built-in API test can be unreliable. It is better to send a real conversation instead of using the test button.</p>
        </div>

        <div class="highlight-box">
            <p><strong>⚠️ Context-length limit:</strong> websites still limit how much text can fit into one input box. For very long input, enable the <strong>File Attach</strong> feature to bypass part of that limit.</p>
        </div>

        <h3>Observed single-send limits</h3>
        <ul>
            <li><strong>ChatGPT</strong>: about 200k.</li>
            <li><strong>Gemini web</strong>: about 30k on free accounts; no clear limit observed on Pro.</li>
            <li><strong>Arena AI</strong>: about 120k.</li>
        </ul>
    `;

    translations.sections['function-calling'] = `
        <h2>🧰 Function Calling (Tool Calling)</h2>

        <div class="info-box">
            <p><strong>🎯 Quick navigation</strong></p>
            <table>
                <tr><th>What you need</th><th>Recommended feature</th><th>Jump</th></tr>
                <tr><td>Let the model call tools such as search or calculators</td><td>Function calling</td><td><a href="#function-calling">View</a></td></tr>
                <tr><td>Reduce Cloudflare challenges</td><td>Stealth mode</td><td><a href="#stealth-mode">View</a></td></tr>
                <tr><td>Clear cookies every 10 turns</td><td>Automation commands</td><td><a href="#commands">View</a></td></tr>
                <tr><td>Switch proxy when a captcha appears</td><td>Commands + proxy switching</td><td><a href="#proxy-switching">View</a></td></tr>
                <tr><td>You do not know how to write selectors for a new site</td><td>AI recognition</td><td><a href="#ai-recognition">View</a></td></tr>
                <tr><td>Change port, proxy, timeout, or other global settings</td><td>Environment settings</td><td><a href="#env-config">View</a></td></tr>
            </table>
        </div>

        <p>The project supports the <strong>OpenAI-style <code>tools</code> format</strong> and also the older <code>functions</code> / <code>function_call</code> fields, so most clients that already support Tool Calling can connect directly.</p>

        <div class="info-box">
            <p><strong>What this is:</strong> a compatibility layer that lets you keep using familiar OpenAI-style tool definitions.</p>
        </div>

        <div class="highlight-box">
            <p><strong>⚠️ Important boundary:</strong> this is <strong>not native API tool calling</strong>. The backend rewrites your tool definitions into prompt instructions, the website model tries to follow them, and the backend then parses the result back into <code>tool_calls</code>. Reliability depends heavily on the model's own reasoning and formatting discipline.</p>
        </div>

        <h3>What affects success rate</h3>
        <table>
            <tr><th>Factor</th><th>Higher success</th><th>Lower success</th></tr>
            <tr><td><strong>Model strength</strong></td><td>Strong models such as GPT-5 or Claude 4.5</td><td>Smaller or specialized models</td></tr>
            <tr><td><strong>Tool count</strong></td><td>1-3 tools</td><td>10+ tools</td></tr>
            <tr><td><strong>Parameter shape</strong></td><td>Flat and simple</td><td>Deep nesting and complex objects</td></tr>
            <tr><td><strong>Naming clarity</strong></td><td><code>search_web</code>, <code>get_weather</code></td><td><code>func1</code>, <code>tool_x</code></td></tr>
            <tr><td><strong>Prompt length</strong></td><td>Focused system prompts</td><td>Very long role or policy setup</td></tr>
        </table>

        <h3>Typical failure patterns</h3>
        <ul>
            <li>The model answers in plain language instead of calling a tool.</li>
            <li>The JSON shape is malformed and cannot be parsed.</li>
            <li>Argument names do not match the schema.</li>
            <li>The output mixes explanation text with a partial tool call.</li>
        </ul>

        <div class="note">
            <p><strong>Expectation management:</strong> if you see wrong function names, missing arguments, plain chat replies instead of tool calls, or backend parse failures, look at the website model first. In many cases it simply did not produce a stable structured result.</p>
        </div>

        <h3>Practical advice</h3>
        <ol>
            <li>Start with only 1 to 3 tools.</li>
            <li>Use clear verb-based names such as <code>search</code>, <code>calculate</code>, or <code>get</code>.</li>
            <li>Keep schemas flat before introducing deep nesting.</li>
            <li>Prefer stronger models whenever possible.</li>
            <li>If parsing keeps failing, simplify tools and arguments before adding more rules.</li>
        </ol>
    `;

    translations.sections['tab-pool'] = `
        <h2>🗂️ Tab Pool</h2>
        <p>The tab pool is the project's main scheduling mechanism. The script scans supported AI-site tabs in the browser and gives each recognized tab a <strong>persistent index</strong>.</p>

        <h3>How it works</h3>
        <ol>
            <li>You open one or more AI-site tabs in the browser.</li>
            <li>The script detects them and assigns indexes such as 1, 2, and 3.</li>
            <li>When an API request arrives, the system chooses one idle tab to handle it.</li>
            <li>After the request finishes, that tab goes back to the pool.</li>
        </ol>

        <h3>Routing options</h3>
        <table>
            <tr><th>Route</th><th>Path format</th><th>Description</th></tr>
            <tr>
                <td><strong>Default route</strong></td>
                <td><code>/v1/chat/completions</code></td>
                <td>Automatically uses one idle tab</td>
            </tr>
            <tr>
                <td><strong>Fixed domain route</strong></td>
                <td><code>/url/{domain}/v1/chat/completions</code></td>
                <td>Matches a tab from the specified domain, such as <code>/url/gemini.com/v1/chat/completions</code></td>
            </tr>
            <tr>
                <td><strong>Fixed tab route</strong></td>
                <td><code>/tab/{index}/v1/chat/completions</code></td>
                <td>Uses a specific tab index and queues if that tab is busy</td>
            </tr>
        </table>

        <div class="info-box">
            <p><strong>💡 When to use a fixed tab:</strong></p>
            <ul style="margin-bottom: 0;">
                <li>When one client should always use a specific website tab</li>
                <li>When different tabs use different presets</li>
                <li>When you want to preserve continuity in a specific tab</li>
            </ul>
        </div>

        <h3>Advanced parameters for domain routes</h3>
        <table>
            <tr><th>Parameter</th><th>Example</th><th>Effect</th></tr>
            <tr>
                <td><code>selector</code></td>
                <td><code>?selector=round_robin</code></td>
                <td>Choose how the server picks among multiple matching tabs</td>
            </tr>
            <tr>
                <td><code>tab_index</code></td>
                <td><code>?tab_index=2</code></td>
                <td>Further pin a domain route to one specific tab</td>
            </tr>
            <tr>
                <td><code>preset_name</code></td>
                <td><code>?preset_name=pro</code></td>
                <td>Temporarily override the preset for only this request</td>
            </tr>
        </table>

        <div class="note">
            <p><strong>💡 Recommended mental model:</strong> <code>/url/gemini.com/...</code> decides <em>which site</em>, <code>selector</code> or <code>tab_index</code> decides <em>which tab</em>, and <code>preset_name</code> decides <em>which config</em> for this one request.</p>
        </div>

        <h3>Managing tabs in the dashboard</h3>
        <p>In the <strong>Tabs</strong> panel you can:</p>
        <ul>
            <li>view real-time state of every tab</li>
            <li>inspect current URL and request count</li>
            <li>copy the dedicated endpoint of a tab</li>
            <li>assign different presets to different tabs</li>
        </ul>

        <div class="note">
            <p><strong>⚠️ Notes:</strong></p>
            <ul style="margin-bottom: 0;">
                <li>Tab indexes remain stable during one script session and are reassigned after restart.</li>
                <li>Closing a browser tab releases its index automatically.</li>
                <li>New tabs are usually detected within a few seconds.</li>
                <li>Blank pages such as <code>chrome://newtab</code> are not added to the pool.</li>
            </ul>
        </div>
    `;

    translations.sections['presets'] = `
        <h2>🎛️ Presets</h2>
        <p>Presets let you create <strong>multiple independent configurations for the same site</strong> and assign different presets to different tabs.</p>

        <h3>Typical use cases</h3>
        <table>
            <tr><th>Tab</th><th>Preset</th><th>Difference</th></tr>
            <tr>
                <td>Tab #1</td>
                <td>Pro chat</td>
                <td>Full workflow, longer timeout, deep extractor</td>
            </tr>
            <tr>
                <td>Tab #2</td>
                <td>Fast vision</td>
                    <td>Simpler workflow, multimodal extraction enabled, shorter timeout</td>
            </tr>
            <tr>
                <td>Tab #3</td>
                <td>Coding assistant</td>
                <td>File attach enabled, higher thresholds, network interception mode</td>
            </tr>
        </table>

        <h3>How to use presets</h3>

        <p><strong>1. Create a preset</strong></p>
        <ol>
            <li>Select a site in the dashboard.</li>
            <li>Use the preset selector at the top of the config panel.</li>
            <li>Click <strong>+ New Preset</strong> and enter a name.</li>
            <li>The new preset clones the current preset as a starting point.</li>
            <li>Edit selectors, workflow, extractors, and other settings independently.</li>
            <li>If needed, click the star action to make it the default preset.</li>
        </ol>

        <p><strong>2. Assign a preset to a tab</strong></p>
        <ol>
            <li>Open the <strong>Tabs</strong> page.</li>
            <li>Find the target tab row.</li>
            <li>Choose the preset from the preset dropdown.</li>
        </ol>

        <p><strong>3. Call the site by domain or fixed tab</strong></p>
        <pre><code># Assume one Gemini tab already exists and tab #2 uses the "Fast vision" preset

# Use the Gemini domain route
curl http://127.0.0.1:8199/url/gemini.com/v1/chat/completions

# Use the fixed vision tab
curl http://127.0.0.1:8199/tab/2/v1/chat/completions</code></pre>

        <p><strong>4. Override the preset for just one request</strong></p>
        <pre><code># Option A: path-based preset (best when used directly as a Base URL)
curl "http://127.0.0.1:8199/url/gemini.com/pro/v1/chat/completions" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Write me a short intro\"}],\"stream\":false}"

# Option B: query parameter
curl "http://127.0.0.1:8199/url/gemini.com/v1/chat/completions?preset_name=pro" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Write me a short intro\"}],\"stream\":false}"

# Option C: JSON body
curl http://127.0.0.1:8199/url/gemini.com/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Write me a short intro\"}],\"stream\":false,\"preset_name\":\"pro\"}"

# Fixed tab + forced preset
curl "http://127.0.0.1:8199/tab/2/v1/chat/completions?preset_name=pro" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"any\",\"messages\":[{\"role\":\"user\",\"content\":\"Continue the previous turn\"}],\"stream\":false}"</code></pre>

        <div class="info-box">
            <p><strong>Calling rule:</strong> if you explicitly pass <code>preset_name</code>, that preset is forced for the current request. If path, query, and body all provide one, the order is <strong>path &gt; query &gt; body</strong>. If you do not pass it, the old behavior remains: tab-bound preset first, then site default preset.</p>
        </div>

        <div class="info-box">
        <p><strong>💡 Tip:</strong> every preset contains its own selectors, workflow, stream config, multimodal extraction, and file-attach settings. Editing one preset does not affect the others.</p>
        </div>

        <h3>Example structure</h3>
<pre><code>{
  "gemini.google.com": {
    "default_preset": "Fast vision",
    "presets": {
      "Main": {
        "selectors": { ... },
        "workflow": [ ... ],
        "stream_config": { ... },
        "image_extraction": { "enabled": false },
        "file_paste": { "threshold": 50000 }
      },
      "Fast vision": {
        "selectors": { ... },
        "workflow": [ ... ],
        "image_extraction": { "enabled": true, "mode": "first" },
        "file_paste": { "threshold": 10000 }
      }
    }
  }
}</code></pre>

        <div class="note">
            <p><strong>⚠️ Warning:</strong> deleting a preset cannot be undone, and at least one preset must remain.</p>
        </div>
    `;

    translations.sections['selectors'] = `
        <h2>🔍 Selector Configuration</h2>
        <p>Each site and each preset needs a set of CSS selectors so the program knows where to type and where to click.</p>

        <h3 id="selector-basics">Start with these three jobs</h3>
        <div class="tutorial-cta-grid">
            <div class="tutorial-cta-card">
                <h4>Learn the three core fields first</h4>
                <p>For the first pass, focus only on <code>input_box</code>, <code>send_btn</code>, and <code>result_container</code>. Once these three are correct, the page usually becomes testable.</p>
                <div class="tutorial-pill-list">
                    <span class="tutorial-pill"><strong>input_box</strong> where you type</span>
                    <span class="tutorial-pill"><strong>send_btn</strong> where the message is submitted</span>
                    <span class="tutorial-pill"><strong>result_container</strong> where the reply appears</span>
                </div>
            </div>
            <div class="tutorial-cta-card">
                <h4>Use the local practice page first</h4>
                <p>The project now ships with a local mock site built for selector practice. It is a much easier place to learn the field mapping before you inspect a real website.</p>
                <div class="tutorial-callout-actions">
                    <a class="btn" href="./selector-practice.html" target="_blank" rel="noopener">Open the local practice page</a>
                </div>
            </div>
        </div>

        <pre><code>"selectors": {
  "input_box": "textarea[id='prompt']",
  "send_btn": "button.send-btn",
  "result_container": ".markdown-body",
  "new_chat_btn": "button.new-chat",
  "temp_chat_btn": "button.temp-chat"
}</code></pre>
        <ul>
            <li><strong>input_box</strong>: required. The chat input field.</li>
            <li><strong>send_btn</strong>: required. The send button.</li>
            <li><strong>result_container</strong>: required. The container that holds the AI reply.</li>
            <li><strong>new_chat_btn</strong>: optional. Used to start a fresh conversation.</li>
        </ul>

        <div class="info-box">
            <p><strong>💡 How to find more stable selectors:</strong></p>
            <ul class="tutorial-mini-list">
                <li>Look for <code>textarea</code>, <code>button</code>, <code>input[type=file]</code>, <code>aria-label</code>, and <code>data-testid</code> first.</li>
                <li>If a class name looks like a random generated string, keep searching. Those selectors often become fragile after a UI update.</li>
                <li>For <code>result_container</code>, start with a larger wrapper around the whole answer. Targeting only a small <code>p</code> or <code>span</code> tends to miss content.</li>
            </ul>
        </div>

        <h3 id="selector-testing-checklist">Suggested fill order</h3>
        <ol>
            <li>Inspect the input area first and fill <code>input_box</code>.</li>
            <li>Find the send button and fill <code>send_btn</code>.</li>
            <li>Wait until one AI answer appears, then capture the outer answer wrapper as <code>result_container</code>.</li>
            <li>Use the dashboard test button after every field instead of waiting until the end.</li>
            <li>After the first three fields are stable, add <code>new_chat_btn</code>, <code>message_wrapper</code>, and upload-related selectors when needed.</li>
        </ol>

        <div class="success-box">
            <p><strong>🧪 Test selectors:</strong> use the <strong>Test</strong> button in the dashboard to confirm whether a selector really works. The local practice page also includes highlight and copy actions, which helps a lot during the first setup.</p>
        </div>

        <div class="note">
            <p><strong>💡 Custom selectors:</strong> you can define extra selectors such as a temporary-chat button and then reference them inside the workflow.</p>
        </div>
    `;

    translations.sections['extractors'] = `
        <h2>🧩 Extractor Configuration</h2>
        <p>When an AI reply comes back messy, incomplete, or loses formatting, start with the extractor. If the issue lives in the underlying response body, move on to parsers and network interception.</p>
        <pre><code>{
  "extractor_id": "deep_mode_v1",
  ...
}</code></pre>
        <ul>
            <li><strong>Default mode</strong>: reads text directly from <code>result_container</code>, which is enough for most normal chat pages.</li>
            <li><strong>deep_mode</strong>: adds extra handling for complex code blocks, LaTeX formulas, and similar formatting-heavy output.</li>
        </ul>

        <div class="note">
            <p><strong>💡 Configuration advice:</strong> if the full answer is already visible on the page and only the extracted result looks wrong, stay in the extractor layer. If default mode is not enough, switch to <strong>deep_mode</strong>. If the real issue is that the answer is not rendered yet, or you want to read the raw JSON / text directly, move on to parsers and network interception.</p>
        </div>

        <div class="info-box">
            <p><strong>⚠️ Complex output reminder:</strong> even <strong>deep_mode</strong> can still struggle with some difficult code blocks. If you need more faithful code, formulas, or raw streaming output, continue to the network interception mode below. Just remember that unsupported sites should not enable it casually.</p>
        </div>
    `;

    translations.sections['image-extraction'] = `
        <h2>🎞️ Multimodal Extraction</h2>
        <p>The control panel now calls this feature <strong>Multimodal Extraction</strong>. It is responsible for extracting images, audio, and video from the page, then returning stable local URLs or Markdown references that downstream clients can use directly.</p>
        <pre><code>"image_extraction": {
  "enabled": true,
  "modalities": {
    "image": true,
    "audio": true,
    "video": true
  },
  "selector": "img",
  "audio_selector": "audio, audio source",
  "video_selector": "video, video source",
  "container_selector": ".img-grid",
  "download_blobs": true,
  "mode": "all",
  "max_size_mb": 10
}</code></pre>
        <div class="info-box">
            <p><strong>Compatibility note:</strong> the stored config key is still <code>image_extraction</code> for backward compatibility, even though the UI and capability are now multimodal.</p>
        </div>
        <p><strong>How to use it in the UI:</strong></p>
        <ul>
            <li>Enable the top-level multimodal switch first.</li>
            <li>Then enable the inner options for <strong>image</strong>, <strong>audio file</strong>, and <strong>video</strong> as needed.</li>
            <li>If you only need one modality, you usually only need to maintain the selector for that specific modality.</li>
        </ul>
        <p><strong>Saved locations:</strong></p>
        <ul>
            <li><strong>Images you send to the AI</strong>: stored in <code>image/</code>.</li>
            <li><strong>Images, audio, and video generated or returned by the AI</strong>: all stored in <code>download_images/</code> and exposed through local URLs.</li>
        </ul>

        <div class="note">
            <p><strong>💡 Response shape:</strong> images are returned as embedded Markdown images, while audio and video are returned as Markdown links such as <code>[audio_0](/download_images/xxx.mp3)</code> or <code>[video_0](/download_images/xxx.mp4)</code>.</p>
        </div>

        <div class="note">
            <p><strong>💡 Practical advice:</strong> decide which modalities you need first, then verify the corresponding selectors against real media nodes. Only add <code>container_selector</code> when the page contains too many unrelated media elements and you need to narrow the search area.</p>
        </div>
    `;

    translations.sections['response-detection'] = `
        <h2>🌊 Response Detection</h2>
        <p>The system needs to know when the AI starts speaking and when it has really finished. There are two modes here: the default DOM mode and the more advanced network interception mode.</p>

        <h3 id="non-stream-listener-basics">When should you look at non-stream monitoring?</h3>
        <div class="tutorial-cta-grid">
            <div class="tutorial-cta-card">
                <h4>Most sites should stay on DOM mode first</h4>
                <p>If the reply is visible on the page and you can capture it reliably, DOM mode is already good enough. It is also the easiest starting point when you adapt a new site for the first time.</p>
            </div>
            <div class="tutorial-cta-card">
                <h4>Switch when these problems show up</h4>
                <p>Move to network interception when code blocks keep breaking, formulas keep losing structure, DOM extraction becomes unreliable, or the site returns one complete JSON / text payload at a time.</p>
                <div class="tutorial-pill-list">
                    <span class="tutorial-pill"><strong>listen_pattern</strong> request keyword</span>
                    <span class="tutorial-pill"><strong>parser</strong> response parser</span>
                </div>
            </div>
        </div>

        <table>
            <tr><th>Mode</th><th>Streaming</th><th>Description</th></tr>
            <tr>
                <td><strong>DOM mode</strong> (recommended default)</td>
                <td>✅ Yes</td>
                <td>Watches page changes like a human eye and works well for most normal chat scenarios</td>
            </tr>
            <tr>
                <td><strong>Network interception mode</strong></td>
                <td>✅ Yes, site-dependent</td>
                <td>Intercepts browser requests directly and parses the response body, better for advanced extraction on adapted sites</td>
            </tr>
        </table>

        <h3>DOM mode (recommended 👀)</h3>
        <p>DOM mode works by “watching the page”: as long as the content is still changing, the system assumes the AI is still typing. It is the best default for plain-text chats, roleplay, and most everyday use cases.</p>
        <ul>
            <li><strong>Best for</strong>: ordinary text conversations and most general chat flows.</li>
            <li><strong>Strength</strong>: highly universal, with no parser required for each site.</li>
            <li><strong>Limitation</strong>: it depends on the site's HTML structure, so complicated code blocks and formulas may still be incomplete.</li>
        </ul>

        <h4>Decision rule</h4>
        <pre><code>Finished = (stable checks ≥ target count) AND (silence duration &gt; silence timeout)</code></pre>
        <p><strong>How it works:</strong></p>
        <ol>
            <li>The script checks the page at the configured interval.</li>
            <li>If the content changes, the system knows the AI is still generating and keeps streaming new content.</li>
            <li>If the content stops changing, the stability count goes up and the silence timer keeps accumulating.</li>
            <li>Once both thresholds are satisfied, the reply is considered finished.</li>
            <li>If nothing appears before the initial wait expires, the request is treated as failed, which often means the selector is wrong or the reply never started.</li>
        </ol>

        <h4>Tuning suggestions</h4>
        <table>
            <tr><th>Scenario</th><th>Silence timeout</th><th>Stable checks</th><th>Initial wait</th></tr>
            <tr><td>Fast models (GPT-4o)</td><td>3-5 s</td><td>3-5</td><td>60 s</td></tr>
            <tr><td>Slow reasoning (o1 / Claude)</td><td>10-15 s</td><td>8-12</td><td>300 s</td></tr>
            <tr><td>Long-form or code generation</td><td>8-15 s</td><td>6-10</td><td>180-300 s</td></tr>
        </table>

        <h3>Network interception mode (advanced 📡)</h3>
        <div class="highlight-box">
            <p><strong>⚠️ Prerequisite:</strong> network interception must be used together with a site parser. Do not enable it casually on unsupported sites, or you may get errors or raise the chance of anti-bot / Cloudflare challenges.</p>
        </div>

        <p>Network interception works like reading the browser's underlying response directly. It intercepts XHR / Fetch traffic, reads the raw JSON or text, and turns it into final output or incremental chunks. On adapted sites, it is often faster, more stable, and better at preserving code blocks and formulas.</p>
        <ul>
            <li><strong>Best for</strong>: higher-fidelity code blocks, formulas, or sites where DOM extraction is unreliable.</li>
            <li><strong>Strength</strong>: usually more stable on adapted sites and closer to the raw streamed response.</li>
            <li><strong>Requirement</strong>: you need a valid <code>parser</code> and a good <code>listen_pattern</code>.</li>
        </ul>

        <div class="info-box">
            <p><strong>💡 How to enable it:</strong> open the site configuration in the dashboard, go to <strong>Streaming Configuration</strong>, and switch the mode to <strong>Network Interception</strong>.</p>
        </div>

        <div class="note">
            <p><strong>💡 Recommended order:</strong> lock the request keyword first, choose the parser second, and tune timeouts after that. Many failed attempts happen because the first two fields are still wrong.</p>
        </div>

        <h4>Network interception fields</h4>
        <table>
            <tr><th>Field</th><th>Description</th><th>Example</th></tr>
            <tr>
                <td><strong>URL match pattern</strong></td>
                <td>Intercept only requests whose URL contains this text; start with the most stable path keyword</td>
                <td><code>GenerateContent</code></td>
            </tr>
            <tr>
                <td><strong>Response parser</strong></td>
                <td>The parser ID used to read the intercepted response body</td>
                <td><code>lmarena</code> / custom parser</td>
            </tr>
            <tr>
                <td><strong>First response timeout</strong></td>
                <td>Maximum wait for the first useful network response</td>
                <td><code>300</code> s</td>
            </tr>
            <tr>
                <td><strong>Silence timeout</strong></td>
                <td>How long to wait after the last new chunk before considering the reply finished</td>
                <td><code>3</code> s</td>
            </tr>
            <tr>
                <td><strong>Polling interval</strong></td>
                <td>How often the listener checks for new intercepted data</td>
                <td><code>0.5</code> s</td>
            </tr>
        </table>

        <div class="note">
            <p><strong>💡 Arena parser note:</strong> <code>arena.ai</code> already ships with two parsers. <code>lmarena</code> is for direct single-column mode, and <code>lmarena_side_left</code> is for side-by-side mode and reads the left output automatically. In most cases, keep the default mapping.</p>
        </div>

        <div class="highlight-box">
            <p><strong>⚠️ arena.ai:</strong> in practice, it is still safer to stay on DOM mode for this site. Extra browser connections from network monitoring can raise the chance of Cloudflare challenges.</p>
        </div>

        <h4 id="non-stream-parser-guide">Practical guide: ask AI to write a parser for a new site</h4>
        <p>If the target site returns JSON, SSE, or plain text from the network layer, you usually need a custom parser. The fastest workflow now is: try the built-in debug capture first, and only fall back to the manual console listener when the built-in capture misses the request.</p>

        <div class="success-box">
            <p><strong>Recommended first choice:</strong> turn on the built-in parser debug capture in <strong>Dashboard → Settings → Browser Constants</strong>. When a network parser is hit, the project can automatically save raw bodies and parser debug data into <code>logs/network_parser_debug/</code>.</p>
            <ul>
                <li>Enable <code>NETWORK_DEBUG_CAPTURE_ENABLED</code>.</li>
                <li>Optionally set <code>NETWORK_DEBUG_CAPTURE_PARSER_FILTER</code> to a parser ID such as <code>deepseek</code> so you only capture the site you are debugging.</li>
                <li>Leave the parser filter empty if you want to capture everything.</li>
            </ul>
        </div>

        <div class="highlight-box">
            <p><strong>⚠️ Important:</strong> built-in capture is much simpler, but it is not guaranteed to catch every site or every response shape. Some websites still need manual in-page interception, especially when their traffic is hidden behind unusual wrappers or paths that the normal listener cannot see cleanly.</p>
        </div>

        <h4>Preferred workflow: built-in debug capture</h4>
        <ol>
            <li>Open the target site in the controlled browser and stay on the real chat page.</li>
            <li>In <strong>Dashboard → Settings → Browser Constants</strong>, enable <code>NETWORK_DEBUG_CAPTURE_ENABLED</code>.</li>
            <li>Optionally fill <code>NETWORK_DEBUG_CAPTURE_PARSER_FILTER</code> with the parser you are testing, such as <code>deepseek</code>.</li>
            <li>Turn on network interception for that site, then send one simple real message and wait until the reply finishes.</li>
            <li>Open <code>logs/network_parser_debug/</code> and collect the generated JSON debug files.</li>
            <li>Send those debug files to AI together with <code>app/core/parsers/base.py</code>, <code>app/core/parsers/__init__.py</code>, and one or two similar parser files.</li>
            <li>Ask AI to create <code>app/core/parsers/xxx_parser.py</code> and tell you how to register it in <code>__init__.py</code>.</li>
            <li>Return to the dashboard, fill in the suggested <code>listen_pattern</code> and parser ID, then test again.</li>
            <li>If the result is still wrong, send AI the debug dump, the bad output, and your current parser file for another round.</li>
        </ol>

        <h4>Fallback workflow: manual console listener</h4>
        <p>Use this only when the built-in capture did not record the request or did not preserve enough structure.</p>

        <div class="info-box">
            <p><strong>Manual helper script:</strong> the project already includes <code>static/拦截请求发生器.txt</code>. You can copy it from this tutorial section or open the original text file directly.</p>
            <div class="script-link-row">
                <button type="button" class="btn" id="copyRequestGeneratorBtn">Copy Script</button>
                <a class="btn" href="./%E6%8B%A6%E6%88%AA%E8%AF%B7%E6%B1%82%E5%8F%91%E7%94%9F%E5%99%A8.txt" target="_blank" rel="noopener">Open Script</a>
            </div>
            <details class="inline-script-viewer">
                <summary>View the script inside the tutorial</summary>
                <iframe
                    id="requestGeneratorFrame"
                    class="inline-script-frame"
                    src="./%E6%8B%A6%E6%88%AA%E8%AF%B7%E6%B1%82%E5%8F%91%E7%94%9F%E5%99%A8.txt"
                    title="Request generator script"
                ></iframe>
            </details>
        </div>

        <ol>
            <li>Open the browser developer tools and switch to <code>Console</code>.</li>
            <li>Copy the request generator script and run it on the real site page.</li>
            <li>Send one simple real message and wait until the reply finishes.</li>
            <li>Run <code>exportRequests()</code> in the console. The browser will download a requests JSON export.</li>
            <li>Send that JSON to AI together with the same parser base files and reference parsers.</li>
            <li>Ask AI to build the parser and tell you what <code>listen_pattern</code> and parser ID to use.</li>
        </ol>

        <div class="info-box">
            <p><strong>💡 Tell AI these requirements:</strong></p>
            <ul>
                <li>This parser may receive plain JSON, SSE-like event streams, or text chunks, so inspect the raw response body carefully before deciding how to extract content.</li>
                <li>After the first successful parse, return <code>done=True</code> to avoid duplicate output.</li>
                <li>If images are present, put them in the <code>images</code> field.</li>
                <li>Tell me what needs to be changed in <code>app/core/parsers/__init__.py</code> to register the parser.</li>
            </ul>
        </div>

        <p>You can send AI a prompt like this:</p>
        <pre><code>This is a network parser debug sample from a new site. Please refer to the existing parsers in app/core/parsers and help me add a new parser.

Requirements:
1. Create a new parser class in app/core/parsers/xxx_parser.py and inherit from ResponseParser
2. Inspect the raw response carefully and decide whether it is plain JSON, an SSE/event stream, or another text format
3. After the first successful parse, return done=True to avoid duplicate appends
4. If images are included, fill the images field
5. Tell me what code I need to change in app/core/parsers/__init__.py to register it
6. Tell me what listen_pattern and parser I should fill into the site configuration

Attached:
- debug dump files from logs/network_parser_debug (preferred), or exported requests json from the manual listener if built-in capture missed it
- one or two reference parser files
- app/core/parsers/base.py
- app/core/parsers/__init__.py</code></pre>
    `;

    translations.sections['workflow'] = `
        <h2>🎬 Workflow</h2>
        <p>The workflow defines a sequence of actions. Each preset can have its own independent workflow.</p>
        <pre><code>"workflow": [
  { "action": "CLICK", "target": "new_chat_btn" },
  { "action": "WAIT", "value": 0.5 },
  { "action": "FILL_INPUT", "target": "input_box" },
  { "action": "CLICK", "target": "send_btn" },
  { "action": "STREAM_WAIT" }
]</code></pre>
        <table>
            <tr><th>Action</th><th>Description</th></tr>
            <tr><td><code>CLICK</code></td><td>Click an element, where <code>target</code> is a selector key</td></tr>
            <tr><td><code>FILL_INPUT</code></td><td>Fill the input box with the user prompt</td></tr>
            <tr><td><code>WAIT</code></td><td>Wait for a fixed number of seconds</td></tr>
            <tr><td><code>KEY_PRESS</code></td><td>Simulate a key press such as Enter</td></tr>
            <tr><td><code>STREAM_WAIT</code></td><td>Wait until the reply is finished</td></tr>
        </table>

        <div class="note">
            <p><strong>📸 Workflow visualization:</strong> <code>static/workflow-visualization.png</code></p>
            <p style="margin-bottom: 0;">This screenshot shows how the visual workflow mode helps locate page elements and action steps.</p>
            <img class="doc-image" src="/static/workflow-visualization.png" alt="Workflow visualization screenshot">
        </div>
    `;

    translations.sections['file-paste'] = `
        <h2>📄 File Attach</h2>
        <p>When your prompt is too long for the site's input box, the system can write it into a temporary <code>.txt</code> file and try to send it through the site's own upload entry instead of forcing all of the text into the input box.</p>

        <h3>Settings</h3>
        <table>
            <tr><th>Field</th><th>Default</th><th>Description</th></tr>
            <tr><td>Enabled</td><td>Depends on site preset</td><td>Whether file-attach mode is enabled</td></tr>
            <tr><td>Threshold</td><td><code>50000</code> chars</td><td>Global default threshold; site presets can override it</td></tr>
            <tr><td>Hint text</td><td><code>Focus entirely on the file content</code></td><td>Extra instruction appended after the file is attached to remind the model to read the attachment</td></tr>
        </table>

        <div class="info-box">
            <p><strong>💡 Upload order:</strong> the system tries <code>file_input</code> first, then generic <code>input[type=file]</code>, then <code>upload_btn</code>, then <code>drop_zone</code>. Only after all of those fail does it fall back to the Windows clipboard path, and it will only continue sending after the attachment state looks stable.</p>
        </div>

        <h3>What to configure</h3>
        <p>For new sites, do not stop at <code>enabled: true</code>. You still need to confirm that the site has a usable <code>upload_btn</code>, <code>file_input</code>, or <code>drop_zone</code> selector, or the feature will not be stable.</p>

        <div class="note">
            <p><strong>⚠️ Why the hint text matters:</strong> after the upload succeeds, the system appends a short reminder such as “focus entirely on the file content” so the model does not ignore the attachment and only react to the short text in the box.</p>
        </div>

        <h3>Enabled by default in built-in presets</h3>
        <ul>
            <li><code>aistudio.google.com</code></li>
            <li><code>gemini.google.com</code></li>
            <li><code>chat.deepseek.com</code></li>
            <li><code>www.doubao.com</code></li>
            <li><code>chat.qwen.ai</code></li>
        </ul>

        <div class="highlight-box">
            <p><strong>Practical advice:</strong> first confirm that the target site really supports file uploads, then make sure at least one reliable upload selector works, and only then test long prompts. Turning on the flag alone is usually not enough.</p>
        </div>
    `;

    translations.sections['stealth-mode'] = `
        <h2>🛡️ Stealth Mode (Anti-Detection)</h2>
        <p>Stealth mode reduces automation-detection risk by making clicks, cursor movement, scrolling, and timing look more like real human behavior.</p>

        <h3>Core idea</h3>
        <p>Instead of “teleport and click immediately”, stealth mode uses more human-like motion and pauses so anti-bot systems have fewer obvious signals to latch onto.</p>
        <table>
            <tr><th>Action</th><th>Normal mode</th><th>Stealth mode</th></tr>
            <tr><td>Click</td><td>Immediate CDP command</td><td>Press -> tiny movement -> release</td></tr>
            <tr><td>Move</td><td>Instant jump</td><td>Bezier-like human motion</td></tr>
            <tr><td>Idle</td><td>No movement</td><td>Small random drift</td></tr>
            <tr><td>Scroll</td><td>Direct call</td><td>Wheel-event style interaction</td></tr>
            <tr><td>Delay</td><td>Minimal</td><td>Randomized 0.1-0.3 s</td></tr>
        </table>

        <h3>When to enable it</h3>
        <ul>
            <li><strong>Recommended</strong>: sites with strong anti-bot protection such as <code>arena.ai</code> and <code>chatgpt.com</code>.</li>
            <li><strong>Recommended</strong>: when captcha or automation warnings appear often.</li>
            <li><strong>Usually unnecessary</strong>: lower-protection sites such as AI Studio or DeepSeek.</li>
        </ul>

        <div class="info-box">
            <p><strong>📍 Where to enable it:</strong> select a site in the dashboard and check <strong>Stealth Mode</strong> at the top. This setting is stored per preset.</p>
        </div>

        <h3>DrissionPage patch (important)</h3>
        <p>This project applies a small DrissionPage patch so network monitoring can reuse the browser's main connection instead of opening extra ones, which lowers detection risk.</p>
        <ul>
            <li><strong>Automatic</strong>: <code>start.bat</code> applies the patch after dependency installation.</li>
            <li><strong>Manual</strong>: <code>python patch_drissionpage.py</code></li>
            <li><strong>Restore</strong>: <code>python patch_drissionpage.py --restore</code></li>
        </ul>

        <div class="note">
            <p><strong>⚠️ Important:</strong> re-apply the patch after every DrissionPage upgrade. The patch is idempotent, so repeated runs are safe.</p>
        </div>

        <div class="highlight-box">
            <p><strong>⚠️ arena.ai note:</strong> even with stealth mode enabled and network monitoring disabled, repeated chatting can still trigger Cloudflare after roughly ten messages in half an hour. That is the site's own policy rather than a script bug. In practice, lowering frequency or combining this with proxy rotation works better.</p>
        </div>
    `;

    translations.sections['commands'] = `
        <h2>⚡ Automation Commands</h2>
        <p>The command system lets you define <strong>triggers</strong> and <strong>actions</strong> so tabs can recover automatically, switch routes, or send alerts.</p>

        <div class="info-box">
            <p><strong>📍 Where to configure it:</strong> open the <a href="/">dashboard</a> → <strong>Commands</strong>.</p>
        </div>

        <div class="highlight-box">
            <p><strong>⚠️ Built-in commands:</strong> the project ships with several predefined commands, most of them disabled by default. Many of them are highly tailored to Gemini and Arena workflows. If you do not understand what a command does yet, leave it disabled.</p>
        </div>

        <h3>Two modes</h3>
        <table>
            <tr><th>Mode</th><th>Description</th><th>Best for</th></tr>
            <tr><td><strong>Simple mode</strong></td><td>Pick a trigger and configure an action chain</td><td>Common automation needs</td></tr>
            <tr><td><strong>Advanced mode</strong></td><td>Write JavaScript or Python code directly</td><td>Complex custom logic</td></tr>
        </table>

        <h3>Main trigger types</h3>
        <table>
            <tr><th>Trigger</th><th>Description</th><th>Example</th></tr>
            <tr><td><code>request_count</code></td><td>Total requests hit a threshold</td><td>Run once every 10 turns</td></tr>
            <tr><td><code>error_count</code></td><td>Consecutive errors hit a threshold</td><td>Recover after 3 failures</td></tr>
            <tr><td><code>idle_timeout</code></td><td>A tab stays idle for too long</td><td>Reclaim a tab after 5 minutes</td></tr>
            <tr><td><code>page_check</code></td><td>Specific text appears on the page</td><td>React to Cloudflare prompts</td></tr>
            <tr><td><code>command_triggered</code></td><td>Fire after another command fires</td><td>Chain recovery steps</td></tr>
            <tr><td><code>command_result_match</code></td><td>Match the result of an upstream command</td><td>Branch on <code>CSS_FAILED</code></td></tr>
            <tr><td><code>network_request_error</code></td><td>Intercept bad network status codes by URL rule</td><td>React to <code>429</code> or <code>5xx</code></td></tr>
        </table>

        <div class="note">
            <p><strong>💡 page_check matching:</strong> it checks <code>document.body.innerText</code> first and falls back to <code>document.title</code> only when necessary. As of 2.5.9, you can also use <code>||</code> for OR and <code>&amp;&amp;</code> for AND inside the match text field.</p>
        </div>

        <h3>Scope and priority</h3>
        <ul>
            <li><strong>All tabs</strong>: apply to every tab.</li>
            <li><strong>Specific domain</strong>: apply only to tabs from a domain such as <code>chatgpt.com</code>.</li>
            <li><strong>Specific tab</strong>: apply only to one tab index.</li>
            <li>Each command can set a numeric <code>priority</code>; larger numbers win.</li>
            <li>The request baseline is controlled by <code>CMD_REQUEST_PRIORITY_BASELINE</code> in <code>.env</code>.</li>
        </ul>

        <h3>Built-in actions</h3>
        <table>
            <tr><th>Action</th><th>Description</th></tr>
            <tr><td><code>clear_cookies</code></td><td>Clear cookies for the current tab</td></tr>
            <tr><td><code>refresh_page</code></td><td>Refresh the page</td></tr>
            <tr><td><code>new_chat</code></td><td>Click the new-chat button</td></tr>
            <tr><td><code>run_js</code></td><td>Execute JavaScript in the page</td></tr>
            <tr><td><code>wait</code></td><td>Wait for a number of seconds</td></tr>
            <tr><td><code>execute_preset</code></td><td>Switch to a preset</td></tr>
            <tr><td><code>execute_workflow</code></td><td>Run a preset workflow immediately</td></tr>
            <tr><td><code>navigate</code></td><td>Open a target URL</td></tr>
            <tr><td><code>switch_proxy</code></td><td>Switch proxy node through Clash</td></tr>
            <tr><td><code>send_webhook</code></td><td>Send an external alert</td></tr>
            <tr><td><code>execute_command_group</code></td><td>Run a grouped command set in sequence</td></tr>
            <tr><td><code>abort_task</code></td><td>Stop the current task and optionally stop following actions</td></tr>
        </table>

        <h3>Branching and alerts</h3>
        <p>You can use <code>command_result_match</code> as a branch controller. It watches the result of another command and triggers only when the expected value is matched.</p>
        <table>
            <tr><th>Field</th><th>Meaning</th><th>Suggested usage</th></tr>
            <tr><td>Observed command</td><td>The upstream command to watch</td><td>Choose one with stable outputs</td></tr>
            <tr><td>Action ref</td><td>Optional specific step inside that command</td><td>Useful for JS probe steps</td></tr>
            <tr><td>Rule</td><td><code>equals</code>, <code>contains</code>, or <code>not_equals</code></td><td>Prefer <code>equals</code> for status strings</td></tr>
            <tr><td>Expected value</td><td>The result you want to match</td><td>Examples: <code>SUCCESS</code>, <code>CSS_FAILED</code></td></tr>
        </table>
        <p><code>send_webhook</code> supports alert templates such as <code>{{tab_index}}</code>, <code>{{domain}}</code>, <code>{{network_status}}</code>, and <code>{{network_url}}</code>.</p>
        <pre><code>{"msg":"Tab #{{tab_index}} on {{domain}} hit {{network_status}}, URL={{network_url}}"}</code></pre>

        <h3 id="proxy-switching">Proxy switching</h3>
        <div class="note">
            <p><strong>Prerequisite:</strong> Clash must be running with External Controller enabled, for example:</p>
            <pre><code>external-controller: 127.0.0.1:9090
secret: ""</code></pre>
        </div>
        <table>
            <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
            <tr><td>Clash API URL</td><td><code>http://127.0.0.1:9090</code></td><td>External Controller address</td></tr>
            <tr><td>Proxy group</td><td><code>Proxy</code></td><td>Proxy group name in Clash</td></tr>
            <tr><td>Switch mode</td><td>Random</td><td>Random, round-robin, or fixed node</td></tr>
            <tr><td>Refresh after switching</td><td>Enabled</td><td>Refresh the page after the node changes</td></tr>
        </table>

        <h3>Typical patterns</h3>
        <div class="config-box">
            <ul style="margin-bottom: 0;">
                <li><strong>Every 10 requests:</strong> clear cookies → refresh page.</li>
                <li><strong>Cloudflare detected:</strong> switch proxy → wait 2 s → refresh page.</li>
                <li><strong>429 or 5xx:</strong> switch proxy → wait 1 s → refresh page → optional webhook.</li>
                <li><strong>Probe fails:</strong> match <code>CSS_FAILED</code> → clear cookies → refresh → execute a recovery preset.</li>
            </ul>
        </div>

        <h3>Advanced mode</h3>
        <pre><code>// JavaScript example
document.cookie.split(";").forEach(c => {
    document.cookie = c.trim().split("=")[0] +
        "=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/";
});
location.reload();</code></pre>
        <pre><code># Python example
logger.info(f"Current URL: {tab.url}")
if session.error_count > 2:
    tab.run_js("location.reload()")
    logger.info("Page refreshed after repeated errors")</code></pre>

        <div class="highlight-box">
            <p><strong>⚠️ Safety:</strong> advanced mode can execute arbitrary code. Python scripts run on the backend and have full system access, so only use trusted code.</p>
        </div>
    `;

    translations.sections['ai-recognition'] = `
        <h2>🎯 AI Recognition (New Site Adaptation)</h2>
        <p>For sites outside the supported list, the system can call a helper AI to analyze the page and identify key elements such as the input box, send button, and reply container.</p>

        <div class="info-box">
            <p><strong>📍 Where to configure it:</strong> open the <a href="/">dashboard</a> → Settings → <strong>AI Recognition</strong>.</p>
        </div>

        <h3>When you need it</h3>
        <ul>
            <li><strong>Needed</strong>: when you are trying to use a site that has not been adapted yet.</li>
            <li><strong>Not needed</strong>: when you only use already supported sites.</li>
        </ul>

        <h3>How it works</h3>
        <p>The flow is simple: open an unknown site -> send the first real request -> the system asks a helper AI to analyze the page -> the generated selectors are written into the site config.</p>

        <h3>Default recognition targets</h3>
        <table>
            <tr><th>Key</th><th>Description</th><th>Required</th></tr>
            <tr><td><code>input_box</code></td><td>User input field</td><td>✅</td></tr>
            <tr><td><code>send_btn</code></td><td>Send button</td><td>✅</td></tr>
            <tr><td><code>result_container</code></td><td>Container that holds AI output only</td><td>✅</td></tr>
            <tr><td><code>new_chat_btn</code></td><td>Button that starts a new chat</td><td>❌</td></tr>
            <tr><td><code>message_wrapper</code></td><td>Outer container for one full message</td><td>❌</td></tr>
            <tr><td><code>generating_indicator</code></td><td>Visible indicator shown while generating</td><td>❌</td></tr>
        </table>

        <h3>Fields</h3>
        <table>
            <tr><th>Field</th><th>Description</th></tr>
            <tr><td><strong>Order</strong></td><td>Controls recognition priority</td></tr>
            <tr><td><strong>Key</strong></td><td>The element identifier to search for, such as <code>input_box</code> or <code>send_btn</code></td></tr>
            <tr><td><strong>Description</strong></td><td>Plain-language guidance that helps the AI find the element accurately</td></tr>
            <tr><td><strong>Enabled</strong></td><td>Whether the recognition item is active</td></tr>
            <tr><td><strong>Actions</strong></td><td>Edit or remove the item</td></tr>
        </table>

        <h3>Automatic flow</h3>
        <ol>
            <li>Configure your own OpenAI-style helper API in the dashboard.</li>
            <li>Open the unsupported site and stay on the real chat page.</li>
            <li>Send the first real API request to that unknown domain.</li>
            <li>The system spends about <strong>8000 tokens</strong> to analyze the page and writes the generated config into <code>config/sites.json</code>.</li>
        </ol>

        <div class="note">
            <p><strong>💡 Tip:</strong> if you only use already supported sites, you can ignore this feature completely.</p>
        </div>

        <div class="note">
            <p><strong>💡 Manual flow:</strong> if you prefer not to configure a helper API, you can still let the project generate an empty site shell, then fill selectors manually with the selector tester or workflow visualizer.</p>
        </div>
    `;

    translations.sections['env-config'] = `
        <h2>⚙️ Environment Settings</h2>
        <p>Edit these in <strong>Dashboard → Settings → Environment</strong>. Changes here <strong>require a restart</strong>.</p>

        <div class="config-group">
            <h4><span class="icon">🖥️</span> Service</h4>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>Listen host</td><td><code>127.0.0.1</code></td><td>Use <code>0.0.0.0</code> to allow external access</td></tr>
                <tr><td>Listen port</td><td><code>8199</code></td><td>HTTP service port</td></tr>
                <tr><td>Debug mode</td><td>On</td><td>Enables <code>/docs</code> and detailed error output</td></tr>
                <tr><td>Log level</td><td><code>INFO</code></td><td><code>DEBUG</code>, <code>INFO</code>, <code>WARNING</code>, or <code>ERROR</code></td></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">🔐</span> Authentication</h4>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>Enable auth</td><td>Off</td><td>Require a Bearer token for API calls</td></tr>
                <tr><td>Bearer token</td><td>Empty</td><td>Must be set when auth is enabled</td></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">🌐</span> CORS</h4>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>Enable CORS</td><td>On</td><td>Allow cross-origin requests or not</td></tr>
                <tr><td>Allowed origins</td><td><code>*</code></td><td>Comma-separated list, or <code>*</code> for all origins</td></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">🔀</span> Proxy</h4>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>Enable proxy</td><td>Off</td><td>Route browser traffic through a proxy</td></tr>
                <tr><td>Proxy URL</td><td><code>socks5://127.0.0.1:1080</code></td><td>Supports <code>socks5://</code> and <code>http://</code></td></tr>
                <tr><td>Bypass list</td><td><code>localhost,127.0.0.1</code></td><td>Addresses that should skip the proxy</td></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">🌍</span> Browser</h4>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">🧳</span> Reuse your own browser profile</h4>
            <p>If you want the script to inherit your existing login state, cookies, and extensions, point it to a <strong>copied user-data directory</strong> rather than your live system Chrome profile.</p>

            <div class="highlight-box">
                <p><strong>⚠️ Important:</strong> starting from <strong>Chrome 136</strong>, Chrome tightened remote-debugging behavior. If you point <code>BROWSER_PROFILE_DIR</code> to the live system <code>User Data</code> folder, Chrome may open while the debugging port never becomes usable.</p>
            </div>

            <h5>Recommended approach</h5>
            <ol>
                <li>Close every Chrome window completely.</li>
                <li>Create a dedicated directory such as <code>C:\\Users\\YourName\\AppData\\Local\\UniversalWebApiProfile</code>.</li>
                <li>Copy at least <code>Local State</code> and the profile folder you want into that new directory.</li>
                <li>Point the script to that copy in <code>.env</code> and restart <code>start.bat</code>.</li>
            </ol>

            <pre><code>BROWSER_PATH=C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe
BROWSER_PROFILE_DIR=C:\\Users\\YourName\\AppData\\Local\\UniversalWebApiProfile
BROWSER_PROFILE_NAME=Default</code></pre>

            <div class="note">
                <p><strong>Field meaning:</strong></p>
                <ul>
                    <li><code>BROWSER_PATH</code>: browser executable path.</li>
                    <li><code>BROWSER_PROFILE_DIR</code>: the root user-data directory.</li>
                    <li><code>BROWSER_PROFILE_NAME</code>: a profile name such as <code>Default</code> or <code>Profile 1</code>.</li>
                </ul>
            </div>

            <div class="highlight-box">
                <p><strong>Common mistake:</strong> do <strong>not</strong> set <code>BROWSER_PROFILE_DIR</code> to <code>...\\User Data\\Default</code>. The directory should point to the root, while the exact profile name belongs in <code>BROWSER_PROFILE_NAME</code>.</p>
            </div>

            <div class="info-box">
                <p><strong>If you do not need your own login state:</strong> the simplest option is to leave <code>BROWSER_PROFILE_DIR</code> empty and keep using the project's built-in <code>chrome_profile/</code> directory.</p>
            </div>
        </div>

        <div class="config-group">
            <h4><span class="icon">🤖</span> Helper AI</h4>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>API Key</td><td>Empty</td><td>API key for the helper AI</td></tr>
                <tr><td>Base URL</td><td><code>http://127.0.0.1:5104/v1</code></td><td>Base URL of the helper API</td></tr>
                <tr><td>Model</td><td><code>gemini-3.0-pro</code></td><td>Model used for page analysis</td></tr>
                <tr><td>Max HTML length</td><td><code>120000</code></td><td>Longer HTML is truncated to save tokens</td></tr>
            </table>
        </div>
    `;

    translations.sections['browser-config'] = `
        <h2>🌐 Browser Constants</h2>
        <p>Edit these in <strong>Dashboard → Settings → Browser Constants</strong>. Changes here take effect <strong>immediately</strong>.</p>

        <div class="config-group">
            <h4><span class="icon">🔌</span> Connection</h4>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>Debug port</td><td><code>9222</code></td><td>Chrome DevTools remote debugging port</td></tr>
                <tr><td>Connection timeout</td><td><code>10</code> s</td><td>Timeout for connecting to the browser</td></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">⏱️</span> Interaction delays</h4>
            <p style="color: var(--desc-color); font-size: 0.9rem; margin-bottom: 10px;">Randomized delays that help the automation behave more like a human user</p>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>Stealth delay min</td><td><code>0.1</code> s</td><td>Minimum stealth-mode delay</td></tr>
                <tr><td>Stealth delay max</td><td><code>0.3</code> s</td><td>Maximum stealth-mode delay</td></tr>
                <tr><td>Action delay min</td><td><code>0.15</code> s</td><td>Minimum delay for clicks and inputs</td></tr>
                <tr><td>Action delay max</td><td><code>0.3</code> s</td><td>Maximum delay for clicks and inputs</td></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">🔍</span> Element lookup</h4>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>Default wait</td><td><code>3</code> s</td><td>Default timeout when searching for an element</td></tr>
                <tr><td>Fallback wait</td><td><code>1</code> s</td><td>Retry timeout after the first failure</td></tr>
                <tr><td>Cache lifetime</td><td><code>5</code> s</td><td>How long cached element positions stay valid</td></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">🪄</span> Log presentation</h4>
            <p style="color: var(--desc-color); font-size: 0.9rem; margin-bottom: 10px;">These switches only change how logs read in the dashboard. They do not change runtime behavior, and the original log text is still preserved for troubleshooting.</p>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>INFO cute mode</td><td>Off</td><td>Polishes common INFO logs into friendlier, easier-to-scan wording.</td></tr>
                <tr><td>DEBUG cute mode</td><td>Off</td><td>Polishes the main DEBUG paths into more readable hints, such as “Xiaolu starts splitting the text now”; useful when you stare at logs for a long time.</td></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">📡</span> Response detection</h4>
            <p style="color: var(--desc-color); font-size: 0.9rem; margin-bottom: 10px;">Controls polling frequency and timeout thresholds while waiting for replies</p>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>Check interval min</td><td><code>0.1</code> s</td><td>Fastest polling interval</td></tr>
                <tr><td>Check interval max</td><td><code>1.0</code> s</td><td>Slowest polling interval</td></tr>
                <tr><td>Default interval</td><td><code>0.3</code> s</td><td>Initial polling interval</td></tr>
                <tr><td><strong>Silence timeout</strong></td><td><code>8.0</code> s</td><td>Main finish condition when content stops changing</td></tr>
                <tr><td>Silence fallback</td><td><code>12</code> s</td><td>Extra patience for slower models</td></tr>
                <tr><td>Max timeout</td><td><code>600</code> s</td><td>Absolute timeout for one request</td></tr>
                <tr><td>Initial wait</td><td><code>180</code> s</td><td>Max wait before any response appears</td></tr>
                <tr><td><strong>Stable checks</strong></td><td><code>8</code></td><td>How many unchanged checks are needed to finish</td></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">⚙️</span> Advanced response detection</h4>
            <p style="color: var(--desc-color); font-size: 0.9rem; margin-bottom: 10px;">Usually you do not need to change these values</p>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>Re-render wait</td><td><code>0.5</code> s</td><td>Extra wait after page re-rendering</td></tr>
                <tr><td>Shrink tolerance</td><td><code>3</code></td><td>Allowed number of content-shrink events</td></tr>
                <tr><td>Minimum valid length</td><td><code>10</code></td><td>Shortest response considered valid</td></tr>
                <tr><td>Initial element wait</td><td><code>10</code> s</td><td>How long to wait for the response element to appear</td></tr>
                <tr><td>Max anomaly count</td><td><code>5</code></td><td>Abort after too many anomalies</td></tr>
                <tr><td>Max missing-element count</td><td><code>10</code></td><td>Abort after repeated missing-element checks</td></tr>
            </table>
        </div>

        <div class="config-group">
            <h4><span class="icon">🧪</span> Parser debug capture</h4>
            <p style="color: var(--desc-color); font-size: 0.9rem; margin-bottom: 10px;">Use these when you are developing or fixing a network parser. Captured files are written to <code>logs/network_parser_debug/</code>.</p>
            <table>
                <tr><th>Setting</th><th>Default</th><th>Description</th></tr>
                <tr><td>Enable response debug capture</td><td>Off</td><td>When a network parser runs, save the raw body, request metadata, and parser debug summary for later inspection.</td></tr>
                <tr><td>Max body chars</td><td><code>200000</code></td><td>Maximum raw response length to save per capture file. Raise this if the site streams long replies and the useful tail gets cut off.</td></tr>
                <tr><td>Parser filter</td><td>Empty</td><td>Leave blank to capture all parsers, or set a specific parser ID such as <code>deepseek</code> to reduce noise.</td></tr>
            </table>
        </div>

        <div class="note">
            <p><strong>💡 Recommended usage:</strong> when adapting a new site, enable parser debug capture first and reproduce one real reply. If no useful dump appears, then switch to the manual listener script from the tutorial's parser guide.</p>
        </div>
    `;

    translations.sections['config-manage'] = `
        <h2>💾 Config Management</h2>
        <ul>
            <li><strong>Save</strong>: click the <strong>Save</strong> button after editing the config in the dashboard.</li>
            <li><strong>Hot reload</strong>: most config changes take effect immediately, except environment settings.</li>
            <li><strong>Import / export</strong>: you can export the full config or a single site as JSON and import it again later.</li>
            <li><strong>Preset management</strong>: each site's presets can be created, deleted, and switched independently.</li>
            <li><strong>Backups</strong>: it is a good habit to back up the <code>config/</code> directory regularly.</li>
        </ul>

        <div class="config-group">
            <h4><span class="icon">🛠️</span> Update whitelist</h4>
            <p>Edit this in <strong>Dashboard → Settings → Update Whitelist</strong>. After you save it, the settings are written into <code>config/update_settings.json</code> and take effect during the <strong>next automatic update</strong>.</p>
            <ul>
                <li><strong>Purpose</strong>: it controls what should stay untouched during updates, not a runtime on/off switch.</li>
                <li><strong>Directories</strong>: if you whitelist directories such as <code>config/</code>, <code>app/</code>, or <code>static/</code>, the whole directory is preserved.</li>
                <li><strong>Merge behavior</strong>: when <code>config/sites.json</code> and <code>config/commands.json</code> are not whitelisted, the updater tries to merge release updates while preserving local changes.</li>
                <li><strong>Internal preserve</strong>: <code>config/update_settings.json</code> itself is always preserved automatically.</li>
            </ul>
        </div>

        <div class="note">
            <p><strong>Default preserved items:</strong></p>
            <ul>
                <li><code>config/sites.local.json</code></li>
                <li><code>config/commands.local.json</code></li>
                <li><code>chrome_profile/</code></li>
                <li><code>venv/</code></li>
                <li><code>logs/</code></li>
                <li><code>image/</code></li>
                <li><code>updater.py</code></li>
                <li><code>.git/</code></li>
                <li><code>__pycache__/</code></li>
                <li><code>*.pyc</code></li>
                <li><code>backup_*/</code></li>
            </ul>
            <p style="margin-bottom: 0;">If all you want is to preserve login state and your own configuration, the default selection is usually enough.</p>
        </div>

        <div class="highlight-box">
            <p><strong>⚠️ Warning:</strong> the visual editor currently has <strong>no undo feature</strong>. After saving, you cannot quickly revert changes unless you already have a backup.</p>
        </div>
    `;

    translations.sections['faq'] = `
        <h2>❓ FAQ</h2>

        <h3>Q1: Why does SillyTavern say the connection failed?</h3>
        <p>A: The built-in API test often sends a request that is too short. This is especially common with AI Studio. In practice, it is better to send a real chat message instead of using the test button.</p>

        <h3>Q2: What should I check if failures happen frequently?</h3>
        <div class="troubleshoot-list">
            <ol>
                <li><strong>Manual interference:</strong> do not click or switch things inside the controlled browser while the workflow is running.</li>
                <li><strong>Collapsed UI or layout changes:</strong> if elements are hidden or folded, selectors may stop working.</li>
                <li><strong>Website-side issues:</strong> captcha, blocked content, or messages that are too long can all cause failures.</li>
                <li><strong>Network instability:</strong> proxy changes or unstable networking can cause deadlocks or partial sends.</li>
                <li><strong>Extra unrelated tabs:</strong> when the script is about to run, the controlled browser should contain nothing except the target site.</li>
                <li><strong>Outdated site config:</strong> website UI changes can invalidate selectors.</li>
            </ol>
        </div>

        <h3>Q3: How does the script behave when I open multiple pages?</h3>
        <ul>
            <li><strong>Default route</strong> (<code>/v1/chat/completions</code>): uses one idle tab automatically.</li>
            <li><strong>Fixed domain route</strong> (<code>/url/gemini.com/v1/chat/completions</code>): matches one tab from that site.</li>
            <li><strong>Fixed tab route</strong> (<code>/tab/1/v1/chat/completions</code>): always uses tab #1.</li>
            <li>You can assign different presets to different tabs.</li>
            <li>If all tabs are busy, new requests wait in line.</li>
        </ul>

        <h3>Q4: Why is a newly opened tab missing from the tab pool?</h3>
        <ul>
            <li>Make sure the page fully loaded.</li>
            <li>Wait 2 to 3 seconds and click refresh.</li>
            <li>If it still does not appear, restart the script.</li>
        </ul>

        <h3>Q5: Do I need to tune dashboard parameters?</h3>
        <p>A: Usually not. Most defaults are already tuned for common usage.</p>

        <h3>Q6: What are the known issues?</h3>
        <ul>
            <li>The VSCode <strong>Codex</strong> plugin is currently incompatible.</li>
            <li>The DrissionPage patch must be re-applied after every upgrade.</li>
            <li><strong>Independent Cookie tabs</strong> are still an advanced experimental feature. The tutorial does not walk through that workflow yet. In particular, trying to force truly isolated cookies inside the same controlled browser window is not reliable right now, so the shared-cookie path is still the recommended default.</li>
        </ul>

        <h3>Q7: Why is proxy switching not working?</h3>
        <div class="troubleshoot-list">
            <ol>
                <li>Make sure Clash is running and External Controller is enabled.</li>
                <li>Check whether the API URL is correct.</li>
                <li>Confirm the proxy-group name by visiting <code>http://127.0.0.1:9090/proxies</code>.</li>
                <li>If Clash uses a secret, fill it in inside the command config.</li>
                <li>Check <code>[CMD]</code> logs in the dashboard for exact failures.</li>
            </ol>
        </div>

        <h3>Q8: Why does reusing my own Chrome login state still fail?</h3>
        <ul>
            <li><strong>You pointed directly at the live system <code>User Data</code> directory</strong>, which often breaks remote debugging on Chrome 136+.</li>
            <li><strong><code>BROWSER_PROFILE_DIR</code> points to the wrong level</strong>; it should point to the root user-data directory, not <code>...\\User Data\\Default</code>.</li>
            <li><strong>You forgot to restart the startup script</strong> after changing browser settings.</li>
        </ul>

        <h3>Q9: What is this project useful for?</h3>
        <ul>
            <li>Turning free web AI access into an OpenAI-style API</li>
            <li>Inspecting how websites build context</li>
            <li>Running multiple tabs and presets in parallel</li>
            <li>Bypassing some long-input limits through file attach</li>
        </ul>

        <div class="info-box">
            <p><strong>📬 Feedback channels:</strong></p>
            <ul style="margin-bottom: 0;">
                <li>GitHub Issues for bug reports and suggestions</li>
                <li>QQ group: <strong>1073037753</strong></li>
            </ul>
        </div>
    `;

    translations.sections['author-note'] = `
        <h2>⚠️ Notes From the Author</h2>

        <div class="config-group">
            <h4><span class="icon">1️⃣</span> Function calling is still fragile</h4>
            <p>Function calling still has plenty of rough edges and depends heavily on the model's own ability to understand instructions. Treat it as a feature that needs testing, not as something you can trust blindly.</p>
        </div>

        <div class="config-group">
            <h4><span class="icon">2️⃣</span> Gemini is the main maintenance target</h4>
            <p>The author mainly uses <strong>Gemini</strong>, so other sites may not receive timely updates. For problems, the QQ group is a better feedback channel than GitHub Issues or Discord.</p>
        </div>

        <div class="config-group">
            <h4><span class="icon">3️⃣</span> Ask AI first when something breaks</h4>
            <p>Many issues do not require waiting for the author. Selector failures, wrong configs, and broken workflow order are often faster to diagnose by giving logs and element trees to another AI first.</p>
        </div>

        <div class="config-group">
            <h4><span class="icon">4️⃣</span> Suspect selectors before parsers</h4>
            <p>When a site suddenly stops working, parser bugs are possible, but selector failures are more common.</p>
            <div class="note">
                <p><strong>Updating selectors is usually simple:</strong></p>
                <ol>
                    <li>Inspect the target element in the browser.</li>
                    <li>Capture or copy the element tree.</li>
                    <li>Ask an AI to suggest a new selector based on that structure.</li>
                </ol>
            </div>
            <div class="note">
                <p><strong>Parser issues are harder:</strong> they often require network interception, response analysis, and reverse engineering.</p>
            </div>
            <div class="info-box">
                <p><strong>If you can fix it yourself, please do:</strong> if you already solved the problem, sending the updated config or code back to the author is very helpful.</p>
            </div>
        </div>

        <div class="config-group">
            <h4><span class="icon">5️⃣</span> Clear bug reports are much easier to act on</h4>
            <p>If you report a bug, describe the issue clearly and attach <strong>DEBUG-level logs</strong> whenever possible. If wording is difficult, ask another AI to help organize the report first.</p>
        </div>

        <div class="config-group">
            <h4><span class="icon">6️⃣</span> The tutorial is broad, not always deep</h4>
            <p>This tutorial tries to cover the major features, but some details are still brief. Contributions to improve the docs are welcome.</p>
        </div>

        <div class="config-group">
            <h4><span class="icon">7️⃣</span> Many features were built for RP-style usage</h4>
            <p>A lot of this project's design comes from roleplay-oriented use cases. Features outside that core workflow may exist in a more lightly maintained state.</p>
        </div>
    `;

    window.TUTORIAL_I18N_EN = translations;
})();
