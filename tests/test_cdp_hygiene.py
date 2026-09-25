"""P0-2 / P0-4: CDP resource hygiene.

* ``parse_js_result`` wrapper releases non-node RemoteObjects.
* ``Network.enable`` receives buffer caps.
* Guard: polled ``run_js`` scripts in hot paths must return JSON strings,
  never raw objects/arrays (which leak RemoteObjects in DrissionPage).
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest

from app.core import cdp_hygiene
from tests._real_browser import launch_page


ROOT = pathlib.Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# unit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"type": "object", "objectId": "1"}, "1"),
        ({"type": "object", "subtype": "array", "objectId": "2"}, "2"),
        ({"type": "object", "subtype": "node", "objectId": "3"}, None),
        ({"type": "object", "subtype": "null"}, None),
        ({"type": "object", "className": "HTMLDocument", "objectId": "4"}, None),
        ({"type": "string", "value": "x"}, None),
        ({"type": "object"}, None),
        (None, None),
    ],
)
def test_should_release_classification(result, expected):
    assert cdp_hygiene._should_release(result) == expected


def test_install_is_idempotent_and_patches_drissionpage():
    from DrissionPage._base.driver import Driver
    from DrissionPage._elements import chromium_element as ce

    assert cdp_hygiene.install() is True
    assert cdp_hygiene.install() is True
    assert getattr(ce.parse_js_result, "_uwapi_release_wrapper", False)
    assert getattr(Driver.run, "_uwapi_network_limit_wrapper", False)
    # never double-wrapped
    assert not getattr(ce.parse_js_result._uwapi_original, "_uwapi_release_wrapper", False)


def test_parse_wrapper_releases_object_after_parsing():
    from DrissionPage._elements import chromium_element as ce

    cdp_hygiene.install()
    calls = []

    class _Driver:
        def run(self, method, **kwargs):
            calls.append((method, kwargs))
            if method == "Runtime.callFunctionOn":
                return {"result": {"type": "string", "value": json.dumps({"a": 1})}}
            return {}

    class _Page:
        driver = _Driver()
        _driver = driver

        def _run_cdp(self, method, **kwargs):
            return self.driver.run(method, **kwargs)

    page = _Page()
    result = {"type": "object", "objectId": "obj-1", "className": "Object"}
    try:
        value = ce.parse_js_result(page, None, result, 9e18)
    except Exception:
        value = None  # DrissionPage internals may differ; release must still happen
    methods = [m for m, _ in calls]
    assert "Runtime.releaseObject" in methods
    release_kwargs = dict(calls[methods.index("Runtime.releaseObject")][1])
    assert release_kwargs["objectId"] == "obj-1"
    if value is not None:
        assert value == {"a": 1}


def test_network_enable_limits_env(monkeypatch):
    monkeypatch.delenv("CDP_NETWORK_MAX_TOTAL_BUFFER_MB", raising=False)
    monkeypatch.delenv("CDP_NETWORK_MAX_RESOURCE_BUFFER_MB", raising=False)
    limits = cdp_hygiene.network_enable_limits()
    assert limits["maxTotalBufferSize"] == 32 * 1024 * 1024
    assert limits["maxResourceBufferSize"] == 16 * 1024 * 1024
    assert "maxPostDataSize" not in limits  # arena bridge needs full postData

    monkeypatch.setenv("CDP_NETWORK_MAX_TOTAL_BUFFER_MB", "0")
    monkeypatch.setenv("CDP_NETWORK_MAX_RESOURCE_BUFFER_MB", "0")
    assert cdp_hygiene.network_enable_limits() == {}


def test_network_enable_wrapper_injects_limits():
    from DrissionPage._base.driver import Driver

    cdp_hygiene.install()
    seen = {}
    original = Driver.run._uwapi_original

    def fake_original(self, _method, sessionId=None, **kwargs):
        seen[_method] = kwargs
        return {}

    wrapper = Driver.run
    try:
        # re-create a wrapper around a fake transport to observe kwargs
        Driver.run = original
        Driver.run = _rewrap(fake_original)
        drv = Driver.__new__(Driver)
        drv.run("Network.enable")
        drv.run("Network.enable", maxTotalBufferSize=1)
        assert seen["Network.enable"]["maxTotalBufferSize"] == 1
        drv.run("Page.enable")
        assert "maxTotalBufferSize" not in seen["Page.enable"]
    finally:
        Driver.run = wrapper



@pytest.mark.parametrize("style", ["drission_4_0", "drission_4_1_1"])
def test_network_enable_wrapper_passes_arguments_through_unchanged(style):
    """Old DrissionPage (4.0.x-4.1.0.x) puts every kwarg into CDP params: never inject sessionId=None."""
    seen = []

    if style == "drission_4_0":
        def fake_original(self, _method, **kwargs):  # params = kwargs
            seen.append((_method, dict(kwargs)))
            return {}
    else:
        def fake_original(self, _method, sessionId=None, **kwargs):
            seen.append((_method, dict(kwargs, _sid=sessionId)))
            return {}

    from DrissionPage._base.driver import Driver

    wrapper = Driver.run
    try:
        Driver.run = _rewrap(fake_original)
        drv = Driver.__new__(Driver)
        drv.run("Page.navigate", url="about:blank")
        drv.run("Runtime.releaseObject", objectId="1")
        drv.run("Network.enable")
        if style == "drission_4_0":
            assert all("sessionId" not in kw for _, kw in seen), seen
        else:
            assert all(kw["_sid"] is None for _, kw in seen)
            drv.run("Page.enable", sessionId="S1")
            assert seen[-1][1]["_sid"] == "S1"
        assert seen[2][1]["maxTotalBufferSize"] > 0
    finally:
        Driver.run = wrapper

def _rewrap(fake_original):
    from DrissionPage._base.driver import Driver

    saved = Driver.run
    Driver.run = fake_original
    try:
        cdp_hygiene._patch_network_enable()
        return Driver.run
    finally:
        Driver.run = saved


def test_json_helpers_round_trip():
    wrapped = cdp_hygiene.wrap_js_json("return {a: arguments[0]};")
    from DrissionPage._functions.web import is_js_func

    assert is_js_func(wrapped)
    assert cdp_hygiene.decode_js_json('{"a": 1}') == {"a": 1}
    assert cdp_hygiene.decode_js_json({"a": 1}) == {"a": 1}  # test doubles
    assert cdp_hygiene.decode_js_json("", default={}) == {}
    assert cdp_hygiene.decode_js_json("nope", default=None) is None


# --------------------------------------------------------------------------- #
# guard: hot polling scripts must not return raw objects
# --------------------------------------------------------------------------- #

# (file, function names whose run_js scripts are polled)
HOT_PATHS = {
    "app/core/stream_monitor.py": None,  # every run_js in the monitor
    "app/core/stream_snapshot.py": None,
    "app/services/arena_tab_listener.py": None,
    "app/core/page_lifecycle.py": {"install_visibility_emulation"},
    "app/core/page_capture/kimi_fetch_capture.py": {"get_state"},
    "app/services/arena_image_generation.py": {
        "get_arena_generation_status",
        "evaluate_arena_direct_generation_state",
        "capture_arena_result_baseline",
        "detect_terminal_error",
    },
}

_RAW_RETURN = re.compile(r"(^|[;{}\n])\s*return\s*[\{\[]")
_SAFE_WRAPPERS = {"_json_js", "wrap_js_json", "_ARENA_STORE_SNAPSHOT_JSON_JS"}


def _script_for(arg, assigns):
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.Name):
        if arg.id in _SAFE_WRAPPERS or arg.id == "SNAPSHOT_JS":
            return None
        return assigns.get(arg.id)
    if isinstance(arg, ast.Call):
        name = getattr(arg.func, "id", None) or getattr(arg.func, "attr", None)
        if name in _SAFE_WRAPPERS:
            return None
    return None


def _top_level_returns_raw(script: str) -> bool:
    """True when the script's own return (not a nested helper) yields an object."""
    if "JSON.stringify" in script:
        return False
    stripped = script.strip()
    # IIFE style: ``return ((a) => {...})(...)`` → inspect the IIFE body returns
    return bool(_RAW_RETURN.search(stripped))


def _iter_run_js_calls(tree):
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assigns = {}
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigns[target.id] = node.value.value
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run_js"
                and node.args
            ):
                yield func.name, node, assigns


def test_hot_polling_scripts_return_json_strings():
    offenders = []
    for rel, functions in HOT_PATHS.items():
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func_name, call, assigns in _iter_run_js_calls(tree):
            if functions is not None and func_name not in functions:
                continue
            script = _script_for(call.args[0], assigns)
            if script and _top_level_returns_raw(script):
                offenders.append(f"{rel}:{call.lineno} ({func_name})")
    assert not offenders, (
        "run_js scripts on polling paths must return JSON.stringify(...) "
        "(raw objects/arrays leak RemoteObjects): " + ", ".join(offenders)
    )


def test_hygiene_installed_on_browser_connection_import():
    import app.core.browser.connection  # noqa: F401
    from DrissionPage._elements import chromium_element as ce

    assert getattr(ce.parse_js_result, "_uwapi_release_wrapper", False)


# --------------------------------------------------------------------------- #
# real browser: released ids really are gone; nodes stay usable
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def real_page():
    yield from launch_page()


def test_real_browser_object_results_are_released(real_page, monkeypatch):
    cdp_hygiene.install()
    released = []
    original_release = cdp_hygiene._release_object

    def spy(page, object_id):
        released.append(object_id)
        original_release(page, object_id)

    monkeypatch.setattr(cdp_hygiene, "_release_object", spy)
    real_page.get("data:text/html,<div id=a>hello</div><div class=b>x</div><div class=b>y</div>")

    assert real_page.run_js("return {a: 1, b: [1, 2]};") == {"a": 1, "b": [1, 2]}
    assert real_page.run_js("return [1, 'x'];") == [1, "x"]
    assert len(released) == 2

    for object_id in released:
        with pytest.raises(Exception):
            real_page.run_cdp("Runtime.callFunctionOn", objectId=object_id, functionDeclaration="function(){return 1}")

    # node results are still live elements
    ele = real_page.run_js("return document.getElementById('a');")
    assert ele.text == "hello"
    nodes = real_page.run_js("return Array.from(document.querySelectorAll('.b'));")
    assert [n.text for n in nodes] == ["x", "y"]
    # string results never create objects
    before = len(released)
    assert real_page.run_js("return JSON.stringify({k: 1});") == '{"k": 1}'.replace(" ", "")
    assert len(released) == before


def test_real_browser_network_enable_accepts_limits(real_page):
    cdp_hygiene.install()
    real_page.listen.start(targets=True)
    try:
        real_page.get("data:text/html,<p>ok</p>")
    finally:
        real_page.listen.stop()
    assert cdp_hygiene.get_stats().get("network_enable_limited", 0) >= 1


# --------------------------------------------------------------------------- #
# P0-4: patched listener accumulates fullText lazily
# --------------------------------------------------------------------------- #


def test_stream_capture_patch_lazy_full_text(tmp_path):
    import importlib.util
    import shutil
    import subprocess
    import sys

    import DrissionPage

    src = pathlib.Path(DrissionPage.__file__).parent
    dst = tmp_path / "site" / "DrissionPage"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.bak"))
    listener = dst / "_units" / "listener.py"
    backup = listener.with_suffix(".py.bak")
    if backup.exists():
        backup.unlink()
    # start from the pristine upstream listener when the installed one is patched
    pristine = src / "_units" / "listener.py.bak"
    if pristine.exists():
        shutil.copy2(pristine, listener)

    spec = importlib.util.spec_from_file_location("uwapi_patch_drissionpage", ROOT / "patch_drissionpage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.apply_patch(listener)
    content = listener.read_text(encoding="utf-8")
    assert module.check_already_patched(content)
    assert "current + text" not in content

    script = r"""
import copy, json, sys
from DrissionPage._units.listener import DataPacket
p = DataPacket('tab', True)
s = p._stream
assert type(s).__name__ == '_ListenerStreamStateV1'
for part in ['a', 'b', '\u4f60', 'c']:
    s.append_text(part)
assert s.full_text_length() == 4
assert s['fullText'] == 'ab\u4f60c'
assert s.get('fullText') == 'ab\u4f60c'
s.append_text('d')
assert json.loads(json.dumps(s))['fullText'] == 'ab\u4f60cd'
assert copy.deepcopy(s)['fullText'] == 'ab\u4f60cd'
s['fullText'] = 'reset'
assert s['fullText'] == 'reset'

# CDP event thread appends while a monitor thread merges parts inside _joined().
# Deterministic interleaving: pause the reader right before it replaces the parts list,
# let the writer append, then resume. Without the lock that chunk is lost.
import linecache, threading
st = DataPacket('tab', True)._stream
st.append_text('a'); st.append_text('b')
at_assign, appended = threading.Event(), threading.Event()
def tracer(frame, event, arg):
    if frame.f_code.co_name != '_joined':
        return tracer
    if event == 'line' and linecache.getline(frame.f_code.co_filename, frame.f_lineno).strip() == 'self._parts = [joined]':
        at_assign.set()
        appended.wait(0.5)   # locked implementation: writer is blocked, so this times out
    return tracer
def reader():
    sys.settrace(tracer)
    try:
        st.get('fullText')
    finally:
        sys.settrace(None)
def writer():
    at_assign.wait(5)
    st.append_text('c')
    appended.set()
rt, wt = threading.Thread(target=reader), threading.Thread(target=writer)
rt.start(); wt.start(); rt.join(10); wt.join(10)
assert at_assign.is_set(), 'reader never reached the merge step'
assert st['fullText'] == 'abc', st['fullText']
print('OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**__import__("os").environ, "PYTHONPATH": str(tmp_path / "site")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "OK" in result.stdout


def test_stream_capture_patch_upgrades_previous_snippet_without_duplicating(tmp_path):
    import importlib.util
    import shutil

    import DrissionPage

    src = pathlib.Path(DrissionPage.__file__).parent / "_units" / "listener.py"
    pristine = src.with_suffix(".py.bak")
    listener = tmp_path / "listener.py"
    shutil.copy2(pristine if pristine.exists() else src, listener)

    spec = importlib.util.spec_from_file_location("uwapi_patch_drissionpage_up", ROOT / "patch_drissionpage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.apply_patch(listener)
    content = listener.read_text(encoding="utf-8")
    assert module.check_already_patched(content)

    # simulate a machine that still has the previous (lock-less) snippet installed
    old = content.replace("_uwapi_stream_lock_v2", "old_snippet_without_lock")
    listener.write_text(old, encoding="utf-8")
    assert not module.check_already_patched(old)
    assert module.apply_patch(listener)
    upgraded = listener.read_text(encoding="utf-8")
    assert module.check_already_patched(upgraded)
    assert upgraded.count("class _ListenerStreamStateV1(dict)") == 1
    assert "old_snippet_without_lock" not in upgraded
