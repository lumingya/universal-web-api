"""R2-6：进程模型（API 进程 + 浏览器 worker）。

worker 端用真实的 uvicorn 线程承载内部接口，浏览器换成假实现；API 端使用远程代理与转发中间件。
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time

import pytest
import requests
from fastapi import Request  # 模块级导入：本文件启用了延迟注解，FastAPI 需要在模块全局解析该类型

from app.core.config_parts.sse_formatter import SSEFormatter
from app.worker import MODE_ENV, ROLE_ENV, TOKEN_ENV, TOKEN_HEADER, URL_ENV

TOKEN = "test-worker-token"
ANSWER_PARTS = ["远程", "执行", "成功"]


class _FakePool:
    allocation_mode = "first_idle"
    excluded_urls = ["https://skip.example/"]
    preserve_error_tabs = False
    auto_remember_url_presets = True

    def get_tabs_with_index(self):
        return [{"persistent_index": 1, "url": "https://chat.example.com/", "status": "idle"}]

    def get_route_groups_snapshot(self):
        return [{"id": "g1", "members": []}]

    def set_tab_preset(self, persistent_index, preset_name):
        return persistent_index == 1 and preset_name == "主预设"


class _FakeBrowser:
    def __init__(self):
        self.tab_pool = _FakePool()
        self.calls = []
        self.stopped = threading.Event()
        self.slow = False

    def _run(self, method, kwargs):
        stop_checker = kwargs.pop("stop_checker")
        self.calls.append((method, kwargs))
        for part in ANSWER_PARTS:
            yield SSEFormatter.pack_chunk(part, model="web-browser")
            if self.slow:
                deadline = time.time() + 5
                while time.time() < deadline:
                    if stop_checker():
                        self.stopped.set()
                        return
                    time.sleep(0.05)
        yield SSEFormatter.pack_finish(model="web-browser")

    def execute_workflow(self, messages, **kwargs):
        return self._run("execute_workflow", {"messages": messages, **kwargs})

    def execute_workflow_for_route_domain(self, route_domain, messages, **kwargs):
        return self._run("execute_workflow_for_route_domain", {"route_domain": route_domain, "messages": messages, **kwargs})

    def execute_workflow_for_tab_index(self, tab_index, messages, **kwargs):
        return self._run("execute_workflow_for_tab_index", {"tab_index": tab_index, "messages": messages, **kwargs})

    def execute_workflow_for_exact_url(self, exact_url, messages, **kwargs):
        return self._run("execute_workflow_for_exact_url", {"exact_url": exact_url, "messages": messages, **kwargs})

    def execute_workflow_for_route_group(self, group_id, messages, **kwargs):
        return self._run("execute_workflow_for_route_group", {"group_id": group_id, "messages": messages, **kwargs})

    def get_pool_status(self):
        return {"total": 1}

    def health_check(self):
        return {"connected": True, "tabs": 1}


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def worker(monkeypatch):
    import uvicorn
    from fastapi import FastAPI

    from app.worker import rpc_routes

    fake = _FakeBrowser()
    monkeypatch.setattr(rpc_routes, "_browser", lambda: fake)
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    app = FastAPI()
    app.include_router(rpc_routes.router)

    @app.get("/api/ping")
    async def ping(request: Request):  # 用于验证转发：回显转发头
        return {"from": "worker", "xff": request.headers.get("x-forwarded-for"),
                "secret": request.headers.get("x-uwa-proxy-secret")}

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    monkeypatch.setenv(URL_ENV, f"http://127.0.0.1:{port}")
    yield fake
    server.should_exit = True
    thread.join(timeout=5)


def _proxy():
    from app.worker.client import RemoteBrowserProxy

    return RemoteBrowserProxy()


def test_remote_execute_streams_the_same_chunks(worker):
    chunks = list(_proxy().execute_workflow([{"role": "user", "content": "hi"}], task_id="t1", workflow_priority=2))
    assert "".join(chunks).count("远程") == 1 and chunks[-1].rstrip().endswith("[DONE]")
    method, kwargs = worker.calls[-1]
    assert method == "execute_workflow" and kwargs["task_id"] == "t1" and kwargs["workflow_priority"] == 2


def test_stop_on_api_side_reaches_the_worker(worker):
    worker.slow = True
    seen = []

    def stop_checker():
        return len(seen) >= 1

    for chunk in _proxy().execute_workflow_for_route_domain("chat.example.com", [{"role": "user", "content": "hi"}],
                                                            stop_checker=stop_checker):
        seen.append(chunk)
    assert worker.stopped.wait(timeout=5), "worker 没有收到停止信号"
    assert worker.calls[-1][1]["route_domain"] == "chat.example.com"


def test_tab_pool_rpc_attributes_and_unsupported_access(worker):
    from app.worker.client import WorkerModeUnsupported

    proxy = _proxy()
    assert proxy.tab_pool.get_tabs_with_index()[0]["persistent_index"] == 1
    assert proxy.tab_pool.allocation_mode == "first_idle"
    assert proxy.tab_pool.route_groups == [{"id": "g1", "members": []}]
    assert proxy.tab_pool.set_tab_preset(1, "主预设") is True
    assert proxy.get_pool_status() == {"total": 1}
    assert proxy.health_check()["connected"] is True
    with pytest.raises(WorkerModeUnsupported):
        proxy.tab_pool.acquire  # noqa: B018 - 不可序列化的能力不开放
    with pytest.raises(WorkerModeUnsupported):
        proxy.page  # noqa: B018


def test_worker_rejects_bad_token_and_unknown_methods(worker):
    import os

    base = os.environ[URL_ENV]
    bad = requests.post(base + "/internal/worker/execute", json={"method": "execute_workflow", "kwargs": {}},
                        headers={TOKEN_HEADER: "wrong"}, timeout=5)
    assert bad.status_code == 403
    health_without_token = requests.get(base + "/internal/worker/health", timeout=5)
    assert health_without_token.status_code == 403
    unknown = requests.post(base + "/internal/worker/execute", json={"method": "shutdown", "kwargs": {}},
                            headers={TOKEN_HEADER: TOKEN}, timeout=5)
    assert unknown.status_code == 400
    tab_pool = requests.post(base + "/internal/worker/tab_pool", json={"method": "acquire"}, headers={TOKEN_HEADER: TOKEN},
                             timeout=5)
    assert tab_pool.status_code == 400


def test_api_process_forwards_api_requests_with_real_client_ip(worker, monkeypatch):
    import httpx
    import main

    monkeypatch.setenv(MODE_ENV, "process")
    monkeypatch.delenv(ROLE_ENV, raising=False)
    monkeypatch.setenv("UWAPI_PROXY_SECRET", "proxy-secret-1")

    async def run():
        transport = httpx.ASGITransport(app=main.app, client=("203.0.113.5", 44321))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8199") as client:
            return await client.get("/api/ping")

    response = asyncio.run(run())
    assert response.status_code == 200, response.text[:300]
    assert response.json() == {"from": "worker", "xff": "203.0.113.5", "secret": "proxy-secret-1"}


def test_chat_completion_in_process_mode_executes_on_the_worker(worker, monkeypatch):
    openai = pytest.importorskip("openai")
    import httpx
    import main

    monkeypatch.setenv(MODE_ENV, "process")
    monkeypatch.delenv(ROLE_ENV, raising=False)
    for name in ("AUTH_ENABLED", "AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    async def run():
        http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://127.0.0.1:8199")
        client = openai.AsyncOpenAI(base_url="http://127.0.0.1:8199/v1", api_key="k", http_client=http_client)
        async with client:
            return await client.chat.completions.create(model="deepseek-v4", messages=[{"role": "user", "content": "hi"}])

    completion = asyncio.run(run())
    assert completion.choices[0].message.content == "".join(ANSWER_PARTS)
    assert worker.calls, "请求没有在 worker 上执行"


def test_get_browser_returns_proxy_only_in_api_process_mode(monkeypatch):
    from app.core.browser import main as browser_main
    from app.worker.client import RemoteBrowserProxy

    monkeypatch.setenv(MODE_ENV, "process")
    monkeypatch.delenv(ROLE_ENV, raising=False)
    assert isinstance(browser_main.get_browser(auto_connect=False), RemoteBrowserProxy)
    monkeypatch.setenv(ROLE_ENV, "worker")
    assert not isinstance(browser_main.get_browser(auto_connect=False), RemoteBrowserProxy)
    monkeypatch.setenv(MODE_ENV, "inproc")
    monkeypatch.delenv(ROLE_ENV, raising=False)
    assert not isinstance(browser_main.get_browser(auto_connect=False), RemoteBrowserProxy)


def test_supervisor_restarts_worker_after_unexpected_exit(monkeypatch):
    """API 进程监控到 worker 崩溃后会自动拉起新子进程。"""
    from app.worker.forwarding import PROXY_SECRET_ENV
    from app.worker.supervisor import WorkerSupervisor

    monkeypatch.setenv("AUTO_OPEN_BROWSER", "false")
    monkeypatch.setenv("BROWSER_PORT", str(_free_port()))
    for key in (URL_ENV, TOKEN_ENV, "UWAPI_WORKER_PORT", PROXY_SECRET_ENV):
        monkeypatch.delenv(key, raising=False)

    supervisor = WorkerSupervisor()
    try:
        first_url = supervisor.start(ready_timeout=90)
        first_process = supervisor.process
        assert first_process is not None
        first_token = os.environ[TOKEN_ENV]
        first_health = requests.get(
            first_url + "/internal/worker/health", headers={TOKEN_HEADER: first_token}, timeout=5
        ).json()
        first_worker_pid = first_health["pid"]
        supervisor.start_monitoring(
            check_interval=0.05,
            ready_timeout=15,
            initial_retry_delay=0.05,
            max_retry_delay=0.2,
        )
        first_process.kill()
        first_process.wait(timeout=5)

        deadline = time.monotonic() + 30
        restarted = False
        while time.monotonic() < deadline:
            current = supervisor.process
            token = os.environ.get(TOKEN_ENV, "")
            if current is not None and current is not first_process and supervisor.alive() and token != first_token:
                try:
                    response = requests.get(
                        str(supervisor.url) + "/internal/worker/health",
                        headers={TOKEN_HEADER: token}, timeout=1,
                    )
                except requests.RequestException:
                    time.sleep(0.05)
                    continue
                if response.status_code == 200 and response.json().get("pid") != first_worker_pid:
                    restarted = True
                    break
            time.sleep(0.05)
        assert restarted, "worker 崩溃后监控器没有在时限内拉起新进程"
    finally:
        supervisor.stop()
    assert not supervisor.alive()


def test_supervisor_starts_and_stops_a_real_worker_process(monkeypatch):
    """真实启动一个 worker 子进程（python -m uvicorn main:app，worker 角色），确认就绪与关闭。"""
    from app.worker.supervisor import WorkerSupervisor

    monkeypatch.setenv("AUTO_OPEN_BROWSER", "false")
    monkeypatch.setenv("BROWSER_PORT", str(_free_port()))  # 指向一个没有浏览器的端口，worker 只会告警
    for key in (URL_ENV, TOKEN_ENV):
        monkeypatch.delenv(key, raising=False)
    supervisor = WorkerSupervisor()
    try:
        url = supervisor.start(ready_timeout=90)
        assert supervisor.alive()
        health = requests.get(url + "/internal/worker/health", headers={TOKEN_HEADER: os.environ[TOKEN_ENV]}, timeout=5)
        assert health.json()["role"] == "worker"
        denied = requests.post(url + "/internal/worker/browser", json={"method": "health_check"}, timeout=5)
        assert denied.status_code == 403  # 没有令牌
    finally:
        supervisor.stop()
    assert not supervisor.alive()
