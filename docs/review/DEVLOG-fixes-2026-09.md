# 审查问题修复开发日志（2026-09-25 起）

> 用途：记录「按 `docs/review/CODE_REVIEW_REPORT.md` 修复 Bug」阶段的进度与关键决策，
> 防止上下文压缩或沙盒重启后丢失信息。上一阶段（只审查不改代码）的日志见
> `docs/review/DEVLOG-code-review-2026-09.md`。

## 0. 恢复指引（上下文丢失 / 沙盒重启后先看这里）

> ⚠️ **分支约定（2026-09-25 用户确认）**：发现另一会话在并行往 `main` 推同一批修复（`c8006cd`、`06f8103`…）。
> 用户选择：本会话**只推独立分支 `fix/review-p1-p2-b`**，基于 `d663bb7` 独立完成 P1+P2，**不碰 `main`**，
> 最终由用户对比/择优合并。恢复时：`git checkout fix/review-p1-p2-b && git pull`，推送用
> `git push origin fix/review-p1-p2-b`。下面第 1 步里的 `origin/main` 对本分支不适用。
> 沙盒重启会丢 `.git/config`（含 remote 与 user.name/email）和 `/tmp`，需按第 3 步和第 2 节重建。


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

> 2026-09-25 第二会话复核：重新 clone 后基线同为 `524 passed, 73 failed`，失败清单已存 `/tmp/baseline_failures.txt`。
> 快捷脚本 `/tmp/check.sh` = 跑测 + 与基线 diff（`comm -13`），输出「new failures」段必须为空。
> 丢失时按下文重建：`/tmp/runtests.sh 2>&1 | grep '^FAILED' | sed 's/^FAILED //; s/ - .*//' | sort`（在 `d663bb7` 上跑）。

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

（每完成一项追加：条目 ID、改了哪些文件、做了什么决策、怎么验证的、提交哈希）

### 通用基础设施（多个条目共用）

- 新增 `app/core/http_security.py`：回环判定、Origin 判定、启动期安全检查、备份脱敏、
  「可信本机请求」判定（识别反代/隧道转发头 + start.py handoff 代理共享密钥）。
- 新增回归测试 `tests/test_review_fixes_p1.py`（P1）/ `tests/test_review_fixes_p2.py`（P2）；
  `.gitignore` 用 `!tests/test_review_fixes_*.py` 放行（tests/ 目录默认忽略，只有白名单文件入库）。

### S13 备份泄露密钥 ✅

- `app/core/http_security.py`: `is_secret_env_key` / `redact_env_for_backup`（键名含 TOKEN/SECRET/PASSWORD/API_KEY/COOKIE/
  CREDENTIAL/AUTH_USER/SESSION 等，或值形如 `scheme://user:pass@host` 的一律剔除）。
- `app/api/system.py`: 备份 `files.env` 只含安全子集，新增 `secrets_redacted` / `redacted_env_keys`；
  导入时丢弃 `***` 等占位符，并复用 `_validate_env_config_payload`（此前导入 env 完全不校验，可换行注入）。
  **决策**：秘密「剔除」而非「打码」——导入时缺失键保持目标 `.env` 现值，不会被占位符覆盖。
- `app/api/deps.py`: 新增 `verify_sensitive_admin_auth` —— 面板认证开启时校验令牌；未开启时只允许
  「直接来自回环且无 X-Forwarded-For 等转发头」的请求，否则 403。备份 GET/POST 改用它。
- `static/js/dashboard-methods.js`: 导出备份不再写 `dashboard_token`/`api_token`；导入旧备份仍兼容读取。
- 验证：`tests/test_review_fixes_p1.py` 7 项（无授权远端 403、隧道头 403、启用认证无令牌 401、响应不含合成密钥、
  占位符不覆盖、换行注入 400、前端不导出令牌）。全量：531 passed / 73 failed（与基线相同的 73 项）。

### S1 控制面默认开放 + H1 模板诱导不安全部署 ✅（同一提交，互相依赖）

- `app/core/config_parts/env_config.py`
  - `get_cors_origins()` 默认由 `["*"]` 改为 `[]`（`parse_cors_origins`，空=不放行任何跨源）。
  - 新增 `_env_bool_secure()`：`AUTH_ENABLED`/`DASHBOARD_AUTH_ENABLED` 值非法时 **fail-closed 返回 True**。
- `main.py`
  - CORS 中间件仅在 `CORS_ORIGINS` 非空时挂载；显式 `*` 打告警。
  - 新增 `reject_untrusted_origins` 中间件（最外层）：带 `Origin` 且既非同源（Host / X-Forwarded-Host）
    也不在 `CORS_ORIGINS` 的请求（含预检、`Origin: null`）直接 403。**决策**：CORS 只挡读取，挡不住跨站
    简单 POST，所以服务端必须按 Origin 拒绝；无 Origin 的 curl/SDK 不受影响。
  - `_enforce_secure_startup_config()` 在 `lifespan` 开头和 `__main__` 调用，非回环监听且未同时启用
    API + 面板认证（含令牌）、或认证开关不是合法布尔值 → 抛 `InsecureStartupConfigError` 拒绝启动。
    逃生开关 `ALLOW_INSECURE_PUBLIC_BIND=true`。
- `start.py`：启动器用 `importlib` 按路径加载纯标准库的 `http_security.py`（不导入 app 包），启动前同样检查，
  不通过直接 `return 2`（避免子进程拒绝→启动器 3 秒重启的死循环）；handoff 代理模式下把公开地址通过
  `UWAPI_PUBLIC_BIND_HOST` 传给子进程（子进程自身只绑 127.0.0.1，否则检查会被绕过）。
- `app/api/system.py`：保存 `.env` / 导入备份时复用 `startup_security_errors`，避免保存出一个起不来的配置。
- `.env.example`：删掉重复的前 25 行；`APP_HOST=127.0.0.1`、`APP_DEBUG=false`、`AUTH_ENABLED=false`、
  `DASHBOARD_AUTH_ENABLED=`（沿用）、`CORS_ORIGINS=`、`PROXY_*` 关闭且无个人地址、`HELPER_API_KEY=` 空、
  `CMD_ALLOW_UNSAFE_PYTHON_COMMANDS=false`（与代码默认一致）；删掉代码中完全未使用的 `NGROK_*`（含个人静态域名）。
- `static/js/dashboard-schema.js`：`CORS_ORIGINS` 前端默认值 `*` → `''`（前端默认值会在 .env 缺键时写盘）。
- README.md / README.en.md：同步 `APP_HOST` / `CORS_ORIGINS` 说明。
- 已知影响：`workflow-editor-inject.js` 的跨源直连 fetch 本来就走不通（端口写死 9099 且跨源时
  `shouldPreferBridgeMode()` 恒为真 → 走 CDP 桥），因此 CORS 默认收紧不影响可视化编辑器。
- 验证：p1 测试增至 24 项（Origin 判定矩阵 10 例、主应用跨源 GET/POST/预检均 403 且无 ACAO、占位符 fail-closed、
  启动检查矩阵、lifespan 拒绝含 handoff 场景、启动器可独立加载检查模块、模板无重复键/无示例秘密/可直接通过检查）。
  全量 548 passed / 73 failed（基线同集合）。

### S8 SVG 同源活动内容 + S9 音视频 HTML 落盘 ✅（同一链路，同一提交）

- 新增 `app/utils/media_safety.py`（单一事实源）：图片/音频/视频 MIME→扩展名白名单、
  `choose_media_extension(kind, content_type, url)`（活动 MIME 拒绝 → 映射 → 仅采纳同类白名单 URL 后缀 → 类别默认；
  MIME 大类与 kind 不符也拒绝）、`looks_like_active_content()`（文件头以 `<` 开头 / UTF-16 BOM 即判活动内容）、
  `resolve_media_delivery()`（出口：非白名单扩展名 → `application/octet-stream` + `attachment`）、
  `safe_media_response_headers()`（`nosniff` + `sandbox; default-src 'none'; img-src/media-src 'self'` CSP）。
- `app/core/browser/media.py`：
  - 音视频 `_persist_remote_media_urls_to_local`：扩展名不再取 `urlparse(url).path` 后缀（S9 根因），
    首个 chunk 写盘前嗅探，命中抛 `unsafe_media_content` 走原有清理分支（删半截文件、保留远程链接）。
  - 前台图片回退：删掉 `"image/svg+xml": ".svg"`（S8 根因），`is_active_content_mime` 拦截，
    内容嗅探为标记语言时不落盘、继续走截图回退。
  - data URI：`image/svg+xml`/`text/html` 等 MIME 或内容嗅探命中 → 不落盘（保留原 data_uri）。
- `app/core/extractors/media_extractor.py`：网络音频捕获落盘前同样嗅探。
- `main.py`：`/media/{filename}` 走 `resolve_media_delivery` + 加固头；`/download_images` 改挂
  `HardenedMediaStaticFiles`（覆写 `file_response`）。**出口加固必要**：目录里可能已有旧版落盘的 .svg/.html。
  **决策**：不加 `Cross-Origin-Resource-Policy`——跨站聊天前端（如 localhost:8000）需要 `<img>` 引用这些链接。
- `background_image_downloader.py` 未改（本来就拒绝 SVG 且有魔数校验，属报告「不要误报」项）。
- 验证：p1 测试增至 43 项（白名单矩阵 9 例、嗅探、`video/custom`+`clip.html`+HTML 不落盘且无残留、
  真实 mp4 带 .html 后缀落盘为 .mp4、SVG data URI 与谎报 png 的 SVG 均不落盘、遗留 .svg/.html 经 ASGI
  取回为 octet-stream+attachment+nosniff+sandbox、png 仍 inline）。全量 567 passed / 73 failed（基线同集合）。

### S12 默认响应调试抓取 ✅（第一批完成）

- `config/browser_config.json`：`NETWORK_DEBUG_CAPTURE_ENABLED` 出厂 `true` → `false`；新增 `NETWORK_DEBUG_CAPTURE_RETENTION_HOURS: 24`。
- `app/core/network_monitor.py`：
  - `_is_network_debug_capture_enabled()` 改严格布尔（旧 `bool("false") == True`）；只接受 true/1/yes/on，其余一律关闭。
  - 首次判定为开启时打一次 WARNING，提示快照含聊天内容、用完关闭并清理。
  - `trim_network_parser_debug_dir()` 新增 `max_age_seconds`（默认读保留期配置）：启动时和每次写入后删除过期快照
    （仍排除当前活跃文件），再按容量清理。**决策**：没有尝试更强的正文脱敏——解析器调试本来就需要原始正文，
    改为「默认关 + 醒目告警 + 限期自动删除」来控制暴露面。
- `browser_constants.py` / `system.py` 默认值 & `dashboard-schema.js`：补齐保留期配置项；开关说明加隐私警告。
- 验证：p1 测试 58 项（受跟踪配置为 false、严格布尔 13 例含 `"false"`/`"garbage"`、保留期删除过期但保留活跃文件）。
