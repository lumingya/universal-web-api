"""R2-1：浏览器驱动层。业务代码经由这里访问浏览器，逐步替代对 DrissionPage 的直接调用。"""

from app.core.driver.base import ElementHandle, TabDriver
from app.core.driver.drission import DrissionElement, DrissionTabDriver, driver_for_tab
from app.core.driver.errors import (
    ContextLost,
    DriverDisconnected,
    DriverError,
    DriverTimeout,
    ScriptError,
    classify_driver_error,
    translate_error,
    translated_errors,
)
from app.core.driver.fake import FakeElement, FakeTabDriver
from app.core.driver.locators import split_locator, to_locator

__all__ = [
    "ContextLost",
    "DriverDisconnected",
    "DriverError",
    "DriverTimeout",
    "DrissionElement",
    "DrissionTabDriver",
    "ElementHandle",
    "FakeElement",
    "FakeTabDriver",
    "ScriptError",
    "TabDriver",
    "classify_driver_error",
    "driver_for_tab",
    "split_locator",
    "to_locator",
    "translate_error",
    "translated_errors",
]
