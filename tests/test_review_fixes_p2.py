"""审查修复回归测试 · 第二批（P2）。

全部使用合成数据、临时目录与桩对象；不依赖真实浏览器、CDP 或外网。
"""

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# B8 · 命令配置短暂损坏不清空 last-known-good
# ---------------------------------------------------------------------------

def _bump_mtime(path: Path, delta: float) -> None:
    st = path.stat()
    os.utime(path, (st.st_atime + delta, st.st_mtime + delta))


def test_b8_corrupt_commands_json_keeps_last_known_good(tmp_path, monkeypatch):
    from app.services.command_engine import CommandEngine

    monkeypatch.delenv("CMD_ENGINE_AUTO_START", raising=False)
    commands_file = tmp_path / "commands.json"
    local_file = tmp_path / "commands.local.json"
    command = {
        "id": "cmd-1", "name": "inject", "enabled": True,
        "trigger": {"type": "request_count", "value": 1},
        "actions": [{"type": "run_js_file", "file_path": "custom_scripts/_review_b8.js"}],
    }
    commands_file.write_text(json.dumps({"commands": [command]}), encoding="utf-8")

    engine = CommandEngine()
    try:
        engine._commands_file = str(commands_file)
        engine._commands_local_file = str(local_file)
        cleanup_calls = []
        monkeypatch.setattr(engine, "_cleanup_disabled_run_js_file_actions",
                            lambda actions: cleanup_calls.append(list(actions)))

        engine._refresh_commands_if_changed(force=True)
        assert [c["id"] for c in engine._commands_cache] == ["cmd-1"]
        good_mtime = engine._commands_mtime

        # 编辑器半写入 → 损坏 JSON
        commands_file.write_text('{"commands": [ {"id": "cmd-1", ', encoding="utf-8")
        _bump_mtime(commands_file, 5)
        engine._refresh_commands_if_changed()
        assert [c["id"] for c in engine._commands_cache] == ["cmd-1"]
        assert engine._commands_mtime == good_mtime          # 未推进 mtime
        assert all(not actions for actions in cleanup_calls)  # 没有产生清理动作

        # 同一损坏文件不会反复读取
        with mock.patch.object(engine, "_read_commands_file", wraps=engine._read_commands_file) as reader:
            engine._refresh_commands_if_changed()
            reader.assert_not_called()

        # 结构错误（不是 list）同样视为读失败
        commands_file.write_text('{"commands": {"oops": 1}}', encoding="utf-8")
        _bump_mtime(commands_file, 10)
        engine._refresh_commands_if_changed()
        assert [c["id"] for c in engine._commands_cache] == ["cmd-1"]

        # 修复为合法新配置 → 正常切换
        command2 = dict(command, id="cmd-2", name="other", actions=[])
        commands_file.write_text(json.dumps({"commands": [command2]}), encoding="utf-8")
        _bump_mtime(commands_file, 15)
        engine._refresh_commands_if_changed()
        assert [c["id"] for c in engine._commands_cache] == ["cmd-2"]
        # 对照：合法切换确实会产生 run_js_file 清理动作（证明上面的「无清理」断言有效）
        assert cleanup_calls and cleanup_calls[-1]

        # 合法的空配置仍然会清空（与读失败区分）
        commands_file.write_text(json.dumps({"commands": []}), encoding="utf-8")
        _bump_mtime(commands_file, 20)
        engine._refresh_commands_if_changed()
        assert engine._commands_cache == []
    finally:
        engine.shutdown()


# ---------------------------------------------------------------------------
# B5 · 解冻失败不交付 BUSY 标签页
# ---------------------------------------------------------------------------

class _FakeCdpTab:
    def __init__(self, fail: bool):
        self.fail = fail
        self.calls = []

    def run_cdp(self, method, **params):
        self.calls.append((method, params))
        if method == "Page.setWebLifecycleState" and self.fail:
            raise RuntimeError("synthetic CDP failure")
        return {}


def _frozen_session(fail: bool):
    from app.core.tab_pool_parts.session import TabSession

    session = TabSession(id="tab-b5", tab=_FakeCdpTab(fail))
    session._uwapi_frozen = True
    session._uwapi_frozen_at = time.time() - 120
    return session


def test_b5_unfreeze_failure_does_not_hand_out_session():
    from app.core.tab_pool_parts.session import TabStatus

    session = _frozen_session(fail=True)
    assert session.acquire("task-1") is False
    assert session.status == TabStatus.IDLE
    assert session.current_task_id is None
    assert session.request_count == 0
    assert session._uwapi_frozen is True            # 保留待确认冻结态

    # 冷却期内直接跳过，不再发 CDP
    calls_before = len(session.tab.calls)
    assert session.acquire_for_command("cmd-1") is False
    assert len(session.tab.calls) == calls_before

    # 冷却期过后 CDP 恢复正常 → 可正常交付并清除冻结
    session._uwapi_resume_failed_at = time.time() - 60
    session.tab.fail = False
    assert session.acquire("task-2") is True
    assert session.status == TabStatus.BUSY
    assert session._uwapi_frozen is False
    assert session.request_count == 1


def test_b5_command_acquire_rolls_back_without_touching_request_count():
    from app.core.tab_pool_parts.session import TabStatus

    session = _frozen_session(fail=True)
    session.request_count = 7
    assert session.acquire_for_command("cmd-1") is False
    assert session.status == TabStatus.IDLE and session.request_count == 7


def test_b5_unfrozen_session_acquire_is_unchanged():
    from app.core.tab_pool_parts.session import TabSession, TabStatus

    session = TabSession(id="tab-ok", tab=_FakeCdpTab(fail=True))
    assert session.acquire("task") is True
    assert session.status == TabStatus.BUSY
    assert session.tab.calls == []   # 未冻结时不发任何 CDP


# ---------------------------------------------------------------------------
# B2 · 旧版顶层数组历史恢复
# ---------------------------------------------------------------------------

def _bare_request_manager(tmp_path, monkeypatch, max_records=200):
    from app.services.request_manager import RequestManager

    rm = object.__new__(RequestManager)
    rm._history_file = str(tmp_path / "request_history.json")
    rm._monitor_history = []
    rm._history_revision_cache = None
    rm.total_input_tokens = 0
    rm.total_output_tokens = 0
    monkeypatch.setattr(rm, "_request_monitor_enabled", lambda: True, raising=False)
    monkeypatch.setattr(rm, "_request_monitor_max_records", lambda: max_records, raising=False)
    return rm


def _history_record(i):
    return {"request_id": f"req-{i}", "created_at": 1000.0 + i, "prompt": f"p{i}",
            "response": f"r{i}", "status": "completed",
            "token_estimate": {"prompt": 3, "response": 5}}


def test_b2_legacy_top_level_array_history_is_restored(tmp_path, monkeypatch):
    rm = _bare_request_manager(tmp_path, monkeypatch)
    Path(rm._history_file).write_text(json.dumps([_history_record(1), _history_record(2), "junk"]),
                                      encoding="utf-8")
    rm._load_history()
    assert [r["request_id"] for r in rm._monitor_history] == ["req-1", "req-2"]
    assert rm.total_input_tokens == 6 and rm.total_output_tokens == 10


def test_b2_object_format_still_works_and_zero_limit_clears(tmp_path, monkeypatch):
    rm = _bare_request_manager(tmp_path, monkeypatch)
    Path(rm._history_file).write_text(json.dumps({"records": [_history_record(1)]}), encoding="utf-8")
    rm._load_history()
    assert [r["request_id"] for r in rm._monitor_history] == ["req-1"]

    rm0 = _bare_request_manager(tmp_path, monkeypatch, max_records=0)
    rm0._load_history()
    assert rm0._monitor_history == []   # lst[-0:] 陷阱


# ---------------------------------------------------------------------------
# B1 · 搜索引擎主域禁自动发现，AI 子域保留
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain,allowed", [
    ("google.de", False), ("www.google.com.br", False), ("cn.bing.com", False), ("yandex.ru", False),
    ("duckduckgo.com", False), ("www.baidu.com", False),
    ("gemini.google.com", True), ("aistudio.google.com", True), ("google.com.attacker.org", True),
    ("notgoogle.com", True), ("copilot.microsoft.com", True),
])
def test_b1_builtin_search_hosts(domain, allowed):
    from app.utils.site_discovery import automatic_discovery_allowed

    assert automatic_discovery_allowed(domain) is allowed


def test_b1_explicit_rule_overrides_builtin(monkeypatch):
    from app.utils import site_discovery

    monkeypatch.setattr(site_discovery, "get_site_rule",
                        lambda host: {"auto_discovery": True} if host == "google.de" else {})
    assert site_discovery.automatic_discovery_allowed("google.de") is True


# ---------------------------------------------------------------------------
# B3 · Responses 续接历史字节预算 + 主体隔离
# ---------------------------------------------------------------------------

@pytest.fixture
def responses_state(monkeypatch):
    from app.api import chat

    monkeypatch.setattr(chat, "_responses_state_by_id", chat.OrderedDict())
    monkeypatch.setattr(chat, "_responses_state_total_bytes", 0)
    return chat


def _payload(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def test_b3_entry_over_budget_is_not_stored_and_resume_returns_413(responses_state, monkeypatch):
    chat = responses_state
    monkeypatch.setattr(chat, "RESPONSES_STATE_MAX_ENTRY_BYTES", 1024)
    chat._store_responses_state("resp_big", [{"role": "user", "content": "x" * 5000}],
                                _payload("ok"), enabled=True)
    assert chat._responses_state_total_bytes == 0
    with pytest.raises(chat.HTTPException) as exc:
        chat._load_responses_state("resp_big")
    assert exc.value.status_code == 413


def test_b3_total_budget_evicts_lru(responses_state, monkeypatch):
    chat = responses_state
    monkeypatch.setattr(chat, "RESPONSES_STATE_MAX_ENTRY_BYTES", 10_000)
    monkeypatch.setattr(chat, "RESPONSES_STATE_MAX_TOTAL_BYTES", 5_000)
    for i in range(5):
        chat._store_responses_state(f"resp_{i}", [{"role": "user", "content": "y" * 1500}],
                                    _payload("ok"), enabled=True)
    assert chat._responses_state_total_bytes <= 5_000
    assert "resp_0" not in chat._responses_state_by_id
    assert "resp_4" in chat._responses_state_by_id
    assert sum(e[3] for e in chat._responses_state_by_id.values()) == chat._responses_state_total_bytes
    history = chat._load_responses_state("resp_4")
    assert history[-1]["role"] == "assistant"


def test_b3_state_is_bound_to_principal(responses_state):
    chat = responses_state

    class Req:
        def __init__(self, headers):
            self.headers = headers

    alice = chat._responses_principal_from_request(Req({"authorization": "Bearer alice-token"}))
    bob = chat._responses_principal_from_request(Req({"x-api-key": "bob-token"}))
    anon = chat._responses_principal_from_request(Req({}))
    assert alice and bob and alice != bob and anon == ""
    assert "alice-token" not in alice

    chat._store_responses_state("resp_a", [{"role": "user", "content": "secret"}],
                                _payload("ok"), enabled=True, principal=alice)
    assert chat._load_responses_state("resp_a", alice)[0]["content"] == "secret"
    for other in (bob, anon):
        with pytest.raises(chat.HTTPException) as exc:
            chat._load_responses_state("resp_a", other)
        assert exc.value.status_code == 404


def test_b3_store_false_keeps_nothing(responses_state):
    chat = responses_state
    chat._store_responses_state("resp_x", [{"role": "user", "content": "hi"}], _payload("ok"), enabled=False)
    assert "resp_x" not in chat._responses_state_by_id


# ---------------------------------------------------------------------------
# S11 · 校验过的 DNS 结果即实际连接地址（DNS pinning），Host/SNI 保持原主机名
# ---------------------------------------------------------------------------

@pytest.fixture
def local_http_server():
    import http.server
    import threading

    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["host"] = self.headers.get("Host")
            body = b"pinned-ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen
    finally:
        server.shutdown()
        server.server_close()


def test_s11_fetch_connects_to_validated_address_not_fresh_dns(monkeypatch, local_http_server):
    from app.utils import remote_resource

    port, seen = local_http_server
    # 「校验时」解析结果 = 127.0.0.1（测试用放行）；系统 DNS 根本解析不了 .invalid，
    # 请求能成功只可能是连接用了校验阶段的地址，而不是重新解析。
    monkeypatch.setattr(remote_resource, "resolve_public_addresses", lambda host: ("127.0.0.1",))
    monkeypatch.setenv("NO_PROXY", "*")
    url = f"http://rebind-check.invalid:{port}/x"
    response = remote_resource.get_public_remote_resource(url, stream=False)
    assert response.status_code == 200 and response.content == b"pinned-ok"
    assert seen["host"] == f"rebind-check.invalid:{port}"
    # 调用结束后 pin 不残留
    assert remote_resource._current_pins() == {}


def test_s11_pin_is_scoped_to_call(monkeypatch, local_http_server):
    import requests
    from app.utils import remote_resource

    port, _seen = local_http_server
    monkeypatch.setenv("NO_PROXY", "*")
    url = f"http://scoped-check.invalid:{port}/"
    with remote_resource._pinned_dns(url, ("127.0.0.1",)):
        assert requests.get(url, timeout=5).status_code == 200
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(url, timeout=5)


# ---------------------------------------------------------------------------
# S10 · 图片比对 / C2PA 读取第三方图片：受限抓取器 + 字节 / 像素预算
# ---------------------------------------------------------------------------

class _TabRecorder:
    def __init__(self):
        self.js_calls = []
        self.url = "https://page.example/chat"

    def run_js(self, script, *args):
        self.js_calls.append(args)
        return ""


def test_s10_private_url_is_rejected_without_browser_fallback(monkeypatch):
    from app.utils import image_validation, remote_resource

    monkeypatch.setattr(remote_resource, "_resolve_addresses", lambda host: ("10.0.0.5",))
    called = []
    monkeypatch.setattr(remote_resource.requests, "get", lambda *a, **k: called.append(a))
    tab = _TabRecorder()
    assert image_validation.read_image_bytes(tab, "http://intranet.example/secret.png") == b""
    assert called == []
    # 只有取 location.href 的那次 run_js，没有浏览器 fetch 兜底
    assert all(not args for args in tab.js_calls)


def test_s10_remote_download_is_byte_limited(monkeypatch):
    from app.utils import image_validation

    class Resp:
        status_code = 200
        headers = {}
        closed = False

        def iter_content(self, chunk_size=65536):
            for _ in range(100):
                yield b"x" * 65536

        def close(self):
            Resp.closed = True

    monkeypatch.setattr(image_validation, "MAX_VALIDATION_IMAGE_BYTES", 200_000)
    monkeypatch.setattr(image_validation, "get_public_remote_resource", lambda *a, **k: Resp())
    assert image_validation._read_remote_image_limited("https://cdn.example/a.png", {}, 5) == b""
    assert Resp.closed


def test_s10_declared_oversize_is_skipped(monkeypatch):
    from app.utils import image_validation

    class Resp:
        status_code = 200
        headers = {"Content-Length": str(10 ** 9)}

        def iter_content(self, chunk_size=65536):
            raise AssertionError("must not read body")

        def close(self):
            return None

    monkeypatch.setattr(image_validation, "get_public_remote_resource", lambda *a, **k: Resp())
    assert image_validation._read_remote_image_limited("https://cdn.example/a.png", {}, 5) == b""


def test_s10_data_uri_budget_and_pixel_budget(monkeypatch):
    import base64 as b64
    import io

    from PIL import Image

    from app.utils import image_validation

    monkeypatch.setattr(image_validation, "MAX_VALIDATION_IMAGE_BYTES", 1000)
    big = "data:image/png;base64," + b64.b64encode(b"a" * 5000).decode()
    assert image_validation.read_image_bytes(None, big) == b""
    assert image_validation.read_uploaded_image_bytes(big) == b""

    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, format="PNG")
    monkeypatch.setattr(image_validation, "MAX_VALIDATION_IMAGE_PIXELS", 100)
    sig = image_validation.image_signatures(buf.getvalue())
    assert sig["sha256"] and sig["pixel_sha256"] is None and sig["dhash"] is None


# ---------------------------------------------------------------------------
# S4 · open-profile-url 鉴权 / 目标 URL 限制；handoff 代理的来源传递
# S5 · handoff 代理：连接/缓冲预算、chunked、Expect、CL+TE
# ---------------------------------------------------------------------------

def _open_profile_app(monkeypatch, calls):
    from fastapi import FastAPI

    from app.api import browser_routes

    monkeypatch.setattr(browser_routes, "open_url_in_profile",
                        lambda url, profile: calls.append(url) or {"success": True})
    app = FastAPI()
    app.include_router(browser_routes.router)
    return app


def _call(app, headers=None, client=("127.0.0.1", 5000), url="https://example.com/a"):
    import asyncio

    from httpx import ASGITransport, AsyncClient

    async def run():
        transport = ASGITransport(app=app, client=client)
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8199") as c:
            return await c.post("/api/browser/open-profile-url", headers=headers or {},
                                json={"url": url, "profile": {}})

    return asyncio.run(run())


@pytest.fixture
def no_dashboard_auth(monkeypatch):
    for name in ("DASHBOARD_AUTH_ENABLED", "DASHBOARD_AUTH_TOKEN", "UWAPI_PROXY_SECRET"):
        monkeypatch.delenv(name, raising=False)


def test_s4_open_profile_url_rejects_tunneled_and_remote(monkeypatch, no_dashboard_auth):
    calls = []
    app = _open_profile_app(monkeypatch, calls)
    assert _call(app).status_code == 200
    assert _call(app, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 403
    assert _call(app, headers={"CF-Connecting-IP": "203.0.113.9"}).status_code == 403
    assert _call(app, client=("203.0.113.9", 4000)).status_code == 403
    assert calls == ["https://example.com/a"]


def test_s4_open_profile_url_trusts_handoff_proxy_client_addr(monkeypatch, no_dashboard_auth):
    calls = []
    app = _open_profile_app(monkeypatch, calls)
    monkeypatch.setenv("UWAPI_PROXY_SECRET", "launch-secret")
    remote_via_proxy = {"X-UWA-Client-Addr": "203.0.113.9", "X-UWA-Proxy-Secret": "launch-secret"}
    local_via_proxy = {"X-UWA-Client-Addr": "127.0.0.1", "X-UWA-Proxy-Secret": "launch-secret"}
    forged = {"X-UWA-Client-Addr": "127.0.0.1", "X-UWA-Proxy-Secret": "guess"}
    nginx_in_front = dict(local_via_proxy, **{"X-Forwarded-For": "203.0.113.9"})
    assert _call(app, headers=remote_via_proxy).status_code == 403
    assert _call(app, headers=forged).status_code == 403
    assert _call(app, headers=nginx_in_front).status_code == 403
    assert _call(app, headers=local_via_proxy).status_code == 200


def test_s4_open_profile_url_token_and_target_restrictions(monkeypatch, no_dashboard_auth):
    calls = []
    app = _open_profile_app(monkeypatch, calls)
    for bad in ("http://127.0.0.1:8199/api/settings/backup", "http://localhost/x", "http://192.168.1.1/",
                "https://user:pw@example.com/", "file:///etc/passwd", "http://[::1]/"):
        assert _call(app, url=bad).status_code == 400, bad
    monkeypatch.setenv("DASHBOARD_AUTH_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_AUTH_TOKEN", "dash-token-123")
    assert _call(app, client=("203.0.113.9", 4000)).status_code == 401
    assert _call(app, client=("203.0.113.9", 4000),
                 headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert _call(app, client=("203.0.113.9", 4000),
                 headers={"Authorization": "Bearer dash-token-123"}).status_code == 200


# ---- handoff proxy ---------------------------------------------------------

@pytest.fixture
def handoff_proxy():
    import socket
    import threading

    import start

    received = []
    backend = socket.socket()
    backend.bind(("127.0.0.1", 0))
    backend.listen(16)
    stop = threading.Event()

    def serve():
        backend.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = backend.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(3)
                data = bytearray()
                try:
                    while True:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        data.extend(chunk)
                        if b"\r\n\r\n" in data and (data.endswith(b"0\r\n\r\n") or b"chunked" not in data.lower()):
                            head, _, body = bytes(data).partition(b"\r\n\r\n")
                            length = 0
                            for line in head.split(b"\r\n")[1:]:
                                if line.lower().startswith(b"content-length:"):
                                    length = int(line.split(b":", 1)[1])
                            if b"chunked" in head.lower() or len(body) >= length:
                                break
                except OSError:
                    pass
                received.append(bytes(data))
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    proxies = []

    def make(**kwargs):
        proxy = start._RestartHandoffProxy("127.0.0.1", 0, backend.getsockname()[1], **kwargs)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        proxies.append(proxy)
        return proxy

    try:
        yield make, received
    finally:
        for proxy in proxies:
            proxy.shutdown()
            proxy.server_close()
        stop.set()
        backend.close()


def _raw(port, data, read=True):
    import socket

    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        for part in (data if isinstance(data, list) else [data]):
            s.sendall(part)
        if not read:
            return b""
        out = bytearray()
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                out.extend(chunk)
        except OSError:
            pass
        return bytes(out)


def test_s4_proxy_strips_client_uwa_headers_and_injects_secret(handoff_proxy):
    make, received = handoff_proxy
    proxy = make(proxy_secret="launch-secret")
    port = proxy.server_address[1]
    resp = _raw(port, b"GET /x HTTP/1.1\r\nHost: a\r\nX-UWA-Client-Addr: 127.0.0.1\r\n"
                      b"X-UWA-Proxy-Secret: forged\r\nX-Forwarded-For: 203.0.113.9\r\n\r\n")
    assert resp.endswith(b"ok")
    req = received[-1].decode("latin-1")
    assert "forged" not in req
    assert req.count("X-UWA-Client-Addr:") == 1 and "X-UWA-Client-Addr: 127.0.0.1" in req
    assert "X-UWA-Proxy-Secret: launch-secret" in req
    assert "X-Forwarded-For: 203.0.113.9" in req  # 外部转发头保留给后端判定
    assert "Connection: close" in req


def test_s5_proxy_handles_chunked_and_expect(handoff_proxy):
    make, received = handoff_proxy
    port = make(proxy_secret="s").server_address[1]
    body = b"5\r\nhello\r\n6;ext=1\r\n world\r\n0\r\n\r\n"
    resp = _raw(port, [b"POST /c HTTP/1.1\r\nHost: a\r\nTransfer-Encoding: chunked\r\n\r\n", body])
    assert resp.endswith(b"ok")
    assert received[-1].endswith(body)

    import socket

    # 真实客户端行为：发完头等 100 再发体
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(b"POST /e HTTP/1.1\r\nHost: a\r\nContent-Length: 4\r\nExpect: 100-continue\r\n\r\n")
        interim = s.recv(64)
        assert interim == b"HTTP/1.1 100 Continue\r\n\r\n"
        s.sendall(b"abcd")
        resp = bytearray()
        while chunk := s.recv(65536):
            resp.extend(chunk)
    assert bytes(resp).endswith(b"ok")
    assert b"expect:" not in received[-1].lower() and received[-1].endswith(b"abcd")


def test_s5_proxy_rejects_smuggling_and_oversize(handoff_proxy):
    make, received = handoff_proxy
    proxy = make(max_buffer_bytes=1024 * 1024)
    port = proxy.server_address[1]
    before = len(received)
    assert _raw(port, b"POST / HTTP/1.1\r\nContent-Length: 3\r\nTransfer-Encoding: chunked\r\n\r\nabc") \
        .startswith(b"HTTP/1.1 400")
    assert _raw(port, b"POST / HTTP/1.1\r\nContent-Length: abc\r\n\r\n").startswith(b"HTTP/1.1 400")
    assert _raw(port, b"POST / HTTP/1.1\r\nTransfer-Encoding: gzip\r\n\r\n").startswith(b"HTTP/1.1 501")
    assert _raw(port, b"POST / HTTP/1.1\r\nContent-Length: 99999999999\r\n\r\n").startswith(b"HTTP/1.1 413")
    # 超过全局缓冲预算（1MiB）的单个请求 → 503，不会被缓冲
    assert _raw(port, b"POST / HTTP/1.1\r\nContent-Length: 2000000\r\n\r\n").startswith(b"HTTP/1.1 503")
    assert _raw(port, b"GET / HTTP/1.1\r\n" + b"X-Big: " + b"a" * 70000 + b"\r\n\r\n") \
        .startswith(b"HTTP/1.1 431")
    assert len(received) == before
    assert proxy._buffered_bytes == 0


def test_s5_proxy_connection_budget(handoff_proxy):
    import socket
    import time

    make, _received = handoff_proxy
    proxy = make(max_connections=2)
    port = proxy.server_address[1]
    held = [socket.create_connection(("127.0.0.1", port), timeout=5) for _ in range(2)]
    try:
        time.sleep(0.3)
        assert _raw(port, b"GET / HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 503")
    finally:
        for s in held:
            s.close()
    time.sleep(0.3)
    assert _raw(port, b"GET / HTTP/1.1\r\nHost: a\r\n\r\n").endswith(b"ok")


# ---------------------------------------------------------------------------
# S6 · 媒体路由可选鉴权 + 转码资源预算（并发上限 / 同键合并 / 源大小）
# ---------------------------------------------------------------------------

def test_s6_media_auth_policy():
    from app.utils.media_access import media_request_authorized as ok

    off = {}
    on = {"MEDIA_REQUIRE_AUTH": "true", "AUTH_TOKEN": "svc-token", "DASHBOARD_AUTH_TOKEN": "dash-token"}
    remote = "203.0.113.9"
    assert ok(remote, {}, off) is True  # 默认兼容：不强制
    assert ok(remote, {}, on) is False
    assert ok(remote, {"authorization": "Bearer svc-token"}, on) is True
    assert ok(remote, {"x-api-key": "svc-token"}, on) is True
    assert ok(remote, {"authorization": "Bearer dash-token"}, on) is True
    assert ok(remote, {"authorization": "Bearer nope"}, on) is False
    assert ok("127.0.0.1", {}, on) is True
    assert ok("127.0.0.1", {"x-forwarded-for": remote}, on) is False
    assert ok(remote, {}, {"MEDIA_REQUIRE_AUTH": "garbage"}) is False  # 非法值 fail-closed
    assert ok(remote, {"authorization": "Bearer "}, {"MEDIA_REQUIRE_AUTH": "1", "AUTH_TOKEN": ""}) is False


def test_s6_media_routes_require_auth_when_enabled(monkeypatch):
    import asyncio

    import main
    from httpx import ASGITransport, AsyncClient

    base = Path("download_images")
    base.mkdir(exist_ok=True)
    name = "_review_s6_ok.png"
    (base / name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    monkeypatch.setenv("MEDIA_REQUIRE_AUTH", "true")
    monkeypatch.setenv("AUTH_TOKEN", "svc-token-s6")
    try:
        async def run(client, headers=None):
            async with AsyncClient(transport=ASGITransport(app=main.app, client=client),
                                   base_url="http://127.0.0.1:8199") as c:
                return [
                    (await c.get(f"/media/{name}", headers=headers or {})).status_code,
                    (await c.get(f"/download_images/{name}", headers=headers or {})).status_code,
                ]

        remote = ("203.0.113.9", 4000)
        assert asyncio.run(run(remote)) == [401, 401]
        assert asyncio.run(run(remote, {"Authorization": "Bearer svc-token-s6"})) == [200, 200]
        assert asyncio.run(run(("127.0.0.1", 5000))) == [200, 200]
        monkeypatch.setenv("MEDIA_REQUIRE_AUTH", "false")
        assert asyncio.run(run(remote)) == [200, 200]
    finally:
        (base / name).unlink(missing_ok=True)


def test_s6_transcode_same_key_is_coalesced(tmp_path, monkeypatch):
    import threading
    import time
    from types import SimpleNamespace

    import main
    from app.utils.media_access import TranscodeGate

    src = tmp_path / "voice.wav"
    src.write_bytes(b"wav")
    monkeypatch.setattr(main.shutil, "which", lambda _n: "ffmpeg")
    monkeypatch.setattr(main, "_TRANSCODE_GATE", TranscodeGate(max_concurrency=4, queue_timeout=5))
    runs = []

    def fake_run(cmd, **_kw):
        runs.append(cmd)
        time.sleep(0.3)
        main.Path(cmd[-1]).write_bytes(b"converted")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    results = []
    threads = [threading.Thread(target=lambda: results.append(main._transcode_media(src, "mp3")))
               for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(results) == 5 and len(set(results)) == 1
    assert len(runs) == 1


def test_s6_transcode_global_limit_returns_503(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    import main
    from app.utils.media_access import TranscodeGate

    monkeypatch.setattr(main.shutil, "which", lambda _n: "ffmpeg")
    monkeypatch.setattr(main, "_TRANSCODE_GATE", TranscodeGate(max_concurrency=1, queue_timeout=0.2))
    release = threading.Event()
    started = threading.Event()

    def slow_run(cmd, **_kw):
        started.set()
        release.wait(5)
        main.Path(cmd[-1]).write_bytes(b"converted")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(main.subprocess, "run", slow_run)
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    t = threading.Thread(target=lambda: main._transcode_media(a, "mp3"))
    t.start()
    assert started.wait(5)
    with pytest.raises(main.HTTPException) as exc:
        main._transcode_media(b, "mp3")
    assert exc.value.status_code == 503
    release.set()
    t.join(5)


def test_s6_transcode_source_size_limit(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(main.shutil, "which", lambda _n: "ffmpeg")
    monkeypatch.setenv("MEDIA_TRANSCODE_MAX_SOURCE_MB", "0.001")
    src = tmp_path / "big.wav"
    src.write_bytes(b"x" * 5000)
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: pytest.fail("must not transcode"))
    with pytest.raises(main.HTTPException) as exc:
        main._transcode_media(src, "mp3")
    assert exc.value.status_code == 413


# ---------------------------------------------------------------------------
# S3 · 解析器安装：默认关闭 + 导入期无副作用静态约束 + 模块路径限制
# ---------------------------------------------------------------------------

_SAFE_PARSER = '''
"""demo parser"""
import re
from typing import Optional

from app.core.parsers.base import ResponseParser

_PATTERN = re.compile(r"data: (.*)")


class DemoParser(ResponseParser):
    NAME = "demo"
    PATTERNS = ("**/demo/**",)

    @classmethod
    def build(cls, value: Optional[str] = None) -> "DemoParser":
        return cls()

    def parse(self, raw):
        return {"text": raw}
'''


@pytest.mark.parametrize("snippet", [
    "import os\nos.system('touch /tmp/pwned')\nclass DemoParser: pass\n",
    "class DemoParser:\n    X = __import__('os').getcwd()\n",
    "import subprocess\nclass DemoParser:\n    def f(self, x=subprocess.run(['id'])):\n        pass\n",
    "def deco(f):\n    return f\n@deco\nclass DemoParser: pass\n",
    "class DemoParser(type('B', (), {})): pass\n",
    "try:\n    import os\nexcept Exception:\n    pass\nclass DemoParser: pass\n",
    "class DemoParser:\n    x = [open('/etc/passwd').read() for _ in range(1)]\n",
    "class DemoParser:\n    x = ().__class__.__bases__\n",
])
def test_s3_import_time_side_effects_rejected(snippet):
    import ast

    from app.services.parser_manager import ParserConfigManager, check_import_time_side_effects

    assert check_import_time_side_effects(ast.parse(snippet))
    with pytest.raises(ValueError):
        ParserConfigManager._validate_parser_source(snippet, "DemoParser", "demo.py")


def test_s3_safe_parser_and_builtin_parsers_pass():
    import ast
    import pathlib

    from app.services.parser_manager import ParserConfigManager, check_import_time_side_effects

    ParserConfigManager._validate_parser_source(_SAFE_PARSER, "DemoParser", "demo.py")
    for path in pathlib.Path("app/core/parsers").glob("*_parser.py"):
        assert check_import_time_side_effects(ast.parse(path.read_text(encoding="utf-8"))) == [], path.name


def test_s3_install_disabled_by_default(monkeypatch, tmp_path):
    from app.services import parser_manager as pm

    monkeypatch.delenv("PARSER_INSTALL_ENABLED", raising=False)
    mgr = object.__new__(pm.ParserConfigManager)
    mgr._parsers_config, mgr._config = {}, {}
    monkeypatch.setattr(mgr, "_normalize_parser_package",
                        lambda payload: pytest.fail("must not even parse the package"), raising=False)
    with pytest.raises(PermissionError):
        mgr.install_parser_package({"parser_id": "demo", "source_code": _SAFE_PARSER})
    monkeypatch.setenv("PARSER_INSTALL_ENABLED", "garbage")
    with pytest.raises(PermissionError):
        mgr.install_parser_package({"parser_id": "demo", "source_code": _SAFE_PARSER})


def test_s3_config_module_must_be_inside_parser_package(monkeypatch):
    from app.services import parser_manager as pm

    loaded = []
    monkeypatch.setattr(pm.ParserRegistry, "load_from_module",
                        lambda module, cls, pid: loaded.append(module))
    mgr = object.__new__(pm.ParserConfigManager)
    for bad in ("os", "app.core.parsers", "app.core.parsers.x.y", "app.core.parsersevil.x", "subprocess"):
        with pytest.raises(ValueError):
            mgr._load_parser_entry("p", {"module": bad, "class": "C"})
    mgr._load_parser_entry("p", {"module": "app.core.parsers.mimo_runtime_parser", "class": "MimoParser"})
    assert loaded == ["app.core.parsers.mimo_runtime_parser"]


# ---------------------------------------------------------------------------
# H12 · 网络事件 URL 正则：25ms 求值预算 + 模式/输入长度上限
# ---------------------------------------------------------------------------

def _results_mixin():
    from app.services import command_engine_results as cer

    cls = next(v for v in vars(cer).values()
               if isinstance(v, type) and hasattr(v, "_matches_url_rule") and v.__module__ == cer.__name__)
    return object.__new__(cls)


def test_h12_catastrophic_regex_is_time_bounded():
    m = _results_mixin()
    url = "https://x.example/" + "a" * 5000 + "!"
    started = time.perf_counter()
    assert m._matches_url_rule(url, r"(a+)+$", "regex") is False
    assert m._matches_url_rule(url, r"(a|aa)*c", "regex") is False
    assert time.perf_counter() - started < 1.0


def test_h12_regex_semantics_preserved():
    m = _results_mixin()
    url = "https://chat.example.com/api/Conversation?id=1"
    assert m._matches_url_rule(url, r"/api/conversation", "regex") is True
    assert m._matches_url_rule(url, r"^https://chat\.example\.com/api/\w+", "regex") is True
    assert m._matches_url_rule(url, r"/api/other", "regex") is False
    # 无效正则 → 通配回退 → 关键词
    assert m._matches_url_rule(url, "*.example.com/api*", "regex") is True
    assert m._matches_url_rule(url, "*.other.com/api*", "regex") is False
    assert m._matches_url_rule(url, "conversation", "keyword") is True
    # 超长模式按关键词处理，不交给正则引擎
    assert m._matches_url_rule(url, "(" * 600, "regex") is False
