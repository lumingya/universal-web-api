"""P0-4: global network listener only captures API-type resources."""

from __future__ import annotations

import http.server
import threading

import pytest

from app.core.tab_pool_parts.network import _GlobalNetworkInterceptionManager as GNM
from tests._real_browser import launch_page


def _mgr(res_types=None):
    return GNM(lambda _sid: None, lambda: False, res_types=res_types)


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, GNM.DEFAULT_RES_TYPES),
        ("", GNM.DEFAULT_RES_TYPES),
        ("  ", GNM.DEFAULT_RES_TYPES),
        ("*", True),
        ("ALL", True),
        (True, True),
        ("XHR, Fetch;EventSource", ("XHR", "Fetch", "EventSource")),
        (["XHR", " ", "Fetch"], ("XHR", "Fetch")),
        (",,", GNM.DEFAULT_RES_TYPES),
        (123, GNM.DEFAULT_RES_TYPES),
    ],
)
def test_parse_res_types(value, expected):
    assert GNM.parse_res_types(value) == tuple(expected) if expected is not True else GNM.parse_res_types(value) is True


def test_default_manager_filters_to_api_types():
    assert _mgr()._res_types == ("XHR", "Fetch", "EventSource", "Document")
    assert _mgr("*")._res_types is True


class _Listen:
    def __init__(self, accept_res_type=True):
        self.accept_res_type = accept_res_type
        self.calls = []
        self._res_type = True

    def start(self, targets=None, **kwargs):
        if kwargs and not self.accept_res_type:
            raise TypeError("start() got an unexpected keyword argument 'res_type'")
        self.calls.append((targets, kwargs))


class _Tab:
    def __init__(self, listen):
        self.listen = listen


def test_start_listen_passes_res_type():
    listen = _Listen()
    _mgr()._start_listen(_Tab(listen))
    assert listen.calls == [("http", {"res_type": GNM.DEFAULT_RES_TYPES})]


def test_start_listen_falls_back_for_listeners_without_res_type():
    listen = _Listen(accept_res_type=False)
    _mgr()._start_listen(_Tab(listen))
    assert listen.calls == [("http", {})]


def test_reset_listener_res_type_restores_unfiltered():
    listen = _Listen()
    listen._res_type = {"XHR"}
    GNM._reset_listener_res_type(listen)
    assert listen._res_type is True
    GNM._reset_listener_res_type(object())  # tolerant of foreign objects


# --------------------------------------------------------------------------- #
# real browser: DrissionPage accepts the CDP type names and drops the rest
# --------------------------------------------------------------------------- #

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/img"):
            self._send(_PNG, "image/png")
        elif self.path.startswith("/js"):
            self._send(b"window.__js=1;", "application/javascript")
        elif self.path.startswith("/api"):
            self._send(b'{"ok":1}', "application/json")
        else:
            self._send(b"<html><body>page</body></html>", "text/html")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._send(b'{"posted":1}', "application/json")


@pytest.fixture(scope="module")
def http_base():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def real_page():
    yield from launch_page()


def test_real_browser_default_res_types_skip_images_and_scripts(real_page, http_base):
    page = real_page
    page.get(f"{http_base}/index")
    page.listen.start(f"{http_base}/", res_type=GNM.parse_res_types(None))
    try:
        page.run_js(
            f"""
            var i = new Image(); i.src = '{http_base}/img?1'; document.body.appendChild(i);
            var s = document.createElement('script'); s.src = '{http_base}/js?1'; document.body.appendChild(s);
            fetch('{http_base}/api/fetch', {{method: 'POST', body: 'x'}});
            var x = new XMLHttpRequest(); x.open('GET', '{http_base}/api/xhr'); x.send();
            """
        )
        seen = set()
        for packet in page.listen.steps(timeout=4):
            seen.add(packet.url.split(http_base, 1)[-1])
            if {"/api/fetch", "/api/xhr"} <= seen:
                break
        # allow stragglers (image/script) a moment to be (not) reported
        for packet in page.listen.steps(timeout=0.8):
            seen.add(packet.url.split(http_base, 1)[-1])
    finally:
        page.listen.stop()
        GNM._reset_listener_res_type(page.listen)
    assert {"/api/fetch", "/api/xhr"} <= seen, seen
    assert not any(p.startswith(("/img", "/js")) for p in seen), seen
    assert page.listen._res_type is True
