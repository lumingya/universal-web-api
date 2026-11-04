# uv 环境与启动方式

本项目**只使用** [uv](https://docs.astral.sh/uv/) 管理 Python 版本、虚拟环境、
依赖和启动，不再提供 `start.py` / `start.bat` / `requirements.txt`，也不支持
用 pip 安装依赖。

## 文件职责

| 文件 | 作用 | 是否提交 |
| --- | --- | --- |
| `pyproject.toml` | 依赖声明（`[project.dependencies]`）、开发依赖（`[dependency-groups].dev`）、pytest/ruff 配置 | 是 |
| `uv.lock` | 所有平台与 Python 版本的精确解析结果 | 是 |
| `.python-version` | uv 默认使用的 Python 版本（当前 3.12） | 是 |
| `.venv/` | uv 创建的唯一虚拟环境 | 否（已在 `.gitignore` 中） |

`requires-python = ">=3.10"`，因此 3.10、3.11、3.12、3.13 都能运行；`.python-version`
只决定 uv 默认下载/使用哪个解释器。

## 安装

```bash
# 安装 uv（已安装可跳过）
# Windows PowerShell:
#   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# macOS / Linux:
#   curl -LsSf https://astral.sh/uv/install.sh | sh

# 按 .python-version 准备 Python，并安装运行 + dev 依赖
uv python install 3.12
uv sync
```

`uv sync` 会创建 `.venv/`，并默认安装 `dev` 依赖组。只安装运行依赖：

```bash
uv sync --no-dev
```

## 启动

```bash
uv run python main.py
```

也可以先激活环境再运行（效果相同）：

```bash
source .venv/bin/activate      # Windows: .venv\Scripts\activate
python main.py
```

`main.py` 作为脚本启动、且在导入应用代码之前，会依次执行
`app/runtime/bootstrap.py`：

1. 设置 UTF-8 并加载 `.env`；
2. 校验当前解释器来自项目 `.venv`（否则提示 `uv run python main.py` 并退出）；
3. 按 `AUTO_UPDATE_ENABLED` 检查并应用更新；更新后用 `uv sync --no-dev --locked`
   同步依赖（失败时回退为 `uv sync --no-dev`）；
4. 以子进程应用 DrissionPage 补丁，保证随后导入的是补丁后的源码；
5. 按 `PROFILE_CLEAN_ENABLED` 清理浏览器缓存；
6. 按 `BROWSER_AUTO_LAUNCH`（默认 `true`）拉起受控 Chromium。

服务运行中保存配置、切换版本或启用定时守护时，`app/runtime/relaunch.py` 会自行
重新拉起进程（`os.execv`），不再依赖任何启动脚本或退出码 3。

## 常用命令

```bash
uv run python main.py        # 启动服务（自动拉起浏览器）
uv run pytest                # 运行测试
uv run ruff check .          # lint
uv lock                      # 修改 pyproject 后重新生成 uv.lock
uv sync --locked             # 严格按 lock 同步（CI/部署）
uv add <包名>                # 添加运行依赖，自动更新 pyproject 与 uv.lock
uv add --group dev <包名>    # 添加开发依赖
uv remove <包名>             # 移除依赖
uv tree                      # 查看依赖树
```

## 镜像与代理

- 依赖镜像用 uv 自己的环境变量配置：
  ```bash
  # Windows PowerShell: $env:UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
  export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
  ```
- `PROXY_ENABLED=true` 时，`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 会传给服务
  和浏览器启动参数；`uv` 自己读取标准的 `HTTP_PROXY` / `HTTPS_PROXY`。
- uv 需要下载 Python 时会访问 GitHub Releases；网络受限时可以先安装 Python 3.10+，
  再执行 `uv sync --python <解释器路径>`。

## 依赖升级

1. 修改 `pyproject.toml` 中的版本范围，或使用 `uv add` / `uv remove`；
2. 运行 `uv lock`；
3. 运行 `uv sync` 与 `uv run pytest`；
4. 提交 `pyproject.toml` 与 `uv.lock`。

`tests/test_uv_environment.py` 会检查：

- 启动脚本、`requirements*.txt`、`check_deps.py` 不存在；
- `pyproject.toml` 声明了运行依赖与 dev 依赖，版本与 `VERSION` 一致；
- `uv.lock` 存在且与 `requires-python` 一致；
- `main.py` 在导入 `app.core` 之前执行 `bootstrap_runtime()`；
- 重启逻辑使用 `os.execv`，不再依赖退出码 3。

## 常见问题

**`uv: command not found`**
按上面的命令安装 uv：Windows 用 `winget install --id=astral-sh.uv`，macOS/Linux 用官方安装脚本 `curl -LsSf https://astral.sh/uv/install.sh | sh`；安装后重新打开终端。

**`未找到项目 .venv，依赖尚未安装`**
```bash
uv sync
uv run python main.py
```

**`检测到项目 .venv，但当前 Python 不是它`**
不要用系统 Python 启动；改用 `uv run python main.py`。

**`error: Failed to download Python ...`**
uv 下载解释器需要访问 GitHub。可以改用系统 Python：
```bash
uv sync --python 3.12          # 使用已安装的 3.12
uv sync --python /usr/bin/python3.11
```

**`uv.lock` 与 `pyproject.toml` 不一致**
```bash
uv lock
uv sync --locked
```
不要手工编辑 `uv.lock`。

**误提交 `.venv`**
`.venv/` 已在 `.gitignore` 中。若曾被跟踪：
```bash
git rm -r --cached .venv
```

**想自己管理浏览器**
在 `.env` 中设置 `BROWSER_AUTO_LAUNCH=false`，并自行启动带
`--remote-debugging-port=9222` 的 Chromium；服务只负责连接。
