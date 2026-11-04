"""项目路径的唯一来源。

历史问题：代码里同时存在两套路径写法——``PROJECT_ROOT``（绝对）与
``Path("static")`` / ``Path("download_images")``（相对当前工作目录）。
后者在「不是从仓库根目录启动」时（systemd、计划任务、IDE 的 CWD 配置、
``uv run --directory``）会静默走偏：静态目录不挂载、控制面板 404、图片写到别处。

本模块只依赖标准库，且刻意放在 ``app/`` 顶层而不是 ``app/core/``，
避免为了取一个路径而触发 ``app.core`` 的浏览器相关导入。

约定：

* 需要目录本身时用 :func:`download_dir` / :func:`static_dir`（**每次调用**读取模块常量，
  便于测试用 ``monkeypatch.setattr("app.paths.DOWNLOAD_DIR", tmp)`` 做隔离）；
* 只做只读判断时可以读模块常量，但不要在导入时把它固化成闭包默认值。
"""

from __future__ import annotations

from pathlib import Path

# <root>/app/paths.py -> <root>
PROJECT_ROOT = Path(__file__).resolve().parents[1]

STATIC_DIR = PROJECT_ROOT / "static"
CONFIG_DIR = PROJECT_ROOT / "config"
DOWNLOADS_DIR_NAME = "download_images"
DOWNLOAD_DIR = PROJECT_ROOT / DOWNLOADS_DIR_NAME
LOGS_DIR = PROJECT_ROOT / "logs"

# 运行时由 main.py 挂载、由媒体模块写入；测试可整体替换。
__all__ = [
    "PROJECT_ROOT",
    "STATIC_DIR",
    "CONFIG_DIR",
    "DOWNLOAD_DIR",
    "DOWNLOADS_DIR_NAME",
    "LOGS_DIR",
    "download_dir",
    "static_dir",
    "config_dir",
    "logs_dir",
    "resolve_project_path",
]


def download_dir(*, create: bool = True) -> Path:
    """返回媒体下载目录；默认顺手创建（与旧实现的 mkdir(exist_ok=True) 等价）。"""
    path = Path(DOWNLOAD_DIR)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def static_dir() -> Path:
    return Path(STATIC_DIR)


def config_dir() -> Path:
    return Path(CONFIG_DIR)


def logs_dir(*, create: bool = False) -> Path:
    path = Path(LOGS_DIR)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_project_path(raw: str | Path) -> Path:
    """把配置里的相对路径按项目根目录解析；绝对路径原样返回。"""
    path = Path(str(raw).strip())
    return path if path.is_absolute() else PROJECT_ROOT / path
