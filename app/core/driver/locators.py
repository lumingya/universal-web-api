"""R2-1：定位语法的统一规则（ElementFinder、健康巡检和各驱动共用）。

- 带 DrissionPage 前缀的原样使用：``css:``、``xpath:``、``tag:``、``@属性=值``、含 ``@@`` 的组合语法；
- 以 ``/`` 或 ``(`` 开头的按 XPath 处理（工作流执行器对这类选择器走 document.evaluate）；
- 其余按 CSS 处理。
"""

from __future__ import annotations

DRISSION_PREFIXES = ("tag:", "@", "xpath:", "css:")


def to_locator(selector: str) -> str:
    selector = str(selector or "").strip()
    if selector.startswith(DRISSION_PREFIXES) or "@@" in selector:
        return selector
    if selector.startswith(("/", "(")):
        return f"xpath:{selector}"
    return f"css:{selector}"


def split_locator(locator: str) -> tuple:
    """把规范化后的定位器拆成 (类型, 表达式)，类型为 css / xpath / tag / attr / drission。"""
    locator = to_locator(locator)
    if locator.startswith("css:"):
        return "css", locator[4:]
    if locator.startswith("xpath:"):
        return "xpath", locator[6:]
    if "@@" in locator:
        return "drission", locator
    if locator.startswith("tag:"):
        return "tag", locator[4:]
    if locator.startswith("@"):
        return "attr", locator[1:]
    return "drission", locator
