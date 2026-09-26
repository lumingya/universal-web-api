"""R2-6：API 进程侧的远程浏览器代理（process 模式下由 get_browser() 返回）。

只覆盖协议接口执行所需的能力：五个 execute_workflow* 方法、标签页池中参数与返回值可 JSON 化的方法与配置属性、
get_pool_status / health_check。其余能力由 worker 通过面板接口提供（API 进程会把 /api/* 整体转发给 worker），
在 API 进程里访问时抛出 WorkerModeUnsupported，给出明确原因。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Iterator, Optional

import requests

from app.core.config import SSEFormatter, get_logger
from app.worker import TOKEN_ENV, TOKEN_HEADER, URL_ENV
from app.worker.protocol import BROWSER_METHODS, TAB_POOL_ATTRS, TAB_POOL_METHODS

logger = get_logger("WORKER_CLIENT")


class WorkerUnavailable(RuntimeError):
    """worker 未配置、未就绪或已退出。"""


class WorkerModeUnsupported(RuntimeError):
    """process 模式下 API 进程不持有浏览器，该能力由 worker 通过面板接口提供。"""


def _endpoint(path: str) -> str:
    base = str(os.getenv(URL_ENV, "") or "").rstrip("/")
    if not base:
        raise WorkerUnavailable("process 模式下未配置 UWAPI_WORKER_URL（worker 未启动）")
    return base + path


def _headers() -> Dict[str, str]:
    return {TOKEN_HEADER: str(os.getenv(TOKEN_ENV, "") or "")}


def _rpc(path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Any:
    try:
        if payload is None:
            response = requests.get(_endpoint(path), headers=_headers(), timeout=timeout)
        else:
            response = requests.post(_endpoint(path), json=payload, headers=_headers(), timeout=timeout)
    except requests.RequestException as exc:
        raise WorkerUnavailable(f"无法连接 worker：{exc}") from exc
    if response.status_code != 200:
        raise WorkerUnavailable(f"worker 返回 {response.status_code}：{response.text[:200]}")
    return response.json()


class RemoteTabPool:
    def __getattr__(self, name: str) -> Any:
        if name in TAB_POOL_ATTRS:
            return _rpc("/internal/worker/tab_pool_attrs").get(name)
        if name in TAB_POOL_METHODS:
            return lambda *args, **kwargs: _rpc(
                "/internal/worker/tab_pool", {"method": name, "args": list(args), "kwargs": kwargs}
            )["result"]
        raise WorkerModeUnsupported(f"process 模式下无法在 API 进程中访问标签页池的 {name}（由 worker 提供）")


class RemoteBrowserProxy:
    """与 BrowserCore 同名的执行入口；执行结果以浏览器层产出的 SSE 文本块原样返回。"""

    is_remote = True

    def __init__(self) -> None:
        self.tab_pool = RemoteTabPool()

    def _execute(self, method: str, kwargs: Dict[str, Any]) -> Iterator[str]:
        stop_checker = kwargs.pop("stop_checker", None)
        payload = {"method": method, "kwargs": kwargs}
        try:
            with requests.post(_endpoint("/internal/worker/execute"), json=payload, headers=_headers(),
                               stream=True, timeout=(5, 30)) as response:
                if response.status_code != 200:
                    yield SSEFormatter.pack_error(f"worker 拒绝执行（{response.status_code}）：{response.text[:200]}")
                    return
                for line in response.iter_lines():
                    if stop_checker is not None and stop_checker():
                        break  # 关闭连接即通知 worker 停止
                    if not line:
                        continue
                    item = json.loads(line)
                    if "chunk" in item:
                        yield item["chunk"]
                    elif "error" in item:
                        yield SSEFormatter.pack_error(f"worker 执行失败：{item['error']}")
        except (requests.RequestException, WorkerUnavailable, ValueError) as exc:
            logger.warning(f"worker 执行请求失败: {exc}")
            yield SSEFormatter.pack_error(f"worker 不可用：{exc}")

    def execute_workflow(self, messages, **kwargs):
        return self._execute("execute_workflow", {"messages": messages, **kwargs})

    def execute_workflow_for_tab_index(self, tab_index, messages, **kwargs):
        return self._execute("execute_workflow_for_tab_index", {"tab_index": tab_index, "messages": messages, **kwargs})

    def execute_workflow_for_route_domain(self, route_domain, messages, **kwargs):
        return self._execute("execute_workflow_for_route_domain",
                             {"route_domain": route_domain, "messages": messages, **kwargs})

    def execute_workflow_for_route_group(self, group_id, messages, **kwargs):
        return self._execute("execute_workflow_for_route_group", {"group_id": group_id, "messages": messages, **kwargs})

    def execute_workflow_for_exact_url(self, exact_url, messages, **kwargs):
        return self._execute("execute_workflow_for_exact_url", {"exact_url": exact_url, "messages": messages, **kwargs})

    def get_pool_status(self) -> Any:
        return _rpc("/internal/worker/browser", {"method": "get_pool_status"})["result"]

    def health_check(self) -> Dict[str, Any]:
        try:
            result = _rpc("/internal/worker/browser", {"method": "health_check"}, timeout=10.0)["result"]
        except WorkerUnavailable as exc:
            return {"connected": False, "error": str(exc), "worker": "unavailable"}
        return {**(result or {}), "worker": "process"}

    def __getattr__(self, name: str) -> Any:
        if name in BROWSER_METHODS:
            return lambda: _rpc("/internal/worker/browser", {"method": name})["result"]
        raise WorkerModeUnsupported(
            f"process 模式下 API 进程不持有浏览器，无法访问 {name}；该功能由 worker 提供（面板的 /api 请求会自动转发给 worker）"
        )


_REMOTE: Optional[RemoteBrowserProxy] = None
_REMOTE_LOCK = threading.Lock()


def get_remote_browser() -> RemoteBrowserProxy:
    global _REMOTE
    if _REMOTE is None:
        with _REMOTE_LOCK:
            if _REMOTE is None:
                _REMOTE = RemoteBrowserProxy()
    return _REMOTE
