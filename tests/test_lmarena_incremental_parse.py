"""P0-5: LmarenaParser parses a growing SSE body incrementally."""

from __future__ import annotations

import json
import random
import time

import pytest

from app.core.parsers.lmarena_parser import LmarenaParser, _CP1252_TO_LATIN1


class _FullParser(LmarenaParser):
    _INCREMENTAL = False  # pre-P0-5 behaviour: re-parse the whole body every call


_LATIN1_TO_CP1252 = {v: k for k, v in _CP1252_TO_LATIN1.items()}


def _mojibake(text: str) -> str:
    """UTF-8 bytes decoded as cp1252 (+ latin-1 for cp1252 holes), as CDP sometimes delivers."""
    return "".join(chr(_LATIN1_TO_CP1252.get(b, b)) for b in text.encode("utf-8"))


_WORDS = ["hello", "世界", "💞", "naïve", "テスト", "ok", " ", "\\n", "数据", "résumé", "🚀🚀"]


def _stream(rng: random.Random):
    lines, content, reasoning, images = [], [], [], []
    for i in range(rng.randint(20, 300)):
        r = rng.random()
        if r < 0.6:
            t = "".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 6)))
            content.append(t)
            lines.append("a0:" + json.dumps(t, ensure_ascii=rng.random() < 0.3))
        elif r < 0.75:
            t = "思考" + str(i)
            reasoning.append(t)
            lines.append(rng.choice(["ag:", "bg:"]) + json.dumps(t, ensure_ascii=False))
        elif r < 0.8:
            url = f"https://img.example/{i}.png"
            images.append(url)
            lines.append("a2:" + json.dumps([{"type": "image", "image": url, "mimeType": "image/png"}]))
        elif r < 0.85:
            lines.append("")
        elif r < 0.9:
            lines.append("zz:" + json.dumps({"x": i}))
        else:
            lines.append("a0:" + json.dumps(" "))
            content.append(" ")
    lines.append('ad:{"finishReason":"stop"}')
    sep = "\r\n" if rng.random() < 0.3 else "\n"
    return sep.join(lines) + sep, "".join(content), "".join(reasoning), images


def _feed(parser, body, cuts):
    results = []
    for cut in cuts:
        results.append(parser.parse_chunk(body[:cut]))
    return results


def _cuts(rng, n):
    points = sorted({rng.randint(1, n) for _ in range(rng.randint(3, 40))} | {n})
    if rng.random() < 0.3:  # repeated identical snapshots happen when polling
        points = sorted(points + points[: len(points) // 2])
    return points


def _summary(results):
    return (
        "".join(r["content"] for r in results),
        "".join(r["reasoning_content"] for r in results),
        [img.get("url") for r in results for img in r["images"]],
        results[-1]["done"],
        results[-1]["error"],
    )


@pytest.mark.parametrize("seed", range(80))
def test_incremental_equals_full_parse_per_call(seed):
    rng = random.Random(seed)
    body, content, reasoning, images = _stream(rng)
    cuts = _cuts(rng, len(body))
    inc = _feed(LmarenaParser(), body, cuts)
    full = _feed(_FullParser(), body, cuts)
    for a, b in zip(inc, full):
        strip = lambda imgs: [{k: v for k, v in img.items() if k != "detected_at"} for img in imgs]
        assert (a["content"], a["reasoning_content"], strip(a["images"]), a["done"], a["error"]) == (
            b["content"], b["reasoning_content"], strip(b["images"]), b["done"], b["error"]
        )
    assert _summary(inc) == (content, reasoning, images, True, None)


@pytest.mark.parametrize("seed", range(40))
def test_incremental_mojibake_stream_recovers_true_text(seed):
    rng = random.Random(1000 + seed)
    body, content, reasoning, images = _stream(rng)
    garbled = _mojibake(body)
    cuts = _cuts(rng, len(garbled))  # cuts may split multi-byte sequences
    inc = _summary(_feed(LmarenaParser(), garbled, cuts))
    assert inc == (content, reasoning, images, True, None)


def test_old_whole_body_mojibake_fix_corrupted_mid_character_snapshots():
    """Regression: the whole-body _fix_mojibake failed whenever a snapshot ended inside a
    multi-byte sequence, leaking garbled text into the answer. Complete lines are always
    decodable, so the incremental parser does not have this problem."""
    corrupted = 0
    for seed in range(40):
        rng = random.Random(1000 + seed)
        body, content, _reasoning, _images = _stream(rng)
        garbled = _mojibake(body)
        cuts = _cuts(rng, len(garbled))
        if _summary(_feed(_FullParser(), garbled, cuts))[0] != content:
            corrupted += 1
        assert _summary(_feed(LmarenaParser(), garbled, cuts))[0] == content
    assert corrupted > 0  # the old behaviour really was broken on these inputs


def test_non_prefix_body_falls_back_to_full_scan():
    parser = LmarenaParser()
    first = 'a0:"hello "\na0:"wor'
    assert parser.parse_chunk(first)["content"] == "hello "
    # a different stream (not an extension): nothing duplicated, new text found
    other = 'a0:"hello "\na0:"there"\nad:{"finishReason":"stop"}\n'
    result = parser.parse_chunk(other)
    assert result["content"] == "there" and result["done"] is True
    parser.reset()
    assert parser._inc_state is None


def test_error_line_and_partial_tail():
    parser = LmarenaParser()
    r1 = parser.parse_chunk('a0:"x"\nae:{"message":"quota')  # partial error line: reported, not cached
    assert r1["error"] and r1["done"] is True
    assert parser._inc_state.committed_len == len('a0:"x"\n')
    r2 = parser.parse_chunk('a0:"x"\nae:{"message":"quota exceeded"}\n')
    assert r2["error"] == "quota exceeded"


def test_incremental_is_much_faster_on_long_streams():
    lines = ["a0:" + json.dumps("这是第%d段回答内容，包含一些文字。 " % i, ensure_ascii=False) for i in range(4000)]
    body = "\n".join(lines) + "\n"
    step = len(body) // 300
    cuts = [k * step for k in range(1, 301)] + [len(body)]

    t0 = time.perf_counter()
    inc = _summary(_feed(LmarenaParser(), body, cuts))
    t_inc = time.perf_counter() - t0
    t0 = time.perf_counter()
    full = _summary(_feed(_FullParser(), body, cuts))
    t_full = time.perf_counter() - t0
    assert inc == full
    assert t_inc * 5 < t_full, (t_inc, t_full)
