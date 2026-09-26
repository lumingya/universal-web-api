"""R2-8：站点适配器维护面板（AdapterPanel）的界面测试。

在空白页里加载本地 Vue 与组件，请求函数换成返回固定数据的假实现，不连接后端、不需要浏览器以外的服务。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tests._playwright import launch_chromium, sync_playwright  # noqa: E402

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[1]

RESPONSES = {
    "/api/adapters/health": {
        "site": "chat.example.com", "preset": "主预设", "status": "degraded", "problems": [],
        "tabs": [{"status": "degraded", "url": "https://chat.example.com/", "problems": [], "selectors": [
            {"key": "input_box", "selector": "textarea", "count": 1, "status": "ok", "required": True,
             "quality": {"score": 100, "issues": []}},
            {"key": "send_btn", "selector": "button.send", "count": 0, "status": "missing", "required": True,
             "quality": {"score": 100, "issues": []}, "hint": "发送按钮通常在输入内容后才出现或可点击",
             "suggestions": [{"selector": "css:button[type=\"submit\"]", "count": 1, "unique": True, "score": 100}]},
        ]}],
    },
    "/api/adapters/lint": {"site": "chat.example.com", "presets": {"主预设": [
        {"key": "send_btn", "selector": "div.bf38813a button", "score": 80,
         "issues": [{"code": "generated_class", "message": "疑似构建工具生成的类名：bf38813a"}], "semantic_anchors": []},
        {"key": "input_box", "selector": "textarea", "score": 100, "issues": [], "semantic_anchors": []},
    ]}},
    "/api/adapters/updates": {"app_version": "3.0.0", "summary": {}, "sites": [
        {"site": "a.example", "status": "update_available", "local_version": "2026.09.01", "official_version": "2026.10.01"},
        {"site": "b.example", "status": "conflict", "local_version": "2026.09.01", "official_version": "2026.10.01"},
        {"site": "c.example", "status": "up_to_date", "local_version": "2026.10.01", "official_version": "2026.10.01"},
    ]},
    "/api/adapters/updates/apply": {"applied": [{"site": "a.example", "status": "update_available"}], "skipped": []},
}


def _html() -> str:
    vue = (ROOT / "static" / "vendor" / "vue.global.prod.js").read_text(encoding="utf-8")
    panel = (ROOT / "static" / "js" / "components" / "panels" / "AdapterPanel.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "adapter-panel.css").read_text(encoding="utf-8")
    bootstrap = """
    window.calls = [];
    const responses = %s;
    async function request(url, options = {}) {
        window.calls.push([url, options.method || 'GET', options.body || null]);
        return JSON.parse(JSON.stringify(responses[url.split('?')[0]]));
    }
    window.confirm = () => true;
    Vue.createApp({
        components: { 'adapter-panel': window.AdapterPanel },
        data: () => ({ domain: 'chat.example.com', preset: '主预设' }),
        methods: { request },
        template: '<adapter-panel :domain="domain" :preset-name="preset" :request="request"></adapter-panel>'
    }).mount('#app');
    """ % json.dumps(RESPONSES, ensure_ascii=False)
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body><div id='app'></div>"
            f"<script>{vue}</script><script>{panel}</script><script>{bootstrap}</script></body></html>")


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = launch_chromium(p.chromium)
        try:
            page = browser.new_page()
            page.set_content(_html())
            yield page
        finally:
            browser.close()


def test_health_check_renders_status_hints_and_suggestions(page):
    page.get_by_role("button", name="开始巡检").click()
    card = page.get_by_label("健康巡检")
    card.get_by_text("部分缺失").first.wait_for()
    assert "命中" in card.inner_text() and "未命中" in card.inner_text()
    assert "发送按钮通常在输入内容后才出现" in card.inner_text()
    assert 'css:button[type="submit"]' in card.inner_text()
    url = page.evaluate("window.calls[window.calls.length - 1][0]")
    assert url.startswith("/api/adapters/health?site=chat.example.com&preset=")


def test_lint_lists_only_problematic_selectors(page):
    page.get_by_role("button", name="检查", exact=True).click()
    card = page.get_by_label("选择器稳健度")
    card.get_by_text("bf38813a").first.wait_for()
    text = card.inner_text()
    assert "send_btn" in text and "input_box" not in text


def test_updates_apply_safe_ones_and_merge_conflict_on_request(page):
    page.get_by_role("button", name="检查更新").click()
    card = page.get_by_label("适配器更新")
    card.get_by_text("可安全更新").first.wait_for()
    assert "c.example" not in card.inner_text()  # 已是最新的站点不列出
    page.get_by_role("button", name="应用 1 个安全更新").click()
    card.get_by_text("已更新 1 个站点").wait_for()
    apply_calls = page.evaluate("window.calls.filter(c => c[0] === '/api/adapters/updates/apply')")
    assert apply_calls[-1][1] == "POST" and json.loads(apply_calls[-1][2]) == {"include_conflicts": False}

    card.get_by_role("button", name="合并").click()
    page.wait_for_function("window.calls.filter(c => c[0] === '/api/adapters/updates/apply').length === 2")
    last = page.evaluate("window.calls.filter(c => c[0] === '/api/adapters/updates/apply').pop()")
    assert json.loads(last[2]) == {"include_conflicts": True, "sites": ["b.example"]}
