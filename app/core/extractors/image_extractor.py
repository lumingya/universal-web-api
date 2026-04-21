"""
app/core/extractors/image_extractor.py - 图片内容提取器 (v1.0)

Phase A 实现：
- 支持 http(s)、data:、blob:、相对路径四种来源
- blob 自动转换为 data_uri（在浏览器上下文中完成）
- 支持大小限制和去重
- 所有异常均被捕获，保证不影响文本提取

安全规范：
- 不记录 data_uri 完整内容到日志
- 仅记录 mime、byte_size、前缀片段
"""

from typing import List, Optional, Any, Dict
from datetime import datetime

from app.core.config import get_logger

logger = get_logger("IMG_EXT")


def get_default_image_extraction_config() -> Dict:
    """获取默认的多模态提取配置"""
    return {
        "enabled": False,
        "modalities": {
            "image": False,
            "audio": False,
            "video": False,
        },
        "selector": "img",
        "audio_selector": "audio, audio source",
        "video_selector": "video, video source",
        "container_selector": None,
        "debounce_seconds": 2.0,
        "wait_for_load": True,
        "load_timeout_seconds": 5.0,
        "download_blobs": True,
        "max_size_mb": 10,
        "mode": "all"
    }


class ImageExtractor:
    """
    图片提取器
    
    从页面元素中提取图片信息，支持四种来源：
    1. http(s) URL：直接返回 kind="url"
    2. data: URI：直接返回 kind="data_uri"  
    3. blob: URL：转换为 data_uri 后返回
    4. 相对路径：补全为绝对 URL 后返回
    
    使用方式：
        extractor = ImageExtractor()
        images = extractor.extract(element, config)
    """
    
    # ============ 核心 JS 代码 ============
    # 功能：收集图片 + 可选等待加载 + blob 转 data_uri
    # 执行方式：async IIFE，使用 .call(this, opts)
    # 返回格式：{ images: [...], warnings: [...] }
    
    EXTRACT_IMAGES_JS = r"""
    return (async function(opts) {
        const {
            selector = "img",
            containerSelector = null,
            waitForLoad = true,
            loadTimeoutMs = 5000,
            downloadBlobs = true,
            maxBytes = 10485760,
            mode = "all"
        } = opts || {};

        // ===== 1. 确定根元素 =====
        // 🔧 修复：优先使用传入的元素（this），避免 containerSelector 重新定位到错误元素
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
            // 传入了有效的 DOM 元素，直接使用
            pushRoot(this);
        }

        if (containerSelector) {
            // 回退：使用 containerSelector 查找
            try {
                const scopedRoots = Array.from(document.querySelectorAll(containerSelector));
                for (const scopedRoot of scopedRoots) {
                    pushRoot(scopedRoot);
                }
            } catch {}
        } else {
            // 最终回退：使用 document
            pushRoot(document);
        }

        if (candidateRoots.length === 0) {
            return { images: [], warnings: ["container_not_found"] };
        }

        // ===== 2. 查找所有图片元素 =====
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
            return { images: [], warnings: [] };
        }

        // ===== 辅助函数 =====
        
        // 获取图片源（优先 currentSrc）
        function pickSrc(img) {
            const cs = img.currentSrc;
            if (cs && cs.trim()) return cs.trim();
            const s = img.src;
            if (s && s.trim()) return s.trim();
            return "";
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

        async function imageElementToDataUri(img) {
            if (!img) {
                throw new Error("img_missing");
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
            try {
                dataUri = canvas.toDataURL("image/png");
            } catch (e) {
                throw new Error("canvas_export_failed:" + String(e).slice(0, 80));
            }

            return {
                dataUri: dataUri,
                mime: "image/png",
                byteSize: estimateByteSizeFromDataUri(dataUri),
                source: "blob_canvas"
            };
        }

        let items = nodes.map((img, i) => {
            const src = pickSrc(img);
            return {
                _node: img,
                index: i,
                src: src,
                alt: img.getAttribute("alt") || "",
                width: img.naturalWidth || img.width || null,
                height: img.naturalHeight || img.height || null,
                complete: !!img.complete,
                naturalWidth: img.naturalWidth || 0
            };
        }).filter(x => x.src);  // 过滤无 src 的

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
                return { ...x, src: abs, _source: "relative" };
            } catch {
                return { ...x, _bad: true };
            }
        }).filter(x => !x._bad);

        const out = [];
        const warnings = [];

        // ===== 7. 处理 blob URL =====
        if (downloadBlobs) {
            const blobItems = items.filter(x => x.src.startsWith("blob:"));
            const nonBlobItems = items.filter(x => !x.src.startsWith("blob:"));

            // 先添加非 blob 项
            for (const x of nonBlobItems) {
                out.push(stripRuntimeFields(x));
            }

            // 逐个处理 blob（优先 fetch，失败时回退到 canvas）
            for (const x of blobItems) {
                let converted = null;
                let fetchError = null;

                try {
                    const res = await fetch(x.src);
                    const blob = await res.blob();

                    // 校验类型
                    if (!blob.type || !blob.type.startsWith("image/")) {
                        warnings.push("blob_not_image:" + (blob.type || "unknown"));
                        continue;
                    }
                    
                    // 校验大小
                    if (maxBytes && blob.size > maxBytes) {
                        warnings.push("blob_too_large:" + blob.size);
                        continue;
                    }

                    converted = await blobToDataUri(blob);
                } catch (e) {
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
            // 不下载 blob，直接返回所有项（blob URL 可能会失效）
            for (const x of items) {
                out.push(stripRuntimeFields(x));
            }
        }

        return { images: out, warnings: warnings };
    }).call(this, arguments[0]);
    """

    def __init__(self):
        self._log_prefix = "[ImageExtractor]"
    
    def extract(
        self,
        element: Any,
        config: Optional[Dict] = None,
        container_selector_fallback: Optional[str] = None
    ) -> List[Dict]:
        """
        从页面元素提取图片
        
        Args:
            element: 页面元素对象（需支持 run_js 方法）
            config: 图片提取配置（ImageExtractionConfig 格式）
            container_selector_fallback: 容器选择器回退值（当 config 中未指定时使用）
        
        Returns:
            图片数据列表，每项符合 ImageData 格式
            任何异常都返回空列表，不抛出异常
        
        Example:
            >>> extractor = ImageExtractor()
            >>> images = extractor.extract(element, {"enabled": True})
            >>> for img in images:
            ...     print(img["kind"], img.get("url") or "data_uri")
        """
        # 合并默认配置
        final_config = get_default_image_extraction_config()
        if config:
            final_config.update(config)
        
        # 检查是否启用
        if not final_config.get("enabled", False):
            logger.debug(f" 图片提取未启用，跳过")
            return []
        
        if not element:
            logger.debug(f" 元素为空，跳过")
            return []
        
        # 构建 JS 参数
        container_selector = final_config.get("container_selector") or container_selector_fallback
        js_opts = {
            "selector": final_config.get("selector", "img"),
            "containerSelector": container_selector,
            "waitForLoad": final_config.get("wait_for_load", True),
            "loadTimeoutMs": int(final_config.get("load_timeout_seconds", 5) * 1000),
            "downloadBlobs": final_config.get("download_blobs", True),
            "maxBytes": final_config.get("max_size_mb", 10) * 1024 * 1024,
            "mode": final_config.get("mode", "all")
        }
        
        logger.debug(
            f"开始提取: selector={js_opts['selector']}, "
            f"container={container_selector or 'element'}, mode={js_opts['mode']}"
        )
        
        try:
            # 执行 JS
            result = element.run_js(self.EXTRACT_IMAGES_JS, js_opts)
            
            if not result:
                logger.debug(f" JS 返回空结果")
                return []
            
            raw_images = result.get("images", [])
            warnings = result.get("warnings", [])
            
            # 记录警告（不中断流程）
            for w in warnings:
                logger.warning(f" {w}")
            
            # 规范化 + 去重
            images = self._normalize_and_dedupe(raw_images)
            
            # 日志摘要
            logger.debug(f" 提取完成: {len(images)} 张图片")
            for img in images[:5]:  # 最多记录前 5 张
                self._log_image_summary(img)
            if len(images) > 5:
                logger.debug(f" ... 还有 {len(images) - 5} 张")
            
            return images
            
        except Exception as e:
            # 🔴 关键：图片提取失败不能影响主流程
            logger.error(f" 提取失败（已降级为空列表）: {e}")
            return []
    
    def _normalize_and_dedupe(self, raw_images: List[Dict]) -> List[Dict]:
        """
        规范化并去重
        
        处理逻辑：
        1. 确定 kind (url/data_uri)
        2. 提取 source 类型
        3. 按 key 去重（url 用完整 URL，data_uri 用前 200 字符）
        """
        seen_keys = set()
        result = []
        now = datetime.utcnow().isoformat() + "Z"
        
        for i, img in enumerate(raw_images):
            src = img.get("src", "")
            data_uri = img.get("data_uri")
            
            # 确定 kind 和去重键
            if data_uri:
                kind = "data_uri"
                key = data_uri[:200]  # 前 200 字符作为去重键
            elif src.startswith("data:"):
                kind = "data_uri"
                data_uri = src
                key = src[:200]
            else:
                kind = "url"
                key = src
            
            # 去重检查
            if key in seen_keys:
                logger.debug(f" 跳过重复: {key[:50]}...")
                continue
            seen_keys.add(key)
            
            # 检测来源类型
            source = img.get("_source")
            if not source:
                source = self._detect_source(src)
            
            # 构建标准化结构（符合 ImageData schema）
            image_data = {
                "kind": kind,
                "url": src if kind == "url" else None,
                "data_uri": data_uri if kind == "data_uri" else None,
                "mime": img.get("mime"),
                "byte_size": img.get("byte_size"),
                "alt": img.get("alt"),
                "width": img.get("width"),
                "height": img.get("height"),
                "index": i,
                "detected_at": now,
                "source": source
            }
            
            result.append(image_data)
        
        return result
    
    def _detect_source(self, src: str) -> str:
        """检测图片来源类型"""
        if not src:
            return "unknown"
        if src.startswith("data:"):
            return "data_uri"
        if src.startswith("blob:"):
            return "blob"
        if src.startswith("http://") or src.startswith("https://"):
            return "currentSrc"
        return "relative"
    
    def _log_image_summary(self, img: Dict):
        """
        记录图片摘要信息（安全日志）
        
        ⚠️ 绝不记录 data_uri 完整内容
        """
        kind = img.get("kind")
        source = img.get("source", "unknown")
        index = img.get("index", 0)
        
        if kind == "url":
            url = img.get("url", "")
            # 截断长 URL
            url_display = (url[:80] + "...") if len(url) > 80 else url
            logger.debug(f"  [{index}] {kind}/{source}: {url_display}")
        else:
            # data_uri 只记录元信息
            mime = img.get("mime", "unknown")
            size = img.get("byte_size")
            size_str = f"{size} bytes" if size else "unknown size"
            logger.debug(f"  [{index}] {kind}/{source}: mime={mime}, {size_str}")


# ============ 单例实例 ============
image_extractor = ImageExtractor()


__all__ = ['ImageExtractor', 'image_extractor', 'get_default_image_extraction_config']
