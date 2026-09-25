"""手动端到端检查：真实 TabPoolManager + 有头 Chromium 下的空闲回收 / 冻结（P0-3 / P0-6）。

不是 pytest 用例（需要 X 显示）。Linux 下运行：
    xvfb-run -a python tests/e2e_idle_maintenance_headed.py
Windows / macOS 桌面环境可直接运行。需要 Chromium（UWAPI_TEST_CHROME 或 playwright 缓存）。

期望最后一行：RESULT recycled | freed | froze | resumed
"""
import glob, http.server, os, socket, sys, tempfile, threading, time

os.environ.update({
    "BROWSER_IDLE_FREEZE_AFTER_SEC": "15",
    "BROWSER_CDP_RECYCLE_IDLE_SEC": "10",
    "BROWSER_CDP_RECYCLE_AFTER_REQUESTS": "1",
})
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PAGE = b"""<html><body><div id=root></div><script>
window.__ticks=0; setInterval(()=>{window.__ticks++},20);
</script></body></html>"""

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(PAGE))); self.end_headers(); self.wfile.write(PAGE)

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
co.set_argument("--no-sandbox"); co.set_local_port(dbg); co.set_user_data_path(tempfile.mkdtemp()); co.headless(False)
co.set_argument('--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable'); co.set_argument(f'--proxy-server=http://127.0.0.1:{port}')
browser = Chromium(co)
page = browser.latest_tab   # like app/core/browser/connection.py
page.get("http://a.example.com:8080/")
page.wait.doc_loaded(); time.sleep(0.5)
browser.new_tab("http://b.example.com:8080/")  # foreground; A now background
time.sleep(1)

def ensure_loaded(t, url):
    for _ in range(8):
        try:
            if t.run_js("return !!document.getElementById('root')"):
                return True
        except Exception:
            pass
        try:
            t.get(url)
        except Exception:
            pass
        time.sleep(0.7)
    raise RuntimeError("page did not load: " + url)

from app.core.tab_pool_parts.manager import TabPoolManager
from app.core.tab_pool_parts import idle_maintenance as im

mgr = TabPoolManager(browser_page=browser, max_tabs=3, min_tabs=1)
mgr.initialize()
print("refresh:", mgr.refresh_tabs())
sessions = list(mgr._tabs.values())
print("sessions:", [(s.id, s.tab.url) for s in sessions])
sa = next(s for s in sessions if "a.example.com" in s.tab.url)
sb = next(s for s in sessions if "b.example.com" in s.tab.url)
b_tab = sb.tab
print("idle cfg:", mgr._idle_maintenance_config)

for s_ in (sa, sb):
    s_.created_at = time.time() - 600
# --- a "request" on A that pins lots of DOM through CDP ---
assert sa.acquire("req-1")
tab = sa.tab
ensure_loaded(tab, "http://a.example.com:8080/")
ensure_loaded(b_tab, "http://b.example.com:8080/")
sb.tab.run_cdp("Target.activateTarget", targetId=sb.tab.tab_id) if hasattr(sb.tab, "tab_id") else None
print("A is the singleton tab object:", tab is page)
tab.run_js("const r=document.getElementById('root'); for(let i=0;i<3000;i++){const d=document.createElement('div');d.className='m';d.textContent='msg '+i;r.appendChild(d);}")
eles = tab.eles("css:div.m")
tab.run_js("document.getElementById('root').innerHTML=''")   # page drops them, CDP handles keep them alive
tab.run_cdp("HeapProfiler.collectGarbage")
pinned = tab.run_cdp("Memory.getDOMCounters")["nodes"]
mgr.release(sa.id, expected_task_id="req-1")
del eles
print("pinned DOM nodes after request:", pinned)

# backdate: idle long enough for recycle then freeze
for s in (sa, sb):
    s.last_used_at = time.time() - 120
def tick_and_wait():
    print("  A state:", sa.status, "idle", im._is_idle(sa), "pending", getattr(sa, "_uwapi_maint_pending", None), "since_activity %.0f" % (time.time()-im.last_activity_at(sa)), "net", getattr(sa, "_uwapi_last_network_event_at", None) and round(time.time()-sa._uwapi_last_network_event_at), "resumed", getattr(sa, "_uwapi_last_resumed_at", None))
    mgr.run_watchdog_tick()
    time.sleep(2.5)

print("recycle reason:", im._recycle_reason(sa, mgr._idle_maintenance_config, time.time()))
_l = getattr(sa.tab, "_listener", None)
print("listener before:", _l is not None and getattr(_l, "listening", None), "global workers:", list(getattr(mgr._global_network_monitor, "_workers", {}).keys()) if getattr(mgr, "_global_network_monitor", None) else None)
print("stop ->", mgr._stop_global_monitor_for_session(sa.id, reason="probe", wait=True))
print("listener after stop:", _l is not None and getattr(_l, "listening", None))
tick_and_wait()
print("after tick1: recycle_count", getattr(sa, "_uwapi_recycle_count", 0), "frozen", getattr(sa, "_uwapi_frozen", False))
sa.tab.run_cdp("HeapProfiler.collectGarbage")
after = sa.tab.run_cdp("Memory.getDOMCounters")["nodes"]
print("DOM nodes after recycle:", after)
sa.last_used_at = time.time() - 120   # recycle restores last_used_at; make sure freeze threshold is met
for _ in range(3):
    if getattr(sa, "_uwapi_frozen", False):
        break
    tick_and_wait()
print("A retry_at:", getattr(sa, "_uwapi_freeze_retry_at", None) and round(sa._uwapi_freeze_retry_at - time.time()))
print("A frozen:", getattr(sa, "_uwapi_frozen", False), " B frozen:", getattr(sb, "_uwapi_frozen", False),
      " B retry_at set:", bool(getattr(sb, "_uwapi_freeze_retry_at", 0)))
t0 = sa.tab.run_js("return window.__ticks"); time.sleep(1.0); t1 = sa.tab.run_js("return window.__ticks")
u0 = b_tab.run_js("return window.__ticks"); time.sleep(0.5); u1 = b_tab.run_js("return window.__ticks")
print(f"A ticks/1s while frozen: {t1-t0}   B ticks/0.5s: {u1-u0}")

got = mgr.acquire("req-2", timeout=5)
got2 = mgr.acquire("req-3", timeout=5)
print("acquired:", got and got.id, got2 and got2.id)
t2 = sa.tab.run_js("return window.__ticks"); time.sleep(0.5); t3 = sa.tab.run_js("return window.__ticks")
print(f"A ticks/0.5s after acquire: {t3-t2}   A frozen flag: {getattr(sa, '_uwapi_frozen', None)}   hasFocus: {sa.tab.run_js('return document.hasFocus()')}")
ok = (getattr(sa, "_uwapi_recycle_count", 0) >= 1 and after < pinned - 2500 and not sb._uwapi_frozen if hasattr(sb, "_uwapi_frozen") else True)
print("RESULT", "recycled" if getattr(sa, "_uwapi_recycle_count", 0) >= 1 else "NOT-recycled",
      "| freed" if after < pinned - 2500 else "| not-freed",
      "| froze" if t1 - t0 <= 1 else "| NOT-frozen",
      "| resumed" if t3 - t2 >= 10 else "| NOT-resumed")
for g in (got, got2):
    if g: mgr.release(g.id, expected_task_id=g.current_task_id)
mgr.shutdown()
browser.quit()
