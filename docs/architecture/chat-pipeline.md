# 聊天请求管线（R2-2）

## 总览

```
客户端协议                  入口（转换请求）                         执行（唯一入口）              输出（按协议渲染）
────────────────────────────────────────────────────────────────────────────────────────────────────────────────
OpenAI Chat      /v1/chat/completions、/tab/…/v1、/url/…/v1 ─┐
Anthropic        /v1/messages、/url/…/v1/messages ─→ 转 ChatRequest ├─→ chat_completions / tab_routes ─→ 浏览器层
OpenAI Responses /v1/responses ─→ 转 ChatRequest ───────────────┘        │ 产出 OpenAI 形状的 SSE chunk
                                                                          ▼
                                             iter_openai_sse_payloads / iter_chat_events（app/core/chat_job.py）
                                                                          ▼
                                   OpenAI：原样透传 │ Anthropic：_anthropic_stream_from_openai │ Responses：_stream_responses_compat
```

- **内部请求**是 `ChatRequest`（OpenAI Chat 形状）加上 `ChatJob`。`ChatJob` 记录协议、模型、是否流式、路由目标和 `X-Request-ID`。
  - 协议入口用 `use_chat_job(job_for(...))` 设置 ChatJob；流式响应体在端点函数返回之后才执行，所以要用 `with_chat_job(job, 生成器)` 包起来。
  - 执行入口 `request_manager.create_request()` 据此设置 `RequestContext.protocol`，`finish_request()` 再按协议和最终状态记录 `uwapi_chat_requests_total` 与 `uwapi_chat_request_duration_seconds`。
- **内部事件**：执行层的输出统一由 `iter_openai_sse_payloads` 解码（处理增量 UTF-8、CRLF、跨块分帧、注释帧和 `[DONE]`），再由 `events_from_openai_chunk` 拆成类型化的 `ChatEvent`：

  | kind | 含义 | 字段 |
  |------|------|------|
  | `text` | 正文增量 | `text` |
  | `reasoning` | 思考过程增量 | `text` |
  | `tool_call` | 工具调用增量（OpenAI tool_call 形状） | `data` |
  | `media` | 生成的图片、视频等 | `data` |
  | `usage` | 用量 | `data` |
  | `finish` | 结束原因 | `data["finish_reason"]` |
  | `error` | 错误 | `text`、`data` |

## 新增一种客户端协议

1. **请求转换**：把新协议的请求转换成 `ChatRequest`，可参考 `anthropic_routes._anthropic_request_to_openai_payload`。
2. **入口**：新建路由，调用 `chat_api.chat_completions(...)`（或按域名、标签页路由的 `tab_routes` 处理函数），并用 `use_chat_job(job_for("新协议名", ...))` 包住。
   - 同时把协议名加入 `app/core/chat_job.PROTOCOLS`。
3. **输出渲染**：流式时用 `iter_chat_events(response.body_iterator)` 消费 `ChatEvent`，渲染成新协议的事件；非流式时把 OpenAI 形状的 JSON 结果转换过去。
   - 工具调用、停止序列、媒体、会话续接等能力由执行层统一实现，渲染器只做格式映射。
4. **测试**：
   - 在 `tests/test_protocol_conformance.py` 里用该协议的官方 SDK 走一遍流式和非流式；
   - 在 `tests/test_chat_job.py::test_each_protocol_is_tagged_and_counted` 里加上该协议。

## 现状与后续

- 现有的 Anthropic 与 Responses 渲染器是两个成熟的状态机（约 1300 行），覆盖工具调用增量、停止序列、媒体、用量、错误映射等细节，已有官方 SDK 一致性测试保护。本次**没有重写**它们，只统一了解码入口；Anthropic 适配器的 `_iter_openai_stream_chunks` 已是共享解码器的别名。
- 下一步可以让浏览器层直接产出 `ChatEvent`，省掉「序列化成 SSE 再解码」这一步；渲染器届时只需改为消费事件。这一步与 R2-1（BrowserDriver 接口）一起做更合适。
