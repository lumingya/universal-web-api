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

    element = driver.find("css:textarea", timeout=0.5)  # 定位器按 DrissionPage 原生语法原样传递
    assert tab.calls[-1] == ("ele", "css:textarea", 0.5)
    assert element.tag == "textarea" and element.text == "hello" and element.attr("id") == "prompt"
    element.click(by_js=True)
    element.input("hi", clear=True)
    assert tab.element.clicked == [True] and tab.element.typed == [("hi", True)]
    assert driver.find("#missing") is None  # NoneElement（假值）-> None
    assert len(driver.find_all("css:textarea")) == 1  # 假值元素被过滤
    driver.find("发送")
    assert tab.calls[-1] == ("ele", "发送", None)  # 不带前缀＝DrissionPage 文本匹配，驱动不改写

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
    assert driver.find_all("css:button.send")[0].text == "发送"
    assert driver.find("发送") is not None  # DrissionPage 原生：不带前缀按文本匹配
    assert driver.find(".does-not-exist") is None
    evaluated = driver.run_cdp("Runtime.evaluate", expression="1 + 2", returnByValue=True)
    assert evaluated["result"]["value"] == 3
    assert driver.screenshot().startswith(b"\x89PNG")
    with pytest.raises(ScriptError):
        driver.run_js("throw new Error('boom')")


# --------------------------------------------------------------------------- 收口阶段新增：元素包装、动作链、监听器、浏览器级 CDP


def test_only_drissionpage_errors_are_translated_builtin_errors_pass_through():
    """回归：network_monitor 依赖 `except TypeError` 退回旧签名；内置异常必须原样抛出。"""
    class _OldListenTab(_StubTab):
        def run_js(self, script, *args, **kwargs):
            raise TypeError("start() got an unexpected keyword argument 'res_type'")

    with pytest.raises(TypeError):
        DrissionTabDriver(_OldListenTab()).run_js("x")
    with pytest.raises(OSError):
        with translated_errors():
            raise OSError("socket closed")


def test_as_element_wrapping_rules_and_click_proxy():
    from app.core.driver import DrissionElement, as_element, unwrap

    raw = _StubElement()
    wrapped = as_element(raw)
    assert isinstance(wrapped, DrissionElement) and wrapped.raw is raw
    assert as_element(wrapped) is wrapped  # 不重复包装
    assert as_element(None) is None
    falsy = _NoneElement()
    assert as_element(falsy) is falsy  # 假值（NoneElement）原样返回，保持原有报错行为
    fake = FakeElement("div")
    assert as_element(fake) is fake
    assert wrapped == raw and hash(wrapped) == hash(raw) and unwrap(wrapped) is raw
    wrapped.click(by_js=True)  # 点击器代理可直接调用
    assert raw.clicked == [True]
    assert wrapped.run_js("return 1") == "ran"
    assert wrapped.tag == "textarea" and wrapped.text == "hello"


def test_run_js_unwraps_element_arguments():
    from app.core.driver import as_element

    tab = _StubTab()
    element = as_element(_StubElement())
    DrissionTabDriver(tab).run_js("arguments[0].focus()", element)
    assert tab.calls[-1][2][0] is element.raw  # DrissionPage 只认原始元素


def test_actions_wrapper_chains_and_unwraps():
    from app.core.driver import as_element

    class _Actions:
        def __init__(self):
            self.log = []

        def move_to(self, target, **kwargs):
            self.log.append(("move_to", target))
            return self

        def click(self):
            self.log.append(("click",))
            return self

        def position(self):
            return (1, 2)

    class _TabWithActions(_StubTab):
        def __init__(self):
            super().__init__()
            self.actions = _Actions()

    tab = _TabWithActions()
    element = as_element(_StubElement())
    driver = DrissionTabDriver(tab)
    chained = driver.actions.move_to(element).click()
    assert tab.actions.log == [("move_to", element.raw), ("click",)]
    assert chained.raw is tab.actions  # 链式调用返回包装对象
    assert driver.actions.position() == (1, 2)  # 非链式返回值原样透传


def test_listener_wrapper_preserves_network_monitor_helpers():
    import queue

    class _Driver:
        is_running = True

    class _Listen:
        def __init__(self):
            self.listening = True
            self._driver = _Driver()
            self._network_enabled = True
            self._running_targets = 2
            self._running_requests = 3
            self._caught = queue.Queue()
            self._caught.put("packet")
            self._reuse_driver = False
            self.stopped = 0
            self.cleared = 0

        def stop(self):
            self.stopped += 1
            self.listening = False

        def clear(self):
            self.cleared += 1

    class _TabWithListen(_StubTab):
        def __init__(self):
            super().__init__()
            self.listen = _Listen()

    tab = _TabWithListen()
    listener = DrissionTabDriver(tab).listener
    assert listener.is_active() and listener.listening
    assert listener.counters() == {"running_targets": 2, "running_requests": 3, "queued_packets": 1}
    listener.reuse_driver = True
    assert tab.listen._reuse_driver is True
    listener.safe_stop()
    assert tab.listen.stopped == 1 and tab.listen.cleared == 1 and not listener.is_active()
    listener.force_reset()
    assert tab.listen._driver is None and tab.listen._network_enabled is False
    assert DrissionTabDriver(_StubTab()).listener_or_none is None  # 没有 listen 属性


def test_browser_driver_runs_browser_level_cdp():
    from app.core.driver import browser_driver

    class _Browser:
        def __init__(self):
            self.calls = []

        def _run_cdp(self, method, **params):
            self.calls.append((method, params))
            return {"targetInfos": []}

    browser = _Browser()
    driver = browser_driver(browser)
    assert driver.run_cdp("Target.getTargets") == {"targetInfos": []}
    assert browser.calls == [("Target.getTargets", {})]
    assert browser_driver(browser) is driver and browser_driver(driver) is driver
