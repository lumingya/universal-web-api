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

logger = get_logger("MEDIA_EXT")


PAGE_TTS_WS_PROBE_INSTALL_JS = r"""
return (function(opts) {
    const stateKey = "__uwaTtsWsProbe";
    const maxLogs = Math.max(16, Number(opts && opts.maxLogs || 128) || 128);
    let state = window[stateKey];

    const ensureState = () => {
        if (!state) {
            state = {
                installed: true,
                logs: [],
                push(item) {
                    this.logs.push({ t: Date.now(), ...(item || {}) });
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

    const toBase64 = async (data) => {
        if (typeof data === "string") {
            return { kind: "text", text: data, size: data.length };
        }

        let bytes = null;
        if (data instanceof Blob) {
            bytes = new Uint8Array(await data.arrayBuffer());
        } else if (data instanceof ArrayBuffer) {
            bytes = new Uint8Array(data);
        } else if (ArrayBuffer.isView(data)) {
            bytes = new Uint8Array(data.buffer.slice(0));
        } else {
            return { kind: typeof data, text: String(data), size: 0 };
        }

        let binary = "";
        const chunkSize = 0x8000;
        for (let i = 0; i < bytes.length; i += chunkSize) {
            binary += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + chunkSize)));
        }

        return {
            kind: "binary",
            size: bytes.length,
            base64: btoa(binary),
        };
    };

    const patchSocket = (ws) => {
        if (!ws || ws.__uwaTtsWsProbeWrapped) return ws;
        ws.__uwaTtsWsProbeWrapped = true;
        const url = String(ws.url || "");
        state.push({ dir: "ws-created", url });
        ws.addEventListener("open", () => state.push({ dir: "ws-open", url }));
        ws.addEventListener("close", () => state.push({ dir: "ws-close", url }));
        ws.addEventListener("error", () => state.push({ dir: "ws-error", url }));

        const rawSend = ws.send;
        ws.send = function(data) {
            Promise.resolve(toBase64(data)).then((info) => state.push({ dir: "send", url, ...(info || {}) }));
            return rawSend.apply(this, arguments);
        };

        ws.addEventListener("message", (event) => {
            Promise.resolve(toBase64(event.data)).then((info) => state.push({ dir: "recv", url, ...(info || {}) }));
        });

        return ws;
    };

    if (!state._patched) {
        const RawWS = window.WebSocket;
        function WrappedWebSocket(...args) {
            const ws = new RawWS(...args);
            return patchSocket(ws);
        }
        WrappedWebSocket.prototype = RawWS.prototype;
        Object.assign(WrappedWebSocket, RawWS);
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
            mediaType = "audio"
        } = opts || {};

        const candidateRoots = [];
        const pushRoot = (value) => {
            if (!value) return;
            const nodeType = Number(value.nodeType || 0);
            if (nodeType !== 1 && nodeType !== 9) return;
            if (!candidateRoots.includes(value)) {
                candidateRoots.push(value);
            }
        };

        if (this && (this.nodeType === 1 || this.nodeType === 9)) {
            pushRoot(this);
        }

        if (containerSelector) {
            try {
                const scopedRoots = Array.from(document.querySelectorAll(containerSelector));
                for (const scopedRoot of scopedRoots) {
                    pushRoot(scopedRoot);
                }
            } catch {}
        } else {
            pushRoot(document);
        }

        if (candidateRoots.length === 0) {
            return { items: [], warnings: ["container_not_found"] };
        }

        const nodes = [];
        const seenNodes = new Set();
        const pushNode = (value) => {
            if (!(value instanceof Element)) return;
            if (seenNodes.has(value)) return;
            seenNodes.add(value);
            nodes.push(value);
        };

        for (const root of candidateRoots) {
            try {
                if (root instanceof Element && typeof root.matches === "function" && root.matches(selector)) {
                    pushNode(root);
                }
            } catch {}

            try {
                const scopedNodes = root.querySelectorAll ? Array.from(root.querySelectorAll(selector)) : [];
                for (const node of scopedNodes) {
                    pushNode(node);
                }
            } catch {}
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
                    const response = await fetch(src);
                    const blob = await response.blob();
                    if (maxBytes && blob.size > maxBytes) {
                        warnings.push("blob_too_large:" + blob.size);
                        continue;
                    }
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
            meta.lastDataAt = 0;
            meta.lastActiveAt = 0;
            meta.lastRms = 0;
            recorder.ondataavailable = (event) => {
                try {
                    if (event && event.data && event.data.size > 0) {
                        meta.chunks.push(event.data);
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

    TRIGGER_AUDIO_PLAYBACK_JS = r"""
    return (function(opts) {
        const fallbackLabels = ["朗读", "语音朗读", "收听", "read aloud", "listen", "tts", "voice"];
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
            return {
                clicked: false,
                candidate_count: 0,
                selector_used: triggerSelector || selectors.join(", "),
                labels_used: normalizedLabels,
                debug_matches: debugMatches.slice(0, 8),
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
            if isinstance(config.get("modalities"), dict):
                final_config["modalities"] = {
                    **(get_default_image_extraction_config().get("modalities") or {}),
                    **(config.get("modalities") or {}),
                }

        final_config["enabled"] = bool(final_config.get("enabled")) or any(
            bool((final_config.get("modalities") or {}).get(key))
            for key in ("image", "audio", "video")
        )

        modalities = dict(final_config.get("modalities") or {})
        enabled = bool(final_config.get("enabled"))
        if not enabled and not any(bool(modalities.get(key)) for key in ("image", "audio", "video")):
            return []
        if not element:
            return []

        media_items: List[Dict] = []

        if bool(modalities.get("image")):
            images = image_extractor.extract(
                element,
                config=final_config,
                container_selector_fallback=container_selector_fallback,
            )
            for item in images:
                media_items.append({
                    **item,
                    "media_type": "image",
                    "label": item.get("alt"),
                })

        if bool(modalities.get("audio")):
            media_items.extend(
                self._extract_media_type(
                    element=element,
                    media_type="audio",
                    selector=final_config.get("audio_selector", "audio, audio source"),
                    config=final_config,
                    container_selector_fallback=container_selector_fallback,
                )
            )

        if bool(modalities.get("video")):
            media_items.extend(
                self._extract_media_type(
                    element=element,
                    media_type="video",
                    selector=final_config.get("video_selector", "video, video source"),
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

    def trigger_audio_playback(
        self,
        element: Any,
        config: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        if not element:
            return {}

        final_config = get_default_image_extraction_config()
        if config:
            final_config.update(config)

        js_opts = {
            "audioTriggerSelector": final_config.get("audio_trigger_selector") or "",
            "audioTriggerLabels": final_config.get("audio_trigger_labels") or [],
        }

        try:
            result = element.run_js(self.TRIGGER_AUDIO_PLAYBACK_JS, js_opts) or {}
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            logger.debug(f"触发页面音频播放失败（已忽略）: {exc}")
            return {}

    def install_audio_network_probe(self, tab: Any, config: Optional[Dict] = None) -> bool:
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
                    "clear": True,
                    "maxLogs": int(network_config.get("max_logs") or 1024),
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
            tab.run_js(PAGE_TTS_WS_PROBE_INSTALL_JS, {"clear": True, "maxLogs": 256})
            return True
        except Exception as exc:
            logger.debug(f"清空页面 TTS 网络探针失败（已忽略）: {exc}")
            return False

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

        extractor_name = str(network_config.get("extractor") or "").strip() or "voicegenie_ogg_pages"
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
            self.install_audio_network_probe(tab, final_config)

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

        return self._normalize_media_items("audio", result.get("items", []))

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
        merged["extractor"] = str(merged.get("extractor") or "voicegenie_ogg_pages").strip() or "voicegenie_ogg_pages"
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
        }
        return registry.get(str(name or "").strip())

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
