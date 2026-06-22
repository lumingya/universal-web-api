import time

import app.core.workflow.executor_actions as executor_actions
import app.utils.human_mouse as human_mouse
from app.core.page_lifecycle import BACKGROUND_WAKE_CDP_TIMEOUT
from app.core.config import WorkflowError
from app.core.workflow.executor_actions import WorkflowExecutorActionMixin


class FakeRect:
    viewport_midpoint = (40, 50)


class FakeElement:
    tag = "button"
    rect = FakeRect()


class FakeTab:
    def __init__(self):
        self.calls = []

    def run_cdp(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return {}


class Harness(WorkflowExecutorActionMixin):
    def __init__(self):
        self.tab = FakeTab()
        self._mouse_pos = None

    def _check_cancelled(self):
        return False

    def _get_viewport_size(self):
        return (1200, 800)


OLD_CHAT_URL = "https://chatglm.cn/main/chat/6a356127a8bc59f55e984564"
NEW_CHAT_URL = "https://chatglm.cn/main/chat/6a356222a8bc59f55e984565"


class FakeUrlTab:
    tab_id = "raw-tab"

    def __init__(self, url=NEW_CHAT_URL):
        self.snapshot_url = url
        self.calls = []

    @property
    def url(self):
        raise AssertionError("hot-path URL checks should not read tab.url directly")

    def run_cdp(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command == "Page.getNavigationHistory":
            return {
                "currentIndex": 0,
                "entries": [{"url": self.snapshot_url}],
            }
        if command == "Target.getTargetInfo":
            return {"targetInfo": {"url": self.snapshot_url}}
        return {}


class EmptyUrlTab(FakeUrlTab):
    def __init__(self):
        super().__init__(url="")

    def run_cdp(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command == "Page.getNavigationHistory":
            return {"currentIndex": 0, "entries": [{"url": ""}]}
        if command == "Target.getTargetInfo":
            return {"targetInfo": {"url": ""}}
        return {}


class TransitionHarness(WorkflowExecutorActionMixin):
    def __init__(self, tab):
        self.tab = tab
        self.session = type("Session", (), {"id": "session-1", "last_known_url": ""})()
        self._site_advanced_config = {"url_transition_wait_on_new_chat": True}
        self._last_new_chat_clicked_url = OLD_CHAT_URL
        self._last_new_chat_clicked_snapshot = {
            "url": OLD_CHAT_URL,
            "session_id": self.session.id,
            "tab_id": tab.tab_id,
            "tab_ref_id": id(tab),
        }

    def _check_cancelled(self):
        return False

    def _coerce_bool(self, value, default=False):
        return bool(default if value is None else value)


def test_background_cdp_click_is_fire_and_forget(monkeypatch):
    harness = Harness()
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(executor_actions.random, "randint", lambda _start, _end: 0)
    monkeypatch.setattr(executor_actions.random, "uniform", lambda start, _end: start)
    monkeypatch.setattr(human_mouse.random, "randint", lambda start, _end: start)
    monkeypatch.setattr(human_mouse.random, "uniform", lambda start, _end: start)

    assert harness._background_cdp_click_element(FakeElement(), "new_chat_btn") is True

    dispatches = [
        kwargs
        for command, kwargs in harness.tab.calls
        if command == "Input.dispatchMouseEvent"
    ]
    assert any(call["type"] == "mousePressed" for call in dispatches)
    assert any(call["type"] == "mouseReleased" for call in dispatches)
    assert all(call["_timeout"] == 0 for call in dispatches)


def test_new_chat_transition_uses_current_url_without_polling():
    tab = FakeUrlTab()
    harness = TransitionHarness(tab)

    result = harness._wait_for_new_chat_url_transition_if_needed(
        fill_after_new_chat=True,
        current_url=NEW_CHAT_URL,
    )

    assert result == NEW_CHAT_URL
    assert tab.calls == []


def test_url_snapshot_uses_bounded_cdp_timeout():
    tab = FakeUrlTab()
    harness = TransitionHarness(tab)

    assert harness._get_current_url_snapshot("test") == NEW_CHAT_URL

    assert tab.calls
    assert tab.calls[0][0] == "Page.getNavigationHistory"
    assert tab.calls[0][1].get("_timeout") == BACKGROUND_WAKE_CDP_TIMEOUT


def test_new_chat_transition_poll_does_not_use_cached_url(monkeypatch):
    tab = EmptyUrlTab()
    harness = TransitionHarness(tab)
    harness.session.last_known_url = NEW_CHAT_URL
    monkeypatch.setattr(executor_actions.time, "sleep", lambda _seconds: None)

    original_time = executor_actions.time.time
    ticks = iter([original_time(), original_time() + 10.0])
    monkeypatch.setattr(executor_actions.time, "time", lambda: next(ticks, original_time() + 10.0))

    try:
        harness._wait_for_new_chat_url_transition_if_needed(
            fill_after_new_chat=True,
            current_url=OLD_CHAT_URL,
        )
    except WorkflowError as exc:
        assert str(exc) == "new_chat_transition_timeout"
    else:
        raise AssertionError("transition polling must not succeed from session cache")
