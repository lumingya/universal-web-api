"""R1-4：解析器金标测试。

样本位于 ``tests/fixtures/parsers/<解析器ID>/*.json``：``raw`` 是网络监听拿到的完整响应体，
``expected`` 是期望的聚合结果。目前全部为**合成样本**（``"synthetic": true``，生成脚本见
``tests/fixtures/parsers/build_synthetic.py``），以后可以直接放入真实的脱敏样本。

每个样本按三种方式喂给解析器，三者的聚合结果都必须等于 expected：

- ``oneshot``：一次性给完整响应体；
- ``events``：按事件/分帧边界逐步给“越来越长的完整快照”（与网络监听的调用方式一致）；
- ``chunks7``：每次多 7 个字符，模拟任意位置截断的快照（考验跨块缓冲）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.parsers import ParserRegistry

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "parsers"
FIXTURES = sorted(FIXTURE_DIR.glob("*/*.json"))
MODES = ("oneshot", "events", "chunks7")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _boundaries(raw: str, mode: str) -> list[int]:
    if mode == "oneshot":
        return [len(raw)]
    if mode == "chunks7":
        return list(range(7, len(raw), 7)) + [len(raw)]
    cuts = []
    for separator in ("\r\n\r\n", "\n\n"):
        start = 0
        while (index := raw.find(separator, start)) != -1:
            cuts.append(index + len(separator))
            start = index + len(separator)
        if cuts:
            break
    if not cuts:  # 二进制分帧（如 Kimi 的 Connect 协议）：在每个帧头 \x00 之前切开
        cuts = [i for i, ch in enumerate(raw) if ch == "\x00" and i > 0]
    return sorted(set(cuts + [len(raw)]))


def _feed(parser_id: str, raw: str, mode: str) -> dict:
    parser = ParserRegistry.get(parser_id)
    parser.reset()
    content, reasoning, errors = [], [], []
    images, done = 0, False
    for end in _boundaries(raw, mode):
        result = parser.parse_chunk(raw[:end]) or {}
        content.append(result.get("content") or "")
        reasoning.append(result.get("reasoning_content") or result.get("reasoning") or "")
        images += len(result.get("images") or [])
        done = done or bool(result.get("done"))
        if result.get("error"):
            errors.append(result["error"])
    return {
        "content": "".join(content),
        "reasoning_content": "".join(reasoning),
        "images": images,
        "done": done,
        "errors": errors,
    }


def test_fixtures_exist_for_previously_untested_parsers():
    covered = {path.parent.name for path in FIXTURES}
    must_cover = {"aistudio", "claude", "qwen", "glm", "kimi", "deepseek", "chatgpt", "gemini"}
    assert must_cover <= covered, f"缺少金标样本：{sorted(must_cover - covered)}"
    for path in FIXTURES:
        payload = _load(path)
        assert payload["parser"] == path.parent.name
        assert isinstance(payload.get("synthetic"), bool), "样本必须标明是否为合成数据"
        assert ParserRegistry.exists(payload["parser"]), payload["parser"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_parser_golden(path: Path, mode: str):
    payload = _load(path)
    expected = payload["expected"]
    actual = _feed(payload["parser"], payload["raw"], mode)

    assert actual["errors"] == ([] if "error" not in expected else actual["errors"]), actual["errors"]
    assert actual["content"] == expected["content"]
    assert actual["done"] is expected.get("done", True)
    if "reasoning_content" in expected:
        assert actual["reasoning_content"] == expected["reasoning_content"]
    if "images" in expected:
        assert actual["images"] == expected["images"]
