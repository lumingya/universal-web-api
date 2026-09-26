"""R2-1：驱动层统一异常。

各处代码以前靠匹配 DrissionPage 异常的类型或消息文本来判断“页面在刷新”“上下文丢了”“连接断了”，
规则分散在 tab_pool（执行上下文）、page_lifecycle（页面刷新）、command_engine（断连）三处。
这里把它们合并成一个分类器，驱动实现抛出的都是下面这几种异常。

**翻译后的异常消息与原异常完全相同**（原异常保存在 ``__cause__`` 和 ``original`` 上），
所以迁移期间仍按字符串判断的旧代码不受影响。
"""

from __future__ import annotations

import contextlib
from typing import Iterator, Optional, Type


class DriverError(Exception):
    """驱动层错误的基类。"""

    def __init__(self, message: str = "", original: Optional[BaseException] = None):
        super().__init__(message)
        self.original = original


class DriverDisconnected(DriverError):
    """与浏览器或标签页的连接已断开（标签页关闭、WebSocket 断开、浏览器退出）。"""


class ContextLost(DriverError):
    """页面执行上下文已失效：页面正在刷新、导航，或脚本所在的 frame 被销毁。通常稍后重试即可。"""


class DriverTimeout(DriverError, TimeoutError):
    """等待超时（元素、页面加载、脚本执行）。"""


class ScriptError(DriverError):
    """页面内脚本执行出错（JavaScript 异常、非法定位器等）。"""


_CONTEXT_LOST_MARKERS = (
    "cannot find context with specified id",
    "cannot find default execution context",
    "execution context was destroyed",
    "execution context is not available",
    "context was destroyed",
    "inspected target navigated or closed",
    # page_lifecycle.is_page_refresh_error 的判定
    "页面被刷新",
    "page was refreshed",
    "page is refreshing",
    "page refreshed",
    "try waiting for page refresh",
)
_DISCONNECTED_MARKERS = (
    "连接已断开",
    "connection disconnected",
    "target closed",
    "session closed",
    "no target with given id",
    "browser has disconnected",
)


def _drission_error_types():
    try:
        from DrissionPage import errors
    except Exception:  # pragma: no cover - DrissionPage 为必装依赖
        return {}
    return {
        "context": tuple(getattr(errors, n) for n in ("ContextLostError",) if hasattr(errors, n)),
        "disconnected": tuple(
            getattr(errors, n) for n in ("PageDisconnectedError", "BrowserConnectError", "TargetNotFoundError")
            if hasattr(errors, n)
        ),
        "timeout": tuple(getattr(errors, n) for n in ("WaitTimeoutError",) if hasattr(errors, n)),
        "script": tuple(getattr(errors, n) for n in ("JavaScriptError", "LocatorError") if hasattr(errors, n)),
    }


def classify_driver_error(error: BaseException) -> Type[DriverError]:
    """判断一个底层异常属于哪一类驱动错误（不修改异常本身）。"""
    if isinstance(error, DriverError):
        return type(error)
    types = _drission_error_types()
    text = str(error or "").lower()
    if isinstance(error, types.get("context", ())) or any(m in text for m in _CONTEXT_LOST_MARKERS):
        return ContextLost
    if isinstance(error, types.get("disconnected", ())) or any(m in text for m in _DISCONNECTED_MARKERS) or (
        "websocket" in text and "closed" in text
    ):
        return DriverDisconnected
    if isinstance(error, types.get("timeout", ())) or isinstance(error, TimeoutError):
        return DriverTimeout
    if isinstance(error, types.get("script", ())):
        return ScriptError
    return DriverError


def translate_error(error: BaseException) -> DriverError:
    if isinstance(error, DriverError):
        return error
    return classify_driver_error(error)(str(error), original=error)


def _is_drission_error(error: BaseException) -> bool:
    try:
        from DrissionPage.errors import BaseError
    except Exception:  # pragma: no cover
        return False
    return isinstance(error, BaseError)


@contextlib.contextmanager
def translated_errors() -> Iterator[None]:
    """只把 DrissionPage 自己的异常翻译成驱动异常；其他异常原样抛出。

    Python 内置异常（TypeError、OSError、KeyError……）保持原样，调用方已有的 ``except TypeError`` 之类
    的兼容逻辑因此不受影响——例如 network_monitor 先试 ``start(pattern, res_type=True)``，
    旧版 DrissionPage 不认识该参数时抛 TypeError，再退回 ``start(pattern)``。
    """
    try:
        yield
    except DriverError:
        raise
    except Exception as exc:
        if not _is_drission_error(exc):
            raise
        raise translate_error(exc) from exc
