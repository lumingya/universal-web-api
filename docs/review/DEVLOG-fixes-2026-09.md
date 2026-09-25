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
- [ ] S12 默认开启的响应调试抓取

### 第二批

- [ ] B8 命令配置损坏时清空运行缓存
- [ ] B5 解冻失败仍交付标签页
- [ ] B2 旧版顶层数组历史恢复丢失
- [ ] B1 搜索引擎主域被自动发现
- [ ] B3 Responses 内存历史无字节预算
- [ ] S10 图片比对 / C2PA 直取外部 URL
- [ ] S11 DNS 校验后连接重解析
- [ ] S4 回环 IP 当作授权
- [ ] S5 定时重启代理容量 / 协议
- [ ] S6 媒体路由无认证与转码资源
- [ ] S3 解析器安装立即 import
- [ ] H9 标签页等待队列无总量上限
- [ ] H12 网络事件 URL 正则回溯

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
