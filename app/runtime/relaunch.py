"""服务自重启。

原 ``start.py`` 用退出码 3 通知启动器重新拉起服务。取消启动脚本后，服务在
同一入口上自行重启：

1. 写入重启标记（新进程据此跳过首次启动页）；
2. 进程模型为 ``process`` 时先停掉浏览器 worker，避免留下孤儿进程；
3. 启动一个短暂的替身进程，等端口释放后用 ``os.execv`` 换成新的服务进程；
4. 当前进程退出。

因此保存配置、切换版本、定时守护都仍然能自动重启，且不需要任何外部脚本。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)

# sys.argv[1] = 延迟秒数, [2] = 工作目录, [3:] = 真正要执行的命令
_RELAUNCH_SNIPPET = (
    "import os, sys, time\n"
    "time.sleep(float(sys.argv[1]))\n"
    "os.chdir(sys.argv[2])\n"
    "target = sys.argv[3:]\n"
    "os.execv(target[0], target)\n"
)

_restart_lock = threading.Lock()
_restart_started = False


def _resume_command() -> list[str]:
    """重建当前进程的启动命令。"""
    argv = [str(part) for part in sys.argv]
    if not argv:
        return [sys.executable, str(PROJECT_ROOT / "main.py")]
    first = argv[0]
    if os.name == "nt" and first.lower().endswith(".exe"):
        return [first, *argv[1:]]
    return [sys.executable, *argv]


def _stop_browser_worker() -> None:
    """process 模式下先关闭 worker，避免重启后残留两个浏览器持有者。"""
    try:
        from app.worker import is_worker_role, worker_mode

        if is_worker_role() or worker_mode() != "process":
            return
        from app.worker.supervisor import supervisor

        supervisor.stop()
    except Exception as exc:  # broad-except: 重启路径尽力而为，不能让清理失败阻止退出
        logger.debug(f"停止浏览器 worker 失败（忽略）: {exc}")


def restart_service(delay_seconds: float = 2.5) -> None:
    """重新拉起服务进程并退出当前进程。重复调用只会生效一次。"""
    global _restart_started
    with _restart_lock:
        if _restart_started:
            return
        _restart_started = True

    logger.warning("服务即将重启，新版进程会在 %.1f 秒后接管端口", max(0.0, delay_seconds))
    try:
        from app.services.restart_guard import mark_service_restart

        mark_service_restart()
    except Exception as exc:  # broad-except: 标记失败只影响引导页，不影响重启
        logger.debug(f"写入重启标记失败（忽略）: {exc}")

    _stop_browser_worker()

    command = _resume_command()
    try:
        subprocess.Popen(
            [sys.executable, "-c", _RELAUNCH_SNIPPET, str(max(0.0, delay_seconds)), str(PROJECT_ROOT), *command],
            cwd=str(PROJECT_ROOT),
            env=os.environ.copy(),
        )
    except OSError as exc:
        logger.error("无法启动新服务进程: %s；请手动重新运行 uv run python main.py", exc)
        raise SystemExit(1) from exc

    os._exit(0)


__all__ = ["restart_service"]
