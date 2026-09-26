"""R2-6：进程模型（实验性，默认关闭）。

``UWAPI_WORKER_MODE=inproc``（默认）：与以前完全一样，单进程持有浏览器。
``UWAPI_WORKER_MODE=process``：
- **worker 进程**（``UWAPI_WORKER_ROLE=worker``）运行完整应用，持有浏览器、标签页池与命令调度，
  只监听 127.0.0.1，另外提供带令牌鉴权的 ``/internal/worker/*`` 接口；
- **API 进程**对外服务：协议接口（/v1、/url、/tab 等）在本进程完成协议转换、鉴权与计量，
  执行时经 ``RemoteBrowserProxy`` 交给 worker；所有 ``/api/*`` 面板请求整体转发给 worker；
  worker 由 API 进程启动、监控并在退出时关闭；意外退出后自动以指数退避重启。重启窗口内的在途请求仍可能失败，但 API 进程保持可用。
"""

from __future__ import annotations

import os

MODE_ENV = "UWAPI_WORKER_MODE"
ROLE_ENV = "UWAPI_WORKER_ROLE"
URL_ENV = "UWAPI_WORKER_URL"
TOKEN_ENV = "UWAPI_WORKER_TOKEN"
PORT_ENV = "UWAPI_WORKER_PORT"
TOKEN_HEADER = "X-UWAPI-Worker-Token"


def worker_mode() -> str:
    mode = str(os.getenv(MODE_ENV, "inproc") or "inproc").strip().lower()
    return mode if mode in ("inproc", "process") else "inproc"


def is_worker_role() -> bool:
    return str(os.getenv(ROLE_ENV, "") or "").strip().lower() == "worker"


def is_api_process() -> bool:
    """process 模式下的对外 API 进程（不持有浏览器）。"""
    return worker_mode() == "process" and not is_worker_role()
