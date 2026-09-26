"""R2-1：基于 DrissionPage 的驱动实现——只做转发与异常翻译，不改变任何行为。"""

from __future__ import annotations

import weakref
from typing import Any, List, Optional

from app.core.driver.errors import translated_errors
from app.core.driver.locators import to_locator


class DrissionElement:
    __slots__ = ("_ele",)

    def __init__(self, ele: Any):
        self._ele = ele

    @property
    def raw(self) -> Any:
        return self._ele

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

    def click(self, *, by_js: bool = False) -> None:
        with translated_errors():
            self._ele.click(by_js=by_js)

    def input(self, text: str, *, clear: bool = False) -> None:
        with translated_errors():
            self._ele.input(text, clear=clear)

    def run_js(self, script: str, *args: Any) -> Any:
        with translated_errors():
            return self._ele.run_js(script, *args)


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
        kwargs: dict = {}
        if as_expr:
            kwargs["as_expr"] = True
        if timeout is not None:
            kwargs["timeout"] = timeout
        with translated_errors():
            return self._tab.run_js(script, *args, **kwargs)

    def run_cdp(self, method: str, **params: Any) -> Any:
        with translated_errors():
            return self._tab.run_cdp(method, **params)

    def find(self, locator: str, timeout: float = 0) -> Optional[DrissionElement]:
        with translated_errors():
            ele = self._tab.ele(to_locator(locator), timeout=timeout)
        # DrissionPage 找不到元素时返回假值的 NoneElement，而不是抛异常
        return DrissionElement(ele) if ele else None

    def find_all(self, locator: str, timeout: float = 0) -> List[DrissionElement]:
        with translated_errors():
            elements = self._tab.eles(to_locator(locator), timeout=timeout)
        if not isinstance(elements, list):
            elements = [elements] if elements else []
        return [DrissionElement(ele) for ele in elements if ele]

    def navigate(self, url: str, timeout: Optional[float] = None) -> bool:
        with translated_errors():
            return bool(self._tab.get(url, timeout=timeout))

    def reload(self, ignore_cache: bool = False) -> None:
        with translated_errors():
            self._tab.refresh(ignore_cache=ignore_cache)

    def screenshot(self, full_page: bool = False) -> bytes:
        with translated_errors():
            return self._tab.get_screenshot(as_bytes="png", full_page=full_page)

    def insert_text(self, text: str) -> None:
        with translated_errors():
            self._tab.run_cdp("Input.insertText", text=str(text))


_WRAPPERS: "weakref.WeakKeyDictionary[Any, DrissionTabDriver]" = weakref.WeakKeyDictionary()


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
