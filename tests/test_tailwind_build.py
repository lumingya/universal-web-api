"""R2-8：预编译 Tailwind 的结构与覆盖检查（不需要浏览器或 Node）。

逐元素的样式等价性核对见 tests/test_tailwind_equivalence.py（需要 Playwright，本机运行）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
CSS = ROOT / "static" / "css" / "tailwind.css"

# 看起来像 Tailwind 工具类、但实际是项目自定义的类名（在 dashboard*.css 等文件里定义），编译器不会为它们生成规则
KNOWN_NON_TAILWIND = {"text-muted", "text-subtle", "bg-surface", "border-soft", "shadow-soft", "top-tabs"}

# 模板里早就存在、但从未生效的写法：透明度 /92、/88、/12、/18、/98、/6 不在 Tailwind 默认刻度内，
# py-0.2 也不是有效间距。原运行时同样不会生成它们（等价性测试可证），为保持现有外观不做修改，只在此登记。
NEVER_EFFECTIVE = {
    "bg-slate-900/92", "bg-slate-950/88", "bg-violet-500/12", "bg-violet-950/18", "bg-white/98",
    "border-white/6", "dark:bg-slate-900/98", "py-0.2",
}

_VARIANT = re.compile(r"^(?:[a-z0-9-]+:)*")
_UTILITY = re.compile(
    r"^-?(?:p[xytrbl]?|m[xytrbl]?|w|h|min-w|min-h|max-w|max-h|gap(?:-[xy])?|space-[xy]|top|right|bottom|left|inset(?:-[xy])?|"
    r"z|order|col-span|row-span|grid-cols|grid-rows|basis|text|font|leading|tracking|bg|border(?:-[xytrbl])?|rounded(?:-[a-z]+)?|"
    r"shadow|ring(?:-offset)?|opacity|divide(?:-[xy])?|outline|fill|stroke|translate-[xy]|scale(?:-[xy])?|rotate|duration|delay|"
    r"ease|transition|animate|cursor|select|overflow(?:-[xy])?|whitespace|break|items|justify|content|self|place-[a-z]+|object|"
    r"aspect|line-clamp|list|decoration|backdrop|blur|from|via|to)-[a-z0-9\[\]#./%_-]+$"
    r"|^(?:flex|grid|block|inline|inline-block|inline-flex|inline-grid|hidden|contents|table|absolute|relative|fixed|sticky|"
    r"static|visible|invisible|truncate|uppercase|lowercase|capitalize|italic|underline|antialiased|sr-only|shadow|rounded|"
    r"border|transition|grow|shrink|flex-1|flex-auto|flex-none|flex-wrap|flex-col|flex-row|shrink-0|grow-0|ring|outline-none)$"
)


def _template_sources():
    yield INDEX.read_text(encoding="utf-8")
    for path in sorted((ROOT / "static" / "js").rglob("*.js")):
        yield path.read_text(encoding="utf-8", errors="ignore")


def _class_tokens():
    tokens = set()
    for text in _template_sources():
        for value in re.findall(r'(?<![:\w-])class="([^"]*)"', text):
            tokens.update(value.split())
        for value in re.findall(r':class="([^"]*)"', text):  # 绑定表达式里带引号的类名
            for quoted in re.findall(r"'([^']*)'", value):
                tokens.update(quoted.split())
        for value in re.findall(r"classList\.(?:add|remove|toggle)\(\s*'([^']+)'", text):
            tokens.update(value.split())
    return {t for t in tokens if t and not t.startswith(("{", "$", "'"))}


def _css_escape(token: str) -> str:
    return "".join("\\" + ch if ch in ":/.[]%#()!,&=+*'\"" else ch for ch in token)


def test_index_uses_precompiled_stylesheet_last_in_head():
    html = INDEX.read_text(encoding="utf-8")
    head = html[: html.index("</head>")]
    assert "tailwindcss.js" not in html, "控制面板不应再加载运行时 JIT 脚本"
    links = re.findall(r'<link rel="stylesheet" href="([^"]+)"', head)
    assert links and links[-1].startswith("/static/css/tailwind.css"), "tailwind.css 必须是 head 中最后一个样式表（与原运行时层叠顺序一致）"
    assert CSS.exists() and CSS.stat().st_size > 20_000


def test_config_matches_the_former_inline_runtime_config():
    config = (ROOT / "tailwind.config.js").read_text(encoding="utf-8")
    assert "darkMode: 'class'" in config
    assert "rgb(var(--twc-${name}-${s}) / <alpha-value>)" in config
    for name in ("gray", "slate", "stone"):
        assert f"scale('{name}')" in config
    css = CSS.read_text(encoding="utf-8")
    assert "--twc-gray-" in css  # 中性色经 CSS 变量取值，夜间主题才能统一切换


def test_every_utility_class_in_templates_is_compiled():
    css = CSS.read_text(encoding="utf-8")
    missing = []
    for token in sorted(_class_tokens()):
        base = _VARIANT.sub("", token.lstrip("!"))
        if token in KNOWN_NON_TAILWIND or token in NEVER_EFFECTIVE or base[:1].isdigit() or not _UTILITY.match(base):
            continue
        if "." + _css_escape(token) not in css:
            missing.append(token)
    assert not missing, (
        "模板里使用了尚未编译进 static/css/tailwind.css 的 Tailwind 类名，请运行 python scripts/build_tailwind.py：\n"
        + ", ".join(missing[:40])
    )
