"""``app/api/system`` 的 diagnostics 分组（原 app/api/system.py 的一部分）。"""

import asyncio
import os
import os as _os
import re
import threading as _threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil as _psutil
from fastapi import Depends, HTTPException, Request

# 从子模块直接导入而非 `from app.api.system import runtime`：
# 后者要求 app.api.system 的 __init__ 先执行完，与 __init__ 导入本模块形成环。
# 写成 `import ... as runtime` 既断开环，又保留 runtime.get_browser() 这种
# 属性访问形式——测试用 monkeypatch.setattr(runtime, "get_browser", ...) 打桩，
# 若改成 `from ... import get_browser` 拿到的是直接引用，桩会失效。
import app.api.system.runtime as runtime
from app.api.deps import verify_dashboard_auth as verify_auth
from app.api.system.common import (
    _load_env_config_from_file,
    _read_json_object_or_400,
    logger,
    router,
)
from app.core import BrowserConnectionError
from app.core.config import AppConfig
from app.core.driver import as_element, driver_for_tab
from app.utils.site_url import extract_remote_site_domain, route_domain_matches

_PROJECT_PROCESS_CACHE: Dict[int, _psutil.Process] = {}

_PROJECT_PROCESS_CACHE_LOCK = _threading.Lock()

_PROJECT_PROCESS_SNAPSHOT_TTL_SECONDS = 5.0

_PROJECT_PROCESS_SNAPSHOT_CACHE = {
    "expires_at": 0.0,
    "snapshot": None,
}

_PROJECT_PROCESS_SNAPSHOT_CACHE_LOCK = _threading.Lock()

_PROJECT_MEMORY_CACHE_TTL_SECONDS = 10.0

_PROJECT_MEMORY_CACHE = {
    "expires_at": 0.0,
    "memory_mb": 0.0,
}

_PROJECT_MEMORY_CACHE_LOCK = _threading.Lock()

_SYSTEM_STATS_CACHE = {
    "expires_at": 0.0,
    "payload": {
        "memory_mb": 0.0,
        "disk_status": "0 MB",
        "total_requests": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "cpu_percent": 0.0,
        "project_cpu": 0.0,
        "memory_percent": 0.0,
        "project_memory_percent": 0.0,
        # 与实际构建的 payload 保持同一形状，避免消费方缺键
        "running_count": 0,
        "queued_count": 0,
    },
}

_SYSTEM_STATS_CACHE_LOCK = _threading.Lock()

_DISK_USAGE_CACHE = {
    "expires_at": 0.0,
    "value": 0.0,
    "last_success_value": 0.0,
}

_DISK_USAGE_REFRESH_LOCK = _threading.Lock()

_DISK_USAGE_REFRESH_WORKER: Optional[_threading.Thread] = None


def _build_selector_test_diagnosis(
    selector: str,
    match_count: int,
    top_candidates: Optional[list[dict[str, Any]]] = None,
) -> Dict[str, Any]:
    normalized = str(selector or "").strip()
    warnings: list[str] = []
    tips: list[str] = []

    if ":nth-child" in normalized or ":nth-of-type" in normalized:
        warnings.append("当前写法用了 nth-child / nth-of-type，页面结构稍微变化就容易失效。")
    if normalized.count(">") >= 3 or len(re.split(r"\s+", normalized)) >= 5:
        warnings.append("当前选择器层级比较深，维护成本会偏高，建议优先收敛到稳定属性。")
    if ":has(" in normalized:
        warnings.append("当前选择器包含 :has()，兼容性和稳定性通常都不如稳定属性写法。")
    if re.search(r"\.[A-Za-z0-9_-]{14,}", normalized):
        warnings.append("当前写法里有很长的 class 名，看起来像动态类名，页面一改就可能失效。")

    status = "missing"
    if match_count <= 0:
        summary = "当前没有命中任何元素。"
        tips.extend([
            "先检查是不是还停在正确页面，很多站点切到聊天页前后 DOM 差别很大。",
            "优先试试 id、data-testid、aria-label、name 这类稳定属性，不要先从随机 class 开始。",
            "如果页面刚切换完，适当把超时时间调高一点再测一次。",
        ])
    elif match_count == 1:
        status = "unique"
        summary = "已经唯一命中 1 个元素，可以继续判断是不是命中了你真正想要的目标。"
        tips.extend([
            "下一步看元素摘要和候选选择器，确认它是不是你真正要找的输入框、发送按钮或回复容器。",
            "如果当前写法能命中，但很依赖深层结构或长 class，优先换成下方更稳的候选。",
        ])
    else:
        status = "multiple"
        summary = f"当前命中了 {match_count} 个元素，范围还不够收敛。"
        tips.extend([
            "先看下方每个命中元素的候选选择器，优先挑唯一命中的那种。",
            "如果都是 button / div 这类大范围匹配，通常要补上 aria-label、data-testid、name 或更具体的父级信息。",
        ])

    if top_candidates:
        unique_count = sum(1 for item in top_candidates if item.get("unique"))
        if unique_count > 0:
            tips.insert(0, f"下面已经生成了 {unique_count} 个唯一候选，可以直接挑一个更稳的写法继续复测。")

    return {
        "status": status,
        "summary": summary,
        "warnings": warnings,
        "tips": tips,
    }


def _collect_selector_test_element_snapshot(ele: Any) -> Dict[str, Any]:
    try:
        snapshot = as_element(ele).run_js(
            """
            return (function () {
                const target = this;
                const tag = String(target.tagName || '').toLowerCase();
                const textValue = String(target.innerText || target.textContent || '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const attrKeys = ['id', 'class', 'name', 'data-testid', 'aria-label', 'role', 'type', 'placeholder', 'href'];
                const attributes = {};
                for (const key of attrKeys) {
                    try {
                        const value = target.getAttribute ? target.getAttribute(key) : null;
                        if (value) {
                            attributes[key] = String(value).trim().slice(0, 160);
                        }
                    } catch (error) {}
                }

                function cssEscapeIdent(value) {
                    const raw = String(value || '');
                    if (!raw) return raw;
                    if (window.CSS && typeof window.CSS.escape === 'function') {
                        return window.CSS.escape(raw);
                    }
                    return raw.replace(/[^a-zA-Z0-9_-]/g, function (char) {
                        return '\\\\' + char;
                    });
                }

                function cssEscapeString(value) {
                    return String(value || '')
                        .replace(/\\\\/g, '\\\\\\\\')
                        .replace(/"/g, '\\\\\\"');
                }

                function looksStableClass(token) {
                    const cls = String(token || '').trim();
                    if (!cls || cls.length > 40) return false;
                    if (/^(css|jsx)-/i.test(cls)) return false;
                    if (/^_[A-Za-z0-9-]{8,}$/.test(cls)) return false;
                    if (/^[A-Fa-f0-9_-]{10,}$/.test(cls)) return false;
                    if (/[A-Z]/.test(cls) && /\\d/.test(cls) && cls.length >= 12) return false;
                    return /^[A-Za-z][A-Za-z0-9_-]*$/.test(cls);
                }

                function pushCandidate(bucket, selector, reason, score) {
                    if (!selector) return;
                    bucket.push({
                        selector,
                        reason,
                        score,
                    });
                }

                const candidates = [];
                const rawClasses = String(attributes['class'] || '')
                    .split(/\\s+/)
                    .map(item => String(item || '').trim())
                    .filter(Boolean);
                const stableClasses = rawClasses.filter(looksStableClass).slice(0, 3);

                if (attributes.id) {
                    pushCandidate(candidates, `#${cssEscapeIdent(attributes.id)}`, 'ID 精确匹配', 120);
                    if (tag) {
                        pushCandidate(candidates, `${tag}#${cssEscapeIdent(attributes.id)}`, '标签 + ID', 112);
                    }
                }

                if (attributes['data-testid']) {
                    pushCandidate(candidates, `[data-testid="${cssEscapeString(attributes['data-testid'])}"]`, 'data-testid', 110);
                    if (tag) {
                        pushCandidate(candidates, `${tag}[data-testid="${cssEscapeString(attributes['data-testid'])}"]`, '标签 + data-testid', 104);
                    }
                }

                if (attributes['aria-label']) {
                    pushCandidate(candidates, `[aria-label="${cssEscapeString(attributes['aria-label'])}"]`, 'aria-label', 96);
                    if (tag) {
                        pushCandidate(candidates, `${tag}[aria-label="${cssEscapeString(attributes['aria-label'])}"]`, '标签 + aria-label', 92);
                    }
                }

                if (attributes.name) {
                    pushCandidate(candidates, `[name="${cssEscapeString(attributes.name)}"]`, 'name', 92);
                    if (tag) {
                        pushCandidate(candidates, `${tag}[name="${cssEscapeString(attributes.name)}"]`, '标签 + name', 88);
                    }
                }

                if (attributes.placeholder) {
                    pushCandidate(candidates, `[placeholder="${cssEscapeString(attributes.placeholder)}"]`, 'placeholder', 76);
                    if (tag) {
                        pushCandidate(candidates, `${tag}[placeholder="${cssEscapeString(attributes.placeholder)}"]`, '标签 + placeholder', 72);
                    }
                }

                if (attributes.type && ['input', 'button', 'textarea'].includes(tag)) {
                    pushCandidate(candidates, `${tag}[type="${cssEscapeString(attributes.type)}"]`, '标签 + type', 74);
                }

                if (attributes.role) {
                    pushCandidate(candidates, `[role="${cssEscapeString(attributes.role)}"]`, 'role', 62);
                    if (tag) {
                        pushCandidate(candidates, `${tag}[role="${cssEscapeString(attributes.role)}"]`, '标签 + role', 58);
                    }
                }

                if (attributes.href && tag === 'a') {
                    pushCandidate(candidates, `a[href="${cssEscapeString(attributes.href)}"]`, '精确 href', 82);
                }

                if (stableClasses.length > 0) {
                    const classSelector = stableClasses.map(item => `.${cssEscapeIdent(item)}`).join('');
                    pushCandidate(candidates, classSelector, '稳定 class 组合', 54);
                    if (tag) {
                        pushCandidate(candidates, `${tag}${classSelector}`, '标签 + 稳定 class 组合', 56);
                    }
                }

                if (tag) {
                    pushCandidate(candidates, tag, '仅标签名（通常过宽）', 8);
                }

                function evaluateSelector(selector) {
                    try {
                        const matches = Array.from(document.querySelectorAll(selector));
                        return {
                            count: matches.length,
                            exact: matches.includes(target),
                        };
                    } catch (error) {
                        return {
                            count: -1,
                            exact: false,
                            error: String(error && error.message ? error.message : error),
                        };
                    }
                }

                const seen = new Set();
                const evaluated = [];
                for (const candidate of candidates) {
                    if (!candidate.selector || seen.has(candidate.selector)) continue;
                    seen.add(candidate.selector);
                    const stats = evaluateSelector(candidate.selector);
                    if (stats.error || !stats.exact) continue;
                    evaluated.push({
                        ...candidate,
                        count: stats.count,
                        unique: stats.count === 1,
                    });
                }

                evaluated.sort((left, right) => {
                    if (left.unique !== right.unique) {
                        return left.unique ? -1 : 1;
                    }
                    if (left.score !== right.score) {
                        return right.score - left.score;
                    }
                    if (left.count !== right.count) {
                        return left.count - right.count;
                    }
                    return String(left.selector).length - String(right.selector).length;
                });

                const rect = target.getBoundingClientRect ? target.getBoundingClientRect() : { x: 0, y: 0, width: 0, height: 0 };
                const style = window.getComputedStyle ? window.getComputedStyle(target) : null;
                const visible = !!(
                    rect.width > 0 &&
                    rect.height > 0 &&
                    (!style || (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0'))
                );

                const warnings = [];
                if (rawClasses.some(item => !looksStableClass(item))) {
                    warnings.push('这个元素带有疑似动态 class，优先考虑 id、data-testid、aria-label 这类属性。');
                }
                if (!evaluated.some(item => item.unique)) {
                    warnings.push('候选里还没有唯一命中的写法，通常需要加稳定属性或更具体的父级范围。');
                }

                return {
                    tag,
                    text: textValue.slice(0, 120),
                    visible,
                    rect: {
                        x: Math.round(rect.x || 0),
                        y: Math.round(rect.y || 0),
                        width: Math.round(rect.width || 0),
                        height: Math.round(rect.height || 0),
                    },
                    attributes,
                    html_preview: String(target.outerHTML || '').replace(/\\s+/g, ' ').trim().slice(0, 400),
                    candidate_selectors: evaluated.slice(0, 8),
                    warnings,
                };
            }).call(this);
            """
        )
        if isinstance(snapshot, dict):
            return snapshot
    except Exception as e:
        logger.debug(f"收集元素快照失败: {e}")

    return {}


def _build_selector_top_candidates(elements: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for element_index, element in enumerate(elements):
        for candidate in element.get("candidate_selectors") or []:
            selector = str(candidate.get("selector") or "").strip()
            if not selector:
                continue

            normalized = {
                "selector": selector,
                "reason": str(candidate.get("reason") or "").strip(),
                "score": int(candidate.get("score") or 0),
                "count": int(candidate.get("count") or 0),
                "unique": bool(candidate.get("unique")),
                "element_index": element_index,
            }

            existing = merged.get(selector)
            if existing is None:
                merged[selector] = normalized
                continue

            if normalized["unique"] and not existing["unique"]:
                merged[selector] = normalized
                continue

            if normalized["score"] > existing["score"]:
                merged[selector] = normalized

    ranked = list(merged.values())
    ranked.sort(
        key=lambda item: (
            0 if item.get("unique") else 1,
            -int(item.get("score") or 0),
            int(item.get("count") or 0),
            int(item.get("element_index") or 0),
            len(str(item.get("selector") or "")),
        )
    )
    return ranked[:10]


def _selector_test_tab_summary(session: Any, tab: Any) -> Dict[str, Any]:
    url = ""
    try:
        url = str(getattr(tab, "url", "") or "")
    except Exception:
        pass
    return {
        "tab_id": str(getattr(tab, "tab_id", "") or ""),
        "session_id": str(getattr(session, "id", "") or "") if session else "",
        "tab_index": int(getattr(session, "persistent_index", 0) or 0) if session else 0,
        "url": url,
    }


def _selector_test_session_candidates(browser: Any) -> List[Any]:
    """Return a stable snapshot instead of letting the pool choose a random tab."""
    pool = browser.tab_pool
    pool.refresh_tabs()
    sessions = pool.get_sessions_snapshot()
    return sorted(
        sessions,
        key=lambda item: (
            int(getattr(item, "persistent_index", 0) or 0),
            str(getattr(item, "id", "") or ""),
        ),
    )


def _run_selector_test(
    selector: str,
    timeout: Any = 2,
    highlight: bool = False,
    route_domain: str = "",
) -> Dict[str, Any]:
    try:
        browser = runtime.get_browser()
        normalized_domain = str(route_domain or "").strip()
        if selector.startswith(('tag:', '@', 'xpath:', 'css:')) or '@@' in selector:
            query_selector = selector
        else:
            query_selector = f'css:{selector}'

        detail_limit = 6
        result = {
            "success": False,
            "selector": selector,
            "locator_used": query_selector,
            "route_domain": normalized_domain,
            "count": 0,
            "elements": [],
            "tabs": [],
            "tabs_tested": 0,
            "matched_tabs": 0,
            "skipped_busy_tabs": 0,
            "inspected_count": 0,
            "truncated": False,
        }

        def inspect_tab(tab: Any, session: Any = None) -> None:
            tab_summary = _selector_test_tab_summary(session, tab)
            actual_domain = extract_remote_site_domain(tab_summary.get("url", "")) or ""
            if normalized_domain and not route_domain_matches(normalized_domain, actual_domain):
                return

            try:
                elements = driver_for_tab(tab).find_all(query_selector, timeout=timeout)
            except Exception as error:
                tab_summary.update({"success": False, "count": 0, "message": str(error)})
                result["tabs"].append(tab_summary)
                return

            if not isinstance(elements, list):
                elements = [elements] if elements else []

            valid_elements = []
            for ele in elements:
                try:
                    if ele and hasattr(ele, 'tag'):
                        valid_elements.append(ele)
                except Exception:
                    pass

            tab_summary.update({
                "success": bool(valid_elements),
                "count": len(valid_elements),
                "message": "找到元素" if valid_elements else "元素未找到",
            })
            result["tabs"].append(tab_summary)
            result["count"] += len(valid_elements)

            if highlight:
                for ele in valid_elements:
                    try:
                        as_element(ele).run_js("""
                            const token = `selector-test-${Date.now()}-${Math.random().toString(36).slice(2)}`;
                            const previous = {
                                outline: this.style.outline,
                                outlineOffset: this.style.outlineOffset,
                                boxShadow: this.style.boxShadow,
                                transition: this.style.transition,
                            };

                            this.dataset.selectorTestHighlightToken = token;
                            this.style.transition = 'outline .15s ease, box-shadow .15s ease';
                            this.style.outline = '3px solid rgba(239, 68, 68, 0.95)';
                            this.style.outlineOffset = '2px';
                            this.style.boxShadow = '0 0 0 6px rgba(251, 191, 36, 0.45)';
                            this.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});

                            setTimeout(() => {
                                if (this.dataset.selectorTestHighlightToken !== token) return;
                                this.style.outline = previous.outline;
                                this.style.outlineOffset = previous.outlineOffset;
                                this.style.boxShadow = previous.boxShadow;
                                this.style.transition = previous.transition;
                                delete this.dataset.selectorTestHighlightToken;
                            }, 5000);
                        """)
                    except Exception as error:
                        logger.debug(f"高亮失败: {error}")

            remaining = max(0, detail_limit - len(result["elements"]))
            start_index = len(result["elements"])
            for offset, ele in enumerate(valid_elements[:remaining]):
                try:
                    ele_info = {
                        "index": start_index + offset,
                        "tag": ele.tag if hasattr(ele, 'tag') else "unknown",
                        "text": "",
                        "candidate_selectors": [],
                        "warnings": [],
                        "tab_id": tab_summary.get("tab_id", ""),
                        "tab_index": tab_summary.get("tab_index", 0),
                    }

                    snapshot = _collect_selector_test_element_snapshot(ele)
                    if snapshot:
                        ele_info.update(snapshot)

                    try:
                        text = ele.text
                        if text:
                            ele_info["text"] = text[:100]
                    except:
                        pass

                    try:
                        attrs = {}
                        for attr in ['id', 'class', 'name', 'data-testid', 'aria-label']:
                            val = ele.attr(attr)
                            if val:
                                attrs[attr] = val[:50] if isinstance(val, str) else str(val)[:50]
                        if attrs:
                            ele_info["attributes"] = attrs
                    except:
                        pass

                    result["elements"].append(ele_info)
                except Exception as e:
                    logger.debug(f"处理元素 {start_index + offset} 失败: {e}")
                    continue

        if normalized_domain:
            pool = browser.tab_pool
            for candidate in _selector_test_session_candidates(browser):
                status = str(getattr(getattr(candidate, "status", None), "value", ""))
                cached_domain = str(getattr(candidate, "current_domain", "") or "")
                if cached_domain and not route_domain_matches(normalized_domain, cached_domain):
                    continue
                if status != "idle":
                    if cached_domain and route_domain_matches(normalized_domain, cached_domain):
                        result["skipped_busy_tabs"] += 1
                    continue

                raw_tab_id = str(getattr(getattr(candidate, "tab", None), "tab_id", "") or "")
                if not raw_tab_id:
                    continue
                task_id = f"selector_test_{time.time_ns()}"
                acquired = pool.acquire_by_raw_tab_id(
                    raw_tab_id,
                    task_id,
                    timeout=min(max(float(timeout or 2), 0.2), 2.0),
                    count_request=False,
                    activate=False,
                )
                if acquired is None:
                    continue
                try:
                    inspect_tab(acquired.tab, acquired)
                finally:
                    pool.release(
                        acquired.id,
                        check_triggers=False,
                        expected_task_id=task_id,
                    )
        else:
            with browser.get_temporary_tab() as tab:
                inspect_tab(tab)

        result["tabs_tested"] = len(result["tabs"])
        result["matched_tabs"] = sum(1 for item in result["tabs"] if item.get("count", 0) > 0)
        result["inspected_count"] = len(result["elements"])
        result["truncated"] = result["count"] > len(result["elements"])
        result["success"] = result["count"] > 0
        if not result["tabs"]:
            result["message"] = (
                f"没有可测试的 {normalized_domain} 空闲页面"
                if normalized_domain else "没有可测试的页面"
            )
            return result
        if not result["success"]:
            result["message"] = "已在所有匹配页面测试，元素均未找到"
            return result

        result["top_candidates"] = _build_selector_top_candidates(result["elements"])
        per_tab_counts = [int(item.get("count", 0) or 0) for item in result["tabs"]]
        result["diagnosis"] = _build_selector_test_diagnosis(
            selector,
            max(per_tab_counts) if per_tab_counts else result["count"],
            result["top_candidates"],
        )
        if len(set(per_tab_counts)) > 1:
            result["diagnosis"].setdefault("warnings", []).append(
                "同一站点的不同页面命中数量不一致，请结合下方逐页结果确认页面状态。"
            )

        if result["elements"]:
            first = result["elements"][0]
            result["tag"] = first.get("tag", "unknown")
            result["text"] = first.get("text", "")
            result["attributes"] = first.get("attributes", {})

        return result

    except BrowserConnectionError as e:
        return {"success": False, "count": 0, "message": f"浏览器未连接: {e!s}"}

    except Exception as e:
        logger.error(f"测试选择器失败: {e}")
        return {"success": False, "count": 0, "message": str(e)}


@router.post("/api/debug/test-selector")
async def test_selector(
    request: Request,
    authenticated: bool = Depends(verify_auth)
):
    """测试选择器是否有效"""
    if not AppConfig.is_debug():
        raise HTTPException(status_code=403, detail="调试功能未启用")

    data = await _read_json_object_or_400(request)
    selector = data.get("selector", "")
    timeout = data.get("timeout", 2)
    highlight = data.get("highlight", False)
    route_domain = data.get("route_domain", "")

    if not selector:
        raise HTTPException(status_code=400, detail="缺少 selector")

    return await asyncio.to_thread(
        _run_selector_test,
        selector,
        timeout,
        highlight,
        route_domain,
    )


@router.get("/api/debug/request-status")
async def request_status(authenticated: bool = Depends(verify_auth)):
    """查看请求管理器状态"""
    if not AppConfig.DEBUG:
        raise HTTPException(status_code=403, detail="调试功能未启用")
    return runtime.request_manager.get_status()


@router.post("/api/debug/force-release")
async def force_release(authenticated: bool = Depends(verify_auth)):
    """强制释放锁"""
    if not AppConfig.DEBUG:
        raise HTTPException(status_code=403, detail="调试功能未启用")

    was_locked = runtime.request_manager.is_locked()
    cancelled_requests = runtime.request_manager.force_release()
    released_tabs = 0
    release_error = ""

    try:
        browser = runtime.get_browser(auto_connect=False)
        pool = getattr(browser, "tab_pool", None)
        if pool is not None and hasattr(pool, "force_release_all"):
            released_tabs = int(pool.force_release_all() or 0)
    except Exception as e:
        release_error = str(e)
        logger.warning(f"调试强制释放标签页失败: {e}")

    released = bool(cancelled_requests or released_tabs)
    is_now_locked = runtime.request_manager.is_locked()

    logger.warning(
        f"手动解锁: was={was_locked}, cancelled={cancelled_requests}, "
        f"released_tabs={released_tabs}, released={released}, now={is_now_locked}"
    )

    result = {
        "was_locked": was_locked,
        "released": released,
        "cancelled_requests": cancelled_requests,
        "released_tabs": released_tabs,
        "is_now_locked": is_now_locked
    }
    if release_error:
        result["release_error"] = release_error
    return result


@router.post("/api/debug/cancel-current")
async def cancel_current(
    tab_id: Optional[str] = None,
    authenticated: bool = Depends(verify_auth),
):
    """取消当前正在执行的请求"""
    if not AppConfig.DEBUG:
        raise HTTPException(status_code=403, detail="调试功能未启用")

    running_requests = runtime.request_manager.get_running_requests(tab_id=tab_id)
    if not running_requests:
        return {"cancelled": False, "message": "没有正在执行的请求", "tab_id": tab_id}

    if not tab_id and len(running_requests) > 1:
        raise HTTPException(
            status_code=400,
            detail="存在多个运行中的请求，请指定 tab_id",
        )

    request_ids = [ctx.request_id for ctx in running_requests]
    success = False
    for request_id in request_ids:
        success = runtime.request_manager.cancel_request(request_id, "manual_cancel") or success

    return {
        "cancelled": success,
        "request_id": request_ids[0],
        "request_ids": request_ids,
        "tab_id": tab_id or running_requests[0].tab_id,
    }


def _panel_memory_metric() -> str:
    """PANEL_MEMORY_METRIC: fast（默认）| uss | rss。"""
    value = str(_os.getenv("PANEL_MEMORY_METRIC", "fast") or "fast").strip().lower()
    return value if value in {"fast", "uss", "rss"} else "fast"


def _get_process_private_memory_bytes(proc: _psutil.Process) -> int:
    """估算进程私有内存，避免多进程浏览器按 RSS 累加后虚高。

    P0-7：默认不再调用 memory_full_info()（USS 需要读 smaps / 逐页扫描，
    对几十个 Chrome 进程每次采样开销很大），改用 memory_info() 里已有的廉价字段：
      - Windows：private（提交的私有内存）
      - Linux：rss - shared（statm，近似私有常驻内存）
      - 其他平台：rss
    PANEL_MEMORY_METRIC=uss 恢复旧的 USS 统计；=rss 直接用 RSS。
    """
    metric = _panel_memory_metric()
    if metric == "uss":
        try:
            full_info = proc.memory_full_info()
            for attr_name in ("uss", "private", "private_bytes"):
                value = getattr(full_info, attr_name, None)
                if isinstance(value, (int, float)) and value > 0:
                    return int(value)
        except (_psutil.NoSuchProcess, _psutil.AccessDenied, AttributeError):
            pass

    try:
        info = proc.memory_info()
    except (_psutil.NoSuchProcess, _psutil.AccessDenied, AttributeError):
        return 0
    rss = int(getattr(info, "rss", 0) or 0)
    if metric == "fast":
        private = getattr(info, "private", None)
        if isinstance(private, (int, float)) and private > 0:
            return int(private)
        shared = getattr(info, "shared", None)
        if isinstance(shared, (int, float)) and 0 <= shared < rss:
            return int(rss - shared)
    return rss


def _collect_process_tree_memory_bytes(proc: _psutil.Process, seen_pids: set[int]) -> int:
    """统计进程树内存，并避免重复累计已经见过的 PID。"""
    total_bytes = 0

    try:
        processes = [proc]
        processes.extend(proc.children(recursive=True))
    except (_psutil.NoSuchProcess, _psutil.AccessDenied):
        processes = [proc]

    for item in processes:
        try:
            pid = int(item.pid)
        except (_psutil.NoSuchProcess, _psutil.AccessDenied, AttributeError, TypeError, ValueError):
            continue

        if pid in seen_pids:
            continue

        seen_pids.add(pid)
        total_bytes += _get_process_private_memory_bytes(item)

    return total_bytes


def _extract_process_flag_value(cmdline: list[str], flag_name: str) -> str:
    """从命令行参数中提取浏览器 flag 的值，兼容 --flag value / --flag=value。"""
    normalized_flag = str(flag_name or "").strip()
    if not normalized_flag:
        return ""

    for index, raw_part in enumerate(cmdline or []):
        part = str(raw_part or "").strip().strip('"')
        if not part:
            continue

        if part == normalized_flag:
            if index + 1 < len(cmdline):
                return str(cmdline[index + 1] or "").strip().strip('"')
            return ""

        prefix = f"{normalized_flag}="
        if part.startswith(prefix):
            return part[len(prefix):].strip().strip('"')

    return ""


def _normalize_path_for_compare(path_value: Any) -> str:
    """路径比较前统一大小写和绝对路径，降低命令行写法差异的影响。"""
    raw_text = str(path_value or "").strip().strip('"')
    if not raw_text:
        return ""

    try:
        return str(Path(raw_text).expanduser().resolve()).casefold()
    except Exception:
        try:
            return str(Path(raw_text).expanduser().absolute()).casefold()
        except Exception:
            return raw_text.casefold()


def _is_chromium_family_process(process_name: str) -> bool:
    lowered = str(process_name or "").strip().lower()
    if not lowered:
        return False

    return any(
        token in lowered
        for token in ("chrome", "msedge", "brave", "vivaldi", "opera")
    )


def _cmdline_matches_browser_profile(cmdline: list[str], profile_root_norm: str) -> bool:
    if not profile_root_norm:
        return False

    user_data_dir = _extract_process_flag_value(cmdline, "--user-data-dir")
    if not user_data_dir:
        return False

    return _normalize_path_for_compare(user_data_dir) == profile_root_norm


def _find_project_browser_root_processes(
    main_proc: _psutil.Process,
    *,
    project_dir: Path,
    python_descendant_pids: set[int],
) -> list[_psutil.Process]:
    """
    只匹配当前项目启动的调试浏览器主进程。
    必须同时命中项目 profile 目录和调试端口，避免把普通 Chrome 窗口算进来。
    """
    profile_root = _resolve_browser_profile_root(project_dir)
    profile_root_norm = _normalize_path_for_compare(profile_root)
    debug_port = str(AppConfig.get_browser_port() or "").strip()
    matched_processes: list[_psutil.Process] = []

    for proc in _psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            pid = int(proc.pid)
            if pid == main_proc.pid or pid in python_descendant_pids:
                continue

            proc_name = proc.info.get('name') or ""
            if not _is_chromium_family_process(proc_name):
                continue

            cmdline = proc.info.get('cmdline') or []
            if not cmdline:
                continue

            process_type = _extract_process_flag_value(cmdline, "--type")
            if process_type:
                continue

            proc_debug_port = _extract_process_flag_value(cmdline, "--remote-debugging-port")
            if not (
                debug_port
                and proc_debug_port == debug_port
            ):
                continue

            if not _cmdline_matches_browser_profile(cmdline, profile_root_norm):
                continue

            matched_processes.append(proc)
        except (_psutil.NoSuchProcess, _psutil.AccessDenied, ValueError, TypeError):
            continue

    return matched_processes


def _collect_project_process_snapshot() -> Dict[str, Any]:
    """收集一次项目进程快照，供 CPU/内存统计共用。"""
    try:
        project_dir = Path(__file__).resolve().parents[2]
        main_pid = _os.getpid()
        main_proc = _psutil.Process(main_pid)
        try:
            python_descendants = main_proc.children(recursive=True)
        except Exception:
            python_descendants = []
        python_descendant_pids = {
            int(child.pid)
            for child in python_descendants
            if getattr(child, "pid", None) is not None
        }
        browser_roots = _find_project_browser_root_processes(
            main_proc,
            project_dir=project_dir,
            python_descendant_pids=python_descendant_pids,
        )
        tracked_processes = [main_proc]
        tracked_processes.extend(python_descendants)
        tracked_pids = {main_pid}
        tracked_pids.update(python_descendant_pids)
        for browser_proc in browser_roots:
            try:
                browser_pid = int(browser_proc.pid)
            except Exception:
                browser_pid = None
            if browser_pid is not None:
                tracked_processes.append(browser_proc)
                tracked_pids.add(browser_pid)
            try:
                browser_descendants = browser_proc.children(recursive=True)
            except Exception:
                browser_descendants = []
            for child in browser_descendants:
                try:
                    child_pid = int(child.pid)
                except Exception:
                    continue
                tracked_processes.append(child)
                tracked_pids.add(child_pid)
        return {
            "project_dir": project_dir,
            "main_pid": main_pid,
            "main_proc": main_proc,
            "python_descendants": python_descendants,
            "python_descendant_pids": python_descendant_pids,
            "browser_roots": browser_roots,
            "tracked_processes": tracked_processes,
            "tracked_pids": tracked_pids,
        }
    except Exception:
        return {
            "project_dir": Path(__file__).resolve().parents[2],
            "main_pid": _os.getpid(),
            "main_proc": None,
            "python_descendants": [],
            "python_descendant_pids": set(),
            "browser_roots": [],
            "tracked_processes": [],
            "tracked_pids": set(),
        }


def _get_project_memory_mb(process_snapshot: Optional[Dict[str, Any]] = None) -> float:
    """估算本项目相关进程的私有内存，尽量避免浏览器多进程重复计数。"""
    try:
        snapshot = process_snapshot or _get_project_process_snapshot_cached()
        main_proc = snapshot.get("main_proc")
        if main_proc is None:
            return 0.0

        seen_pids: set[int] = set()
        total_bytes = 0
        if "tracked_processes" in snapshot:
            for proc in snapshot.get("tracked_processes") or []:
                try:
                    pid = int(proc.pid)
                except Exception:
                    continue
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                total_bytes += _get_process_private_memory_bytes(proc)
        else:
            total_bytes = _collect_process_tree_memory_bytes(main_proc, seen_pids)
            for browser_proc in snapshot.get("browser_roots") or []:
                total_bytes += _collect_process_tree_memory_bytes(browser_proc, seen_pids)

        return round(total_bytes / (1024 * 1024), 1)
    except Exception:
        return 0.0


def _get_project_process_snapshot_cached(ttl_seconds: float = _PROJECT_PROCESS_SNAPSHOT_TTL_SECONDS) -> Dict[str, Any]:
    """复用项目进程树快照，避免频繁枚举系统进程。"""
    now = time.monotonic()
    cached_snapshot = _PROJECT_PROCESS_SNAPSHOT_CACHE.get("snapshot")
    if cached_snapshot is not None and now < float(_PROJECT_PROCESS_SNAPSHOT_CACHE.get("expires_at", 0.0) or 0.0):
        return cached_snapshot

    with _PROJECT_PROCESS_SNAPSHOT_CACHE_LOCK:
        now = time.monotonic()
        cached_snapshot = _PROJECT_PROCESS_SNAPSHOT_CACHE.get("snapshot")
        if cached_snapshot is not None and now < float(_PROJECT_PROCESS_SNAPSHOT_CACHE.get("expires_at", 0.0) or 0.0):
            return cached_snapshot

        snapshot = _collect_project_process_snapshot()
        ttl = max(2.0, float(ttl_seconds or _PROJECT_PROCESS_SNAPSHOT_TTL_SECONDS))
        _PROJECT_PROCESS_SNAPSHOT_CACHE["snapshot"] = snapshot
        _PROJECT_PROCESS_SNAPSHOT_CACHE["expires_at"] = time.monotonic() + ttl
        return snapshot


def _get_project_memory_mb_cached(
    process_snapshot: Optional[Dict[str, Any]] = None,
    ttl_seconds: float = _PROJECT_MEMORY_CACHE_TTL_SECONDS,
) -> float:
    """缓存项目内存采样，减少对多进程浏览器的频繁内存查询。"""
    now = time.monotonic()
    if now < float(_PROJECT_MEMORY_CACHE.get("expires_at", 0.0) or 0.0):
        return float(_PROJECT_MEMORY_CACHE.get("memory_mb", 0.0) or 0.0)

    with _PROJECT_MEMORY_CACHE_LOCK:
        now = time.monotonic()
        if now < float(_PROJECT_MEMORY_CACHE.get("expires_at", 0.0) or 0.0):
            return float(_PROJECT_MEMORY_CACHE.get("memory_mb", 0.0) or 0.0)

        snapshot = process_snapshot or _get_project_process_snapshot_cached()
        memory_mb = float(_get_project_memory_mb(snapshot) or 0.0)
        ttl = max(2.0, float(ttl_seconds or _PROJECT_MEMORY_CACHE_TTL_SECONDS))
        _PROJECT_MEMORY_CACHE["memory_mb"] = memory_mb
        _PROJECT_MEMORY_CACHE["expires_at"] = time.monotonic() + ttl
        return memory_mb


def _get_project_cpu_percent(process_snapshot: Optional[Dict[str, Any]] = None) -> float:
    """估算本项目（包含主 Python 进程及其下所有 Chrome 调试浏览器进程）的总 CPU 占比，除以核心数折算为 0-100% 格式。"""
    global _PROJECT_PROCESS_CACHE

    try:
        snapshot = process_snapshot or _collect_project_process_snapshot()
        main_pid = int(snapshot.get("main_pid") or _os.getpid())
        main_proc = snapshot.get("main_proc")
        if main_proc is None:
            return 0.0

        with _PROJECT_PROCESS_CACHE_LOCK:
            if main_pid not in _PROJECT_PROCESS_CACHE:
                _PROJECT_PROCESS_CACHE[main_pid] = main_proc

            tracked_by_pid = {}

            def remember_snapshot_process(proc: Any) -> Optional[int]:
                try:
                    pid = int(proc.pid)
                except Exception:
                    return None
                tracked_by_pid[pid] = proc
                return pid

            remember_snapshot_process(main_proc)
            for proc in snapshot.get("tracked_processes") or []:
                remember_snapshot_process(proc)

            # 收集所有相关的当前活跃 PID。新快照阶段已遍历浏览器子进程，避免 CPU/内存路径重复 children()。
            if "tracked_pids" in snapshot:
                target_pids = set(snapshot.get("tracked_pids") or set())
                target_pids.add(main_pid)
                target_pids.update(tracked_by_pid.keys())
            else:
                target_pids = set()
                remembered_pid = remember_snapshot_process(main_proc)
                if remembered_pid is not None:
                    target_pids.add(remembered_pid)
                for proc in snapshot.get("python_descendants") or []:
                    remembered_pid = remember_snapshot_process(proc)
                    if remembered_pid is not None:
                        target_pids.add(remembered_pid)
                target_pids.update(snapshot.get("python_descendant_pids") or set())
                for b_proc in snapshot.get("browser_roots") or []:
                    try:
                        remembered_pid = remember_snapshot_process(b_proc)
                        if remembered_pid is not None:
                            target_pids.add(remembered_pid)
                        for child in b_proc.children(recursive=True):
                            if getattr(child, "pid", None) is not None:
                                remembered_pid = remember_snapshot_process(child)
                                if remembered_pid is not None:
                                    target_pids.add(remembered_pid)
                    except Exception:
                        pass
                if not target_pids:
                    target_pids = {main_pid}

            # 更新/清理 Process 缓存，只保留活跃的进程以防止内存泄露
            new_cache = {}
            for pid in target_pids:
                if pid in tracked_by_pid:
                    new_cache[pid] = tracked_by_pid[pid]
                elif pid in _PROJECT_PROCESS_CACHE:
                    new_cache[pid] = _PROJECT_PROCESS_CACHE[pid]
                else:
                    try:
                        new_cache[pid] = _psutil.Process(pid)
                    except Exception:
                        pass
            _PROJECT_PROCESS_CACHE = new_cache

            # 复制一份用于在锁外进行计算，防阻塞
            processes_to_measure = list(_PROJECT_PROCESS_CACHE.items())

        # 在锁外调用 cpu_percent 以提升并发性能并减少锁定时间
        seen_pids = set()
        total_cpu = 0.0
        for pid, proc in processes_to_measure:
            try:
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                total_cpu += proc.cpu_percent(interval=None)
            except Exception:
                pass

        cpu_count = _psutil.cpu_count() or 1
        return round(total_cpu / cpu_count, 1)
    except Exception as e:
        logger.error(f"[CPU_DEBUG] 计算 CPU 失败: {e}")
        return 0.0


def _resolve_browser_profile_root(project_dir: Path) -> Optional[Path]:
    """解析浏览器用户目录根路径。留空时默认使用项目内 chrome_profile。"""
    try:
        env_config = _load_env_config_from_file()
        raw_value = str(
            env_config.get("BROWSER_PROFILE_DIR")
            or os.getenv("BROWSER_PROFILE_DIR", "")
            or ""
        ).strip()
        if not raw_value:
            return project_dir / "chrome_profile"

        profile_dir = Path(raw_value)
        if not profile_dir.is_absolute():
            profile_dir = project_dir / profile_dir
        return profile_dir
    except Exception:
        return project_dir / "chrome_profile"


def _get_path_size_bytes(path: Path, *, skip_dir_names: Optional[set[str]] = None) -> int:
    """递归统计路径大小。"""
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path

    if not resolved.exists():
        return 0

    if resolved.is_file():
        try:
            return resolved.stat().st_size
        except OSError:
            return 0

    total_size = 0
    skip_names = set(skip_dir_names or set())

    for dirpath, dirnames, filenames in os.walk(resolved):
        if skip_names:
            dirnames[:] = [d for d in dirnames if d not in skip_names]
        for filename in filenames:
            file_path = Path(dirpath) / filename
            try:
                total_size += file_path.stat().st_size
            except OSError:
                pass

    return total_size


def _get_project_disk_usage_mb() -> float:
    """计算项目实际占用磁盘空间，包含 .venv/venv 和浏览器用户目录。"""
    try:
        project_dir = Path(__file__).resolve().parents[2]
        total_size = _get_path_size_bytes(
            project_dir,
            skip_dir_names={".git", "__pycache__", "node_modules"},
        )

        profile_dir = _resolve_browser_profile_root(project_dir)
        if profile_dir is not None:
            try:
                project_resolved = project_dir.resolve()
                profile_resolved = profile_dir.resolve()
            except Exception:
                project_resolved = project_dir
                profile_resolved = profile_dir

            # 外部浏览器用户目录不在项目内时，额外计入一次。
            if profile_resolved != project_resolved and project_resolved not in profile_resolved.parents:
                total_size += _get_path_size_bytes(profile_resolved)

        return round(total_size / (1024 * 1024), 1)
    except Exception:
        return 0.0


def _reset_system_stats_cache_for_tests() -> None:
    """清理系统统计缓存，供回归测试隔离并避免后台刷新状态串扰。"""
    global _PROJECT_PROCESS_CACHE, _DISK_USAGE_REFRESH_WORKER
    with _SYSTEM_STATS_CACHE_LOCK:
        _SYSTEM_STATS_CACHE["expires_at"] = 0.0
        _SYSTEM_STATS_CACHE["payload"] = {
            "memory_mb": 0.0,
            "disk_status": "0 MB",
            "total_requests": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "cpu_percent": 0.0,
            "project_cpu": 0.0,
            "memory_percent": 0.0,
            "project_memory_percent": 0.0,
            # 与实际构建的 payload 保持同一形状，避免消费方缺键
            "running_count": 0,
            "queued_count": 0,
        }

    with _PROJECT_PROCESS_CACHE_LOCK:
        _PROJECT_PROCESS_CACHE = {}

    with _PROJECT_PROCESS_SNAPSHOT_CACHE_LOCK:
        _PROJECT_PROCESS_SNAPSHOT_CACHE["expires_at"] = 0.0
        _PROJECT_PROCESS_SNAPSHOT_CACHE["snapshot"] = None

    with _PROJECT_MEMORY_CACHE_LOCK:
        _PROJECT_MEMORY_CACHE["expires_at"] = 0.0
        _PROJECT_MEMORY_CACHE["memory_mb"] = 0.0

    _DISK_USAGE_CACHE["expires_at"] = 0.0
    _DISK_USAGE_CACHE["value"] = 0.0
    _DISK_USAGE_CACHE["last_success_value"] = 0.0

    with _DISK_USAGE_REFRESH_LOCK:
        _DISK_USAGE_REFRESH_WORKER = None


def _format_disk_usage(disk_mb: float) -> str:
    if disk_mb >= 1024:
        return f"{round(disk_mb / 1024, 2)} GB"
    return f"{disk_mb} MB"


def _set_disk_usage_cache_result(disk_mb: float, ttl_seconds: float) -> float:
    now = time.monotonic()
    ttl = max(1.0, float(ttl_seconds or 60.0))
    if disk_mb > 0:
        _DISK_USAGE_CACHE["value"] = disk_mb
        _DISK_USAGE_CACHE["last_success_value"] = disk_mb
        _DISK_USAGE_CACHE["expires_at"] = now + ttl
        return disk_mb

    fallback_value = float(
        _DISK_USAGE_CACHE.get("last_success_value")
        or _DISK_USAGE_CACHE.get("value", 0.0)
        or 0.0
    )
    if fallback_value > 0:
        _DISK_USAGE_CACHE["value"] = fallback_value
        _DISK_USAGE_CACHE["expires_at"] = now + min(ttl, 5.0)
        return fallback_value

    _DISK_USAGE_CACHE["value"] = 0.0
    _DISK_USAGE_CACHE["expires_at"] = now + min(ttl, 5.0)
    return 0.0


def _refresh_project_disk_usage_cache(ttl_seconds: float = 60.0) -> float:
    try:
        disk_mb = float(_get_project_disk_usage_mb() or 0.0)
    except Exception:
        disk_mb = 0.0
    return _set_disk_usage_cache_result(disk_mb, ttl_seconds)


def _run_disk_usage_refresh_worker(ttl_seconds: float) -> None:
    global _DISK_USAGE_REFRESH_WORKER
    current_worker = _threading.current_thread()
    try:
        try:
            _refresh_project_disk_usage_cache(ttl_seconds)
        except Exception as exc:
            try:
                logger.warning(f"磁盘用量后台刷新失败: {exc}")
            except Exception:
                pass
    finally:
        with _DISK_USAGE_REFRESH_LOCK:
            if _DISK_USAGE_REFRESH_WORKER is current_worker:
                _DISK_USAGE_REFRESH_WORKER = None


def _schedule_disk_usage_refresh(ttl_seconds: float = 60.0) -> None:
    global _DISK_USAGE_REFRESH_WORKER
    with _DISK_USAGE_REFRESH_LOCK:
        if _DISK_USAGE_REFRESH_WORKER and _DISK_USAGE_REFRESH_WORKER.is_alive():
            return
        worker = _threading.Thread(
            target=_run_disk_usage_refresh_worker,
            args=(ttl_seconds,),
            daemon=True,
            name="disk-usage-refresh",
        )
        _DISK_USAGE_REFRESH_WORKER = worker
    worker.start()


def _get_project_disk_usage_mb_cached(ttl_seconds: float = 60.0) -> float:
    now = time.monotonic()
    cached_value = float(_DISK_USAGE_CACHE.get("value", 0.0) or 0.0)
    if now < float(_DISK_USAGE_CACHE.get("expires_at", 0.0) or 0.0):
        return cached_value

    ttl = max(1.0, float(ttl_seconds or 60.0))
    fallback_value = float(
        _DISK_USAGE_CACHE.get("last_success_value")
        or cached_value
        or 0.0
    )
    if fallback_value > 0:
        _DISK_USAGE_CACHE["value"] = fallback_value
        _DISK_USAGE_CACHE["expires_at"] = now + min(ttl, 5.0)
        _schedule_disk_usage_refresh(ttl)
        return fallback_value

    return _refresh_project_disk_usage_cache(ttl)


def _get_live_request_counts() -> Dict[str, int]:
    """实时请求计数（运行中 / 排队中）。stats 是高频轮询接口，取值失败一律回落 0，不能因此整体 500。"""
    try:
        status = runtime.request_manager.get_status() or {}
        status_counts = status.get("status_counts") or {}
        return {
            "running_count": int(status.get("running_count", 0) or 0),
            "queued_count": int(status_counts.get("queued", 0) or 0),
        }
    except Exception:
        return {"running_count": 0, "queued_count": 0}


def _get_fresh_system_stats_payload() -> Optional[Dict[str, Any]]:
    now = time.monotonic()
    cached_payload = _SYSTEM_STATS_CACHE.get("payload") or {}
    if now < float(_SYSTEM_STATS_CACHE.get("expires_at", 0.0) or 0.0):
        payload = dict(cached_payload)
        # 进程/内存采样缓存 5s；请求计数很便宜，保持实时
        payload.update(_get_live_request_counts())
        payload["total_requests"] = int(getattr(runtime.request_manager, "total_requests", 0) or 0)
        payload["total_input_tokens"] = int(getattr(runtime.request_manager, "total_input_tokens", 0) or 0)
        payload["total_output_tokens"] = int(getattr(runtime.request_manager, "total_output_tokens", 0) or 0)
        return payload
    return None


def _get_system_stats_payload_cached(ttl_seconds: float = 5.0) -> Dict[str, Any]:
    fresh_payload = _get_fresh_system_stats_payload()
    if fresh_payload is not None:
        return fresh_payload

    with _SYSTEM_STATS_CACHE_LOCK:
        now = time.monotonic()
        cached_payload = _SYSTEM_STATS_CACHE.get("payload") or {}
        if now < float(_SYSTEM_STATS_CACHE.get("expires_at", 0.0) or 0.0):
            return dict(cached_payload)

        cpu_percent = 0.0
        project_cpu = 0.0
        memory_percent = 0.0
        project_memory_percent = 0.0
        process_snapshot = _get_project_process_snapshot_cached()
        try:
            cpu_percent = float(_psutil.cpu_percent(interval=None) or 0.0)
            virtual_memory = _psutil.virtual_memory()
            memory_percent = float(virtual_memory.percent or 0.0)
            project_cpu = float(_get_project_cpu_percent(process_snapshot) or 0.0)
        except Exception:
            virtual_memory = None

        project_memory_mb = _get_project_memory_mb_cached(process_snapshot)
        try:
            total_memory_bytes = int(getattr(virtual_memory, "total", 0) or 0)
            if total_memory_bytes > 0:
                project_memory_percent = round((project_memory_mb * 1024 * 1024 / total_memory_bytes) * 100, 1)
        except Exception:
            project_memory_percent = 0.0

        # 追加实时请求计数：request-history 只落终态记录，无法反映“正在执行”
        live_counts = _get_live_request_counts()

        payload = {
            "memory_mb": project_memory_mb,
            "disk_status": _format_disk_usage(_get_project_disk_usage_mb_cached()),
            "total_requests": int(getattr(runtime.request_manager, "total_requests", 0) or 0),
            "total_input_tokens": int(getattr(runtime.request_manager, "total_input_tokens", 0) or 0),
            "total_output_tokens": int(getattr(runtime.request_manager, "total_output_tokens", 0) or 0),
            "cpu_percent": cpu_percent,
            "project_cpu": project_cpu,
            "memory_percent": memory_percent,
            "project_memory_percent": project_memory_percent,
            "running_count": live_counts["running_count"],
            "queued_count": live_counts["queued_count"],
        }
        _SYSTEM_STATS_CACHE["payload"] = payload
        _SYSTEM_STATS_CACHE["expires_at"] = now + max(0.8, float(ttl_seconds or 5.0))
        return dict(payload)


@router.get("/api/system/stats")
async def get_system_stats(authenticated: bool = Depends(verify_auth)):
    cached_payload = _get_fresh_system_stats_payload()
    if cached_payload is not None:
        return cached_payload
    return await asyncio.to_thread(_get_system_stats_payload_cached, 1.0)


@router.get("/api/system/request-history")
async def get_request_history(
    limit: int = 200,
    detail: bool = False,
    if_revision: Optional[str] = None,
    authenticated: bool = Depends(verify_auth),
):
    # RequestManager applies the configured REQUEST_MONITOR_MAX_RECORDS boundary.
    # Do not impose a second 200-record ceiling at the API layer.
    safe_limit = max(1, int(limit or 200))
    return await asyncio.to_thread(
        runtime.request_manager.get_request_history_payload,
        safe_limit,
        bool(detail),
        if_revision,
    )


@router.get("/api/system/request-history/{request_id}")
async def get_request_history_detail(
    request_id: str,
    authenticated: bool = Depends(verify_auth),
):
    record = await asyncio.to_thread(runtime.request_manager.get_request_history_record, request_id)
    if not record:
        raise HTTPException(status_code=404, detail="请求历史不存在")
    return record
