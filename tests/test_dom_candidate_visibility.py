"""Visual candidate selection must ignore unrendered DOM nodes.

Some chat sites keep a collapsed activity summary ("thought for N seconds") in
the DOM. It matches the reply selector, but ``display:none`` sits on an ancestor
and ``getBoundingClientRect()`` is all zeros. Because the candidate scorer sorts
by bottom and area, a 0x0 node can win the race; worse, the midpoint fallback
treats its ``left`` (0) as the leftmost column and keeps it while discarding the
real reply. Real-world symptom: the stream ended on an 8-character summary
instead of the full answer.

The fix drops unrendered candidates before any ranking happens. It deliberately
does not touch the midpoint fallback: that path is required for genuinely
side-by-side layouts (arena battle pages) and its behaviour must be preserved.
"""
from __future__ import annotations

import json

import app.core.stream_monitor as sm_module
from app.core.stream_monitor import StreamMonitor


class _FakeElement:
    def __init__(self, marker: str) -> None:
        self.marker = marker

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FakeElement {self.marker}>"


class _FakeExtractor:
    def get_anchor(self, el):
        return "anchor:" + getattr(el, "marker", "?")


class _FakeAsElement:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def run_js(self, *args, **kwargs):
        return self.payload


def _score(
    *,
    rendered=None,
    bottom=0.0,
    left=0.0,
    width=0.0,
    height=0.0,
    turn=0,
    reverse=False,
    side="single",
):
    payload = {
        "contentBottom": bottom,
        "left": left,
        "width": width,
        "height": height,
        "turnIndex": turn,
        "isReverse": reverse,
        "side": side,
    }
    if rendered is not None:
        payload["rendered"] = rendered
    return json.dumps(payload)


def _monitor(column: str = "left"):
    monitor = StreamMonitor.__new__(StreamMonitor)
    monitor._image_config = {"latest_visual_column": column}
    monitor.extractor = _FakeExtractor()
    monitor._last_visual_reply_log_info = None
    return monitor


def _install_scores(monkeypatch, mapping):
    def _fake_as_element(el):
        return _FakeAsElement(mapping[id(el)])

    monkeypatch.setattr(sm_module, "as_element", _fake_as_element)


def test_hidden_activity_summary_is_dropped_before_ranking(monkeypatch):
    """The folded summary (0x0 box, display:none ancestor) must not win."""
    hidden = _FakeElement("hidden-summary")
    reply = _FakeElement("real-reply")
    _install_scores(
        monkeypatch,
        {
            # Hidden summary: rendered=False, zero box at the left edge.
            id(hidden): _score(rendered=False, bottom=0, left=0, width=0, height=0),
            # Real reply: rendered, wider box, further to the right.
            id(reply): _score(rendered=True, bottom=817, left=435, width=640, height=726),
        },
    )

    picked, _ = _monitor("left")._select_candidate_element([hidden, reply])

    assert picked is reply


def test_hidden_candidates_are_kept_when_every_candidate_is_hidden(monkeypatch):
    """Never empty the pool: if all are unrendered, fall back to the full list."""
    first = _FakeElement("first")
    second = _FakeElement("second")
    _install_scores(
        monkeypatch,
        {
            id(first): _score(rendered=False, bottom=100, left=0, width=0, height=0),
            id(second): _score(rendered=False, bottom=50, left=0, width=0, height=0),
        },
    )

    picked, _ = _monitor("left")._select_candidate_element([first, second])

    # Both unrendered -> the filter is a no-op and the original ranking applies.
    assert picked is first


def test_missing_rendered_flag_defaults_to_visible(monkeypatch):
    """Payloads produced before this change carry no ``rendered`` field."""
    explicit = _FakeElement("explicit")
    legacy = _FakeElement("legacy")
    _install_scores(
        monkeypatch,
        {
            # "explicit" is rendered but sits higher on the page.
            id(explicit): _score(rendered=True, bottom=700, left=0, width=400, height=700),
            # "legacy" has no rendered key at all; it must still be eligible.
            id(legacy): _score(bottom=817, left=0, width=400, height=700),
        },
    )

    picked, _ = _monitor("left")._select_candidate_element([explicit, legacy])

    # If the missing key were treated as hidden, only ``explicit`` would remain.
    assert picked is legacy


def test_as_element_failure_is_treated_as_rendered(monkeypatch):
    """A raising ``run_js`` must not silently drop the candidate."""
    broken = _FakeElement("broken")
    hidden = _FakeElement("hidden")

    class _RaisingAsElement:
        def run_js(self, *args, **kwargs):
            raise RuntimeError("boom")

    def _fake(el):
        if el is broken:
            return _RaisingAsElement()
        return _FakeAsElement(
            _score(rendered=False, bottom=0, left=0, width=0, height=0)
        )

    monkeypatch.setattr(sm_module, "as_element", _fake)

    picked, _ = _monitor("left")._select_candidate_element([hidden, broken])

    # ``hidden`` is filtered out by its explicit flag; ``broken`` survives
    # because a probe failure is never used as proof of invisibility.
    assert picked is broken


def test_explicit_two_column_split_is_unchanged(monkeypatch):
    """A real left/right layout must still be honoured exactly as before."""
    left_side = _FakeElement("left-side")
    right_side = _FakeElement("right-side")
    _install_scores(
        monkeypatch,
        {
            id(left_side): _score(
                rendered=True, bottom=817, left=100, width=400, height=700, side="left"
            ),
            id(right_side): _score(
                rendered=True, bottom=817, left=700, width=400, height=700, side="right"
            ),
        },
    )

    picked, _ = _monitor("right")._select_candidate_element([left_side, right_side])

    assert picked is right_side


def test_snapshot_js_declares_is_rendered_helper():
    """The in-page snapshot path must carry the same visibility predicate."""
    from app.core.stream_snapshot import SNAPSHOT_JS

    assert "function isRendered(" in SNAPSHOT_JS
    assert (
        "var renderedOnly = scored.filter(function (s) { return s.rendered; });"
        in SNAPSHOT_JS
    )
