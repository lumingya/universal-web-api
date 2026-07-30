import json
from pathlib import Path

from app.core.browser.media import BrowserMediaMixin
from app.core.browser.workflow import BrowserWorkflowMixin
from app.core.extractors.image_extractor import ImageExtractor


class _MediaHarness(BrowserMediaMixin):
    pass


def _item(media_type: str, url: str, source: str) -> dict:
    return {
        "media_type": media_type,
        "kind": "url",
        "url": url,
        "data_uri": None,
        "source": source,
    }


def test_current_stream_image_replaces_stale_dom_image() -> None:
    harness = _MediaHarness()
    stale_dom = _item("image", "https://example.test/previous.jpg", "dom")
    current_stream = _item("image", "https://example.test/current.png", "lmarena_stream")

    merged = harness._merge_dom_and_stream_media_items(
        [stale_dom],
        [current_stream],
        {},
    )

    assert [item["url"] for item in merged] == [current_stream["url"]]


def test_stream_image_keeps_dom_media_of_other_types() -> None:
    harness = _MediaHarness()
    stale_dom = _item("image", "https://example.test/previous.jpg", "dom")
    dom_audio = _item("audio", "https://example.test/current.mp3", "dom")
    current_stream = _item("image", "https://example.test/current.png", "lmarena_stream")

    merged = harness._merge_dom_and_stream_media_items(
        [stale_dom, dom_audio],
        [current_stream],
        {},
    )

    assert [(item["media_type"], item["url"]) for item in merged] == [
        ("image", current_stream["url"]),
        ("audio", dom_audio["url"]),
    ]


def test_pending_stream_image_does_not_replace_ready_dom_image() -> None:
    harness = _MediaHarness()
    dom_image = _item("image", "https://example.test/current.jpg", "dom")
    pending_stream = _item("image", "https://example.test/placeholder.png", "lmarena_stream")
    pending_stream["pending"] = True

    merged = harness._merge_dom_and_stream_media_items(
        [dom_image],
        [pending_stream],
        {},
    )

    assert [item["url"] for item in merged] == [dom_image["url"]]


def _image_config(run_policy: str = "disabled") -> dict:
    return {
        "enabled": True,
        "selector": "img",
        "debounce_seconds": 0,
        "wait_for_load": False,
        "modalities": {
            "image": {"enabled": True, "run_policy": run_policy},
            "audio": {"enabled": False, "run_policy": "disabled"},
            "video": {"enabled": False, "run_policy": "disabled"},
        },
    }


def _video_config(run_policy: str = "on_signal") -> dict:
    return {
        "enabled": True,
        "selector": "img",
        "video_selector": "video, video source",
        "debounce_seconds": 0,
        "wait_for_load": False,
        "late_render_poll_seconds": 0.2,
        "modalities": {
            "image": {"enabled": False, "run_policy": "disabled"},
            "audio": {"enabled": False, "run_policy": "disabled"},
            "video": {
                "enabled": True,
                "run_policy": run_policy,
                "late_wait_timeout_seconds": 1,
            },
        },
    }


def test_capture_media_dom_baseline_marks_existing_images() -> None:
    class _Tab:
        def run_js(self, script, selector, token, property_name):
            assert "Object.defineProperty" in script
            assert selector == "img"
            assert token
            assert property_name == BrowserMediaMixin._MEDIA_DOM_BASELINE_PROPERTY
            return {
                "ok": True,
                "node_count": 3,
                "marked_count": 3,
                "url_count": 2,
                "references": [
                    "https://example.test/upload.png?signature=before-send",
                ],
                "page_url": "https://arena.test/direct",
            }

    baseline = _MediaHarness()._capture_media_dom_baseline(_Tab(), _image_config())

    assert baseline is not None
    assert baseline["node_count"] == 3
    assert baseline["marked_count"] == 3
    assert baseline["url_count"] == 2
    assert baseline["token"]
    assert baseline["references"] == [
        "https://example.test/upload.png?signature=before-send"
    ]


def test_arena_baseline_excludes_preexisting_nodes_after_source_changes() -> None:
    assert BrowserWorkflowMixin._should_exclude_preexisting_media_nodes(
        {"page_url": "https://arena.ai/c/request"}
    ) is True
    assert BrowserWorkflowMixin._should_exclude_preexisting_media_nodes(
        {"page_url": "https://example.test/chat"}
    ) is False


def test_recreated_upload_preview_is_filtered_by_stable_url_path() -> None:
    upload_before_send = _item(
        "image",
        "https://messages-prod.example.r2.cloudflarestorage.com/request/upload.png?X-Amz-Signature=before-send",
        "dom",
    )
    generated = _item(
        "image",
        "https://messages-prod.example.r2.cloudflarestorage.com/result/generated.png?X-Amz-Signature=result",
        "dom",
    )
    baseline = {
        "references": [
            "https://messages-prod.example.r2.cloudflarestorage.com/request/upload.png?X-Amz-Signature=original"
        ]
    }

    filtered = _MediaHarness._filter_media_items_against_dom_baseline(
        [upload_before_send, generated],
        baseline,
    )

    assert [item["url"] for item in filtered] == [generated["url"]]


def test_unsigned_query_urls_remain_distinct_during_baseline_filtering() -> None:
    candidate = _item(
        "image",
        "https://example.test/image?id=generated",
        "dom",
    )
    baseline = {"references": ["https://example.test/image?id=upload"]}

    filtered = _MediaHarness._filter_media_items_against_dom_baseline(
        [candidate],
        baseline,
    )

    assert filtered == [candidate]


def test_image_extractor_receives_request_baseline_options() -> None:
    class _Element:
        options = None

        def run_js(self, script, options):
            assert "requestBaselineToken" in script
            assert "requestBaselineExcludeExistingNodes" in script
            self.options = options
            return {"images": [], "warnings": [], "scope": "primary", "nodeCount": 0}

    element = _Element()
    extractor = ImageExtractor()
    extractor.extract(
        element,
        {
            "enabled": True,
            "wait_for_load": False,
            "request_baseline_token": "request-token",
            "request_baseline_property": "__requestBaseline",
            "request_baseline_exclude_existing_nodes": True,
        },
    )

    assert element.options["requestBaselineToken"] == "request-token"
    assert element.options["requestBaselineProperty"] == "__requestBaseline"
    assert element.options["requestBaselineExcludeExistingNodes"] is True


def test_pre_submit_node_source_change_is_not_fresh_dom_output() -> None:
    class _ExistingNodeCandidate:
        def run_js(self, script, selector, token, property_name, exclude_existing_nodes):
            assert selector == "img"
            assert token == "request-token"
            assert property_name == "__requestBaseline"
            assert exclude_existing_nodes is True
            return False

    assert _MediaHarness._candidate_has_fresh_dom_image(
        _ExistingNodeCandidate(),
        {
            **_image_config(),
            "request_baseline_exclude_existing_nodes": True,
        },
        _baseline(),
    ) is False


class _Candidate:
    def __init__(self, url: str, *, fresh: bool, text: str = ""):
        self.url = url
        self.fresh = fresh
        self.text = text

    def run_js(self, script, *args):
        if "baselineToken" in script:
            return self.fresh
        if "getBoundingClientRect" in script:
            return {"bottom": 100, "left": 0, "width": 100, "height": 100}
        if "innerText" in script or "textContent" in script:
            return self.text
        return False


class _Extractor:
    def extract_media(self, target, config, container_selector_fallback):
        assert config["request_baseline_token"] == "request-token"
        assert config["request_baseline_property"] == "__requestBaseline"
        return [_item("image", target.url, "dom")]

    @staticmethod
    def extract_text(target):
        return target.text


class _BaselineMediaHarness(_MediaHarness):
    @staticmethod
    def _should_stop_checker():
        return False

    @staticmethod
    def _looks_like_image_generation_request(text):
        return "generate an image" in str(text or "").lower()

    @staticmethod
    def _prefetch_remote_image_urls(*args, **kwargs):
        return None

    @staticmethod
    def _try_screenshot_images_to_local(tab, last_element, images, image_config=None):
        return images

    @staticmethod
    def _persist_data_uri_media_to_local(media_items, max_size_mb=10):
        return media_items


def _baseline() -> dict:
    return {
        "token": "request-token",
        "property": "__requestBaseline",
        "node_count": 1,
    }


def test_dom_order_cannot_make_old_image_replace_fresh_image(monkeypatch) -> None:
    fresh = _Candidate("https://example.test/current.png", fresh=True)
    stale = _Candidate("https://example.test/old.png", fresh=False)

    class _Finder:
        def __init__(self, tab):
            self.tab = tab

        def find_all(self, selector, timeout):
            return [fresh, stale]

    monkeypatch.setattr("app.core.elements.ElementFinder", _Finder)
    result = _BaselineMediaHarness()._extract_media_after_stream(
        tab=object(),
        extractor=_Extractor(),
        image_config=_image_config(),
        result_selector=".reply",
        request_text_hint="ordinary request",
        media_dom_baseline=_baseline(),
    )

    assert [item["url"] for item in result] == [fresh.url]


def test_terminal_error_never_returns_old_image_or_blind_waits(monkeypatch) -> None:
    stale = _Candidate(
        "https://example.test/old.png",
        fresh=False,
        text="Something went wrong with this response, please try again. Trace ID: abc123",
    )

    class _Finder:
        def __init__(self, tab):
            self.tab = tab

        def find_all(self, selector, timeout):
            return [stale]

    def _unexpected_sleep(_seconds):
        raise AssertionError("terminal error response must not enter image wait")

    monkeypatch.setattr("app.core.elements.ElementFinder", _Finder)
    monkeypatch.setattr("app.core.browser.media.time.sleep", _unexpected_sleep)
    result = _BaselineMediaHarness()._extract_media_after_stream(
        tab=object(),
        extractor=_Extractor(),
        image_config=_image_config("on_signal"),
        result_selector=".reply",
        request_text_hint="generate an image of a city",
        media_dom_baseline=_baseline(),
    )

    assert result == []


def test_request_intent_alone_does_not_trigger_late_image_wait(monkeypatch) -> None:
    stale = _Candidate(
        "https://example.test/old.png",
        fresh=False,
        text='{"decision":"REFUSE","reason":"This request cannot be fulfilled."}',
    )

    class _Finder:
        def __init__(self, tab):
            self.tab = tab

        def find_all(self, selector, timeout):
            return [stale]

    def _unexpected_sleep(_seconds):
        raise AssertionError("request wording alone must not trigger a late image wait")

    monkeypatch.setattr("app.core.elements.ElementFinder", _Finder)
    monkeypatch.setattr("app.core.browser.media.time.sleep", _unexpected_sleep)
    response_text = '{"decision":"REFUSE","reason":"This request cannot be fulfilled."}'
    result = _BaselineMediaHarness()._extract_media_after_stream(
        tab=object(),
        extractor=_Extractor(),
        image_config=_image_config("on_signal"),
        result_selector=".reply",
        request_text_hint="Review whether this prompt asks to generate an image.",
        response_text_hint=response_text,
        media_dom_baseline=_baseline(),
    )

    assert result == []


def test_pending_video_text_waits_for_late_video_render(monkeypatch) -> None:
    candidate = _Candidate("", fresh=True, text="正在生成视频...")

    class _Finder:
        def __init__(self, tab):
            self.tab = tab

        def find_all(self, selector, timeout):
            return [candidate]

    class _VideoExtractor:
        calls = 0

        def extract_media(self, target, config, container_selector_fallback):
            self.calls += 1
            if self.calls < 2:
                return []
            return [_item("video", "https://example.test/generated.mp4", "dom")]

        @staticmethod
        def extract_text(target):
            return target.text

    extractor = _VideoExtractor()
    monkeypatch.setattr("app.core.elements.ElementFinder", _Finder)
    monkeypatch.setattr("app.core.browser.media.time.sleep", lambda _seconds: None)

    result = _BaselineMediaHarness()._extract_media_after_stream(
        tab=object(),
        extractor=extractor,
        image_config=_video_config(),
        result_selector=".reply",
        response_text_hint="正在生成视频，这可能需要几分钟时间。",
    )

    assert extractor.calls >= 2
    assert [(item["media_type"], item["url"]) for item in result] == [
        ("video", "https://example.test/generated.mp4")
    ]


def test_all_gemini_presets_wait_on_explicit_video_generation_signal() -> None:
    sites_path = Path(__file__).resolve().parents[1] / "config" / "sites.json"
    sites = json.loads(sites_path.read_text(encoding="utf-8"))
    presets = sites["gemini.google.com"]["presets"]

    assert presets
    for preset_name, preset in presets.items():
        video_policy = preset["image_extraction"]["modalities"]["video"]
        assert video_policy["enabled"] is True, preset_name
        assert video_policy["run_policy"] == "on_signal", preset_name
        assert video_policy["late_wait_timeout_seconds"] == 300, preset_name
