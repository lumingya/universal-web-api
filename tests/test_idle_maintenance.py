"""P0-3 (CDP session recycle) and P0-6 (idle freeze) tests."""

from __future__ import annotations

import json
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.tab_pool_parts import idle_maintenance as im
from app.core.tab_pool_parts.session import TabSession, TabStatus
from tests._real_browser import launch_page


class _CdpTab:
    def __init__(self, visibility="hidden", window_state="normal"):
        self.calls = []
        self.browser_calls = []
        self.visibility = visibility
        self.window_state = window_state
        self.freeze_delay = 0.0

    def run_cdp(self, method, **kwargs):
        if method.startswith("Browser."):
            # window lookups for the side-effect-free visibility hint; not page state changes
            self.browser_calls.append(method)
            return {"windowId": getattr(self, "window_id", 1), "bounds": {"windowState": self.window_state}}
        if method == "Page.setWebLifecycleState" and kwargs.get("state") == "frozen" and self.freeze_delay:
            time.sleep(self.freeze_delay)
        self.calls.append((method, kwargs.get("state", kwargs.get("enabled"))))
        if method == "Page.setWebLifecycleState":
            self._lifecycle(kwargs.get("state"))
        return {}

    ignore_freeze = False  # simulate Chrome silently not freezing
    _mark = None

    def run_js(self, script, *args, **kwargs):
        if "__uwapiFz" in script and "defineProperty" in script:
            self._mark = {"t": args[0], "f": 0, "r": 0}
            return 1
        if "__uwapiFz" in script:
            return json.dumps(self._mark) if self._mark else ""
        if script == "return 1":  # connection health probe
            return 1
        return self.visibility

    def _lifecycle(self, state):
        if self._mark is None or self.ignore_freeze:
            return
        if state == "frozen" and not self._mark["f"]:
            self._mark["f"] = 1
        elif state == "active" and self._mark["f"] and not self._mark["r"]:
            self._mark["r"] = 1

    def external_resume(self):
        """Chrome lifted the freeze behind our back (session detach, user switched to the tab, ...)."""
        self._lifecycle("active")


class _FakeBrowser:
    """MRU-ordered tab_ids like DrissionPage's /json based Chromium.tab_ids."""

    def __init__(self, mru, windows):
        self.tab_ids = list(mru)
        self._windows = dict(windows)

    def _run_cdp(self, method, **kwargs):
        return {"windowId": self._windows[kwargs["targetId"]]}


class _FakeManager:
    def __init__(self, sessions):
        self._tabs = {s.id: s for s in sessions}
        self._lock = threading.RLock()
        self._shutdown = False
        self._maintenance_executor = ThreadPoolExecutor(max_workers=2)
        self.released = []
        self.stopped = []

    def release(self, tab_id, check_triggers=True, expected_task_id=""):
        session = self._tabs[tab_id]
        self.released.append((tab_id, expected_task_id))
        with session._lock:
            session.status = TabStatus.IDLE
            session.current_task_id = None
        return True

    def _stop_global_monitor_for_session(self, session_id, reason="", wait=False):
        self.stopped.append((session_id, reason, wait))
        return True


def _cfg(**kw):
    base = dict(
        freeze_enabled=True,
        freeze_after_sec=60.0,
        freeze_require_hidden=True,
        recycle_enabled=True,
        recycle_idle_sec=30.0,
        recycle_after_requests=3,
        recycle_interval_sec=0.0,
        recycle_dom_nodes=0,
        dom_check_interval_sec=300.0,
    )
    base.update(kw)
    return im.IdleMaintenanceConfig(**base)


def _idle_session(tab, idle_for=120.0, sid="s1"):
    session = TabSession(sid, tab)
    session.last_used_at = time.time() - idle_for
    return session


# --------------------------------------------------------------------------- #
# freeze / resume
# --------------------------------------------------------------------------- #


def test_freeze_hidden_idle_tab_and_resume_on_acquire():
    tab = _CdpTab("hidden")
    session = _idle_session(tab)
    manager = _FakeManager([session])

    assert im.freeze_session(manager, session, _cfg()) is True
    assert session._uwapi_frozen is True
    # focus emulation must be switched off first, otherwise Chromium ignores the freeze
    assert tab.calls == [
        ("Emulation.setFocusEmulationEnabled", False),
        ("Page.setWebLifecycleState", "frozen"),
    ]

    assert session.acquire("req-1") is True
    assert session._uwapi_frozen is False
    assert tab.calls[-2:] == [
        ("Page.setWebLifecycleState", "active"),
        ("Emulation.setFocusEmulationEnabled", True),
    ]
    assert session._uwapi_focus_emulation_off is False


def test_acquire_for_command_also_resumes():
    tab = _CdpTab("hidden")
    session = _idle_session(tab)
    im.freeze_session(_FakeManager([session]), session, _cfg())

    assert session.acquire_for_command("cmd-1") is True
    assert tab.calls[-2] == ("Page.setWebLifecycleState", "active")
    assert tab.calls[-1] == ("Emulation.setFocusEmulationEnabled", True)


def test_visible_tab_is_never_frozen():
    tab = _CdpTab("visible")
    session = _idle_session(tab)

    assert im.freeze_session(_FakeManager([session]), session, _cfg()) is False
    assert not getattr(session, "_uwapi_frozen", False)
    assert all(state != "frozen" for _, state in tab.calls)
    # focus emulation is restored for a tab the user is looking at
    assert tab.calls[-1] == ("Emulation.setFocusEmulationEnabled", True)
    assert session._uwapi_focus_emulation_off is False
    assert session._uwapi_freeze_retry_at > time.time()


def test_failed_freeze_restores_focus_emulation():
    class _Failing(_CdpTab):
        def run_cdp(self, method, **kwargs):
            if method == "Page.setWebLifecycleState":
                raise RuntimeError("boom")
            return super().run_cdp(method, **kwargs)

    tab = _Failing("hidden")
    session = _idle_session(tab)
    assert im.freeze_session(_FakeManager([session]), session, _cfg()) is False
    assert tab.calls[-1] == ("Emulation.setFocusEmulationEnabled", True)
    assert session._uwapi_focus_emulation_off is False
    assert not getattr(session, "_uwapi_frozen", False)
    assert session._uwapi_freeze_retry_at > time.time() + 200


def test_recently_active_or_busy_tabs_are_not_frozen():
    tab = _CdpTab("hidden")
    recent = _idle_session(tab, idle_for=5.0)
    assert im.freeze_session(_FakeManager([recent]), recent, _cfg()) is False

    woken = _idle_session(tab, idle_for=300.0, sid="s2")
    woken._pc_last_wake_at = time.time() - 5.0  # page_check just ran
    assert im.freeze_session(_FakeManager([woken]), woken, _cfg()) is False

    net = _idle_session(tab, idle_for=300.0, sid="s3")
    im.note_network_activity(net)
    assert im.freeze_session(_FakeManager([net]), net, _cfg()) is False

    busy = _idle_session(tab, idle_for=300.0, sid="s4")
    busy.status = TabStatus.BUSY
    assert im.freeze_session(_FakeManager([busy]), busy, _cfg()) is False


def test_acquire_racing_with_freeze_ends_active():
    """Acquire during an in-flight freeze must wait and leave the tab active."""
    tab = _CdpTab("hidden")
    tab.freeze_delay = 0.3
    session = _idle_session(tab)
    manager = _FakeManager([session])

    freezer = threading.Thread(target=im.freeze_session, args=(manager, session, _cfg()))
    freezer.start()
    time.sleep(0.1)  # freeze CDP call in flight
    assert session.acquire("req-race") is True
    freezer.join()

    states = [state for method, state in tab.calls if method == "Page.setWebLifecycleState"]
    assert states == ["frozen", "active"]
    assert session._uwapi_frozen is False


def test_resume_without_freeze_is_noop():
    tab = _CdpTab()
    session = _idle_session(tab)
    assert im.resume_if_frozen(session) is False
    assert tab.calls == []


# --------------------------------------------------------------------------- #
# scheduling
# --------------------------------------------------------------------------- #


def test_schedule_prefers_recycle_then_freeze():
    tab = _CdpTab("hidden")
    tab.disconnect = lambda: None
    tab._driver_init = lambda target_id: None
    tab._get_document = lambda: True
    tab._target_id = "T1"
    session = _idle_session(tab, idle_for=120.0)
    session._uwapi_last_recycle_at = time.time() - 600
    session._uwapi_recycle_request_mark = 0
    session.request_count = 5
    session.last_used_at = time.time() - 120
    manager = _FakeManager([session])

    assert im.schedule_idle_maintenance(manager, _cfg()) == 1
    manager._maintenance_executor.shutdown(wait=True)

    assert manager.released and manager.released[0][1].startswith("maint:cdp_recycle:")
    assert manager.stopped == [("s1", "cdp_recycle", True)]
    assert session._uwapi_recycle_count == 1
    assert session._uwapi_recycle_request_mark == 5
    assert session.status == TabStatus.IDLE
    # maintenance lease must not reset the idle clock
    assert time.time() - session.last_used_at >= 119

    manager._maintenance_executor = ThreadPoolExecutor(max_workers=1)
    assert im.schedule_idle_maintenance(manager, _cfg()) == 1  # now freeze
    manager._maintenance_executor.shutdown(wait=True)
    assert session._uwapi_frozen is True


def test_skipped_recycle_backs_off_so_freeze_is_not_starved():
    tab = _CdpTab("hidden")
    tab.disconnect = tab._driver_init = tab._get_document = lambda *a: None
    session = _idle_session(tab, idle_for=120.0)
    session.created_at = time.time() - 600
    session.request_count = 3  # served since creation: the never-recycled mark starts at 0
    manager = _FakeManager([session])
    manager._stop_global_monitor_for_session = lambda *a, **k: False  # monitor refuses to stop

    assert im._recycle_reason(session, _cfg(), time.time()) == "requests=3"
    assert im.schedule_idle_maintenance(manager, _cfg()) == 1
    manager._maintenance_executor.shutdown(wait=True)
    assert not getattr(session, "_uwapi_recycle_count", 0)
    assert session.status == TabStatus.IDLE

    manager._maintenance_executor = ThreadPoolExecutor(max_workers=1)
    assert im.schedule_idle_maintenance(manager, _cfg()) == 1  # freeze gets its turn
    manager._maintenance_executor.shutdown(wait=True)
    assert session._uwapi_frozen is True


def test_unused_tab_is_not_recycled():
    tab = _CdpTab("visible")
    tab.disconnect = tab._driver_init = tab._get_document = lambda *a: None
    session = _idle_session(tab, idle_for=5000.0)
    session._uwapi_last_recycle_at = time.time() - 100  # recycled after last use
    session.request_count = 100
    assert im._recycle_reason(session, _cfg(recycle_interval_sec=60.0), time.time()) == ""


def test_disabled_config_schedules_nothing():
    session = _idle_session(_CdpTab(), idle_for=5000)
    manager = _FakeManager([session])
    assert im.schedule_idle_maintenance(manager, _cfg(freeze_enabled=False, recycle_enabled=False)) == 0


def test_keepalive_defaults_off_when_freeze_enabled(monkeypatch):
    monkeypatch.delenv("CMD_PERIODIC_KEEPALIVE_ENABLED", raising=False)
    monkeypatch.setenv("BROWSER_IDLE_FREEZE_ENABLED", "true")
    from app.services.command_engine import CommandEngine

    assert CommandEngine()._periodic_keepalive_enabled is False
    monkeypatch.setenv("BROWSER_IDLE_FREEZE_ENABLED", "false")
    monkeypatch.setenv("BROWSER_MEMORY_SAVER", "false")
    assert CommandEngine()._periodic_keepalive_enabled is True


def test_disconnect_during_recycle_does_not_close_session():
    from app.services.command_engine import CommandEngine

    engine = CommandEngine.__new__(CommandEngine)
    session = _idle_session(_CdpTab())
    session._cdp_recycle_in_progress = True
    closed = []
    session.mark_closed = lambda reason: closed.append(reason)
    engine._looks_like_page_disconnected_error = lambda error: True

    assert engine._mark_session_closed_if_disconnected(session, RuntimeError("disconnected"), "x") is True
    assert closed == []


# --------------------------------------------------------------------------- #
# real browser
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def real_page():
    yield from launch_page()


def _dom_nodes(page):
    page.run_cdp("HeapProfiler.collectGarbage")
    return int(page.run_cdp("Memory.getDOMCounters")["nodes"])


def test_real_browser_recycle_releases_pinned_detached_dom(real_page):
    page = real_page
    page.get("data:text/html,<main id=root></main>")
    page.run_js(
        "const r=document.getElementById('root');"
        "for(let i=0;i<3000;i++){const d=document.createElement('div');d.className='x';d.textContent='n'+i;r.appendChild(d);}"
    )
    # DrissionPage node lookups pin every element through the CDP session
    elements = page.eles("css:div.x")
    assert len(elements) == 3000
    held_id = elements[0]._obj_id
    page.run_js("document.getElementById('root').innerHTML='';")
    del elements

    pinned = _dom_nodes(page)
    assert pinned > 3000  # detached nodes survive GC while the session holds them

    session = TabSession("real", page)
    session.last_used_at = time.time() - 120
    manager = _FakeManager([session])
    assert im.recycle_session(manager, session, "test") is True

    after = _dom_nodes(page)
    assert after < 500, (pinned, after)
    # old RemoteObject ids are gone, the page itself is untouched and usable
    with pytest.raises(Exception):
        page.run_cdp("Runtime.callFunctionOn", objectId=held_id, functionDeclaration="function(){return 1}")
    assert page.url.startswith("data:text/html")
    assert page.run_js("return document.getElementById('root') ? 'ok' : 'missing'") == "ok"
    page.run_js("document.getElementById('root').innerHTML='<p class=y>hello</p>'")
    assert page.ele("css:p.y").text == "hello"
    # network listening still works on the fresh session
    page.listen.start(targets=True)
    page.listen.stop()
    assert session.status == TabStatus.IDLE
    assert manager.released


def test_real_browser_recycle_recovers_from_stuck_document_read_latch(real_page):
    """Regression: a document read aborted by an exception (e.g. in DrissionPage's load-event thread)
    leaves ``_is_reading`` set; ``_get_document()`` then silently returns None and the recycle used to
    report success with a stale document root (every run_js -> ElementLostError)."""
    page = real_page
    page.get("data:text/html,<main id=root>latch</main>")
    session = TabSession("latch", page)
    session.last_used_at = time.time() - 120
    page._is_reading = True  # what an aborted read leaves behind
    try:
        assert im.recycle_session(_FakeManager([session]), session, "test") is True
        assert page.run_js("return 1+1") == 2
        assert page.ele("#root").text == "latch"
    finally:
        page._is_reading = False


def test_real_browser_visible_tab_is_not_frozen_and_focus_emulation_restored(real_page):
    """Headless tabs are always visible: freeze must refuse and leave the tab as it was."""
    page = real_page
    page.get("data:text/html,<p>t</p>")
    page.run_js("window.__ticks=0; setInterval(()=>{window.__ticks++}, 20);")
    session = TabSession("real-visible", page)
    session.last_used_at = time.time() - 120
    assert page.run_js("return document.hasFocus()") is True

    page.run_js(
        "window.__fev=[]; for (const n of ['blur','focus']) window.addEventListener(n,()=>__fev.push(n));"
    )
    assert im.freeze_session(_FakeManager([session]), session, _cfg()) is False
    assert not getattr(session, "_uwapi_frozen", False)
    assert getattr(session, "_uwapi_focus_emulation_off", False) is False
    # it is the front tab: skipped without toggling focus emulation -> no blur/focus reach the page
    assert page.run_js("return window.__fev") == []
    # DrissionPage's focus emulation is back on, timers keep running
    assert page.run_js("return document.hasFocus()") is True
    before = page.run_js("return window.__ticks")
    time.sleep(0.3)
    assert page.run_js("return window.__ticks") - before >= 5


@pytest.fixture(scope="module")
def headed_page():
    import os
    import sys

    desktop_opt_in = sys.platform in ("win32", "darwin") and os.getenv("UWAPI_ALLOW_HEADED") == "1"
    if not (os.getenv("DISPLAY") or desktop_opt_in):
        pytest.skip(
            "needs a display for real tab visibility: xvfb-run on Linux, "
            "or UWAPI_ALLOW_HEADED=1 on Windows/macOS (a Chromium window will pop up)"
        )
    yield from launch_page(headless=False)


def test_real_browser_hidden_tab_freeze_stops_timers_and_acquire_resumes(headed_page):
    """Headed Chromium: a background tab really freezes, acquire() brings it back."""
    page = headed_page
    page.get("data:text/html,<p>bg</p>")
    page.run_js(
        "window.__ticks=0; window.__ev=[]; setInterval(()=>{window.__ticks++}, 20);"
        "document.addEventListener('freeze',()=>__ev.push('freeze'));"
        "document.addEventListener('resume',()=>__ev.push('resume'));"
    )
    foreground = page.new_tab("data:text/html,<p>fg</p>")  # pushes `page` to the background
    try:
        time.sleep(0.5)
        # DrissionPage focus emulation makes the background tab look visible ...
        assert page.run_js(im.REAL_VISIBILITY_JS) == "visible"
        session = TabSession("real-freeze", page)
        session.last_used_at = time.time() - 120
        manager = _FakeManager([session])

        # ... freeze_session sees through it and freezes the tab
        assert im.freeze_session(manager, session, _cfg()) is True
        t0 = page.run_js("return window.__ticks")
        time.sleep(0.6)
        t1 = page.run_js("return window.__ticks")
        assert t1 - t0 <= 1, (t0, t1)
        assert "freeze" in page.run_js("return JSON.stringify(window.__ev)")
        page.run_cdp("HeapProfiler.collectGarbage")  # CDP still works on a frozen page

        assert session.acquire("req-real") is True
        assert session._uwapi_frozen is False
        t2 = page.run_js("return window.__ticks")
        time.sleep(0.5)
        t3 = page.run_js("return window.__ticks")
        assert t3 - t2 >= 10, (t2, t3)
        assert "resume" in page.run_js("return JSON.stringify(window.__ev)")
        # focus emulation restored -> DrissionPage behaviour unchanged for the request
        assert page.run_js(im.REAL_VISIBILITY_JS) == "visible"
        assert page.run_js("return document.hasFocus()") is True

        # the foreground tab (what the user sees) is never frozen
        fg_session = TabSession("real-fg", foreground)
        fg_session.last_used_at = time.time() - 120
        assert im.freeze_session(_FakeManager([fg_session]), fg_session, _cfg()) is False
        assert foreground.run_js(im.REAL_VISIBILITY_JS) == "visible"
    finally:
        try:
            foreground.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# visible tabs: no blur/focus side effects, backoff, user wake sync
# --------------------------------------------------------------------------- #


def _tab_in_window(mru, windows, tab_id, **kw):
    tab = _CdpTab(**kw)
    tab.tab_id = tab_id
    tab.window_id = windows[tab_id]
    tab.browser = _FakeBrowser(mru, windows)
    return tab


def test_front_tab_is_skipped_without_toggling_focus_emulation():
    tab = _tab_in_window(["T1", "T2"], {"T1": 1, "T2": 1}, "T1", visibility="hidden")
    session = _idle_session(tab)
    before = time.time()

    assert im.freeze_session(_FakeManager([session]), session, _cfg()) is False
    assert tab.calls == []  # no Emulation.setFocusEmulationEnabled -> no blur/focus on the page
    retry = session._uwapi_freeze_retry_at - before
    assert 59.0 <= retry <= 61.5


def test_background_tab_in_same_window_is_probed_and_frozen():
    tab = _tab_in_window(["T2", "T1"], {"T1": 1, "T2": 1}, "T1", visibility="hidden")
    session = _idle_session(tab)
    assert im.freeze_session(_FakeManager([session]), session, _cfg()) is True
    assert ("Page.setWebLifecycleState", "frozen") in tab.calls


def test_front_tab_of_minimized_window_is_frozen():
    tab = _tab_in_window(["T1"], {"T1": 1}, "T1", visibility="hidden", window_state="minimized")
    session = _idle_session(tab)
    assert im.freeze_session(_FakeManager([session]), session, _cfg()) is True


def test_front_tab_in_its_own_window_while_another_window_is_mru():
    # T1 is MRU of window 2 even though window 1's tab is globally more recent
    tab = _tab_in_window(["T9", "T1", "T2"], {"T9": 1, "T1": 2, "T2": 2}, "T1")
    session = _idle_session(tab)
    assert im._front_tab_hint(tab) == "front"
    assert im.freeze_session(_FakeManager([session]), session, _cfg()) is False
    assert tab.calls == []


def test_visible_probe_backs_off_exponentially_and_resets_on_activity():
    tab = _CdpTab("visible")  # no browser -> hint unknown -> real probe (toggles emulation)
    session = _idle_session(tab)
    manager = _FakeManager([session])
    delays = []
    for _ in range(6):
        assert im.freeze_session(manager, session, _cfg()) is False
        delays.append(round(session._uwapi_freeze_retry_at - time.time()))
    assert delays == [60, 120, 240, 480, 900, 900]
    assert session._uwapi_focus_emulation_off is False

    # new activity (e.g. a request) resets the streak, and the scheduler does not wait out the backoff
    session.last_used_at = time.time() - 61
    assert im.schedule_idle_maintenance(manager, _cfg(recycle_enabled=False)) == 1
    manager._maintenance_executor.shutdown(wait=True)
    assert round(session._uwapi_freeze_retry_at - time.time()) == 60


def test_sync_frozen_visibility_resumes_when_user_brings_tab_back():
    tab = _CdpTab("hidden")
    session = _idle_session(tab)
    manager = _FakeManager([session])
    assert im.freeze_session(manager, session, _cfg()) is True

    assert im.sync_frozen_visibility(session) is False  # still hidden
    assert session._uwapi_frozen is True

    tab.visibility = "visible"  # user switched to the tab / restored the window
    assert im.sync_frozen_visibility(session) is True
    assert session._uwapi_frozen is False
    assert session._uwapi_focus_emulation_off is False
    assert tab.calls[-2:] == [
        ("Page.setWebLifecycleState", "active"),
        ("Emulation.setFocusEmulationEnabled", True),
    ]


def test_scheduler_checks_frozen_tabs_periodically():
    tab = _CdpTab("hidden")
    session = _idle_session(tab)
    manager = _FakeManager([session])
    assert im.freeze_session(manager, session, _cfg()) is True
    cfg = _cfg(recycle_enabled=False)
    assert im.schedule_idle_maintenance(manager, cfg) == 0  # just frozen: throttled
    session._uwapi_frozen_check_at = time.time() - im.FROZEN_VISIBILITY_CHECK_SEC - 1
    tab.visibility = "visible"
    assert im.schedule_idle_maintenance(manager, cfg) == 1
    manager._maintenance_executor.shutdown(wait=True)
    assert session._uwapi_frozen is False


def test_real_browser_user_switching_to_frozen_tab_unfreezes_and_syncs(headed_page):
    """Chrome lifts the CDP freeze when the user activates the tab; we then resync focus emulation."""
    page = headed_page
    page.get("data:text/html,<p>bg2</p>")
    page.run_js("window.__t=0; setInterval(()=>{window.__t++}, 20);")
    foreground = page.new_tab("data:text/html,<p>fg2</p>")
    try:
        time.sleep(0.5)
        session = TabSession("real-user-switch", page)
        session.last_used_at = time.time() - 120
        assert im.freeze_session(_FakeManager([session]), session, _cfg()) is True
        t0 = page.run_js("return window.__t"); time.sleep(0.3)
        assert page.run_js("return window.__t") - t0 == 0

        page.browser._run_cdp("Target.activateTarget", targetId=page.tab_id)  # the user clicks the tab
        time.sleep(0.6)
        t0 = page.run_js("return window.__t"); time.sleep(0.3)
        assert page.run_js("return window.__t") - t0 >= 5  # page alive without any acquire

        assert im.sync_frozen_visibility(session) is True
        assert session._uwapi_frozen is False
        assert session._uwapi_focus_emulation_off is False
    finally:
        try:
            foreground.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# freeze verification (Chrome may ignore or silently lift a CDP freeze)
# --------------------------------------------------------------------------- #


def test_ineffective_freeze_is_rolled_back_and_retried():
    tab = _CdpTab("hidden")
    tab.ignore_freeze = True  # page never receives the 'freeze' lifecycle event
    session = _idle_session(tab)
    before = time.time()

    assert im.freeze_session(_FakeManager([session]), session, _cfg()) is False
    assert not getattr(session, "_uwapi_frozen", False)
    assert session._uwapi_focus_emulation_off is False
    assert tab.calls[-2:] == [
        ("Page.setWebLifecycleState", "active"),
        ("Emulation.setFocusEmulationEnabled", True),
    ]
    assert 29.0 <= session._uwapi_freeze_retry_at - before <= 31.5
    assert session._uwapi_freeze_ineffective_count == 1


def test_externally_lifted_freeze_is_detected_by_sync_and_can_refreeze():
    tab = _CdpTab("hidden")
    session = _idle_session(tab)
    manager = _FakeManager([session])
    assert im.freeze_session(manager, session, _cfg()) is True
    assert im.freeze_was_lifted(session) is False

    tab.external_resume()  # e.g. the DevTools session that froze it went away, tab still hidden
    assert im.freeze_was_lifted(session) is True
    assert im.sync_frozen_visibility(session) is False  # not visible, but bookkeeping fixed
    assert session._uwapi_frozen is False
    assert session._uwapi_focus_emulation_off is False
    assert session._uwapi_freeze_retry_at == 0.0

    session.last_used_at = time.time() - 120
    session._uwapi_last_resumed_at = 0.0
    assert im.freeze_session(manager, session, _cfg()) is True


def test_real_browser_recycle_lifts_freeze_and_sync_refreezes(headed_page):
    """Chrome lifts a CDP freeze when the freezing session detaches (CDP recycle); detect and recover."""
    page = headed_page
    page.get("data:text/html,<p>bg3</p>")
    page.run_js("window.__t=0; setInterval(()=>{window.__t++}, 20);")
    foreground = page.new_tab("data:text/html,<p>fg3</p>")

    def rate():
        a = page.run_js("return window.__t"); time.sleep(0.3)
        return page.run_js("return window.__t") - a

    try:
        time.sleep(0.5)
        session = TabSession("real-lifted", page)
        session.last_used_at = time.time() - 120
        manager = _FakeManager([session])
        assert im.freeze_session(manager, session, _cfg()) is True
        assert rate() == 0

        im.recycle_tab_connection(page)  # detaches the session that sent the freeze
        time.sleep(0.3)
        assert rate() >= 5  # Chrome resumed the page on its own
        assert im.freeze_was_lifted(session) is True
        im.sync_frozen_visibility(session)
        assert session._uwapi_frozen is False

        session.last_used_at = time.time() - 120
        session._uwapi_last_resumed_at = 0.0
        assert im.freeze_session(manager, session, _cfg()) is True
        assert rate() == 0
        im.resume_if_frozen(session, "test")
        assert rate() >= 5
    finally:
        try:
            foreground.close()
        except Exception:
            pass


def test_freeze_yields_to_acquire_instead_of_making_it_wait():
    """An acquire that lands mid-freeze must not wait for the probe/verify polling to finish."""
    tab = _CdpTab("visible")  # probe would poll the full 0.6s waiting for 'hidden'
    session = _idle_session(tab)
    manager = _FakeManager([session])
    result = {}

    def run_freeze():
        result["frozen"] = im.freeze_session(manager, session, _cfg())

    th = threading.Thread(target=run_freeze)
    th.start()
    deadline = time.time() + 2
    while not session._uwapi_focus_emulation_off and time.time() < deadline:
        time.sleep(0.005)  # wait until the probe is in progress
    t0 = time.perf_counter()
    assert session.acquire("req-mid-freeze") is True
    waited = time.perf_counter() - t0
    th.join()
    assert result["frozen"] is False
    assert waited < 0.25, f"acquire waited {waited:.3f}s for the freeze probe"
    assert session._uwapi_focus_emulation_off is False
    assert not getattr(session, "_uwapi_frozen", False)


def test_acquire_during_freeze_verification_still_ends_resumed():
    tab = _CdpTab("hidden")
    tab.ignore_freeze = True  # verification would poll 0.4s and then roll back
    session = _idle_session(tab)
    manager = _FakeManager([session])
    th = threading.Thread(target=lambda: im.freeze_session(manager, session, _cfg()))
    orig = tab._lifecycle

    def lifecycle_then_claim(state):
        orig(state)
        if state == "frozen":
            threading.Thread(target=lambda: session.acquire("req-verify")).start()
    tab._lifecycle = lifecycle_then_claim
    t0 = time.perf_counter()
    th.start(); th.join()
    deadline = time.time() + 2
    while session.status != TabStatus.BUSY and time.time() < deadline:
        time.sleep(0.005)
    time.sleep(0.05)
    assert time.perf_counter() - t0 < 0.4
    assert session.status == TabStatus.BUSY
    assert not getattr(session, "_uwapi_frozen", False)
    assert session._uwapi_focus_emulation_off is False
    assert ("Page.setWebLifecycleState", "active") in tab.calls


# --------------------------------------------------------------------------- #
# recycle failure handling: never hand out a tab whose connection could not be rebuilt
# --------------------------------------------------------------------------- #


class _RebuildTab:
    def __init__(self, fail_init=0, fail_forever=False, healthy_after_failure=False):
        self.tab_id = self._target_id = "T-rebuild"
        self.events = []
        self.fail_init = fail_init
        self.fail_forever = fail_forever
        self.healthy_after_failure = healthy_after_failure
        self.broken = False
        self.doc_results = []
        self.unresponsive_polls = 0

    def disconnect(self):
        self.events.append("disconnect")

    def _driver_init(self, target_id):
        self.events.append("init")
        if self.fail_forever or self.fail_init > 0:
            self.fail_init -= 1
            self.broken = True
            raise ConnectionError("simulated: cannot attach")
        self.broken = False

    def _get_document(self):
        # mimics DrissionPage: returns None while another reader holds the latch, True on success
        if getattr(self, "_is_reading", False):
            self.events.append("doc-skipped")
            return None
        self.events.append("doc")
        if self.doc_results:
            return self.doc_results.pop(0)
        return True

    def run_js(self, script, *args, **kwargs):
        if self.broken and not self.healthy_after_failure:
            raise RuntimeError("PageDisconnectedError")
        if self.unresponsive_polls > 0:
            self.unresponsive_polls -= 1
            raise RuntimeError("ElementLostError: stale document root")
        return 1


class _StatusRecordingManager(_FakeManager):
    def release(self, tab_id, check_triggers=True, expected_task_id=""):
        self.status_at_release = self._tabs[tab_id].status
        if self.status_at_release == TabStatus.ERROR:  # the real release keeps ERROR
            self.released.append((tab_id, expected_task_id))
            return True
        return super().release(tab_id, check_triggers, expected_task_id)


@pytest.fixture
def _fast_rebuild(monkeypatch):
    monkeypatch.setattr(im.time, "sleep", lambda s: None)
    monkeypatch.setattr(im, "_supports_recycle", lambda tab: True)


def test_recycle_rebuilds_from_scratch_on_each_attempt(_fast_rebuild):
    tab = _RebuildTab(fail_init=2)
    session = _idle_session(tab)
    manager = _StatusRecordingManager([session])

    assert im.recycle_session(manager, session, "test") is True
    # every attempt starts from disconnect so no half-initialised driver is reused
    assert tab.events == ["disconnect", "init", "disconnect", "init", "disconnect", "init", "doc"]
    assert session.status == TabStatus.IDLE


def test_recycle_retries_when_document_read_silently_fails(_fast_rebuild):
    """DrissionPage's _get_document() returns False instead of raising: that is a failure too."""
    tab = _RebuildTab()
    tab.doc_results = [False]
    session = _idle_session(tab)
    assert im.recycle_session(_StatusRecordingManager([session]), session, "test") is True
    assert tab.events.count("init") == 2


def test_recycle_clears_stuck_document_read_latch(_fast_rebuild):
    """An aborted read (exception inside _get_document) leaves _is_reading set forever."""
    tab = _RebuildTab()
    tab._is_reading = True
    session = _idle_session(tab)
    assert im.recycle_session(_StatusRecordingManager([session]), session, "test") is True
    assert "doc" in tab.events and "doc-skipped" not in tab.events


def test_recycle_requires_a_responsive_tab_not_just_a_document(_fast_rebuild):
    tab = _RebuildTab()
    tab.unresponsive_polls = 1  # first attempt: document read "ok" but the root is stale
    session = _idle_session(tab)
    assert im.recycle_session(_StatusRecordingManager([session]), session, "test") is True
    assert tab.events.count("init") == 2


def test_recycle_persistent_failure_marks_error_and_evicts_singleton(_fast_rebuild, monkeypatch):
    from DrissionPage._pages.chromium_tab import ChromiumTab

    tab = _RebuildTab(fail_forever=True)
    monkeypatch.setitem(ChromiumTab._TABS, tab.tab_id, tab)
    session = _idle_session(tab)
    manager = _StatusRecordingManager([session])

    assert im.recycle_session(manager, session, "test") is False
    assert tab.events.count("init") == im.RECYCLE_REBUILD_ATTEMPTS
    assert tab.events[-1] == "disconnect"  # leftover driver stopped
    assert tab.tab_id not in ChromiumTab._TABS
    assert manager.status_at_release == TabStatus.ERROR
    assert session.status == TabStatus.ERROR
    assert manager.released and manager.released[0][1].startswith("maint:cdp_recycle:")


def test_recycle_failure_with_working_connection_keeps_tab(_fast_rebuild, monkeypatch):
    from DrissionPage._pages.chromium_tab import ChromiumTab

    tab = _RebuildTab(fail_forever=True, healthy_after_failure=True)
    monkeypatch.setitem(ChromiumTab._TABS, tab.tab_id, tab)
    session = _idle_session(tab)
    manager = _StatusRecordingManager([session])

    assert im.recycle_session(manager, session, "test") is False
    assert ChromiumTab._TABS.get(tab.tab_id) is tab
    assert session.status == TabStatus.IDLE


@pytest.fixture
def real_pool():
    import http.server
    import socket
    import tempfile

    from DrissionPage import Chromium, ChromiumOptions

    from app.core.tab_pool_parts.manager import TabPoolManager
    from tests._real_browser import find_chrome

    chrome = find_chrome()
    if not chrome:
        pytest.skip("no chrome")
    page = b"<html><body><div id=root>ok</div></body></html>"

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    co = ChromiumOptions().set_browser_path(chrome)
    co.set_argument("--no-sandbox")
    co.set_local_port(port)
    co.set_user_data_path(tempfile.mkdtemp())
    co.headless(True)
    co.set_argument("--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable")
    co.set_argument(f"--proxy-server=http://127.0.0.1:{srv.server_address[1]}")
    from app.services.command_engine import command_engine as global_engine

    scheduler_was_running = global_engine.is_scheduler_running()
    browser = Chromium(co)
    first = browser.latest_tab
    deadline = time.time() + 20
    while time.time() < deadline:  # under load the first navigation can be slow / time out
        try:
            first.get("http://a.example.com:8080/", timeout=10)
            if str(first.url).startswith("http://a.example.com"):
                break
        except Exception:
            pass
        time.sleep(0.5)
    mgr = TabPoolManager(browser_page=browser, max_tabs=1, min_tabs=1)
    mgr.initialize()
    deadline = time.time() + 15
    while not mgr._tabs and time.time() < deadline:
        time.sleep(0.5)
        mgr.refresh_tabs()
    if not mgr._tabs:
        urls = [getattr(t, "url", "?") for t in browser.get_tabs()]
        mgr.shutdown()
        browser.quit()
        srv.shutdown()
        pytest.fail(f"tab pool registered no tab (tabs: {urls})")
    try:
        yield browser, mgr
    finally:
        try:
            mgr.shutdown()
        finally:
            browser.quit()
            srv.shutdown()
            # releases run the global command engine's triggers, which start its periodic
            # scheduler; stop it again (without shutting the singleton down for other tests)
            if not scheduler_was_running:
                with global_engine._scheduler_lifecycle_lock:
                    global_engine._periodic_stop_event.set()
                    thread = global_engine._periodic_thread
                if thread is not None:
                    thread.join(timeout=3.0)
                with global_engine._scheduler_lifecycle_lock:
                    if global_engine._periodic_thread is thread:
                        global_engine._periodic_thread = None


def _use_tab(mgr, expect_session):
    got = mgr.acquire("probe", timeout=3)
    try:
        if got is not expect_session:
            return got
        tab = got.tab
        assert tab.run_js("return 1+1") == 2
        assert tab.ele("#root", timeout=3).text == "ok"
        assert tab.get("http://a.example.com:8080/?x", timeout=8)
        return got
    finally:
        if got is not None:
            mgr.release(got.id, expected_task_id="probe")


def test_real_browser_failed_recycle_retries_then_quarantines_broken_tab(real_pool):
    from DrissionPage._pages.chromium_tab import ChromiumTab

    browser, mgr = real_pool
    session = next(iter(mgr._tabs.values()))
    tab = session.tab
    original_run_cdp = type(tab)._run_cdp

    def inject(fault):
        # patch only this tab object: a class-level patch would also hit other live tabs'
        # event threads (e.g. the module-scoped headed browser) and leak faults into them
        tab._run_cdp = types.MethodType(fault, tab)

    def restore():
        tab.__dict__.pop("_run_cdp", None)

    # transient: the half-initialised driver left by a failed attach is torn down and rebuilt
    failures = [2]

    def flaky(self, cmd, **kw):
        if cmd == "Page.getFrameTree" and failures[0] > 0:
            failures[0] -= 1
            raise TimeoutError("simulated: Page.getFrameTree timed out")
        return original_run_cdp(self, cmd, **kw)

    inject(flaky)
    try:
        assert im.recycle_session(mgr, session, "test") is True
    finally:
        restore()
    assert failures[0] == 0  # both simulated failures were really hit by the recycle
    assert _use_tab(mgr, session) is session

    # persistent: the session must not be handed out again, and a rescan builds a fresh tab object
    def hung(self, cmd, **kw):
        if cmd == "Page.getFrameTree":
            raise TimeoutError("simulated: renderer hung")
        return original_run_cdp(self, cmd, **kw)

    inject(hung)
    try:
        assert im.recycle_session(mgr, session, "test") is False
    finally:
        restore()
    assert session.status == TabStatus.ERROR
    assert ChromiumTab._TABS.get(tab.tab_id) is not tab
    got = mgr.acquire("probe", timeout=2)
    if got is not None:
        mgr.release(got.id, expected_task_id="probe")
    assert got is not session

    mgr.refresh_tabs()
    assert session.id not in mgr._tabs
    rebuilt = [s for s in mgr._tabs.values() if getattr(s.tab, "tab_id", None) == tab.tab_id]
    assert rebuilt and rebuilt[0].tab is not tab
    assert _use_tab(mgr, rebuilt[0]) is rebuilt[0]


# --------------------------------------------------------------------------- #
# page_check / command scripts keep working across freeze and CDP recycle
# --------------------------------------------------------------------------- #


def _js_retry(page, script, attempts=30):
    last = None
    for _ in range(attempts):
        try:
            return page.run_js(script)
        except Exception as exc:  # context may be replaced right after a reload
            last = exc
            time.sleep(0.1)
    raise last


def test_real_browser_recycle_keeps_command_bootstrap_js_registered(real_page, tmp_path):
    """run_js_file new-document scripts must survive a CDP recycle (and not run twice)."""
    from app.services.command_engine import CommandEngine

    page = real_page
    page.get("data:text/html,<p>boot</p>")
    script = tmp_path / "resilience.js"
    script.write_text("window.__boot = (window.__boot || 0) + 1;", encoding="utf-8")
    action = {"type": "run_js_file", "file_path": str(script), "inject_on_new_document": True, "apply_now": True}

    engine = CommandEngine()
    try:
        session = TabSession("boot", page)
        session.last_used_at = time.time() - 120
        result = engine._execute_action(dict(action), session)
        assert isinstance(result, dict) and result.get("ok"), result
        assert _js_retry(page, "return window.__boot") == 1
        page.refresh()
        assert _js_retry(page, "return window.__boot") == 1  # control: injected on reload

        assert im.recycle_session(_FakeManager([session]), session, "test") is True
        assert _js_retry(page, "return window.__boot") == 1  # not executed again in the live page
        page.refresh()
        assert _js_retry(page, "return window.__boot") == 1  # still injected after the recycle

        # the stored identifier belongs to the new session: the command can still remove it
        cleanup = engine._cleanup_run_js_file_action(session, dict(action), reason="test")
        assert cleanup["removed_init_script"] is True, cleanup
        page.refresh()
        assert _js_retry(page, "return window.__boot === undefined") is True
    finally:
        engine.shutdown()


def test_real_browser_page_check_resumes_frozen_tab_and_sees_anomaly(headed_page):
    """A frozen background tab: page_check wakes it first, then detects what the page renders."""
    from app.services.command_engine import CommandEngine

    page = headed_page
    page.get("data:text/html,<p>ok</p>")
    foreground = page.new_tab("data:text/html,<p>fg</p>")  # pushes `page` to the background
    engine = CommandEngine()
    try:
        time.sleep(0.5)
        session = TabSession("pc-frozen", page)
        session.last_used_at = time.time() - 120
        manager = _FakeManager([session])
        assert im.freeze_session(manager, session, _cfg()) is True

        # the site renders a challenge as soon as it gets to run again (tasks do not run while frozen).
        # Note: after any page_check wake a background tab is throttled by Chrome (the wake turns focus
        # emulation off again) -- same as without freezing, so only "runs at all" is asserted here.
        page.run_js(
            "window.__ran=0; setTimeout(()=>{ window.__ran=1; const d=document.createElement('div');"
            " d.textContent='Verify you are human'; document.body.appendChild(d); }, 0);"
        )
        engine._ensure_page_check_observer(session, {"Verify you are human"})  # works on a frozen page
        time.sleep(1.5)
        assert page.run_js("return window.__ran") == 0
        assert session._uwapi_frozen is True

        seen = ""
        deadline = time.time() + 10.0
        checks = 0
        while time.time() < deadline:
            seen = engine._get_page_check_snapshot_text(session)  # the periodic page_check read path
            checks += 1
            assert session._uwapi_frozen is False  # woken before reading
            if "verify you are human" in seen.lower():  # page_check snapshots are lower-cased
                break
            time.sleep(1.5)  # observer-mode page_check interval
        assert "verify you are human" in seen.lower(), (checks, seen[:200])

        # a page_check wake counts as activity: the tab is not re-frozen between checks
        assert im.freeze_session(manager, session, _cfg()) is False
        assert (time.time() - im.last_activity_at(session)) < 10
    finally:
        engine.shutdown()
        try:
            foreground.close()
        except Exception:
            pass
