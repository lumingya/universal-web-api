"""
app/core/extractors/media_extractor.py - 多模态内容提取器

职责：
- 复用图片提取器处理图片
- 补充音频、视频节点提取
- 对 blob 媒体做可选 data-uri 转换，避免返回临时 blob URL
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.config import get_logger
from app.core.extractors.image_extractor import (
    get_default_image_extraction_config,
    image_extractor,
)

logger = get_logger("MEDIA_EXT")


class MediaExtractor:
    """多模态提取器。"""

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
