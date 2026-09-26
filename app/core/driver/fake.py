"""R2-1：内存假驱动——不需要浏览器，供单测替代真实标签页。

支持的定位器（经 ``to_locator`` 规范化后）：``css:tag``、``css:#id``、``css:.class``、
``css:tag#id.class[attr="v"]`` 这类单个复合选择器（不支持后代、逗号分组、伪类），
以及 ``tag:名称``、``@属性=值``。其他写法抛 ScriptError，行为与真实浏览器遇到非法选择器一致。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from app.core.driver.errors import DriverError, ScriptError
from app.core.driver.locators import split_locator

_COMPOUND = re.compile(r"^(?P<tag>[a-zA-Z][\w-]*)?(?P<rest>(?:#[\w-]+|\.[\w-]+|\[[^\]]+\])*)$")
_ATTR = re.compile(r"""\[\s*([\w-]+)\s*(?:=\s*["']?([^"'\]]*)["']?)?\s*\]""")


class FakeElement:
    def __init__(self, tag: str, text: str = "", **attrs: Any):
        self.tag = tag.lower()
        self.text = text
        # class_ 之类规避 Python 关键字的写法：先去掉末尾下划线，再把下划线换成连字符（data_test -> data-test）
        self.attrs = {k.rstrip("_").replace("_", "-"): str(v) for k, v in attrs.items()}
        self.clicks = 0
        self.inputs: List[str] = []

    def attr(self, name: str) -> Optional[str]:
        return self.attrs.get(name)

    def click(self, *, by_js: bool = False) -> None:
        self.clicks += 1

    def input(self, text: str, *, clear: bool = False) -> None:
        if clear:
            self.inputs.clear()
        self.inputs.append(text)

    def run_js(self, script: str, *args: Any) -> Any:
        return None

    def matches(self, locator: str) -> bool:
        kind, expr = split_locator(locator)
        if kind == "tag":
            return self.tag == expr.strip().lower()
        if kind == "attr":
            name, _, value = expr.partition("=")
            return self.attrs.get(name.strip()) == value.strip() if value else name.strip() in self.attrs
        if kind != "css":
            raise ScriptError(f"FakeTabDriver 不支持的定位器: {locator}")
        match = _COMPOUND.match(expr.strip())
        if not match or not expr.strip():
            raise ScriptError(f"FakeTabDriver 不支持的 CSS 选择器: {expr}")
        if match.group("tag") and match.group("tag").lower() != self.tag:
            return False
        rest = match.group("rest")
        for element_id in re.findall(r"#([\w-]+)", rest):
            if self.attrs.get("id") != element_id:
                return False
        classes = set((self.attrs.get("class") or "").split())
        if not set(re.findall(r"\.([\w-]+)", rest)) <= classes:
            return False
        for name, value in _ATTR.findall(rest):
            if name not in self.attrs or (value and self.attrs[name] != value):
                return False
        return True


class FakeTabDriver:
    def __init__(
        self,
        elements: Optional[List[FakeElement]] = None,
        *,
        url: str = "about:blank",
        title: str = "",
        tab_id: str = "fake-tab",
        js: Optional[Callable[[str, tuple], Any]] = None,
        cdp: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    ):
        self.elements = list(elements or [])
        self._url = url
        self._title = title
        self._tab_id = tab_id
        self._js = js
        self._cdp = cdp
        self.js_calls: List[tuple] = []
        self.cdp_calls: List[tuple] = []
        self.navigations: List[str] = []
        self.reloads = 0
        self.inserted_text: List[str] = []
        self._next_error: Optional[DriverError] = None

    # 让下一次调用抛出指定的驱动异常，用于测试错误处理路径
    def fail_next(self, error: DriverError) -> None:
        self._next_error = error

    def _maybe_fail(self) -> None:
        if self._next_error is not None:
            error, self._next_error = self._next_error, None
            raise error

    @property
    def raw(self) -> Any:
        return self

    @property
    def tab_id(self) -> str:
        return self._tab_id

    @property
    def url(self) -> str:
        return self._url

    @property
    def title(self) -> str:
        return self._title

    def run_js(self, script: str, *args: Any, timeout: Optional[float] = None, as_expr: bool = False) -> Any:
        self._maybe_fail()
        self.js_calls.append((script, args))
        return self._js(script, args) if self._js else None

    def run_cdp(self, method: str, **params: Any) -> Any:
        self._maybe_fail()
        self.cdp_calls.append((method, params))
        return self._cdp(method, params) if self._cdp else {}

    def find(self, locator: str, timeout: float = 0) -> Optional[FakeElement]:
        matches = self.find_all(locator, timeout)
        return matches[0] if matches else None

    def find_all(self, locator: str, timeout: float = 0) -> List[FakeElement]:
        self._maybe_fail()
        return [element for element in self.elements if element.matches(locator)]

    def navigate(self, url: str, timeout: Optional[float] = None) -> bool:
        self._maybe_fail()
        self.navigations.append(url)
        self._url = url
        return True

    def reload(self, ignore_cache: bool = False) -> None:
        self._maybe_fail()
        self.reloads += 1

    def screenshot(self, full_page: bool = False) -> bytes:
        self._maybe_fail()
        return b"\x89PNG\r\n\x1a\nFAKE"

    def insert_text(self, text: str) -> None:
        self._maybe_fail()
        self.inserted_text.append(str(text))
