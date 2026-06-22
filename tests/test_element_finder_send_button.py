from app.core.elements import ElementFinder


class FakeStates:
    is_displayed = True
    is_enabled = True


class FakeElement:
    tag = "button"

    def __init__(self, attrs=None, text="", html=""):
        self._attrs = attrs or {}
        self.text = text
        self.html = html
        self.states = FakeStates()

    def attr(self, name):
        return self._attrs.get(name)


class FakeTab:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def ele(self, selector, timeout=0):
        self.calls.append((selector, timeout))
        return self.responses.get(selector)


def test_send_button_primary_selector_is_trusted_without_send_semantics():
    primary = FakeElement(attrs={"class": "chatglm-icon-button"})
    tab = FakeTab({"css:button.chatglm-submit": primary})
    finder = ElementFinder(tab)

    found = finder.find_with_fallback(
        "button.chatglm-submit",
        "send_btn",
        timeout=1.0,
    )

    assert found is primary
    assert [selector for selector, _timeout in tab.calls] == ["css:button.chatglm-submit"]


def test_send_button_primary_stop_state_still_uses_fallback():
    stop_button = FakeElement(attrs={"aria-label": "Stop generation"})
    fallback = FakeElement(attrs={"aria-label": "Submit"})
    tab = FakeTab(
        {
            "css:button.chatglm-submit": stop_button,
            'css:button[aria-label="Send message"][type="submit"]:not(:disabled):not([aria-disabled="true"])': fallback,
        }
    )
    finder = ElementFinder(tab)

    found = finder.find_with_fallback(
        "button.chatglm-submit",
        "send_btn",
        timeout=1.0,
    )

    assert found is fallback
    assert [selector for selector, _timeout in tab.calls] == [
        "css:button.chatglm-submit",
        'css:button[aria-label="Send message"][type="submit"]:not(:disabled):not([aria-disabled="true"])',
    ]


def test_grouped_primary_selector_does_not_retry_whole_selector():
    fallback = FakeElement(attrs={"class": "scroll-display-none"})
    grouped = "textarea.scroll-display-none[rows=\"1\"], textarea.scroll-display-none, textarea[rows=\"1\"], textarea"
    tab = FakeTab({"css:textarea.scroll-display-none": fallback})
    finder = ElementFinder(tab)

    found = finder.find_with_fallback(
        grouped,
        "input_box",
        timeout=3.0,
    )

    assert found is fallback
    assert [selector for selector, _timeout in tab.calls] == [
        'css:textarea.scroll-display-none[rows="1"]',
        "css:textarea.scroll-display-none",
    ]
    assert all(timeout <= 0.25 for _selector, timeout in tab.calls)
