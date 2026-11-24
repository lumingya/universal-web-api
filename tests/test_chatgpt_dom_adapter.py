"""ChatGPT DIL/legacy DOM adapter regressions on local synthetic pages only.

Uses a disposable Chromium profile, never the user's ChatGPT session. The DIL
attributes mirror the observed page without retaining conversation content.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path

import pytest

from tests._real_browser import launch_page

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = json.loads((ROOT / "config/sites/chatgpt.com.json").read_text(encoding="utf-8"))
PRESET = PAYLOAD["config"]["presets"][PAYLOAD["config"]["default_preset"]]
SELECTORS = PRESET["selectors"]


@pytest.fixture(scope="module")
def page():
    with closing(launch_page()) as pages:
        page = next(pages)
        page.get("data:text/html,<html><body></body></html>")
        page.wait.doc_loaded()
        yield page


def load(page, html):
    # Reuse the temporary document; repeated navigations can race CDP contexts.
    page.run_js("document.body.innerHTML = arguments[0]", html)


def texts(page, key):
    if not SELECTORS.get(key):
        return []
    return page.run_js(
        "return Array.from(document.querySelectorAll(arguments[0]), e => e.textContent)",
        SELECTORS[key],
    )


@pytest.mark.real_browser
def test_dil_selects_answer_roots_without_user_sidebar_or_nested_duplicates(page):
    load(page, '''<aside>Sidebar</aside>
    <div data-content-search-unit-key="turn1:user">User prompt</div>
    <div data-content-search-unit-key="turn1:assistant">
      <div data-response-body="answer1">
        <div data-markdown-text-style="assistant-message">
          <div data-dil-response data-dil-message-id="answer1"><p>First answer</p></div>
        </div>
      </div>
    </div>
    <div data-content-search-unit-key="turn2:user">New prompt</div>
    <div data-content-search-unit-key="turn2:assistant">
      <div data-response-body="answer2">
        <div data-markdown-text-style="assistant-message">
          <div data-dil-response data-dil-message-id="answer2"><p>Latest answer</p></div>
        </div>
      </div>
    </div>''')
    assert [t.strip() for t in texts(page, "result_container")] == ["First answer", "Latest answer"]
    assert len(texts(page, "message_wrapper")) == 4


@pytest.mark.real_browser
@pytest.mark.parametrize("tag", ["section", "article"])
def test_legacy_text_answer_is_still_selected_once(page, tag):
    load(page, f'''<{tag} data-testid="conversation-turn-1">
      <div data-message-author-role="user">User prompt</div>
    </{tag}>
    <{tag} data-testid="conversation-turn-2">
      <div data-message-author-role="assistant"><div class="markdown">Legacy answer</div></div>
    </{tag}>''')
    assert texts(page, "result_container") == ["Legacy answer"]
    assert len(texts(page, "message_wrapper")) == 2


@pytest.mark.real_browser
@pytest.mark.parametrize("tag", ["section", "article"])
def test_legacy_image_only_reply_is_not_lost(page, tag):
    load(page, f'<{tag} data-testid="conversation-turn-2"><div id="image-1"><img alt="Generated"></div></{tag}>')
    assert len(texts(page, "result_container")) == 1


@pytest.mark.real_browser
@pytest.mark.parametrize("label", ["Stop streaming", "Stop generating", "停止流式传输", "停止生成", "停止"])
def test_generating_indicator_uses_stop_button_not_voice_or_send(page, label):
    load(page, f'<button aria-label="Send">Send</button><button aria-label="开始语音">Voice</button><button aria-label="{label}">Stop</button>')
    assert texts(page, "generating_indicator") == ["Stop"]


@pytest.mark.real_browser
def test_dil_body_keeps_code_and_images_inside_one_result(page):
    load(page, '''<div data-markdown-text-style="assistant-message"><div data-dil-response>
      <p>Answer</p><pre><code>print(1)</code></pre><img alt="Generated">
    </div></div>''')
    result = texts(page, "result_container")
    assert len(result) == 1
    assert "print(1)" in result[0]
    assert page.run_js("return document.querySelector(arguments[0]).querySelectorAll('img').length", SELECTORS["result_container"]) == 1


def test_stream_target_stays_exact_and_does_not_capture_prepare():
    import re

    pattern = PRESET["stream_config"]["network"]["stream_match_pattern"]
    assert re.search(pattern, "https://chatgpt.com/backend-api/f/conversation")
    assert not re.search(pattern, "https://chatgpt.com/backend-api/f/conversation/prepare")
