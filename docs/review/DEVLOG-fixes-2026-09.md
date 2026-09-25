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
- [ ] S1 控制面默认开放（认证默认关 + CORS `*`）
- [ ] H1 `.env.example` 诱导不安全部署
- [ ] S8 SVG 以同源活动内容落盘/提供
- [ ] S9 音视频响应头 + `.html` 后缀落盘
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
