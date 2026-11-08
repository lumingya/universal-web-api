"""
app/core/config_parts/paths.py - 统一路径解析（不信任进程工作目录）

sidecar 由运行期管理器以任意工作目录拉起（不是 uwa 根），历史上散落在各处
的 `Path("config/...")`、`Path("temp/...")` 相对路径在运行期一律落空：配置被
静默忽略、设置写到野目录、状态文件散落在启动目录。所有配置/状态/日志路径
都应从这里派生，禁止再写字面量相对路径。

约定：
  * `config_path()` / `sites_path()` / `state_path()` 返回绝对路径，用于读写；
  * `resolve_relative()` 用于接受「用户可能传相对路径」的入口，先按 PROJECT_ROOT
    解析，不存在再退到 CWD（兼容历史行为），都不存在时返回 PROJECT_ROOT 版本，
    保证写操作落在项目目录而不是进程 CWD。
"""
from pathlib import Path
from typing import Optional, Union

from .env_config import PROJECT_ROOT

# ===== 目录 =====
CONFIG_DIR: Path = PROJECT_ROOT / "config"
SITES_DIR: Path = CONFIG_DIR / "sites"
DATA_DIR: Path = PROJECT_ROOT / "data"
LOGS_DIR: Path = PROJECT_ROOT / "logs"
TEMP_DIR: Path = PROJECT_ROOT / "temp"

# ===== 历史常量（供等号右边仍是裸字符串的调用点复用）=====
CONFIG_DIR_STR: str = str(CONFIG_DIR)


def config_path(name: str) -> Path:
    """config/ 下的文件（可传子路径，如 "sites/chat.qwen.ai.json"）。"""
    return CONFIG_DIR / str(name)


def sites_path(name: str) -> Path:
    """config/sites/ 下的文件。"""
    return SITES_DIR / str(name)


def state_path(name: str) -> Path:
    """运行期状态文件（runtime.sqlite3、*.local.json 等）。"""
    return CONFIG_DIR / str(name)


def resolve_relative(value: Union[str, Path]) -> Path:
    """把（可能是相对的）路径解析成绝对路径。

    PROJECT_ROOT 优先，其次 CWD；都不存在时返回 PROJECT_ROOT 版本。
    绝对路径原样返回。
    """
    raw = Path(value)
    if raw.is_absolute():
        return raw
    candidates = (PROJECT_ROOT / raw, Path.cwd() / raw)
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return candidates[0]


def as_str(value: Optional[Union[str, Path]]) -> str:
    """None 安全的 str()，方便塞给只吃字符串的老接口。"""
    if value is None:
        return ""
    return str(value)


__all__ = [
    "PROJECT_ROOT",
    "CONFIG_DIR",
    "CONFIG_DIR_STR",
    "SITES_DIR",
    "DATA_DIR",
    "LOGS_DIR",
    "TEMP_DIR",
    "config_path",
    "sites_path",
    "state_path",
    "resolve_relative",
    "as_str",
]
