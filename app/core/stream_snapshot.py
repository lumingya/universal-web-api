"""
app/core/stream_snapshot.py - 流式轮询的“页面内快照”（一次 CDP 往返）

原实现（StreamMonitor._get_snapshot_prefer_anchor 等）每轮询一次要：
  performSearch 全 DOM 搜索 → 每个候选元素 describeNode/resolveNode →
  每个候选元素 run_js 取布局信息（返回对象，再额外一次 JSON.stringify 往返）→
  find_content_node 若干次属性查询 → 发送 ~10KB 的深度提取脚本 →
  生成状态最多 4 次全 DOM 搜索 → 图片探测……
会话 40 轮时约 138 条 CDP 命令/次，并且每条命令都会在渲染进程里留下
RemoteObject（DrissionPage 从不释放），把已经移除的旧会话 DOM 钉在内存里。

这里把同样的逻辑合并成**一个页面内函数**，只返回一个 JSON 字符串：
- 原始字符串不会生成 RemoteObject，因此不会泄漏；
- 每次轮询只需 1 次 ``Runtime.callFunctionOn``；
- 候选排序规则、锚点格式、内容节点定位、深度提取与原实现逐条对齐
  （见 tests/test_stream_snapshot.py 的真实浏览器对照测试）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from app.core.extractors.deep_mode import DeepBrowserExtractor

SNAPSHOT_PROTOCOL_VERSION = 1

# 与 GeneratingStatusCache 的指示器保持一致（逐个取第一个匹配并判断可见性）
GENERATING_INDICATOR_SELECTORS: Tuple[str, ...] = (
    'button[aria-label*="Stop"]',
    'button[aria-label*="stop"]',
    '[data-state="streaming"]',
    '.stop-generating',
)

_CONTENT_CHILD_SELECTOR = DeepBrowserExtractor.CONTENT_CHILD_SELECTOR_COMBINED
if _CONTENT_CHILD_SELECTOR.startswith("css:"):
    _CONTENT_CHILD_SELECTOR = _CONTENT_CHILD_SELECTOR[4:]


_SNAPSHOT_JS_TEMPLATE = r"""
function (cfgRaw) {
  var cfg = {};
  try { cfg = JSON.parse(String(cfgRaw || '{}')) || {}; } catch (e) { cfg = {}; }
  var out = { v: __VERSION__, ok: true, n: 0 };

  var deepExtract = function () {
    __DEEP_EXTRACT_BODY__
  };

  function toStr(v) { return (v === null || v === undefined) ? '' : String(v); }
  function attr(el, name) {
    try { var v = el.getAttribute(name); return v === null ? '' : String(v); } catch (e) { return ''; }
  }
  function classText(el) { return attr(el, 'class'); }
  function tagName(el) {
    try { return String(el.localName || el.tagName || '').toLowerCase(); } catch (e) { return ''; }
  }

  // ---------- 元素查找（与 DrissionPage css/xpath 定位结果一致，仅保留元素节点） ----------
  function deepQueryAll(root, sel, acc) {
    var nodes = [];
    try { nodes = root.querySelectorAll('*'); } catch (e) { nodes = []; }
    for (var i = 0; i < nodes.length; i++) {
      var sr = nodes[i].shadowRoot;
      if (sr) {
        try {
          var found = sr.querySelectorAll(sel);
          for (var j = 0; j < found.length; j++) acc.push(found[j]);
        } catch (e) {}
        deepQueryAll(sr, sel, acc);
      }
    }
    return acc;
  }
  function queryAll(kind, q) {
    if (kind === 'xpath') {
      var r = document.evaluate(q, document, null, 7, null);
      var arr = [];
      for (var i = 0; i < r.snapshotLength; i++) {
        var node = r.snapshotItem(i);
        if (node && node.nodeType === 1) arr.push(node);
      }
      return arr;
    }
    var list = Array.prototype.slice.call(document.querySelectorAll(q));
    if (!list.length && cfg.pierceShadow) {
      // performSearch 会穿透 shadow DOM；只在轻 DOM 无结果时补查，避免额外开销
      list = deepQueryAll(document, q, []);
    }
    return list;
  }

  // ---------- 锚点（与 DeepBrowserExtractor.get_anchor 输出格式一致） ----------
  function anchorOf(el) {
    try {
      var stable = ['data-message-id', 'data-turn-id', 'data-testid', 'id'];
      for (var i = 0; i < stable.length; i++) {
        var v = attr(el, stable[i]);
        if (v) return stable[i] + '=' + v;
      }
      var cls = classText(el).trim();
      var classes = cls ? cls.split(/\s+/).filter(Boolean).slice(0, 3) : [];
      var classPart = classes.length ? '|cls=' + classes.join('.') : '';
      var idxPart = '';
      var parent = el.parentElement;
      if (parent) {
        var idx = Array.prototype.indexOf.call(parent.children, el);
        if (idx >= 0) idxPart = '|idx=' + idx;
      }
      return 'tag:' + String(el.localName || 'unknown') + classPart + idxPart;
    } catch (e) {
      return '';
    }
  }

  // ---------- 视觉最新回复选择（与 StreamMonitor._select_candidate_element 一致） ----------
  function selectCandidate(els, column) {
    var sc = document.querySelector('[data-radix-scroll-area-viewport]') || document.scrollingElement || document.documentElement;
    var scScrollTop = sc ? (sc.scrollTop || 0) : 0;
    var scRect = sc ? sc.getBoundingClientRect() : { top: 0, left: 0 };
    var olCache = new Map();
    function olInfo(ol) {
      var info = olCache.get(ol);
      if (info) return info;
      var style = window.getComputedStyle(ol);
      var isReverse = String(style.flexDirection || '').indexOf('reverse') >= 0
        || !!(ol.className && typeof ol.className === 'string' && ol.className.indexOf('reverse') >= 0);
      var children = Array.prototype.slice.call(ol.children);
      var turnIndex = new Map();
      var t = 0;
      for (var i = 0; i < children.length; i++) {
        var c = children[i];
        if (c.children.length > 0 || c.offsetHeight > 0) { turnIndex.set(c, t); t++; }
      }
      var childIndex = new Map();
      for (var k = 0; k < children.length; k++) childIndex.set(children[k], k);
      info = { isReverse: isReverse, turnIndex: turnIndex, childIndex: childIndex };
      olCache.set(ol, info);
      return info;
    }

    var scored = [];
    for (var index = 0; index < els.length; index++) {
      var el = els[index];
      var item = { index: index, bottom: 0, left: 0, area: 0, turn: 0, rev: false, side: 'single' };
      try {
        var rect = el.getBoundingClientRect();
        var ol = el.closest('main ol, ol, [role="feed"], [data-testid*="conversation"]');
        var isReverse = false;
        var turnIdx = 0;
        if (ol) {
          var info = olInfo(ol);
          isReverse = info.isReverse;
          var turnEl = el.closest('main ol > div, ol > li, [data-testid*="conversation-turn"], [class*="turn-container"]');
          if (turnEl && info.turnIndex.has(turnEl)) {
            turnIdx = info.turnIndex.get(turnEl);
          } else if (ol.contains(el)) {
            var direct = el.closest('ol > *');
            turnIdx = (direct && info.childIndex.has(direct)) ? info.childIndex.get(direct) : -1;
          } else {
            turnIdx = 0;
          }
        }
        var side = 'single';
        var col = el.closest('[class*="basis-"]');
        if (col && col.parentElement && col.parentElement.children.length === 2) {
          side = col.parentElement.children[0] === col ? 'left' : 'right';
        }
        var contentBottom = Math.round(rect.bottom - scRect.top + scScrollTop);
        var bottomAbs = Number(rect && rect.bottom || 0) + Number(window.scrollY || 0);
        item.bottom = Number(contentBottom || bottomAbs || 0);
        item.left = Number(rect && rect.left || 0) + Number(window.scrollX || 0);
        item.area = Number(rect && rect.width || 0) * Number(rect && rect.height || 0);
        item.turn = Number(turnIdx || 0);
        item.rev = !!isReverse;
        item.side = side;
      } catch (e) {}
      item.h = column === 'right' ? item.left : -item.left;
      scored.push(item);
    }

    // 1. 锁定最新轮次
    if (scored.length > 1) {
      var turns = scored.filter(function (s) { return s.turn >= 0; }).map(function (s) { return s.turn; });
      if (turns.length) {
        var anyRev = scored.some(function (s) { return s.rev; });
        var target = anyRev ? Math.min.apply(null, turns) : Math.max.apply(null, turns);
        var latest = scored.filter(function (s) { return s.turn === target; });
        if (latest.length) scored = latest;
      }
    }
    // 2. 目标侧
    if (column === 'left' || column === 'right') {
      var exact = scored.filter(function (s) { return s.side === column; });
      if (exact.length) {
        scored = exact;
      } else {
        var opposite = column === 'right' ? 'left' : 'right';
        if (scored.some(function (s) { return s.side === opposite; })) {
          return { blocked: true };
        }
        if (scored.length > 1) {
          var lefts = scored.map(function (s) { return s.left; });
          var minL = Math.min.apply(null, lefts), maxL = Math.max.apply(null, lefts);
          var span = maxL - minL;
          if (span >= 80.0) {
            var mid = minL + span / 2.0;
            var sideCands = scored.filter(function (s) { return column === 'right' ? s.left >= mid : s.left <= mid; });
            if (sideCands.length) scored = sideCands;
          }
        }
      }
    }
    // 3. 排序：bottom、水平分、面积降序，index 升序
    scored.sort(function (a, b) {
      return (b.bottom - a.bottom) || (b.h - a.h) || (b.area - a.area) || (a.index - b.index);
    });
    return { best: scored[0] };
  }

  // ---------- 内容节点（与 DeepBrowserExtractor.find_content_node 一致） ----------
  function findContentNode(el) {
    try {
      var cls = classText(el);
      var tag = tagName(el);
      var patterns = ['ds-markdown', 'markdown-body', 'prose', 'message-content', 'response-content'];
      for (var i = 0; i < patterns.length; i++) {
        if (cls.indexOf(patterns[i]) >= 0 && cls.indexOf('paragraph') < 0 && cls.indexOf('html') < 0) return el;
      }
      if ((tag === 'div' || tag === 'article' || tag === 'section')
          && (cls.indexOf('markdown') >= 0 || cls.indexOf('prose') >= 0) && cls.indexOf('paragraph') < 0) {
        return el;
      }
    } catch (e) {}
    try {
      var kids = el.querySelectorAll(__CONTENT_CHILD_SELECTOR__);
      var limit = Math.min(kids.length, 8);
      for (var k = 0; k < limit; k++) {
        var child = kids[k];
        if (classText(child).indexOf('paragraph') >= 0) continue;
        var t = child.textContent || child.innerText || '';
        if (String(t).trim().length > 0) return child;
      }
    } catch (e) {}
    return el;
  }

  function extractText(el) {
    var node = findContentNode(el);
    var text = '';
    try { text = toStr(deepExtract.call(node)); } catch (e) { text = ''; }
    if (text && text.trim()) return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    var fallback = '';
    try { fallback = toStr(node.innerText || node.textContent || ''); } catch (e) { fallback = ''; }
    return fallback.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  }

  // ---------- 图片（与 StreamMonitor._extract_image_info / _extract_page_image_info 一致） ----------
  var IMG_OK = /^(?:https?:\/\/|blob:|data:image\/)/i;
  var IMG_HTTP = /^https?:\/\//i;
  function collectImages(nodes, limit) {
    var sources = new Set();
    var urls = [];
    var refs = [];
    var token = toStr(cfg.baselineToken);
    var prop = toStr(cfg.baselineProperty);
    var exclude = !!cfg.excludeExisting;
    for (var i = 0; i < nodes.length; i++) {
      var img = nodes[i];
      try {
        var baseline = prop ? img[prop] : null;
        if (exclude && token && baseline && String(baseline.token || '') === token) continue;
        var src = String(img.currentSrc || img.getAttribute('src') || img.src || '').trim();
        if (!src || sources.has(src)) continue;
        if (!IMG_OK.test(src)) continue;
        sources.add(src);
        if (src.length <= 8192) refs.push(src);
        if (IMG_HTTP.test(src)) urls.push(src);
      } catch (e) {}
    }
    if (limit) {
      urls = Array.from(new Set(urls)).slice(-limit);
      refs = Array.from(new Set(refs)).slice(-limit);
    }
    return { count: sources.size, urls: urls, references: refs };
  }

  // ---------- 生成状态（与 GeneratingStatusCache 一致：每个选择器取第一个匹配，
  //            可见性判定同 DrissionPage states.is_displayed） ----------
  function isDisplayed(el) {
    try {
      var style = window.getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') return false;
      if (el.hidden) return false;
      return true;
    } catch (e) { return false; }
  }
  function generatingNow() {
    var sels = cfg.generatingSelectors || [];
    for (var i = 0; i < sels.length; i++) {
      try {
        var el = document.querySelector(sels[i]);
        if (el && isDisplayed(el)) return true;
      } catch (e) {}
    }
    return false;
  }

  try {
    var els;
    try {
      els = queryAll(cfg.kind, cfg.query);
    } catch (e) {
      return JSON.stringify({ v: __VERSION__, ok: false, error: 'selector:' + String(e && e.message || e) });
    }
    out.n = els.length;
    if (cfg.withGenerating) out.generating = generatingNow();
    if (cfg.withPageImages) {
      var pageNodes = [];
      try {
        var root = document.querySelector('main') || document;
        pageNodes = Array.prototype.slice.call(root.querySelectorAll(toStr(cfg.pageImageSelector) || 'img'));
      } catch (e) { pageNodes = []; }
      var pageInfo = collectImages(pageNodes, 256);
      out.pageImages = { urls: pageInfo.urls, references: pageInfo.references };
    }
    if (!els.length) return JSON.stringify(out);

    var target = null;
    var preferAnchor = toStr(cfg.preferAnchor);
    if (preferAnchor) {
      for (var p = els.length - 1; p >= 0; p--) {
        if (anchorOf(els[p]) === preferAnchor) { target = els[p]; out.preferMatched = true; break; }
      }
    }
    if (!target) {
      var sel = selectCandidate(els, toStr(cfg.column) || 'left');
      if (sel.blocked) { out.blocked = true; return JSON.stringify(out); }
      if (!sel.best) return JSON.stringify(out);
      target = els[sel.best.index];
      out.selected = { index: sel.best.index, bottom: sel.best.bottom, left: sel.best.left };
    }
    out.found = true;
    out.anchor = anchorOf(target);
    out.text = extractText(target);
    if (cfg.withImages) {
      var info = collectImages(Array.prototype.slice.call(target.querySelectorAll('img') || []), 0);
      out.images = info;
    }
    return JSON.stringify(out);
  } catch (e) {
    return JSON.stringify({ v: __VERSION__, ok: false, error: String(e && e.message || e) });
  }
}
"""


def _build_snapshot_js() -> str:
    deep_body = DeepBrowserExtractor.DEEP_EXTRACT_JS.strip()
    return (
        _SNAPSHOT_JS_TEMPLATE
        .replace("__DEEP_EXTRACT_BODY__", deep_body)
        .replace("__CONTENT_CHILD_SELECTOR__", json.dumps(_CONTENT_CHILD_SELECTOR))
        .replace("__VERSION__", str(SNAPSHOT_PROTOCOL_VERSION))
        .strip()
    )


SNAPSHOT_JS = _build_snapshot_js()


def resolve_locator(selector: str) -> Optional[Tuple[str, str]]:
    """Translate a project selector into ``(kind, query)`` for in-page lookup.

    Mirrors ``ElementFinder._find_all_with_syntax``: DrissionPage syntaxes are
    passed through, plain strings are treated as CSS. Returns ``None`` when
    the selector cannot be expressed as CSS/XPath (caller falls back).
    """
    raw = str(selector or "").strip()
    if not raw:
        return None
    if raw.startswith(("tag:", "@", "xpath:", "css:")) or "@@" in raw:
        loc_text = raw
    else:
        loc_text = f"css:{raw}"
    try:
        from DrissionPage._functions.locator import get_loc

        by, query = get_loc(loc_text)
    except Exception:
        if loc_text.startswith("css:"):
            return ("css", loc_text[4:].strip())
        if loc_text.startswith("xpath:"):
            return ("xpath", loc_text[6:].strip())
        return None
    by = str(by or "").lower()
    if by == "css selector":
        return ("css", str(query))
    if by == "xpath":
        return ("xpath", str(query))
    return None


def build_snapshot_config(
    selector: str,
    *,
    prefer_anchor: Optional[str],
    column: str,
    with_images: bool,
    with_page_images: bool,
    page_image_selector: str = "img",
    baseline_token: str = "",
    baseline_property: str = "",
    exclude_existing: bool = False,
    with_generating: bool = True,
    pierce_shadow: bool = True,
) -> Optional[Dict[str, Any]]:
    locator = resolve_locator(selector)
    if locator is None:
        return None
    kind, query = locator
    return {
        "kind": kind,
        "query": query,
        "preferAnchor": str(prefer_anchor or ""),
        "column": column if column in {"left", "right"} else "left",
        "withImages": bool(with_images),
        "withPageImages": bool(with_page_images),
        "pageImageSelector": str(page_image_selector or "img"),
        "baselineToken": str(baseline_token or ""),
        "baselineProperty": str(baseline_property or ""),
        "excludeExisting": bool(exclude_existing),
        "withGenerating": bool(with_generating),
        "generatingSelectors": list(GENERATING_INDICATOR_SELECTORS),
        "pierceShadow": bool(pierce_shadow),
    }


def decode_snapshot(raw: Any) -> Optional[Dict[str, Any]]:
    """Decode the page snapshot; ``None`` means "not usable, fall back"."""
    if not isinstance(raw, str) or not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != SNAPSHOT_PROTOCOL_VERSION:
        return None
    if not data.get("ok", False):
        return None
    return data


def image_lists(info: Any) -> Tuple[int, List[str], List[str]]:
    if not isinstance(info, dict):
        return 0, [], []
    try:
        count = int(info.get("count", 0) or 0)
    except (TypeError, ValueError):
        count = 0
    urls = [str(u) for u in (info.get("urls") or []) if u]
    refs = [str(u) for u in (info.get("references") or []) if u]
    return count, urls, refs


__all__ = [
    "GENERATING_INDICATOR_SELECTORS",
    "SNAPSHOT_JS",
    "SNAPSHOT_PROTOCOL_VERSION",
    "build_snapshot_config",
    "decode_snapshot",
    "image_lists",
    "resolve_locator",
]
