
const vm = require('vm');
const code = `(function() {
  
    return (async function(opts) {
        const {
            selector = "img",
            containerSelector = null,
            waitForLoad = true,
            loadTimeoutMs = 5000,
            downloadBlobs = true,
            maxBytes = 10485760,
            srcAllowPatterns = [],
            mode = "all",
            allowContainerFallback = true,
            canvasExportMime = "image/jpeg",
            canvasExportQuality = 0.88,
            requestBaselineToken = "",
            requestBaselineProperty = "",
            requestBaselineExcludeExistingNodes = false
        } = opts || {};
        const windowedPlaceholderAttribute = "data-uwa-image-window-placeholder";
        const windowedOriginalSourceAttribute = "data-uwa-image-window-original-src";
        const knownPlaceholders = new Set([
            "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
            "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==",
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        ]);
        const isKnownPlaceholderSrc = (src) => {
            const trimmed = String(src || "").trim();
            return knownPlaceholders.has(trimmed);
        };

        // ===== 1. 确定根元素 =====
        // 优先只在传入元素（当前回复节点）内查找；只有查不到时才回退到容器/整页。

        // ===== 1. 确定根元素 =====
        // 优先只在传入元素（当前回复节点）内查找；只有查不到时才回退到容器/整页。
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
            return { images: [], warnings: ["container_not_found"] };
        }

        // ===== 2. 查找所有图片元素 =====
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

        let scopeUsed = "primary";
        let nodes = collectNodes(primaryRoots);
        if (nodes.length === 0 && allowContainerFallback) {
            scopeUsed = "fallback";
            nodes = collectNodes(fallbackRoots);
        }

        if (nodes.length === 0) {
            return { images: [], warnings: [], scope: scopeUsed, nodeCount: 0 };
        }

        // ===== 辅助函数 =====
        
        // 获取图片源（优先 currentSrc）
        function pickSrc(img) {
            if (!img) return "";
            const cs = typeof img.currentSrc === "string" ? img.currentSrc : "";
            if (cs && cs.trim()) return cs.trim();
            const s = typeof img.src === "string" ? img.src : "";
            if (s && s.trim()) return s.trim();
            return "";
        }

        // A request baseline is attached to every image node that existed immediately
        // before submit. Upload previews often fill or replace their src asynchronously
        // after the click, so a changed src does not make a pre-submit node generated output.
        if (requestBaselineToken && requestBaselineProperty) {
            nodes = nodes.filter((img) => {
                const baseline = img[requestBaselineProperty];
                if (!baseline || String(baseline.token || "") !== String(requestBaselineToken)) {
                    return true;
                }
                if (requestBaselineExcludeExistingNodes) return false;
                return pickSrc(img) !== String(baseline.reference || "");
            });
            if (nodes.length === 0) {
                return { images: [], warnings: [], scope: scopeUsed, nodeCount: 0 };
            }
        }

        // 判断图片是否加载完成
        function isLoaded(img) {
            return !!(img.complete && img.naturalWidth > 0);
        }

        // ===== 3. 可选：等待图片加载 =====
        if (waitForLoad) {
            const deadline = Date.now() + loadTimeoutMs;
            while (Date.now() < deadline) {
                const allOk = nodes.every(img => {
                    const s = pickSrc(img);
                    if (!s) return true;                    // 无 src 不阻塞
                    if (s.startsWith("data:")) return true; // data uri 无需加载
                    return isLoaded(img);                   // 检查 complete
                });
                if (allOk) break;
                await new Promise(r => setTimeout(r, 100));
            }
        }

        // ===== 4. 收集基础信息 =====
        function stripRuntimeFields(item) {
            const { _node, ...rest } = item || {};
            return rest;
        }

        function estimateByteSizeFromDataUri(dataUri) {
            try {
                const base64 = String(dataUri || "").split(",", 2)[1] || "";
                const paddingMatch = base64.match(/=*$/);
                const padding = paddingMatch ? paddingMatch[0].length : 0;
                return Math.max(0, Math.floor(base64.length * 3 / 4) - padding);
            } catch {
                return null;
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
                dataUri: dataUri,
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

        async function imageElementToDataUri(img) {
            if (!img) {
                throw new Error("img_missing");
            }
            const isPlaceholderNode = isKnownPlaceholderSrc(pickSrc(img)) ||
                img.getAttribute(windowedPlaceholderAttribute) === "1";
            if (isPlaceholderNode) {
                throw new Error("img_is_placeholder");
            }

            if (typeof img.decode === "function") {
                try {
                    await img.decode();
                } catch {}
            }

            const width = img.naturalWidth || img.width || 0;
            const height = img.naturalHeight || img.height || 0;
            if (!img.complete || width <= 0 || height <= 0) {
                throw new Error("img_not_ready");
            }

            const canvas = document.createElement("canvas");
            canvas.width = width;
            canvas.height = height;

            const ctx = canvas.getContext("2d");
            if (!ctx) {
                throw new Error("canvas_ctx_unavailable");
            }

            ctx.drawImage(img, 0, 0, width, height);

            let dataUri;
            const exportMime = String(canvasExportMime || "image/jpeg");
            const exportQuality = Number(canvasExportQuality || 0.88);
            try {
                dataUri = canvas.toDataURL(exportMime, exportQuality);
            } catch (e) {
                throw new Error("canvas_export_failed:" + String(e).slice(0, 80));
            }

            const byteSize = estimateByteSizeFromDataUri(dataUri);
            if (maxBytes && byteSize && byteSize > maxBytes) {
                throw new Error("blob_too_large:" + byteSize);
            }

            return {
                dataUri: dataUri,
                mime: exportMime,
                byteSize: byteSize,
                source: "blob_canvas"
            };
        }

        const warnings = [];

        const isMicroIcon = (img) => {
            if (!img) return false;
            try {
                const nw = Number(img.naturalWidth || 0);
                const nh = Number(img.naturalHeight || 0);
                if (nw > 0 && nh > 0 && (nw < 48 || nh < 48)) {
                    const rect = img.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0 && (rect.width < 48 || rect.height < 48)) {
                        return true;
                    }
                }
            } catch {}
            return false;
        };

        const isHiddenNode = (img) => {
            if (!img) return false;
            try {
                const style = window.getComputedStyle(img);
                if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || "1") <= 0.01) {
                    return true;
                }
            } catch {}
            return false;
        };

        let items = nodes.map((img, i) => {
            const rawSrc = pickSrc(img);
            const isPlaceholderSrc = isKnownPlaceholderSrc(rawSrc);
            const windowedPlaceholder = img.getAttribute(windowedPlaceholderAttribute) === "1" || isPlaceholderSrc;
            const rawOriginalSource = windowedPlaceholder
                ? String(img.getAttribute(windowedOriginalSourceAttribute) || "").trim()
                : "";
            const windowedOriginalSource = isKnownPlaceholderSrc(rawOriginalSource) ? "" : rawOriginalSource;
            const src = windowedOriginalSource || rawSrc;
            const microIcon = isMicroIcon(img);
            const hidden = isHiddenNode(img);
            return {
                _node: img,
                index: i,
                src: src,
                alt: img.getAttribute("alt") || "",
                width: img.naturalWidth || img.width || null,
                height: img.naturalHeight || img.height || null,
                complete: !!img.complete,
                naturalWidth: img.naturalWidth || 0,
                _windowed_placeholder: windowedPlaceholder && !windowedOriginalSource,
                _is_micro_icon: microIcon,
                _is_hidden: hidden,
                _source: windowedOriginalSource ? "image_window_original_src" : ""
            };
        }).filter(x => x.src);  // 过滤无 src 的

        // 过滤微小图标与隐藏占位节点（若有有效大图候选时）
        const validGenerations = items.filter(item => !item._windowed_placeholder && !item._is_micro_icon && !item._is_hidden);
        if (validGenerations.length > 0) {
            items = validGenerations;
        }

        const windowedPlaceholderCount = items.filter(item => item._windowed_placeholder).length;
        if (windowedPlaceholderCount > 0) {
            warnings.push("windowed_placeholders_skipped:" + windowedPlaceholderCount);
            items = items.filter(item => !item._windowed_placeholder);
        }

        const beforeAllowFilterCount = items.length;
        const beforeAllowFilterSamples = items.slice(0, 5).map((item) => {
            const s = String(item.src || "");
            return s.length > 80 ? (s.slice(0, 80) + "...") : s;
        });

        // ===== 4.5 可选：按 src 白名单过滤 =====
        const allowRegexes = Array.isArray(srcAllowPatterns)
            ? srcAllowPatterns
                .map((pattern) => {
                    try {
                        const text = String(pattern || "").trim();
                        if (!text) return null;
                        return new RegExp(text, "i");
                    } catch {
                        return null;
                    }
                })
                .filter(Boolean)
            : [];

        if (allowRegexes.length > 0) {
            items = items.filter((item) => allowRegexes.some((regex) => regex.test(item.src)));
        }

        if (allowRegexes.length > 0 && beforeAllowFilterCount > 0 && items.length === 0) {
            warnings.push(
                "all_filtered_by_src_allow_patterns:" + JSON.stringify({
                    count: beforeAllowFilterCount,
                    sample_srcs: beforeAllowFilterSamples,
                    patterns: allowRegexes.map((regex) => String(regex))
                })
            );
        }

        // ===== 5. 按模式筛选 =====
        if (mode === "first") items = items.slice(0, 1);
        if (mode === "last") items = items.slice(-1);

        // ===== 6. 相对路径补全 =====
        items = items.map(x => {
            const s = x.src;
            if (s.startsWith("http://") || s.startsWith("https://") ||
                s.startsWith("data:") || s.startsWith("blob:")) {
                return x;
            }
            // 尝试补全相对路径
            try {
                const abs = new URL(s, document.baseURI).href;
                return { ...x, src: abs, _source: x._source || "relative" };
            } catch {
                return { ...x, _bad: true };
            }
        }).filter(x => !x._bad);

        const out = [];

        // ===== 7. 处理 blob URL（保持原有 DOM 顺序） =====
        if (downloadBlobs) {
            for (const x of items) {
                if (!x.src.startsWith("blob:")) {
                    out.push(stripRuntimeFields(x));
                    continue;
                }

                let converted = null;
                let fetchError = null;

                try {
                    const blob = await fetchBlobWithLimit(x.src, maxBytes);

                    // 校验类型
                    if (!blob.type || !blob.type.startsWith("image/")) {
                        warnings.push("blob_not_image:" + (blob.type || "unknown"));
                    } else {
                        converted = await blobToDataUri(blob);
                    }
                } catch (e) {
                    if (String(e || "").includes("blob_too_large:")) {
                        warnings.push(String(e).replace(/^Error:\s*/, ""));
                        continue;
                    }
                    fetchError = e;
                }

                if (!converted) {
                    try {
                        converted = await imageElementToDataUri(x._node);
                    } catch (canvasError) {
                        const fetchMsg = fetchError ? String(fetchError).slice(0, 60) : "n/a";
                        const canvasMsg = String(canvasError).slice(0, 60);
                        warnings.push("blob_convert_failed:fetch=" + fetchMsg + ";canvas=" + canvasMsg);
                        continue;
                    }
                }

                out.push({
                    ...stripRuntimeFields(x),
                    data_uri: converted.dataUri,
                    mime: converted.mime,
                    byte_size: converted.byteSize,
                    _source: converted.source
                });
            }
        } else {
            for (const x of items) {
                out.push(stripRuntimeFields(x));
            }
        }

        return { images: out, warnings: warnings, scope: scopeUsed, nodeCount: nodes.length };
    }).call(this, arguments[0]);
    
})`;
try {
  new vm.Script(code);
  console.log("EXTRACT_IMAGES_JS syntax is 100% valid!");
} catch (e) {
  console.error("Syntax Error in JS:", e);
  process.exit(1);
}
