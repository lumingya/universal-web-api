"""R2-7：可观测性——无第三方依赖的 Prometheus 指标与请求 ID。

- ``RequestMetricsMiddleware``（纯 ASGI 中间件，不缓冲流式响应）：
  - 为每个 HTTP 请求分配或透传 ``X-Request-ID``，写入响应头与 ``REQUEST_ID`` 上下文变量；
  - 记录请求数、耗时直方图（含流式响应体发送完的总耗时），以及进行中的请求数。
  - 标签用路由模板（如 ``/v1/chat/completions``），未匹配到路由的统一记为 ``<unmatched>``，避免标签基数爆炸。
- ``render_prometheus()``：Prometheus 文本格式（text/plain; version=0.0.4）。
  抓取时顺带读取标签页池状态，但只在浏览器**已连接**时读取，抓取本身绝不触发连接。
"""

from __future__ import annotations

import contextvars
import re
import threading
import time
import uuid
from typing import Dict, Iterable, List, Optional, Tuple

REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("uwapi_http_request_id", default="")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_STARTED_AT = time.time()

Labels = Tuple[Tuple[str, str], ...]


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: Labels, extra: Iterable[Tuple[str, str]] = ()) -> str:
    items = list(labels) + list(extra)
    if not items:
        return ""
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in items) + "}"


class _Metric:
    kind = ""

    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help = help_text
        self._lock = threading.Lock()

    def header(self) -> List[str]:
        return [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help_text: str):
        super().__init__(name, help_text)
        self._values: Dict[Labels, float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = tuple(sorted((k, str(v)) for k, v in labels.items()))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        return self._values.get(tuple(sorted((k, str(v)) for k, v in labels.items())), 0.0)

    def render(self) -> List[str]:
        with self._lock:
            items = sorted(self._values.items())
        return self.header() + [f"{self.name}{_format_labels(k)} {v:g}" for k, v in items]


class Gauge(Counter):
    kind = "gauge"

    def set(self, value: float, **labels: str) -> None:
        key = tuple(sorted((k, str(v)) for k, v in labels.items()))
        with self._lock:
            self._values[key] = float(value)

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


class Histogram(_Metric):
    kind = "histogram"

    def __init__(self, name: str, help_text: str, buckets: Iterable[float]):
        super().__init__(name, help_text)
        self.buckets = tuple(sorted(buckets))
        self._data: Dict[Labels, List[float]] = {}  # [bucket counts..., sum, count]

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(sorted((k, str(v)) for k, v in labels.items()))
        with self._lock:
            data = self._data.setdefault(key, [0.0] * (len(self.buckets) + 2))
            for index, bound in enumerate(self.buckets):
                if value <= bound:
                    data[index] += 1
            data[-2] += value
            data[-1] += 1

    def render(self) -> List[str]:
        lines = self.header()
        with self._lock:
            items = sorted((k, list(v)) for k, v in self._data.items())
        for labels, data in items:
            for index, bound in enumerate(self.buckets):
                lines.append(f"{self.name}_bucket{_format_labels(labels, [('le', f'{bound:g}')])} {data[index]:g}")
            lines.append(f"{self.name}_bucket{_format_labels(labels, [('le', '+Inf')])} {data[-1]:g}")
            lines.append(f"{self.name}_sum{_format_labels(labels)} {data[-2]:.6f}")
            lines.append(f"{self.name}_count{_format_labels(labels)} {data[-1]:g}")
        return lines


HTTP_REQUESTS = Counter("uwapi_http_requests_total", "HTTP 请求数（按方法、路由模板、状态码）")
HTTP_DURATION = Histogram(
    "uwapi_http_request_duration_seconds",
    "HTTP 请求耗时（秒，流式响应计到响应体发送完）",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)
HTTP_IN_FLIGHT = Gauge("uwapi_http_requests_in_flight", "进行中的 HTTP 请求数")
CHAT_REQUESTS = Counter("uwapi_chat_requests_total", "聊天请求数（按协议 openai.chat / anthropic.messages / openai.responses 与最终状态）")
CHAT_REQUEST_DURATION = Histogram(
    "uwapi_chat_request_duration_seconds",
    "聊天请求从创建到结束的耗时（秒，按协议）",
    buckets=(0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300, 600),
)
TAB_POOL_TABS = Gauge("uwapi_tab_pool_tabs", "标签页池中的标签页数（按状态；浏览器未连接时不输出）")
BUILD_INFO = Gauge("uwapi_build_info", "构建信息（值恒为 1）")
UPTIME = Gauge("uwapi_uptime_seconds", "进程已运行秒数")
REGISTRY: List[_Metric] = [
    HTTP_REQUESTS, HTTP_DURATION, HTTP_IN_FLIGHT, CHAT_REQUESTS, CHAT_REQUEST_DURATION, TAB_POOL_TABS, BUILD_INFO, UPTIME,
]


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def _incoming_request_id(scope) -> Optional[str]:
    for key, value in scope.get("headers") or []:
        if key == b"x-request-id":
            candidate = value.decode("latin-1").strip()
            return candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else None
    return None


class RequestMetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        request_id = _incoming_request_id(scope) or new_request_id()
        token = REQUEST_ID.set(request_id)
        status = {"code": 500}
        started = time.perf_counter()

        async def send_with_request_id(message):
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status") or 500)
                headers = [(k, v) for k, v in (message.get("headers") or []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        HTTP_IN_FLIGHT.inc()
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            HTTP_IN_FLIGHT.dec()
            route = getattr(scope.get("route"), "path", None) or "<unmatched>"
            method = str(scope.get("method") or "GET")
            HTTP_REQUESTS.inc(method=method, route=route, status=str(status["code"]))
            HTTP_DURATION.observe(time.perf_counter() - started, method=method, route=route)
            REQUEST_ID.reset(token)


def _collect_runtime_gauges() -> None:
    from app import __version__

    BUILD_INFO.set(1, version=__version__)
    UPTIME.set(time.time() - _STARTED_AT)
    TAB_POOL_TABS.clear()
    try:
        from app.core.browser import main as browser_main

        browser = getattr(browser_main, "_browser_instance", None)  # 不调用 get_browser：抓取不能创建或连接浏览器
        if browser is None or not getattr(browser, "_connected", False):
            return
        pool = getattr(browser, "tab_pool", None)
        if pool is None:
            return
        counts: Dict[str, int] = {}
        for session in pool.get_sessions_snapshot():
            state = str(getattr(getattr(session, "status", None), "value", "unknown") or "unknown")
            counts[state] = counts.get(state, 0) + 1
        for state, count in counts.items():
            TAB_POOL_TABS.set(count, status=state)
    except Exception:  # broad-except: 指标抓取不能因为浏览器状态异常而失败
        return


def render_prometheus() -> str:
    _collect_runtime_gauges()
    lines: List[str] = []
    for metric in REGISTRY:
        lines.extend(metric.render())
    return "\n".join(lines) + "\n"
