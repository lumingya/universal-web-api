"""P0-1: in-page stream snapshot.

* Unit tests use a fake tab and count CDP round-trips per poll.
* ``test_real_browser_parity_*`` compare the new single-call snapshot with the
  legacy per-element path in a real Chromium (skipped when no browser is
  available; set ``UWAPI_TEST_CHROME`` to a chrome binary to force a path).
"""

from __future__ import annotations

import json
import urllib.parse

import pytest

from app.core.elements import ElementFinder
from tests._real_browser import launch_page
from app.core.extractors.base import BaseExtractor
from app.core.extractors.deep_mode import DeepBrowserExtractor
from app.core.stream_monitor import StreamMonitor
from app.core.stream_snapshot import (
    SNAPSHOT_JS,
    build_snapshot_config,
    decode_snapshot,
    resolve_locator,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


class _FakeTab:
    _uwapi_page_snapshot_ok = True

    def __init__(self, response):
        self.response = response
        self.calls = []

    def run_js(self, script, *args, **kwargs):
        self.calls.append((script, args, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        if callable(self.response):
            return self.response(script, *args)
        return self.response


class _ExplodingFinder:
    """Legacy path marker: any use means we fell back."""

    def __init__(self):
        self.calls = 0

    def find_all(self, *args, **kwargs):
        self.calls += 1
        return []


def _monitor(tab, finder=None, **kwargs):
    return StreamMonitor(tab, finder or _ExplodingFinder(), formatter=None, **kwargs)


def _payload(**overrides):
    data = {
        "v": 1,
        "ok": True,
        "n": 3,
        "generating": True,
        "found": True,
        "anchor": "len:5|hash:1|preview:hello",
        "text": "hello",
        "selected": {"index": 2, "bottom": 300.0, "left": 10.0},
    }
    data.update(overrides)
    return json.dumps(data)


# --------------------------------------------------------------------------- #
# unit tests
# --------------------------------------------------------------------------- #


def test_resolve_locator_matches_element_finder_syntax():
    assert resolve_locator("div.message") == ("css", "div.message")
    assert resolve_locator("css:div.message") == ("css", "div.message")
    assert resolve_locator("xpath://div[@class='m']") == ("xpath", "//div[@class='m']")
    kind, query = resolve_locator("tag:div@class=m")
    assert kind == "xpath" and "div" in query
    assert resolve_locator("") is None


def test_snapshot_js_is_a_drission_js_function():
    from DrissionPage._functions.web import is_js_func

    assert is_js_func(SNAPSHOT_JS)


def test_decode_snapshot_rejects_bad_payloads():
    assert decode_snapshot(None) is None
    assert decode_snapshot({"v": 1, "ok": True}) is None  # object result = leak path
    assert decode_snapshot("not json") is None
    assert decode_snapshot(json.dumps({"v": 99, "ok": True})) is None
    assert decode_snapshot(json.dumps({"v": 1, "ok": False, "error": "x"})) is None
    assert decode_snapshot(json.dumps({"v": 1, "ok": True, "n": 0})) == {"v": 1, "ok": True, "n": 0}


def test_poll_uses_exactly_one_cdp_call_and_no_legacy_lookups():
    tab = _FakeTab(_payload())
    finder = _ExplodingFinder()
    monitor = _monitor(tab, finder)

    snap = monitor._get_snapshot_prefer_anchor("div.msg", None)

    assert len(tab.calls) == 1
    assert finder.calls == 0
    script, args, kwargs = tab.calls[0]
    assert script == SNAPSHOT_JS
    assert isinstance(args[0], str)  # config travels as a JSON string
    assert json.loads(args[0])["query"] == "div.msg"
    assert snap["groups_count"] == 3
    assert snap["text"] == "hello"
    assert snap["text_len"] == 5
    assert snap["anchor"] == "len:5|hash:1|preview:hello"
    assert snap["is_generating"] is True
    assert snap["image_count"] == 0


def test_latest_snapshot_only_reports_count_when_target_found():
    tab = _FakeTab(_payload(found=False, text="", anchor=None, selected=None))
    monitor = _monitor(tab)

    latest = monitor._get_latest_message_snapshot("div.msg")
    prefer = monitor._get_snapshot_prefer_anchor("div.msg", None)

    assert latest["groups_count"] == 0
    assert prefer["groups_count"] == 3


def test_blocked_side_returns_empty_result():
    tab = _FakeTab(_payload(found=False, blocked=True, text="", anchor=None))
    monitor = _monitor(tab, image_config={"_parser_id": "lmarena_side_right"})

    snap = monitor._get_snapshot_prefer_anchor("div.msg", None)

    assert json.loads(tab.calls[0][1][0])["column"] == "right"
    assert snap["anchor"] is None
    assert snap["text"] == ""


def test_images_are_normalized_like_legacy_path():
    tab = _FakeTab(
        _payload(
            images={
                "count": 1,
                "urls": ["https://example.test/a.png", "https://example.test/a.png"],
                "references": ["blob:https://example.test/x"],
            },
            pageImages={"urls": ["https://example.test/b.png"], "references": []},
        )
    )
    monitor = _monitor(
        tab,
        image_config={"enabled": True, "modalities": {"image": {"enabled": True}}},
    )
    monitor._expect_image_output = True

    snap = monitor._get_snapshot_prefer_anchor("div.msg", None)
    cfg = json.loads(tab.calls[0][1][0])

    assert cfg["withImages"] is True and cfg["withPageImages"] is True
    assert snap["has_images"] is True
    assert snap["image_count"] >= 1
    assert snap["image_urls"].count("https://example.test/a.png") == 1
    assert snap["page_image_urls"] == ["https://example.test/b.png"]


def test_transient_exception_falls_back_without_disabling():
    tab = _FakeTab(RuntimeError("context destroyed"))
    finder = _ExplodingFinder()
    monitor = _monitor(tab, finder)

    for _ in range(5):
        monitor._get_snapshot_prefer_anchor("div.msg", None)

    assert finder.calls == 5
    assert len(tab.calls) == 5  # still trying the fast path every poll
    assert monitor._page_snapshot_failures == 0


def test_invalid_payload_disables_fast_path_after_three_failures():
    tab = _FakeTab(json.dumps({"v": 1, "ok": False, "error": "selector: bad"}))
    finder = _ExplodingFinder()
    monitor = _monitor(tab, finder)

    for _ in range(6):
        monitor._get_snapshot_prefer_anchor("div.msg", None)

    assert len(tab.calls) == StreamMonitor.PAGE_SNAPSHOT_MAX_FAILURES
    assert finder.calls == 6


def test_custom_extractor_keeps_legacy_path():
    class _Custom(BaseExtractor):
        def extract_text(self, element, *a, **k):
            return ""

        def get_anchor(self, element):
            return ""

        def find_content_node(self, element):
            return element

    tab = _FakeTab(_payload())
    finder = _ExplodingFinder()
    try:
        monitor = _monitor(tab, finder, extractor=_Custom())
    except TypeError:
        pytest.skip("BaseExtractor signature differs")

    monitor._get_snapshot_prefer_anchor("div.msg", None)

    assert tab.calls == []
    assert finder.calls == 1


def test_env_switch_disables_page_snapshot(monkeypatch):
    monkeypatch.setenv("STREAM_PAGE_SNAPSHOT_ENABLED", "false")
    tab = _FakeTab(_payload())
    finder = _ExplodingFinder()
    monitor = _monitor(tab, finder)

    monitor._get_snapshot_prefer_anchor("div.msg", None)

    assert tab.calls == []
    assert finder.calls == 1


def test_stream_config_override_beats_env(monkeypatch):
    monkeypatch.setenv("STREAM_PAGE_SNAPSHOT_ENABLED", "false")
    tab = _FakeTab(_payload())
    monitor = _monitor(tab, stream_config={"page_snapshot": True})

    monitor._get_snapshot_prefer_anchor("div.msg", None)

    assert len(tab.calls) == 1


def test_monitor_created_via_new_keeps_legacy_behaviour():
    monitor = StreamMonitor.__new__(StreamMonitor)
    assert monitor._page_snapshot_supported() is False


# --------------------------------------------------------------------------- #
# real browser parity
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def real_page():
    yield from launch_page()


def _load(page, body):
    html = "<!doctype html><html><head><meta charset='utf-8'></head><body>" + body + "</body></html>"
    page.get("data:text/html;charset=utf-8," + urllib.parse.quote(html))


def _both(page, selector, prefer_anchor=None, image_config=None, expect_images=False, latest=False):
    fast = StreamMonitor(page, ElementFinder(page), formatter=None, image_config=image_config)
    legacy = StreamMonitor(page, ElementFinder(page), formatter=None, image_config=image_config)
    legacy._page_snapshot_enabled = False
    fast._expect_image_output = legacy._expect_image_output = expect_images
    if latest:
        a = fast._get_latest_message_snapshot(selector)
        b = legacy._get_latest_message_snapshot(selector)
    else:
        a = fast._get_snapshot_prefer_anchor(selector, prefer_anchor)
        b = legacy._get_snapshot_prefer_anchor(selector, prefer_anchor)
    assert fast._page_snapshot_failures == 0
    return a, b


CHAT = """
<main><ol>
  <li><div class="msg"><div class="markdown"><p>First answer</p></div></div></li>
  <li><div class="msg"><div class="markdown"><p>Second <b>answer</b></p><ul><li>one</li><li>two</li></ul></div></div></li>
  <li><div class="msg"><div class="markdown"><p>Third answer with <code>code</code></p>
      <pre><code>print(1)\nprint(2)</code></pre>
      <img src="https://example.test/gen.png"></div></div></li>
</ol></main>
<button aria-label="Stop generating">stop</button>
"""


def test_real_browser_parity_latest_and_anchor(real_page):
    _load(real_page, CHAT)

    fast, legacy = _both(real_page, "div.msg")
    assert fast == legacy
    assert fast["groups_count"] == 3
    assert "Third answer" in fast["text"]
    assert fast["is_generating"] is True

    fast_l, legacy_l = _both(real_page, "div.msg", latest=True)
    assert fast_l == legacy_l

    # anchor of the first message must be honoured (stable id attribute)
    _load(real_page, CHAT.replace('<div class="msg">', '<div class="msg" data-message-id="m">', 1))
    first = real_page.eles("css:div.msg")[0]
    anchor = DeepBrowserExtractor().get_anchor(first)
    fast_a, legacy_a = _both(real_page, "div.msg", prefer_anchor=anchor)
    assert fast_a == legacy_a
    assert fast_a["text"].startswith("First answer")


def test_real_browser_parity_images(real_page):
    _load(real_page, CHAT)
    cfg = {"enabled": True, "modalities": {"image": {"enabled": True}}}

    fast, legacy = _both(real_page, "div.msg", image_config=cfg, expect_images=True)

    assert fast == legacy
    assert fast["image_urls"] == ["https://example.test/gen.png"]


def test_real_browser_parity_generating_hidden(real_page):
    _load(real_page, CHAT.replace('<button aria-label="Stop generating">', '<button style="display:none" aria-label="Stop generating">'))

    fast, legacy = _both(real_page, "div.msg")

    assert fast == legacy
    assert fast["is_generating"] is False


SIDE_BY_SIDE = """
<main><ol>
  <li><div class="flex"><div class="basis-1/2"><div class="msg"><p>old left</p></div></div>
      <div class="basis-1/2"><div class="msg"><p>old right</p></div></div></div></li>
  <li><div class="flex" style="display:flex"><div class="basis-1/2" style="width:50%"><div class="msg"><p>new left</p></div></div>
      <div class="basis-1/2" style="width:50%"><div class="msg"><p>new right</p></div></div></div></li>
</ol></main>
"""


@pytest.mark.parametrize("parser_id", ["lmarena_side_left", "lmarena_side_right", ""])
def test_real_browser_parity_side_by_side(real_page, parser_id):
    _load(real_page, SIDE_BY_SIDE)

    fast, legacy = _both(real_page, "div.msg", image_config={"_parser_id": parser_id})

    assert fast == legacy
    if parser_id.endswith("right"):
        assert fast["text"] == "new right"
    else:
        assert fast["text"] == "new left"


def test_real_browser_parity_blocked_side(real_page):
    _load(
        real_page,
        """<main><ol>
          <li><div class="flex"><div class="basis-1/2"><div class="msg"><p>old left</p></div></div>
              <div class="basis-1/2"><div class="msg"><p>old right</p></div></div></div></li>
          <li><div class="flex"><div class="basis-1/2"><div class="msg"><p>only left</p></div></div>
              <div class="basis-1/2"><div class="err">500</div></div></div></li>
        </ol></main>""",
    )

    fast, legacy = _both(real_page, "div.msg", image_config={"_parser_id": "lmarena_side_right"})

    assert fast == legacy
    assert fast["text"] == ""


def test_real_browser_parity_empty_and_xpath(real_page):
    _load(real_page, CHAT)

    fast, legacy = _both(real_page, "div.nothing-here")
    assert fast == legacy
    assert fast["groups_count"] == 0

    fast_x, legacy_x = _both(real_page, "xpath://div[@class='msg']")
    assert fast_x == legacy_x
    assert fast_x["groups_count"] == 3


def test_real_browser_snapshot_leaves_no_remote_objects(real_page):
    """String results must not accumulate RemoteObjects in the renderer."""
    _load(real_page, CHAT)
    monitor = StreamMonitor(real_page, ElementFinder(real_page), formatter=None)
    for _ in range(50):
        monitor._get_snapshot_prefer_anchor("div.msg", None)
    assert monitor._page_snapshot_failures == 0
    raw = real_page.run_js(SNAPSHOT_JS, json.dumps(build_snapshot_config(
        "div.msg", prefer_anchor=None, column="left", with_images=False, with_page_images=False,
    )))
    assert isinstance(raw, str)
