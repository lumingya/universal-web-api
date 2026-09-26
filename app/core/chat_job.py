"""R2-2：统一的内部聊天请求（ChatJob）与类型化内部事件（ChatEvent）。

请求管线（详见 docs/architecture/chat-pipeline.md）::

    OpenAI Chat ─┐
    Anthropic ───┼─> 转换为 ChatRequest（OpenAI Chat 形状）+ ChatJob（协议、路由、请求 ID）
    Responses ───┘            │
                              ▼
               chat_completions / tab_routes（唯一的执行入口）→ 浏览器层
                              │  产出 OpenAI 形状的 SSE chunk（SSEFormatter）
                              ▼
          iter_chat_events() 解码为 ChatEvent → 各协议渲染器（Anthropic / Responses 适配器）

- ``ChatJob`` 放在上下文变量里：协议入口设置，执行入口创建 RequestContext 时据此标注协议，
  结束时按协议和状态记录指标（``uwapi_chat_requests_total``）。
- ``iter_openai_sse_payloads`` 是解码执行层输出的唯一实现（增量 UTF-8、CRLF、跨块分帧、[DONE]），
  ``events_from_openai_chunk`` 把单个 chunk 拆成类型化事件，新协议的渲染器只需要消费 ChatEvent。
"""

from __future__ import annotations

import codecs
import contextlib
import json
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

from app.core.config import get_logger

logger = get_logger("CHAT_JOB")

PROTOCOL_OPENAI_CHAT = "openai.chat"
PROTOCOL_ANTHROPIC_MESSAGES = "anthropic.messages"
PROTOCOL_OPENAI_RESPONSES = "openai.responses"
PROTOCOLS = (PROTOCOL_OPENAI_CHAT, PROTOCOL_ANTHROPIC_MESSAGES, PROTOCOL_OPENAI_RESPONSES)


@dataclass
class ChatJob:
    protocol: str
    model: str = ""
    stream: bool = False
    routing: Dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    created_at: float = field(default_factory=time.time)


CURRENT_JOB: ContextVar[Optional[ChatJob]] = ContextVar("uwapi_chat_job", default=None)


def current_chat_job() -> Optional[ChatJob]:
    return CURRENT_JOB.get()


def current_protocol() -> str:
    job = CURRENT_JOB.get()
    return job.protocol if job is not None else PROTOCOL_OPENAI_CHAT


def job_for(protocol: str, *, model: Any = "", stream: Any = False, **routing: Any) -> ChatJob:
    if protocol not in PROTOCOLS:
        raise ValueError(f"未知协议: {protocol}")
    try:
        from app.services.metrics import REQUEST_ID

        request_id = REQUEST_ID.get()
    except ImportError:
        request_id = ""
    return ChatJob(
        protocol=protocol,
        model=str(model or ""),
        stream=bool(stream),
        routing={k: v for k, v in routing.items() if v not in (None, "")},
        request_id=request_id,
    )


@contextlib.contextmanager
def use_chat_job(job: ChatJob) -> Iterator[ChatJob]:
    token = CURRENT_JOB.set(job)
    try:
        yield job
    finally:
        try:
            CURRENT_JOB.reset(token)
        except ValueError:  # 在另一个上下文里结束（例如生成器被别的任务关闭）
            CURRENT_JOB.set(None)


async def with_chat_job(job: ChatJob, iterator: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """让流式响应体在迭代期间也能看到 ChatJob（响应体在端点函数返回之后才执行）。"""
    token = CURRENT_JOB.set(job)
    try:
        async for item in iterator:
            yield item
    finally:
        try:
            CURRENT_JOB.reset(token)
        except ValueError:
            CURRENT_JOB.set(None)
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


# --------------------------------------------------------------------------- 内部事件


@dataclass
class ChatEvent:
    """类型化的内部事件。kind 取值：
    text / reasoning / tool_call（data 为 OpenAI tool_call 增量）/ media（data 为媒体项）/
    usage（data 为 usage）/ finish（data["finish_reason"]）/ error（text 为错误信息，data 为原始 error）。
    """

    kind: str
    text: str = ""
    index: int = 0
    data: Dict[str, Any] = field(default_factory=dict)


def events_from_openai_chunk(chunk: Dict[str, Any]) -> List[ChatEvent]:
    if not isinstance(chunk, dict):
        return []
    events: List[ChatEvent] = []
    error = chunk.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        events.append(ChatEvent("error", text=str(message or "unknown error"), data=error if isinstance(error, dict) else {"message": message}))
    for choice in chunk.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        index = int(choice.get("index") or 0)
        delta = choice.get("delta") or choice.get("message") or {}
        if isinstance(delta, dict):
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                events.append(ChatEvent("reasoning", text=reasoning, index=index))
            content = delta.get("content")
            if isinstance(content, str) and content:
                events.append(ChatEvent("text", text=content, index=index))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                        events.append(ChatEvent("text", text=str(part["text"]), index=index))
            for tool_call in delta.get("tool_calls") or []:
                if isinstance(tool_call, dict):
                    events.append(ChatEvent("tool_call", index=index, data=tool_call))
            for media in delta.get("media") or []:
                if isinstance(media, dict):
                    events.append(ChatEvent("media", index=index, data=media))
        if choice.get("finish_reason"):
            events.append(ChatEvent("finish", index=index, data={"finish_reason": choice["finish_reason"]}))
    if isinstance(chunk.get("usage"), dict):
        events.append(ChatEvent("usage", data=chunk["usage"]))
    return events


def _sse_data_text(segment: str) -> str:
    try:
        from app.api.openai_stop import sse_frame_data_text

        return sse_frame_data_text(segment)
    except Exception:  # broad-except: 共享解析器不可用或解析失败时回退到简单实现
        lines = [line[5:].lstrip() for line in segment.split("\n") if line.startswith("data:")]
        return "\n".join(lines)


async def iter_openai_sse_payloads(body_iterator: AsyncIterator[Any]) -> AsyncIterator[Dict[str, Any]]:
    """把执行层输出的 SSE 字节/文本流解码为 chunk 字典（跳过 [DONE] 与注释帧）。"""
    buffer = ""
    utf8_decoder = codecs.getincrementaldecoder("utf-8")("ignore")

    def decode(segment: str) -> Optional[Dict[str, Any]]:
        payload_text = _sse_data_text(segment)
        if not payload_text or payload_text.strip() == "[DONE]":
            return None
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError, RecursionError):
            logger.debug(f"无法解析 OpenAI SSE chunk: {payload_text[:200]}")
            return None
        return payload if isinstance(payload, dict) else None

    def drain() -> Iterator[Dict[str, Any]]:
        nonlocal buffer
        buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in buffer:
            segment, buffer = buffer.split("\n\n", 1)
            data = decode(segment.strip())
            if data is not None:
                yield data

    try:
        async for raw_chunk in body_iterator:
            text = utf8_decoder.decode(raw_chunk) if isinstance(raw_chunk, (bytes, bytearray)) else str(raw_chunk or "")
            if not text:
                continue
            buffer += text
            for data in drain():
                yield data
        buffer += utf8_decoder.decode(b"", final=True)
        for data in drain():
            yield data
        tail = buffer.strip()
        if tail:
            data = decode(tail)
            if data is not None:
                yield data
    finally:
        close = getattr(body_iterator, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:  # broad-except: 关闭上游流是收尾操作，失败不影响结果
                pass


async def iter_chat_events(body_iterator: AsyncIterator[Any]) -> AsyncIterator[ChatEvent]:
    async for chunk in iter_openai_sse_payloads(body_iterator):
        for event in events_from_openai_chunk(chunk):
            yield event
