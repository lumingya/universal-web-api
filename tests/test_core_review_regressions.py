import base64
import asyncio
import io
import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import background_image_downloader as downloader_module
from app.core.background_image_downloader import (
    BackgroundImageDownloader,
    get_image_download_partition,
    normalize_remote_image_url,
)
from app.core.browser import media as browser_media_module
from app.core.browser.media import BrowserMediaMixin
from app.core.network_monitor import NetworkMonitor
from app.core.parsers.lmarena_parser import LmarenaParser
from app.core.parsers.lmarena_battle_side_parser import (
    LmarenaBattleSideLeftParser,
    LmarenaBattleWinnerParser,
)
from app.core.parsers.lmarena_image_side_left_parser import LmarenaImageSideLeftParser
from app.core.parsers.lmarena_image_side_right_parser import LmarenaImageSideRightParser
from app.core.parsers.lmarena_side_left_parser import LmarenaSideLeftParser
from app.core.tab_pool_parts.network import (
    _GlobalNetworkInterceptionManager,
    _GlobalNetworkWorker,
)
from app.core.tab_pool_parts.manager import TabPoolManager
from app.core.tab_pool_parts.session import TabSession
from app.services import request_lifecycle
from app.services.request_lifecycle import (
    TrackedWorkerExecutionCancelled,
    run_tracked_blocking_call,
)
from app.services.request_manager import RequestContext
from app.utils import image_handler
from app.utils.image_handler import extract_images_from_messages
from app.utils.remote_resource import UnsafeRemoteResourceError, validate_public_remote_url
from app.utils.site_url import normalize_exact_tab_url


ROOT = Path(__file__).resolve().parents[1]


def _arena_text(prefix: str, text: str) -> str:
    return f"{prefix}:{json.dumps(text)}"


def test_cancelled_tracked_worker_consumes_late_exception(monkeypatch):
    release_worker = threading.Event()
    consumed = []
    original_consumer = request_lifecycle._consume_worker_future_exception

    def recording_consumer(future):
        consumed.append(future)
        original_consumer(future)

    monkeypatch.setattr(
        request_lifecycle,
        "_consume_worker_future_exception",
        recording_consumer,
    )

    async def exercise():
        ctx = RequestContext(request_id="req-test-cancelled-worker")
        ctx.mark_running("arena_1")
        worker_state = {}

        def worker():
            release_worker.wait(timeout=2.0)
            raise RuntimeError("请求已取消")

        task = asyncio.create_task(
            run_tracked_blocking_call(
                worker,
                ctx=ctx,
                worker_state=worker_state,
                label="cancelled-worker-test",
                poll_timeout=0.01,
            )
        )
        while worker_state.get("thread") is None:
            await asyncio.sleep(0)

        ctx.request_cancel("client_disconnected")
        with pytest.raises(TrackedWorkerExecutionCancelled):
            await task

        release_worker.set()
        deadline = time.monotonic() + 1.0
        while not consumed and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

        assert len(consumed) == 1
        assert isinstance(consumed[0].exception(), RuntimeError)
        assert worker_state.get("thread") is None

    asyncio.run(exercise())


def test_lmarena_incomplete_empty_capture_falls_back_to_dom():
    parser = LmarenaParser()
    result = parser.parse_chunk(
        'a2:[{"type":"routed_model","organization":"openai"}]\n'
        'a2:[{"type":"heartbeat"}]\n'
    )
    monitor = NetworkMonitor.__new__(NetworkMonitor)
    monitor.parser = parser
    monitor._total_chunks = 0
    monitor._last_stream_media_items = []

    assert result["done"] is False
    assert result["images"] == []
    assert monitor._should_fallback_on_incomplete_empty_capture() is True


def test_lmarena_incomplete_capture_keeps_network_media_result():
    monitor = NetworkMonitor.__new__(NetworkMonitor)
    monitor.parser = LmarenaParser()
    monitor._total_chunks = 0
    monitor._last_stream_media_items = [
        {"url": "https://example.test/generated.png"}
    ]

    assert monitor._should_fallback_on_incomplete_empty_capture() is False


def test_lmarena_extracts_generated_image_directly_from_network_stream():
    parser = LmarenaParser()
    image_url = "https://messages-prod.example.r2.cloudflarestorage.com/result.png"
    result = parser.parse_chunk(
        "a2:" + json.dumps([{"type": "image", "image": image_url, "mimeType": "image/png"}]) + "\n"
        'ad:{"finishReason":"stop"}\n'
    )

    assert result["done"] is True
    assert [item["url"] for item in result["images"]] == [image_url]


def test_lmarena_image_side_parsers_keep_image_only_completed_results():
    cases = [
        (LmarenaImageSideLeftParser(), "a2", "ad"),
        (LmarenaImageSideRightParser(), "b2", "bd"),
    ]

    for parser, image_prefix, done_prefix in cases:
        result = parser.parse_chunk(
            f'{image_prefix}:[{{"type":"image","image":"https://example.test/result.png"}}]\n'
            f'{done_prefix}:{{"finishReason":"stop"}}\n'
        )

        assert result["content"] == ""
        assert result["done"] is True
        assert len(result["images"]) == 1


def test_arena_side_parsers_merge_rolling_overlap_without_duplicates():
    cases = [
        (LmarenaSideLeftParser(), "a0"),
        (LmarenaImageSideRightParser(), "b0"),
        (LmarenaBattleSideLeftParser(), "a0"),
    ]

    for parser, prefix in cases:
        first = parser.parse_chunk(_arena_text(prefix, "hello world"))
        second = parser.parse_chunk(_arena_text(prefix, "world again"))

        assert first["content"] == "hello world"
        assert second["content"] == " again"


def test_arena_winner_buffer_merges_rolling_overlap():
    parser = LmarenaBattleWinnerParser()

    parser.parse_chunk(_arena_text("a0", "hello world"))
    parser.parse_chunk(_arena_text("a0", "world again"))
    completed = parser.parse_chunk('ad:{"finishReason":"stop"}')

    assert completed["content"] == "hello world again"


def test_stopping_network_worker_stays_registered_until_thread_exits():
    release_worker = threading.Event()

    def run_worker():
        release_worker.wait(timeout=2.0)

    thread = threading.Thread(target=run_worker, daemon=True)
    thread.start()
    worker = _GlobalNetworkWorker("session-1", thread, threading.Event())

    manager = _GlobalNetworkInterceptionManager.__new__(_GlobalNetworkInterceptionManager)
    manager._lock = threading.RLock()
    manager._workers = {"session-1": worker}
    manager._stop_join_timeout = 0.01
    manager._get_session = lambda _session_id: None

    assert manager.stop_for_session("session-1", join=True) is False
    assert manager._workers["session-1"] is worker

    release_worker.set()
    thread.join(timeout=1.0)
    assert manager.stop_for_session("session-1", join=True) is True
    assert "session-1" not in manager._workers


def test_normalize_exact_url_does_not_raise_for_invalid_port():
    invalid = "https://example.com:not-a-port/path"
    assert normalize_exact_tab_url(invalid) == invalid


def test_normalize_exact_url_preserves_ipv6_brackets():
    assert normalize_exact_tab_url("HTTP://[2001:DB8::1]:8080/path") == (
        "http://[2001:db8::1]:8080/path"
    )


def test_image_message_extraction_ignores_non_mapping_items(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert extract_images_from_messages([None, "text", 42, {"content": "hello"}]) == []


def test_arena_side_parsers_keep_disjoint_incremental_chunks():
    cases = [
        (LmarenaSideLeftParser(), "a0"),
        (LmarenaImageSideRightParser(), "b0"),
        (LmarenaBattleSideLeftParser(), "a0"),
    ]
    for parser, prefix in cases:
        assert parser.parse_chunk(_arena_text(prefix, "hello"))["content"] == "hello"
        assert parser.parse_chunk(_arena_text(prefix, " world"))["content"] == " world"


def test_arena_winner_keeps_disjoint_incremental_chunks():
    parser = LmarenaBattleWinnerParser()
    parser.parse_chunk(_arena_text("a0", "hello"))
    parser.parse_chunk(_arena_text("a0", " world"))
    assert parser.parse_chunk('ad:{"finishReason":"stop"}')["content"] == "hello world"


def test_tutorial_catalog_fields_are_escaped_and_guide_restricts_navigation_scheme():
    tutorial = (ROOT / "static/js/tutorial-page.js").read_text(encoding="utf-8")
    guide = (ROOT / "static/controlled-browser-guide.html").read_text(encoding="utf-8")
    assert 'data-site-url="${escapeDocsHtml(site.url)}"' in tutorial
    assert "${escapeDocsHtml(site.name)}" in tutorial
    assert "${escapeDocsHtml(site.id)}" in tutorial
    assert '<span class="site-card-name">${site.name}</span>' not in tutorial
    assert "function normalizeHttpUrl(value)" in guide
    assert "['http:', 'https:'].includes(parsed.protocol)" in guide


def test_image_preset_late_response_cannot_overwrite_new_domain():
    panel_file = ROOT / "static/js/components/panels/ImageConfigPanel.js"
    script = r"""
const fs = require('fs');
const vm = require('vm');
const pending = {};
const context = { window: {}, console, fetch(url) {
    return new Promise(resolve => { pending[url] = data => resolve({ ok: true, json: async () => data }); });
} };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const check = context.window.ImageConfigPanel.methods.checkCurrentPreset;
const state = { currentDomain: 'old.example', currentPreset: null, imagePresetRequestSeq: 0,
    buildAuthHeaders() { return {}; } };
(async () => {
    const oldRequest = check.call(state);
    state.currentDomain = 'new.example';
    const newRequest = check.call(state);
    pending['/api/sites/new.example/image-preset']({ available: true, name: 'new' });
    await newRequest;
    pending['/api/sites/old.example/image-preset']({ available: true, name: 'old' });
    await oldRequest;
    if (!state.currentPreset || state.currentPreset.name !== 'new') throw new Error('stale preset overwrite');
})().catch(error => { console.error(error); process.exit(1); });
"""
    subprocess.run(["node", "-e", script, str(panel_file)], cwd=ROOT, check=True, capture_output=True, text=True)


def test_invalid_base64_image_payload_is_not_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = base64.b64encode(b"<script>alert(1)</script>").decode("ascii")
    messages = [{"content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{payload}"}}]}]
    assert extract_images_from_messages(messages) == []
    assert list((tmp_path / "temp" / "image_inputs").iterdir()) == []


def test_remote_image_url_normalization_rejects_credentials_and_bad_ports():
    assert normalize_remote_image_url("https://user:secret@example.com/a.png") == ""
    assert normalize_remote_image_url("https://example.com:not-a-port/a.png") == ""
    assert normalize_remote_image_url("HTTPS://[2001:DB8::1]:8443/a.png#preview") == "https://[2001:db8::1]:8443/a.png"


class _FakeImageResponse:
    def __init__(self, body: bytes, content_type: str):
        self.status_code = 200
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
        self._body = body

    def iter_content(self, chunk_size=65536):
        yield self._body

    def close(self):
        return None


@pytest.mark.parametrize(
    ("body", "content_type", "expected_error"),
    [
        (b"<html>not an image</html>", "image/png", "invalid_image_payload"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml", "unsafe_image_type"),
    ],
)
def test_background_downloader_rejects_active_or_fake_image_payloads(
    tmp_path, monkeypatch, body, content_type, expected_error
):
    monkeypatch.setattr(
        downloader_module.requests,
        "get",
        lambda *args, **kwargs: _FakeImageResponse(body, content_type),
    )
    monkeypatch.setattr(
        "app.utils.remote_resource.resolve_public_addresses",
        lambda _hostname: ("93.184.216.34",),
    )
    downloader = BackgroundImageDownloader(tmp_path, min_bytes=1)
    try:
        downloader.start_download("https://example.com/payload.svg")
        result = downloader.get_download_result("https://example.com/payload.svg", wait=True, timeout=2)
        assert result is not None and result["status"] == "failed"
        assert expected_error in str(result["error"])
        assert list(tmp_path.iterdir()) == []
    finally:
        downloader.shutdown()


def test_background_downloader_honors_per_request_image_size_limit(tmp_path, monkeypatch):
    body = b"\x89PNG\r\n\x1a\n" + (b"\0" * 1024)
    response = _FakeImageResponse(body, "image/png")
    response.headers["Content-Length"] = str(11 * 1024 * 1024)
    monkeypatch.setattr(
        downloader_module,
        "get_public_remote_resource",
        lambda *_args, **_kwargs: response,
    )

    downloader = BackgroundImageDownloader(tmp_path)
    try:
        downloader.start_download(
            "https://example.com/large.png",
            max_bytes=20 * 1024 * 1024,
        )
        result = downloader.get_download_result(
            "https://example.com/large.png",
            wait=True,
            timeout=2,
        )
        assert result is not None and result["status"] == "done"
        assert result["byte_size"] == len(body)
    finally:
        downloader.shutdown()


def test_image_input_rejects_local_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    local_image = tmp_path / "private.png"
    local_image.write_bytes(b"not relevant")
    messages = [{
        "content": [{"type": "image_url", "image_url": {"url": str(local_image)}}]
    }]

    assert extract_images_from_messages(messages) == []


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/image.png",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/image.png",
    "http://10.0.0.8/image.png",
])
def test_remote_image_policy_rejects_non_public_addresses(url):
    with pytest.raises(UnsafeRemoteResourceError):
        validate_public_remote_url(url)


def test_r2_download_allows_https_tun_fake_ip_without_weakening_public_validator(monkeypatch):
    from app.utils import remote_resource

    url = "https://messages-prod.example.r2.cloudflarestorage.com/image.png"
    calls = []

    class Response:
        status_code = 200
        headers = {}

    monkeypatch.setattr(
        remote_resource,
        "_resolve_addresses",
        lambda _host: ("198.18.0.34",),
    )
    monkeypatch.setattr(
        remote_resource.requests,
        "get",
        lambda target, **kwargs: calls.append((target, kwargs)) or Response(),
    )

    with pytest.raises(UnsafeRemoteResourceError):
        validate_public_remote_url(url)

    cookies = {"session": "secret"}
    response = remote_resource.get_public_remote_resource(
        url,
        cookies=cookies,
        headers={"Referer": "https://gemini.google.com/app"},
        credential_origin_url=url,
    )
    assert response.status_code == 200
    assert calls[0][0] == url
    assert calls[0][1]["cookies"] is cookies
    assert calls[0][1]["headers"]["Referer"] == "https://gemini.google.com/app"


def test_google_contribution_download_allows_https_tun_fake_ip(monkeypatch):
    from app.utils import remote_resource

    url = "https://contribution.usercontent.google.com/download?filename=video.mp4"
    calls = []

    class Response:
        status_code = 200
        headers = {"Content-Type": "video/mp4"}

    monkeypatch.setattr(
        remote_resource,
        "_resolve_addresses",
        lambda _host: ("198.18.0.202",),
    )
    monkeypatch.setattr(
        remote_resource.requests,
        "get",
        lambda target, **kwargs: calls.append((target, kwargs)) or Response(),
    )

    response = remote_resource.get_public_remote_resource(url)
    assert response.status_code == 200
    assert calls[0][0] == url


def test_gemini_video_download_scopes_cookies_to_google_contribution(
    tmp_path, monkeypatch
):
    calls = []

    class Response:
        status_code = 200
        headers = {"Content-Type": "video/mp4"}

        def iter_content(self, chunk_size=65536):
            yield b"valid-mp4"

        def close(self):
            return None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        browser_media_module,
        "build_image_download_request_context",
        lambda _tab, accept="*/*": (
            {"session": "secret"},
            {"Referer": "https://gemini.google.com/app", "Accept": accept},
        ),
    )
    monkeypatch.setattr(
        browser_media_module,
        "get_public_remote_resource",
        lambda url, **kwargs: calls.append((url, kwargs)) or Response(),
    )

    url = "https://contribution.usercontent.google.com/download?filename=video.mp4"
    result = BrowserMediaMixin()._persist_remote_media_urls_to_local(
        [{"kind": "url", "media_type": "video", "url": url}],
        tab=object(),
    )

    assert result[0]["url"].startswith("/media/")
    assert calls[0][1]["credential_origin_url"] == url


@pytest.mark.parametrize(
    ("url", "addresses"),
    [
        ("http://bucket.r2.cloudflarestorage.com/image.png", ("198.18.0.34",)),
        ("https://untrusted.example/image.png", ("198.18.0.34",)),
        (
            "https://bucket.r2.cloudflarestorage.com/image.png",
            ("198.18.0.34", "127.0.0.1"),
        ),
    ],
)
def test_remote_fetch_rejects_unsafe_fake_ip_exceptions(monkeypatch, url, addresses):
    from app.utils import remote_resource

    monkeypatch.setattr(remote_resource, "_resolve_addresses", lambda _host: addresses)

    with pytest.raises(UnsafeRemoteResourceError):
        remote_resource.get_public_remote_resource(url)


def test_image_screenshot_fallback_matches_stream_url_across_battle_sides(tmp_path, monkeypatch):
    target_url = "https://cdn.example/left/generated.png?signature=new"
    wrong = _FakeDomImage("https://cdn.example/right/old.png")
    correct = _FakeDomImage("https://cdn.example/left/generated.png?signature=dom")
    last_element = SimpleNamespace(eles=lambda *_args, **_kwargs: [wrong])
    tab = SimpleNamespace(eles=lambda *_args, **_kwargs: [wrong, correct])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        browser_media_module,
        "build_image_download_request_context",
        lambda _tab: ({}, {}),
    )
    monkeypatch.setattr(
        browser_media_module,
        "build_image_download_partition",
        lambda *_args: "test",
    )
    monkeypatch.setattr(
        browser_media_module.background_image_downloader,
        "get_download_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        browser_media_module.background_image_downloader,
        "register_downloaded_file",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        browser_media_module,
        "get_public_remote_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("download failed")),
    )

    localized = BrowserMediaMixin()._try_screenshot_images_to_local(
        tab,
        last_element,
        [{"kind": "url", "url": target_url, "media_type": "image"}],
        {"selector": "img", "background_download_wait_seconds": 0},
    )

    assert wrong.screenshot_paths == []
    assert len(correct.screenshot_paths) == 1
    assert localized[0]["source"] == "local_file"


def test_image_screenshot_fallback_waits_for_matching_dom_image_to_finish_loading(tmp_path, monkeypatch):
    target_url = "https://cdn.example/generated.png?signature=stream"
    image = _FakeDomImage(
        "https://cdn.example/generated.png?signature=dom",
        ready_states=[False, True, True],
    )
    last_element = SimpleNamespace(eles=lambda *_args, **_kwargs: [image])
    tab = SimpleNamespace(eles=lambda *_args, **_kwargs: [])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(browser_media_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        browser_media_module,
        "build_image_download_request_context",
        lambda _tab: ({}, {}),
    )
    monkeypatch.setattr(
        browser_media_module,
        "build_image_download_partition",
        lambda *_args: "test",
    )
    monkeypatch.setattr(
        browser_media_module.background_image_downloader,
        "get_download_result",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        browser_media_module.background_image_downloader,
        "register_downloaded_file",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        browser_media_module,
        "get_public_remote_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("download failed")),
    )

    localized = BrowserMediaMixin()._try_screenshot_images_to_local(
        tab,
        last_element,
        [{"kind": "url", "url": target_url, "media_type": "image"}],
        {
            "selector": "img",
            "background_download_wait_seconds": 0,
            "screenshot_ready_wait_seconds": 1,
            "screenshot_ready_poll_seconds": 0.05,
        },
    )

    assert len(image.screenshot_paths) == 1
    assert localized[0]["source"] == "local_file"


def test_image_screenshot_fallback_preserves_remote_url_when_dom_image_never_loads(tmp_path, monkeypatch):
    target_url = "https://cdn.example/generated.png?signature=stream"
    image = _FakeDomImage(
        "https://cdn.example/generated.png?signature=dom",
        ready_states=[False],
    )
    last_element = SimpleNamespace(eles=lambda *_args, **_kwargs: [image])
    tab = SimpleNamespace(eles=lambda *_args, **_kwargs: [])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        browser_media_module,
        "build_image_download_request_context",
        lambda _tab: ({}, {}),
    )
    monkeypatch.setattr(
        browser_media_module,
        "build_image_download_partition",
        lambda *_args: "test",
    )
    monkeypatch.setattr(
        browser_media_module.background_image_downloader,
        "get_download_result",
        lambda *_args, **_kwargs: None,
    )
    register_calls = []
    monkeypatch.setattr(
        browser_media_module.background_image_downloader,
        "register_downloaded_file",
        lambda *args, **kwargs: register_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        browser_media_module,
        "get_public_remote_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("download failed")),
    )

    localized = BrowserMediaMixin()._try_screenshot_images_to_local(
        tab,
        last_element,
        [{"kind": "url", "url": target_url, "media_type": "image"}],
        {
            "selector": "img",
            "background_download_wait_seconds": 0,
            "screenshot_ready_wait_seconds": 0,
        },
    )

    assert image.screenshot_paths == []
    assert register_calls == []
    assert localized[0]["url"] == target_url
    assert localized[0].get("source") != "local_file"
    assert list((tmp_path / "download_images").iterdir()) == []


def test_image_localization_does_not_wait_or_duplicate_pending_background_download(
    tmp_path,
    monkeypatch,
):
    target_url = "https://cdn.example/generated.png?signature=stream"
    last_element = SimpleNamespace(eles=lambda *_args, **_kwargs: [])
    tab = SimpleNamespace(eles=lambda *_args, **_kwargs: [])
    foreground_calls = []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        browser_media_module,
        "build_image_download_request_context",
        lambda _tab: ({}, {}),
    )
    monkeypatch.setattr(
        browser_media_module,
        "build_image_download_partition",
        lambda *_args: "test",
    )
    monkeypatch.setattr(
        browser_media_module.background_image_downloader,
        "get_download_result",
        lambda *_args, **_kwargs: {"status": "downloading"},
    )
    monkeypatch.setattr(
        browser_media_module,
        "get_public_remote_resource",
        lambda *_args, **_kwargs: foreground_calls.append(True),
    )

    localized = BrowserMediaMixin()._try_screenshot_images_to_local(
        tab,
        last_element,
        [{"kind": "url", "url": target_url, "media_type": "image"}],
        {"selector": "img", "background_download_wait_seconds": 1},
    )

    assert localized[0]["url"] == target_url
    assert foreground_calls == []


class _FakeDomImage:
    def __init__(self, src, ready_states=None):
        self.src = src
        self.screenshot_paths = []
        self.ready_states = list(ready_states or [True])
        self.ready_probe_count = 0

    def run_js(self, script):
        if "natural_width" in script and "natural_height" in script:
            state_index = min(self.ready_probe_count, len(self.ready_states) - 1)
            ready = bool(self.ready_states[state_index])
            self.ready_probe_count += 1
            return {
                "src": self.src,
                "complete": ready,
                "natural_width": 1024 if ready else 0,
                "natural_height": 1024 if ready else 0,
            }
        return self.src

    def attr(self, _name):
        return self.src

    @property
    def link(self):
        return self.src

    def get_screenshot(self, path):
        self.screenshot_paths.append(path)
        Path(path).write_bytes(b"screenshot")


def test_image_validation_rejects_excessive_pixel_budget(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(image_handler, "MAX_IMAGE_PIXELS", 3)
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    messages = [{
        "content": [{
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{payload}"},
        }]
    }]

    assert extract_images_from_messages(messages) == []


def test_background_cache_is_partitioned_by_browser_identity(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    downloader = BackgroundImageDownloader(tmp_path, min_bytes=1)
    try:
        downloader.register_downloaded_file(
            "https://example.com/image.png",
            local_path=first,
            partition_key="profile-a",
        )
        downloader.register_downloaded_file(
            "https://example.com/image.png",
            local_path=second,
            partition_key="profile-b",
        )

        assert downloader.get_download_result(
            "https://example.com/image.png", partition_key="profile-a"
        )["local_path"] == str(first)
        assert downloader.get_download_result(
            "https://example.com/image.png", partition_key="profile-b"
        )["local_path"] == str(second)
        assert downloader.get_download_result("https://example.com/image.png") is None
    finally:
        downloader.shutdown()


def test_signed_r2_images_share_a_download_partition_across_browser_contexts():
    url = (
        "https://bucket.r2.cloudflarestorage.com/result.png?"
        "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=signature"
    )

    assert get_image_download_partition(url, {"one": "a"}, {"Referer": "https://first.example"}) == "signed-public"
    assert get_image_download_partition(url, {"two": "b"}, {"Referer": "https://second.example"}) == "signed-public"


def test_non_public_image_downloads_remain_partitioned_by_browser_identity():
    url = "https://example.com/image.png"

    assert get_image_download_partition(url, {"one": "a"}, {"Referer": "https://first.example"}) != get_image_download_partition(
        url,
        {"two": "b"},
        {"Referer": "https://second.example"},
    )


def test_cross_origin_media_fetch_drops_cookies_and_referer(monkeypatch):
    from requests.cookies import RequestsCookieJar
    from app.utils import remote_resource

    calls = []

    class Response:
        status_code = 200
        headers = {}

        def close(self):
            return None

    monkeypatch.setattr(remote_resource, "resolve_public_addresses", lambda _host: ("93.184.216.34",))
    monkeypatch.setattr(
        remote_resource.requests,
        "get",
        lambda url, **kwargs: calls.append((url, kwargs)) or Response(),
    )
    cookies = RequestsCookieJar()
    cookies.set("session", "secret", domain="source.example", path="/")

    remote_resource.get_public_remote_resource(
        "https://cdn.example/image.png",
        cookies=cookies,
        headers={"Referer": "https://source.example/chat", "Authorization": "Bearer secret"},
        credential_origin_url="https://source.example/chat",
    )

    assert calls[0][1]["cookies"] is None
    assert "Referer" not in calls[0][1]["headers"]
    assert "Authorization" not in calls[0][1]["headers"]


def test_cancelled_async_acquire_releases_late_session():
    manager = TabPoolManager.__new__(TabPoolManager)
    manager._acquire_executor = None
    manager._acquire_executor_lock = threading.Lock()
    manager._maintenance_executor = None
    worker_started = threading.Event()
    worker_finish = threading.Event()
    released = threading.Event()
    release_calls = []

    def acquire_late():
        worker_started.set()
        worker_finish.wait(timeout=2)
        return SimpleNamespace(id="tab-1")

    def release(tab_id, **kwargs):
        release_calls.append((tab_id, kwargs))
        released.set()
        return True

    manager.release = release

    async def scenario():
        task = asyncio.create_task(manager._run_cancellable_acquire(acquire_late, "req-1"))
        await asyncio.to_thread(worker_started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        worker_finish.set()
        await asyncio.to_thread(released.wait, 1)

    asyncio.run(scenario())

    assert release_calls == [(
        "tab-1",
        {
            "check_triggers": False,
            "rollback_request_count": True,
            "expected_task_id": "req-1",
        },
    )]


def test_browser_close_clears_class_and_factory_singletons(monkeypatch):
    from app.core.browser import main as browser_main

    instance = object.__new__(browser_main.BrowserCore)
    instance._watchdog_stop = threading.Event()
    instance._watchdog_thread = None
    instance._tab_pool = None
    instance._connected = True
    instance.browser_handle = object()
    instance.page = object()
    instance._initialized = True
    monkeypatch.setattr(browser_main.BrowserCore, "_instance", instance)
    monkeypatch.setattr(browser_main, "_browser_instance", instance)

    instance.close()

    assert browser_main.BrowserCore._instance is None
    assert browser_main._browser_instance is None
    assert instance._initialized is False


class _SelectorTestElement:
    tag = "button"
    text = ""

    def __init__(self):
        self.highlight_calls = 0

    def attr(self, _name):
        return None

    def run_js(self, _script):
        self.highlight_calls += 1
        return None


class _SelectorTestTab:
    def __init__(self, tab_id, url, elements):
        self.tab_id = tab_id
        self.url = url
        self._elements = elements
        self.queries = []

    def eles(self, selector, timeout):
        self.queries.append((selector, timeout))
        return list(self._elements)


class _SelectorTestPool:
    def __init__(self, sessions):
        self.sessions = sessions
        self.released = []

    def refresh_tabs(self):
        return {"total": len(self.sessions)}

    def get_sessions_snapshot(self):
        return list(reversed(self.sessions))

    def acquire_by_raw_tab_id(self, raw_tab_id, task_id, timeout, count_request, activate):
        assert count_request is False
        assert activate is False
        for session in self.sessions:
            if session.tab.tab_id == raw_tab_id:
                session.current_task_id = task_id
                return session
        return None

    def release(self, session_id, check_triggers, expected_task_id):
        assert check_triggers is False
        self.released.append((session_id, expected_task_id))
        return True


def _selector_test_session(index, tab, status="idle", domain="arena.ai"):
    return SimpleNamespace(
        id=f"session-{index}",
        persistent_index=index,
        status=SimpleNamespace(value=status),
        current_domain=domain,
        tab=tab,
    )


def _patch_selector_test_helpers(monkeypatch, browser):
    from app.api import system

    monkeypatch.setattr(system, "get_browser", lambda: browser)
    monkeypatch.setattr(system, "_collect_selector_test_element_snapshot", lambda _element: {})
    monkeypatch.setattr(system, "_build_selector_top_candidates", lambda _elements: [])
    return system


def test_selector_test_scans_every_matching_domain_tab_and_highlights_all_matches(monkeypatch):
    first_element = _SelectorTestElement()
    second_element = _SelectorTestElement()
    first = _selector_test_session(1, _SelectorTestTab("tab-a", "https://arena.ai/one", [first_element]))
    second = _selector_test_session(2, _SelectorTestTab("tab-b", "https://lmarena.ai/two", [second_element]))
    unrelated = _selector_test_session(
        3,
        _SelectorTestTab("tab-c", "https://example.com/", [_SelectorTestElement()]),
        domain="example.com",
    )
    pool = _SelectorTestPool([first, second, unrelated])
    system = _patch_selector_test_helpers(monkeypatch, SimpleNamespace(tab_pool=pool))

    result = system._run_selector_test("button.retry", 2, True, "arena.ai")

    assert result["success"] is True
    assert result["count"] == 2
    assert result["tabs_tested"] == 2
    assert result["matched_tabs"] == 2
    assert [item["tab_index"] for item in result["tabs"]] == [1, 2]
    assert first_element.highlight_calls == 1
    assert second_element.highlight_calls == 1
    assert unrelated.tab.queries == []
    assert len(pool.released) == 2


def test_selector_test_reports_per_tab_mismatch_instead_of_random_zero(monkeypatch):
    element = _SelectorTestElement()
    first = _selector_test_session(1, _SelectorTestTab("tab-a", "https://arena.ai/one", []))
    second = _selector_test_session(2, _SelectorTestTab("tab-b", "https://arena.ai/two", [element]))
    busy = _selector_test_session(
        3,
        _SelectorTestTab("tab-c", "https://arena.ai/three", [element]),
        status="busy",
    )
    pool = _SelectorTestPool([first, second, busy])
    system = _patch_selector_test_helpers(monkeypatch, SimpleNamespace(tab_pool=pool))

    result = system._run_selector_test("button.retry", 2, False, "arena.ai")

    assert result["success"] is True
    assert result["count"] == 1
    assert [item["count"] for item in result["tabs"]] == [0, 1]
    assert result["skipped_busy_tabs"] == 1
    assert any("不同页面命中数量不一致" in item for item in result["diagnosis"]["warnings"])


def _minimal_tab_pool_manager() -> TabPoolManager:
    manager = TabPoolManager.__new__(TabPoolManager)
    manager._lock = threading.RLock()
    manager._shutdown = False
    manager._tabs = {}
    manager._context_recovery_lock = threading.Lock()
    manager._context_recovery_pending = set()
    manager._context_recovery_requested_at = {}
    manager._idle_memory_purge_interval_sec = 60.0
    return manager


def test_tab_pool_idle_memory_purge_uses_heap_profiler_and_cleans_up():
    class CdpTab:
        tab_id = "raw-1"
        url = "https://example.com/"

        def __init__(self):
            self.calls = []

        def run_cdp(self, method, **kwargs):
            self.calls.append((method, kwargs))
            return {}

    tab = CdpTab()
    manager = _minimal_tab_pool_manager()
    manager._purge_idle_session_memory(TabSession("s1", tab))

    assert [method for method, _kwargs in tab.calls] == [
        "HeapProfiler.enable",
        "HeapProfiler.collectGarbage",
        "HeapProfiler.disable",
    ]
    assert all(kwargs.get("_timeout") == 1.0 for _method, kwargs in tab.calls)


def test_tab_pool_idle_memory_purge_supports_legacy_without_heap_profiler_enable():
    class LegacyCdpTab:
        tab_id = "raw-1"
        url = "https://example.com/"

        def __init__(self):
            self.calls = []

        def run_cdp(self, method, **kwargs):
            self.calls.append((method, kwargs))
            if method == "HeapProfiler.enable":
                raise RuntimeError("unknown method")
            return {}

    tab = LegacyCdpTab()
    manager = _minimal_tab_pool_manager()
    manager._purge_idle_session_memory(TabSession("s1", tab))

    assert [method for method, _kwargs in tab.calls] == [
        "HeapProfiler.enable",
        "HeapProfiler.collectGarbage",
    ]


def test_tab_pool_idle_memory_purge_disables_heap_profiler_after_collect_failure():
    class FailingCdpTab:
        tab_id = "raw-1"
        url = "https://example.com/"

        def __init__(self):
            self.calls = []

        def run_cdp(self, method, **kwargs):
            self.calls.append((method, kwargs))
            if method == "HeapProfiler.collectGarbage":
                raise RuntimeError("collect failed")
            return {}

    tab = FailingCdpTab()
    manager = _minimal_tab_pool_manager()
    manager._purge_idle_session_memory(TabSession("s1", tab))

    assert [method for method, _kwargs in tab.calls] == [
        "HeapProfiler.enable",
        "HeapProfiler.collectGarbage",
        "HeapProfiler.disable",
    ]


def test_tab_pool_force_reload_detaches_nested_session_on_failure():
    class Browser:
        def __init__(self):
            self.calls = []

        def _run_cdp(self, method, **kwargs):
            self.calls.append((method, kwargs))
            if method == "Target.attachToTarget":
                return {"sessionId": "nested-1"}
            if method == "Target.sendMessageToTarget":
                raise RuntimeError("temporary CDP failure")
            return {}

    browser = Browser()
    manager = _minimal_tab_pool_manager()
    manager.page = browser

    assert manager._force_reload_tab_via_cdp("raw-1") is False
    assert [method for method, _kwargs in browser.calls] == [
        "Target.attachToTarget",
        "Target.sendMessageToTarget",
        "Target.detachFromTarget",
    ]


def test_tab_pool_force_reload_accepts_public_browser_cdp_runner():
    class Browser:
        def __init__(self):
            self.calls = []

        def run_cdp(self, method, **kwargs):
            self.calls.append((method, kwargs))
            if method == "Target.attachToTarget":
                return {"sessionId": "nested-1"}
            return {}

    browser = Browser()
    manager = _minimal_tab_pool_manager()
    manager.page = browser

    assert manager._force_reload_tab_via_cdp("raw-1") is True
    assert [method for method, _kwargs in browser.calls] == [
        "Target.attachToTarget",
        "Target.sendMessageToTarget",
        "Target.detachFromTarget",
    ]


def test_tab_pool_force_reload_falls_back_to_flat_driver():
    class Driver:
        def __init__(self):
            self.calls = []

        def _send(self, message, timeout=None):
            self.calls.append((message, timeout))
            return {"id": 1, "result": {}}

    class Browser:
        def __init__(self):
            self.calls = []
            self._driver = Driver()

        def _run_cdp(self, method, **kwargs):
            self.calls.append((method, kwargs))
            if method == "Target.attachToTarget":
                return {"sessionId": "flat-1" if kwargs.get("flatten") else "nested-1"}
            if method == "Target.sendMessageToTarget":
                raise RuntimeError("Target.sendMessageToTarget method not found")
            return {}

    browser = Browser()
    manager = _minimal_tab_pool_manager()
    manager.page = browser

    assert manager._force_reload_tab_via_cdp("raw-1") is True
    attach_calls = [
        {key: value for key, value in kwargs.items() if key != "_timeout"}
        for method, kwargs in browser.calls
        if method == "Target.attachToTarget"
    ]
    assert attach_calls == [{"targetId": "raw-1"}, {"targetId": "raw-1", "flatten": True}]
    assert browser._driver.calls[0][0]["sessionId"] == "flat-1"


def test_tab_pool_resolve_context_error_schedules_recovery():
    class Browser:
        def get_tab(self, _raw_id):
            raise RuntimeError("Cannot find default execution context")

    manager = _minimal_tab_pool_manager()
    manager.page = Browser()
    scheduled = []
    manager._schedule_context_recovery = lambda raw_id: scheduled.append(raw_id) or True

    assert manager._resolve_tab_from_ref("raw-1") is None
    assert scheduled == ["raw-1"]


def test_tab_pool_context_recovery_scheduler_applies_per_target_cooldown():
    class Executor:
        def __init__(self):
            self.submitted = []

        def submit(self, fn, *args):
            self.submitted.append((fn, args))

    manager = _minimal_tab_pool_manager()
    manager._maintenance_executor = Executor()

    assert manager._schedule_context_recovery("raw-1") is True
    assert manager._schedule_context_recovery("raw-1") is False
    assert len(manager._maintenance_executor.submitted) == 1
