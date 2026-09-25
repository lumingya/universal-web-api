"""P0-7: request history — bounded sanitize, debounced saves without fsync."""

from __future__ import annotations

import json
import os
import random
import threading
import time

import pytest

import sys

from app.services.request_manager import RequestManager

rm = sys.modules[RequestManager.__module__]


def _reference(text: str, max_chars: int) -> str:
    """Behaviour before P0-7: replace everything, then cut."""
    text = rm._DATA_URI_PATTERN.sub("[图片占位符]", text)
    text = rm._LONG_BASE64_PATTERN.sub("[图片占位符]", text)
    if len(text) > max_chars:
        return text[:max_chars]
    return text


_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _random_doc(rng: random.Random) -> str:
    pieces = []
    for _ in range(rng.randint(1, 12)):
        kind = rng.random()
        if kind < 0.45:
            pieces.append(" ".join("word%d" % rng.randint(0, 999) for _ in range(rng.randint(1, 4000))))
        elif kind < 0.65:
            pieces.append(
                "data:image/png;base64," + "".join(rng.choice(_B64) for _ in range(rng.randint(500, 60000)))
            )
        elif kind < 0.85:
            pieces.append("".join(rng.choice(_B64) for _ in range(rng.randint(4000, 40000))) + "=" * rng.randint(0, 2))
        else:
            pieces.append("中文内容" * rng.randint(1, 3000))
    return rng.choice(["\n", " ", "", "\"", ","]).join(pieces)


@pytest.mark.parametrize("seed", range(60))
def test_sanitize_matches_full_replace_then_cut(seed):
    rng = random.Random(seed)
    text = _random_doc(rng)
    max_chars = rng.choice([100, 2000, 20000, 30000, 80000])
    got = RequestManager._sanitize_text_for_storage(text, max_chars=max_chars)
    ref = _reference(text, max_chars)
    marker = "\n\n[内容已截断，原始长度 "
    if marker in got:
        body = got.split(marker, 1)[0]
        assert len(body) == max_chars
        assert body == ref, (seed, max_chars)
    else:
        assert got == ref, (seed, max_chars)


def test_text_after_image_is_kept_and_huge_prompt_is_fast():
    image = "data:image/png;base64," + "A" * 3_000_000
    tail_question = "请描述这张图片"
    text = "看图：" + image + " " + tail_question
    got = RequestManager._sanitize_text_for_storage(text, max_chars=20000)
    assert got == "看图：[图片占位符] " + tail_question == _reference(text, 20000)

    prose = ("lorem ipsum dolor sit amet " * 40000)  # ~1M chars, no media
    t0 = time.perf_counter()
    for _ in range(20):
        out = RequestManager._sanitize_text_for_storage(prose, max_chars=20000)
    per_call = (time.perf_counter() - t0) / 20
    assert out.startswith(prose[:20000]) and f"原始长度 {len(prose)} 字符" in out
    assert per_call < 0.01, per_call  # only the first ~28k chars are scanned


def _fresh_manager(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    mgr = RequestManager.__new__(RequestManager)
    mgr._initialized = False
    monkeypatch.setattr(RequestManager, "_run_zombie_sweep_loop", lambda self: None)
    RequestManager.__init__(mgr)
    monkeypatch.setattr(mgr, "_request_monitor_enabled", lambda: True)
    monkeypatch.setattr(mgr, "_request_monitor_save_to_file", lambda: True)
    return mgr


def test_history_saves_are_debounced_and_flushed(tmp_path, monkeypatch):
    # 防抖窗口留足余量：25 次调度约 0.25s，全量测试负载下曾超过 0.5s 导致偶发失败
    monkeypatch.setenv("REQUEST_HISTORY_SAVE_DEBOUNCE_SEC", "2.0")
    mgr = _fresh_manager(tmp_path, monkeypatch)
    writes = []
    real_save = mgr._save_history
    monkeypatch.setattr(mgr, "_save_history", lambda: (writes.append(time.time()), real_save()))
    fsyncs = []
    monkeypatch.setattr(os, "fsync", lambda fd: fsyncs.append(fd))

    for i in range(25):
        with mgr._history_lock:
            mgr._monitor_history.append({"request_id": f"r{i}", "created_at": time.time()})
        mgr._schedule_history_save()
        time.sleep(0.01)
    assert writes == []  # still inside the debounce window
    deadline = time.time() + 5.0
    while not writes and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)
    assert len(writes) == 1
    data = json.loads((tmp_path / "config" / "request_history.json").read_text(encoding="utf-8"))
    assert len(data["records"]) == 25

    # a save requested right before shutdown is written by flush_pending_saves
    with mgr._history_lock:
        mgr._monitor_history.append({"request_id": "last", "created_at": time.time()})
    mgr._schedule_history_save()
    t0 = time.time()
    mgr.flush_pending_saves()
    assert time.time() - t0 < 0.4
    data = json.loads((tmp_path / "config" / "request_history.json").read_text(encoding="utf-8"))
    assert data["records"][-1]["request_id"] == "last"
    assert fsyncs == []
    # 只检查本管理器自己的保存线程：全局 request_manager（其他测试发起的请求）也可能有同名线程在去抖窗口内
    worker = mgr._history_save_worker
    assert worker is None or not worker.is_alive()
