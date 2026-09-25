# 仓库安全加固清单（路线图 R0-8）

> 这些设置只能由维护者在 GitHub 网页或本机上完成，这里只给步骤。每完成一项就勾选。
> 背景：本项目的自动更新器会下载 GitHub Release 资产并替换程序文件。所以**谁能改 Release，谁就能在所有用户机器上执行代码**。仓库和账号的安全就是用户的安全。

## 1. 令牌与凭据（优先级最高）

- [ ] **吊销在 AI 对话里出现过的 PAT。** 本轮开发开始时提供给 Arena 沙盒的是 admin 级令牌，它已经出现在对话记录中。
  - 入口：GitHub → Settings → Developer settings → Personal access tokens，删除对应令牌。
  - 现在的推送走「沙盒打包 bundle → 你本机的 Git Credential Manager」，不需要任何令牌。
- [ ] 以后确实要给自动化工具令牌时，一律用 **Fine-grained token**：
  - 只选本仓库；
  - 权限最小化，一般只给 Contents 读写，要改工作流时再加 Workflows 读写；
  - 设置有效期（≤ 30 天）。
- [ ] Portal：每次 AI 会话结束后执行「重新生成地址」并停止 Portal。另外关闭 `portal.startOnActivation`，避免一打开编辑器就对外暴露命令行。
- [ ] 本机 `gh` CLI 已登录。用 `gh auth status` 看一下它的令牌权限，不需要的 scope（比如 `delete_repo`、`admin:org`）可以用 `gh auth refresh -r` 去掉。

## 2. 账号

- [ ] 维护者账号开启 **2FA**，优先用通行密钥或 TOTP，不要用短信。
- [ ] 保存好恢复码。

## 3. 分支与标签规则集（Settings → Rules → Rulesets）

- [ ] **main 分支规则集**：
  - 禁止 force push；
  - 禁止删除；
  - 要求通过 PR 合并。个人仓库也建议开：可以把自己设为 bypass，这样平时照常直接推，但误操作的 force push 会被拦下。
  - 等 CI（R0-3）上线后，再加「必须通过状态检查」。
- [ ] **发布标签规则集**，目标为 `refs/tags/*`：禁止删除，禁止更新（即不能移动已发布的标签）。
  - 顺便处理误打的 `3.7.5` 标签：它指向 848622b，与发布序列无关。先删掉，再启用规则。这一步暂缓，等你确认。

## 4. Release 与更新器

- [ ] 开启 **Immutable releases**（仓库 Settings → General → Releases）：发布后资产不能被替换或删除，防止有人事后偷换更新包。
- [ ] 更新器已经会校验 GitHub API 返回的资产 `digest`（sha256，见 `updater.py`）。但这只能证明「下载的就是仓库里的那个文件」，证明不了「文件是从这份源码构建的」。
  - 等自动发包（R0-4）上线后，用 `actions/attest-build-provenance` 生成构建证明；
  - 更新器（或发布说明）再提供 `gh attestation verify` 校验方式。
- [ ] 发布包清单检查（`static/js` 存在、VERSION 与标签一致、不含 `chrome_profile`、`.env`、`logs`）最好由工作流自动完成。对应 #25：2.9.8 的包缺少 `static/js`。

## 5. 仓库安全功能（Settings → Code security）

- [ ] **Secret scanning** 和 **Push protection**：公开仓库免费，确认已开启。
- [ ] **Dependabot alerts** 和 **Dependabot security updates**。升级依赖前先看 `docs/review/DEPENDENCY_UPGRADE_PLAN.md`，注意 Starlette 下限必须 ≥ 0.49.1。
- [ ] **Private vulnerability reporting**：开启后，别人可以私下报告漏洞，不必开公开 issue。
  - 可以同时加一个简短的 `SECURITY.md`，说明报告渠道和支持的版本，并链接到 `docs/SECURITY-TRUST-BOUNDARIES.md`。

## 6. 社区贡献

- [ ] 行尾已统一（R0-6，`.gitattributes`）。之前因为行尾冲突合不进来的社区 PR（#16、#17、#18、#21），可以请作者 rebase 到新的 main 再提交。
- [ ] 以后的 PR 由 CI（R0-3，暂缓）自动跑测试，维护者只需要看代码。
