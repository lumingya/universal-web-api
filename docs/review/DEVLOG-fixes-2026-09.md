# 审查问题修复开发日志（2026-09-25 起）

> 用途：记录「按 `docs/review/CODE_REVIEW_REPORT.md` 修复 Bug」阶段的进度与关键决策，
> 防止上下文压缩或沙盒重启后丢失信息。上一阶段（只审查不改代码）的日志见
> `docs/review/DEVLOG-code-review-2026-09.md`。

## 0. 恢复指引（上下文丢失 / 沙盒重启后先看这里）

1. `cd /home/user/repo && git log --oneline -5` 确认最新提交点（远端 `origin/main`）。
2. 读本文件「3. 进度清单」，从第一个未勾选项继续。**不要**重新通读全仓库。
3. 推送凭据只存在于 `.git/config`（clone URL 内嵌临时 PAT）。沙盒快照**不含** `.git/config`，
   重启后需用会话里给出的临时 token 重新 `git remote set-url`。
   **严禁**把 token 写进任何被跟踪的文件、日志或提交信息。
4. 沙盒硬性约束（用户规则）：
   - 每完成一小项立刻 `git add` → `git commit` → `git push origin main`，不堆积改动；
   - 禁止安装 Playwright/Chromium、禁止重度浏览器 E2E、不要往工作区留 `.png` 截图；
   - 临时文件用完即删；工作区约 128MB / 10k 文件配额。
5. 本仓库没有用户规则里提到的 `js/build.js` / `js/tests.js`。等效轻量验证：
   - `bash /tmp/runtests.sh`（见下方「2. 测试基线」，若 `/tmp` 被清空按该节重建）
   - `node --check static/js/<file>.js`（前端语法）
   - `python3 -m compileall -q app main.py start.py`（Python 语法）

## 1. 本轮范围

用户确认：**第一批（P1）+ 第二批（P2）**，直接提交到 `main`。第三批 P3 本轮不做。

- 第一批（阻断凭据与同源脚本风险）：S13、S1、H1、S8、S9、S12
- 第二批（确定性功能错误）：B8、B5、B2、B1、B3，以及 P2 其余条件性风险项
  S10、S11、S4、S5、S6、S3、H9、H12、T1

## 2. 测试基线（沙盒内可复现）

依赖安装：`pip install -r requirements.txt` + `pytest pytest-asyncio httpx`
（**不装** playwright；`requirements-dev.txt` 里的 playwright 与 T2 缺失的 httpx 属 P3，不在本轮）。

跑测脚本（10 个需要真实浏览器/Playwright 的模块被排除）：

```bash
cat > /tmp/runtests.sh <<'EOF'
#!/bin/bash
cd /home/user/repo
timeout 1200 python3 -m pytest tests -q -p no:cacheprovider \
  --ignore=tests/browser_operations_studio.py \
  --ignore=tests/browser_preset_transfer.py \
  --ignore=tests/browser_site_capabilities.py \
  --ignore=tests/browser_upgraded_sites.py \
  --ignore=tests/browser_workflow_studio.py \
  --ignore=tests/test_attachment_browser.py \
  --ignore=tests/test_site_workflow_upgrade.py \
  --ignore=tests/test_text_input_atomic.py \
  --ignore=tests/test_workflow_page_guide.py \
  --ignore=tests/test_workflow_readable_ui.py "$@"
EOF
chmod +x /tmp/runtests.sh
```

**修复前基线（commit `5f724c5`）：`524 passed, 73 failed`。**
**B1 修复后基线更新为 64 failed**（9 个站点发现断言已转绿），
`/tmp/baseline_failures.txt` 已同步刷新。
73 个失败的构成（与审查报告一致，不是本轮引入的回归）：

- 64 个：依赖未跟踪的本地 fixture（`config/commands*.json`、本地 JS 脚本等）→ 条目 T1
- 9 个：`tests/test_site_discovery.py` 站点自动发现断言 → 条目 B1（本轮要修，修完应变绿）

判定回归的方法：把当前失败集合与 `/tmp/baseline_failures.txt` 做 diff，只允许减少、不允许新增。

```bash
/tmp/runtests.sh 2>&1 | grep '^FAILED' | sed 's/^FAILED //' | sort > /tmp/now.txt
diff /tmp/baseline_failures.txt /tmp/now.txt   # '>' 行 = 新增回归，必须为空
```

## 3. 进度清单

图例：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 已完成并推送

### 第一批

- [x] S13 备份接口泄露 `.env` 密钥 + 前端导出本地令牌
- [x] S1 控制面默认开放（认证默认关 + CORS `*`）
- [x] H1 `.env.example` 诱导不安全部署
- [x] S8 SVG 以同源活动内容落盘/提供
- [x] S9 音视频响应头 + `.html` 后缀落盘
- [x] S12 默认开启的响应调试抓取

### 第二批

- [x] B8 命令配置损坏时清空运行缓存
- [x] B5 解冻失败仍交付标签页
- [x] B2 旧版顶层数组历史恢复丢失
- [x] B1 搜索引擎主域被自动发现
- [x] B3 Responses 内存历史无字节预算
- [x] S10 图片比对 / C2PA 直取外部 URL
- [x] S11 DNS 校验后连接重解析
- [x] S4 回环 IP 当作授权
- [ ] S5 定时重启代理容量 / 协议
- [x] S6 媒体路由无认证与转码资源
- [ ] S3 解析器安装立即 import
- [x] H9 标签页等待队列无总量上限
- [x] H12 网络事件 URL 正则回溯

## 4. 修复记录

### 2026-09-25 · S13 + S1 + H1（默认安全边界与备份泄密）

一次提交处理，因为三者互相依赖：S13 的严重性来自 S1 的默认关闭认证，而 S1 的默认值来自 H1 的模板。

**改动文件**

- `app/core/config_parts/env_config.py`
  - 新增 `parse_bool_literal()`：严格布尔解析，无法识别返回 `None`。
  - 新增 `AppConfig._env_bool_secure()`：安全开关专用，值无法解析时 **fail-closed 返回 True**。
    `is_auth_enabled` / `is_dashboard_auth_enabled` 改用它，于是
    `AUTH_ENABLED=your-secret-here` 不再等价于「关闭认证」。
  - `get_cors_origins()` 默认值由 `["*"]` 改为回环同端口来源
    （面板与 API 同源，本来就不需要 CORS 放行，因此不影响开箱即用）。
  - 新增 `is_loopback_bind()` / `collect_security_config_errors()` / `is_insecure_startup_allowed()`。
  - 新增本地异常 `InsecureStartupConfigError`（**没有**复用 `exceptions.ConfigurationError`，
    避免 `env_config` 反向依赖 `exceptions` 造成导入环）。
- `app/core/config_parts/__init__.py`、`app/core/config.py`：导出上述两个新符号。
- `main.py`：`lifespan` 开头调用新增的 `_enforce_secure_startup_config()`，
  发现不安全组合直接抛 `InsecureStartupConfigError` 拒绝启动；
  仅 `UWA_ALLOW_INSECURE_STARTUP=true` 时降级为告警。
- `app/api/deps.py`：新增 `client_is_loopback()` / `verify_admin_access()` / `verify_admin_auth`。
  规则顺序：跨源 → 403；已配置令牌 → 必须出示；未配置令牌 → 仅本机回环放行（返回 `False`）。
  **返回值语义很重要**：`True` = 出示了真实令牌，`False` = 靠回环放行，调用方据此决定能否输出明文密钥。
- `app/api/system.py`：
  - 新增 `is_secret_env_key()` / `_split_env_secrets()`；
  - `_build_settings_backup_bundle(include_secrets=False)`，响应新增 `contains_secrets`
    与 `env_redacted_keys` 字段；
  - `/api/settings/backup`（GET/POST）与 `/api/settings/env`（GET/POST）全部改用 `verify_admin_auth`。
- `static/js/dashboard-methods.js`：`getDashboardPreferencesBackup()` 不再写入
  `dashboard_token` / `api_token`；导出成功提示会说明剔除了几项密钥。
  `applyDashboardPreferencesBackup()` **保持不变**，旧备份仍可导入。
- `.env.example`：整体重写（原文件把前 25 行重复了两遍）。

**关键决策**

1. 脱敏方式是**整键删除**而不是写 `***` 占位符。因为 `_write_env_config_file()` 只覆盖
   payload 里出现过的键，删除后导入这份备份时目标机器上的真实密钥会原样保留；
   若写占位符反而会把真密钥覆盖成 `***`。
2. 未配置令牌时允许回环访问，而不是硬性 401。否则全新克隆的单机用户连面板都打不开，
   修复会被绕过（直接改回旧版）。回环**不是**凭证，所以它拿不到 `include_secrets=true`。
3. 启动校验放在 `main.py:lifespan` 而非模块导入期，避免 import `main` 的测试/工具被连带拒绝。

**验证**

- 新增 `tests/test_security_hardening_fixes.py`（29 项，全部通过），用 `httpx.ASGITransport`
  进程内请求，密钥全部是 `synthetic-*` 合成值。
- 全量轻量套件：`553 passed, 73 failed`，失败集合与基线**逐行相同**（无回归）。

提交：`c695750`

### 2026-09-25 · S8 + S9（活动内容落盘与同源提供）

两条是同一条链路的两个入口，合并修复。

**新增** `app/utils/safe_media_types.py` —— 单一事实源，含：

- `SAFE_IMAGE/AUDIO_VIDEO_EXTENSIONS`、`ACTIVE_CONTENT_EXTENSIONS`、`ACTIVE_CONTENT_MIME_TYPES`
- `resolve_safe_media_extension(kind, content_type, url)` → 安全扩展名或 `None`（拒绝落盘）
- `looks_like_active_content(head_bytes)` → 内容嗅探，兜住「谎报 Content-Type」
- `resolve_media_delivery(path, mime)` → `(media_type, inline|attachment)`
- `safe_media_response_headers()` → `nosniff` + `default-src 'none'; sandbox` + CORP + no-referrer

**扩展名判定顺序（重要）**

1. Content-Type 是活动内容 → 拒绝
2. Content-Type 在安全白名单 → 用白名单扩展名，**忽略 URL 后缀**；
   且 MIME 类别必须与 kind 一致（防止「image/png 走 video 分支」的新绕过）
3. Content-Type 不认识 **且** URL 后缀是活动内容 → **整体拒绝**（两个线索都不可信）
4. Content-Type 不认识、URL 后缀在同类白名单 → 用它
5. 其余 → 类别默认扩展名 `.png` / `.mp3` / `.mp4`

**改动文件**

- `app/core/browser/media.py`
  - 删掉 3 处各自为政的 `ext_map`（其中图片那份含 `"image/svg+xml": ".svg"`，即 S8 根因）；
  - 前台图片回退：先 `resolve_safe_media_extension("image", ...)`，再对前 1KB 做
    `looks_like_active_content` 复核；
  - 音视频 `_persist_remote_media_urls_to_local`：扩展名不再取自 `urlparse(url).path`
    （S9 根因），并在写入首个 chunk 前做内容嗅探，命中则抛 `unsafe_media_content` 走既有清理分支；
  - `_persist_data_uri_media_to_local`：data URI 的 `image/svg+xml` 同样拒绝。
- `main.py`
  - 新增 `HardenedMediaStaticFiles`（`StaticFiles` 子类，覆写 `file_response`）并用它挂载
    `/download_images`；
  - `/media/{filename}` 返回前走 `resolve_media_delivery` + 加固响应头。
  - **出口加固是必要的**：目录里可能已有旧版本落盘的 `.svg` / `.html`，光堵写入端不够。
- `app/core/background_image_downloader.py`：**未改动**。它本来就拒绝 SVG 且有魔数校验
  （`_detect_safe_image_extension`），属于报告「不要误报」清单。

**验证**

- 新增 `tests/test_safe_media_types.py`（30 项全过）：含 S9 的 `video/custom` + `clip.html`
  复现用例、SVG data URI、历史遗留 `.svg` 经 ASGI 取回必须是
  `application/octet-stream` + `attachment` + `nosniff`。
- 同时验证「正常 mp4 仍能正常落盘为 `/media/xxx.mp4`」，避免过度拦截。
- 全量：`583 passed, 73 failed`，失败集合与基线逐行相同（无回归）。

### 2026-09-25 · S12（默认响应调试抓取泄露聊天内容）

**根因有两层**，缺一不可：

1. `config/browser_config.json` 里跟踪的 `NETWORK_DEBUG_CAPTURE_ENABLED` 是 `true`
   —— 全新克隆开箱即在写响应正文。
2. `NetworkMonitor._is_network_debug_capture_enabled()` 用 `bool(BrowserConstants.get(...))`
   判断。`bool("false") is True`，所以**把它配成字符串 `"false"` 反而是打开**。
   用户以为关掉了，实际没有。

**改动文件**

- `config/browser_config.json`：`NETWORK_DEBUG_CAPTURE_ENABLED` → `false`；
  新增 `NETWORK_DEBUG_CAPTURE_RETENTION_HOURS: 24`。
- `app/core/network_monitor.py`：
  - `_is_network_debug_capture_enabled()` 改用 `_browser_constant_bool(key, False)`（严格布尔、默认关）；
  - 新增 `_warn_network_debug_capture_once()`：开启时打一次醒目 WARNING，说明写什么、留多久；
  - 新增 `get_network_debug_capture_retention_hours()` 与
    `purge_expired_network_parser_debug_files()`——原先**只有**总量上限（50MB），没有保留期，
    旧版本默认开启留下的聊天正文会长期躺在磁盘上。
- `main.py` lifespan：启动清理时先 `purge_expired_...` 再 `trim_...`。
- `app/api/system.py`：`DEFAULT_BROWSER_CONSTANTS` 补 `NETWORK_DEBUG_CAPTURE_RETENTION_HOURS`，
  让面板能配置它（否则前端改不了）。

**未改动**：脱敏器 `sanitize_sensitive_data` 本身。报告指出它对普通聊天文本无能为力，
但那是「默认不该抓」的问题，不是脱敏器的缺陷；强行扩大脱敏会误伤正常排障。

**验证**：`tests/test_security_hardening_fixes.py` 新增 5 项（含 `"false"` 字符串解析矩阵、
过期快照清理、retention=0 时不清理）。全量 `588 passed, 73 failed`，与基线逐行一致。

### 2026-09-25 · B8（命令配置损坏清空运行缓存）

**根因**：`_read_commands_file()` 在 JSON 解析失败 / 读取异常时 `return []`，
与「文件里确实是空命令列表」完全无法区分。于是 `_refresh_commands_if_changed()`
把缓存换成 0 条、推进 mtime，并据此生成 `run_js_file` 清理动作，
把已经注入在线标签页的预注入脚本一并撤掉——触发条件只是编辑器保存到一半。

**改动** `app/services/command_engine.py`

- 新增 `_read_commands_file_or_none()`：读失败/格式非法返回 `None`；
  `_read_commands_file()` 保留为返回 `[]` 的兼容包装（外部契约不变）。
- 新增实例字段 `_commands_invalid_mtime`：记录已知坏文件的 mtime。
- `_refresh_commands_if_changed()`：
  - 读到 `None` 且已有 last-known-good → 保留缓存、**不推进** `_commands_mtime`、
    不生成清理动作，只 WARNING 一次；
  - 同一份坏文件后续轮询直接跳过重读（靠 `_commands_invalid_mtime` 判断）；
  - 首次加载就失败 → 退化为空列表但同样不推进 mtime，修好后能自动加载；
  - 读成功 → 清空 `_commands_invalid_mtime`。
- `_save_commands()` 成功后同样清空 `_commands_invalid_mtime`。

**注意保留的行为**：**合法的空列表 `{"commands": []}` 仍然生效并照常生成清理动作**。
这是本项修复最容易改坏的地方，已单独加测试锁定。

**未改动**：`app/services/command_engine_storage.py` 里那份同名 `_read_commands_file`
属于未被继承的死代码（报告 H13，P3）。本轮不动它，但要注意**别改错文件**。

**测试限界**：mtime 粒度问题——同一秒内连写两次文件可能 mtime 相同，
热加载根本不会触发。测试用 `_rewrite()` 显式 `os.utime` 推进 mtime，
否则测的是「没触发」而不是「触发后的行为」。
（顺带说明：这也是 B7 在 `script_loader` 上的同类问题，属 P3 未处理。）

**验证**：新增 `tests/test_command_config_hot_reload_safety.py`（8 项全过）。
全量 `596 passed, 73 failed`，与基线逐行一致。

### 2026-09-25 · B5（解冻失败仍交付 BUSY 标签页）

**根因**：`idle_maintenance.resume_if_frozen()` 把
`_uwapi_frozen = False` 放在 `finally` 里无条件执行。CDP 的
`Page.setWebLifecycleState` 抛错后，页面**实际仍是 frozen**，但标志已清除；
`TabSession.acquire()` 忽略返回值，照样返回 True 并把 BUSY 会话交出去，
之后所有 DOM/JS 操作都在一个冻结页面上静默失败。

**改动**

- `app/core/tab_pool_parts/idle_maintenance.py`：`finally` 里只保留
  `_restore_focus_emulation`；成功才清除 `_uwapi_frozen` / `_uwapi_freeze_token`
  并重置失败计数；失败则累加 `_uwapi_resume_failures` 与 `_uwapi_resume_failed_at`，
  保留「待确认冻结」态。
- `app/core/tab_pool_parts/session.py`：
  - `_resume_after_acquire()` 改为返回 bool。
    **判据是 `_uwapi_frozen` 是否已清除，而不是 `resume_if_frozen()` 的返回值**——
    后者返回 False 有「本来就没冻结」和「解冻失败」两种含义，不能直接用。
  - `acquire()` / `acquire_for_command()` 在解冻未确认时调用
    `_rollback_acquire_after_failed_resume()` 并返回 False。
  - 回滚是**直接改状态**而不是走 `release()`：此刻 status 仍是 BUSY，
    没有其他线程能介入，而完整 release 会触发页面清理等与本场景无关的副作用。
  - 连续 `_RESUME_FAILURE_ERROR_THRESHOLD = 3` 次失败 → `mark_error()`，
    交给既有错误恢复流程，避免坏标签页在池子里无限空转。

**边界**：CDP 恢复正常后下一次 `acquire()` 必须能成功并清零计数（已加测试），
否则一次瞬时抖动就会永久废掉一个标签页。

**验证**：新增 `tests/test_tab_resume_failure_safety.py`（8 项全过，用 `_FakeTab` 桩）。
全量 `604 passed, 73 failed`，与基线逐行一致。

### 2026-09-25 · B1 + B2（站点自动发现误判 / 旧版历史丢失）

两项都是小而确定的逻辑错误，合并一次提交。

#### B1 搜索引擎主域被自动发现

难点在于要同时满足这组相互冲突的期望（来自已有测试）：

| 必须拒绝 | 必须允许 |
| --- | --- |
| `google.com` / `www.google.com` / `WWW.GOOGLE.COM.` | `gemini.google.com` |
| `google.co.jp` / `www.google.co.uk` | `aistudio.google.com` |
| `accounts.google.com` | `gemini.com` / `chat.deepseek.com` |
| `bing.com` / `www.baidu.com` | `google.com.attacker.org` |

所以**不能**简单按 host 精确匹配（覆盖不了 ccTLD），也**不能**「google 全域拒绝」
（会误伤 gemini/aistudio）。

`app/utils/site_discovery.py` 的做法：

1. `_search_engine_subdomain_labels(host)`：找到 `_SEARCH_ENGINE_LABELS` 里的标签，
   要求它**后面的标签全是公共后缀样式**（`_is_public_suffix_label`：在短后缀集合里，
   或 ≤3 个字母）。这样 `google.co.jp` 命中，而 `google.com.attacker.org`
   因为 `attacker` 不是后缀标签而不命中 → 按普通域名放行。
2. 返回的是「search 标签之前的子域标签」：`[]` = 主域 → 拒绝；`['www']` → 拒绝；
   其他子域只在命中 `_NON_CHAT_SUBDOMAIN_LABELS`（accounts/login/mail/...）时拒绝。
3. **显式规则优先**：`site_rules.json` / `sites.local.json` 里配了 `auto_discovery`
   就直接用它，启发式只在没有显式配置时兜底。用户仍可手工覆盖。

`config/site_rules.json` 另补了 9 个常见搜索主域的显式 `auto_discovery: false`，
既是文档也便于用户就地修改。

#### B2 旧版顶层数组历史丢失

`app/services/request_manager.py:_load_history()` 原写法：

```python
records = data.get("records", data if isinstance(data, list) else [])
```

Python 会**先求值参数**，`data` 是列表时 `data.get` 立刻抛 `AttributeError`，
兼容分支根本没机会生效，异常被外层 `except Exception` 吞掉 → 历史恢复 0 条。
改成先 `isinstance` 分支判断，并对既非 list 也非 dict 的内容打一条 warning。

**验证**
- `tests/test_site_discovery.py` 36 项全过（基线里 9 个失败全部转绿）。
- 新增 `tests/test_request_history_compat.py`（10 项）：顶层数组、对象格式、
  数组里混入非 dict、token 统计回填、坏 JSON 不抛异常。
- 全量 `623 passed, 64 failed`，无新增失败；基线文件已更新为 64。

### 2026-09-25 · B3（Responses 内存历史无字节预算 / 无主体隔离）

`app/api/chat.py`。原来只有「1024 条 + 1 小时 TTL」两个上限；
而 Responses 默认 `store=true`，一条含 base64 图片的历史就可能几十 MB。
另外状态只按 response id 索引，任何拿到 id 的调用方都能续接他人会话。

**改动**

- 常量新增 `RESPONSES_STATE_MAX_ENTRY_BYTES = 4MiB`、`RESPONSES_STATE_MAX_TOTAL_BYTES = 64MiB`。
- 条目结构由 `(stored_at, serialized)` 扩展为
  `(stored_at, subject_key, serialized_or_None, nbytes)`，并维护模块级
  `_responses_state_total_bytes`。
- 新增 `_drop_responses_state_locked()` 统一做「弹出 + 扣减字节」，
  所有淘汰路径（TTL / 条数 / 总字节 / 覆盖写）都走它，避免计数漂移。
- 新增 `_responses_subject_key(request)`：取 Authorization / X-API-Key 的
  SHA-256 前 32 位；未启用认证时为 `"anonymous"`（保持单机原语义）。
  **只存哈希，不存令牌原文。**
- `_load_responses_state(id, subject_key)`：主体不匹配时返回与「不存在」
  **完全相同**的 404 —— 否则就成了「这个 id 是否存在」的探测预言机。
- 超预算条目存「拒绝墓碑」（`serialized=None`）而不是静默丢弃，
  续接时返回 **413 + 明确说明**，而不是含糊的 404。
- `_store_responses_state` 的 docstring 里写明了 `store` 默认 true 的留存语义。
- 三个调用点（`create_response` / `_stream_responses_compat` / 非流式分支）
  都从 `request` 取主体指纹传入。

**验证**：新增 `tests/test_responses_state_budget.py`（14 项），覆盖主体隔离、
404 一致性、413 墓碑、总字节淘汰、覆盖写不重复计数、TTL 释放字节、
`store:false` 不留存、序列化失败不致命。
全量 `637 passed, 64 failed`，与基线逐行一致。

### 2026-09-25 · S10 + S11（远端抓取：SSRF / 体积 / DNS 重绑定）

两项属于同一条抓取链路，合并处理。

#### S11 DNS 重绑定窗口（`app/utils/remote_resource.py`）

原流程是「先 `resolve_public_addresses()` 校验，再把**主机名**交给 requests」——
HTTP 栈会**重新解析一次**。攻击者控制该域名的 DNS（TTL=0）即可让第二次解析
返回 `127.0.0.1` / `169.254.169.254`，前面的校验完全失效。

不能简单地把 URL 里的域名替换成 IP：那样 SNI 与 Host 头都会坏掉，HTTPS 直接失败。
采用**线程局部 DNS 固定**：

- `_pinned_getaddrinfo` 全局装一次；未设置 pin 的线程原样回落到
  `_original_getaddrinfo`，因此对代码库其余部分零影响。
- `pinned_dns(host, addresses)` 上下文管理器把 pin 写进 `threading.local()`，
  退出时恢复（支持嵌套）。同进程的其他并发请求互不污染。
- `_validate_fetch_remote_target()` 取代原 `_validate_fetch_remote_url()`，
  额外把「校验时解析到的地址」带出来；`get_public_remote_resource()`
  在 `with pinned_dns(...)` 内发起请求。**每一跳重定向都重新校验并重新固定。**

#### S10 参考图抓取无防护（`app/utils/image_validation.py`）

`read_image_bytes()` 里的 URL 来自页面抽取结果（攻击者可控），却直接
`requests.get`：无地址校验（内网 SSRF）、无体积上限、还无条件带当前页面 URL 作 Referer。

改为统一走 `get_public_remote_resource()`（公网校验 + 逐跳重定向校验 + DNS 固定
+ 凭据作用域），并新增 `read_remote_response_bytes()` 按字节预算流式读取：
先看 `Content-Length` 提前拒绝，再在读取过程中兜底（防谎报长度 / chunked 绕过）。
上限 `MAX_REMOTE_IMAGE_BYTES = 24MiB`。

注意：被安全策略拒绝时**直接返回 `b""`，不回落到浏览器上下文再抓一次** ——
否则等于换个执行主体继续打内网。仅在普通网络错误时才回落。

新增 `RemoteResourceTooLargeError`（继承 `UnsafeRemoteResourceError`，
调用方一个 except 就能全覆盖）。

**验证**：新增 `tests/test_remote_fetch_hardening.py`（20 项）。
测试踩坑：`203.0.113.0/24` 是 TEST-NET-3 保留段，`ip.is_global` 为 False，
不能拿来当「公网地址」样例，已改用 `93.184.216.34`。
全量 `657 passed, 64 failed`，无新增失败。

**覆盖确认**：C2PA 那条链路（`app/services/arena_gpt_image_command.py:608/642`）
用的就是 `read_image_bytes`，一并被加固，无需单独改。
`command_engine_actions.py:2313` 的 `requests.get` 打的是本机 Clash 管理 API
（`127.0.0.1:9090`，管理员配置），属预期的内网调用，不纳入本项。

### 2026-09-25 · S4（回环 IP 当作授权）

`app/api/browser_routes.py` 的 `POST /api/browser/open-profile-url`
只检查 `request.client.host` 是否回环、不要求任何令牌。
本机反向代理 / 隧道（nginx `proxy_pass`、frp、ngrok）会把外来请求
以回环地址转交进来，这个检查就完全失效。

**一个重要的取舍**：不能直接套用 S13 的 `verify_admin_access`。
该接口是由第三方 AI 站点页面上的 Link Drawer 用户脚本**跨源**调用的，
`verify_admin_access` 里的同源检查会把正常功能打死。
所以改为针对「回环即授权」这个假设本身加固：

1. **核实代理链** —— `app/api/deps.py` 新增 `request_looks_proxied()` 与
   `FORWARDING_HEADERS`。带任何转发头的请求一律 403：
   我们无法核实这条链，就不能承认它是本机调用。空值头不算（避免误伤）。
2. **强制管理认证** —— 配置了 `DASHBOARD_AUTH_TOKEN` 就必须出示
   （Bearer 或 X-API-Key，`secrets.compare_digest` 比较）。
   逃生阀 `BROWSER_OPEN_URL_ALLOW_UNAUTHENTICATED_LOCAL=true` 给
   无法携带令牌的 Link Drawer 用。未配置令牌时维持原「仅本机」开箱即用行为。
3. **限制目标 URL** —— 这些链接会带着用户既有 Cookie 在**用户自己的浏览器**里打开，
   所以默认拒绝内网 / 本机 / 云元数据目标：`_host_is_internal()` 对 IP 字面量用
   `ipaddress.is_global`，对域名用后缀表（`localhost` / `.local` / `.internal` /
   `.lan` / `.home.arpa`）与元数据主机名集合。**故意不做 DNS 解析**——
   保持判定确定、无额外延迟，也避免又引入一个重绑定面。
   同时拒绝内嵌凭据的 URL（`user:pass@`）。逃生阀
   `BROWSER_OPEN_URL_ALLOW_INTERNAL_TARGETS=true`。

`.env.example` 已补上这两项及其说明。

**验证**：新增 `tests/test_open_profile_url_hardening.py`（40 项）。
测试踩坑：**不能用 starlette 的 `TestClient`** —— 本仓库锁定的 httpx 版本与其不兼容
（`Client.__init__() got an unexpected keyword argument 'app'`），
而且它的默认客户端地址是字符串 `"testclient"`，根本不是回环。
改用 `httpx.ASGITransport(app=app, client=(...))` + `asyncio.run` 的 `_call()` helper。
全量 `697 passed, 64 failed`，无新增失败。

### 2026-09-25 · H12（网络事件 URL 正则可能回溯）

`app/services/command_engine_results.py:_matches_url_rule()` 用标准库 `re.search`
匹配**用户自定义模式**与**页面来源 URL**，两边都不可信，而 `re` 没有执行超时。

工作流侧（`app/core/workflow/flow_runtime.py:304`）其实早就用 `regex` 库的
`timeout=0.025` 做了预算 —— 把同样的策略抽成公共实现：

新建 `app/utils/bounded_regex.py`：
- `bounded_search(pattern, text, *, flags, timeout=0.025)`；
- 模式 > 512 字符 → 直接 `RegexBudgetExceeded`；
- 输入 > 8192 字符 → **截断**而不是拒绝（URL 场景尾部信息价值低，截断更不易误伤）；
- 超时 → `RegexBudgetExceeded`；模式非法 → 原样 `re.error`，回退策略交给调用方；
- `lru_cache` 缓存编译结果；`regex` 库缺失时退化为无超时匹配但仍保留长度上限。

`_matches_url_rule` 的回退顺序：预算超限 → 关键词包含；正则非法 → 通配转义
→ 关键词包含（**保持原有行为不变**）。

**测试踩坑**：`regex` 库对经典的 `^(a+)+$` 有专门优化，用它当样例会「测了个寂寞」
（0.0003s 就返回）。实测 `^(a|aa)+$` 才会真实爆炸并准时触发 25ms 预算。

`flow_runtime.py` 保持原样不动（它的异常类型是 `FlowVariableError`，
改造收益小、回归风险大），仅在新模块 docstring 里交叉引用。

**验证**：新增 `tests/test_bounded_regex.py`（13 项）。
全量 `710 passed, 64 failed`，无新增失败。

### 2026-09-25 · S6（媒体路由无认证 / 转码资源无预算）

新建 `app/utils/media_guard.py`，`main.py` 接线。

#### 访问控制（默认关闭，显式开关）

`/media/{filename}` 与 `/download_images` 原本完全无认证。
**但不能简单地默认加上鉴权**：这些 URL 会直接出现在 OpenAI 兼容响应里，
由浏览器的 `<img src>` / `<audio src>` 加载，而这类标签**无法携带
Authorization 头**。默认开启就会静默打断所有现有前端。

折中：`MEDIA_ACCESS_REQUIRE_AUTH`（默认 `false`）。开启后接受
Authorization / X-API-Key / **`?token=`** 三种方式 —— 查询参数是标签场景
在开启鉴权后还能工作的唯一途径。要求鉴权却没配令牌时 **fail-closed**。
令牌比较用 `hmac.compare_digest` 并按 UTF-8 编码（沿用修复6 的非 ASCII 处理）。

静态目录这一侧没有 `Request` 对象，所以在 `HardenedMediaStaticFiles.get_response()`
里从原始 ASGI `scope` 解析头与 query（`_scope_media_access_allowed`）。

#### 转码资源预算

`TranscodeGuard`：
- **全局并发信号量**（`MEDIA_TRANSCODE_MAX_CONCURRENCY`，默认 2），
  等待超过 `MEDIA_TRANSCODE_QUEUE_TIMEOUT_SEC`（默认 30s）→ 503 + `Retry-After`。
- **同键去重锁**：这是关键。原先同一个文件被并发请求 N 次会拉起 N 个 ffmpeg
  做完全相同的工作；加锁后后到的请求等第一个做完，直接命中磁盘缓存。
  **先抢 key 锁再抢信号量** —— 否则同一文件的重复请求会先把全局预算吃光。
- **源文件体积上限**（`MEDIA_TRANSCODE_MAX_SOURCE_MB`，默认 256）→ 413。
- key 锁用完即从字典移除（`waiters` 计数），不会无界增长。

`.env.example` 已补齐这四项。

**验证**：新增 `tests/test_media_access_guard.py`（15 项），
包含「同键并发峰值必须为 1」与「不同键仍能并行」两条对照断言
（后者防止同键锁把整体吞吐误伤成串行）。
全量 `725 passed, 64 failed`，无新增失败。

### 2026-09-25 · H9（标签页等待队列无总量上限）

`app/core/tab_pool_parts/manager.py`。32 个 acquire 工作线程限制的是**并行线程数**，
不是待执行/等待请求总数；五个入队点（generic / exact_url / index / route / group）
用的都是无界 `deque`，请求可以无限堆积。

**改动**：新增 `_enqueue_waiter(waiters, task_id, queue_label)` 统一接管入队，
替换掉五处 `_next_waiter_token()` + `append()`：

- **两级预算**：总量 `TAB_POOL_MAX_WAITERS`（默认 64）保护整个池；
  单队列 `TAB_POOL_MAX_WAITERS_PER_KEY`（默认 32）避免某个热点标签页/路由
  把总预算吃光饿死其它请求。
- **快速失败而不是继续排队**：排到超时才失败是最坏结果（既占资源又浪费时间），
  超限直接返回 `None`，让上层重试或降级。被拒绝的 token 绝不入队。
- 被拒时若队列为空，顺手把 `setdefault` 新建的空 deque 从字典里删掉，避免泄漏。
- 新增 `waiter_stats()` 暴露容量/拥塞/拒绝计数，供监控与上层决定 429/503。

**踩坑（重要）**：把限额只写在 `__init__` 里会炸 —— `tests/test_tab_route_groups.py`
等用例用 `__new__` 绕过 `__init__` 构造 `TabPoolManager`，实例属性缺失时
`_enqueue_waiter` 直接 `AttributeError`，一次跑挂 8 个既有用例。
改为**类级别默认值** `DEFAULT_MAX_TOTAL_WAITERS` / `DEFAULT_MAX_WAITERS_PER_QUEUE`
（`__init__` 里再按环境变量覆盖成实例属性）后全部恢复。

**范围说明**：报告建议「队列过满明确 429/503」。把这个状态一路透到 HTTP 层需要改动
`connection.py` / `workflow.py` / `command_engine*.py` 等十余个 `acquire*` 调用方
（它们目前统一把 `None` 当作获取失败），回归面远超本批次。
这里先落地容量边界与 `waiter_stats()` 可编程判定接口，HTTP 状态码映射留待后续。

**验证**：新增 `tests/test_tab_pool_waiter_budget.py`（17 项），
含并发入队不突破预算、队列消化后恢复可用、热点队列不饿死其它队列等。
全量 `742 passed, 64 failed`，无新增失败。
