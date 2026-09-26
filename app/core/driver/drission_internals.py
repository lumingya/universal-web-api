"""R2-1：访问 DrissionPage 内部对象的唯一入口。

业务代码和 DrissionPage 专用的补丁模块（``app/core/cdp_hygiene.py``）需要用到 DrissionPage 的内部类或函数时，
一律从这里取，不再直接 ``import DrissionPage``。更换驱动时，这些依赖都集中在驱动包里，一目了然。
所有导入都是延迟的：只有真正用到时才加载 DrissionPage。
"""

from __future__ import annotations

from typing import Any, Tuple


def connect_existing_browser(address: str) -> Any:
    """连接一个已经在运行、开启了远程调试端口的浏览器（不会自行启动新浏览器）。"""
    from DrissionPage import Chromium, ChromiumOptions

    opts = ChromiumOptions()
    opts.set_address(address)
    opts.existing_only()
    return Chromium(addr_or_opts=opts)


def chromium_element_module() -> Any:
    """DrissionPage 元素模块（cdp_hygiene 在这里替换 run_js 的结果解析）。"""
    from DrissionPage._elements import chromium_element

    return chromium_element


def driver_class() -> Any:
    """DrissionPage 的底层 CDP 连接类（cdp_hygiene 在这里限制 Network.enable 的缓冲区）。"""
    from DrissionPage._base.driver import Driver

    return Driver


def is_real_page(obj: Any) -> bool:
    """是否为真实的 DrissionPage 页面/标签页对象（测试替身返回 False）。"""
    try:
        from DrissionPage._pages.chromium_base import ChromiumBase

        return isinstance(obj, ChromiumBase)
    except Exception:
        return False


def forget_tab_object(tab: Any) -> None:
    """从 DrissionPage 的标签页对象缓存中移除该对象，下次按 tab_id 获取时会重新创建（用于回收坏掉的会话）。"""
    try:
        from DrissionPage._pages.chromium_tab import ChromiumTab

        tab_id = getattr(tab, "tab_id", None) or getattr(tab, "_target_id", None)
        if tab_id and ChromiumTab._TABS.get(tab_id) is tab:
            ChromiumTab._TABS.pop(tab_id, None)
    except Exception:
        pass


def get_loc(locator: str) -> Tuple[str, str]:
    """把 DrissionPage 定位语法解析为 (by, query)，by 为 'css selector' 或 'xpath'。"""
    from DrissionPage._functions.locator import get_loc as _get_loc

    return _get_loc(locator)
