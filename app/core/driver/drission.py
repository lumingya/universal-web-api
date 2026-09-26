"""R2-1：基于 DrissionPage 的驱动实现——只做转发与异常翻译，不改变任何行为。

包装对象：
- ``DrissionTabDriver``：标签页（run_js / run_cdp / find / find_all / navigate / reload / screenshot /
  insert_text，以及 ``listener`` 网络监听、``actions`` 键鼠动作链）；
- ``DrissionElement``：元素。过渡期内未包装的属性经 ``__getattr__`` 转发给原元素；
- ``DrissionBrowserDriver``：浏览器级 CDP（``browser._run_cdp``）。

兼容性约定：
- 定位器按 DrissionPage 原生语法**原样传递**（不带前缀的字符串在 DrissionPage 中表示按文本模糊匹配）；
  配置里的选择器（默认是 CSS）请先用 ``to_locator()`` 规范化；
- 超时、as_expr 等可选参数只在调用方显式给出时才转发，默认行为与直接调用 DrissionPage 完全一致，
  也兼容签名较窄的测试替身；
- 作为参数传入 run_js / run_cdp / 动作链的包装元素会自动换回原元素（DrissionPage 只认原始元素）。
"""

from __future__ import annotations

import weakref
from typing import Any, List, Optional

from app.core.driver.errors import translated_errors


def unwrap(value: Any) -> Any:
    """包装对象 -> 原始 DrissionPage 对象；其他值原样返回。"""
    if isinstance(value, (DrissionElement, DrissionTabDriver, DrissionBrowserDriver)):
        return value.raw
    return value


def _unwrap_args(args: tuple, kwargs: dict) -> tuple:
    return tuple(unwrap(a) for a in args), {k: unwrap(v) for k, v in kwargs.items()}


def _optional_kwargs(**values: Any) -> dict:
    """只保留调用方显式给出（非 None / 非 False）的可选参数。"""
    return {k: v for k, v in values.items() if v is not None and v is not False}


class DrissionElement:
    __slots__ = ("_ele",)

    def __init__(self, ele: Any):
        object.__setattr__(self, "_ele", ele)

    # ---- 标识
    @property
    def raw(self) -> Any:
        return self._ele

    def __bool__(self) -> bool:
        return bool(self._ele)

    def __eq__(self, other: Any) -> bool:
        return self._ele == unwrap(other)

    def __hash__(self) -> int:
        return hash(self._ele)

    def __repr__(self) -> str:
        return f"DrissionElement({self._ele!r})"

    # ---- 接口内的方法
    @property
    def tag(self) -> str:
        with translated_errors():
            return str(getattr(self._ele, "tag", "") or "")

    @property
    def text(self) -> str:
        with translated_errors():
            return str(getattr(self._ele, "text", "") or "")

    def attr(self, name: str) -> Optional[str]:
        with translated_errors():
            value = self._ele.attr(name)
        return None if value is None else str(value)

    @property
    def click(self) -> "_Clicker":
        """与 DrissionPage 一致：既可直接调用 ele.click(...)，也可 ele.click.left() / .at(x, y) 等。"""
        return _Clicker(self._ele.click)

    def input(self, *args: Any, **kwargs: Any) -> Any:
        with translated_errors():
            return self._ele.input(*args, **kwargs)

    def run_js(self, script: str, *args: Any, timeout: Optional[float] = None, as_expr: bool = False) -> Any:
        args, _ = _unwrap_args(args, {})
        with translated_errors():
            return self._ele.run_js(script, *args, **_optional_kwargs(as_expr=as_expr, timeout=timeout))

    def find(self, locator: str, timeout: Optional[float] = None) -> Optional["DrissionElement"]:
        with translated_errors():
            ele = self._ele.ele(locator, **_optional_kwargs(timeout=timeout))
        return DrissionElement(ele) if ele else None

    def find_all(self, locator: str, timeout: Optional[float] = None) -> List["DrissionElement"]:
        with translated_errors():
            elements = self._ele.eles(locator, **_optional_kwargs(timeout=timeout))
        if not isinstance(elements, list):
            elements = [elements] if elements else []
        return [DrissionElement(e) for e in elements if e]

    def is_displayed(self) -> bool:
        with translated_errors():
            return bool(self._ele.states.is_displayed)

    def is_alive(self) -> bool:
        with translated_errors():
            return bool(self._ele.states.is_alive)

    # ---- 过渡期：其余 DrissionPage 元素属性原样转发（rect、states、parent()、shadow_root 等）
    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_ele"), name)


class _Clicker:
    """DrissionPage 点击器的代理：调用时翻译异常、拆包参数；其余方法（left/at/for_new_tab…）同样处理。"""

    __slots__ = ("_clicker",)

    def __init__(self, clicker: Any):
        self._clicker = clicker

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = _unwrap_args(args, kwargs)
        with translated_errors():
            return self._clicker(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        target = getattr(object.__getattribute__(self, "_clicker"), name)
        if not callable(target):
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            args, kwargs = _unwrap_args(args, kwargs)
            with translated_errors():
                return target(*args, **kwargs)

        return call


def as_element(obj: Any) -> Any:
    """把 DrissionPage 元素包装成 DrissionElement；已经是包装对象或假值（NoneElement、None）则原样返回。"""
    if obj is None or isinstance(obj, DrissionElement) or not obj:
        return obj
    if hasattr(obj, "matches") and hasattr(obj, "inputs"):  # FakeElement
        return obj
    return DrissionElement(obj)


class DrissionListener:
    """网络监听（tab.listen）的包装。返回的数据包仍是 DrissionPage 的 DataPacket（鸭子类型约定：
    url / method / resourceType / request / response / is_failed 等属性），更换驱动时需要产出同形对象。"""

    __slots__ = ("_listen",)

    def __init__(self, listen: Any):
        self._listen = listen

    @property
    def raw(self) -> Any:
        return self._listen

    @property
    def listening(self) -> bool:
        return bool(getattr(self._listen, "listening", False))

    @property
    def reuse_driver(self) -> bool:
        return bool(getattr(self._listen, "_reuse_driver", False))

    @reuse_driver.setter
    def reuse_driver(self, value: bool) -> None:
        # DrissionPage 的私有开关：复用标签页已有的 CDP 连接，避免为监听单独建连接
        self._listen._reuse_driver = bool(value)

    def start(self, *args: Any, **kwargs: Any) -> Any:
        with translated_errors():
            return self._listen.start(*args, **kwargs)

    def wait(self, *args: Any, **kwargs: Any) -> Any:
        with translated_errors():
            return self._listen.wait(*args, **kwargs)

    def steps(self, *args: Any, **kwargs: Any) -> Any:
        with translated_errors():
            return self._listen.steps(*args, **kwargs)

    def stop(self) -> Any:
        with translated_errors():
            return self._listen.stop()

    def pause(self, *args: Any, **kwargs: Any) -> Any:
        with translated_errors():
            return self._listen.pause(*args, **kwargs)

    def resume(self) -> Any:
        with translated_errors():
            return self._listen.resume()

    def clear(self) -> Any:
        with translated_errors():
            return self._listen.clear()

    # ---- 以下四个方法原样迁自 network_monitor：它们依赖 DrissionPage Listener 的内部状态，属于驱动实现细节
    def is_active(self) -> bool:
        """监听处于活动状态：listening 为真且独立 Driver 连接仍在运行。"""
        try:
            driver = getattr(self._listen, "_driver", None)
            return bool(getattr(self._listen, "listening", False) and driver is not None
                        and getattr(driver, "is_running", False))
        except Exception:
            return False

    def force_reset(self) -> None:
        """stop() 失败时强制复位内部状态（listening / _network_enabled / _driver），再清空缓存。"""
        listen = self._listen
        try:
            setattr(listen, "listening", False)
        except Exception:
            pass
        try:
            if hasattr(listen, "_network_enabled"):
                setattr(listen, "_network_enabled", False)
        except Exception:
            pass
        try:
            if hasattr(listen, "_driver"):
                setattr(listen, "_driver", None)
        except Exception:
            pass
        try:
            clear = getattr(listen, "clear", None)
            if callable(clear):
                clear()
        except Exception:
            pass

    def safe_stop(self) -> None:
        """停止监听（会移除 Network.* 回调并关闭独立的 Driver 连接）；失败则强制复位。"""
        listen = self._listen
        try:
            if getattr(listen, "listening", False):
                listen.stop()
        except Exception:
            self.force_reset()
            return
        try:
            clear = getattr(listen, "clear", None)
            if callable(clear):
                clear()
        except Exception:
            pass

    def counters(self) -> dict:
        """内部计数：进行中的目标数、请求数，以及已捕获待取的数据包数。"""
        listen = self._listen
        try:
            running_targets = int(getattr(listen, "_running_targets", 0) or 0)
        except Exception:
            running_targets = 0
        try:
            running_requests = int(getattr(listen, "_running_requests", 0) or 0)
        except Exception:
            running_requests = 0
        queued_packets = 0
        try:
            caught = getattr(listen, "_caught", None)
            if caught is not None and hasattr(caught, "qsize"):
                queued_packets = int(caught.qsize() or 0)
        except Exception:
            queued_packets = 0
        return {
            "running_targets": max(0, running_targets),
            "running_requests": max(0, running_requests),
            "queued_packets": max(0, queued_packets),
        }

    def __getattr__(self, name: str) -> Any:  # 过渡期：其余属性原样转发
        return getattr(object.__getattribute__(self, "_listen"), name)


class DrissionActions:
    """键鼠动作链（tab.actions）的包装：元素参数自动拆包，链式调用返回包装对象本身。"""

    __slots__ = ("_actions",)

    def __init__(self, actions: Any):
        self._actions = actions

    @property
    def raw(self) -> Any:
        return self._actions

    def __getattr__(self, name: str) -> Any:
        target = getattr(object.__getattribute__(self, "_actions"), name)
        if not callable(target):
            return target

        def call(*args: Any, **kwargs: Any) -> Any:
            args, kwargs = _unwrap_args(args, kwargs)
            with translated_errors():
                result = target(*args, **kwargs)
            return self if result is self._actions else result

        return call


class DrissionTabDriver:
    """包装一个 DrissionPage 标签页（ChromiumTab / ChromiumPage）。"""

    __slots__ = ("_tab", "__weakref__")

    def __init__(self, tab: Any):
        self._tab = tab

    @property
    def raw(self) -> Any:
        return self._tab

    @property
    def tab_id(self) -> str:
        return str(getattr(self._tab, "tab_id", "") or "")

    @property
    def url(self) -> str:
        with translated_errors():
            return str(getattr(self._tab, "url", "") or "")

    @property
    def title(self) -> str:
        with translated_errors():
            return str(getattr(self._tab, "title", "") or "")

    def run_js(self, script: str, *args: Any, timeout: Optional[float] = None, as_expr: bool = False) -> Any:
        # 只传与默认值不同的关键字参数：对 DrissionPage 完全等价，也兼容签名为 run_js(script, *args) 的测试替身
        args, _ = _unwrap_args(args, {})
        with translated_errors():
            return self._tab.run_js(script, *args, **_optional_kwargs(as_expr=as_expr, timeout=timeout))

    def run_cdp(self, method: str, **params: Any) -> Any:
        _, params = _unwrap_args((), params)
        with translated_errors():
            return self._tab.run_cdp(method, **params)

    def find(self, locator: str, timeout: Optional[float] = None) -> Optional[DrissionElement]:
        """不传 timeout 时沿用 DrissionPage 的默认等待（与直接调用 tab.ele 一致）。"""
        with translated_errors():
            ele = self._tab.ele(locator, **_optional_kwargs(timeout=timeout))
        # DrissionPage 找不到元素时返回假值的 NoneElement，而不是抛异常
        return DrissionElement(ele) if ele else None

    def find_all(self, locator: str, timeout: Optional[float] = None) -> List[DrissionElement]:
        with translated_errors():
            elements = self._tab.eles(locator, **_optional_kwargs(timeout=timeout))
        if not isinstance(elements, list):
            elements = [elements] if elements else []
        return [DrissionElement(ele) for ele in elements if ele]

    def navigate(self, url: str, timeout: Optional[float] = None) -> bool:
        with translated_errors():
            return bool(self._tab.get(url, **_optional_kwargs(timeout=timeout)))

    def reload(self, ignore_cache: bool = False) -> None:
        with translated_errors():
            self._tab.refresh(ignore_cache=ignore_cache)

    def screenshot(self, full_page: bool = False) -> bytes:
        with translated_errors():
            return self._tab.get_screenshot(as_bytes="png", full_page=full_page)

    def insert_text(self, text: str) -> None:
        with translated_errors():
            self._tab.run_cdp("Input.insertText", text=str(text))

    @property
    def listener(self) -> DrissionListener:
        return DrissionListener(self._tab.listen)

    @property
    def listener_or_none(self) -> Optional[DrissionListener]:
        """标签页没有 listen 属性（例如某些测试替身）时返回 None，而不是抛异常。"""
        listen = getattr(self._tab, "listen", None)
        return DrissionListener(listen) if listen is not None else None

    @property
    def actions(self) -> DrissionActions:
        return DrissionActions(self._tab.actions)


class DrissionBrowserDriver:
    """浏览器级操作（browser._run_cdp：Target.*、Browser.* 等不属于某个标签页的 CDP 命令）。"""

    __slots__ = ("_browser", "__weakref__")

    def __init__(self, browser: Any):
        self._browser = browser

    @property
    def raw(self) -> Any:
        return self._browser

    def run_cdp(self, method: str, **params: Any) -> Any:
        _, params = _unwrap_args((), params)
        with translated_errors():
            return self._browser._run_cdp(method, **params)


_WRAPPERS: "weakref.WeakKeyDictionary[Any, DrissionTabDriver]" = weakref.WeakKeyDictionary()
_BROWSER_WRAPPERS: "weakref.WeakKeyDictionary[Any, DrissionBrowserDriver]" = weakref.WeakKeyDictionary()


def driver_for_tab(tab: Any):
    """返回标签页对应的驱动；已经是驱动对象则原样返回。同一个标签页对象复用同一个包装。"""
    if tab is None:
        raise ValueError("tab 不能为空")
    if hasattr(tab, "raw") and hasattr(tab, "find_all") and hasattr(tab, "insert_text"):
        return tab
    try:
        driver = _WRAPPERS.get(tab)
        if driver is None:
            driver = DrissionTabDriver(tab)
            _WRAPPERS[tab] = driver
        return driver
    except TypeError:  # 不支持弱引用的对象（例如某些测试替身）：每次新建包装，开销很小
        return DrissionTabDriver(tab)


def browser_driver(browser: Any):
    """返回浏览器对象（DrissionPage Chromium）对应的浏览器级驱动。"""
    if browser is None:
        raise ValueError("browser 不能为空")
    if isinstance(browser, DrissionBrowserDriver) or (hasattr(browser, "raw") and hasattr(browser, "run_cdp")
                                                      and not hasattr(browser, "_run_cdp")):
        return browser
    try:
        driver = _BROWSER_WRAPPERS.get(browser)
        if driver is None:
            driver = DrissionBrowserDriver(browser)
            _BROWSER_WRAPPERS[browser] = driver
        return driver
    except TypeError:
        return DrissionBrowserDriver(browser)
