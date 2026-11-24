"""input_box candidate ranking on local synthetic pages only.

A ChatGPT answer that contains a fenced code block renders that block through a
CodeMirror editor which also matches
``div[role="textbox"][contenteditable="true"]``. Because it comes first in
document order, taking the first match fills the *code block* instead of the
composer: the chat box stays empty, so no send button appears and the request
fails downstream.

``ElementFinder.find_with_fallback`` therefore ranks ``input_box`` candidates
instead of trusting document order. These tests pin that behaviour without
touching a real session: synthetic pages, disposable Chromium profile.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
from typing import Any, List, Optional

import pytest

from app.core.elements import ElementFinder
from tests._real_browser import launch_page

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = json.loads((ROOT / "config/sites/chatgpt.com.json").read_text(encoding="utf-8"))
PRESET = PAYLOAD["config"]["presets"][PAYLOAD["config"]["default_preset"]]
INPUT_SELECTOR = PRESET["selectors"]["input_box"]


class _FakeElement:
    def __init__(self, marker: str):
        self.marker = marker

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FakeElement {self.marker}>"


def _finder(scores: List[int], expect_count: Optional[int] = None) -> ElementFinder:
    """ElementFinder with ranking stubbed to ``scores`` (no browser needed).

    ``expect_count`` asserts that ranking saw every candidate; pass a value only
    for the cases where the score list is meant to line up with the candidates.
    """
    finder = ElementFinder.__new__(ElementFinder)
    finder.tab = None

    def _rank(_selector: str, count: int) -> List[int]:
        if expect_count is not None:
            assert count == expect_count, "rank() must see every candidate"
        return scores

    finder._rank_input_candidates = _rank  # type: ignore[method-assign]
    return finder


def test_single_candidate_is_returned_without_ranking():
    """The common case must not pay for an extra page-level query."""
    calls = []

    def _rank(selector: str, count: int):  # pragma: no cover - must not run
        calls.append((selector, count))
        return []

    finder = ElementFinder.__new__(ElementFinder)
    finder.tab = None
    finder._rank_input_candidates = _rank  # type: ignore[method-assign]
    only = _FakeElement("composer")
    assert finder._pick_best_input_candidate([only], INPUT_SELECTOR) is only
    assert calls == [], "len==1 must skip the ranking round-trip"


def test_code_editor_first_in_document_order_is_not_picked():
    """Regression: the CodeMirror block precedes the composer but scores lower."""
    code_editor = _FakeElement("code-editor")
    composer = _FakeElement("composer")
    finder = _finder([2, 7], expect_count=2)
    picked = finder._pick_best_input_candidate([code_editor, composer], INPUT_SELECTOR)
    assert picked is composer, "must not fill the inline code block"


def test_ties_keep_document_order():
    """Equal scores must not introduce behaviour drift."""
    first = _FakeElement("first")
    second = _FakeElement("second")
    finder = _finder([7, 7], expect_count=2)
    assert finder._pick_best_input_candidate([first, second], INPUT_SELECTOR) is first


def test_unusable_scores_fall_back_to_document_order():
    """When ranking is unavailable the previous behaviour is preserved."""
    first = _FakeElement("first")
    second = _FakeElement("second")
    finder = _finder([])  # length mismatch -> ranking unusable
    assert finder._pick_best_input_candidate([first, second], INPUT_SELECTOR) is first


def test_all_zero_scores_fall_back_to_document_order():
    first = _FakeElement("first")
    second = _FakeElement("second")
    finder = _finder([0, 0], expect_count=2)
    assert finder._pick_best_input_candidate([first, second], INPUT_SELECTOR) is first


def test_empty_candidate_list_returns_none():
    finder = _finder([], expect_count=None)
    assert finder._pick_best_input_candidate([], INPUT_SELECTOR) is None


@pytest.fixture(scope="module")
def page():
    with closing(launch_page()) as pages:
        page = next(pages)
        page.get("data:text/html,<html><body></body></html>")
        page.wait.doc_loaded()
        yield page


def load(page, html):
    page.run_js("document.body.innerHTML = arguments[0]", html)


@pytest.mark.real_browser
def test_ranking_scores_code_editor_below_composer(page):
    """The scoring JS itself must rank a synthetic code block lower."""
    load(
        page,
        """
        <main>
          <div class="markdown">
            <div class="cm-editor">
              <div class="cm-content" role="textbox" contenteditable="true">fenced block</div>
            </div>
          </div>
          <form>
            <div class="ProseMirror" role="textbox" contenteditable="true">composer</div>
          </form>
        </main>
        """,
    )
    finder = ElementFinder.__new__(ElementFinder)
    finder.tab = _DriverShim(page)
    count = page.run_js(
        "return document.querySelectorAll(arguments[0]).length", INPUT_SELECTOR
    )
    assert count >= 2, "fixture must expose both candidates"
    scores = finder._rank_input_candidates(INPUT_SELECTOR, int(count))
    assert len(scores) == int(count), "every candidate must receive a score"
    # code editor scores (isCode=0) < composer (isCode=1 -> 4+2+1 = 7)
    assert scores[0] < scores[-1], f"code editor must rank lower, got {scores}"


@pytest.mark.real_browser
def test_ranking_prefers_composer_over_code_editor_end_to_end(page):
    load(
        page,
        """
        <main>
          <div class="markdown">
            <div class="cm-editor">
              <div class="cm-content" role="textbox" contenteditable="true">fenced block</div>
            </div>
          </div>
          <form>
            <div class="ProseMirror" role="textbox" contenteditable="true">composer</div>
          </form>
        </main>
        """,
    )
    finder = ElementFinder.__new__(ElementFinder)
    finder.tab = _DriverShim(page)
    candidates = [_FakeElement("code-editor"), _FakeElement("composer")]
    picked = finder._pick_best_input_candidate(candidates, INPUT_SELECTOR)
    assert picked is candidates[1], "composer must win over the code editor"


class _DriverShim:
    """Minimal adapter so the real scoring JS runs against a bare Chromium page.

    DrissionPage's ``run_js(..., as_expr=True)`` wraps the script in ``return (...)``.
    Playwright does not, and the scoring script is a bare IIFE expression, so the
    shim adds the ``return`` itself; ``as_expr``/``timeout`` are accepted and ignored.
    """

    def __init__(self, page: Any):
        self._page = page

    def run_js(self, script: str, *args: Any, **kwargs: Any) -> Optional[str]:
        body = script.strip()
        if not body.startswith("return"):
            body = "return " + body
        return self._page.run_js(body, *args)
