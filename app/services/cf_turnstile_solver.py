"""
app/services/cf_turnstile_solver.py - Cloudflare Turnstile / 5s 盾通用自动过盾服务

职责：
1. 探测并等待可点击人机验证按钮出现（支持动态选择器、自定义探测脚本或保底坐标）；
2. 复用项目的低熵模式移动轨迹（smooth_move_mouse + cdp_precise_click）平滑移动并精确点击；
3. 确认验证通过（最多尝试指定次数，连续失败抛出异常）；
4. 通过后比对当前 URL 与本次请求实际占用的目标 URL，支持参数化校验与重定向。
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from app.core.config import logger as default_logger
from app.utils.human_mouse import cdp_precise_click as default_cdp_precise_click, smooth_move_mouse as default_smooth_move_mouse

_DEFAULT_MAX_ATTEMPTS = 3

DEFAULT_FIND_CLICK_POINT_JS = r"""
return (() => {
  try {
    const visibleRect = (el) => {
      if (!el) return null;
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      if (
        style.display === 'none' ||
        style.visibility === 'hidden' ||
        Number(style.opacity || 1) <= 0.02 ||
        rect.width < 8 ||
        rect.height < 8
      ) {
        return null;
      }
      const vw = window.innerWidth || document.documentElement.clientWidth || 0;
      const vh = window.innerHeight || document.documentElement.clientHeight || 0;
      if (rect.right < 0 || rect.bottom < 0 || rect.left > vw || rect.top > vh) {
        return null;
      }
      const left = Math.max(0, rect.left);
      const top = Math.max(0, rect.top);
      const right = Math.min(vw, rect.right);
      const bottom = Math.min(vh, rect.bottom);
      return {
        left,
        top,
        right,
        bottom,
        width: right - left,
        height: bottom - top,
        rawWidth: rect.width,
        rawHeight: rect.height
      };
    };

    const describe = (el) => {
      const attrs = [
        el.getAttribute('src'),
        el.getAttribute('title'),
        el.getAttribute('aria-label'),
        el.getAttribute('name'),
        el.id,
        el.className
      ];
      return attrs.map((v) => String(v || '')).join(' ').toLowerCase();
    };

    const candidates = [];
    const pushCandidate = (el, kind, score) => {
      const rect = visibleRect(el);
      if (!rect) return;
      const text = describe(el);
      const checkboxLike = /checkbox|anchor|recaptcha|hcaptcha|turnstile|challenge|cloudflare/.test(text);
      let clickX = rect.left + rect.width / 2;
      let clickY = rect.top + rect.height / 2;
      if (checkboxLike || rect.width >= 120) {
        clickX = rect.left + Math.min(Math.max(rect.width * 0.16, 24), 46);
      }
      const insetX = Math.min(4, Math.max(0, (rect.width - 1) / 2));
      const insetY = Math.min(4, Math.max(0, (rect.height - 1) / 2));
      clickX = Math.max(rect.left + insetX, Math.min(rect.left + rect.width - 1 - insetX, clickX));
      clickY = Math.max(rect.top + insetY, Math.min(rect.top + rect.height - 1 - insetY, clickY));
      candidates.push({
        kind,
        score: score + (checkboxLike ? 20 : 0) + Math.min(rect.width, 320) / 100,
        x: Math.round(clickX),
        y: Math.round(clickY),
        rect,
        hint: text.slice(0, 160)
      });
    };

    for (const el of document.querySelectorAll('iframe')) {
      const text = describe(el);
      const rect = visibleRect(el);
      const isExpandedImage = !!rect && rect.width >= 360 && rect.height >= 300;
      if (isExpandedImage) continue;
      if (/recaptcha|google\.com\/recaptcha/.test(text)) pushCandidate(el, 'recaptcha_iframe', 100);
      else if (/hcaptcha|hcaptcha\.com/.test(text)) pushCandidate(el, 'hcaptcha_iframe', 95);
      else if (/turnstile|challenges\.cloudflare\.com|cloudflare|challenge/.test(text)) pushCandidate(el, 'cloudflare_iframe', 90);
    }

    for (const selector of [
      '.cf-turnstile',
      '#challenge-stage',
      '#turnstile-wrapper',
      '.g-recaptcha',
      '.h-captcha',
      '[data-sitekey]',
      '[role="checkbox"]',
      'input[type="checkbox"]'
    ]) {
      for (const el of document.querySelectorAll(selector)) {
        pushCandidate(el, selector, 50);
      }
    }

    candidates.sort((a, b) => b.score - a.score);
    const vw = window.innerWidth || document.documentElement.clientWidth || 800;
    const vh = window.innerHeight || document.documentElement.clientHeight || 600;

    if (candidates.length > 0) {
      return { ok: true, ...candidates[0], viewport: { width: vw, height: vh } };
    }
    return {
      ok: false,
      reason: 'no_candidate_element',
      viewport: { width: vw, height: vh },
      fallback: {
        x: Math.round(vw / 2 - 90),
        y: Math.round(vh / 2 + 10)
      }
    };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
})();
"""

DEFAULT_PAGE_READY_PROBE_JS = r"""
return (() => {
  try {
    const isVisible = (el) => {
      if (!el || !el.isConnected) return false;
      const r = el.getBoundingClientRect();
      const s = window.getComputedStyle(el);
      return r.width >= 8 && r.height >= 8 && r.bottom > 0 && r.right > 0 &&
             r.top < window.innerHeight && r.left < window.innerWidth &&
             s.display !== 'none' && s.visibility !== 'hidden' &&
             Number(s.opacity || 1) > 0.02;
    };
    const text = String(document.body && document.body.innerText ? document.body.innerText : '').toLowerCase();
    const hasVerifyText = text.includes('security verification') ||
                          text.includes('protected by cloudflare') ||
                          text.includes('verify you are human') ||
                          text.includes('checking your browser') ||
                          text.includes('confirm you are human') ||
                          text.includes('确认您是真人') ||
                          text.includes('人机身份验证') ||
                          text.includes('请验证您是真人') ||
                          text.includes('正在进行安全验证');
    const challengeIframes = Array.from(document.querySelectorAll(
      'iframe[src*="challenges.cloudflare.com"], iframe[src*="challenge-platform"], iframe[title*="challenge" i], iframe[title*="cloudflare" i], iframe[title*="turnstile" i]'
    ));
    const hasVisibleChallengeIframe = challengeIframes.some(isVisible);
    if (hasVerifyText || hasVisibleChallengeIframe) {
      return { resolved: false, reason: 'challenge_still_present' };
    }
    const hasInput = !!document.querySelector('textarea, [contenteditable="true"], input[placeholder*="ask" i], input[placeholder*="Type" i]');
    const hasArenaUI = text.includes('new chat') || text.includes('leaderboard') || text.includes('battle mode') || hasInput;
    return { resolved: true, ready: hasArenaUI || hasInput };
  } catch (e) {
    return { resolved: false, error: String(e) };
  }
})();
"""


def _interruptible_sleep(seconds: float, raise_if_cancelled=None, step: float = 0.1) -> None:
    deadline = time.time() + max(0.0, float(seconds))
    while time.time() < deadline:
        if raise_if_cancelled:
            raise_if_cancelled()
        remaining = deadline - time.time()
        time.sleep(min(step, max(0.01, remaining)))


def is_valid_http_url(url: Optional[str]) -> bool:
    """通用 HTTP/HTTPS URL 有效性校验（排除 Cloudflare Challenge 自身页面）。"""
    if not url or not isinstance(url, str):
        return False
    u = url.lower().strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    if "challenges.cloudflare.com" in u or "challenge-platform" in u:
        return False
    return True


def _urls_match(url1: Optional[str], url2: Optional[str]) -> bool:
    u1 = str(url1 or "").strip().rstrip("/")
    u2 = str(url2 or "").strip().rstrip("/")
    if not u1 and not u2:
        return True
    if not u1 or not u2:
        return False
    if u1 == u2:
        return True
    try:
        p1 = urlsplit(u1)
        p2 = urlsplit(u2)
        if p1.netloc.lower() == p2.netloc.lower() and p1.path.rstrip("/") == p2.path.rstrip("/"):
            if p1.query == p2.query:
                return True
    except Exception:
        pass
    return False


def _get_occupied_url(
    tab: Any,
    session: Any = None,
    default_url: str = "https://arena.ai/code",
    validator_fn: Optional[Callable[[Optional[str]], bool]] = None,
) -> str:
    """提取本次请求在过盾前绑定的实际占用 URL。"""
    validator = validator_fn or is_valid_http_url
    if session is not None:
        occupied = getattr(session, "_request_occupied_url", None)
        if occupied and validator(occupied):
            return str(occupied).strip()
        last_known = getattr(session, "last_known_url", None)
        if last_known and validator(last_known):
            return str(last_known).strip()
    try:
        tab_url = str(getattr(tab, "url", "") or "").strip()
        if tab_url and validator(tab_url):
            return tab_url
    except Exception:
        pass
    return default_url


def _wait_for_clickable_point(
    tab: Any,
    timeout_sec: float = 6.0,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
    fallback_coords: Optional[Tuple[int, int]] = None,
    find_click_point_js: Optional[str] = None,
    log: Any = default_logger,
) -> Dict[str, Any]:
    """等待并获取可点击按钮坐标，若无法准确定位元素则使用固定/自适应保底坐标。"""
    deadline = time.time() + max(1.0, float(timeout_sec))
    last_fallback = None
    js_code = find_click_point_js or DEFAULT_FIND_CLICK_POINT_JS

    while time.time() < deadline:
        if raise_if_cancelled:
            raise_if_cancelled()
        try:
            probe = tab.run_js(js_code)
            if isinstance(probe, dict):
                if probe.get("ok"):
                    x = max(0, int(probe["x"]) + random.randint(-2, 2))
                    y = max(0, int(probe["y"]) + random.randint(-2, 2))
                    return {
                        "ok": True,
                        "x": x,
                        "y": y,
                        "kind": probe.get("kind", "element"),
                        "rect": probe.get("rect"),
                    }
                elif probe.get("fallback"):
                    last_fallback = probe["fallback"]
        except Exception as e:
            log.debug(f"[CF_SOLVER] 寻找点击坐标异常: {e}")

        _interruptible_sleep(0.3, raise_if_cancelled=raise_if_cancelled)

    # 无法准确定位元素时启用保底坐标
    if fallback_coords is not None:
        fx, fy = max(0, int(fallback_coords[0])), max(0, int(fallback_coords[1]))
    elif last_fallback and isinstance(last_fallback, dict):
        fx = max(0, int(last_fallback.get("x", 310)))
        fy = max(0, int(last_fallback.get("y", 310)))
    else:
        try:
            vw = int(tab.run_js("return window.innerWidth || document.documentElement.clientWidth || 800") or 800)
            vh = int(tab.run_js("return window.innerHeight || document.documentElement.clientHeight || 600") or 600)
            fx = max(0, int(vw / 2 - 90))
            fy = max(0, int(vh / 2 + 10))
        except Exception:
            fx, fy = 310, 310

    fx = max(0, fx + random.randint(-4, 4))
    fy = max(0, fy + random.randint(-4, 4))
    log.info(f"[CF_SOLVER] 未定位到具体元素，启用固定/保底坐标点击: ({fx}, {fy})")
    return {"ok": True, "x": fx, "y": fy, "kind": "fixed_fallback"}


def _wait_for_challenge_resolved(
    tab: Any,
    timeout_sec: float = 8.0,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
    page_ready_probe_js: Optional[str] = None,
    log: Any = default_logger,
) -> bool:
    """轮询等待验证通过（特征文字与 iframe 消失且页面就绪）。"""
    deadline = time.time() + max(1.0, float(timeout_sec))
    js_code = page_ready_probe_js or DEFAULT_PAGE_READY_PROBE_JS
    while time.time() < deadline:
        if raise_if_cancelled:
            raise_if_cancelled()
        try:
            probe = tab.run_js(js_code)
            if isinstance(probe, dict) and probe.get("resolved"):
                return True
        except Exception as e:
            log.debug(f"[CF_SOLVER] 探测验证状态异常: {e}")
        _interruptible_sleep(0.5, raise_if_cancelled=raise_if_cancelled)
    return False


def solve_turnstile_challenge(
    tab: Any,
    session: Any = None,
    target_url: Optional[str] = None,
    is_valid_url_fn: Optional[Callable[[Optional[str]], bool]] = None,
    ready_probe_js: Optional[str] = None,
    click_point_js: Optional[str] = None,
    fallback_coords: Optional[Tuple[int, int]] = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    click_timeout_sec: float = 6.0,
    resolve_timeout_sec: float = 8.0,
    raise_if_cancelled: Optional[Callable[[], None]] = None,
    smooth_move_fn: Optional[Callable[..., Any]] = None,
    cdp_click_fn: Optional[Callable[..., Any]] = None,
    logger: Any = None,
) -> Dict[str, Any]:
    """
    通用 Cloudflare Turnstile / 5s 盾自动过盾流程。
    """
    log = logger or default_logger
    smooth_move = smooth_move_fn or default_smooth_move_mouse
    cdp_click = cdp_click_fn or default_cdp_precise_click

    if raise_if_cancelled:
        raise_if_cancelled()

    occupied_url = target_url or _get_occupied_url(
        tab,
        session,
        validator_fn=is_valid_url_fn or is_valid_http_url,
    )
    log.info(f"[CF_SOLVER] 启动 Cloudflare 5s 盾自动处理: 实际占用目标 URL={occupied_url}")

    attempts = 0
    passed = False

    while attempts < max_attempts:
        attempts += 1
        if raise_if_cancelled:
            raise_if_cancelled()

        log.info(f"[CF_SOLVER] 第 {attempts}/{max_attempts} 次尝试通过 5s 盾...")

        click_target = _wait_for_clickable_point(
            tab,
            timeout_sec=click_timeout_sec,
            raise_if_cancelled=raise_if_cancelled,
            fallback_coords=fallback_coords,
            find_click_point_js=click_point_js,
            log=log,
        )

        click_x = max(0, int(click_target["x"]))
        click_y = max(0, int(click_target["y"]))
        log.info(
            f"[CF_SOLVER] 准备低熵点击验证目标 (第 {attempts} 次): "
            f"({click_x}, {click_y}), kind={click_target.get('kind', 'fallback')}"
        )

        start_x = max(0, click_x - random.randint(40, 80))
        start_y = max(0, click_y - random.randint(30, 60))
        smooth_move(tab, (start_x, start_y), (click_x, click_y))
        _interruptible_sleep(random.uniform(0.06, 0.14), raise_if_cancelled=raise_if_cancelled)
        click_ok = cdp_click(tab, click_x, click_y)
        if not click_ok:
            log.warning(f"[CF_SOLVER] CDP 精确点击未确认: ({click_x}, {click_y})")

        passed = _wait_for_challenge_resolved(
            tab,
            timeout_sec=resolve_timeout_sec,
            raise_if_cancelled=raise_if_cancelled,
            page_ready_probe_js=ready_probe_js,
            log=log,
        )
        if passed:
            log.info(f"[CF_SOLVER] ✅ 第 {attempts} 次尝试后 5s 盾验证通过！")
            break
        else:
            log.warning(f"[CF_SOLVER] 第 {attempts} 次点击后 5s 盾仍未通过，准备重试...")
            _interruptible_sleep(random.uniform(1.5, 2.5), raise_if_cancelled=raise_if_cancelled)

    if not passed:
        err_msg = f"连续尝试 {max_attempts} 次仍未通过 Cloudflare 5s 盾人机验证"
        log.error(f"[CF_SOLVER] ❌ {err_msg}")
        raise RuntimeError(f"cf_turnstile_verify_failed_after_{max_attempts}_attempts")

    current_url = str(getattr(tab, "url", "") or "").strip()
    url_matches = _urls_match(current_url, occupied_url)

    validator = is_valid_url_fn or is_valid_http_url
    if not url_matches and occupied_url and validator(occupied_url):
        log.warning(
            f"[CF_SOLVER] 过盾后 URL 不一致: 当前 URL ({current_url}) != 实际占用 URL ({occupied_url})，"
            f"正在跳转回实际占用 URL..."
        )
        try:
            tab.get(occupied_url)
            _interruptible_sleep(random.uniform(1.0, 2.0), raise_if_cancelled=raise_if_cancelled)
            current_url = str(getattr(tab, "url", "") or "").strip()
            log.info(f"[CF_SOLVER] 已跳转回实际占用 URL: {current_url}")
        except Exception as e:
            log.error(f"[CF_SOLVER] 跳转回实际占用 URL 失败: {e}")
            raise
    else:
        log.info(f"[CF_SOLVER] 过盾后 URL 一致 ({current_url})，无需重定向。")

    return {
        "ok": True,
        "attempts": attempts,
        "current_url": current_url,
        "occupied_url": occupied_url,
        "url_redirected": not url_matches,
    }


# 兼容别名
solve_arena_turnstile_challenge = solve_turnstile_challenge


__all__ = [
    "DEFAULT_FIND_CLICK_POINT_JS",
    "DEFAULT_PAGE_READY_PROBE_JS",
    "_get_occupied_url",
    "_interruptible_sleep",
    "_urls_match",
    "_wait_for_challenge_resolved",
    "_wait_for_clickable_point",
    "is_valid_http_url",
    "solve_arena_turnstile_challenge",
    "solve_turnstile_challenge",
]
