from contextlib import contextmanager

import pytest

from app.core.config import WorkflowError
from app.core.workflow.executor_actions import WorkflowExecutorActionMixin
from app.core.workflow.executor_send import WorkflowExecutorSendMixin
from app.core.page_lifecycle import BACKGROUND_WAKE_CDP_TIMEOUT


class _StrictRunJsTab:
    def __init__(self):
        self.payload = None

    def run_js(self, script, argument, timeout):
        assert isinstance(argument, dict)
        assert "payload.probes" in script
        assert timeout == 1.0
        self.payload = argument
        return [
            {
                "target": probe["target"],
                "state": probe["state"],
                "present": False,
                "visible": False,
                "matched": probe["state"] == "absent",
            }
            for probe in argument["probes"]
        ]


def test_click_verification_wraps_probe_list_for_drissionpage():
    executor = WorkflowExecutorActionMixin.__new__(WorkflowExecutorActionMixin)
    executor.tab = _StrictRunJsTab()
    executor._selectors = {"retry button": "button.retry"}

    result = executor._probe_click_verification_conditions(
        [{"target": "retry button", "state": "absent"}]
    )

    assert executor.tab.payload == {
        "probes": [
            {
                "target": "retry button",
                "selector": "button.retry",
                "state": "absent",
            }
        ]
    }
    assert result == [
        {
            "target": "retry button",
            "state": "absent",
            "present": False,
            "visible": False,
            "matched": True,
        }
    ]


class _RectUnavailableElement:
    _backend_id = 4225

    @property
    def rect(self):
        raise RuntimeError("rect unavailable")


class _BoxModelTab:
    def __init__(self):
        self.calls = []

    def run_cdp(self, method, **kwargs):
        self.calls.append((method, kwargs))
        return {
            "model": {
                "content": [10, 20, 110, 20, 110, 60, 10, 60],
            }
        }


def test_element_position_falls_back_to_native_cdp_box_model():
    executor = WorkflowExecutorActionMixin.__new__(WorkflowExecutorActionMixin)
    executor.tab = _BoxModelTab()

    position = executor._get_element_viewport_pos(_RectUnavailableElement())

    assert position == (60, 40)
    assert executor.tab.calls == [
        (
            "DOM.getBoxModel",
            {
                "backendNodeId": 4225,
                "_timeout": BACKGROUND_WAKE_CDP_TIMEOUT,
            },
        )
    ]


def test_box_model_skips_degenerate_content_quad():
    executor = WorkflowExecutorActionMixin.__new__(WorkflowExecutorActionMixin)

    class _BorderBoxModelTab:
        @staticmethod
        def run_cdp(_method, **_kwargs):
            return {
                "model": {
                    "content": [20, 30, 20, 30, 20, 30, 20, 30],
                    "border": [15, 25, 125, 25, 125, 65, 15, 65],
                }
            }

    executor.tab = _BorderBoxModelTab()

    assert executor._get_element_viewport_pos(_RectUnavailableElement()) == (70, 45)


class _StopOnlyFinder:
    _last_send_btn_blocked_by_stop = True

    @staticmethod
    def find_with_fallback(_selector, _target_key):
        return None


def test_send_click_reports_not_dispatched_when_only_stop_button_exists():
    executor = WorkflowExecutorActionMixin.__new__(WorkflowExecutorActionMixin)
    executor.finder = _StopOnlyFinder()
    executor._check_cancelled = lambda: False
    pressed_keys = []
    executor._execute_keypress = pressed_keys.append

    @contextmanager
    def interaction_slot(_action, _target_key):
        yield True

    executor._page_interaction_slot = interaction_slot

    dispatched = executor._execute_click("button.send", "send_btn", optional=False)

    assert dispatched is False
    assert pressed_keys == []


def test_preexisting_generation_is_waited_out_before_send():
    executor = WorkflowExecutorSendMixin.__new__(WorkflowExecutorSendMixin)
    states = iter(
        [
            {"generating": True, "sendLooksLikeStop": True},
            {"generating": False, "sendLooksLikeStop": False},
        ]
    )
    executor._probe_send_post_click_state = lambda _selector: next(states)
    executor._get_send_confirmation_window = lambda *_args, **_kwargs: 0.02
    executor._check_cancelled = lambda: False

    assert executor._wait_for_send_idle_before_action("button.send") is True


def test_stealth_send_does_not_confirm_when_click_was_skipped():
    executor = WorkflowExecutorSendMixin.__new__(WorkflowExecutorSendMixin)
    executor._context = {"images": []}
    executor._network_monitor = None
    executor._get_send_confirmation_flag = lambda *_args, **_kwargs: True
    executor._read_stable_send_input_len = lambda *_args, **_kwargs: 12
    executor._wait_for_send_idle_before_action = lambda _selector: True
    executor._execute_click = lambda *_args, **_kwargs: False

    with pytest.raises(WorkflowError, match="send_action_not_dispatched"):
        executor._execute_click_send_stealth("button.send", "send_btn", optional=False)
