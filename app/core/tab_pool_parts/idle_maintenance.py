"""
app/core/tab_pool_parts/idle_maintenance.py - 空闲标签页维护（P0-3 / P0-6）

P0-3  CDP 会话回收（recycle）
    DrissionPage 执行 run_js / 元素查找时产生的 RemoteObject、DOM agent 的
    nodeId 映射都挂在 CDP 会话上，只有会话断开才会整体释放；它们会把早已从
    页面移除的旧对话 DOM 钉在渲染进程里。空闲标签页满足触发条件时，断开并
    重新建立该标签页的 CDP 连接（不刷新页面、不影响登录态），释放这些引用。

    顺序：maint 占用会话 → 停止全局网络监听 → tab.disconnect() →
          tab._driver_init(target_id) → tab._get_document() →
          重置 listener 驱动状态 / 会话级注入脚本标记 → 释放会话（释放流程会恢复
          可见性模拟状态，全局监听随后自动重启）。

    触发（任一满足，且自上次回收后标签页确有使用）：
      - 自上次回收后已处理 BROWSER_CDP_RECYCLE_AFTER_REQUESTS 个请求（默认 20）
      - 距上次回收超过 BROWSER_CDP_RECYCLE_INTERVAL_SEC（默认 1800s）
      - Memory.getDOMCounters 节点数 ≥ BROWSER_CDP_RECYCLE_DOM_NODES（默认 150000）

P0-6  空闲冻结（freeze）
    空闲 ≥ BROWSER_IDLE_FREEZE_AFTER_SEC（默认 60s）、且真实 visibilityState 为
    hidden 的标签页执行 Page.setWebLifecycleState=frozen（计时器 / rAF / 页面任务
    全部暂停）。被占用（acquire / acquire_for_command）或 page_check 唤醒时
    先恢复为 active。可见（用户正在看）的标签页永远不会被冻结。

环境变量：
    BROWSER_IDLE_FREEZE_ENABLED           默认 true
    BROWSER_IDLE_FREEZE_AFTER_SEC         默认 60（最小 15）
    BROWSER_IDLE_FREEZE_REQUIRE_HIDDEN    默认 true（仅冻结后台/被遮挡的标签页）
    BROWSER_CDP_RECYCLE_ENABLED           默认 true
    BROWSER_CDP_RECYCLE_IDLE_SEC          默认 30（最小 10）
    BROWSER_CDP_RECYCLE_AFTER_REQUESTS    默认 20（0 = 不按请求数触发）
    BROWSER_CDP_RECYCLE_INTERVAL_SEC      默认 1800（0 = 不按时间触发）
    BROWSER_CDP_RECYCLE_DOM_NODES         默认 150000（0 = 不按节点数触发）
    BROWSER_CDP_RECYCLE_DOM_CHECK_SEC     默认 300
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import logger
from app.core.driver import browser_driver, driver_for_tab


# 真实可见性：可见性模拟在 document 实例上定义了 own property，
# 这里直接调用 Document.prototype 上的原生 getter，得到浏览器真实状态。
REAL_VISIBILITY_JS = (
    "return (function(){try{"
    "var d=Object.getOwnPropertyDescriptor(Document.prototype,'visibilityState');"
    "return String(d&&d.get?d.get.call(document):document.visibilityState);"
    "}catch(e){return 'unknown';}})();"
)

_LOCK_FACTORY_GUARD = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    try:
        value = float(str(raw).strip()) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        value = float(default)
    if not math.isfinite(value):
        # B6：inf / nan 不是有效配置（int(inf) 会 OverflowError，nan 让所有比较为假）。
        # 想要「禁用」请写 0。
        logger.warning(f"[IdleMaintenance] {name}={raw!r} 不是有限数值，使用默认值 {default}（禁用请设为 0）")
        value = float(default)
    if value <= 0:
        return 0.0
    return max(minimum, value)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    return int(_env_float(name, float(default), float(minimum)))


@dataclass
class IdleMaintenanceConfig:
    freeze_enabled: bool = True
    freeze_after_sec: float = 60.0
    freeze_require_hidden: bool = True
    recycle_enabled: bool = True
    recycle_idle_sec: float = 30.0
    recycle_after_requests: int = 20
    recycle_interval_sec: float = 1800.0
    recycle_dom_nodes: int = 150000
    dom_check_interval_sec: float = 300.0

    @classmethod
    def from_env(cls) -> "IdleMaintenanceConfig":
        return cls(
            freeze_enabled=_env_bool("BROWSER_IDLE_FREEZE_ENABLED", True),
            freeze_after_sec=_env_float("BROWSER_IDLE_FREEZE_AFTER_SEC", 60.0, 15.0) or 60.0,
            freeze_require_hidden=_env_bool("BROWSER_IDLE_FREEZE_REQUIRE_HIDDEN", True),
            recycle_enabled=_env_bool("BROWSER_CDP_RECYCLE_ENABLED", True),
            recycle_idle_sec=_env_float("BROWSER_CDP_RECYCLE_IDLE_SEC", 30.0, 10.0) or 30.0,
            recycle_after_requests=_env_int("BROWSER_CDP_RECYCLE_AFTER_REQUESTS", 20, 1),
            recycle_interval_sec=_env_float("BROWSER_CDP_RECYCLE_INTERVAL_SEC", 1800.0, 60.0),
            recycle_dom_nodes=_env_int("BROWSER_CDP_RECYCLE_DOM_NODES", 150000, 1000),
            dom_check_interval_sec=_env_float("BROWSER_CDP_RECYCLE_DOM_CHECK_SEC", 300.0, 30.0) or 300.0,
        )


def is_idle_freeze_enabled() -> bool:
    return _env_bool("BROWSER_IDLE_FREEZE_ENABLED", True)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _run_cdp(tab: Any, method: str, *, timeout: float = 2.0, **params):
    runner = getattr(tab, "run_cdp", None)
    if not callable(runner):
        runner = getattr(tab, "_run_cdp", None)
    if not callable(runner):
        raise AttributeError(f"CDP runner unavailable for {method}")
    try:
        return runner(method, _timeout=max(0.1, float(timeout)), **params)
    except TypeError as exc:
        if "timeout" not in str(exc).lower():
            raise
        return runner(method, **params)


def lifecycle_lock(session: Any) -> threading.Lock:
    lock = getattr(session, "_uwapi_lifecycle_lock", None)
    if lock is None:
        with _LOCK_FACTORY_GUARD:
            lock = getattr(session, "_uwapi_lifecycle_lock", None)
            if lock is None:
                lock = threading.Lock()
                setattr(session, "_uwapi_lifecycle_lock", lock)
    return lock


def _status_name(session: Any) -> str:
    status = getattr(session, "status", None)
    return str(getattr(status, "value", status) or "").lower()


def _is_idle(session: Any) -> bool:
    return _status_name(session) == "idle" and not bool(getattr(session, "_termination_in_progress", False))


def last_activity_at(session: Any) -> float:
    """Most recent sign of life: request/command use, page_check wake, network event."""
    values = [
        getattr(session, "last_used_at", 0.0),
        getattr(session, "_pc_last_wake_at", 0.0),
        getattr(session, "_uwapi_last_network_event_at", 0.0),
        getattr(session, "_uwapi_last_resumed_at", 0.0),
    ]
    best = 0.0
    for value in values:
        try:
            best = max(best, float(value or 0.0))
        except (TypeError, ValueError):
            continue
    return best


def note_network_activity(session: Any) -> None:
    try:
        setattr(session, "_uwapi_last_network_event_at", time.time())
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# freeze / resume (P0-6)
# --------------------------------------------------------------------------- #
#
# 注意：DrissionPage 在 _driver_init 中对每个标签页调用
# Emulation.setFocusEmulationEnabled(true)。Chromium 用“增加 capturer 计数”
# 实现该仿真，结果是被控制的标签页永远被视为 visible —— 后台标签页既不会被
# 浏览器降频，Page.setWebLifecycleState=frozen 也会被静默忽略（实测：冻结后
# 计时器照常运行）。因此冻结前必须先在本会话上关闭焦点仿真，读取真实可见性；
# 恢复时重新打开。


def _set_focus_emulation(tab: Any, enabled: bool) -> bool:
    try:
        _run_cdp(tab, "Emulation.setFocusEmulationEnabled", enabled=bool(enabled), timeout=1.5)
        return True
    except Exception:
        return False


def _read_real_visibility(tab: Any) -> str:
    try:
        return str(driver_for_tab(tab).run_js(REAL_VISIBILITY_JS, timeout=1.0) or "").strip().lower()
    except Exception:
        return "unknown"


def _wait_real_hidden(tab: Any, timeout_sec: float = 0.6, should_abort=None) -> str:
    """关闭焦点仿真后，可见性变化是异步传到渲染进程的，短暂轮询。"""
    deadline = time.time() + max(0.0, timeout_sec)
    state = _read_real_visibility(tab)
    while state != "hidden" and time.time() < deadline:
        if should_abort is not None and should_abort():
            return "aborted"
        time.sleep(0.05)
        state = _read_real_visibility(tab)
    return state


# 冻结核验：CDP 冻结可能被 Chrome 静默忽略或在我们背后解除（发起冻结的 DevTools 会话断开、
# 用户切回标签页、与其它会话附加并发等，均已实测）。冻结前在页面挂一次性的标准生命周期事件
# freeze / resume 监听（不可枚举属性，不影响页面逻辑），用同步 run_js 读取——冻结的页面也能执行。
_FREEZE_MARK_INSTALL_JS = (
    "return (function(t){try{var st={t:t,f:0,r:0};"
    "Object.defineProperty(window,'__uwapiFz',{value:st,configurable:true,enumerable:false,writable:true});"
    "document.addEventListener('freeze',function(){st.f=Date.now();},{once:true});"
    "document.addEventListener('resume',function(){st.r=Date.now();},{once:true});"
    "return 1;}catch(e){return 0;}})(arguments[0]);"
)
_FREEZE_MARK_READ_JS = (
    "return (function(){try{var s=window.__uwapiFz;"
    "return s?JSON.stringify({t:s.t,f:s.f,r:s.r}):'';}catch(e){return '';}})();"
)


def _read_freeze_mark(tab: Any) -> Optional[dict]:
    """``None`` = could not read; ``{}`` = marker missing (document replaced)."""
    try:
        raw = driver_for_tab(tab).run_js(_FREEZE_MARK_READ_JS, timeout=1.0)
    except Exception:
        return None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _freeze_took_effect(tab: Any, token: str, timeout_sec: float = 0.4, should_abort=None) -> bool:
    deadline = time.time() + max(0.0, timeout_sec)
    while True:
        mark = _read_freeze_mark(tab)
        if mark is None:
            return True  # 读不到（非真实页面对象等）时不否定冻结，交给后续巡检
        if mark.get("t") == token and mark.get("f") and not mark.get("r"):
            return True
        if should_abort is not None and should_abort():
            return True  # 已被占用：不再核验，按已冻结登记，由等锁的占用方立刻解冻
        if time.time() >= deadline:
            return False
        time.sleep(0.03)


def freeze_was_lifted(session: Any) -> bool:
    """True when the page is known to be running again although we think it is frozen."""
    tab = getattr(session, "tab", None)
    token = str(getattr(session, "_uwapi_freeze_token", "") or "")
    if tab is None or not token:
        return False
    mark = _read_freeze_mark(tab)
    if mark is None:
        return False
    return mark.get("t") != token or bool(mark.get("r"))


def _front_tab_hint(tab: Any) -> str:
    """Guess whether the user can see ``tab`` WITHOUT touching focus emulation.

    Toggling focus emulation off/on (the authoritative probe) fires ``blur`` /
    ``focus`` on a tab the user is looking at, and sites with refetch-on-focus
    react to that. So first use browser-side facts that have no page-visible
    side effects:

    * ``"minimized"``  – the tab's window is minimized (hidden for sure)
    * ``"front"``      – the tab is the most recently activated page of its
      (non-minimized) window, i.e. the tab currently shown there. The DevTools
      ``/json`` list is MRU-ordered (DrissionPage's ``latest_tab`` relies on it).
    * ``"background"`` – another page is in front of it in the same window
    * ``"unknown"``    – cannot tell (ws-only mode, errors, …) → caller probes
    """
    try:
        info = _run_cdp(tab, "Browser.getWindowForTarget", timeout=1.0) or {}
        window_id = info.get("windowId")
        window_state = str((info.get("bounds") or {}).get("windowState") or "").lower()
    except Exception:
        return "unknown"
    if window_state == "minimized":
        return "minimized"
    browser = getattr(tab, "browser", None)
    my_id = str(getattr(tab, "tab_id", "") or "")
    if browser is None or not my_id or window_id is None or getattr(browser, "_ws_only", False):
        return "unknown"
    try:
        mru_ids = list(browser.tab_ids)
    except Exception:
        return "unknown"
    for target_id in mru_ids[:32]:
        if str(target_id) == my_id:
            return "front"
        try:
            other = browser_driver(browser).run_cdp("Browser.getWindowForTarget", targetId=target_id) or {}
        except Exception:
            continue
        if other.get("windowId") == window_id:
            return "background"
    return "unknown"


_VISIBLE_BACKOFF_MAX_SEC = 900.0


def _schedule_visible_retry(session: Any, config: "IdleMaintenanceConfig", *, probed: bool) -> None:
    """Back off after finding the tab visible.

    A side-effect-free ``front`` hint retries at the normal cadence; an actual
    probe (which toggles focus emulation) backs off exponentially up to 15 min
    so a tab the user keeps looking at sees at most a handful of blur/focus
    pairs. Any real activity resets the streak (see schedule_idle_maintenance).
    """
    base = max(30.0, float(config.freeze_after_sec))
    if not probed:
        setattr(session, "_uwapi_freeze_retry_at", time.time() + base)
        return
    activity = last_activity_at(session)
    streak = int(getattr(session, "_uwapi_visible_probe_streak", 0) or 0)
    if activity > float(getattr(session, "_uwapi_visible_probe_mark", 0.0) or 0.0):
        streak = 0
    streak += 1
    setattr(session, "_uwapi_visible_probe_streak", streak)
    setattr(session, "_uwapi_visible_probe_mark", activity)
    delay = min(_VISIBLE_BACKOFF_MAX_SEC, base * (2 ** (streak - 1)))
    setattr(session, "_uwapi_freeze_retry_at", time.time() + delay)


def _restore_focus_emulation(session: Any, tab: Any) -> None:
    if getattr(session, "_uwapi_focus_emulation_off", False):
        _set_focus_emulation(tab, True)
        setattr(session, "_uwapi_focus_emulation_off", False)


def resume_if_frozen(session: Any, reason: str = "") -> bool:
    """Set a frozen tab back to ``active``. Cheap no-op when not frozen.

    Always goes through the lifecycle lock so a concurrent freeze cannot land
    after the session has been acquired.
    """
    if session is None:
        return False
    if (
        not getattr(session, "_uwapi_frozen", False)
        and not getattr(session, "_uwapi_freeze_in_progress", False)
        and not getattr(session, "_uwapi_focus_emulation_off", False)
    ):
        return False
    with lifecycle_lock(session):
        tab = getattr(session, "tab", None)
        if not getattr(session, "_uwapi_frozen", False):
            _restore_focus_emulation(session, tab)
            return False
        ok = False
        try:
            _run_cdp(tab, "Page.setWebLifecycleState", state="active", timeout=2.0)
            ok = True
        except Exception as exc:
            logger.warning(
                f"[{getattr(session, 'id', '?')}] 解除标签页冻结失败（reason={reason or '-'}）: {exc}"
            )
        finally:
            _restore_focus_emulation(session, tab)
            if ok:
                setattr(session, "_uwapi_frozen", False)
                setattr(session, "_uwapi_freeze_token", "")
                setattr(session, "_uwapi_last_resumed_at", time.time())
                setattr(session, "_uwapi_resume_failed_at", 0.0)
            else:
                # B5：解冻失败时保留「待确认冻结」状态，调用方据此拒绝交付该会话；
                # 旧实现无条件清除冻结标志，acquire() 会把仍冻结的页面当作可用标签页交出去。
                setattr(session, "_uwapi_resume_failed_at", time.time())
        frozen_at = float(getattr(session, "_uwapi_frozen_at", 0.0) or 0.0)
        if ok:
            logger.debug(
                f"[{getattr(session, 'id', '?')}] 标签页已解除冻结 "
                f"(reason={reason or '-'}, frozen_for={max(0.0, time.time() - frozen_at):.0f}s)"
            )
        return ok


def freeze_session(manager: Any, session: Any, config: IdleMaintenanceConfig) -> bool:
    """Freeze one idle, hidden tab. Returns True when the tab was frozen."""
    if session is None or getattr(manager, "_shutdown", False):
        return False
    lock = lifecycle_lock(session)
    if not lock.acquire(timeout=0.5):
        return False
    try:
        setattr(session, "_uwapi_freeze_in_progress", True)
        session_lock = getattr(session, "_lock", None)
        if session_lock is not None:
            with session_lock:
                eligible = _is_idle(session)
        else:
            eligible = _is_idle(session)
        if not eligible or getattr(session, "_uwapi_frozen", False):
            return False
        if getattr(session, "_cdp_recycle_in_progress", False):
            return False
        if (time.time() - last_activity_at(session)) < config.freeze_after_sec:
            return False

        tab = getattr(session, "tab", None)
        if tab is None:
            return False

        def claimed() -> bool:
            # 冻结给占用让路：acquire 先把状态置为 BUSY 再在 lifecycle 锁上等待解冻，
            # 这里每一步都检查，发现被占用立刻收手，不让请求等冻结流程走完
            return (not _is_idle(session)) or bool(getattr(manager, "_shutdown", False))

        if config.freeze_require_hidden and _front_tab_hint(tab) == "front":
            # 用户很可能正看着它：不切换焦点仿真（避免给页面发 blur/focus），直接跳过
            _schedule_visible_retry(session, config, probed=False)
            return False
        if claimed():
            return False
        # 先关闭焦点仿真，否则 Chromium 视其为 visible，冻结无效（见上方说明）
        setattr(session, "_uwapi_focus_emulation_off", True)
        _set_focus_emulation(tab, False)
        if config.freeze_require_hidden:
            state = _wait_real_hidden(tab, should_abort=claimed)
            if state == "aborted" or claimed():
                _restore_focus_emulation(session, tab)
                return False
            if state != "hidden":
                # 用户正在看这个标签页（前台 / 独立窗口未最小化）或无法判断：不冻结
                _restore_focus_emulation(session, tab)
                _schedule_visible_retry(session, config, probed=True)
                return False

        token = f"{getattr(session, 'id', '?')}:{time.time_ns()}"
        mark_installed = False
        try:
            mark_installed = bool(driver_for_tab(tab).run_js(_FREEZE_MARK_INSTALL_JS, token, timeout=1.0))
        except Exception:
            mark_installed = False

        if claimed():
            _restore_focus_emulation(session, tab)
            return False
        # 此后若被占用，占用方会在 lifecycle 锁上等待并解冻
        try:
            _run_cdp(tab, "Page.setWebLifecycleState", state="frozen", timeout=1.5)
        except Exception:
            _restore_focus_emulation(session, tab)
            raise
        if mark_installed and not _freeze_took_effect(tab, token, should_abort=claimed):
            # Chrome 没有真正冻结（页面仍在跑）：撤销，稍后重试
            try:
                _run_cdp(tab, "Page.setWebLifecycleState", state="active", timeout=1.5)
            except Exception:
                pass
            _restore_focus_emulation(session, tab)
            setattr(session, "_uwapi_freeze_retry_at", time.time() + 30.0)
            setattr(session, "_uwapi_freeze_ineffective_count",
                    int(getattr(session, "_uwapi_freeze_ineffective_count", 0) or 0) + 1)
            logger.debug(f"[{getattr(session, 'id', '?')}] 冻结未生效（页面仍在运行），30s 后重试")
            return False
        setattr(session, "_uwapi_freeze_token", token if mark_installed else "")
        setattr(session, "_uwapi_frozen", True)
        setattr(session, "_uwapi_frozen_at", time.time())
        setattr(session, "_uwapi_frozen_check_at", time.time())
        setattr(session, "_uwapi_visible_probe_streak", 0)
        logger.debug(f"[{getattr(session, 'id', '?')}] 空闲标签页已冻结 (Page.setWebLifecycleState=frozen)")
        return True
    except Exception as exc:
        logger.debug_throttled(
            f"tab_pool.freeze_error.{getattr(session, 'id', '?')}",
            f"[{getattr(session, 'id', '?')}] 空闲标签页冻结失败（忽略）: {exc}",
            interval_sec=300.0,
        )
        setattr(session, "_uwapi_freeze_retry_at", time.time() + 300.0)
        return False
    finally:
        setattr(session, "_uwapi_freeze_in_progress", False)
        lock.release()


# --------------------------------------------------------------------------- #
# CDP session recycle (P0-3)
# --------------------------------------------------------------------------- #


def _supports_recycle(tab: Any) -> bool:
    return all(callable(getattr(tab, name, None)) for name in ("disconnect", "_driver_init", "_get_document"))


def recycle_tab_connection(tab: Any) -> None:
    """Drop and re-create the tab's CDP websocket without touching the page.

    Chrome detaches the old DevTools session, which releases every RemoteObject
    and DOM-agent node mapping held for it.
    """
    target_id = getattr(tab, "_target_id", None) or getattr(tab, "tab_id", None)
    if not target_id:
        raise RuntimeError("tab target id unavailable")
    listener = getattr(tab, "_listener", None)
    # 每次尝试都从 disconnect 开始：_driver_init 第一步就取新驱动，中途失败会留下一个
    # 半初始化的驱动（_is_loading 卡住、load 回调未注册、文档根未获取）。实测半初始化 / 取驱动失败 /
    # _get_document 失败三种情况下，标签页都会在后续请求里报错或空等 10s+，必须完整重建。
    # 另外 DrissionPage 的 _get_document() 失败时不抛异常：读不到返回 False；
    # 另一线程（页面加载事件）正在读时直接返回 None；读的过程中抛异常则会让 _is_reading 标志一直卡住，
    # 之后每次都“直接返回 None”。只看异常会把这些情况当成功、留下失效的文档根（run_js 报 ElementLostError）。
    last_exc: Optional[BaseException] = None
    for attempt in range(RECYCLE_REBUILD_ATTEMPTS):
        try:
            tab.disconnect()
        except Exception:
            pass
        try:
            if getattr(tab, "_is_reading", False):
                tab._is_reading = False  # 旧会话上中断的读取留下的标志，新驱动上重新读取
            tab._driver_init(target_id)
            doc = tab._get_document()
            if doc is None:
                # 新驱动的页面加载事件正在读取文档：等它读完
                deadline = time.monotonic() + 5.0
                while getattr(tab, "_is_reading", False) and time.monotonic() < deadline:
                    time.sleep(0.05)
            elif doc is False:
                raise RuntimeError("document root unavailable after reconnect")
            if not _tab_connection_healthy(tab):
                raise RuntimeError("tab does not respond after reconnect")
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < RECYCLE_REBUILD_ATTEMPTS:
                time.sleep(0.5 * (attempt + 1))
    if last_exc is not None:
        raise last_exc

    # 监听器（补丁版 _reuse_driver 模式）引用的是旧驱动
    if listener is not None:
        for attr, value in (("_driver", None), ("_network_enabled", False), ("listening", False)):
            try:
                if hasattr(listener, attr):
                    setattr(listener, attr, value)
            except Exception:
                pass
    # 会话级注入脚本随旧会话一起失效
    try:
        init_jss = getattr(tab, "_init_jss", None)
        if isinstance(init_jss, list):
            init_jss.clear()
    except Exception:
        pass
    for attr in ("_kimi_fetch_init_js_id",):
        try:
            if hasattr(tab, attr):
                delattr(tab, attr)
        except Exception:
            pass


def _reinstall_command_init_scripts(session: Any, tab: Any) -> int:
    """Re-register command ``run_js_file`` new-document scripts on the fresh CDP session.

    ``Page.addScriptToEvaluateOnNewDocument`` registrations belong to the CDP session and die
    with it, while the command engine's per-session registry (and the bootstrap mtime cache)
    still say "installed" -- so page-resilience / monitoring scripts would silently stop being
    injected after the next reload. Re-register the same source and update the identifier.
    The page itself was not reloaded, so the script is NOT executed again in the current document.
    """
    registry = getattr(session, "_command_init_js_registry", None)
    if not isinstance(registry, dict) or not registry:
        return 0
    restored = 0
    lost = False
    for key, entry in list(registry.items()):
        source = str(entry.get("source", "") or "") if isinstance(entry, dict) else ""
        if not source:
            continue
        try:
            result = _run_cdp(tab, "Page.addScriptToEvaluateOnNewDocument", timeout=5.0, source=source)
            identifier = ""
            if isinstance(result, dict):
                identifier = str(result.get("identifier") or result.get("scriptId") or "").strip()
            entry["identifier"] = identifier
            restored += 1
        except Exception as exc:
            # 让命令引擎下一轮重新同步（启动脚本会重新注册）
            registry.pop(key, None)
            lost = True
            logger.warning(
                f"[{getattr(session, 'id', '?')}] CDP 回收后重新注册命令预注入脚本失败: "
                f"{entry.get('path', key) if isinstance(entry, dict) else key}: {exc}"
            )
    if lost:
        state = getattr(session, "_command_bootstrap_js_files", None)
        if isinstance(state, dict):
            state.clear()
    return restored


RECYCLE_REBUILD_ATTEMPTS = 3


def _tab_connection_healthy(tab: Any) -> bool:
    try:
        return driver_for_tab(tab).run_js("return 1", timeout=2.0) == 1
    except Exception:
        return False


def _quarantine_broken_tab(session: Any, tab: Any, reason: str) -> None:
    """Recycle could not rebuild the connection: never hand this tab out again as-is.

    Stops whatever half-initialised driver is left, drops the DrissionPage singleton so a
    later pool rescan builds a fresh tab object (instead of getting the same broken one back),
    and marks the session ERROR so the pool's existing cleanup / quarantine takes over.
    """
    try:
        tab.disconnect()
    except Exception:
        pass
    from app.core.driver.drission_internals import forget_tab_object

    forget_tab_object(tab)
    try:
        session.mark_error(reason)
    except Exception:
        pass


def _dom_node_count(tab: Any) -> int:
    try:
        result = _run_cdp(tab, "Memory.getDOMCounters", timeout=1.0)
    except Exception:
        return 0
    if isinstance(result, dict):
        try:
            return int(result.get("nodes") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _recycle_reason(session: Any, config: IdleMaintenanceConfig, now: float) -> str:
    if not config.recycle_enabled:
        return ""
    tab = getattr(session, "tab", None)
    if tab is None or not _supports_recycle(tab):
        return ""
    if now < float(getattr(session, "_uwapi_recycle_retry_at", 0.0) or 0.0):
        return ""
    last_recycle = getattr(session, "_uwapi_last_recycle_at", None)
    if last_recycle is None:
        # 从未回收：以会话创建为起点，创建以来处理过的请求都计数
        setattr(session, "_uwapi_last_recycle_at", float(getattr(session, "created_at", now) or now))
        setattr(session, "_uwapi_recycle_request_mark", 0)
        last_recycle = getattr(session, "_uwapi_last_recycle_at")
    last_recycle = float(last_recycle or 0.0)

    # 回收只对“上次回收后有过使用”的标签页有意义
    used_since = float(getattr(session, "last_used_at", 0.0) or 0.0) > last_recycle + 1.0 or float(
        getattr(session, "_pc_last_wake_at", 0.0) or 0.0
    ) > last_recycle + 1.0
    if not used_since:
        return ""
    if (now - float(getattr(session, "last_used_at", 0.0) or 0.0)) < config.recycle_idle_sec:
        return ""

    if config.recycle_after_requests > 0:
        mark = int(getattr(session, "_uwapi_recycle_request_mark", 0) or 0)
        served = int(getattr(session, "request_count", 0) or 0) - mark
        if served >= config.recycle_after_requests:
            return f"requests={served}"
    if config.recycle_interval_sec > 0 and (now - last_recycle) >= config.recycle_interval_sec:
        return f"interval={now - last_recycle:.0f}s"
    if config.recycle_dom_nodes > 0:
        last_check = float(getattr(session, "_uwapi_dom_check_at", 0.0) or 0.0)
        if (now - last_check) >= config.dom_check_interval_sec:
            setattr(session, "_uwapi_dom_check_at", now)
            nodes = _dom_node_count(tab)
            if nodes >= config.recycle_dom_nodes:
                return f"dom_nodes={nodes}"
    return ""


def recycle_session(manager: Any, session: Any, reason: str) -> bool:
    """Recycle one idle session's CDP connection under a maintenance lease."""
    if session is None or getattr(manager, "_shutdown", False):
        return False
    tabs = getattr(manager, "_tabs", {}) or {}
    if tabs.get(getattr(session, "id", None)) is not session:
        return False
    tab = getattr(session, "tab", None)
    if tab is None or not _supports_recycle(tab):
        return False

    task_id = f"maint:cdp_recycle:{int(time.time() * 1000)}"
    saved_last_used = float(getattr(session, "last_used_at", 0.0) or 0.0)
    # 本次若因条件不满足而跳过，60s 内不再尝试回收（否则每个 tick 都选中回收，冻结永远轮不到）
    setattr(session, "_uwapi_recycle_retry_at", time.time() + 60.0)
    if not session.acquire_for_command(task_id):
        return False

    started = time.perf_counter()
    ok = False
    setattr(session, "_cdp_recycle_in_progress", True)
    try:
        stop = getattr(manager, "_stop_global_monitor_for_session", None)
        if callable(stop) and not stop(session.id, reason="cdp_recycle", wait=True):
            logger.debug(f"[{session.id}] 全局监听未能及时停止，跳过本次 CDP 回收")
            return False
        listener = getattr(tab, "_listener", None)
        if listener is not None and getattr(listener, "listening", False):
            logger.debug(f"[{session.id}] 标签页仍在监听网络，跳过本次 CDP 回收")
            return False

        recycle_tab_connection(tab)
        _reinstall_command_init_scripts(session, tab)
        # 旧会话注册的可见性模拟脚本已随会话失效；标签页释放时本来就会恢复页面状态
        try:
            from app.core.page_lifecycle import _clear_visibility_emulation_attrs

            _clear_visibility_emulation_attrs(tab)
            _clear_visibility_emulation_attrs(session)
        except Exception:
            pass
        try:
            setattr(session, "_audio_capture_init_script_source", None)
        except Exception:
            pass

        setattr(session, "_uwapi_last_recycle_at", time.time())
        setattr(session, "_uwapi_recycle_request_mark", int(getattr(session, "request_count", 0) or 0))
        setattr(session, "_uwapi_recycle_count", int(getattr(session, "_uwapi_recycle_count", 0) or 0) + 1)
        setattr(session, "_uwapi_recycle_retry_at", 0.0)
        ok = True
        logger.info(
            f"[{session.id}] 空闲标签页 CDP 会话已回收（{reason}，"
            f"耗时 {time.perf_counter() - started:.2f}s）"
        )
        return True
    except Exception as exc:
        # 避免失败后每个 tick 重试
        setattr(session, "_uwapi_last_recycle_at", time.time())
        setattr(session, "_uwapi_recycle_failures", int(getattr(session, "_uwapi_recycle_failures", 0) or 0) + 1)
        if _tab_connection_healthy(tab):
            logger.warning(f"[{session.id}] 空闲标签页 CDP 会话回收失败，但连接仍可用，继续使用: {exc}")
        else:
            logger.warning(f"[{session.id}] 空闲标签页 CDP 会话回收失败且连接不可用，标记为错误交给池清理: {exc}")
            _quarantine_broken_tab(session, tab, "cdp_recycle_failed")
        return False
    finally:
        setattr(session, "_cdp_recycle_in_progress", False)
        try:
            manager.release(session.id, check_triggers=False, expected_task_id=task_id)
        except Exception as exc:
            logger.warning(f"[{session.id}] CDP 回收后释放标签页失败: {exc}")
        # 维护占用不算“使用”：恢复空闲计时，冻结计时不被推迟
        try:
            session_lock = getattr(session, "_lock", None)
            if session_lock is not None:
                with session_lock:
                    if _is_idle(session):
                        session.last_used_at = saved_last_used
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# scheduling (called from TabPoolManager.run_watchdog_tick)
# --------------------------------------------------------------------------- #


FROZEN_VISIBILITY_CHECK_SEC = 10.0


def sync_frozen_visibility(session: Any) -> bool:
    """Chrome lifts a CDP freeze by itself when the user switches to the tab or
    restores the window (measured). Bring our bookkeeping in line: mark the tab
    resumed and turn focus emulation back on, so it can be re-frozen later once
    it is hidden and idle again. Returns True when the tab was found visible.
    """
    if session is None or not getattr(session, "_uwapi_frozen", False):
        return False
    tab = getattr(session, "tab", None)
    if tab is None:
        return False
    # 冻结状态下同步 run_js 仍可执行；此时焦点仿真已关闭，读到的是真实可见性
    if _read_real_visibility(tab) == "visible":
        resume_if_frozen(session, reason="user_visible")
        return True
    # 页面已在我们背后恢复运行（会话断开 / 用户切回又切走 / 导航）：记账改回，之后可重新冻结
    if freeze_was_lifted(session):
        resume_if_frozen(session, reason="lifted_externally")
        setattr(session, "_uwapi_freeze_retry_at", 0.0)
    return False


def _run_maintenance(manager: Any, session: Any, config: IdleMaintenanceConfig, action: str, reason: str) -> None:
    try:
        if action == "recycle":
            recycle_session(manager, session, reason)
        elif action == "freeze":
            freeze_session(manager, session, config)
        elif action == "sync":
            sync_frozen_visibility(session)
    finally:
        setattr(session, "_uwapi_maint_pending", False)


def schedule_idle_maintenance(manager: Any, config: Optional[IdleMaintenanceConfig] = None) -> int:
    """Pick idle sessions that need recycle/freeze and submit them to the maintenance pool."""
    config = config or getattr(manager, "_idle_maintenance_config", None) or IdleMaintenanceConfig.from_env()
    if not (config.freeze_enabled or config.recycle_enabled):
        return 0
    if getattr(manager, "_shutdown", False):
        return 0
    executor = getattr(manager, "_maintenance_executor", None)
    if executor is None:
        return 0
    lock = getattr(manager, "_lock", None)
    if lock is not None:
        with lock:
            sessions = list((getattr(manager, "_tabs", {}) or {}).values())
    else:
        sessions = list((getattr(manager, "_tabs", {}) or {}).values())

    now = time.time()
    scheduled = 0
    for session in sessions:
        try:
            if getattr(session, "_uwapi_maint_pending", False):
                continue
            if not _is_idle(session) or getattr(session, "_cdp_recycle_in_progress", False):
                continue
            action = ""
            reason = ""
            frozen = bool(getattr(session, "_uwapi_frozen", False))
            if frozen:
                last_check = float(getattr(session, "_uwapi_frozen_check_at", 0.0) or 0.0)
                if now - last_check >= FROZEN_VISIBILITY_CHECK_SEC:
                    setattr(session, "_uwapi_frozen_check_at", now)
                    action = "sync"
            else:
                reason = _recycle_reason(session, config, now)
                if reason:
                    action = "recycle"
            if not action and config.freeze_enabled and not frozen:
                retry_at = float(getattr(session, "_uwapi_freeze_retry_at", 0.0) or 0.0)
                activity = last_activity_at(session)
                # 被“可见”退避推迟时，一旦有新活动就不再等退避期满
                if int(getattr(session, "_uwapi_visible_probe_streak", 0) or 0) > 0 and activity > float(
                    getattr(session, "_uwapi_visible_probe_mark", 0.0) or 0.0
                ):
                    retry_at = min(retry_at, activity + config.freeze_after_sec)
                if now >= retry_at and (now - activity) >= config.freeze_after_sec:
                    action = "freeze"
            if not action:
                continue
            setattr(session, "_uwapi_maint_pending", True)
            try:
                executor.submit(_run_maintenance, manager, session, config, action, reason)
                scheduled += 1
            except RuntimeError:
                setattr(session, "_uwapi_maint_pending", False)
                break
        except Exception as exc:
            logger.debug(f"[TabPool] 空闲维护调度异常（忽略）: {exc}")
    return scheduled


__all__ = [
    "IdleMaintenanceConfig",
    "REAL_VISIBILITY_JS",
    "freeze_session",
    "is_idle_freeze_enabled",
    "last_activity_at",
    "lifecycle_lock",
    "note_network_activity",
    "recycle_session",
    "recycle_tab_connection",
    "resume_if_frozen",
    "schedule_idle_maintenance",
    "sync_frozen_visibility",
]
