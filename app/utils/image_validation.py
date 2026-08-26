"""
app/utils/image_validation.py - 通用图片比对、特征签名与参考图过滤模块

职责：
1. 提取图片指纹特征（SHA256、像素 SHA256、尺寸、dHash 感知哈希）。
2. 精确比对与感知相似度比对，判断两张图片是否一致。
3. 从本地路径、Base64 data URI、HTTP(s) 或浏览器会话中读取图片字节。
4. 在多模态生成完成后，过滤掉与用户上传参考图一致的图片，防止误将原图当下发。
5. 提供通用的生成图片校验接口，供 Arena 等适配器复用。
"""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageOps

from app.core.config import logger


def get_current_page_url(tab: Any) -> str:
    """获取当前标签页 URL"""
    if tab is None:
        return ""
    try:
        return str(tab.run_js("return location.href") or "").strip()
    except Exception:
        return str(getattr(tab, "url", "") or "").strip()


def read_image_bytes(
    tab: Any,
    url: str,
    timeout: Any = (2.0, 5.0),
) -> bytes:
    """通过 HTTP 请求或浏览器上下文安全读取图片二进制数据。"""
    source = str(url or "").strip()
    if not source:
        return b""

    if source.startswith("data:"):
        try:
            return base64.b64decode(source.split(",", 1)[1])
        except Exception:
            return b""

    # 规范化超时时间为 (connect_timeout, read_timeout)
    if isinstance(timeout, (int, float)):
        eff_timeout: Any = (
            (min(2.0, float(timeout)), float(timeout))
            if float(timeout) > 5.0
            else float(timeout)
        )
    elif isinstance(timeout, (tuple, list)) and len(timeout) >= 2:
        try:
            c_to = float(timeout[0]) if timeout[0] is not None else None
            r_to = float(timeout[1]) if timeout[1] is not None else None
            eff_timeout = (c_to, r_to)
        except (TypeError, ValueError):
            eff_timeout = (2.0, 5.0)
    else:
        eff_timeout = (2.0, 5.0)

    if source.startswith(("http://", "https://")):
        try:
            referer = get_current_page_url(tab)
            headers = {"User-Agent": "Mozilla/5.0"}
            if referer:
                headers["Referer"] = referer
            response = requests.get(
                source,
                headers=headers,
                timeout=eff_timeout,
            )
            response.raise_for_status()
            if response.content:
                return response.content
        except Exception:
            pass

    if tab is None:
        return b""

    try:
        result = tab.run_js(
            r"""
            return (async function(url) {
                let timer = null;
                try {
                    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
                    timer = controller ? setTimeout(() => controller.abort(), 6000) : null;
                    const response = await fetch(url, {
                        signal: controller ? controller.signal : undefined
                    });
                    if (!response.ok) return '';
                    const buffer = await response.arrayBuffer();
                    const bytes = new Uint8Array(buffer);
                    let binary = '';
                    const step = 0x8000;
                    for (let index = 0; index < bytes.length; index += step) {
                        binary += String.fromCharCode(...bytes.subarray(index, index + step));
                    }
                    return btoa(binary);
                } catch (e) {
                    return '';
                } finally {
                    if (timer) clearTimeout(timer);
                }
            })(arguments[0]);
            """,
            source,
        )
        return base64.b64decode(str(result or ""))
    except Exception:
        return b""


def read_local_image_bytes(path_value: Any) -> bytes:
    """读取本地文件图片字节"""
    try:
        path = Path(str(path_value or "")).expanduser()
        return path.read_bytes() if path.is_file() else b""
    except Exception:
        return b""


def read_uploaded_image_bytes(
    value: Any,
    *,
    local_reader: Optional[Callable[[Any], bytes]] = None,
) -> bytes:
    """读取上传的参考图片字节（支持 bytes、data URI、本地路径）"""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    source = str(value or "").strip()
    if source.startswith("data:"):
        try:
            return base64.b64decode(source.split(",", 1)[1])
        except Exception:
            return b""
    reader = local_reader or read_local_image_bytes
    return reader(source)


def extract_media_item_bytes(
    tab: Any,
    item: Dict[str, Any],
    *,
    local_reader: Optional[Callable[[Any], bytes]] = None,
) -> bytes:
    """从媒体描述字典中提取图片二进制数据"""
    if not isinstance(item, dict):
        return b""

    reader = local_reader or read_local_image_bytes

    # 优先读本地落盘路径
    for key in ("local_path", "path", "file_path"):
        payload = reader(item.get(key))
        if payload:
            return payload

    # 其次从 data_uri / url / src 中读取
    for key in ("data_uri", "url", "src"):
        value = str(item.get(key) or "").strip()
        if value:
            payload = read_image_bytes(tab, value)
            if payload:
                return payload

    return b""


def image_signatures(payload: bytes) -> Dict[str, Any]:
    """计算图片指纹（字节哈希、像素哈希、尺寸与差异哈希）"""
    data = bytes(payload or b"")
    signatures: Dict[str, Any] = {
        "sha256": hashlib.sha256(data).hexdigest() if data else None,
        "pixel_sha256": None,
        "size": None,
        "dhash": None,
    }
    if not data:
        return signatures

    try:
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            signatures["size"] = image.size
            signatures["pixel_sha256"] = hashlib.sha256(image.tobytes()).hexdigest()
            grayscale = image.convert("L").resize((17, 16), Image.Resampling.LANCZOS)
            flattened_data = getattr(grayscale, "get_flattened_data", grayscale.getdata)
            pixels = list(flattened_data())
        bits = 0
        for row in range(16):
            offset = row * 17
            for column in range(16):
                bits = (bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
        signatures["dhash"] = bits
    except Exception:
        pass
    return signatures


def same_image(
    candidate: Dict[str, Any],
    reference: Optional[Dict[str, Any]],
    *,
    max_dhash_distance: Optional[int] = 4,
) -> bool:
    """比对两张图片是否一致。优先严格匹配，可选感知哈希。"""
    if not reference:
        return False
    # 1. 字节完全一致
    if candidate.get("sha256") and candidate.get("sha256") == reference.get("sha256"):
        return True
    # 2. 解码像素与尺寸完全一致（防御服务端无损重新封装/元数据变动）
    if (
        candidate.get("size") == reference.get("size")
        and candidate.get("pixel_sha256")
        and candidate.get("pixel_sha256") == reference.get("pixel_sha256")
    ):
        return True
    # 3. 感知哈希汉明距离比对
    candidate_dhash = candidate.get("dhash")
    reference_dhash = reference.get("dhash")
    return bool(
        max_dhash_distance is not None
        and isinstance(candidate_dhash, int)
        and isinstance(reference_dhash, int)
        and (candidate_dhash ^ reference_dhash).bit_count() <= max_dhash_distance
    )


def compute_reference_signatures(
    uploaded_images: Iterable[Any],
    *,
    local_reader: Optional[Callable[[Any], bytes]] = None,
) -> List[Dict[str, Any]]:
    """批量计算用户上传参考图的签名列表"""
    signatures: List[Dict[str, Any]] = []
    for raw in uploaded_images or []:
        payload = read_uploaded_image_bytes(raw, local_reader=local_reader)
        if payload:
            sig = image_signatures(payload)
            if sig.get("sha256") or sig.get("pixel_sha256"):
                signatures.append(sig)
    return signatures


def is_candidate_matching_reference(
    candidate_signatures: Dict[str, Any],
    reference_signatures: List[Dict[str, Any]],
    *,
    max_dhash_distance: Optional[int] = None,
) -> bool:
    """检查候选图签名是否命中任一参考图"""
    if not candidate_signatures or not reference_signatures:
        return False
    return any(
        same_image(candidate_signatures, ref, max_dhash_distance=max_dhash_distance)
        for ref in reference_signatures
    )


def filter_reference_images(
    media_items: Iterable[Dict[str, Any]],
    uploaded_images: Iterable[Any],
    *,
    tab: Any = None,
    logger_context: str = "",
    max_dhash_distance: Optional[int] = None,
    local_reader: Optional[Callable[[Any], bytes]] = None,
) -> List[Dict[str, Any]]:
    """
    通用参考图过滤器：
    遍历媒体项列表，识别并剔除与用户上传参考图一致的图片项。
    保留非图片媒体（如音频、视频）以及真正由 AI 生成的新图片。
    """
    if not media_items:
        return []

    items = [item for item in media_items if isinstance(item, dict)]
    if not items:
        return []

    reference_sigs = compute_reference_signatures(uploaded_images, local_reader=local_reader)
    if not reference_sigs:
        return items

    tag = f"[{logger_context}] " if logger_context else ""
    filtered_items: List[Dict[str, Any]] = []
    dropped_count = 0

    for item in items:
        media_type = str(item.get("media_type") or item.get("type") or "image").strip().lower()
        if media_type not in {"image", "image_url", "input_image"}:
            filtered_items.append(item)
            continue

        payload = extract_media_item_bytes(tab, item, local_reader=local_reader)
        if not payload:
            # 无法读取字节时，保留该项（避免误杀）
            filtered_items.append(item)
            continue

        candidate_sig = image_signatures(payload)
        if is_candidate_matching_reference(
            candidate_sig,
            reference_sigs,
            max_dhash_distance=max_dhash_distance,
        ):
            dropped_count += 1
            src_info = item.get("url") or item.get("local_path") or item.get("src") or "data_uri"
            logger.info(
                f"{tag}检测到提取结果与上传参考图一致，已自动过滤该图片项: "
                f"source={str(src_info)[:100]}"
            )
        else:
            filtered_items.append(item)

    if dropped_count > 0:
        logger.debug(
            f"{tag}参考图过滤完成: 原始 {len(items)} 项, 过滤参考图 {dropped_count} 张, 剩余 {len(filtered_items)} 项"
        )

    return filtered_items


def validate_generated_images(
    uploaded_images: Iterable[Any],
    generated_media_items: Iterable[Dict[str, Any]],
    *,
    tab: Any = None,
    max_dhash_distance: Optional[int] = None,
    error_builder: Optional[Callable[[str], Exception]] = None,
    local_reader: Optional[Callable[[Any], bytes]] = None,
) -> None:
    """
    校验生成图片列表中是否存在与上传参考图完全相同的图片。
    若存在，则调用 error_builder 抛出异常；若未提供 error_builder，则抛出 ValueError。
    """
    reference_sigs = compute_reference_signatures(uploaded_images, local_reader=local_reader)
    if not reference_sigs:
        return

    for item in generated_media_items or []:
        if not isinstance(item, dict):
            continue
        media_type = str(item.get("media_type") or item.get("type") or "image").strip().lower()
        if media_type not in {"image", "image_url", "input_image"}:
            continue
        payload = extract_media_item_bytes(tab, item, local_reader=local_reader)
        if not payload:
            continue
        candidate = image_signatures(payload)
        if is_candidate_matching_reference(
            candidate,
            reference_sigs,
            max_dhash_distance=max_dhash_distance,
        ):
            msg = "生成结果与上传图片一致"
            if error_builder:
                raise error_builder(msg)
            raise ValueError(msg)


__all__ = [
    "compute_reference_signatures",
    "extract_media_item_bytes",
    "filter_reference_images",
    "get_current_page_url",
    "image_signatures",
    "is_candidate_matching_reference",
    "read_image_bytes",
    "read_local_image_bytes",
    "read_uploaded_image_bytes",
    "same_image",
    "validate_generated_images",
]
