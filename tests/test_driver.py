"""R2-1 第 1 步：驱动层（统一异常、定位规则、DrissionPage 实现、假驱动）。"""

from __future__ import annotations

import pytest
from DrissionPage import errors as dp_errors

from app.core.driver import (
    ContextLost,
    DriverDisconnected,
    DriverError,
    DriverTimeout,
    DrissionTabDriver,
    FakeElement,
    FakeTabDriver,
    ScriptError,
    classify_driver_error,
    driver_for_tab,
    to_locator,
    translate_error,
    translated_errors,
)
from tests._real_browser import launch_page


# --------------------------------------------------------------------------- 统一异常


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (dp_errors.ContextLostError(), ContextLost),
        (RuntimeError("Cannot find context with specified id"), ContextLost),
        (RuntimeError("页面被刷新，请稍后重试"), ContextLost),
        (RuntimeError("Execution context was destroyed, most likely because of a navigation"), ContextLost),
        (dp_errors.PageDisconnectedError(), DriverDisconnected),
        (RuntimeError("与页面的连接已断开"), DriverDisconnected),
        (RuntimeError("Target closed"), DriverDisconnected),
        (RuntimeError("websocket is already closed"), DriverDisconnected),
        (dp_errors.WaitTimeoutError(), DriverTimeout),
        (TimeoutError("slow"), DriverTimeout),
        (dp_errors.JavaScriptError(), ScriptError),
        (ValueError("something else"), DriverError),
    ],
)
def test_classification(error, expected):
    assert classify_driver_error(error) is expected
    translated = translate_error(error)
    assert type(translated) is expected
    assert str(translated) == str(error)  # 消息原样保留，旧的字符串判断继续有效
    assert translated.original is error


def test_classifier_covers_every_existing_string_rule():
    """原来散落在三处的判断，统一分类器必须给出一致的结论。"""
    from app.core.page_lifecycle import is_page_refresh_error
    from app.core.tab_pool_parts.pool_contexts import TabPoolContextMixin
    from app.services.command_engine_pages import CommandEnginePagesMixin

    samples = [
        "页面被刷新", "Page was refreshed", "page is refreshing", "page refreshed", "Try waiting for page refresh",
        "Cannot find default execution context", "Execution context was destroyed", "execution context is not available",
        "context was destroyed", "连接已断开", "connection disconnected", "Target closed", "Session closed",
        "WebSocket connection closed",
    ]
    for text in samples:
        error = RuntimeError(text)
        if is_page_refresh_error(error) or TabPoolContextMixin._is_execution_context_error(error):
            assert classify_driver_error(error) is ContextLost, text
        if CommandEnginePagesMixin._looks_like_page_disconnected_error(error):
            assert classify_driver_error(error) is DriverDisconnected, text


def test_translated_errors_keeps_cause_and_passes_driver_errors_through():
    with pytest.raises(ContextLost) as exc:
        with translated_errors():
            raise dp_errors.ContextLostError("gone")
    assert isinstance(exc.value.__cause__, dp_errors.ContextLostError)
    original = DriverTimeout("t")
    with pytest.raises(DriverTimeout) as again:
        with translated_errors():
            raise original
    assert again.value is original


# --------------------------------------------------------------------------- DrissionPage 实现（替身标签页）


class _NoneElement:
    def __bool__(self):
        return False


class _StubElement:
    tag = "textarea"
    text = "hello"

    def __init__(self):
        self.clicked = []
        self.typed = []

    def attr(self, name):
        return {"id": "prompt"}.get(name)

    def click(self, by_js=False):
        self.clicked.append(by_js)

    def input(self, text, clear=False):
        self.typed.append((text, clear))

    def run_js(self, script, *args):
        return "ran"


class _StubTab:
    tab_id = "TAB-1"
    url = "https://chat.example.com/"
    title = "Chat"

    def __init__(self):
        self.calls = []
        self.element = _StubElement()
        self.raise_on_js = None

    def run_js(self, script, *args, as_expr=False, timeout=None):
        self.calls.append(("run_js", script, args, as_expr, timeout))
        if self.raise_on_js:
            raise self.raise_on_js
        return 42

    def run_cdp(self, cmd, **params):
        self.calls.append(("run_cdp", cmd, params))
        return {"ok": True}

    def ele(self, locator, timeout=None):
        self.calls.append(("ele", locator, timeout))
        return self.element if locator == "css:textarea" else _NoneElement()

    def eles(self, locator, timeout=None):
        self.calls.append(("eles", locator, timeout))
        return [self.element, _NoneElement()] if locator == "css:textarea" else []

    def get(self, url, timeout=None):
        self.calls.append(("get", url, timeout))
        return True

    def refresh(self, ignore_cache=False):
        self.calls.append(("refresh", ignore_cache))

    def get_screenshot(self, as_bytes=None, full_page=False):
        self.calls.append(("get_screenshot", as_bytes, full_page))
        return b"\x89PNG..."


def test_drission_driver_forwards_calls():
    tab = _StubTab()
    driver = DrissionTabDriver(tab)
    assert (driver.tab_id, driver.url, driver.title, driver.raw) == ("TAB-1", "https://chat.example.com/", "Chat", tab)
    assert driver.run_js("return 1", 5, timeout=3, as_expr=True) == 42
    assert tab.calls[-1] == ("run_js", "return 1", (5,), True, 3)
    assert driver.run_cdp("Page.reload", ignoreCache=True) == {"ok": True}

    element = driver.find("textarea", timeout=0.5)  # 裸选择器按 CSS 处理
    assert tab.calls[-1] == ("ele", "css:textarea", 0.5)
    assert element.tag == "textarea" and element.text == "hello" and element.attr("id") == "prompt"
    element.click(by_js=True)
    element.input("hi", clear=True)
    assert tab.element.clicked == [True] and tab.element.typed == [("hi", True)]
    assert driver.find("#missing") is None  # NoneElement（假值）-> None
    assert len(driver.find_all("textarea")) == 1  # 假值元素被过滤

    assert driver.navigate("https://chat.example.com/new", timeout=10) is True
    driver.reload(ignore_cache=True)
    assert driver.screenshot(full_page=True).startswith(b"\x89PNG")
    driver.insert_text("你好")
    assert tab.calls[-1] == ("run_cdp", "Input.insertText", {"text": "你好"})


def test_drission_driver_translates_errors_with_original_message():
    tab = _StubTab()
    tab.raise_on_js = dp_errors.ContextLostError("Cannot find context with specified id")
    with pytest.raises(ContextLost, match="Cannot find context"):
        DrissionTabDriver(tab).run_js("return document.title")


def test_driver_for_tab_reuses_wrapper_and_passes_drivers_through():
    tab = _StubTab()
    first = driver_for_tab(tab)
    assert driver_for_tab(tab) is first
    fake = FakeTabDriver()
    assert driver_for_tab(fake) is fake
    with pytest.raises(ValueError):
        driver_for_tab(None)


def test_tab_session_exposes_driver():
    from app.core.tab_pool_parts.session import TabSession

    tab = _StubTab()
    session = TabSession(id="s1", tab=tab)
    assert isinstance(session.driver, DrissionTabDriver) and session.driver.raw is tab
    assert session.driver is session.driver


# --------------------------------------------------------------------------- 假驱动


def test_fake_driver_matching_and_failures():
    driver = FakeTabDriver(
        [
            FakeElement("textarea", id="prompt", placeholder="Ask"),
            FakeElement("button", "发送", class_="btn send", type="submit"),
            FakeElement("button", "停止", class_="btn"),
        ],
        url="https://chat.example.com/",
        js=lambda script, args: "pong" if "ping" in script else None,
    )
    assert len(driver.find_all("button")) == 2
    assert driver.find("#prompt").tag == "textarea"
    assert driver.find("button.send").text == "发送"
    assert driver.find('button[type="submit"]').text == "发送"
    assert driver.find("textarea[placeholder=Ask]") is not None
    assert driver.find("tag:textarea") is not None and driver.find("@id=prompt") is not None
    assert driver.find(".missing") is None
    with pytest.raises(ScriptError):
        driver.find_all("div > span")  # 不支持的写法按非法选择器处理
    assert driver.run_js("ping") == "pong" and driver.js_calls[-1][0] == "ping"
    driver.fail_next(ContextLost("页面被刷新"))
    with pytest.raises(ContextLost):
        driver.run_js("return 1")
    assert driver.run_js("return 1") is None  # 只影响下一次调用
    driver.insert_text("abc")
    assert driver.inserted_text == ["abc"]
    assert driver.navigate("https://chat.example.com/c/1") and driver.url.endswith("/c/1")


def test_locator_rules_are_shared():
    from app.services.adapter_health import to_locator as health_to_locator

    assert health_to_locator is to_locator
    assert to_locator("//span[text()='x']") == "xpath://span[text()='x']"
    assert to_locator("tag:div@@class=a") == "tag:div@@class=a"


# --------------------------------------------------------------------------- 真实浏览器（本机运行）


@pytest.fixture(scope="module")
def real_page():
    yield from launch_page()


def test_real_browser_driver_roundtrip(real_page):
    driver = driver_for_tab(real_page)
    assert driver.navigate(
        "data:text/html;charset=utf-8,<title>driver</title><textarea id='prompt'></textarea><button class='send'>发送</button>"
    )
    assert driver.run_js("return 6 * 7") == 42
    assert driver.title == "driver"
    textarea = driver.find("#prompt")
    assert textarea is not None and textarea.tag == "textarea"
    textarea.click(by_js=True)
    driver.run_js("document.getElementById('prompt').focus()")
    driver.insert_text("你好，驱动")
    assert driver.run_js("return document.getElementById('prompt').value") == "你好，驱动"
    assert driver.find_all("button.send")[0].text == "发送"
    assert driver.find(".does-not-exist") is None
    evaluated = driver.run_cdp("Runtime.evaluate", expression="1 + 2", returnByValue=True)
    assert evaluated["result"]["value"] == 3
    assert driver.screenshot().startswith(b"\x89PNG")
    with pytest.raises(ScriptError):
        driver.run_js("throw new Error('boom')")
