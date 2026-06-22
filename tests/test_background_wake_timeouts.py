from app.core.page_lifecycle import (
    BACKGROUND_WAKE_CDP_TIMEOUT,
    BACKGROUND_WAKE_JS_TIMEOUT,
    install_visibility_emulation,
)
from app.core.workflow.executor_interaction import WorkflowExecutorInteractionMixin


class FakeLogger:
    def debug_throttled(self, *_args, **_kwargs):
        return None


class FakeTab:
    def __init__(self):
        self.cdp_calls = []
        self.js_calls = []

    def run_cdp(self, command, **kwargs):
        self.cdp_calls.append((command, kwargs))
        return {}

    def run_js(self, script, *args, **kwargs):
        self.js_calls.append((script, args, kwargs))
        if "visibilityState" in script:
            return {
                "hidden": False,
                "visibilityState": "visible",
                "hasFocus": True,
                "wasDiscarded": False,
            }
        return ""


class Harness(WorkflowExecutorInteractionMixin):
    def __init__(self):
        self.tab = FakeTab()
        self.session = object()
        self.stealth_mode = False
        self._workflow_focus_emulation_active = True
        self._workflow_visibility_emulation_active = True

    def _coerce_bool(self, value, default=False):
        return bool(default if value is None else value)

    def _get_workflow_wake_settings(self):
        return {
            "wake_before_interaction": True,
            "focus_emulation": True,
        }


def test_visibility_emulation_uses_bounded_run_js():
    tab = FakeTab()

    assert install_visibility_emulation(tab, owner=object(), reason="test") is True

    assert tab.cdp_calls
    assert tab.cdp_calls[0][1].get("_timeout") == BACKGROUND_WAKE_CDP_TIMEOUT
    assert tab.js_calls
    assert all(call[2].get("timeout") == BACKGROUND_WAKE_JS_TIMEOUT for call in tab.js_calls)


def test_normal_interaction_wake_probe_uses_bounded_run_js(monkeypatch):
    harness = Harness()
    monkeypatch.setattr(
        "app.core.workflow.executor_interaction.install_visibility_emulation",
        lambda *_args, **_kwargs: True,
    )

    with harness._wake_page_for_interaction("FILL_INPUT:input_box"):
        pass

    assert harness.tab.js_calls
    assert harness.tab.js_calls[-1][2].get("timeout") == BACKGROUND_WAKE_JS_TIMEOUT
    wake_cdp_calls = [
        kwargs
        for command, kwargs in harness.tab.cdp_calls
        if command in {"Emulation.setFocusEmulationEnabled", "Page.setWebLifecycleState"}
    ]
    assert wake_cdp_calls
    assert all(call.get("_timeout") == BACKGROUND_WAKE_CDP_TIMEOUT for call in wake_cdp_calls)
