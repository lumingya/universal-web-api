"""
app/core/extractors/media_extractor.py - 多模态内容提取器

职责：
- 复用图片提取器处理图片
- 补充音频、视频节点提取
- 对 blob 媒体做可选 data-uri 转换，避免返回临时 blob URL
"""

from __future__ import annotations

from datetime import datetime
import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import get_logger
from app.core.extractors.image_extractor import (
    get_default_image_extraction_config,
    image_extractor,
)
from app.models.schemas import is_modality_enabled, normalize_modalities_config, get_modality_policy

logger = get_logger("MEDIA_EXT")


PAGE_TTS_WS_PROBE_INSTALL_JS = r"""
return (function(opts) {
    const stateKey = "__uwaTtsWsProbe";
    const maxLogs = Math.max(16, Number(opts && opts.maxLogs || 128) || 128);
    const maxPayloadBytes = Math.max(0, Number(opts && opts.maxPayloadBytes || 4 * 1024 * 1024) || 0);
    const urlPatterns = Array.isArray(opts && opts.urlPatterns)
        ? opts.urlPatterns
            .map(item => String(item || "").trim().toLowerCase())
            .filter(Boolean)
        : [];
    let state = window[stateKey];

    const ensureState = () => {
        if (!state) {
            state = {
                installed: true,
                logs: [],
                push(item) {
                    const next = { t: Date.now(), ...(item || {}) };
                    if (maxPayloadBytes > 0) {
                        const size = Number(next.size || next.byte_size || 0) || 0;
                        if (size > maxPayloadBytes) {
                            delete next.base64;
                            delete next.text;
                            next.payload_omitted = true;
                            next.payload_limit = maxPayloadBytes;
                        }
                        if (typeof next.base64 === "string" && next.base64.length > Math.ceil(maxPayloadBytes * 4 / 3) + 16) {
                            delete next.base64;
                            next.payload_omitted = true;
                            next.payload_limit = maxPayloadBytes;
                        }
                        if (typeof next.text === "string" && next.text.length > maxPayloadBytes) {
                            next.text = next.text.slice(0, maxPayloadBytes);
                            next.truncated = true;
                            next.payload_limit = maxPayloadBytes;
                        }
                    }
                    this.logs.push(next);
                    if (this.logs.length > maxLogs) {
                        this.logs.splice(0, this.logs.length - maxLogs);
                    }
                },
                clear() {
                    this.logs.length = 0;
                },
                dump() {
                    return this.logs.slice();
                },
            };
            window[stateKey] = state;
        }
        return state;
    };

    state = ensureState();

    const normalizeUrl = (value) => {
        try {
            if (!value) return "";
            if (typeof value === "string") return value;
            if (typeof Request !== "undefined" && value instanceof Request) {
                return String(value.url || "");
            }
            if (typeof URL !== "undefined" && value instanceof URL) {
                return String(value.href || "");
            }
            return String(value.url || value.href || value.src || value || "");
        } catch {
            return "";
        }
    };
    const matchesUrlPattern = (url) => {
        const lowered = String(url || "").trim().toLowerCase();
        if (!lowered) return false;
        if (!urlPatterns.length) return true;
        return urlPatterns.some(pattern => lowered.includes(pattern));
    };
    const looksLikeAudioContentType = (value) => {
        const lowered = String(value || "").trim().toLowerCase();
        if (!lowered) return false;
        return (
            lowered.includes("audio/")
            || lowered.includes("application/ogg")
            || lowered.includes("ogg")
            || lowered.includes("opus")
            || lowered.includes("mpeg")
            || lowered.includes("mp3")
            || lowered.includes("wav")
            || lowered.includes("webm")
            || lowered.includes("octet-stream")
        );
    };
    const shouldCaptureBinary = (url, contentType) => (
        matchesUrlPattern(url) || looksLikeAudioContentType(contentType)
    );

    const blobToBase64 = (blob) => new Promise((resolve, reject) => {
        try {
            const reader = new FileReader();
            reader.onerror = () => reject(new Error("read_failed"));
            reader.onload = () => {
                const result = String(reader.result || "");
                resolve(result.split(",", 2)[1] || "");
            };
            reader.readAsDataURL(blob);
        } catch (error) {
            reject(error);
        }
    });
    const toBase64 = async (data) => {
        if (typeof data === "string") {
            if (maxPayloadBytes > 0 && data.length > maxPayloadBytes) {
                return { kind: "text", text: data.slice(0, maxPayloadBytes), size: data.length, truncated: true };
            }
            return { kind: "text", text: data, size: data.length };
        }

        let blob = null;
        let size = 0;
        if (data instanceof Blob) {
            blob = data;
            size = Number(data.size || 0) || 0;
        } else if (data instanceof ArrayBuffer) {
            size = Number(data.byteLength || 0) || 0;
            blob = new Blob([data]);
        } else if (ArrayBuffer.isView(data)) {
            size = Number(data.byteLength || 0) || 0;
            blob = new Blob([data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)]);
        } else {
            return { kind: typeof data, text: String(data), size: 0 };
        }

        if (maxPayloadBytes > 0 && size > maxPayloadBytes) {
            return { kind: "binary", size, payload_omitted: true, payload_limit: maxPayloadBytes };
        }

        return {
            kind: "binary",
            size,
            base64: await blobToBase64(blob),
        };
    };
    const pushBinary = async ({ url, data, transport, contentType, status, method }) => {
        const info = await toBase64(data);
        state.push({
            dir: "recv",
            url: String(url || ""),
            transport: String(transport || ""),
            content_type: String(contentType || ""),
            status: Number(status || 0) || 0,
            method: String(method || "").toUpperCase(),
            ...(info || {}),
        });
    };

    const patchSocket = (ws) => {
        if (!ws || ws.__uwaTtsWsProbeWrapped) return ws;
        ws.__uwaTtsWsProbeWrapped = true;
        const url = String(ws.url || "");
        state.push({ dir: "ws-created", url });
        ws.addEventListener("open", () => state.push({ dir: "ws-open", url }));
        ws.addEventListener("close", () => state.push({ dir: "ws-close", url }));
        ws.addEventListener("error", () => state.push({ dir: "ws-error", url }));

        ws.addEventListener("message", (event) => {
            Promise.resolve(toBase64(event.data)).then((info) => state.push({ dir: "recv", url, ...(info || {}) }));
        });

        return ws;
    };

    if (!state._fetchPatched && typeof window.fetch === "function") {
        const rawFetch = window.fetch;
        window.fetch = function(...args) {
            const req = args[0];
            const url = normalizeUrl(req);
            const method = String(
                (args[1] && args[1].method)
                || (req && req.method)
                || "GET"
            ).toUpperCase();
            if (matchesUrlPattern(url)) {
                state.push({ dir: "http-request", transport: "fetch", url, method });
            }
            return rawFetch.apply(this, args).then((response) => {
                try {
                    const responseUrl = normalizeUrl(response && response.url) || url;
                    const contentType = String(
                        response
                        && response.headers
                        && typeof response.headers.get === "function"
                        && response.headers.get("content-type")
                        || ""
                    );
                    const status = Number(response && response.status || 0) || 0;
                    const capture = shouldCaptureBinary(responseUrl, contentType);
                    if (capture || matchesUrlPattern(responseUrl)) {
                        state.push({
                            dir: "http-response",
                            transport: "fetch",
                            url: responseUrl,
                            method,
                            status,
                            content_type: contentType,
                            capture,
                        });
                    }
                    if (capture && response && typeof response.clone === "function") {
                        const cloned = response.clone();
                        Promise.resolve().then(async () => {
                            try {
                                const body = await cloned.arrayBuffer();
                                await pushBinary({
                                    url: responseUrl,
                                    data: body,
                                    transport: "fetch",
                                    contentType,
                                    status,
                                    method,
                                });
                            } catch (error) {
                                state.push({
                                    dir: "http-error",
                                    transport: "fetch",
                                    phase: "read-body",
                                    url: responseUrl,
                                    message: String(error && (error.message || error.name) || error || "fetch_body_error").slice(0, 200),
                                });
                            }
                        });
                    }
                } catch (error) {
                    state.push({
                        dir: "http-error",
                        transport: "fetch",
                        phase: "response",
                        url,
                        message: String(error && (error.message || error.name) || error || "fetch_response_error").slice(0, 200),
                    });
                }
                return response;
            }).catch((error) => {
                state.push({
                    dir: "http-error",
                    transport: "fetch",
                    phase: "request",
                    url,
                    method,
                    message: String(error && (error.message || error.name) || error || "fetch_request_error").slice(0, 200),
                });
                throw error;
            });
        };
        state._fetchPatched = true;
    }

    if (!state._xhrPatched && typeof window.XMLHttpRequest !== "undefined") {
        const xhrMeta = new WeakMap();
        const origOpen = XMLHttpRequest.prototype.open;
        const origSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function(method, url, ...rest) {
            try {
                const previous = xhrMeta.get(this) || {};
                xhrMeta.set(this, {
                    ...previous,
                    method: String(method || "GET").toUpperCase(),
                    url: normalizeUrl(url),
                });
            } catch {}
            return origOpen.call(this, method, url, ...rest);
        };
        XMLHttpRequest.prototype.send = function(...args) {
            try {
                const installMeta = xhrMeta.get(this) || {};
                if (!installMeta.loadendInstalled) {
                    installMeta.loadendInstalled = true;
                    xhrMeta.set(this, installMeta);
                    this.addEventListener("loadend", () => {
                        const meta = xhrMeta.get(this) || {};
                        const responseUrl = normalizeUrl(this.responseURL || meta.url || "");
                        const method = String(meta.method || "GET").toUpperCase();
                        let contentType = "";
                        try {
                            contentType = String(
                                typeof this.getResponseHeader === "function"
                                && this.getResponseHeader("content-type")
                                || ""
                            );
                        } catch {}
                        const status = Number(this.status || 0) || 0;
                        const capture = shouldCaptureBinary(responseUrl, contentType);
                        if (capture || matchesUrlPattern(responseUrl)) {
                            state.push({
                                dir: "http-response",
                                transport: "xhr",
                                url: responseUrl,
                                method,
                                status,
                                content_type: contentType,
                                capture,
                            });
                        }
                        if (!capture) {
                            return;
                        }
                        Promise.resolve().then(async () => {
                            try {
                                const responseType = String(this.responseType || "").trim().toLowerCase();
                                const payload = this.response;
                                if (payload instanceof Blob) {
                                    await pushBinary({
                                        url: responseUrl,
                                        data: payload,
                                        transport: "xhr",
                                        contentType,
                                        status,
                                        method,
                                    });
                                    return;
                                }
                                if (payload instanceof ArrayBuffer) {
                                    await pushBinary({
                                        url: responseUrl,
                                        data: payload,
                                        transport: "xhr",
                                        contentType,
                                        status,
                                        method,
                                    });
                                    return;
                                }
                                if (ArrayBuffer.isView(payload)) {
                                    await pushBinary({
                                        url: responseUrl,
                                        data: payload.buffer.slice(0),
                                        transport: "xhr",
                                        contentType,
                                        status,
                                        method,
                                    });
                                    return;
                                }
                                if ((!responseType || responseType === "text") && typeof this.responseText === "string" && this.responseText) {
                                    const info = await toBase64(this.responseText);
                                    state.push({
                                        dir: "recv",
                                        url: responseUrl,
                                        transport: "xhr",
                                        content_type: contentType,
                                        status,
                                        method,
                                        ...(info || {}),
                                    });
                                }
                            } catch (error) {
                                state.push({
                                    dir: "http-error",
                                    transport: "xhr",
                                    phase: "read-body",
                                    url: responseUrl,
                                    method,
                                    message: String(error && (error.message || error.name) || error || "xhr_body_error").slice(0, 200),
                                });
                            }
                        });
                    });
                }
            } catch (error) {
                state.push({
                    dir: "http-error",
                    transport: "xhr",
                    phase: "install",
                    url: normalizeUrl((xhrMeta.get(this) || {}).url),
                    message: String(error && (error.message || error.name) || error || "xhr_install_error").slice(0, 200),
                });
            }
            return origSend.apply(this, args);
        };
        state._xhrPatched = true;
    }

    if (!state._patched) {
        const RawWS = window.WebSocket;
        if (!state._wsSendPatched && RawWS && RawWS.prototype && typeof RawWS.prototype.send === "function") {
            const rawProtoSend = RawWS.prototype.send;
            const patchedSend = function(data) {
                const url = String(this && this.url || "");
                Promise.resolve(toBase64(data)).then((info) => state.push({ dir: "send", url, ...(info || {}) }));
                return rawProtoSend.apply(this, arguments);
            };
            try {
                patchedSend.toString = function() { return rawProtoSend.toString(); };
            } catch {}
            RawWS.prototype.send = patchedSend;
            state._wsSendPatched = true;
        }
        function WrappedWebSocket(...args) {
            const ws = new RawWS(...args);
            return patchSocket(ws);
        }
        WrappedWebSocket.prototype = RawWS.prototype;
        Object.assign(WrappedWebSocket, RawWS);
        try {
            WrappedWebSocket.toString = function() { return RawWS.toString(); };
        } catch {}
        window.WebSocket = WrappedWebSocket;
        state._patched = true;
    }

    if (opts && opts.clear) {
        state.clear();
    }

    return {
        installed: true,
        count: state.logs.length,
    };
})(arguments[0]);
"""

PAGE_TTS_WS_PROBE_DUMP_JS = r"""
return (function() {
    const state = window.__uwaTtsWsProbe;
    if (!state || typeof state.dump !== "function") {
        return [];
    }
    return state.dump();
})();
"""


PAGE_BROWSER_TTS_FALLBACK_START_JS = r"""
return (async function(opts) {
    const stateKey = "__uwaBrowserTtsFallbackState";
    const input = Object.assign({}, opts || {});
    const text = String(input.text || "").trim();
    if (!text) {
        return { ok: false, error: "empty_text" };
    }

    const normalizeString = (value, fallback = "") => {
        const textValue = String(value == null ? fallback : value).trim();
        return textValue || String(fallback || "").trim();
    };
    const normalizeInt = (value, fallback = 0) => {
        const n = Number(value);
        if (!Number.isFinite(n)) return Number(fallback || 0) || 0;
        return Math.trunc(n);
    };
    const readJsonStorage = (key) => {
        try {
            const raw = window.localStorage ? window.localStorage.getItem(key) : null;
            if (!raw) return null;
            return JSON.parse(raw);
        } catch {
            return null;
        }
    };
    const randomUuid = () => {
        try {
            if (window.crypto && typeof window.crypto.randomUUID === "function") {
                return window.crypto.randomUUID();
            }
        } catch {}
        const seed = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx";
        return seed.replace(/[xy]/g, (ch) => {
            const r = Math.random() * 16 | 0;
            const v = ch === "x" ? r : ((r & 0x3) | 0x8);
            return v.toString(16);
        });
    };

    const samanthaMeta = readJsonStorage("samantha_web_web_id") || {};
    const teaMeta = readJsonStorage("__tea_cache_tokens_497858") || {};
    const deviceId = normalizeString(
        input.device_id || input.deviceId || samanthaMeta.web_id || teaMeta.user_unique_id || teaMeta.device_id,
        ""
    );
    const webId = normalizeString(
        input.web_id || input.webId || teaMeta.web_id || samanthaMeta.web_id,
        ""
    );
    const teaUuid = normalizeString(
        input.tea_uuid || input.teaUuid || teaMeta.user_unique_id || webId,
        webId
    );
    const webTabId = normalizeString(
        input.web_tab_id || input.webTabId,
        randomUuid()
    );

    const query = new URLSearchParams();
    query.set("speaker", normalizeString(input.speaker, "2"));
    query.set("format", normalizeString(input.format, "aac").toLowerCase() || "aac");
    query.set("speech_rate", String(normalizeInt(input.speech_rate, 0)));
    query.set("pitch", String(normalizeInt(input.pitch, 0)));
    query.set("version_code", "20800");
    query.set("language", normalizeString(input.language, "zh"));
    query.set("device_platform", normalizeString(input.device_platform, "web"));
    query.set("aid", normalizeString(input.aid, "497858"));
    query.set("real_aid", normalizeString(input.real_aid, "497858"));
    query.set("pkg_type", normalizeString(input.pkg_type, "release_version"));
    query.set("device_id", deviceId);
    query.set("pc_version", normalizeString(input.pc_version, "3.20.2"));
    query.set("web_id", webId);
    query.set("tea_uuid", teaUuid);
    query.set("region", normalizeString(input.region, "CN"));
    query.set("sys_region", normalizeString(input.sys_region, "CN"));
    query.set("samantha_web", normalizeString(input.samantha_web, "1"));
    query.set("use-olympus-account", normalizeString(input.use_olympus_account, "1"));
    query.set("web_tab_id", webTabId);

    const wsUrl = "wss://ws-samantha.doubao.com/samantha/audio/tts?" + query.toString();
    const timeoutMs = Math.max(3000, Math.min(120000, Number(input.timeout_ms || input.timeoutMs || 30000) || 30000));
    const maxBytes = Math.max(0, Number(input.max_bytes || input.maxBytes || 0) || 0);
    const existing = window[stateKey];
    if (existing && existing.active) {
        return {
            ok: false,
            error: "already_active",
            state: {
                active: true,
                started_at: existing.started_at || 0,
            },
        };
    }

    const state = {
        active: true,
        started_at: Date.now(),
        phase: "starting",
        error: "",
        text_length: text.length,
        received_chunks: 0,
        received_bytes: 0,
        mime: "audio/aac",
        events: [],
        audio_chunks: [],
        completed: false,
        data_uri: "",
        open_event_seen: false,
        synthesis_started_seen: false,
        sentence_start_seen: false,
        finish_event_seen: false,
    };
    window[stateKey] = state;

    const pushEvent = (name, payload) => {
        state.events.push({
            t: Date.now(),
            name: String(name || ""),
            ...(payload || {}),
        });
        if (state.events.length > 64) {
            state.events.splice(0, state.events.length - 64);
        }
    };
    const bytesToBase64 = (u8) => {
        let binary = "";
        const chunkSize = 0x8000;
        for (let i = 0; i < u8.length; i += chunkSize) {
            const chunk = u8.subarray(i, i + chunkSize);
            let part = "";
            for (let j = 0; j < chunk.length; j += 1) {
                part += String.fromCharCode(chunk[j]);
            }
            binary += part;
        }
        return btoa(binary);
    };
    const concatUint8Arrays = (chunks) => {
        let total = 0;
        for (const part of chunks) {
            if (part instanceof Uint8Array) {
                total += part.length;
            }
        }
        const merged = new Uint8Array(total);
        let offset = 0;
        for (const part of chunks) {
            if (!(part instanceof Uint8Array)) continue;
            merged.set(part, offset);
            offset += part.length;
        }
        return merged;
    };
    const finalize = () => {
        if (state.completed) {
            return;
        }
        state.active = false;
        if (state.audio_chunks.length > 0) {
            const mergedAudio = concatUint8Arrays(state.audio_chunks);
            state.data_uri = "data:" + state.mime + ";base64," + bytesToBase64(mergedAudio);
        }
        state.completed = true;
        pushEvent("completed", {
            bytes: state.received_bytes,
            chunks: state.received_chunks,
            hasData: !!state.data_uri,
            error: state.error,
        });
    };

    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    const timer = window.setTimeout(() => {
        state.error = state.error || "timeout";
        state.phase = "timeout";
        try { ws.close(); } catch {}
    }, timeoutMs);

    ws.addEventListener("open", () => {
        state.phase = "open";
        state.open_event_seen = true;
        pushEvent("open");
        try {
            ws.send(JSON.stringify({ event: "text", text }));
            ws.send(JSON.stringify({ event: "finish" }));
            state.phase = "request_sent";
            pushEvent("request_sent", { textLength: text.length });
        } catch (error) {
            state.error = String(error && (error.message || error.name) || error || "send_failed");
            state.phase = "send_failed";
            pushEvent("send_failed", { message: state.error });
            try { ws.close(); } catch {}
        }
    });

    ws.addEventListener("message", async (event) => {
        try {
            if (typeof event.data === "string") {
                let payload = null;
                try {
                    payload = JSON.parse(event.data);
                } catch {}
                if (payload && typeof payload === "object") {
                    const eventName = String(payload.event || "");
                    pushEvent("json", {
                        event: eventName,
                        code: Number(payload.code || 0) || 0,
                        message: String(payload.message || payload.error || ""),
                    });
                    if (eventName === "open_success") state.open_event_seen = true;
                    if (eventName === "synthesis_started") state.synthesis_started_seen = true;
                    if (eventName === "sentence_start") state.sentence_start_seen = true;
                    if (eventName === "finish") state.finish_event_seen = true;
                    if (payload.error || (payload.code && Number(payload.code) !== 0)) {
                        state.error = String(payload.message || payload.error || ("code:" + payload.code));
                        state.phase = "api_error";
                        try { ws.close(); } catch {}
                    }
                    return;
                }
                pushEvent("text", { preview: String(event.data || "").slice(0, 120) });
                return;
            }

            let bytes = null;
            if (event.data instanceof ArrayBuffer) {
                bytes = new Uint8Array(event.data);
            } else if (event.data instanceof Blob) {
                bytes = new Uint8Array(await event.data.arrayBuffer());
            } else if (ArrayBuffer.isView(event.data)) {
                bytes = new Uint8Array(event.data.buffer.slice(0));
            }
            if (!bytes || !bytes.length) {
                return;
            }
            if (maxBytes && state.received_bytes + bytes.length > maxBytes) {
                state.error = "audio_too_large:" + String(state.received_bytes + bytes.length);
                state.phase = "size_limit";
                pushEvent("audio_too_large", {
                    bytes: bytes.length,
                    totalBytes: state.received_bytes,
                    limit: maxBytes,
                });
                try { ws.close(); } catch {}
                return;
            }
            state.audio_chunks.push(bytes);
            state.received_chunks = state.audio_chunks.length;
            state.received_bytes += bytes.length;
            state.phase = "receiving_audio";
            pushEvent("audio", { bytes: bytes.length, totalBytes: state.received_bytes });
        } catch (error) {
            pushEvent("message_error", {
                message: String(error && (error.message || error.name) || error || "message_error"),
            });
        }
    });

    ws.addEventListener("error", () => {
        if (!state.error) {
            state.error = "websocket_error";
        }
        state.phase = "websocket_error";
        pushEvent("error", { message: state.error });
    });

    ws.addEventListener("close", (event) => {
        window.clearTimeout(timer);
        if (!state.error && event && event.code && event.code !== 1000) {
            state.error = "close:" + String(event.code);
        }
        state.phase = "closed";
        pushEvent("close", {
            code: Number(event && event.code || 0) || 0,
            reason: String(event && event.reason || ""),
        });
        finalize();
    });

    return {
        ok: true,
        state: {
            ws_url: wsUrl,
            timeout_ms: timeoutMs,
            device_id: deviceId,
            web_id: webId,
            tea_uuid: teaUuid,
            web_tab_id: webTabId,
        },
    };
})(arguments[0]);
"""


PAGE_BROWSER_TTS_FALLBACK_STATUS_JS = r"""
return (function() {
    const state = window.__uwaBrowserTtsFallbackState;
    if (!state || typeof state !== "object") {
        return {};
    }
    return {
        active: !!state.active,
        started_at: Number(state.started_at || 0) || 0,
        phase: String(state.phase || ""),
        error: String(state.error || ""),
        text_length: Number(state.text_length || 0) || 0,
        received_chunks: Number(state.received_chunks || 0) || 0,
        received_bytes: Number(state.received_bytes || 0) || 0,
        mime: String(state.mime || ""),
        completed: !!state.completed,
        has_data: !!state.data_uri,
        data_uri: state.data_uri || "",
        open_event_seen: !!state.open_event_seen,
        synthesis_started_seen: !!state.synthesis_started_seen,
        sentence_start_seen: !!state.sentence_start_seen,
        finish_event_seen: !!state.finish_event_seen,
        events: Array.isArray(state.events) ? state.events.slice(-16) : [],
    };
})();
"""


class MediaExtractor:
    """多模态提取器。"""
    PAGE_AUDIO_CAPTURE_SCRIPT_VERSION = 4

    EXTRACT_MEDIA_JS = r"""
    return (async function(opts) {
        const {
            selector = "audio, audio source",
            containerSelector = null,
            waitForLoad = true,
            loadTimeoutMs = 5000,
            downloadBlobs = true,
            maxBytes = 10485760,
            mode = "all",
            mediaType = "audio",
            allowContainerFallback = true
        } = opts || {};

        const primaryRoots = [];
        const fallbackRoots = [];
        const pushRoot = (bucket, value) => {
            if (!value) return;
            const nodeType = Number(value.nodeType || 0);
            if (nodeType !== 1 && nodeType !== 9) return;
            if (!bucket.includes(value)) {
                bucket.push(value);
            }
        };

        if (this && (this.nodeType === 1 || this.nodeType === 9)) {
            pushRoot(primaryRoots, this);
        }

        if (containerSelector) {
            try {
                const scopedRoots = Array.from(document.querySelectorAll(containerSelector));
                for (const scopedRoot of scopedRoots) {
                    pushRoot(fallbackRoots, scopedRoot);
                }
            } catch {}
        } else {
            pushRoot(fallbackRoots, document);
        }

        if (primaryRoots.length === 0 && fallbackRoots.length === 0) {
            return { items: [], warnings: ["container_not_found"] };
        }

        const collectNodes = (roots) => {
            const scopedNodes = [];
            const seenNodes = new Set();
            const pushNode = (value) => {
                if (!(value instanceof Element)) return;
                if (seenNodes.has(value)) return;
                seenNodes.add(value);
                scopedNodes.push(value);
            };

            for (const root of roots) {
                try {
                    if (root instanceof Element && typeof root.matches === "function" && root.matches(selector)) {
                        pushNode(root);
                    }
                } catch {}

                try {
                    const rootNodes = root.querySelectorAll ? Array.from(root.querySelectorAll(selector)) : [];
                    for (const node of rootNodes) {
                        pushNode(node);
                    }
                } catch {}
            }

            return scopedNodes;
        };

        let nodes = collectNodes(primaryRoots);
        if (nodes.length === 0 && allowContainerFallback) {
            nodes = collectNodes(fallbackRoots);
        }

        if (nodes.length === 0) {
            return { items: [], warnings: [] };
        }

        function toAbsoluteUrl(src) {
            if (!src) return "";
            if (
                src.startsWith("http://")
                || src.startsWith("https://")
                || src.startsWith("data:")
                || src.startsWith("blob:")
            ) {
                return src;
            }
            try {
                return new URL(src, document.baseURI).href;
            } catch {
                return "";
            }
        }

        function resolveMediaNode(node) {
            if (!(node instanceof Element)) return null;
            const tag = String(node.tagName || "").toLowerCase();
            if (tag === mediaType) return node;
            const parent = node.parentElement;
            if (parent && String(parent.tagName || "").toLowerCase() === mediaType) {
                return parent;
            }
            return null;
        }

        function pickSource(node) {
            const mediaNode = resolveMediaNode(node);
            if (!mediaNode) return { src: "", mediaNode: null, mime: null };

            let src = "";
            let sourceNode = null;

            try {
                src = String(mediaNode.currentSrc || mediaNode.src || "").trim();
            } catch {}

            if (!src) {
                try {
                    const childSource = mediaNode.querySelector("source[src]");
                    if (childSource) {
                        sourceNode = childSource;
                        src = String(childSource.getAttribute("src") || "").trim();
                    }
                } catch {}
            }

            if (!src && String(node.tagName || "").toLowerCase() === "source") {
                sourceNode = node;
                try {
                    src = String(node.getAttribute("src") || "").trim();
                } catch {}
            }

            const mime =
                (sourceNode && sourceNode.getAttribute("type"))
                || mediaNode.getAttribute("type")
                || null;

            return {
                src: toAbsoluteUrl(src),
                mediaNode: mediaNode,
                mime: mime
            };
        }

        function isLoaded(mediaNode, src) {
            if (!mediaNode) return true;
            if (!src || src.startsWith("data:")) return true;
            if (src.startsWith("blob:")) return Number(mediaNode.readyState || 0) >= 1;
            return Number(mediaNode.readyState || 0) >= 1;
        }

        async function waitForReady(items) {
            if (!waitForLoad) return;
            const deadline = Date.now() + loadTimeoutMs;
            while (Date.now() < deadline) {
                const allReady = items.every(item => isLoaded(item._mediaNode, item.src));
                if (allReady) return;
                await new Promise(resolve => setTimeout(resolve, 100));
            }
        }

        async function blobToDataUri(blob) {
            const dataUri = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onerror = () => reject(new Error("read_failed"));
                reader.onload = () => resolve(reader.result);
                reader.readAsDataURL(blob);
            });
            return {
                dataUri,
                mime: blob.type || null,
                byteSize: Number(blob.size) || null,
                source: "blob"
            };
        }

        async function fetchBlobWithLimit(src, limitBytes) {
            const response = await fetch(src);
            const contentType = String(response && response.headers && response.headers.get("content-type") || "");
            const contentLength = Number(response && response.headers && response.headers.get("content-length") || 0) || 0;
            if (limitBytes && contentLength && contentLength > limitBytes) {
                throw new Error("blob_too_large:" + contentLength);
            }

            if (response && response.body && typeof response.body.getReader === "function") {
                const reader = response.body.getReader();
                const chunks = [];
                let total = 0;
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    if (!value) continue;
                    total += Number(value.byteLength || value.length || 0) || 0;
                    if (limitBytes && total > limitBytes) {
                        try { await reader.cancel(); } catch {}
                        throw new Error("blob_too_large:" + total);
                    }
                    chunks.push(value);
                }
                return new Blob(chunks, { type: contentType });
            }

            const blob = await response.blob();
            if (limitBytes && blob.size > limitBytes) {
                throw new Error("blob_too_large:" + blob.size);
            }
            return blob;
        }

        let items = nodes.map((node, index) => {
            const source = pickSource(node);
            const mediaNode = source.mediaNode;
            if (!mediaNode || !source.src) {
                return null;
            }

            const label =
                mediaNode.getAttribute("aria-label")
                || mediaNode.getAttribute("title")
                || mediaNode.getAttribute("alt")
                || "";

            return {
                _mediaNode: mediaNode,
                index: index,
                src: source.src,
                label: label,
                mime: source.mime,
                width: mediaType === "video" ? (mediaNode.videoWidth || mediaNode.clientWidth || null) : null,
                height: mediaType === "video" ? (mediaNode.videoHeight || mediaNode.clientHeight || null) : null
            };
        }).filter(Boolean);

        if (items.length === 0) {
            return { items: [], warnings: [] };
        }

        await waitForReady(items);

        if (mode === "first") items = items.slice(0, 1);
        if (mode === "last") items = items.slice(-1);

        const warnings = [];
        const out = [];

        for (const item of items) {
            const src = String(item.src || "");
            if (!src) continue;

            if (downloadBlobs && src.startsWith("blob:")) {
                try {
                    const blob = await fetchBlobWithLimit(src, maxBytes);
                    const converted = await blobToDataUri(blob);
                    out.push({
                        index: item.index,
                        label: item.label,
                        mime: converted.mime || item.mime,
                        byte_size: converted.byteSize,
                        data_uri: converted.dataUri,
                        width: item.width,
                        height: item.height,
                        _source: converted.source
                    });
                    continue;
                } catch (error) {
                    if (String(error || "").includes("blob_too_large:")) {
                        warnings.push(String(error).replace(/^Error:\s*/, ""));
                        continue;
                    }
                    warnings.push("blob_convert_failed:" + String(error).slice(0, 80));
                }
            }

            out.push({
                index: item.index,
                src: src,
                label: item.label,
                mime: item.mime,
                width: item.width,
                height: item.height
            });
        }

        return { items: out, warnings: warnings };
    }).call(this, arguments[0]);
    """

    INSTALL_PAGE_AUDIO_CAPTURE_JS = r"""
    return (function(opts) {
        const stateKey = "__uwaAudioCapture";
        const scriptVersion = 4;
        const now = () => Date.now();
        const mutePlayback = !opts || opts.mutePlayback !== false;
        const activityThreshold = Math.max(0.0001, Number(opts && opts.activityThreshold || 0.006) || 0.006);
        const maxCaptureBytes = Math.max(0, Number(opts && opts.maxCaptureBytes || 0) || 0);
        const mimeCandidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
        const chooseMimeType = () => {
            if (typeof MediaRecorder === "undefined") return "";
            for (const item of mimeCandidates) {
                try {
                    if (MediaRecorder.isTypeSupported(item)) return item;
                } catch {}
            }
            return "";
        };
        const stopRecorder = (recorder) => new Promise((resolve) => {
            try {
                if (!recorder || recorder.state === "inactive") {
                    resolve();
                    return;
                }
                const cleanup = () => {
                    try {
                        recorder.removeEventListener("stop", cleanup);
                    } catch {}
                    resolve();
                };
                recorder.addEventListener("stop", cleanup, { once: true });
                recorder.stop();
            } catch {
                resolve();
            }
        });
        const createState = () => ({
            version: scriptVersion,
            installedAt: now(),
            mutePlayback: true,
            currentSessionId: 0,
            mediaElementMetas: [],
            webAudioMetas: [],
            mediaStreamMetas: [],
            events: [],
            errors: [],
            exported: [],
            pushEvent(kind, payload) {
                this.events.push({ t: now(), kind, ...(payload || {}) });
                if (this.events.length > 200) this.events.splice(0, this.events.length - 200);
            },
            pushError(stage, error) {
                this.errors.push({
                    t: now(),
                    stage,
                    message: String(error && (error.message || error.name) || error || "unknown_error").slice(0, 240),
                });
                if (this.errors.length > 50) this.errors.splice(0, this.errors.length - 50);
            },
            async reset(resetOpts) {
                const preserveGraph = !!(resetOpts && resetOpts.preserveGraph);
                this.currentSessionId += 1;
                this.events = [];
                this.errors = [];
                this.exported = [];
                if (preserveGraph) {
                    for (const meta of [...this.mediaElementMetas, ...this.webAudioMetas]) {
                        try {
                            if (!meta) continue;
                            if (meta.recorder && meta.recorder.state !== "inactive") {
                                await stopRecorder(meta.recorder);
                            }
                            meta.sessionId = this.currentSessionId;
                            meta.chunks = [];
                            meta.totalBytes = 0;
                            meta.lastDataAt = 0;
                            meta.lastActiveAt = 0;
                            meta.lastRms = 0;
                            if (meta.kind === "web_audio") {
                                if (
                                    createRecorderForStream(
                                        meta,
                                        meta.dest && meta.dest.stream,
                                        "web_audio_recorder_construct",
                                        "web_audio_dataavailable",
                                        "web_audio_recorder_runtime",
                                        "web_audio_recorder_error",
                                    )
                                    && meta.recorder
                                    && meta.recorder.state === "inactive"
                                ) {
                                    meta.recorder.start(250);
                                }
                            } else {
                                meta.recorder = null;
                            }
                        } catch (error) {
                            this.pushError("preserve_graph_reset", error);
                        }
                    }
                    return;
                }

                const pending = [];
                for (const meta of [...this.mediaElementMetas, ...this.webAudioMetas]) {
                    try {
                        if (meta && meta.recorder && meta.recorder.state !== "inactive") {
                            pending.push(stopRecorder(meta.recorder));
                        }
                    } catch {}
                }
                await Promise.all(pending);
                this.mediaElementMetas = [];
                this.webAudioMetas = [];
            },
            status() {
                const metas = [...this.mediaElementMetas, ...this.webAudioMetas, ...this.mediaStreamMetas];
                let activeRecordings = 0;
                let totalChunks = 0;
                let lastDataAt = 0;
                let lastActiveAt = 0;
                let peakRms = 0;
                let playingMediaElements = 0;
                let terminalPlaybackElements = 0;
                let lastPlaybackEventAt = 0;
                for (const meta of metas) {
                    const recorder = meta && meta.recorder;
                    if (recorder && recorder.state === "recording") activeRecordings += 1;
                    const chunkCount = Array.isArray(meta && meta.chunks) ? meta.chunks.length : 0;
                    totalChunks += chunkCount;
                    const chunkAt = Number(meta && meta.lastDataAt || 0);
                    if (chunkAt > lastDataAt) lastDataAt = chunkAt;
                    const activeAt = Number(meta && meta.lastActiveAt || 0);
                    if (activeAt > lastActiveAt) lastActiveAt = activeAt;
                    const rms = Number(meta && meta.lastRms || 0);
                    if (rms > peakRms) peakRms = rms;
                    const playbackAt = Number(meta && meta.lastPlaybackEventAt || 0);
                    if (playbackAt > lastPlaybackEventAt) lastPlaybackEventAt = playbackAt;
                    if (meta && meta.kind === "media_element") {
                        if (meta.isPlaying) {
                            playingMediaElements += 1;
                        } else if (meta.playbackState && meta.playbackState !== "idle") {
                            terminalPlaybackElements += 1;
                        }
                    }
                }
                return {
                    version: this.version,
                    session_id: this.currentSessionId,
                    mute_playback: this.mutePlayback,
                    tracked_media_elements: this.mediaElementMetas.length,
                    tracked_web_audio: this.webAudioMetas.length,
                    tracked_media_streams: this.mediaStreamMetas.length,
                    active_recordings: activeRecordings,
                    total_chunks: totalChunks,
                    has_data: totalChunks > 0,
                    last_data_at: lastDataAt,
                    last_active_at: lastActiveAt,
                    peak_rms: peakRms,
                    playing_media_elements: playingMediaElements,
                    terminal_playback_elements: terminalPlaybackElements,
                    last_playback_event_at: lastPlaybackEventAt,
                    recent_events: this.events.slice(-20),
                    recent_errors: this.errors.slice(-10),
                };
            },
            async export(exportOpts) {
                const warnings = [];
                const maxBytes = Number(exportOpts && exportOpts.maxBytes || 0) || 0;
                const toDataUri = (blob) => new Promise((resolve, reject) => {
                    try {
                        const reader = new FileReader();
                        reader.onerror = () => reject(new Error("read_failed"));
                        reader.onload = () => resolve(reader.result);
                        reader.readAsDataURL(blob);
                    } catch (error) {
                        reject(error);
                    }
                });

                const results = [];
                const metas = [...this.mediaElementMetas, ...this.webAudioMetas, ...this.mediaStreamMetas];
                for (const meta of metas) {
                    try {
                        if (meta && meta.recorder && meta.recorder.state !== "inactive") {
                            await stopRecorder(meta.recorder);
                        }
                    } catch (error) {
                        this.pushError("export_stop", error);
                    }

                    const chunks = Array.isArray(meta && meta.chunks) ? meta.chunks : [];
                    if (!chunks.length) continue;

                    const blob = new Blob(chunks, {
                        type: String((meta && meta.mimeType) || "audio/webm"),
                    });
                    if (maxBytes && blob.size > maxBytes) {
                        warnings.push("capture_too_large:" + blob.size);
                        continue;
                    }

                    try {
                        const dataUri = await toDataUri(blob);
                        results.push({
                            index: results.length,
                            label: meta.kind === "media_element" ? "captured_audio" : "captured_web_audio",
                            mime: blob.type || String((meta && meta.mimeType) || "audio/webm"),
                            byte_size: Number(blob.size) || null,
                            data_uri: dataUri,
                            _source: meta.kind === "media_element" ? "capture_stream" : "web_audio",
                        });
                    } catch (error) {
                        this.pushError("export_data_uri", error);
                    }
                }

                this.exported = results.slice();
                return {
                    items: results,
                    warnings,
                    status: this.status(),
                };
            },
        });

        let state = window[stateKey];
        if (!state || state.version !== scriptVersion) {
            state = createState();
            window[stateKey] = state;
        }
        state.mutePlayback = mutePlayback;

        const configureRecorder = (meta, recorder, dataStage, errorStage, errorLabel) => {
            if (!meta || !recorder) return false;
            meta.recorder = recorder;
            meta.mimeType = String(recorder.mimeType || meta.mimeType || "audio/webm");
            meta.chunks = [];
            meta.totalBytes = 0;
            meta.lastDataAt = 0;
            meta.lastActiveAt = 0;
            meta.lastRms = 0;
            recorder.ondataavailable = (event) => {
                try {
                    if (event && event.data && event.data.size > 0) {
                        const nextSize = Number(event.data.size || 0) || 0;
                        const totalBytes = Number(meta.totalBytes || 0) || 0;
                        if (maxCaptureBytes && totalBytes + nextSize > maxCaptureBytes) {
                            meta.captureLimitExceeded = true;
                            state.pushEvent("capture_limit_exceeded", {
                                kind: meta.kind,
                                limit: maxCaptureBytes,
                                totalBytes,
                                nextSize,
                            });
                            try {
                                if (recorder.state !== "inactive") recorder.stop();
                            } catch (stopError) {
                                state.pushError(dataStage + "_stop_after_limit", stopError);
                            }
                            return;
                        }
                        meta.chunks.push(event.data);
                        meta.totalBytes = totalBytes + nextSize;
                        meta.lastDataAt = now();
                    }
                } catch (error) {
                    state.pushError(dataStage, error);
                }
            };
            recorder.onerror = (event) => {
                state.pushError(errorStage, event && (event.error || event.name) || errorLabel);
            };
            return true;
        };

        const monitorAudioActivity = (meta, stream, stage) => {
            if (!meta || !stream || meta._activityMonitor) return;
            try {
                const ActivityAudioContext = window.AudioContext || window.webkitAudioContext;
                if (!ActivityAudioContext) return;
                const monitorCtx = new ActivityAudioContext();
                const source = monitorCtx.createMediaStreamSource(stream);
                const analyser = monitorCtx.createAnalyser();
                analyser.fftSize = 1024;
                source.connect(analyser);
                const samples = new Float32Array(analyser.fftSize);
                const tick = () => {
                    try {
                        analyser.getFloatTimeDomainData(samples);
                        let sum = 0;
                        for (let i = 0; i < samples.length; i += 1) {
                            const value = samples[i] || 0;
                            sum += value * value;
                        }
                        const rms = Math.sqrt(sum / samples.length);
                        meta.lastRms = rms;
                        if (rms >= activityThreshold) {
                            meta.lastActiveAt = now();
                        }
                    } catch (error) {
                        state.pushError(stage || "activity_monitor_tick", error);
                    }
                    if (meta._activityMonitor) {
                        meta._activityMonitor.timer = window.setTimeout(tick, 100);
                    }
                };
                meta._activityMonitor = { ctx: monitorCtx, source, analyser, timer: 0 };
                tick();
            } catch (error) {
                state.pushError(stage || "activity_monitor", error);
            }
        };

        const createRecorderForStream = (meta, stream, constructStage, dataStage, errorStage, errorLabel) => {
            if (!meta || !stream) return false;
            const mimeType = chooseMimeType();
            try {
                const recorder = mimeType
                    ? new MediaRecorder(stream, { mimeType })
                    : new MediaRecorder(stream);
                return configureRecorder(meta, recorder, dataStage, errorStage, errorLabel);
            } catch (error) {
                state.pushError(constructStage, error);
                meta.recorder = null;
                return false;
            }
        };

        const ensureMediaStreamMeta = (stream, label) => {
            if (!stream || typeof MediaStream === "undefined" || !(stream instanceof MediaStream)) return null;
            for (const meta of state.mediaStreamMetas) {
                if (meta.stream === stream) return meta;
            }
            const meta = {
                kind: "media_stream",
                sessionId: state.currentSessionId,
                stream,
                recorder: null,
                mimeType: "audio/webm",
                chunks: [],
                totalBytes: 0,
                lastDataAt: 0,
                lastActiveAt: 0,
                lastRms: 0,
                label: String(label || "media_stream"),
                createdAt: now(),
            };
            if (!createRecorderForStream(
                meta,
                stream,
                "media_stream_recorder_construct",
                "media_stream_dataavailable",
                "media_stream_recorder_runtime",
                "media_stream_recorder_error",
            )) {
                return null;
            }
            state.mediaStreamMetas.push(meta);
            monitorAudioActivity(meta, stream, "media_stream_activity_monitor");
            state.pushEvent("media_stream_tracked", {
                label: meta.label,
                audio_tracks: typeof stream.getAudioTracks === "function" ? stream.getAudioTracks().length : 0,
            });
            return meta;
        };

        const startMediaStreamRecorder = (stream, label) => {
            const meta = ensureMediaStreamMeta(stream, label);
            if (!meta || !meta.recorder) return false;
            try {
                if (meta.recorder.state === "inactive") meta.recorder.start(250);
                state.pushEvent("media_stream_recorder_start", {
                    label: meta.label,
                });
                return true;
            } catch (error) {
                state.pushError("media_stream_recorder_start", error);
                return false;
            }
        };

        const muteMediaElement = (mediaElement, stage) => {
            if (!state.mutePlayback || !(mediaElement instanceof HTMLMediaElement)) return false;
            try {
                mediaElement.muted = true;
                mediaElement.volume = 0;
                return true;
            } catch (error) {
                state.pushError(stage || "media_element_mute", error);
                return false;
            }
        };

        const muteExistingMediaElements = () => {
            if (!state.mutePlayback || !document || !document.querySelectorAll) return;
            try {
                for (const mediaElement of document.querySelectorAll("audio, video")) {
                    muteMediaElement(mediaElement, "media_element_sweep_mute");
                }
            } catch (error) {
                state.pushError("media_element_sweep", error);
            }
        };

        const attachMediaPlaybackHooks = (meta) => {
            const mediaElement = meta && meta.mediaElement;
            if (!meta || !(mediaElement instanceof HTMLMediaElement) || meta._hooksInstalled) {
                return;
            }
            meta._hooksInstalled = true;

            const markPlayback = (stateName) => {
                meta.playbackState = String(stateName || "idle");
                meta.lastPlaybackEventAt = now();
                meta.isPlaying = meta.playbackState === "play" || meta.playbackState === "playing";
            };

            try {
                mediaElement.addEventListener("play", () => markPlayback("play"));
                mediaElement.addEventListener("playing", () => markPlayback("playing"));
                mediaElement.addEventListener("ended", () => markPlayback("ended"));
                mediaElement.addEventListener("emptied", () => markPlayback("emptied"));
                mediaElement.addEventListener("abort", () => markPlayback("abort"));
                mediaElement.addEventListener("pause", () => {
                    const duration = Number(mediaElement.duration || 0);
                    const currentTime = Number(mediaElement.currentTime || 0);
                    const reachedEnd = !!mediaElement.ended || (
                        duration > 0
                        && currentTime > 0
                        && (duration - currentTime) <= 0.2
                    );
                    markPlayback(reachedEnd ? "pause_end" : "pause");
                });
            } catch (error) {
                state.pushError("media_element_playback_hook", error);
            }

            try {
                if (!mediaElement.paused && !mediaElement.ended) {
                    markPlayback("playing");
                } else if (mediaElement.ended) {
                    markPlayback("ended");
                }
            } catch (error) {
                state.pushError("media_element_playback_init", error);
            }
        };

        const ensureMediaMeta = (mediaElement) => {
            if (!(mediaElement instanceof HTMLMediaElement)) return null;
            for (const meta of state.mediaElementMetas) {
                if (meta.mediaElement === mediaElement) {
                    attachMediaPlaybackHooks(meta);
                    return meta;
                }
            }
            const meta = {
                kind: "media_element",
                sessionId: state.currentSessionId,
                mediaElement,
                recorder: null,
                stream: null,
                mimeType: "",
                chunks: [],
                totalBytes: 0,
                lastDataAt: 0,
                playbackState: "idle",
                lastPlaybackEventAt: 0,
                isPlaying: false,
                _hooksInstalled: false,
                createdAt: now(),
            };
            state.mediaElementMetas.push(meta);
            attachMediaPlaybackHooks(meta);
            state.pushEvent("media_element_tracked", {
                tag: String(mediaElement.tagName || "").toLowerCase(),
                src: String(mediaElement.currentSrc || mediaElement.src || "").slice(0, 240),
            });
            return meta;
        };

        const startMediaElementRecorder = (mediaElement) => {
            if (!(mediaElement instanceof HTMLMediaElement)) return false;
            const meta = ensureMediaMeta(mediaElement);
            if (!meta) return false;
            if (state.mutePlayback) {
                muteMediaElement(mediaElement, "media_element_mute");
            }
            if (meta.recorder && meta.recorder.state === "recording") return true;

            if (!meta.recorder) {
                let stream = meta.stream;
                if (!stream) {
                    const captureStream = mediaElement.captureStream || mediaElement.mozCaptureStream;
                    if (typeof captureStream !== "function") {
                        state.pushError("media_capture_stream", "captureStream_unavailable");
                        return false;
                    }

                    try {
                        stream = captureStream.call(mediaElement);
                    } catch (error) {
                        state.pushError("media_capture_stream", error);
                        return false;
                    }
                }
                if (!stream) return false;

                meta.stream = stream;
                if (!createRecorderForStream(
                    meta,
                    stream,
                    "media_recorder_construct",
                    "media_dataavailable",
                    "media_recorder_runtime",
                    "media_recorder_error",
                )) {
                    return false;
                }
                monitorAudioActivity(meta, stream, "media_activity_monitor");
            }

            try {
                if (meta.recorder.state === "inactive") meta.recorder.start(250);
                state.pushEvent("media_recorder_start", {
                    src: String(mediaElement.currentSrc || mediaElement.src || "").slice(0, 240),
                });
                return true;
            } catch (error) {
                state.pushError("media_recorder_start", error);
                return false;
            }
        };

        const ensureWebAudioMeta = (ctx) => {
            if (!ctx) return null;
            for (const meta of state.webAudioMetas) {
                if (meta.ctx === ctx) return meta;
            }

            let dest;
            try {
                dest = ctx.createMediaStreamDestination();
            } catch (error) {
                state.pushError("web_audio_dest", error);
                return null;
            }

            const meta = {
                kind: "web_audio",
                sessionId: state.currentSessionId,
                ctx,
                dest,
                recorder: null,
                mimeType: "audio/webm",
                chunks: [],
                totalBytes: 0,
                lastDataAt: 0,
                tappedNodes: new WeakSet(),
                lastActiveAt: 0,
                lastRms: 0,
                createdAt: now(),
            };
            if (!createRecorderForStream(
                meta,
                dest.stream,
                "web_audio_recorder_construct",
                "web_audio_dataavailable",
                "web_audio_recorder_runtime",
                "web_audio_recorder_error",
            )) {
                return null;
            }
            state.webAudioMetas.push(meta);
            monitorAudioActivity(meta, dest.stream, "web_audio_activity_monitor");
            state.pushEvent("web_audio_context_tracked", {
                sampleRate: Number(ctx.sampleRate || 0),
            });
            return meta;
        };

        if (!window.__uwaAudioCapturePatches || window.__uwaAudioCapturePatches.version !== scriptVersion) {
            window.__uwaAudioCapturePatches = { version: scriptVersion };
        }
        const patches = window.__uwaAudioCapturePatches;
        patches.version = scriptVersion;

        if (!patches.mediaPlay && typeof HTMLMediaElement !== "undefined") {
            const origMediaPlay = HTMLMediaElement.prototype.play;
            HTMLMediaElement.prototype.play = function(...args) {
                if (state.mutePlayback) {
                    muteMediaElement(this, "media_element_pre_mute");
                }
                const result = origMediaPlay.apply(this, args);
                try {
                    startMediaElementRecorder(this);
                } catch (error) {
                    state.pushError("media_play_hook", error);
                }
                return result;
            };
            patches.mediaPlay = true;
        }

        if (!patches.mediaMuteTimer) {
            try {
                patches.mediaMuteTimer = window.setInterval(() => {
                    if (state.mutePlayback) muteExistingMediaElements();
                }, 500);
            } catch (error) {
                state.pushError("media_mute_timer", error);
            }
        }

        if (!patches.audioCtor && typeof window.Audio === "function") {
            const OrigAudio = window.Audio;
            const WrappedAudio = function(...args) {
                const audio = new OrigAudio(...args);
                try {
                    ensureMediaMeta(audio);
                } catch (error) {
                    state.pushError("audio_ctor", error);
                }
                return audio;
            };
            WrappedAudio.prototype = OrigAudio.prototype;
            window.Audio = WrappedAudio;
            patches.audioCtor = true;
        }

        if (!patches.createElement && typeof Document !== "undefined") {
            const origCreateElement = Document.prototype.createElement;
            Document.prototype.createElement = function(tagName, ...rest) {
                const element = origCreateElement.call(this, tagName, ...rest);
                try {
                    if (String(tagName || "").toLowerCase() === "audio") {
                        ensureMediaMeta(element);
                    }
                } catch (error) {
                    state.pushError("create_element", error);
                }
                return element;
            };
            patches.createElement = true;
        }

        if (!patches.mediaSrcObject && typeof HTMLMediaElement !== "undefined") {
            const descriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "srcObject");
            if (descriptor && typeof descriptor.get === "function" && typeof descriptor.set === "function") {
                Object.defineProperty(HTMLMediaElement.prototype, "srcObject", {
                    configurable: true,
                    enumerable: descriptor.enumerable,
                    get: function() {
                        return descriptor.get.call(this);
                    },
                    set: function(value) {
                        try {
                            if (value && typeof MediaStream !== "undefined" && value instanceof MediaStream) {
                                ensureMediaMeta(this);
                                startMediaStreamRecorder(value, "media_element_srcObject");
                            }
                        } catch (error) {
                            state.pushError("media_srcObject_set", error);
                        }
                        return descriptor.set.call(this, value);
                    }
                });
                patches.mediaSrcObject = true;
            }
        }

        const patchAudioContextPrototype = (proto) => {
            if (!proto || proto.__uwaCreateMediaElementSourcePatched || typeof proto.createMediaElementSource !== "function") {
                return;
            }
            const origCreateMediaElementSource = proto.createMediaElementSource;
            proto.createMediaElementSource = function(mediaElement) {
                try {
                    ensureMediaMeta(mediaElement);
                } catch (error) {
                    state.pushError("create_media_element_source", error);
                }
                return origCreateMediaElementSource.call(this, mediaElement);
            };
            proto.__uwaCreateMediaElementSourcePatched = true;
        };
        patchAudioContextPrototype(window.AudioContext && window.AudioContext.prototype);
        patchAudioContextPrototype(window.webkitAudioContext && window.webkitAudioContext.prototype);

        const patchAudioContextStreamPrototype = (proto) => {
            if (!proto) return;
            if (!proto.__uwaCreateMediaStreamSourcePatched && typeof proto.createMediaStreamSource === "function") {
                const origCreateMediaStreamSource = proto.createMediaStreamSource;
                proto.createMediaStreamSource = function(stream) {
                    try {
                        startMediaStreamRecorder(stream, "createMediaStreamSource");
                    } catch (error) {
                        state.pushError("create_media_stream_source", error);
                    }
                    return origCreateMediaStreamSource.call(this, stream);
                };
                proto.__uwaCreateMediaStreamSourcePatched = true;
            }
        };
        patchAudioContextStreamPrototype(window.AudioContext && window.AudioContext.prototype);
        patchAudioContextStreamPrototype(window.webkitAudioContext && window.webkitAudioContext.prototype);

        if (!patches.audioNodeConnect && typeof AudioNode !== "undefined") {
            const origAudioNodeConnect = AudioNode.prototype.connect;
            AudioNode.prototype.connect = function(...args) {
                const target = args[0];
                const isDestinationConnect = target && this.context && target === this.context.destination;
                const result = isDestinationConnect && state.mutePlayback
                    ? target
                    : origAudioNodeConnect.apply(this, args);
                try {
                    if (isDestinationConnect) {
                        const meta = ensureWebAudioMeta(this.context);
                        if (meta && !meta.tappedNodes.has(this)) {
                            origAudioNodeConnect.apply(this, [meta.dest, ...args.slice(1)]);
                            meta.tappedNodes.add(this);
                            if (meta.recorder && meta.recorder.state === "inactive") {
                                meta.recorder.start(250);
                            }
                            state.pushEvent("web_audio_tapped", {
                                source: String(this.constructor && this.constructor.name || "AudioNode"),
                                target: String(target.constructor && target.constructor.name || "AudioDestinationNode"),
                                muted: !!state.mutePlayback,
                            });
                        }
                    }
                } catch (error) {
                    state.pushError("audio_node_connect", error);
                }
                return result;
            };
            patches.audioNodeConnect = true;
        }

        const shouldReset = !opts || opts.reset !== false;
        const applyReset = async () => {
            if (shouldReset) {
                await state.reset({
                    preserveGraph: !!(opts && opts.preserveGraph),
                });
            } else if (state.currentSessionId === 0) {
                state.currentSessionId = 1;
            }
            muteExistingMediaElements();
            return {
                installed: true,
                status: state.status(),
            };
        };

        return applyReset();
    })(typeof arguments !== "undefined" ? arguments[0] : window.__uwaAudioCaptureInitOptions);
    """

    EXPORT_PAGE_AUDIO_CAPTURE_JS = r"""
    return (async function(opts) {
        const state = window.__uwaAudioCapture;
        if (!state || typeof state.export !== "function") {
            return { items: [], warnings: ["capture_not_installed"], status: {} };
        }
        return await state.export(opts || {});
    })(arguments[0]);
    """

    PAGE_AUDIO_CAPTURE_STATUS_JS = r"""
    return (function() {
        const state = window.__uwaAudioCapture;
        if (!state || typeof state.status !== "function") {
            return {};
        }
        return state.status();
    })();
    """

    ACTIVATE_AUDIO_TRIGGER_SURFACE_JS = r"""
    return (function() {
        const roots = [];
        const seen = new Set();
        const pushRoot = (value) => {
            if (!(value instanceof Element) || seen.has(value)) return;
            seen.add(value);
            roots.push(value);
        };

        if (this instanceof Document) {
            pushRoot(this.body || this.documentElement);
        } else if (this instanceof Element) {
            let current = this;
            for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
                pushRoot(current);
            }
        }
        pushRoot(document.body || document.documentElement);

        const describe = (el, reason = "") => ({
            tag: String(el && el.tagName || "").toLowerCase(),
            aria_label: String(el && el.getAttribute && el.getAttribute("aria-label") || "").trim(),
            data_testid: String(el && el.getAttribute && el.getAttribute("data-testid") || "").trim(),
            class_name: String(el && el.className || "").trim().slice(0, 180),
            text: String(el && (el.innerText || el.textContent) || "").trim().slice(0, 120),
            reason,
        });

        const candidates = [];
        for (const root of roots) {
            if (!(root instanceof Element)) continue;
            candidates.push(root);
            const selectors = [
                '[data-testid*="message"]',
                '[data-testid*="content"]',
                '[aria-label="doc_editor"]',
                '[role="article"]',
                '[class*="message"]',
                '[class*="content"]',
                '[class*="assistant"]',
            ];
            for (const selector of selectors) {
                let nodes = [];
                try {
                    nodes = Array.from(root.querySelectorAll(selector));
                } catch {}
                for (const node of nodes.slice(-6)) {
                    if (node instanceof Element) {
                        candidates.push(node);
                    }
                }
            }
        }

        const uniqueCandidates = [];
        const candidateSeen = new Set();
        for (const node of candidates) {
            if (!(node instanceof Element) || candidateSeen.has(node)) continue;
            candidateSeen.add(node);
            uniqueCandidates.push(node);
        }

        const activated = [];
        for (const node of uniqueCandidates.slice(0, 12)) {
            try {
                if (typeof node.scrollIntoView === "function") {
                    node.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
                }
            } catch {}
            try {
                const rect = typeof node.getBoundingClientRect === "function" ? node.getBoundingClientRect() : null;
                const clientX = rect ? rect.left + Math.min(Math.max(rect.width / 2, 6), Math.max(rect.width - 6, 6)) : 24;
                const clientY = rect ? rect.top + Math.min(Math.max(rect.height / 2, 6), Math.max(rect.height - 6, 6)) : 24;
                const eventInit = {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                    clientX,
                    clientY,
                };
                node.dispatchEvent(new PointerEvent("pointerenter", eventInit));
                node.dispatchEvent(new PointerEvent("pointerover", eventInit));
                node.dispatchEvent(new PointerEvent("pointermove", eventInit));
                node.dispatchEvent(new MouseEvent("mouseenter", eventInit));
                node.dispatchEvent(new MouseEvent("mouseover", eventInit));
                node.dispatchEvent(new MouseEvent("mousemove", eventInit));
                activated.push(describe(node, "pointer_and_mouse_move"));
            } catch (error) {
                activated.push(describe(node, `dispatch_failed:${String(error && (error.message || error.name) || error).slice(0, 80)}`));
            }
        }

        return {
            ok: activated.length > 0,
            activated_count: activated.length,
            activated,
        };
    }).call(this);
    """

    TRIGGER_AUDIO_PLAYBACK_JS = r"""
    return (function(opts) {
        const fallbackLabels = ["朗读", "语音朗读", "收听", "开启自动播报", "自动播报", "播报", "read aloud", "listen", "tts", "voice"];
        const labels = Array.isArray(opts && opts.audioTriggerLabels) && opts.audioTriggerLabels.length
            ? opts.audioTriggerLabels
            : fallbackLabels;
        const normalizedLabels = labels
            .map(item => String(item || "").trim().toLowerCase())
            .filter(Boolean);
        const triggerSelector = String(opts && opts.audioTriggerSelector || "").trim();
        const roots = [];
        const rootSeen = new Set();
        const pushRoot = (value) => {
            if (!(value instanceof Element) || rootSeen.has(value)) return;
            rootSeen.add(value);
            roots.push(value);
        };

        if (this instanceof Document) {
            pushRoot(this.body || this.documentElement);
        } else if (this instanceof Element) {
            let current = this;
            for (let depth = 0; current && depth < 6; depth += 1, current = current.parentElement) {
                pushRoot(current);
            }
        }
        pushRoot(document.body || document.documentElement);

        const candidateMap = new Map();
        const debugMatches = [];
        const textOf = (el) => [
            String(el.innerText || "").trim(),
            String(el.getAttribute("aria-label") || "").trim(),
            String(el.getAttribute("title") || "").trim(),
            String(el.getAttribute("data-testid") || "").trim(),
        ].filter(Boolean).join(" ").trim();
        const describeNode = (el, extra = {}) => ({
            tag: String(el && el.tagName || "").toLowerCase(),
            aria_label: String(el && el.getAttribute && el.getAttribute("aria-label") || "").trim(),
            data_testid: String(el && el.getAttribute && el.getAttribute("data-testid") || "").trim(),
            data_dbx_name: String(el && el.getAttribute && el.getAttribute("data-dbx-name") || "").trim(),
            class_name: String(el && el.className || "").trim().slice(0, 180),
            text: String(textOf(el) || "").slice(0, 120),
            ...extra,
        });
        const isExplicitlyClickable = (el) => {
            if (!(el instanceof HTMLElement)) return false;
            const tag = String(el.tagName || "").toLowerCase();
            if (tag === "button") return true;
            if (tag === "a" && el.hasAttribute("href")) return true;
            if (el.getAttribute("role") === "button") return true;
            if (typeof el.onclick === "function") return true;
            if (el.tabIndex >= 0 && (
                el.getAttribute("aria-label")
                || el.getAttribute("title")
                || el.getAttribute("data-testid")
            )) {
                return true;
            }
            const classText = String(el.className || "").toLowerCase();
            if (classText.includes("button") || classText.includes("btn") || classText.includes("icon")) {
                return true;
            }
            return false;
        };
        const isCandidate = (el) => {
            if (!(el instanceof HTMLElement)) return false;
            if (el.disabled || el.getAttribute("aria-disabled") === "true") return false;
            const rect = el.getBoundingClientRect();
            const hasGeometry = !!(rect && rect.width >= 1 && rect.height >= 1);
            if (!triggerSelector && !isExplicitlyClickable(el)) return false;
            if (triggerSelector) {
                if (hasGeometry) return true;
                const ariaLabel = String(el.getAttribute("aria-label") || "").trim().toLowerCase();
                return normalizedLabels.some(label => ariaLabel.includes(label));
            }
            if (!hasGeometry) return false;
            const haystack = textOf(el).toLowerCase();
            return normalizedLabels.some(label => haystack.includes(label));
        };
        const scoreOf = (el, rootIndex) => {
            let score = Math.max(0, 120 - rootIndex * 20);
            const haystack = textOf(el).toLowerCase();
            const ownText = String(el.innerText || el.textContent || "").trim();
            const tag = String(el.tagName || "").toLowerCase();
            const rect = el.getBoundingClientRect();
            for (const label of normalizedLabels) {
                if (haystack === label) score += 120;
                else if (haystack.includes(label)) score += 60;
            }
            const classText = String(el.className || "").toLowerCase();
            if (classText.includes("voice")) score += 20;
            if (classText.includes("audio")) score += 20;
            if (tag === "button") score += 30;
            if (el.getAttribute("role") === "button") score += 20;
            if (ownText && ownText.length > 24) score -= 40;
            if (rect && rect.width > 220) score -= 20;
            if (rect && rect.height > 120) score -= 20;
            if (classText.includes("doc_editor") || classText.includes("message") || classText.includes("content")) score -= 60;
            return score;
        };
        const selectors = triggerSelector
            ? [triggerSelector]
            : ["button", "[role=\"button\"]", "[aria-label]", "[title]", "[data-testid]"];

        roots.forEach((root, rootIndex) => {
            for (const selector of selectors) {
                let nodes = [];
                try {
                    nodes = Array.from(root.querySelectorAll(selector));
                } catch {}
                for (const node of nodes) {
                    const haystack = textOf(node).toLowerCase();
                    if (debugMatches.length < 12 && haystack && normalizedLabels.some(label => haystack.includes(label))) {
                        debugMatches.push(describeNode(node, { selector, root_index: rootIndex }));
                    }
                    if (!isCandidate(node)) continue;
                    const score = scoreOf(node, rootIndex);
                    const existing = candidateMap.get(node);
                    if (!existing || score > existing.score) {
                        candidateMap.set(node, {
                            node,
                            score,
                            text: textOf(node),
                        });
                    }
                }
            }
        });

        if (!candidateMap.size) {
            const hardcodedFallbackSelector = [
                'button[aria-label="朗读"]',
                'button[aria-label*="朗读"]',
                'button[data-dbx-name="button"][aria-label*="朗读"]',
                'button.voiceButton-GAZh3G',
            ].join(', ');
            try {
                for (const node of document.querySelectorAll(hardcodedFallbackSelector)) {
                    if (!(node instanceof HTMLElement)) continue;
                    candidateMap.set(node, {
                        node,
                        score: 999,
                        text: textOf(node) || String(node.getAttribute("aria-label") || "").trim(),
                    });
                }
            } catch {}
        }

        const candidates = Array.from(candidateMap.values()).sort((a, b) => b.score - a.score);
        if (!candidates.length) {
            const nearbyCandidates = [];
            const visibleButtonSamples = [];
            try {
                const probeSelectors = ["button", "[role=\"button\"]", "[aria-label]", "[title]", "[data-testid]", "svg"];
                for (const selector of probeSelectors) {
                    let nodes = [];
                    try {
                        nodes = Array.from(document.querySelectorAll(selector));
                    } catch {}
                    for (const node of nodes) {
                        if (!(node instanceof Element)) continue;
                        const info = describeNode(node, { selector });
                        const haystack = String(info.text || info.aria_label || info.data_testid || info.class_name || "").toLowerCase();
                        if (visibleButtonSamples.length < 16) {
                            const rect = typeof node.getBoundingClientRect === "function" ? node.getBoundingClientRect() : null;
                            if (rect && rect.width >= 1 && rect.height >= 1) {
                                visibleButtonSamples.push(info);
                            }
                        }
                        if (!haystack) continue;
                        if (
                            haystack.includes("朗") ||
                            haystack.includes("读") ||
                            haystack.includes("收听") ||
                            haystack.includes("voice") ||
                            haystack.includes("audio") ||
                            haystack.includes("tts")
                        ) {
                            nearbyCandidates.push(info);
                            if (nearbyCandidates.length >= 12) break;
                        }
                    }
                    if (nearbyCandidates.length >= 12) break;
                }
            } catch {}
            return {
                clicked: false,
                candidate_count: 0,
                selector_used: triggerSelector || selectors.join(", "),
                labels_used: normalizedLabels,
                debug_matches: debugMatches.slice(0, 8),
                nearby_candidates: nearbyCandidates,
                visible_button_samples: visibleButtonSamples,
            };
        }

        if (opts && opts.probeOnly) {
            return {
                clicked: false,
                found: true,
                candidate_count: candidates.length,
                text: candidates[0].text,
                score: candidates[0].score,
                selector_used: triggerSelector || selectors.join(", "),
            };
        }

        const target = candidates[0].node;
        try {
            try {
                target.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, cancelable: true, composed: true }));
                target.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, composed: true }));
                target.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, cancelable: true, composed: true }));
                target.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, composed: true }));
            } catch {}
            target.click();
            return {
                clicked: true,
                candidate_count: candidates.length,
                text: candidates[0].text,
                score: candidates[0].score,
                selector_used: triggerSelector || selectors.join(", "),
            };
        } catch (error) {
            return {
                clicked: false,
                candidate_count: candidates.length,
                error: String(error).slice(0, 160),
                selector_used: triggerSelector || selectors.join(", "),
                debug_matches: candidates.slice(0, 5).map(item => describeNode(item.node, { score: item.score })),
            };
        }
    }).call(this, arguments[0]);
    """

    def extract(
        self,
        element: Any,
        config: Optional[Dict] = None,
        container_selector_fallback: Optional[str] = None,
    ) -> List[Dict]:
        """提取配置中启用的媒体资源。"""
        final_config = get_default_image_extraction_config()
        if config:
            final_config.update(config)
            final_config["modalities"] = normalize_modalities_config(config.get("modalities") or {})

        final_config["enabled"] = bool(final_config.get("enabled")) or any(
            is_modality_enabled(final_config.get("modalities") or {}, key)
            for key in ("image", "audio", "video")
        )

        modalities = dict(final_config.get("modalities") or {})
        enabled = bool(final_config.get("enabled"))
        if not enabled and not any(is_modality_enabled(modalities, key) for key in ("image", "audio", "video")):
            return []
        if not element:
            return []

        media_items: List[Dict] = []

        if is_modality_enabled(modalities, "image"):
            image_policy = get_modality_policy(modalities, "image")
            image_config = dict(final_config)
            if image_policy.get("selector"):
                image_config["selector"] = image_policy.get("selector")
            if "container_selector" in image_policy:
                image_config["container_selector"] = image_policy.get("container_selector")
            images = image_extractor.extract(
                element,
                config=image_config,
                container_selector_fallback=container_selector_fallback,
            )
            for item in images:
                media_items.append({
                    **item,
                    "media_type": "image",
                    "label": item.get("alt"),
                })

        if is_modality_enabled(modalities, "audio"):
            audio_policy = get_modality_policy(modalities, "audio")
            media_items.extend(
                self._extract_media_type(
                    element=element,
                    media_type="audio",
                    selector=audio_policy.get("selector") or final_config.get("audio_selector", "audio, audio source"),
                    config=final_config,
                    container_selector_fallback=container_selector_fallback,
                )
            )

        if is_modality_enabled(modalities, "video"):
            video_policy = get_modality_policy(modalities, "video")
            media_items.extend(
                self._extract_media_type(
                    element=element,
                    media_type="video",
                    selector=video_policy.get("selector") or final_config.get("video_selector", "video, video source"),
                    config=final_config,
                    container_selector_fallback=container_selector_fallback,
                )
            )

        return media_items

    def build_page_audio_capture_init_script(self, config: Optional[Dict] = None) -> str:
        final_config = get_default_image_extraction_config()
        if config:
            final_config.update(config)

        opts = {
            "reset": False,
            "mutePlayback": bool(final_config.get("audio_capture_mute_playback", True)),
            "preserveGraph": True,
            "activityThreshold": float(final_config.get("audio_capture_activity_threshold") or 0.006),
            "maxCaptureBytes": int(final_config.get("max_size_mb", 10) * 1024 * 1024),
        }
        opts_json = json.dumps(opts, ensure_ascii=False, separators=(",", ":"))
        init_body = self.INSTALL_PAGE_AUDIO_CAPTURE_JS.lstrip()
        if init_body.startswith("return "):
            init_body = init_body[len("return "):]
        return (
            f"window.__uwaAudioCaptureInitOptions={opts_json};\n"
            f"{init_body}"
        )

    def prepare_page_audio_capture(self, tab: Any, config: Optional[Dict] = None) -> bool:
        if not tab:
            return False
        final_config = get_default_image_extraction_config()
        if config:
            final_config.update(config)
        try:
            final_config = get_default_image_extraction_config()
            if config:
                final_config.update(config)
            tab.run_js(
                self.INSTALL_PAGE_AUDIO_CAPTURE_JS,
                {
                    "reset": True,
                    "mutePlayback": bool(final_config.get("audio_capture_mute_playback", True)),
                    "preserveGraph": bool(final_config.get("audio_capture_preserve_graph", True)),
                    "activityThreshold": float(final_config.get("audio_capture_activity_threshold") or 0.006),
                    "maxCaptureBytes": int(final_config.get("max_size_mb", 10) * 1024 * 1024),
                },
            )
            return True
        except Exception as exc:
            logger.debug(f"页面音频捕获初始化失败（已忽略）: {exc}")
            return False

    def get_page_audio_capture_status(self, tab: Any) -> Dict[str, Any]:
        if not tab:
            return {}
        try:
            result = tab.run_js(self.PAGE_AUDIO_CAPTURE_STATUS_JS) or {}
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            logger.debug(f"读取页面音频捕获状态失败（已忽略）: {exc}")
            return {}

    def activate_audio_trigger_surface(self, element: Any) -> Dict[str, Any]:
        if not element:
            return {}
        try:
            result = element.run_js(self.ACTIVATE_AUDIO_TRIGGER_SURFACE_JS) or {}
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            logger.debug(f"激活页面音频操作区失败（已忽略）: {exc}")
            return {}

    def trigger_audio_playback(
        self,
        element: Any,
        config: Optional[Dict] = None,
        *,
        probe_only: bool = False,
    ) -> Dict[str, Any]:
        if not element:
            return {}

        final_config = get_default_image_extraction_config()
        if config:
            final_config.update(config)

        js_opts = {
            "audioTriggerSelector": final_config.get("audio_trigger_selector") or "",
            "audioTriggerLabels": final_config.get("audio_trigger_labels") or [],
            "probeOnly": bool(probe_only),
        }

        try:
            result = element.run_js(self.TRIGGER_AUDIO_PLAYBACK_JS, js_opts) or {}
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            logger.debug(f"触发页面音频播放失败（已忽略）: {exc}")
            return {}

    def probe_audio_trigger(
        self,
        element: Any,
        config: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        return self.trigger_audio_playback(element, config=config, probe_only=True)

    def install_audio_network_probe(self, tab: Any, config: Optional[Dict] = None) -> bool:
        return self._install_audio_network_probe(tab, config=config, clear=True)

    def _install_audio_network_probe(
        self,
        tab: Any,
        config: Optional[Dict] = None,
        *,
        clear: bool,
    ) -> bool:
        if not tab:
            return False
        final_config = get_default_image_extraction_config()
        if config:
            final_config.update(config)
        network_config = self._get_audio_network_capture_config(final_config)
        try:
            tab.run_js(
                PAGE_TTS_WS_PROBE_INSTALL_JS,
                {
                    "clear": bool(clear),
                    "maxLogs": int(network_config.get("max_logs") or 1024),
                    "maxPayloadBytes": int(network_config.get("max_payload_bytes") or final_config.get("max_size_mb", 10) * 1024 * 1024),
                    "urlPatterns": network_config.get("url_patterns") or [],
                },
            )
            return True
        except Exception as exc:
            logger.debug(f"安装页面 TTS 网络探针失败（已忽略）: {exc}")
            return False

    def clear_audio_network_probe(self, tab: Any) -> bool:
        if not tab:
            return False
        try:
            tab.run_js(
                PAGE_TTS_WS_PROBE_INSTALL_JS,
                {
                    "clear": True,
                    "maxLogs": 256,
                    "maxPayloadBytes": int(get_default_image_extraction_config().get("max_size_mb", 10) * 1024 * 1024),
                    "urlPatterns": [],
                },
            )
            return True
        except Exception as exc:
            logger.debug(f"清空页面 TTS 网络探针失败（已忽略）: {exc}")
            return False

    def capture_browser_tts_fallback(
        self,
        tab: Any,
        config: Optional[Dict] = None,
        stop_checker: Optional[Any] = None,
        response_text_hint: str = "",
    ) -> List[Dict]:
        if not tab:
            return []

        final_config = get_default_image_extraction_config()
        if config:
            final_config.update(config)

        fallback_config = dict(final_config.get("audio_browser_tts_fallback") or {})
        if not bool(fallback_config.get("enabled", False)):
            return []

        if str(fallback_config.get("provider") or "").strip() != "doubao_samantha":
            return []

        hint_text = str(response_text_hint or "").strip()
        if not hint_text:
            logger.debug("浏览器 TTS 兜底跳过：缺少 response_text_hint")
            return []

        timeout_seconds = max(3.0, min(120.0, float(fallback_config.get("timeout_seconds") or 30.0)))
        timeout_ms = int(timeout_seconds * 1000)
        effective_stop_checker = stop_checker or (lambda: False)
        start_opts = {
            "text": hint_text,
            "speaker": str(fallback_config.get("speaker") or "2").strip() or "2",
            "speech_rate": int(fallback_config.get("speech_rate") or 0),
            "pitch": int(fallback_config.get("pitch") or 0),
            "format": str(fallback_config.get("format") or "aac").strip().lower() or "aac",
            "timeout_ms": timeout_ms,
            "max_bytes": int(final_config.get("max_size_mb", 10) * 1024 * 1024),
            "pc_version": str(fallback_config.get("pc_version") or "3.20.2").strip() or "3.20.2",
            "aid": str(fallback_config.get("aid") or "497858").strip() or "497858",
            "real_aid": str(fallback_config.get("real_aid") or "497858").strip() or "497858",
            "language": str(fallback_config.get("language") or "zh").strip() or "zh",
            "device_platform": str(fallback_config.get("device_platform") or "web").strip() or "web",
            "pkg_type": str(fallback_config.get("pkg_type") or "release_version").strip() or "release_version",
            "region": str(fallback_config.get("region") or "CN").strip() or "CN",
            "sys_region": str(fallback_config.get("sys_region") or "CN").strip() or "CN",
            "use_olympus_account": str(fallback_config.get("use_olympus_account") or "1").strip() or "1",
            "samantha_web": str(fallback_config.get("samantha_web") or "1").strip() or "1",
        }

        try:
            start_result = tab.run_js(PAGE_BROWSER_TTS_FALLBACK_START_JS, start_opts) or {}
        except Exception as exc:
            logger.debug(f"浏览器 TTS 兜底启动失败（已忽略）: {exc}")
            return []

        if not isinstance(start_result, dict) or not bool(start_result.get("ok")):
            logger.debug(
                "浏览器 TTS 兜底启动失败: "
                f"{start_result if isinstance(start_result, dict) else repr(start_result)}"
            )
            return []

        logger.debug(
            "浏览器 TTS 兜底已启动: "
            f"speaker={start_opts['speaker']!r}, format={start_opts['format']!r}, "
            f"text_len={len(hint_text)}, timeout={timeout_seconds:.1f}s"
        )

        deadline = time.time() + timeout_seconds + 2.0
        last_chunks = 0
        last_bytes = 0
        status: Dict[str, Any] = {}

        while time.time() < deadline and not effective_stop_checker():
            time.sleep(0.2)
            try:
                status = tab.run_js(PAGE_BROWSER_TTS_FALLBACK_STATUS_JS) or {}
            except Exception as exc:
                logger.debug(f"读取浏览器 TTS 兜底状态失败（已忽略）: {exc}")
                status = {}

            if not isinstance(status, dict) or not status:
                continue

            current_chunks = int(status.get("received_chunks") or 0)
            current_bytes = int(status.get("received_bytes") or 0)
            if current_chunks > last_chunks or current_bytes > last_bytes:
                last_chunks = current_chunks
                last_bytes = current_bytes
                logger.debug(
                    "浏览器 TTS 兜底增长: "
                    f"chunks={current_chunks}, bytes={current_bytes}, phase={status.get('phase')!r}"
                )

            if bool(status.get("completed")) and (bool(status.get("has_data")) or bool(status.get("error"))):
                break

        if effective_stop_checker():
            return []

        if not status:
            try:
                status = tab.run_js(PAGE_BROWSER_TTS_FALLBACK_STATUS_JS) or {}
            except Exception:
                status = {}

        if not isinstance(status, dict):
            return []

        data_uri = str(status.get("data_uri") or "").strip()
        if not data_uri.startswith("data:audio/"):
            logger.debug(
                "浏览器 TTS 兜底未产出音频: "
                f"phase={status.get('phase')!r}, error={status.get('error')!r}, "
                f"chunks={status.get('received_chunks')}, bytes={status.get('received_bytes')}, "
                f"events={status.get('events')!r}"
            )
            return []

        media_item = {
            "media_type": "audio",
            "kind": "data_uri",
            "data_uri": data_uri,
            "mime": str(status.get("mime") or "audio/aac").strip() or "audio/aac",
            "byte_size": int(status.get("received_bytes") or 0),
            "label": "browser_tts_fallback",
            "source": "browser_tts_fallback",
        }
        logger.debug(
            "浏览器 TTS 兜底成功: "
            f"bytes={media_item.get('byte_size')}, chunks={status.get('received_chunks')}, "
            f"events={status.get('events')!r}"
        )
        return [media_item]

    def capture_network_audio(
        self,
        tab: Any,
        config: Optional[Dict] = None,
        stop_checker: Optional[Any] = None,
        response_text_hint: str = "",
    ) -> List[Dict]:
        if not tab:
            return []

        final_config = get_default_image_extraction_config()
        if config:
            final_config.update(config)
        network_config = self._get_audio_network_capture_config(final_config)
        if not bool(network_config.get("enabled", False)):
            return []

        effective_stop_checker = stop_checker or (lambda: False)
        timeout_seconds = max(0.2, float(network_config.get("timeout_seconds") or 2.5))
        settle_seconds = max(0.05, float(network_config.get("settle_seconds") or 0.35))
        hint_text = str(response_text_hint or "").strip()
        if hint_text:
            try:
                chars_per_second = max(
                    1.0,
                    float(final_config.get("audio_capture_estimated_chars_per_second") or 4.8),
                )
                padding_seconds = max(
                    0.0,
                    float(final_config.get("audio_capture_wait_padding_seconds") or 1.2),
                )
                hard_cap = max(
                    timeout_seconds,
                    min(
                        30.0,
                        float(final_config.get("audio_capture_hard_max_wait_seconds") or 30.0),
                    ),
                )
                estimated_timeout = (len(hint_text) / chars_per_second) + padding_seconds
                timeout_seconds = min(max(timeout_seconds, estimated_timeout), hard_cap)
            except (TypeError, ValueError):
                pass
        url_patterns = [
            str(item or "").strip().lower()
            for item in (network_config.get("url_patterns") or [])
            if str(item or "").strip()
        ]
        if not url_patterns:
            return []

        extractor_name = str(network_config.get("extractor") or "").strip() or "voicegenie_binary_stream"
        extractor = self._get_audio_network_extractor(extractor_name)
        if extractor is None:
            logger.debug(f"未知网络音频提取器（已忽略）: {extractor_name}")
            return []

        save_dir = Path("download_images")
        save_dir.mkdir(exist_ok=True)
        deadline = time.time() + timeout_seconds
        best_event: Dict[str, Any] = {}
        best_size = 0
        last_growth_at = 0.0
        saw_stop_marker = False

        try:
            self._install_audio_network_probe(tab, final_config, clear=False)

            while time.time() < deadline and not effective_stop_checker():
                time.sleep(0.15)
                logs = tab.run_js(PAGE_TTS_WS_PROBE_DUMP_JS) or []
                if not isinstance(logs, list) or not logs:
                    continue

                event = extractor(logs, url_patterns)
                if not event:
                    continue

                current_size = len(event.get("body_bytes") or b"")
                saw_stop_marker = saw_stop_marker or bool(event.get("seen_stop_marker"))
                if current_size > best_size:
                    best_event = event
                    best_size = current_size
                    last_growth_at = time.time()
                    logger.debug(
                        "网络音频捕获增长: "
                        f"url={str(event.get('url') or '')[:160]!r}, bytes={current_size}"
                    )
                    continue

                if (
                    bool(event.get("seen_stop_marker"))
                    and best_event
                    and not bool(best_event.get("seen_stop_marker"))
                ):
                    best_event = event

                if (
                    saw_stop_marker
                    and best_event
                    and last_growth_at
                    and (time.time() - last_growth_at) >= settle_seconds
                ):
                    break
        except Exception as exc:
            logger.debug(f"网络音频捕获失败（已忽略）: {exc}")

        if not best_event:
            try:
                logs = tab.run_js(PAGE_TTS_WS_PROBE_DUMP_JS) or []
                related_count = 0
                summaries: List[str] = []
                for item in logs[-32:]:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "").strip()
                    lowered_url = url.lower()
                    content_type = str(item.get("content_type") or "").strip()
                    if not (
                        any(pattern.lower() in lowered_url for pattern in url_patterns)
                        or ("audio" in content_type.lower() if content_type else False)
                    ):
                        continue
                    related_count += 1
                    summaries.append(
                        (
                            f"{str(item.get('transport') or '')}:{str(item.get('dir') or '')}:"
                            f"status={item.get('status')}:"
                            f"content_type={content_type[:48]!r}:"
                            f"url={url[:120]!r}"
                        )
                    )
                    if len(summaries) >= 8:
                        break
                if summaries:
                    logger.debug(
                        "网络音频捕获未命中，最近相关网络摘要: "
                        + " | ".join(summaries)
                    )
                elif isinstance(logs, list):
                    logger.debug(
                        "网络音频捕获未命中：探针中没有采集到任何相关事件 "
                        f"(total_logs={len(logs)}, patterns={url_patterns!r}, related={related_count})"
                    )
            except Exception as exc:
                logger.debug(f"读取网络音频探针摘要失败（已忽略）: {exc}")
            return []

        if not self._is_network_audio_event_usable(
            best_event,
            response_text_hint=response_text_hint,
        ):
            logger.debug(
                "网络音频捕获结果疑似截断，放弃直抓结果并回退页面录音: "
                f"bytes={len(best_event.get('body_bytes') or b'')}, "
                f"pages={best_event.get('page_count')}, "
                f"gap_count={best_event.get('gap_count')}, "
                f"seen_stop={best_event.get('seen_stop_marker')}"
            )
            return []

        media_item = self._persist_network_audio_event(best_event, save_dir)
        if not media_item:
            return []

        logger.debug(
            "网络音频捕获命中: "
            f"url={str(best_event.get('url') or '')[:160]!r}, "
            f"mime={media_item.get('mime')!r}, bytes={media_item.get('byte_size')}, "
            f"pages={best_event.get('page_count')}, gap_count={best_event.get('gap_count')}, "
            f"seen_stop={best_event.get('seen_stop_marker')}"
        )
        return [media_item]

    def export_page_audio_capture(self, tab: Any, config: Optional[Dict] = None) -> List[Dict]:
        if not tab:
            return []

        final_config = get_default_image_extraction_config()
        if config:
            final_config.update(config)

        js_opts = {
            "maxBytes": int(final_config.get("max_size_mb", 10) * 1024 * 1024),
        }
        try:
            result = tab.run_js(self.EXPORT_PAGE_AUDIO_CAPTURE_JS, js_opts) or {}
        except Exception as exc:
            logger.debug(f"导出页面音频捕获失败（已忽略）: {exc}")
            return []

        if not isinstance(result, dict):
            return []

        for warning in result.get("warnings", []):
            logger.warning(f"页面音频捕获告警: {warning}")

        normalized_items = self._normalize_media_items("audio", result.get("items", []))
        filtered_items: List[Dict] = []
        dropped_items: List[str] = []
        for item in normalized_items:
            byte_size = int(item.get("byte_size") or 0)
            mime = str(item.get("mime") or "").strip().lower()
            if byte_size > 0 and byte_size < 2048 and mime.startswith("audio/"):
                dropped_items.append(f"{mime}:{byte_size}")
                continue
            filtered_items.append(item)

        if dropped_items:
            logger.debug(
                "页面音频捕获已过滤疑似空白片段: "
                + ", ".join(dropped_items)
            )

        return filtered_items

    def _get_audio_network_capture_config(self, config: Dict) -> Dict[str, Any]:
        defaults = dict((get_default_image_extraction_config().get("audio_network_capture") or {}))
        merged = {**defaults}

        raw = config.get("audio_network_capture")
        if isinstance(raw, dict):
            merged.update(raw)

        # 兼容旧平铺字段
        if "audio_network_capture_enabled" in config:
            merged["enabled"] = bool(config["audio_network_capture_enabled"])
        if "audio_network_capture_timeout_seconds" in config:
            merged["timeout_seconds"] = config["audio_network_capture_timeout_seconds"]
        if "audio_network_url_patterns" in config:
            merged["url_patterns"] = config["audio_network_url_patterns"]

        merged["enabled"] = bool(merged.get("enabled", False))
        merged["timeout_seconds"] = max(0.1, float(merged.get("timeout_seconds") or 2.5))
        merged["transport"] = str(merged.get("transport") or "page_websocket_probe").strip() or "page_websocket_probe"
        merged["extractor"] = str(merged.get("extractor") or "voicegenie_binary_stream").strip() or "voicegenie_binary_stream"
        merged["settle_seconds"] = max(0.05, float(merged.get("settle_seconds") or 0.35))
        merged["url_patterns"] = [
            str(item or "").strip()
            for item in (merged.get("url_patterns") or [])
            if str(item or "").strip()
        ]
        return merged

    def _get_audio_network_extractor(self, name: str):
        registry = {
            "voicegenie_ogg_pages": self._extract_voicegenie_ogg_pages_from_probe_logs,
            "voicegenie_binary_stream": self._extract_voicegenie_binary_stream_from_probe_logs,
        }
        return registry.get(str(name or "").strip())

    def _extract_voicegenie_binary_stream_from_probe_logs(
        self,
        logs: List[Dict[str, Any]],
        url_patterns: List[str],
    ) -> Dict[str, Any]:
        best_url = ""
        payload_parts: List[bytes] = []
        payload_size = 0
        seen_stop_marker = False
        stop_markers = (b"TTSSentenceEnd", b"TTSEnded", b"SessionCanceled")
        first_audio_mime = ""

        for item in logs:
            if not isinstance(item, dict):
                continue
            if str(item.get("dir") or "").strip().lower() != "recv":
                continue
            if str(item.get("kind") or "").strip().lower() != "binary":
                continue

            url = str(item.get("url") or "").strip()
            if not url:
                continue
            lowered_url = url.lower()
            if not any(pattern.lower() in lowered_url for pattern in url_patterns):
                continue

            base64_data = str(item.get("base64") or "").strip()
            if not base64_data:
                continue

            try:
                frame_bytes = base64.b64decode(base64_data)
            except Exception:
                continue

            if not frame_bytes:
                continue

            if any(marker in frame_bytes for marker in stop_markers):
                seen_stop_marker = True
                continue

            # 优先复用旧 OGG 页识别能力：如果帧里本来就带 OggS，就交给旧逻辑。
            if b"OggS" in frame_bytes:
                return self._extract_voicegenie_ogg_pages_from_probe_logs(logs, url_patterns)

            # 跳过看起来像控制帧/极小 ACK 的二进制消息。
            if len(frame_bytes) < 256:
                continue

            if not first_audio_mime:
                if frame_bytes[:4] == b"\x1a\x45\xdf\xa3":
                    first_audio_mime = "audio/webm"
                elif (
                    len(frame_bytes) >= 2
                    and frame_bytes[0] == 0xFF
                    and (frame_bytes[1] & 0xF0) == 0xF0
                ):
                    first_audio_mime = "audio/aac"
                elif frame_bytes[:3] == b"ID3" or frame_bytes[:2] == b"\xff\xfb":
                    first_audio_mime = "audio/mpeg"
                elif frame_bytes[:4] == b"RIFF":
                    first_audio_mime = "audio/wav"
                else:
                    first_audio_mime = "audio/webm"

            payload_parts.append(frame_bytes)
            payload_size += len(frame_bytes)
            best_url = url

        if not payload_parts or payload_size < 2048:
            return {}

        return {
            "url": best_url,
            "mime": first_audio_mime or "audio/webm",
            "body_bytes": b"".join(payload_parts),
            "seen_stop_marker": seen_stop_marker,
            "page_count": len(payload_parts),
            "gap_count": 0,
            "min_seq": 0,
            "max_seq": max(0, len(payload_parts) - 1),
        }

    def _extract_voicegenie_ogg_pages_from_probe_logs(
        self,
        logs: List[Dict[str, Any]],
        url_patterns: List[str],
    ) -> Dict[str, Any]:
        best_url = ""
        pages_by_key: Dict[tuple[int, int], bytes] = {}
        serial_sequences: Dict[int, set[int]] = {}
        serial_total_bytes: Dict[int, int] = {}
        largest_segment = b""
        seen_stop_marker = False
        stop_markers = (b"TTSSentenceEnd", b"TTSEnded", b"SessionCanceled")

        for item in logs:
            if not isinstance(item, dict):
                continue
            if str(item.get("dir") or "").strip().lower() != "recv":
                continue
            if str(item.get("kind") or "").strip().lower() != "binary":
                continue

            url = str(item.get("url") or "").strip()
            if not url:
                continue
            lowered_url = url.lower()
            if not any(pattern.lower() in lowered_url for pattern in url_patterns):
                continue

            base64_data = str(item.get("base64") or "").strip()
            if not base64_data:
                continue

            try:
                frame_bytes = base64.b64decode(base64_data)
            except Exception:
                continue

            if not frame_bytes:
                continue
            if any(marker in frame_bytes for marker in stop_markers):
                seen_stop_marker = True

            offset = 0
            found_page = False
            while True:
                ogg_index = frame_bytes.find(b"OggS", offset)
                if ogg_index < 0:
                    break
                page = frame_bytes[ogg_index:]
                parsed = self._parse_ogg_page_header(page)
                if not parsed:
                    offset = ogg_index + 4
                    continue
                serial_no, seq_no, page_size = parsed
                full_page = page[:page_size]
                key = (serial_no, seq_no)
                if key not in pages_by_key:
                    pages_by_key[key] = full_page
                    serial_sequences.setdefault(serial_no, set()).add(seq_no)
                    serial_total_bytes[serial_no] = int(serial_total_bytes.get(serial_no, 0)) + len(full_page)
                if len(full_page) > len(largest_segment):
                    largest_segment = full_page
                best_url = url
                found_page = True
                offset = ogg_index + page_size
            if not found_page and len(frame_bytes) > len(largest_segment):
                largest_segment = frame_bytes

        if not pages_by_key:
            return {}

        best_serial = None
        best_score = (-1, -1)
        for serial_no, seqs in serial_sequences.items():
            ordered = sorted(seqs)
            score = (int(serial_total_bytes.get(serial_no, 0)), len(ordered))
            if score > best_score:
                best_serial = serial_no
                best_score = score

        if best_serial is None:
            return {}

        ordered_sequences = sorted(serial_sequences.get(best_serial) or [])
        if not ordered_sequences:
            return {}

        gap_count = 0
        prev_seq = None
        for seq_no in ordered_sequences:
            if prev_seq is not None and seq_no != prev_seq + 1:
                gap_count += 1
            prev_seq = seq_no

        body_bytes = b"".join(
            pages_by_key[(best_serial, seq_no)]
            for seq_no in ordered_sequences
        )
        if len(body_bytes) <= len(largest_segment):
            body_bytes = largest_segment

        return {
            "url": best_url,
            "mime": "audio/ogg",
            "body_bytes": body_bytes,
            "seen_stop_marker": seen_stop_marker,
            "page_count": len(ordered_sequences),
            "gap_count": gap_count,
            "min_seq": ordered_sequences[0],
            "max_seq": ordered_sequences[-1],
        }

    @staticmethod
    def _is_network_audio_event_usable(
        event: Dict[str, Any],
        response_text_hint: str = "",
    ) -> bool:
        if not isinstance(event, dict):
            return False

        payload = event.get("body_bytes") or b""
        if not isinstance(payload, (bytes, bytearray)) or len(payload) < 512:
            return False

        text_len = len(str(response_text_hint or "").strip())
        gap_count = int(event.get("gap_count") or 0)
        page_count = int(event.get("page_count") or 0)
        seen_stop_marker = bool(event.get("seen_stop_marker"))
        byte_len = len(payload)

        if page_count <= 0:
            return False

        if text_len >= 12:
            min_expected_bytes = max(2048, min(65536, text_len * 220))
            if byte_len < min_expected_bytes and (gap_count > 0 or not seen_stop_marker):
                return False

        if text_len >= 24 and byte_len < 4096:
            return False

        return True

    @staticmethod
    def _parse_ogg_page_header(page_bytes: bytes) -> Optional[tuple[int, int, int]]:
        if not isinstance(page_bytes, (bytes, bytearray)) or len(page_bytes) < 27:
            return None
        if bytes(page_bytes[:4]) != b"OggS":
            return None
        page_segments = page_bytes[26]
        header_len = 27 + page_segments
        if len(page_bytes) < header_len:
            return None
        segment_table = page_bytes[27:header_len]
        payload_len = sum(segment_table)
        page_size = header_len + payload_len
        if len(page_bytes) < page_size:
            return None
        serial_no = int.from_bytes(page_bytes[14:18], "little", signed=False)
        seq_no = int.from_bytes(page_bytes[18:22], "little", signed=False)
        return serial_no, seq_no, page_size

    def _persist_network_audio_event(self, event: Dict[str, Any], save_dir: Path) -> Optional[Dict[str, Any]]:
        if not isinstance(event, dict):
            return None

        body_bytes = event.get("body_bytes")
        if not isinstance(body_bytes, (bytes, bytearray)):
            return None

        payload = bytes(body_bytes)
        if not payload:
            return None

        mime = str(event.get("mime") or "audio/ogg").strip().lower() or "audio/ogg"
        ext_map = {
            "audio/ogg": ".ogg",
            "audio/webm": ".webm",
            "audio/aac": ".aac",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mp4": ".m4a",
        }
        ext = ext_map.get(mime, ".bin")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{uuid.uuid4().hex[:8]}{ext}"
        filepath = save_dir / filename

        try:
            filepath.write_bytes(payload)
        except Exception as exc:
            logger.warning(f"网络音频保存失败: {exc}")
            return None

        try:
            from app.core import get_browser
            logger.debug(f"网络音频已落盘，准备追加尾静音: {filepath.name}")
            get_browser(auto_connect=False)._append_audio_tail_silence(filepath, duration_seconds=0.3)
        except Exception as exc:
            logger.debug(f"网络音频追加尾静音失败（已忽略）: {exc}")

        return {
            "media_type": "audio",
            "kind": "url",
            "url": f"/media/{filename}",
            "data_uri": None,
            "mime": mime,
            "byte_size": len(payload),
            "label": "captured_network_audio",
            "width": None,
            "height": None,
            "index": 0,
            "detected_at": datetime.utcnow().isoformat() + "Z",
            "source": "network_probe",
            "local_path": str(filepath),
        }

    def _extract_media_type(
        self,
        element: Any,
        media_type: str,
        selector: str,
        config: Dict,
        container_selector_fallback: Optional[str] = None,
    ) -> List[Dict]:
        container_selector = config.get("container_selector") or container_selector_fallback
        js_opts = {
            "selector": selector,
            "containerSelector": container_selector,
            "waitForLoad": config.get("wait_for_load", True),
            "loadTimeoutMs": int(config.get("load_timeout_seconds", 5) * 1000),
            "downloadBlobs": config.get("download_blobs", True),
            "maxBytes": int(config.get("max_size_mb", 10) * 1024 * 1024),
            "mode": config.get("mode", "all"),
            "mediaType": media_type,
            "allowContainerFallback": bool(config.get("allow_container_fallback", True)),
        }

        try:
            result = element.run_js(self.EXTRACT_MEDIA_JS, js_opts)
        except Exception as exc:
            logger.warning(f"{media_type} 提取失败（已忽略）: {exc}")
            return []

        if not result:
            return []

        for warning in result.get("warnings", []):
            logger.warning(f"{media_type} 提取告警: {warning}")

        return self._normalize_media_items(media_type, result.get("items", []))

    def _normalize_media_items(self, media_type: str, raw_items: List[Dict]) -> List[Dict]:
        now = datetime.utcnow().isoformat() + "Z"
        result: List[Dict] = []
        seen_keys = set()

        for index, item in enumerate(raw_items or []):
            src = str(item.get("src") or "").strip()
            data_uri = str(item.get("data_uri") or "").strip()

            if data_uri:
                kind = "data_uri"
                key = f"{media_type}:{data_uri[:200]}"
            elif src:
                kind = "url"
                key = f"{media_type}:{src}"
            else:
                continue

            if key in seen_keys:
                continue
            seen_keys.add(key)

            result.append({
                "media_type": media_type,
                "kind": kind,
                "url": src if kind == "url" else None,
                "data_uri": data_uri if kind == "data_uri" else None,
                "mime": item.get("mime"),
                "byte_size": item.get("byte_size"),
                "label": item.get("label"),
                "width": item.get("width"),
                "height": item.get("height"),
                "index": index,
                "detected_at": now,
                "source": item.get("_source") or self._detect_source(src or data_uri),
            })

        return result

    @staticmethod
    def _detect_source(value: str) -> str:
        if not value:
            return "unknown"
        if value.startswith("data:"):
            return "data_uri"
        if value.startswith("blob:"):
            return "blob"
        if value.startswith("http://") or value.startswith("https://"):
            return "currentSrc"
        return "relative"


media_extractor = MediaExtractor()


__all__ = ["MediaExtractor", "media_extractor"]
