"""R2-8：预编译 Tailwind 与原运行时（Play CDN 3.4.17）的逐元素样式等价性核对（需要 Playwright，本机运行）。

同一份 static/index.html 渲染两遍：``/`` 使用预编译的 static/css/tailwind.css；``/jit`` 去掉它，换回原来的
运行时脚本与内联配置。依次点击侧边栏的每个标签页，在亮色与夜间模式下比较所有带 class 的元素的计算样式。
所有 /api/* 请求都返回空 JSON，两边拿到完全相同的数据。
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tests._playwright import launch_chromium, sync_playwright  # noqa: E402

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "tests" / "fixtures" / "vendor" / "tailwindcss-play-3.4.17.js"

# 原 static/index.html 中给运行时的内联配置（原样保留，用于对照）
RUNTIME_SNIPPET = """<script src="/__fixtures__/tailwindcss-play.js"></script>
    <script>
        window.tailwind = window.tailwind || {};
        (function () {
            var shades = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];
            function scale(name) {
                var out = {};
                shades.forEach(function (s) {
                    out[s] = 'rgb(var(--twc-' + name + '-' + s + ') / <alpha-value>)';
                });
                return out;
            }
            window.tailwind.config = {
                darkMode: 'class',
                theme: { extend: { colors: { gray: scale('gray'), slate: scale('slate'), stone: scale('stone') } } }
            };
        })();
    </script>
"""

PROPS = [
    "display", "position", "box-sizing", "float", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding-top", "padding-right", "padding-bottom", "padding-left", "min-width", "max-width", "min-height",
    "max-height", "color", "background-color", "background-image", "font-size", "font-weight", "font-style",
    "line-height", "letter-spacing", "text-align", "text-transform", "text-decoration-line", "white-space",
    "border-top-width", "border-right-width", "border-bottom-width", "border-left-width", "border-top-color",
    "border-top-style", "border-top-left-radius", "border-bottom-right-radius", "gap", "row-gap", "column-gap",
    "flex-direction", "flex-wrap", "flex-grow", "flex-shrink", "justify-content", "align-items", "align-self",
    "grid-template-columns", "overflow-x", "overflow-y", "opacity", "box-shadow", "cursor", "z-index",
    "visibility", "transform", "transition-duration", "object-fit", "outline-style",
]

COLLECT_JS = """(props) => {
  const out = {};
  const all = document.querySelectorAll('body *');
  let n = 0;
  for (const el of all) {
    if (!el.getAttribute('class') || n > 6000) continue;
    n += 1;
    const path = [];
    let node = el;
    while (node && node !== document.body) {
      const parent = node.parentElement;
      path.unshift(node.tagName + ':' + (parent ? Array.prototype.indexOf.call(parent.children, node) : 0));
      node = parent;
    }
    const cs = getComputedStyle(el);
    const animated = cs.getPropertyValue('animation-name') !== 'none';
    const values = {};
    for (const p of props) {
      if (animated && (p === 'opacity' || p === 'transform')) continue;
      values[p] = cs.getPropertyValue(p);
    }
    out[path.join('>')] = {cls: el.getAttribute('class'), values};
  }
  return out;
}"""


def _make_handler(compiled_html: str, runtime_html: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # 安静
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send(compiled_html.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/jit":
                return self._send(runtime_html.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/__fixtures__/tailwindcss-play.js":
                return self._send(RUNTIME.read_bytes(), "application/javascript")
            if path.startswith("/static/"):
                target = (ROOT / path.lstrip("/")).resolve()
                if ROOT / "static" in target.parents and target.is_file():
                    types = {".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                             ".png": "image/png", ".html": "text/html; charset=utf-8"}
                    return self._send(target.read_bytes(), types.get(target.suffix, "application/octet-stream"))
                return self._send(b"not found", "text/plain", 404)
            return self._send(b"{}", "application/json")  # /api/* 等一律返回空 JSON

        do_POST = do_GET

    return Handler


@pytest.fixture(scope="module")
def server():
    compiled = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    link_start = compiled.index('    <link rel="stylesheet" href="/static/css/tailwind.css')
    link_end = compiled.index("\n", link_start) + 1
    runtime = compiled[:link_start] + compiled[link_end:]
    first_script = runtime.index("<script src=", runtime.index("<body"))
    runtime = runtime[:first_script] + RUNTIME_SNIPPET + "    " + runtime[first_script:]
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(compiled, runtime))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _open(browser, url):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(url)
    page.wait_for_selector("button.app-nav-item", timeout=20000)
    page.wait_for_timeout(1500)
    return page


def _diff(a: dict, b: dict):
    problems, common = [], set(a) & set(b)
    for key in sorted(common):
        va, vb = a[key]["values"], b[key]["values"]
        for prop in va:
            if prop in vb and va[prop] != vb[prop]:
                problems.append(f"{a[key]['cls'][:60]!r} {prop}: 运行时={va[prop]!r} 预编译={vb[prop]!r}")
    return problems, len(common), len(set(a) ^ set(b))


def test_precompiled_css_matches_runtime_jit_on_every_tab(server):
    with sync_playwright() as p:
        browser = launch_chromium(p.chromium)
        try:
            runtime_page = _open(browser, server + "/jit")
            compiled_page = _open(browser, server + "/")
            tab_count = runtime_page.locator("button.app-nav-item").count()
            assert tab_count == compiled_page.locator("button.app-nav-item").count() and tab_count >= 3
            report, compared = [], 0
            for dark in (False, True):
                for page in (runtime_page, compiled_page):
                    page.evaluate("(d) => document.documentElement.classList.toggle('dark', d)", dark)
                for index in range(tab_count):
                    for page in (runtime_page, compiled_page):
                        page.locator("button.app-nav-item").nth(index).click()
                        page.wait_for_timeout(900)
                    a = runtime_page.evaluate(COLLECT_JS, PROPS)
                    b = compiled_page.evaluate(COLLECT_JS, PROPS)
                    problems, common, only_one_side = _diff(a, b)
                    compared += common
                    label = f"{'夜间' if dark else '亮色'} · 标签页 {index + 1}"
                    if only_one_side > max(5, common // 50):
                        report.append(f"{label}: 两边 DOM 结构差异过大（{only_one_side} 个元素只出现在一侧）")
                    report.extend(f"{label}: {item}" for item in problems[:10])
            assert compared > 500, f"比较的元素太少（{compared}），页面可能没有正常渲染"
            assert not report, "预编译样式与原运行时不一致：\n" + "\n".join(report[:40])
        finally:
            browser.close()


def test_dashboard_loads_and_every_tab_renders_without_page_errors(server):
    """R2-8：脚本改为 ES 模块（严格模式、延迟执行）后，控制面板加载与切换每个标签页都不能出现未捕获异常。"""
    with sync_playwright() as p:
        browser = launch_chromium(p.chromium)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(server + "/")
            page.wait_for_selector("button.app-nav-item", timeout=20000)
            assert page.evaluate("typeof window.DashboardMethods === 'object' && Object.keys(window.DashboardMethods).length > 150")
            tabs = page.locator("button.app-nav-item")
            for dark in (False, True):
                page.evaluate("(d) => document.documentElement.classList.toggle('dark', d)", dark)
                for index in range(tabs.count()):
                    tabs.nth(index).click()
                    page.wait_for_timeout(500)
            assert not errors, "控制面板出现未捕获异常：\n" + "\n".join(errors[:10])
        finally:
            browser.close()

