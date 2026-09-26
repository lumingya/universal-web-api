"""R2-1：浏览器驱动接口。

业务代码通过这里定义的接口访问浏览器，不再直接调用 DrissionPage。目前的实现：
- ``drission.DrissionTabDriver``：包装现有 DrissionPage 标签页对象，只转发调用、翻译异常；
- ``fake.FakeTabDriver``：内存假驱动，供单测使用，不需要真实浏览器。

迁移计划见 docs/architecture/browser-driver-and-process-model.md。网络监听（listen）
在第 5 步迁移时再加入接口，这里只包含已经实现的部分。
"""

from __future__ import annotations

from typing import Any, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ElementHandle(Protocol):
    @property
    def tag(self) -> str: ...

    @property
    def text(self) -> str: ...

    def attr(self, name: str) -> Optional[str]: ...

    def click(self, *, by_js: bool = False) -> None: ...

    def input(self, text: str, *, clear: bool = False) -> None: ...

    def run_js(self, script: str, *args: Any) -> Any: ...


@runtime_checkable
class TabDriver(Protocol):
    @property
    def tab_id(self) -> str: ...

    @property
    def url(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def raw(self) -> Any:
        """底层对象（迁移期间的逃生口：尚未迁移的代码仍可拿到原始 DrissionPage 标签页）。"""

    def run_js(self, script: str, *args: Any, timeout: Optional[float] = None, as_expr: bool = False) -> Any: ...

    def run_cdp(self, method: str, **params: Any) -> Any: ...

    def find(self, locator: str, timeout: float = 0) -> Optional[ElementHandle]: ...

    def find_all(self, locator: str, timeout: float = 0) -> List[ElementHandle]: ...

    def navigate(self, url: str, timeout: Optional[float] = None) -> bool: ...

    def reload(self, ignore_cache: bool = False) -> None: ...

    def screenshot(self, full_page: bool = False) -> bytes: ...

    def insert_text(self, text: str) -> None:
        """在当前焦点处插入文本（CDP Input.insertText，不模拟逐键输入）。"""
