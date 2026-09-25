"""R2-7：Prometheus 指标、请求 ID 与 /metrics 访问控制。"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.metrics import Counter, Gauge, Histogram, REQUEST_ID


def test_metric_text_format():
    counter = Counter("t_requests_total", "demo")
    counter.inc(route="/a", status="200")
    counter.inc(2, route="/a", status="200")
    gauge = Gauge("t_in_flight", "demo")
    gauge.inc()
    gauge.dec()
    histogram = Histogram("t_seconds", "demo", buckets=(0.1, 1))
    histogram.observe(0.05, route="/a")
    histogram.observe(0.5, route="/a")
    text = "\n".join(counter.render() + gauge.render() + histogram.render())
    assert "# TYPE t_requests_total counter" in text
    assert 't_requests_total{route="/a",status="200"} 3' in text
    assert "t_in_flight 0" in text
    assert 't_seconds_bucket{route="/a",le="0.1"} 1' in text
    assert 't_seconds_bucket{route="/a",le="1"} 2' in text
    assert 't_seconds_bucket{route="/a",le="+Inf"} 2' in text
    assert 't_seconds_count{route="/a"} 2' in text


def test_label_values_are_escaped():
    counter = Counter("t_escape_total", "demo")
    counter.inc(route='/x"y\\z')
    assert 'route="/x\\"y\\\\z"' in "\n".join(counter.render())


@pytest.fixture
def client_factory(monkeypatch):
    import main

    for name in ("AUTH_ENABLED", "AUTH_TOKEN", "DASHBOARD_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    def make(client=("127.0.0.1", 50000)):
        return AsyncClient(transport=ASGITransport(app=main.app, client=client), base_url="http://127.0.0.1:8199")

    return make


def test_request_id_is_generated_or_propagated(client_factory):
    async def run():
        async with client_factory() as client:
            generated = await client.get("/health")
            propagated = await client.get("/health", headers={"X-Request-ID": "trace-abc.123"})
            rejected = await client.get("/health", headers={"X-Request-ID": "bad id with spaces"})
        return generated, propagated, rejected

    generated, propagated, rejected = asyncio.run(run())
    assert len(generated.headers["x-request-id"]) == 16
    assert propagated.headers["x-request-id"] == "trace-abc.123"
    assert rejected.headers["x-request-id"] != "bad id with spaces"
    assert REQUEST_ID.get() == ""  # 上下文变量不泄漏到请求之外


def test_metrics_endpoint_counts_requests_by_route_template(client_factory):
    async def run():
        async with client_factory() as client:
            await client.get("/health")
            await client.get("/definitely-not-a-route-xyz")
            return await client.get("/metrics")

    response = asyncio.run(run())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    # 测试环境没有浏览器，/health 按设计返回 503；这里只关心按路由模板计数
    assert 'uwapi_http_requests_total{method="GET",route="/health",status=' in body
    assert 'route="<unmatched>"' in body and "definitely-not-a-route-xyz" not in body
    assert "uwapi_http_request_duration_seconds_bucket" in body
    assert "uwapi_build_info{version=" in body


def test_metrics_endpoint_requires_local_or_token(client_factory, monkeypatch):
    async def run(client_addr, headers=None):
        async with client_factory(client_addr) as client:
            return await client.get("/metrics", headers=headers or {})

    assert asyncio.run(run(("203.0.113.9", 40000))).status_code == 403
    assert asyncio.run(run(("127.0.0.1", 40000), {"X-Forwarded-For": "203.0.113.9"})).status_code == 403
    monkeypatch.setenv("AUTH_TOKEN", "s3cret-token")
    assert asyncio.run(run(("203.0.113.9", 40000), {"Authorization": "Bearer s3cret-token"})).status_code == 200
