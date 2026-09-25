"""R1-6：站点适配器健康巡检（只查 DOM，不输入、不点击、不发送）。"""

from __future__ import annotations

import pytest

from app.services.adapter_health import DrissionProbe, evaluate_preset, to_locator
from tests._real_browser import launch_page

DEFINITIONS = [
    {"key": "input_box", "required": True},
    {"key": "send_btn", "required": True},
    {"key": "result_container", "required": True},
    {"key": "new_chat_btn", "required": False},
]


class _FakeProbe:
    url = "https://example.com/chat"

    def __init__(self, counts):
        self.counts = counts

    def count(self, selector):
        return self.counts.get(selector, 0)


def _preset(**selectors):
    return {"selectors": {"input_box": "textarea", "send_btn": "button.send", "result_container": ".reply",
                          "new_chat_btn": None, **selectors}}


@pytest.mark.parametrize(
    ("selector", "locator"),
    [("textarea", "css:textarea"), ("css:div", "css:div"), ("xpath://a", "xpath://a"),
     ("//span[text()='新对话']", "xpath://span[text()='新对话']"), ("@id=x", "@id=x"), ("tag:div@@class=a", "tag:div@@class=a")],
)
def test_locator_rules(selector, locator):
    assert to_locator(selector) == locator


def test_healthy_when_input_found_and_nothing_invalid():
    report = evaluate_preset(_FakeProbe({"textarea": 1, "button.send": 1, ".reply": 2}), _preset(), DEFINITIONS)
    assert report["status"] == "healthy" and report["problems"] == []
    assert [row["key"] for row in report["selectors"]] == ["input_box", "send_btn", "result_container"]  # None 跳过


def test_missing_send_or_result_is_only_degraded_with_hint():
    report = evaluate_preset(_FakeProbe({"textarea": 1}), _preset(), DEFINITIONS)
    assert report["status"] == "degraded"
    hints = {row["key"]: row.get("hint") for row in report["selectors"]}
    assert hints["send_btn"] and hints["result_container"]


def test_missing_input_or_invalid_selector_is_broken():
    assert evaluate_preset(_FakeProbe({"button.send": 1}), _preset(), DEFINITIONS)["status"] == "broken"
    report = evaluate_preset(_FakeProbe({"textarea": 1, "!!bad": -1}), _preset(stop_btn="!!bad"), DEFINITIONS)
    assert report["status"] == "broken" and "无效" in report["problems"][0]


@pytest.fixture(scope="module")
def real_page():
    yield from launch_page()


def test_real_browser_probe_counts_css_and_xpath(real_page):
    real_page.get(
        "data:text/html;charset=utf-8,<textarea id='prompt'></textarea><button class='send'>发送</button>"
        "<div class='side'><span>发起新对话</span></div>"
    )
    probe = DrissionProbe(real_page)
    preset = _preset(input_box="textarea#prompt", new_chat_btn="//span[text()='发起新对话']", stop_btn="button.stop")
    report = evaluate_preset(probe, preset, DEFINITIONS)
    by_key = {row["key"]: row for row in report["selectors"]}
    assert by_key["input_box"]["status"] == "ok"
    assert by_key["new_chat_btn"]["status"] == "ok"  # 无前缀 XPath
    assert by_key["result_container"]["status"] == "missing"
    assert report["status"] == "degraded"


def test_health_api_reports_unknown_site_as_404(monkeypatch):
    import asyncio

    import main
    from httpx import ASGITransport, AsyncClient

    for name in ("AUTH_ENABLED", "AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    async def run():
        async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://127.0.0.1:8199") as client:
            return await client.get("/api/adapters/health", params={"site": "no-such-site.example"})

    response = asyncio.run(run())
    assert response.status_code == 404
