"""手动端到端并发压测：空闲冻结（P0-6）+ 回收（P0-3）下，脚本模拟点击 / 输入是否受影响。

不是 pytest 用例（需要 X 显示）。一个进程 = 一个浏览器（独立用户目录 + 调试端口）+ 一个 TabPoolManager，
与项目部署形态一致；多浏览器并发就同时起多个进程。Linux 下：

    xvfb-run -a sh -c 'openbox & sleep 1; python tests/e2e_concurrency_headed.py --tabs 4 --rounds 6'
    # 对照组（关闭冻结）：加 --no-freeze；整窗最小化：加 --minimize

每轮：
  1. 所有标签页空闲 → 巡检把用户看不到的标签页冻结（验证计时器确实停了）；
  2. N 个线程同时 acquire，各自在拿到的标签页上做：
       项目的 cdp_precise_click（CDP 拟人鼠标）、DrissionPage ele.click + ele.input、
       CDP 按键（tab.actions）、execCommand('insertText')、human_scroll；
     页面里所有事件处理都经过 setTimeout + requestAnimationFrame 才记账——页面若仍冻结/被降频就会卡住；
  3. 校验每个动作都被页面处理、isTrusted、输入内容没有串标签页；
  全程有一个“捣乱”线程每 0.2s 把空闲标签页的空闲时间改到阈值以上并触发巡检，让冻结与 acquire 正面竞争。

最后一行输出：RESULT {json}。期望：ok == interactions、errors == 0、ticks_while_frozen 全为 0、
freeze_lifted.undetected == 0 且 sync_failed == 0；groups 里前台 / 后台的点击延迟与 --no-freeze 对照组同一量级。
UWAPI_TRACE=1 时，发现“标记冻结但页面在跑”会打印该标签页最近的 CDP 调用时间线。
"""
import argparse, http.server, json, os, random, socket, statistics, sys, tempfile, threading, time, traceback

ap = argparse.ArgumentParser()
ap.add_argument("--tabs", type=int, default=4)
ap.add_argument("--rounds", type=int, default=6)
ap.add_argument("--tag", default="B1")
ap.add_argument("--no-freeze", action="store_true")
ap.add_argument("--minimize", action="store_true", help="minimize the whole browser window between rounds")
ap.add_argument("--no-chaos", action="store_true")
args = ap.parse_args()

os.environ.update({
    "BROWSER_IDLE_FREEZE_ENABLED": "false" if args.no_freeze else "true",
    "BROWSER_IDLE_FREEZE_AFTER_SEC": "15",
    "BROWSER_CDP_RECYCLE_IDLE_SEC": "10",
    "BROWSER_CDP_RECYCLE_AFTER_REQUESTS": "3",  # 让回收也掺进来
    "LOG_LEVEL": "WARNING",
})
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PAGE = b"""<html><body style="margin:0;height:3000px">
<button id=btn style="position:fixed;left:40px;top:40px;width:220px;height:70px">send</button>
<input id=inp style="position:fixed;left:40px;top:140px;width:300px">
<div id=ce contenteditable style="position:fixed;left:40px;top:190px;width:300px;height:40px;border:1px solid #888"></div>
<script>
window.__ticks=0; setInterval(()=>{window.__ticks++},20);
window.__log=[];
function later(fn){ setTimeout(()=>requestAnimationFrame(fn), 5); }   // needs page tasks + rendering
btn.addEventListener('click', e=>{ const t=performance.now(); later(()=>__log.push({k:'click', trusted:e.isTrusted, dt:performance.now()-t})); });
inp.addEventListener('input', e=>{ later(()=>__log.push({k:'input', v:inp.value, trusted:e.isTrusted})); });
document.addEventListener('keydown', e=>{ if(e.key==='Enter') later(()=>__log.push({k:'enter', trusted:e.isTrusted})); });
ce.addEventListener('input', e=>{ later(()=>__log.push({k:'ce', v:ce.textContent})); });
window.addEventListener('wheel', e=>{ later(()=>__log.push({k:'wheel', trusted:e.isTrusted})); }, {passive:true});
</script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

from DrissionPage import ChromiumOptions, Chromium
from tests._real_browser import find_chrome

chrome = find_chrome()
if not chrome:
    sys.exit("no chromium found (set UWAPI_TEST_CHROME)")
s = socket.socket(); s.bind(("127.0.0.1", 0)); dbg = s.getsockname()[1]; s.close()
co = ChromiumOptions().set_browser_path(chrome)
co.set_argument("--no-sandbox"); co.set_local_port(dbg); co.set_user_data_path(tempfile.mkdtemp(prefix=f"uwapi-{args.tag}-"))
co.headless(False)
# 与 start.py 默认（BROWSER_MEMORY_SAVER=false）一致的后台参数；--disable-features 只能出现一次
for a in ("--disable-backgrounding-occluded-windows", "--disable-background-timer-throttling", "--disable-renderer-backgrounding"):
    co.set_argument(a)
co.set_argument("--disable-features=CalculateNativeWinOcclusion,AutomaticTabDiscarding,TabFreeze,IntensiveWakeUpThrottling,"
                "HttpsUpgrades,HttpsFirstBalancedModeAutoEnable")
co.set_argument(f"--proxy-server=http://127.0.0.1:{port}")
browser = Chromium(co)

hosts = [f"http://{args.tag.lower()}-t{i}.example.com:8080/" for i in range(args.tabs)]
first = browser.latest_tab
first.get(hosts[0])
for h in hosts[1:]:
    browser.new_tab(h)
time.sleep(1.5)


def ensure_loaded(t, url):
    for _ in range(10):
        try:
            if t.run_js("return !!document.getElementById('btn') && typeof window.__log==='object'"):
                return
        except Exception:
            pass
        try:
            t.get(url)
        except Exception:
            pass
        time.sleep(0.6)
    raise RuntimeError("page did not load: " + url)


from app.core.tab_pool_parts.manager import TabPoolManager
from app.core.tab_pool_parts import idle_maintenance as im
from app.core.tab_pool_parts.session import TabStatus
from app.utils.human_mouse import cdp_precise_click, human_scroll

_resume_times = []
_orig_resume = im.resume_if_frozen


_was_frozen = {}


def _timed_resume(session, reason=""):
    _was_frozen[getattr(session, "id", "?")] = bool(getattr(session, "_uwapi_frozen", False))
    t = time.perf_counter()
    try:
        return _orig_resume(session, reason)
    finally:
        _resume_times.append((time.perf_counter() - t) * 1000)


im.resume_if_frozen = _timed_resume

TRACE = []
if os.getenv("UWAPI_TRACE"):
    import DrissionPage._base.driver as _drv
    from DrissionPage._units.listener import Listener as _L
    _t0 = time.time()

    def _wrap(obj, name, label):
        orig = getattr(obj, name)

        def w(self, *a, **k):
            tid = getattr(self, "_target_id", None) or getattr(self, "id", None) or getattr(self, "tab_id", None) or "?"
            TRACE.append((time.time(), label + f" drv@{id(self)%10000}", str(tid)[:8], threading.current_thread().name[:18]))
            return orig(self, *a, **k)
        setattr(obj, name, w)

    _orig_run = _drv.Driver.run

    def _run(self, _method, **kw):
        m = _method
        if m not in ("Runtime.evaluate", "Runtime.callFunctionOn", "DOM.describeNode", "Runtime.releaseObject", "DOM.resolveNode"):
            TRACE.append((time.time(), "cdp " + m + (" " + str(kw.get("state") or kw.get("enabled") or "") if m in ("Page.setWebLifecycleState", "Emulation.setFocusEmulationEnabled") else "") + f" drv@{id(self)%10000}", str(getattr(self, "_target_id", None) or getattr(self, "id", "?"))[:8], threading.current_thread().name[:18]))
        return _orig_run(self, _method, **kw)
    _drv.Driver.run = _run
    _wrap(_drv.Driver, "stop", "Driver.stop")
    _wrap(_L, "start", "Listener.start")
    _wrap(_L, "stop", "Listener.stop")
    _of = im.freeze_session

    def _tf(m, s_, c):
        r = _of(m, s_, c)
        if r:
            TRACE.append((time.time(), "FROZEN " + s_.id, str(getattr(s_.tab, "tab_id", "?"))[:8], threading.current_thread().name[:18]))
        return r
    im.freeze_session = _tf
    _orr = im.recycle_tab_connection

    def _tr(tab):
        TRACE.append((time.time(), "recycle_tab_connection", str(getattr(tab, "tab_id", "?"))[:8], threading.current_thread().name[:18]))
        return _orr(tab)
    im.recycle_tab_connection = _tr

mgr = TabPoolManager(browser_page=browser, max_tabs=args.tabs, min_tabs=1)
mgr.initialize()
mgr.refresh_tabs()
sessions = list(mgr._tabs.values())
assert len(sessions) == args.tabs, [s.tab.url for s in sessions]
for s_ in sessions:
    ensure_loaded(s_.tab, s_.tab.url)
    s_.created_at = time.time() - 600
win_id = browser._run_cdp("Browser.getWindowForTarget", targetId=sessions[0].tab.tab_id)["windowId"]

stop = threading.Event()
chaos_hits = {"ticks": 0}


def make_idle(s_):
    old = time.time() - 120
    s_.last_used_at = min(s_.last_used_at, old)
    for attr in ("_uwapi_last_resumed_at", "_uwapi_last_network_event_at"):
        if getattr(s_, attr, 0.0):
            setattr(s_, attr, min(getattr(s_, attr), old))
    s_._uwapi_freeze_retry_at = 0.0


def chaos():
    """Keep idle tabs over the freeze threshold and tick maintenance: freeze races acquire."""
    while not stop.is_set():
        for s_ in list(mgr._tabs.values()):
            if im._is_idle(s_):
                make_idle(s_)
        try:
            mgr.run_watchdog_tick()
            chaos_hits["ticks"] += 1
        except Exception:
            pass
        stop.wait(0.2)


def ticks_rate(tab, dur=0.4):
    a = tab.run_js("return window.__ticks")
    time.sleep(dur)
    return tab.run_js("return window.__ticks") - a


def wait_log(tab, kinds, timeout=6.0):
    deadline = time.time() + timeout
    log = []
    while time.time() < deadline:
        log = json.loads(tab.run_js("return JSON.stringify(window.__log)") or "[]")
        if kinds <= {e["k"] for e in log}:
            return log, True
        time.sleep(0.02)
    return log, False


results, errors = [], []
res_lock = threading.Lock()


def worker(rnd, idx, barrier):
    task = f"{args.tag}-r{rnd}-w{idx}"
    try:
        barrier.wait(timeout=10)
        t0 = time.perf_counter()
        sess = mgr.acquire(task, timeout=15)
        t_acq = (time.perf_counter() - t0) * 1000
        if sess is None:
            raise RuntimeError("acquire timeout")
        tab = sess.tab
        try:
            was_frozen_during_busy = bool(getattr(sess, "_uwapi_frozen", False))
            frozen_before = _was_frozen.pop(sess.id, False)
            is_front = im._front_tab_hint(tab) == "front"
            token = f"{task}-{random.randint(1000, 9999)}"
            tab.run_js("window.__log=[]; document.getElementById('inp').value=''; document.getElementById('ce').textContent='';")
            # 1) 项目的 CDP 拟人点击
            t1 = time.perf_counter()
            cdp_precise_click(tab, 150, 75)
            clog, ok_click = wait_log(tab, {"click"})
            t_click = (time.perf_counter() - t1) * 1000
            page_dt = next((e.get("dt") for e in clog if e["k"] == "click"), None)
            steps = {}
            ts = time.perf_counter()
            # 2) DrissionPage 点击 + 输入
            inp = tab.ele("#inp")
            inp.click()
            inp.input(token)
            steps["dp_click_input"] = (time.perf_counter() - ts) * 1000; ts = time.perf_counter()
            # 3) CDP 按键
            tab.actions.key_down("ENTER").key_up("ENTER")
            steps["key"] = (time.perf_counter() - ts) * 1000; ts = time.perf_counter()
            # 4) 项目 text_input 用的 execCommand 填充
            tab.run_js(f"const c=document.getElementById('ce'); c.focus(); document.execCommand('insertText', false, {json.dumps(token)});")
            steps["exec_insert"] = (time.perf_counter() - ts) * 1000; ts = time.perf_counter()
            # 5) 项目的拟人滚动
            human_scroll(tab, 240, 200, 400)
            steps["scroll"] = (time.perf_counter() - ts) * 1000; ts = time.perf_counter()
            log, ok_all = wait_log(tab, {"click", "input", "enter", "ce", "wheel"})
            steps["wait_page"] = (time.perf_counter() - ts) * 1000
            inp_vals = [e.get("v") for e in log if e["k"] == "input"]
            ce_vals = [e.get("v") for e in log if e["k"] == "ce"]
            trusted = all(e.get("trusted", True) for e in log)
            no_mix = (inp_vals and inp_vals[-1] == token) and (ce_vals and ce_vals[-1] == token)
            total = (time.perf_counter() - t0) * 1000
            with res_lock:
                results.append({
                    "round": rnd, "tab": sess.id, "acq_ms": t_acq, "steps": steps,
                    "frozen_before": frozen_before, "front": is_front, "page_click_dt": page_dt, "click_ms": t_click, "total_ms": total,
                    "ok": bool(ok_click and ok_all and trusted and no_mix and not was_frozen_during_busy),
                    "missing": sorted({"click", "input", "enter", "ce", "wheel"} - {e["k"] for e in log}),
                    "trusted": trusted, "no_mix": bool(no_mix), "frozen_while_busy": was_frozen_during_busy,
                })
        finally:
            mgr.release(sess.id, expected_task_id=task)
    except Exception as exc:
        with res_lock:
            errors.append(f"{task}: {exc!r}\n{traceback.format_exc(limit=3)}")


froze_counts, frozen_tick_rates, recycles_before = [], [], None
lifted_stats = {"detected": 0, "undetected": 0, "sync_failed": 0}
chaos_thread = None
for rnd in range(args.rounds):
    # --- phase 1: idle → freeze ---
    if args.minimize:
        browser._run_cdp("Browser.setWindowBounds", windowId=win_id, bounds={"windowState": "minimized"})
    for s_ in sessions:
        make_idle(s_)
    for _ in range(12):
        mgr.run_watchdog_tick()
        time.sleep(0.5)
        if args.no_freeze:
            break
        target = args.tabs if args.minimize else args.tabs - 1
        if sum(bool(getattr(x, "_uwapi_frozen", False)) for x in sessions) >= target:
            break
    frozen = [x for x in sessions if getattr(x, "_uwapi_frozen", False)]
    froze_counts.append(len(frozen))
    for x in frozen:
        if im.freeze_was_lifted(x):
            # Chrome resumed it behind our back (detected); the watchdog sync must fix the bookkeeping
            lifted_stats["detected"] += 1
            im.sync_frozen_visibility(x)
            if getattr(x, "_uwapi_frozen", False):
                lifted_stats["sync_failed"] += 1
            continue
        r_ = ticks_rate(x.tab)
        frozen_tick_rates.append(r_)
        if r_ > 1:
            lifted_stats["undetected"] += 1
            print(f"DIAG undetected running-while-frozen round={rnd} tab={x.id} ticks={r_} mark={im._read_freeze_mark(x.tab)}", flush=True)
    if args.minimize:
        browser._run_cdp("Browser.setWindowBounds", windowId=win_id, bounds={"windowState": "normal"}) if rnd % 2 else None
    if chaos_thread is None and not args.no_chaos:
        chaos_thread = threading.Thread(target=chaos, daemon=True)
        chaos_thread.start()
    # --- phase 2: concurrent acquire + interactions ---
    barrier = threading.Barrier(args.tabs)
    ths = [threading.Thread(target=worker, args=(rnd, i, barrier)) for i in range(args.tabs)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=90)

stop.set()
if chaos_thread:
    chaos_thread.join(timeout=5)


def pct(vals, p):
    vals = sorted(vals)
    return round(vals[min(len(vals) - 1, int(len(vals) * p))], 1) if vals else None


summary = {
    "tag": args.tag, "mode": "no-freeze" if args.no_freeze else ("freeze+minimize" if args.minimize else "freeze"),
    "tabs": args.tabs, "rounds": args.rounds,
    "interactions": len(results), "ok": sum(r["ok"] for r in results), "errors": len(errors),
    "frozen_per_round": froze_counts, "ticks_while_frozen": frozen_tick_rates,
    "recycles": sum(int(getattr(x, "_uwapi_recycle_count", 0) or 0) for x in sessions),
    "chaos_ticks": chaos_hits["ticks"],
    "freeze_lifted": lifted_stats,
    "freeze_ineffective_caught": sum(int(getattr(x, "_uwapi_freeze_ineffective_count", 0) or 0) for x in sessions),
    "acq_ms_p50": pct([r["acq_ms"] for r in results], 0.5), "acq_ms_p95": pct([r["acq_ms"] for r in results], 0.95),
    "click_ms_p50": pct([r["click_ms"] for r in results], 0.5), "click_ms_p95": pct([r["click_ms"] for r in results], 0.95),
    "click_ms_max": pct([r["click_ms"] for r in results], 1.0),
    "total_ms_p50": pct([r["total_ms"] for r in results], 0.5), "total_ms_max": pct([r["total_ms"] for r in results], 1.0),
    "resume_ms": {"n": len(_resume_times), "p50": pct(_resume_times, 0.5), "max": pct(_resume_times, 1.0)},
    "steps_p50": {k: pct([r["steps"][k] for r in results], 0.5) for k in (results[0]["steps"] if results else {})},
    "steps_max": {k: pct([r["steps"][k] for r in results], 1.0) for k in (results[0]["steps"] if results else {})},
    "groups": {
        name: {
            "n": len(g), "click_ms_p50": pct([r["click_ms"] for r in g], 0.5), "click_ms_max": pct([r["click_ms"] for r in g], 1.0),
            "page_dt_p50": pct([r["page_click_dt"] or 0 for r in g], 0.5), "page_dt_max": pct([r["page_click_dt"] or 0 for r in g], 1.0),
            "wait_page_p50": pct([r["steps"]["wait_page"] for r in g], 0.5),
        }
        for name, g in (
            ("front", [r for r in results if r["front"]]),
            ("background_was_frozen", [r for r in results if not r["front"] and r["frozen_before"]]),
            ("background_not_frozen", [r for r in results if not r["front"] and not r["frozen_before"]]),
        ) if g
    },
    "slowest": sorted(results, key=lambda r: -r["total_ms"])[:3],
    "resume_slowest": sorted(_resume_times)[-5:],
    "bad": [r for r in results if not r["ok"]][:5],
}
for e in errors[:5]:
    print("ERROR", e)
print("RESULT " + json.dumps(summary, ensure_ascii=False))
try:
    mgr.shutdown()
finally:
    browser.quit()
