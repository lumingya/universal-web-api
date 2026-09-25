# 审查问题修复开发日志（2026-09-25 起）

> 用途：记录「按 `docs/review/CODE_REVIEW_REPORT.md` 修复 Bug」阶段的进度与关键决策，
> 防止上下文压缩或沙盒重启后丢失信息。上一阶段（只审查不改代码）的日志见
> `docs/review/DEVLOG-code-review-2026-09.md`。

## ✅ 状态：P1 + P2 全部完成（分支 `fix/review-p1-p2-b`）

- P1：S13、S1、H1、S8、S9、S12；P2：B8、B5、B2、B1、B3、S10、S11、S4、S5、S6、S3、H9、H12、T1。每一项在下文「4. 修复记录」里都有文件、决策、验证方式的记录。
- 全量测试：修复前 524 passed / 73 failed（其中 9 项是 B1 规格测试，64 项是 T1 fixture 缺失）；现在 **648 passed / 63 skipped / 0 failed**。
  新增回归测试：`tests/test_review_fixes_p1.py`（58 项）和 `tests/test_review_fixes_p2.py`。
- 行为变化中需要合并方留意的新开关（都已写进 `.env.example`）：
  - `RESPONSES_STATE_MAX_ENTRY_MB` / `RESPONSES_STATE_MAX_TOTAL_MB`
  - `RESTART_PROXY_MAX_CONNECTIONS` / `RESTART_PROXY_MAX_BUFFER_MB`
  - `MEDIA_REQUIRE_AUTH`（默认 false）
  - `MEDIA_TRANSCODE_*`
  - `TAB_ACQUIRE_MAX_WAITERS`
  - `PARSER_INSTALL_ENABLED`（默认 false）
- 没做 / 有意保留的：
  - S2（信任边界设计项，按范围排除）。
  - 媒体文件名熵偏低（`时间戳_uuid8`），见 S6 记录，留给 P3。

## 🔜 第三阶段：P3（2026-09-25 用户要求继续）

**用户已批准 P2 的三项取舍**：S4 未强制令牌（严格本机直连仍可用）、S6 媒体鉴权默认关闭、
T1 以 skip + `local_fixture` 标记代替恢复本地文件。原则：**不要把所有接口都改成必须带令牌**，
否则大量第三方工具不可用——P3 中涉及鉴权的改动（S7）同样遵循“本机/已认证可见详情，其余最小化”。

P3 清单（顺序即执行顺序，完成一项勾一项，每项独立提交）：

- [x] B6 `BROWSER_CDP_RECYCLE_AFTER_REQUESTS=inf` OverflowError
- [x] B7 脚本热加载 mtime 相同内容替换仍返回旧脚本
- [x] B4 `n>1` 只返回 1 个 choice
- [x] H7 未导入的类型注解（F821）
- [x] H8 chat.py 重复定义的 Arena 辅助函数
- [x] H10 stream_monitor 重复方法
- [x] H13 未使用的 command_engine_storage mixin
- [x] T2 requirements-dev 缺 httpx
- [x] S14 遗留教程搜索框 innerHTML
- [x] S7 公开引导/健康接口信息最小化
- [x] H14 站点/完整备份导入无大小上限
- [x] H2 README 版本与 CHANGELOG 链接
- [x] H3 .gitignore 规则清理
- [x] H11 受跟踪配置中的具体 Arena 会话 URL
- [x] H5 重复/超大图片资源
- [x] H6 依赖升级计划（只出计划与约束调整，不做大版本跳跃）
- [ ] H4 换行符统一（放最后，单独提交，降低与 main 合并冲突）

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

- [x] B8 命令配置损坏时清空运行缓存
- [x] B5 解冻失败仍交付标签页
- [x] B2 旧版顶层数组历史恢复丢失
- [x] B1 搜索引擎主域被自动发现
- [x] B3 Responses 内存历史无字节预算
- [x] S10 图片比对 / C2PA 直取外部 URL
- [x] S11 DNS 校验后连接重解析
- [x] S4 回环 IP 当作授权
- [x] S5 定时重启代理容量 / 协议
- [x] S6 媒体路由无认证与转码资源
- [x] S3 解析器安装立即 import
- [x] H9 标签页等待队列无总量上限
- [x] H12 网络事件 URL 正则回溯
- [x] T1 干净克隆测试资源不全（范围里列了但清单漏了，补上）

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

### B8 命令配置损坏清空运行缓存 ✅

- `app/services/command_engine.py`：`_read_commands_file()` 返回 `Optional[List]`——JSON 损坏/结构不是 list/IO 异常 → `None`；
  文件不存在或合法空列表 → `[]`。`_refresh_commands_if_changed()` 遇 `None`：保留 last-known-good、**不推进** `_commands_mtime`、
  不做 run_js_file 清理；记录 `_commands_failed_mtime`，同一损坏 mtime 不再重复读取/刷日志；文件修好（mtime 变化）自动重载。
  首次加载即失败时以空配置运行（无 last-known-good 可用）。`_save_commands` 成功时清除失败标记。
- `command_engine_storage.py` 是未被继承的死代码（H13，P3），**未改**。
- 验证：`tests/test_review_fixes_p2.py::test_b8_*`（损坏 JSON/错误结构均保留旧命令且无清理动作、同一损坏文件不重复读、
  修复后切换并产生清理动作（对照）、合法空配置仍会清空）。该用例在修复前代码上失败（已 stash 验证）。

### B5 解冻失败仍交付标签页 ✅

- `app/core/tab_pool_parts/idle_maintenance.py::resume_if_frozen`：CDP `Page.setWebLifecycleState=active` 失败时**不再**
  在 `finally` 清除 `_uwapi_frozen`，改记 `_uwapi_resume_failed_at`；成功时才清冻结标志并清零失败时间。
- `app/core/tab_pool_parts/session.py`：
  - `_resume_after_acquire()` 返回「是否可交付」（调用后仍冻结 → False）。
  - `acquire()` / `acquire_for_command()`：解冻失败 → `_rollback_failed_acquire()` 退回 IDLE、清 task、请求模式回退
    `request_count`，返回 False（manager 会去试下一个会话）。
  - 解冻失败后 10s 冷却期（`_RESUME_FAILURE_BACKOFF_SEC`）内 acquire 直接跳过，避免每次卡 2s CDP 超时。
  - **决策**：没有把会话标成 ERROR——冻结失败多为瞬时 CDP 问题，冷却后重试即可；真正死掉的标签页由既有健康检查处理。
- 验证：p2 新增 3 项（假 CDP 抛错 → acquire False/IDLE/计数不变/保留冻结；冷却期内不发 CDP；恢复后可交付；
  命令模式不动 request_count；未冻结会话不发 CDP）。

### B2 旧版顶层数组历史恢复丢失 ✅

- `app/services/request_manager.py::_load_history`：先 `isinstance(data, list)` / `dict` 分支再取 `records`
  （旧代码 `data.get(...)` 对 list 抛 AttributeError，被外层 except 吞掉 → 恢复 0 条）。
  顺带修掉 `max_records<=0` 时 `lst[-0:]` 返回整个列表的陷阱。
- 验证：p2 新增 2 项（顶层数组 + 混入非 dict 项恢复 2 条且 token 统计回填；对象格式不变；上限 0 时清空）。

### B1 搜索引擎主域被自动发现 ✅（基线 9 个失败转绿）

- `config/site_rules.json`：为 google.com / google.co.jp / google.co.uk / google.com.hk / accounts.google.com / bing.com /
  cn.bing.com / baidu.com / duckduckgo.com / search.yahoo.com / yandex.com / yandex.ru / sogou.com / so.com /
  search.brave.com / naver.com 加 `"auto_discovery": false`（规则按**精确主机**匹配，www 前缀已有别名处理）。
- `app/utils/site_discovery.py`：内置整串精确匹配的兜底模式（`google.<cc>` / `google.co(m).<cc>`、`<cc>.bing.com`、
  `yandex.<cc>`），覆盖 JSON 里不可能穷举的各国主域；site_rules.json 显式值（含 true）优先。
  gemini.google.com、aistudio.google.com、`google.com.attacker.org` 不受影响。
- 验证：`tests/test_site_discovery.py` 36/36 通过（原 9 个失败转绿）；p2 新增 12 项。
- **基线更新**：失败数 73 → 64（剩余 64 个全部属 T1 fixture 缺失）；`/tmp/baseline_failures.txt` 已同步为 64 项。

### B3 Responses 内存历史无字节预算 ✅

- `app/api/chat.py`：
  - 新增 `RESPONSES_STATE_MAX_ENTRY_MB`（默认 8）/ `RESPONSES_STATE_MAX_TOTAL_MB`（默认 64）环境变量，非法值回落默认。
  - 条目改为 `(stored_at, serialized|None, owner, nbytes)`，维护 `_responses_state_total_bytes`；prune 在条数上限之外再按总字节 LRU 淘汰。
  - 单条超限：不存储正文，只留墓碑；续接时返回 **413** 并提示“在 input 中携带完整上下文”（原来是静默吃内存）。
  - 主体隔离：`_responses_principal_from_request` 取 Bearer / X-API-Key 的 SHA-256 指纹；续接时主体不一致一律 404（不泄露 ID 是否存在）。无令牌时为空串，匿名调用共享命名空间（兼容认证关闭的本机场景）。
  - store 语义保持与 OpenAI 一致：`store` 未给出 = 存储，`store:false` = 不存储。
- 测试：p2 新增 4 项（超限 413、总量 LRU + 字节计数一致、跨主体 404、store:false）。全量无新增失败（64 基线）。

### S11 DNS 校验后连接重解析 ✅

- `app/utils/remote_resource.py`：
  - `_validate_fetch_target` 返回 `(url, 已校验地址)`；`_validate_fetch_remote_url` 保留为薄包装。
  - DNS pinning：给 `urllib3.util.connection.create_connection` 包一层（只装一次），它只查**本线程**登记的 `host → 已校验 IP`。
    `get_public_remote_resource` 每一跳（含重定向后的重新校验）都用 `_pinned_dns(url, addresses)` 包住 `requests.get`。
    TCP 连接直接打到校验过的 IP；`HTTPConnection.host` 不变，因此 SNI、证书校验、Host 头都保持原主机名。
    未登记的主机（其他代码路径、HTTP(S)_PROXY 代理主机）原样放行，代理场景交给可信出网代理解析。
  - 选这个方案而不是自定义 Session/Adapter，是因为现有测试（以及调用方）都在 monkeypatch `remote_resource.requests.get`，这样保持兼容。
- 测试：p2 新增 2 项，用本地 HTTP 服务：
  - 校验返回 127.0.0.1、主机名是 `.invalid`（系统 DNS 解析不了）→ 请求成功，Host 头是原主机名。用 stash 验证过：旧代码下这项失败。
  - pin 只在调用期间有效，退出后同一 URL 连接失败。

### S10 图片比对 / C2PA 直取外部 URL ✅

- `app/utils/image_validation.py`（`arena_gpt_image_command` 的 C2PA 检测也经由这里的 `read_image_bytes`）：
  - 不再裸调 `requests.get`，改用 `get_public_remote_resource`：逐跳私网/重定向校验，加上 S11 的 DNS pinning；Referer 只发往图片原始 origin。
  - 流式读取，上限 `MAX_VALIDATION_IMAGE_BYTES`=20MiB。Content-Length 超限时不读 body，读取中途超限就中止并 close。
  - `UnsafeRemoteResourceError` 直接返回空，**不再退回浏览器 fetch**（否则等于绕过同一道校验）。浏览器 fetch 兜底（blob: 或普通网络错误时）也加了字节上限，JS 端和 Python 端都查。
  - data URI（生成图片及上传参考图）先按 base64 长度预估大小，超预算就不解码。
  - `image_signatures` 解码前检查头部尺寸（40MP / 边长 16384），超限只保留字节 sha256，不做像素或 dHash 计算，防解压炸弹。
- 测试：p2 新增 4 项（私网 URL 被拒且无浏览器兜底、流式字节上限、声明超限不读 body、data URI 与像素预算）。

### S4 回环 IP 当作授权 + S5 定时重启代理容量/协议 ✅（同一提交，共用 handoff 代理的设计）

**S4**
- `app/api/browser_routes.py::open-profile-url`：原来只看 `request.client.host` 是不是回环，现在改为 `verify_open_profile_url_auth`：
  - 控制面板认证已启用、且请求带了 Authorization：按管理令牌校验，错误令牌返回 401。
  - 其他情况只接受 `is_trusted_local_request`，即直接回环且没有任何反代/隧道转发头；经 handoff 代理时以代理注入的真实地址为准。
  - 都不满足：认证已启用返回 401，未启用返回 403。
  - 决策：**没有**直接换成 `verify_sensitive_admin_auth`。Link Drawer（外部客户端）不保存面板密钥，改成必须带令牌会让本机功能直接失效；而严格的本机判定已经覆盖了报告里的反代/隧道场景。
  - 目标 URL 限制：拒绝带 userinfo 的链接、localhost / *.localhost，以及非全局 IP 字面量（127/10/192.168/::1 等），返回 400。
- `app/core/http_security.py::is_trusted_local_request`：经 handoff 代理的请求也要检查转发头。代理前面若还有 nginx/隧道，真实来源在 X-Forwarded-For 里，不能因为代理报告的对端是 127.0.0.1 就放行。
- `start.py`：`UWAPI_PROXY_SECRET` 每次启动用 `secrets.token_urlsafe(32)` 生成，只注入子进程环境。子进程环境先 pop 掉继承来的旧值；不走代理时子进程没有这个密钥。

**S5**（`start.py::_RestartHandoffProxy / Handler`）
- 连接预算：`RESTART_PROXY_MAX_CONNECTIONS`（默认 64）个非阻塞信号量，超额直接回 503。
- 缓冲预算：`RESTART_PROXY_MAX_BUFFER_MB`（默认 256）全局字节账本。每个连接按实际缓冲量逐步预占（Content-Length 请求一次性按声明大小预占），超额回 503，finally 中释放。
- 单请求上限仍为 64MiB（超出回 413）。请求头上限 64KiB（超出回 431），原来请求头最多可达 64MB。读请求有 60s 端到端时限（超时回 408），原来是每次 recv 60s。
- 协议处理：
  - chunked 请求体完整读取并原样转发，支持 chunk 扩展和 trailer。
  - `Transfer-Encoding` 最后一项不是 chunked 时回 501。
  - CL 与 TE 同时出现、CL 非数字、多个 CL 不一致，都回 400（防请求走私）。
  - `Expect: 100-continue` 由代理自己回 100 并剥离 Expect 头；其他 Expect 值回 417。
  - 丢弃管线化的多余字节。出错时回明确的 HTTP 状态，而不是静默断开。
- 请求头改写：剥离 Connection/Keep-Alive/Proxy-Connection/Expect 以及**所有客户端送来的 `X-UWA-*`**，再注入 `X-UWA-Client-Addr`（真实对端地址）和 `X-UWA-Proxy-Secret`。外部转发头原样保留，交给后端判定。
- 测试：p2 新增 7 项：
  - 3 项 ASGI：隧道/远程被拒；handoff 地址可信、伪造密钥或前置 nginx 被拒；令牌和目标 URL 限制。
  - 4 项起真实代理加假后端：剥离并注入请求头；chunked 与 Expect；走私/超限/预算返回 400/501/413/503/431，且后端零请求、账本归零；连接预算 503 以及释放后恢复。

### S6 媒体路由无认证与转码资源 ✅

- 新增 `app/utils/media_access.py`，这是纯逻辑模块：
  - `media_request_authorized` 负责鉴权判定。
  - `TranscodeGate` 提供全局并发名额 `slot()`，以及同键合并 `key()`，后者带引用计数，用完就清理。
  - 开关 `MEDIA_REQUIRE_AUTH` 的非法取值 fail-closed。
- 鉴权决策：**默认不强制**，需要显式设置 `MEDIA_REQUIRE_AUTH=true` 才开启。
  - 原因：生成的 `/media`、`/download_images` 链接会原样写进 OpenAI 兼容响应，第三方聊天前端直接用 `<img src>` 渲染，不会带 Bearer 头。默认开启会破坏主流程。
  - 开启后接受以下三种之一：服务令牌（Bearer 或 X-API-Key）、面板令牌、严格本机直连。否则返回 401。
  - 实现方式是 `main.py` 里的 `guard_private_media` 中间件，同时覆盖 `/media` 路由和 `/download_images` 挂载。
  - 文件名仍是 `时间戳_uuid8`，熵偏低，这点已记下，没有改：改动面太大，放到 P3 讨论。
- 转码（`main.py::_transcode_media`）：
  - 先查缓存，然后检查源文件大小上限 `MEDIA_TRANSCODE_MAX_SOURCE_MB`（默认 200），超出返回 413。
  - 进入同键锁后再查一次缓存，所以同一文件的并发请求只跑一次 ffmpeg。
  - 全局名额 `MEDIA_TRANSCODE_MAX_CONCURRENCY`（默认 2），排队超过 `MEDIA_TRANSCODE_QUEUE_TIMEOUT_SEC`（默认 10）返回 503 + Retry-After。
  - ffmpeg 执行部分拆到 `_run_ffmpeg_transcode`，逻辑不变。
- `.env.example` 为 B3/S5/S6 新增的环境变量补了说明（CRLF 保持不变）。
- 测试：p2 新增 5 项：鉴权矩阵、真实 app 上的 401/200 切换、5 线程同键只跑 1 次 ffmpeg、全局满载返回 503、源文件超限返回 413。
  - 顺带修了 S5 的 Expect 测试：原先头和体一次性发出，代理已经收齐请求体就不会回 100（这是正确行为），导致测试偶发失败。现在改为按真实客户端的方式等收到 100 再发体。

### S3 解析器安装立即 import ✅

- `app/services/parser_manager.py`：
  - `install_parser_package` 默认关闭，只有 `PARSER_INSTALL_ENABLED=true` 时才允许安装，非法值视为关闭；关闭时抛 `PermissionError`，连解析包都不做。目前仓库里没有 HTTP 入口会调用它，这个开关是给将来接入口时准备的“可信管理员专用”闸门。
  - `check_import_time_side_effects(tree)` 做“导入期无副作用”静态约束：
    - 模块顶层和类体只允许以下内容：import、pass、docstring、函数和类定义、对名字的字面量赋值，以及末尾的 `if __name__ == "__main__"`。
    - 导入期会被求值的表达式（赋值右侧、默认参数、注解、基类）只能是字面量、名字/属性引用（禁止 `__dunder__`），或白名单纯函数调用：`re.compile`、`set`、`frozenset`、`tuple`、`list`、`dict`、`str.maketrans`、`get_logger`、`logging.getLogger`，且参数也必须满足同样条件。
    - 装饰器只允许 staticmethod、classmethod、property 及其访问器、abstractmethod、dataclass、cached_property。
    - 类不允许 metaclass 或关键字参数。
    - 仓库里现有的 18 个 `*_parser.py` 全部通过（有测试守护），因此不影响正常的解析器写法。
  - 目标类必须是模块顶层类（以前用 ast.walk，嵌套类也能匹配）。
  - `_load_parser_entry` 限制 parsers.json 里的 module 必须是 `app.core.parsers.<合法模块名>`，防止借配置导入任意模块。
  - 模块文档写明：这不是沙箱，解析器方法仍以服务进程权限运行。
- 测试：p2 新增 11 项：
  - 8 种导入期执行写法全部被拒：os.system、`__import__`、默认参数调用、自定义装饰器、动态基类、try、推导式读文件、dunder 链。
  - 安全示例和全部内置解析器通过。
  - 默认关闭，非法开关值同样视为关闭。
  - 模块路径白名单。

### H12 网络事件 URL 正则回溯 ✅

- `app/services/command_engine_results.py::_matches_url_rule`：
  - 正则改用 `regex` 模块，timeout 设为 25ms。它已经是依赖，工作流的 `matches` 用的也是同一预算。
  - 超时按“不匹配”处理，并打 warning。
  - 模式上限 512 字符，超过就按关键词匹配；URL 最多取前 8192 字符参与匹配。
  - 无效正则时，通配回退也走同一个带预算的匹配器，其余语义不变。
- 测试：p2 新增 2 项：
  - 灾难性模式 `(a+)+$`、`(a|aa)*c` 在 5000 字符 URL 上 1 秒内返回 False。用 stash 验证过：旧代码在 100 秒 timeout 内都没跑完。
  - 常规正则、忽略大小写、通配回退、关键词、超长模式的语义保持不变。

### H9 标签页等待队列无总量上限 ✅

- 现状核实：`acquire_async` 系列（32 线程 executor）在应用里**没有调用方**。真正的请求路径是 `workflow.py` 在请求线程里同步调用 `tab_pool.acquire*(timeout=60)`，每个等待者都挂进 `_acquire_waiters / _index_waiters / _route_waiters / _group_waiters` 队列，最长 60 秒，总数没有上限。
- `app/core/tab_pool_parts/manager.py`：
  - 新增 `TAB_ACQUIRE_MAX_WAITERS`（默认 128，0 表示不限）。`_acquire_queue_full` 统计四类队列的总长度。
  - 5 个 acquire 路径在挂入队列**之前**检查，满了立即返回 None，不创建空 deque，也不占位。同时把该 task 记进 `_queue_full_rejections`（有界 256）。
  - `consume_queue_full_rejection(task_id)` 查询后即清除该记录。
- `app/core/browser/workflow.py`：5 处 `session is None` 分支先判断是否因队列已满被拒。是的话返回 `capacity_error / tab_queue_full / status_code=503 / retryable=true`，不再误报成“标签页不存在”（404 类）或无状态的繁忙提示。
- `.env.example` 加了说明。
- 测试：p2 新增 3 项：队列满时三种 acquire 都在 1 秒内拒绝且不挂队、查询即清除；limit=0 不限；工作流错误负载经 `resolve_error_metadata` 解析为 503。

### T1 干净克隆测试资源不全 ✅（全量：64 failed → 0 failed）

- 缺失原因：这些资源在历史上都被作者**有意取消跟踪**：
  - `config/commands.json` 在 cafad7f 删除，之后成为运行时生成的文件。
  - `custom_scripts/examples/arena_payload_interceptor.js` 和 `js/arena-conversation-image-window.user.js` 在 3cd68aa 删除，`custom_scripts/` 被 gitignore。
  - 历史版本的 commands.json 已经过时：拿来运行，57 项里仍有 14 项失败，不能作为 fixture 恢复。
  - 结论：不恢复这些文件，改为“标记为需要本地环境的测试”。
- 新增 `tests/conftest.py`（`.gitignore` 加入白名单 `!tests/conftest.py`）：
  - 静态识别依赖本地资源的测试，依据以下几类写法：
    - 测试函数本身出现资源路径写法。
    - 调用了同模块里用到这些资源的辅助函数/方法（按不动点传递）。
    - unittest 的 `setUp/setUpClass` 读取资源后存进 `self.X`，而测试方法用到了 `self.X`。
  - 资源写法是收紧过的：
    - `COMMANDS_PATH`、`"config" / "commands.json"`、`"config" / "commands.local.json"`。
    - `examples/arena_payload_interceptor.js`、`== "arena_payload_interceptor.js"`。
    - `arena-conversation-image-window`。
    - 自建的 `tmp_path / "commands.json"` 以及只检查字符串的测试都不会误判。
  - 识别出的测试统一打上 `local_fixture` 标记，CI 可以用 `-m "not local_fixture"` 只跑纯逻辑子集。
  - 资源缺失时 skip，并写明缺什么；资源齐全时照常运行。用历史 commands.json 验证过：放回去后这些测试确实会执行。
- `tests/test_arena_direct_models.py::test_collect_model_entries_respects_tab_preset_isolation`：
  - 原来依赖未跟踪的 `config/arena_model_catalog.local.json`。
  - 改为在测试内 monkeypatch 目录数据，现在自足并通过（它是纯逻辑测试，不该依赖本地环境）。
- 结果：
  - `/tmp/runtests.sh`：648 passed, 63 skipped, 0 failed。
  - `-m "not local_fixture"`：648 passed, 63 deselected。
  - 对比修改前：原先通过的 647 项全部仍通过，另有 1 项转为通过；其余 63 项从失败变为跳过。
  - `/tmp/baseline_failures.txt` 已清空，此后任何失败都算新增。

### B6 非有限数值配置 OverflowError ✅（P3）

- `app/core/tab_pool_parts/idle_maintenance.py::_env_float`：`math.isfinite` 校验，inf/-inf/nan/1e400 → 警告并用默认值（禁用仍用 0）；
  新增 `_env_int` 包装，`BROWSER_CDP_RECYCLE_AFTER_REQUESTS` / `_DOM_NODES` 改用它。
- 测试：新建 `tests/test_review_fixes_p3.py`，B6 共 6 项。

### B7 脚本热加载同 mtime 替换返回旧脚本 ✅（P3）

- `app/core/workflow/script_loader.py::load_script_content`：缓存签名由浮点 `st_mtime` 改为
  `(st_mtime_ns, st_ctime_ns, st_size, st_ino)`（原子替换会换 inode、原地改写会动 ctime/size）；
  另外 mtime 距今 < 2s 的文件不信任缓存直接重读（racy-git 做法，应对粗粒度时间戳文件系统）。
- 测试：p3 新增 3 项（同 mtime 不同长度、同 mtime 同长度原子替换、未变化命中缓存）；旧代码下前两项失败（stash 验证）。

### B4 n>1 静默降级 ✅（P3）

- 决策：**拒绝**而非实现（网页端一次只生成一个回复，实现 n>1 需并发占多个标签页，代价高且结果不同源）。
- `app/api/chat.py::ChatRequest` 新增 `validate_n`：`n>1` → ValidationError「仅支持 n=1…」，经 main.py 现有
  RequestValidationError 处理器返回 **422 + OpenAI 风格 `invalid_request_error`**（与其他参数校验失败一致）；n 缺省/1/null 行为不变。
- 测试：p3 新增 2 项（模型层 + ASGI 端到端）。

### H7 F821 缺失 Optional/Any 导入 ✅（P3）

- `start.py` 补 `from typing import Any, Optional`；`app/utils/model_routing.py` 补 `Optional`。
  （两文件均有 `from __future__ import annotations`，运行时不崩，但 `typing.get_type_hints` / 工具链会 NameError。）
- `pyflakes app main.py start.py` 的 undefined name 清零。测试：p3 新增 1 项（get_type_hints 遍历两模块函数；旧代码失败）。

### H8 chat.py 重复 Arena 辅助函数 ✅（P3）

- 删除 `app/api/chat.py` 前一组 6 个被后文覆盖、从未生效的定义（`_is_arena_prompt_rejection` 等），保留实际生效的后一组 → **运行时行为零变化**。
  顺带去掉后一组里与模块级导入重复的 `ARENA_PROMPT_REJECTED_CODE` 局部导入（同一对象，已验证）。模块级兼容别名导入保留。
- pyflakes 的 redefinition 告警从 6+1 降为仅剩与本项无关的局部 `import copy`。测试：p3 新增 1 项（AST 确认各只定义一次）。

### H10 stream_monitor 重复方法 ✅（P3）

- `app/core/stream_monitor.py`：删除 `StreamMonitor` 中被后文覆盖的前一版 `_is_arena_page`（子串匹配，`notarena.ai.evil` 也会命中）
  与内联 JS 版 `_arena_native_stop_present`；保留实际生效的后一版（`is_arena_page_url` 严格主机匹配 / `is_visible_arena_stop`）→ 行为不变。
  （`_arena_image_guard` 的两处是 property + setter，属正常写法，未动。）
- 测试：p3 新增 5 项（AST 唯一性 + 严格 URL 匹配参数化）。

### H13 未使用的 command_engine_storage mixin ✅（P3）

- `git grep` 确认 `app/services/command_engine_storage.py`（`CommandEngineStorageMixin`）在代码、打包脚本、文档中均无引用，
  `CommandEngine` 自带全部同名方法 → 直接删除，避免有人修补错文件。
- 测试：p3 新增 1 项（文件不存在 + CommandEngine 仍有 CRUD 方法）。

### T2 requirements-dev 缺 httpx ✅（P3）

- `requirements-dev.txt` 增加 `httpx>=0.27,<1`（TestClient/ASGITransport 所需；当前环境 0.28.1）。
  pytest-asyncio 经核实没有测试使用 `pytest.mark.asyncio`，不加。测试：p3 新增 1 项。

### S14 教程页 innerHTML 注入 ✅（P3）

- `static/tutorial/index.html`：新增 `escHtml`；搜索结果（含**用户输入的查询词**回显、章节标题/分组/小节摘要、href）
  与 TOC（h3 文本/id）拼 innerHTML 前全部转义。NAV/SITES 为页内常量，未改。
- 校验：抽取内联脚本 `node --check` 通过；escHtml 对 `<img onerror>` 等输出正确。测试：p3 新增 1 项（源码断言）。

### S7 公开健康/引导接口信息暴露 ✅（P3）

- 新增 `app/utils/diagnostics_access.py`：特权调用者 = 严格本机直连（复用 S4 `is_trusted_local_request`，有转发头即不算本机）
  或携带有效 `AUTH_TOKEN` / `DASHBOARD_AUTH_TOKEN`（Bearer / X-API-Key）。**不强制令牌**（遵照用户要求）。
- `GET /health`：非特权 → 最小响应 `{service, browser:{connected}, config:{auth_enabled, dashboard_auth_enabled}, detail:"restricted", timestamp}`，
  只读连接标志、**不调用 `browser.health_check()`**（避免匿名请求触发浏览器连接）；200/503 语义保留，探活脚本不受影响；
  控制面板登录前需要的 `dashboard_auth_enabled` 保留。错误令牌也只是降级为最小响应，不返回 401。特权 → 原详细响应。
- `GET /api/startup/controlled-browser-guide-data`：非特权 → 403；引导页/教程页前端本身在失败时回落内置默认站点列表。
- README 接口表注明行为。测试：p3 新增 9 项（远程匿名最小且不连浏览器、503 保留、本机详情、本机+转发头视为远程、三种令牌、错误令牌不 401、引导数据）。

### H14 站点/完整备份导入无大小上限 ✅（P3）

- `static/js/dashboard-methods.js`：新增 `SITE_CONFIG_IMPORT_MAX_BYTES = 8 MiB`、`SETTINGS_BACKUP_IMPORT_MAX_BYTES = 32 MiB`
  与 `importFileSizeError()`；`handleImportFile` / `handleSettingsBackupImportFile` 在 `FileReader` 读取前检查 `file.size`，
  超限给出中文提示并重置 input；顺带补 `reader.onerror` 提示。（当前 `config/` 全量约 400KB，上限余量充足。）
- 校验：`node --check` 通过。测试：p3 新增 2 项（源码顺序断言 + node 执行 helper）。

### H2 README 版本号/更新日志链接过期 ✅（P3）

- `README.md` / `README.zh-CN.md` / `README.en.md`：版本 2.9.8 → 3.0.0（与 `VERSION` 一致）；已被作者删除的
  `CHANGELOG_CURRENT.md` 链接改指向仓库中实际存在的 `CHANGELOG-3.0.0.md`（en 版升级说明一处同改）。
- 测试：p3 新增 3 项（三份 README 的版本号 == VERSION，且所有 `](./xxx)` 相对链接目标存在）——以后发版忘改 README 会被测试拦下。

### H3 .gitignore 清理 ✅（P3）

- `config/marketplace_cache.json`：被忽略却仍被跟踪；全仓库已无任何代码引用 marketplace（功能已下线）→ `git rm --cached` 取消跟踪（本地文件保留，规则保留）。
- scripts 规则：原先 `scripts/`（未锚定）+ `scripts/arena_models_cache.json` + `scripts/custom/` + `!/scripts/` `/scripts/*` 重叠，
  合并为 `/scripts/*` + 两个随仓库发布脚本的白名单（仓库里只有根目录一个 scripts/，语义不变）。
- tests 白名单：散落 8 处的 `!tests/...` 合并为一个按字母排序的块；删去 4 个从不存在/已删除的条目
  （hard_stop_page_lock、attachment_evidence_regressions、proxy_api_cancellation、proxy_disconnect_regressions，origin/main 也没有）。
- 校验：`git ls-files -ci --exclude-standard` 为空；check-ignore 抽查本地脚本/本地测试仍被忽略、发布文件不被忽略。
- 测试：p3 新增 2 项（无「已跟踪却被忽略」文件；白名单条目都存在）。
- ⚠️ 与 main 合并时 `.gitignore` 若冲突，以本分支结构为准，再把 main 新增的条目并入白名单块即可。

### H11 browser_config.json 含个人 Arena 会话 URL ✅（P3）

- `config/browser_config.json`（随仓库/更新包分发，更新器默认不保留它）的 `tab_pool.excluded_urls` 里有 121 条、
  `tab_pool.route_groups` 5 组共 25 个成员都是作者本人的 `https://arena.ai/c/<uuid>` 会话。
  → `excluded_urls` 只保留通用的 `https://arena.ai/image/direct`，`route_groups` 置为 `[]`（空列表即功能默认值；
  用占位 URL 反而会被当成真实路由去打开，所以不用占位符）。其余键不动，JSON 按原格式（indent=2、ensure_ascii=False）写回，diff 只涉及这两个键。
- ⚠️ 维护者若本地正在用这些分组：旧值可用 `git show 9ab1b11:config/browser_config.json` 找回，建议之后放在本地、不再提交。
- 测试：p3 新增 1 项（分发配置不含 `/c/<uuid>` 会话 URL，route_groups 为空）。

### H5 图片重复 / logo.svg 过大 ✅（P3）

- 删除 `assets/tutorial-dashboard-overview.png`、`assets/workflow-visualization.png`：与 `static/` 下同名文件字节完全相同（sha1 一致），
  且全仓库只引用 `static/` 版本（教程页、update_preserve）。`assets/` 其余两张图保留。
- `static/images/logo.svg`（VTracer 描摹，1407 条 path）无损压缩 **792,262 → 525,032 字节（-34%，gzip 289KB → 192KB）**：
  scour（4 位有效数字、去 XML 声明/注释）+ 去掉 no-op `translate(0)` + 把 1373 个 `translate(x,y)` 烘焙进路径起点
  （起点 `m` 改 `M` 后补 `l` 保持后续隐式坐标为相对）。保留 `width/height=640`，未加 viewBox（不改变现有缩放行为）。
- 视觉校验：cairosvg 在 640/128/32 px 渲染逐像素对比，平均差 ≤0.38/255、最大 9/255（抗锯齿级）；ImageMagick 渲染 PSNR 同样极高。
  PNG 用 Pillow 无损重编码只省 3~5%，不值得改动二进制历史，未做。临时文件已删除。
- 测试：p3 新增 1 项（已跟踪图片无字节重复；logo < 600KB 且尺寸属性不变）。

### H6 依赖升级计划 ✅（P3，仅计划）

- 新增 `docs/review/DEPENDENCY_UPGRADE_PLAN.md`：现状表、三条 Starlette CVE 适用性核对（当前 0.36.3 均不适用；
  **升级时下限必须 ≥0.49.1** 以避开 CVE-2025-62727 的 0.39–0.49.0 区间）、分阶段步骤（先 lock 再升级）、人工冒烟清单、回退。
- 实测：临时 venv 装 fastapi 0.141.1 / starlette 1.7.0 / uvicorn 0.54.0 跑非浏览器测试：686 通过 / 63 跳过 / 1 失败；
  失败原因是新版 Starlette 不再把发送异常包进 ExceptionGroup，清理断言都成立。
  → 放宽 `tests/test_cancel_storm_regressions.py` 的异常类型断言为 `(BaseExceptionGroup, OSError)`，新旧版都能通过。临时 venv 已删除。
- `requirements.txt` 未改动（按计划分阶段做）。

### H4 换行符统一 ⏸（P3，等待用户决定）

- 现状（`git ls-files --eol`）：242 LF / 39 CRLF / 40 混合 / 9 二进制；无 `.gitattributes`。
- 风险：若现在整文件规范化（`.gitattributes` + `git add --renormalize .`），会改动约 79 个文件的每一行，
  其中大部分 origin/main 也在改 → 合并时大面积冲突。单加 `* text=auto` 而不重新规范化，又会让这些文件在所有人的工作区里显示为「已修改」。
- 所以 P3 其余 16 项已全部完成，H4 暂停，请用户选择执行时机/方式。

