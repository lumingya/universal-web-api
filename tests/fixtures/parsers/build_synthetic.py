#!/usr/bin/env python3
"""生成解析器金标测试用的**合成**网络流样本（R1-4）。

这些样本不是真实抓包：它们按各解析器源码注释里记录的线上格式（以及公开可查的协议格式）构造，
目的是锁定解析行为、验证增量解析与一次性解析结果一致，并在阶段 2 的大重构中防止回归。
拿到真实的脱敏样本后，可以放在同一目录，文件里把 "synthetic" 设为 false 即可，测试框架不需要改。

用法：python tests/fixtures/parsers/build_synthetic.py   （会覆盖本目录下的 *.json）
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sse(events, *, newline="\n"):
    """events: [(event_name or None, payload)]；payload 为 dict/list 时转 JSON，str 原样写入 data。"""
    blocks = []
    for name, payload in events:
        data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        lines = ([f"event: {name}"] if name else []) + [f"data: {data}"]
        blocks.append(newline.join(lines) + newline + newline)
    return "".join(blocks)


def save(parser, case, description, raw, expected, source):
    target = HERE / parser / f"{case}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "parser": parser,
        "synthetic": True,
        "source": source,
        "description": description,
        "raw": raw,
        "expected": expected,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------- Claude
CLAUDE_EVENTS = [
    ("message_start", {"type": "message_start", "message": {"id": "msg_syn", "type": "message", "role": "assistant", "content": []}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "先确认单位，"}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "再计算。"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "答案是 "}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "42。\n\n```python\nprint(42)\n```"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
    ("message_stop", {"type": "message_stop"}),
]
CLAUDE_EXPECTED = {"content": "答案是 42。\n\n```python\nprint(42)\n```", "reasoning_content": "先确认单位，再计算。", "done": True}

# --------------------------------------------------------------------------- Qwen
QWEN_EVENTS = [
    (None, {"response.created": {"chat_id": "chat_syn", "response_id": "resp_syn"}}),
    (None, {"choices": [{"delta": {"role": "assistant", "content": "", "phase": "thinking_summary",
                                   "extra": {"summary_thought": {"content": ["分析用户的问题"]}}}}]}),
    (None, {"choices": [{"delta": {"role": "assistant", "content": "你好", "phase": "answer", "status": "typing"}}]}),
    (None, {"choices": [{"delta": {"content": "，世界！", "phase": "answer", "status": "typing"}}]}),
    (None, {"choices": [{"delta": {"content": "", "phase": "answer", "status": "finished"}}]}),
]

# --------------------------------------------------------------------------- GLM（全文快照，而不是增量）
def _glm(parts_content, status):
    return {"id": "syn", "conversation_id": "conv_syn", "status": status,
            "parts": [{"id": "p1", "logic_id": "l1", "role": "assistant", "status": status, "content": parts_content}]}


GLM_EVENTS = [
    (None, _glm([{"type": "text", "text": "你好", "status": "init"}], "init")),
    (None, _glm([{"type": "text", "text": "你好，我是", "status": "init"}], "init")),
    (None, _glm([{"type": "text", "text": "你好，我是 GLM。", "status": "finish"}], "finish")),
]

# --------------------------------------------------------------------------- Kimi（Connect 分帧：5 字节头 + JSON）
def _kimi_frames(messages):
    raw = []
    for message in messages:
        body = json.dumps(message, ensure_ascii=False)
        header = bytes([0]) + len(body.encode("utf-8")).to_bytes(4, "big")
        raw.append("".join(chr(b) for b in header) + body)
    return "".join(raw)


KIMI_MESSAGES = [
    {"op": "set", "mask": "block.text", "block": {"id": "b1", "text": {"content": "你好"}}},
    {"op": "append", "mask": "block.text.content", "block": {"id": "b1", "text": {"content": "，我是 Kimi"}}},
    {"op": "append", "mask": "block.text.content", "block": {"id": "b1", "text": {"content": "。"}}},
    {"op": "set", "mask": "message.status", "message": {"status": "MESSAGE_STATUS_COMPLETED"}},
    {"done": {}},
]

# --------------------------------------------------------------------------- DeepSeek（p/o/v 补丁操作）
DEEPSEEK_EVENTS = [
    (None, {"v": {"response": {"message_id": 2, "parent_id": 1, "role": "ASSISTANT", "thinking_enabled": True,
                               "fragments": [{"id": 1, "type": "THINK", "content": "嗯，", "references": []}],
                               "status": "WIP"}}}),
    (None, {"p": "response/fragments/-1/content", "o": "APPEND", "v": "先想一下"}),
    (None, {"v": "。"}),
    (None, {"p": "response/fragments", "o": "APPEND", "v": [{"id": 2, "type": "RESPONSE", "content": "答案", "references": []}]}),
    (None, {"p": "response/fragments/-1/content", "v": "是 42。"}),
    (None, {"p": "response/status", "o": "SET", "v": "FINISHED"}),
    ("finish", {}),
]

# --------------------------------------------------------------------------- ChatGPT（v1 delta 编码）
CHATGPT_EVENTS = [
    ("delta_encoding", '"v1"'),
    ("delta", {"p": "", "o": "add", "v": {"message": {"id": "m_syn", "author": {"role": "assistant"},
                                                     "content": {"content_type": "text", "parts": [""]},
                                                     "status": "in_progress", "metadata": {}},
                                         "conversation_id": "c_syn"}, "c": 0}),
    ("delta", {"p": "/message/content/parts/0", "o": "append", "v": "Hello"}),
    ("delta", {"v": ", world"}),
    ("delta", {"p": "", "o": "patch", "v": [{"p": "/message/content/parts/0", "o": "append", "v": "!"},
                                             {"p": "/message/status", "o": "replace", "v": "finished_successfully"}]}),
    (None, "[DONE]"),
]

# --------------------------------------------------------------------------- Gemini（StreamGenerate：)]}' + 长度前缀 + 累积文本）
def _gemini_chunk(text):
    inner = [None, ["c_syn", "r_syn"], None, None, [["rc_syn", [text]]]]
    return json.dumps([["wrb.fr", None, json.dumps(inner, ensure_ascii=False)]], ensure_ascii=False)


def _gemini_body(texts):
    chunks = [_gemini_chunk(t) for t in texts] + [json.dumps([["di", 42], ["af.httprm", 41, "-123", 7]])]
    return ")]}'\n\n" + "".join(f"{len(c)}\n{c}\n" for c in chunks)


# --------------------------------------------------------------------------- AI Studio（json+protobuf 嵌套数组，增量文本）
def _aistudio_body():
    think = [None, "**Thinking about units**"] + [None] * 10 + [1]      # len>=13 且 [12]==1 -> 思考过程
    text_block = lambda ca: [[[[[ca]]]]]                               # block[0][0][0][0][0] == content_arr
    final_block = lambda ca: [[[[[ca]], 1]]]                           # level2[1] == 1 -> 结束
    stats = [None, None, None, [1727000000, 5, 7]]                    # 统计块 -> 结束
    return json.dumps([text_block(think), text_block([None, "你好"]), final_block([None, "，世界！"]), stats],
                      ensure_ascii=False)


def main():
    save("claude", "thinking_then_text", "Extended Thinking + 正文 + message_stop", sse(CLAUDE_EVENTS), CLAUDE_EXPECTED,
         "按 Anthropic Messages 流式协议（claude.ai 网页端同构）合成")
    save("claude", "thinking_then_text_crlf", "同上，但使用 CRLF 行尾", sse(CLAUDE_EVENTS, newline="\r\n"), CLAUDE_EXPECTED,
         "按 Anthropic Messages 流式协议合成（CRLF 变体）")
    save("qwen", "summary_then_answer", "thinking_summary 后输出 answer，status=finished 结束", sse(QWEN_EVENTS),
         {"content": "你好，世界！", "done": True}, "按 qwen_parser.py 注释记录的 /api/v2/chat/completions SSE 格式合成")
    save("glm", "snapshot_text", "text 为全文快照，parts status=finish 结束", sse(GLM_EVENTS),
         {"content": "你好，我是 GLM。", "done": True}, "按 glm_parser.py 注释记录的 assistant/stream SSE 格式合成")
    save("kimi", "connect_frames", "Connect 分帧：set + append + MESSAGE_STATUS_COMPLETED", _kimi_frames(KIMI_MESSAGES),
         {"content": "你好，我是 Kimi。", "done": True}, "按 kimi_parser.py 注释记录的 Connect(+json) 分帧格式合成")
    save("deepseek", "think_then_response", "THINK 片段不进正文，RESPONSE 片段为正文，status=FINISHED 结束", sse(DEEPSEEK_EVENTS),
         {"content": "答案是 42。", "done": True}, "按 deepseek_parser.py 注释记录的 fragments/p-o-v 补丁格式合成")
    save("chatgpt", "v1_delta", "v1 delta 编码：add 快照 + append + 隐式续写 + patch + [DONE]", sse(CHATGPT_EVENTS),
         {"content": "Hello, world!", "done": True}, "按 chatgpt_parser.py 注释记录的 v1 delta 编码合成")
    save("gemini", "cumulative_chunks", "每块返回从头开始的累积文本，di/af.httprm 块结束", _gemini_body(["你好", "你好，世界", "你好，世界！"]),
         {"content": "你好，世界！", "done": True}, "按 gemini_parser.py 注释记录的 StreamGenerate 格式合成")

    save("aistudio", "thinking_then_text", "思考块 + 两个正文块（第二块 level2[1]=1）+ 统计块", _aistudio_body(),
         {"content": "你好，世界！", "reasoning_content": "**Thinking about units**", "done": True},
         "按 aistudio_parser.py 注释记录的 GenerateContent 嵌套数组格式合成")


if __name__ == "__main__":
    main()
