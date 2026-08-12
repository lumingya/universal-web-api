# GitHub Similar Projects Research

Date: 2026-08-12

## Scope

Universal Web API converts logged-in local browser sessions into OpenAI- and
Anthropic-compatible APIs. This review covers projects with the same local
browser/session bridge direction. It intentionally excludes approaches based
on credential extraction, authentication bypass, or CAPTCHA solving.

## Comparable Projects

| Project | Relevant ideas | Fit for this project |
| --- | --- | --- |
| [guberm/chatgpt-web-provider](https://github.com/guberm/chatgpt-web-provider) | Machine-readable provider capabilities and status, dedicated profile, request queue, mock backend, redaction | High |
| [BinaryBeastMaster/chat-relay](https://github.com/BinaryBeastMaster/chat-relay) | API server to browser-extension WebSocket transport, request ID correlation, ping/timeout, provider modules | Medium, future transport abstraction |
| [agentify-sh/desktop](https://github.com/agentify-sh/desktop) | Stable tab keys, MCP tools, artifact persistence, isolated browser profile policy | Medium, future developer workflow |
| [xiaoxihexiaoyu/AIClient-2-API](https://github.com/xiaoxihexiaoyu/AIClient-2-API) | Provider pools, health checks, failover and visual operations | Medium, operations reference only |
| [Zen4-bit/Proxima](https://github.com/Zen4-bit/Proxima) | Session routing plus BYOK separation, direct stream capture, MCP integration | Medium, architecture reference |
| [AmazingAng/auth2api](https://github.com/AmazingAng/auth2api) | OpenAI/Responses/Messages endpoint coverage, rate limits and local security defaults | Medium, compatibility and security reference |
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | Protocol and capability normalization | Low, use as a specification reference rather than a dependency |

## Existing Strengths

The project already has stronger browser-side functionality than most
comparators: multi-site rules, a tab pool, dual-channel stream parsing, media
handling, tool-call repair, and OpenAI Chat/Responses plus Anthropic Messages
compatibility. Evidence includes `README.md`, `app/api/chat.py`,
`app/api/anthropic_routes.py`, and `app/api/system.py`.

The main opportunity is making those capabilities observable and predictable
for clients, instead of adding more browser automation techniques.

## Recommended Backlog

### P0: Provider capabilities and status contract

Add `GET /v1/provider/capabilities` and `GET /v1/provider/status`.

Expose the supported API paths, available models and presets, site/modalities,
streaming and tool-call support, browser login/readiness hints, tab pool queue
depth, busy/idle counts, and a safe last-error summary. Reuse the existing
health, tab pool and model-routing data; do not expose session secrets.

Reference: [chatgpt-web-provider feature and status documentation](https://github.com/guberm/chatgpt-web-provider#features).

Estimated effort: 3-5 days.

### P0: Public request correlation

Return the existing internal request identifier as `x-request-id` on all API
responses, including error responses and streamed responses. Add dashboard
search and a sanitized diagnostic export keyed by that ID, correlating route,
tab, network trace and media work.

Reference: [OpenAI Python SDK request IDs](https://github.com/openai/openai-python#request-ids).

Estimated effort: 1-2 days.

### P0: Responses streaming compatibility matrix

Treat `/v1/responses` as a tested public contract: golden fixtures for text,
errors, completed/incomplete states, tool calls, structured output,
cancellation, and `stream=false/true`. Verify with the OpenAI SDK and retain
the honest distinction between SSE protocol compatibility and token-level
upstream streaming.

References: [OpenAI Python SDK response streaming](https://github.com/openai/openai-python#streaming-responses), [chatgpt-web-provider known limitations](https://github.com/guberm/chatgpt-web-provider#current-limitations).

Estimated effort: 3-5 days.

### P1: Protocol contract test suite

Build a fake or recorded browser backend and public-endpoint golden tests for
Chat Completions, Responses, Anthropic Messages, and `count_tokens`. Cover
streaming, non-streaming, image input, tool calls, cancellation, and normalized
errors. Add SDK smoke tests to CI.

References: [chatgpt-web-provider mock backend](https://github.com/guberm/chatgpt-web-provider#features), [auth2api endpoint coverage](https://github.com/AmazingAng/auth2api#endpoints).

Estimated effort: 5-8 days.

### P1: Enforced local security baseline

Make loopback binding the default. When a user enables remote exposure, display
a prominent dashboard warning and require authentication. Add startup checks
for an isolated persistent browser profile, weak/missing tokens, token-scoped
rate limits, concurrency limits, and redacted audit logging.

References: [chatgpt-web-provider security model](https://github.com/guberm/chatgpt-web-provider#security-model), [auth2api security features](https://github.com/AmazingAng/auth2api#features).

Estimated effort: 3-5 days.

### P1: Explicit model and session behavior

Define a consistent `new_session`, `reasoning_effort`, model-alias, and
unsupported-field policy. Publish it in the capabilities endpoint so clients
can avoid sending a request to a tab that cannot satisfy it.

Reference: [chatgpt-web-provider model and session selection](https://github.com/guberm/chatgpt-web-provider#model-and-level-selection).

Estimated effort: 3-5 days.

### P2: Optional browser-extension transport

Keep DrissionPage/CDP as the default, but define an internal request/response
transport interface that could later support a browser extension over WebSocket.
This would let users target already-open browser tabs while retaining explicit
authentication, rate limits, readiness checks and user-controlled login.

Reference: [Chat Relay architecture](https://github.com/BinaryBeastMaster/chat-relay#architecture-overview).

Estimated effort: 2-4 weeks.

### P2: Optional MCP status and stable-tab tools

For coding-agent users, consider a small local MCP server for status, tab
listing, readiness checks, stable tab keys and artifact retrieval. Do not add
unrestricted shell or file tools; use allowlists, argument schemas, approval
gates and audit logs.

Reference: [Agentify Desktop MCP tools](https://github.com/agentify-sh/desktop#useful-mcp-tools).

## Suggested Delivery Order

1. `x-request-id` end-to-end.
2. Provider capabilities/status endpoints.
3. Responses compatibility fixtures and SDK smoke tests.
4. Enforced local security baseline.
5. Session/model contract and browser-extension transport exploration.

## Notes

The recommendations are for local, user-controlled sessions only. They do not
recommend credential extraction, authentication bypass, CAPTCHA solving, or
public resale of provider access.
