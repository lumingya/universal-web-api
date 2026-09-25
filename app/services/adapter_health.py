"""R1-6：站点适配器健康巡检——只查询页面 DOM，绝不输入、点击或发送消息。

对站点当前预设里配置的每个选择器，在该站点已打开的空闲标签页上统计命中数量，给出：

- ``broken``：输入框找不到（常见原因：未登录、页面改版），或存在语法无效的选择器；
- ``degraded``：其他必需元素找不到。发送按钮通常要输入内容后才出现，回复容器要有对话后才出现，
  所以这里只作提示，不算坏；
- ``healthy``：输入框能找到，且没有无效选择器；
- ``unreachable``：浏览器里没有该站点的空闲标签页（巡检不会自行打开页面）。

``evaluate_preset`` 是与浏览器无关的纯函数（便于单测）；``DrissionProbe`` 把 DrissionPage 标签页适配成探针。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional, Protocol

from app.core.config import get_logger
from app.services.selector_quality import fallback_suggestions, lint_selector

logger = get_logger("ADAPTER_HEALTH")

ALWAYS_PRESENT_KEYS = ("input_box",)
_HINTS = {
    "send_btn": "发送按钮通常在输入内容后才出现或可点击",
    "result_container": "回复容器通常在已有对话时才出现",
}


class Probe(Protocol):
    url: str

    def count(self, selector: str) -> int:
        """返回命中数量；选择器语法无效时返回 -1。"""


def to_locator(selector: str) -> str:
    """与 ElementFinder 相同：带 DrissionPage 前缀的原样使用，其余按 CSS 处理；
    另外以 ``/`` 或 ``(`` 开头的按 XPath 处理（工作流执行器对这类选择器走 document.evaluate）。"""
    selector = selector.strip()
    if selector.startswith(("tag:", "@", "xpath:", "css:")) or "@@" in selector:
        return selector
    if selector.startswith(("/", "(")):
        return f"xpath:{selector}"
    return f"css:{selector}"


class DrissionProbe:
    def __init__(self, tab: Any, url: str = "", timeout: float = 0.3):
        self.tab = tab
        self.url = url or str(getattr(tab, "url", "") or "")
        self.timeout = timeout

    def count(self, selector: str) -> int:
        try:
            elements = self.tab.eles(to_locator(selector), timeout=self.timeout)
        except Exception as exc:
            logger.debug(f"选择器无效或查询失败: {selector!r}: {exc}")
            return -1
        if not isinstance(elements, list):
            elements = [elements] if elements else []
        return sum(1 for element in elements if element)


def evaluate_preset(probe: Probe, preset: Dict[str, Any], definitions: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    required = {str(d.get("key")) for d in definitions or [] if isinstance(d, dict) and d.get("required")}
    rows: List[Dict[str, Any]] = []
    for key, selector in (preset.get("selectors") or {}).items():
        if not isinstance(selector, str) or not selector.strip():
            continue
        count = probe.count(selector)
        status = "invalid" if count < 0 else ("missing" if count == 0 else "ok")
        row = {"key": key, "selector": selector, "count": max(count, 0), "status": status, "required": key in required,
               "quality": lint_selector(selector)}
        if status == "missing" and key in _HINTS:
            row["hint"] = _HINTS[key]
        if status != "ok" and key in required:
            # R1-7：运行时找不到配置的元素时会尝试这些回退选择器；命中的可作为修复起点
            row["suggestions"] = fallback_suggestions(key, probe)
        rows.append(row)

    by_key = {row["key"]: row for row in rows}
    problems = []
    if any(row["status"] == "invalid" for row in rows):
        problems.append("存在语法无效的选择器")
    for key in ALWAYS_PRESENT_KEYS:
        if key not in by_key:
            problems.append(f"预设没有配置 {key}")
        elif by_key[key]["status"] != "ok":
            problems.append(f"找不到 {key}（可能未登录或页面已改版）")
    if problems:
        overall = "broken"
    elif any(row["required"] and row["status"] != "ok" for row in rows):
        overall = "degraded"
    else:
        overall = "healthy"
    return {"status": overall, "url": probe.url, "problems": problems, "selectors": rows}


def check_site_health(domain: str, preset_name: Optional[str] = None, *, timeout: float = 0.3) -> Dict[str, Any]:
    """在浏览器里该站点的空闲标签页上巡检（与 /api/debug/test-selector 相同的借用/归还方式）。"""
    from app.core.browser import get_browser
    from app.services.config_engine import config_engine
    from app.utils.site_url import extract_remote_site_domain, route_domain_matches

    site = config_engine.sites.get(domain)
    if not isinstance(site, dict):
        raise KeyError(f"未知站点: {domain}")
    presets = site.get("presets") or {}
    name = preset_name or site.get("default_preset") or next(iter(presets), "")
    if name not in presets:
        raise KeyError(f"站点 {domain} 没有预设 {name!r}")
    definitions = config_engine.global_config.to_dict().get("selector_definitions") or []

    browser = get_browser()
    pool = browser.tab_pool
    pool.refresh_tabs()
    reports, busy = [], 0
    for session in pool.get_sessions_snapshot():
        cached = str(getattr(session, "current_domain", "") or "")
        if cached and not route_domain_matches(domain, cached):
            continue
        if str(getattr(getattr(session, "status", None), "value", "")) != "idle":
            busy += 1 if cached else 0
            continue
        raw_tab_id = str(getattr(getattr(session, "tab", None), "tab_id", "") or "")
        if not raw_tab_id:
            continue
        task_id = f"adapter_health_{time.time_ns()}"
        acquired = pool.acquire_by_raw_tab_id(raw_tab_id, task_id, timeout=1.0, count_request=False, activate=False)
        if acquired is None:
            continue
        try:
            url = str(getattr(acquired.tab, "url", "") or "")
            if not route_domain_matches(domain, extract_remote_site_domain(url) or ""):
                continue
            reports.append(evaluate_preset(DrissionProbe(acquired.tab, url, timeout), presets[name], definitions))
        finally:
            pool.release(acquired.id, check_triggers=False, expected_task_id=task_id)

    if not reports:
        return {"site": domain, "preset": name, "status": "unreachable", "busy_tabs": busy,
                "problems": [f"浏览器里没有 {domain} 的空闲标签页；巡检不会自行打开页面"], "tabs": []}
    order = {"healthy": 0, "degraded": 1, "broken": 2}
    best = min(reports, key=lambda report: order[report["status"]])
    return {"site": domain, "preset": name, "status": best["status"], "busy_tabs": busy,
            "problems": best["problems"], "tabs": reports}
