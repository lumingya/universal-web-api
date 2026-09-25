"""
app/utils/image_handler.py - 图片处理工具

职责：
- 解析多模态消息中的图片
- 下载网络图片/解码 Base64
- 保存到本地 image/ 目录
- 复制图片到剪贴板（Windows）
"""

import os
import re
import hashlib
import time
import warnings
from app.core.config import get_logger
import requests
import base64
import json
import io
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from PIL import Image
from app.utils.system_clipboard import (
    ClipboardDependencyError,
    ClipboardUnsupportedError,
    copy_image_to_native_clipboard,
)
from app.utils.remote_resource import get_public_remote_resource


logger = get_logger("IMG_HDL")

# ================= 配置常量 =================

IMAGE_DIR = Path("temp") / "image_inputs"
MAX_IMAGE_SIZE_MB = 20  # 单张图片最大 20MB
MAX_IMAGES_PER_REQUEST = 8
MAX_IMAGES_TOTAL_MB = 50
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 16_384
MAX_IMAGE_FRAMES = 100
IMAGE_INPUT_TTL_SECONDS = 60 * 60
DOWNLOAD_TIMEOUT = 30   # 下载超时 30 秒
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}

_PIL_FORMAT_EXTENSIONS = {
    'JPEG': '.jpg',
    'PNG': '.png',
    'GIF': '.gif',
    'WEBP': '.webp',
    'BMP': '.bmp',
}


# ================= 图片提取 =================

def extract_images_from_messages(messages: List[Dict]) -> List[str]:
    """
    从消息列表中提取所有图片并保存到本地
    
    Args:
        messages: OpenAI 格式的消息列表
    
    Returns:
        本地图片路径列表 ["image/1234567890_abc.png", ...]
    """
    if not messages:
        return []
    
    # 确保 image 目录存在
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_expired_image_inputs()
    
    image_paths = []
    total_bytes = 0

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get('content')
        
        if not content:
            continue
        
        # 🆕 情况1：字符串格式的多模态消息（需要解析）
        if isinstance(content, str):
            # 检测是否是列表的字符串形式
            stripped = content.strip()
            if stripped.startswith('[') and stripped.endswith(']'):
                parsed = None
                
                # 尝试 JSON 解析
                try:
                    parsed = json.loads(stripped)
                    logger.debug("[IMAGE] JSON 解析成功")
                except (json.JSONDecodeError, TypeError):
                    pass
                
                # 尝试 Python literal_eval
                if parsed is None:
                    try:
                        import ast
                        parsed = ast.literal_eval(stripped)
                        logger.debug("[IMAGE] Python literal_eval 解析成功")
                    except (ValueError, SyntaxError):
                        pass
                
                # 解析成功，更新 content
                if parsed and isinstance(parsed, list):
                    content = parsed
                else:
                    continue  # 纯文本，无图片
            else:
                continue  # 纯文本，无图片
        
        # 情况2：列表格式（多模态）
        if isinstance(content, (list, tuple)):
            for item in content:
                if not isinstance(item, dict):
                    continue
                
                if item.get("type") == "image_url":
                    image_url_obj = item.get("image_url", {})
                    
                    if isinstance(image_url_obj, dict):
                        url = image_url_obj.get("url", "")
                    else:
                        url = str(image_url_obj)
                    
                    if url:
                        if len(image_paths) >= MAX_IMAGES_PER_REQUEST:
                            logger.warning(f"[IMAGE] 单次请求图片数量超过限制: {MAX_IMAGES_PER_REQUEST}")
                            return image_paths
                        local_path = _process_single_image(url)
                        if local_path:
                            try:
                                image_size = Path(local_path).stat().st_size
                            except OSError:
                                image_size = 0
                            if total_bytes + image_size > MAX_IMAGES_TOTAL_MB * 1024 * 1024:
                                try:
                                    Path(local_path).unlink(missing_ok=True)
                                except OSError:
                                    pass
                                logger.warning(f"[IMAGE] 单次请求图片累计大小超过限制: {MAX_IMAGES_TOTAL_MB}MB")
                                return image_paths
                            total_bytes += image_size
                            image_paths.append(local_path)
    
    if image_paths:
        logger.debug(f"[IMAGE] 成功处理 {len(image_paths)} 张图片")
    
    return image_paths


def _process_single_image(url: str) -> Optional[str]:
    """
    处理单张图片：下载或解码，保存到本地
    
    Args:
        url: 图片 URL（可以是 https:// 或 data:image/... 格式）
    
    Returns:
        本地文件路径，失败返回 None
    """
    try:
        # 情况1：Base64 Data URI
        if url.startswith("data:image"):
            return _save_base64_image(url)
        
        # 情况2：网络 URL
        elif url.startswith(("http://", "https://")):
            return _download_image(url)
        
        else:
            logger.warning(f"[IMAGE] 不支持的图片格式: {url[:100]}")
            return None
    
    except Exception as e:
        logger.error(f"[IMAGE] 处理失败: {e}")
        return None


def _validate_image_bytes(image_bytes: bytes) -> Optional[str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with io.BytesIO(image_bytes) as image_buffer:
                with Image.open(image_buffer) as img:
                    width, height = img.size
                    frames = int(getattr(img, "n_frames", 1) or 1)
                    detected_ext = _PIL_FORMAT_EXTENSIONS.get(str(img.format or '').upper())
                    if not detected_ext:
                        raise ValueError(f"unsupported_image_format:{img.format}")
                    if width <= 0 or height <= 0:
                        raise ValueError("invalid_image_dimensions")
                    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
                        raise ValueError(f"image_dimensions_too_large:{width}x{height}")
                    if width * height > MAX_IMAGE_PIXELS:
                        raise ValueError(f"image_pixels_too_large:{width * height}")
                    if frames > MAX_IMAGE_FRAMES:
                        raise ValueError(f"image_frames_too_many:{frames}")
                    img.verify()
        return detected_ext
    except Exception as exc:
        logger.warning(f"[IMAGE] 图片格式或尺寸验证失败: {str(exc)[:120]}")
        return None


def _cleanup_expired_image_inputs(now: Optional[float] = None) -> None:
    cutoff = float(time.time() if now is None else now) - IMAGE_INPUT_TTL_SECONDS
    try:
        for path in IMAGE_DIR.iterdir():
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        return


def _save_base64_image(data_uri: str) -> Optional[str]:
    """
    解码并保存 Base64 图片
    
    格式: data:image/png;base64,iVBORw0KGgo...
    """
    try:
        # 提取 MIME 类型和 Base64 数据
        match = re.match(r'data:image/(\w+);base64,(.+)', data_uri)
        if not match:
            logger.warning("[IMAGE] Base64 格式无效")
            return None
        
        image_format = match.group(1).lower()
        claimed_ext = f".{image_format}"
        if claimed_ext not in SUPPORTED_FORMATS:
            logger.warning(f"[IMAGE] 不支持的 Base64 图片格式: {image_format}")
            return None
        base64_data = match.group(2)
        # 🆕 空数据直接拒绝（AstrBot 常见：只给前缀 data:image/...;base64,）
        if not base64_data or not base64_data.strip():
            logger.warning("[IMAGE] Base64 数据为空（仅有 data:image/...;base64, 前缀）")
            return None
        compact_base64 = re.sub(r"\s+", "", base64_data)
        padding = compact_base64.count("=")
        estimated_size = max(0, (len(compact_base64) * 3) // 4 - padding)
        max_bytes = int(MAX_IMAGE_SIZE_MB * 1024 * 1024)
        if estimated_size > max_bytes:
            logger.warning(f"[IMAGE] Base64 图片过大: {estimated_size / (1024 * 1024):.2f}MB")
            return None
        # 解码
        padded_base64 = compact_base64 + ("=" * ((4 - len(compact_base64) % 4) % 4))
        image_bytes = base64.b64decode(padded_base64, validate=True)
        
        # 大小检查
        size_mb = len(image_bytes) / (1024 * 1024)
        if size_mb > MAX_IMAGE_SIZE_MB:
            logger.warning(f"[IMAGE] Base64 图片过大: {size_mb:.2f}MB")
            return None
        
        detected_ext = _validate_image_bytes(image_bytes)
        if not detected_ext:
            return None

        # 生成文件名
        timestamp = int(time.time() * 1000)
        file_hash = hashlib.sha256(image_bytes).hexdigest()[:12]
        filename = f"{timestamp}_{file_hash}{detected_ext}"
        filepath = IMAGE_DIR / filename
        
        # 保存文件
        with open(filepath, 'wb') as f:
            f.write(image_bytes)
        
        logger.debug(f"[IMAGE] Base64 已保存: {filepath} ({size_mb:.2f}MB)")
        return str(filepath)
    
    except Exception as e:
        logger.error(f"[IMAGE] Base64 解码失败: {e}")
        return None


def _download_image(url: str) -> Optional[str]:
    """
    下载网络图片
    """
    response = None
    try:
        logger.debug(f"[IMAGE] 开始下载: {url[:100]}")
        
        # 下载
        response = get_public_remote_resource(
            url,
            timeout=(8, DOWNLOAD_TIMEOUT),
            headers={'User-Agent': 'Mozilla/5.0'},
            stream=True
        )
        response.raise_for_status()
        
        # 获取内容类型
        content_type = response.headers.get('Content-Type', '')

        max_bytes = int(MAX_IMAGE_SIZE_MB * 1024 * 1024)
        content_length = response.headers.get('Content-Length')
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    logger.warning(f"[IMAGE] 下载图片过大: {int(content_length) / (1024 * 1024):.2f}MB")
                    return None
            except (TypeError, ValueError):
                pass
        
        # 流式读取内容，避免超大响应在尺寸检查前完整进入内存
        chunks = []
        total_bytes = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                logger.warning(f"[IMAGE] 下载图片过大: {total_bytes / (1024 * 1024):.2f}MB")
                return None
            chunks.append(chunk)
        image_bytes = b''.join(chunks)
        
        # 大小检查
        size_mb = len(image_bytes) / (1024 * 1024)
        if size_mb > MAX_IMAGE_SIZE_MB:
            logger.warning(f"[IMAGE] 下载图片过大: {size_mb:.2f}MB")
            return None
        
        # 尝试从 URL 或 Content-Type 推断格式
        ext = _guess_extension(url, content_type)

        detected_ext = _validate_image_bytes(image_bytes)
        if not detected_ext:
            return None
        ext = detected_ext
        
        # 生成文件名
        timestamp = int(time.time() * 1000)
        file_hash = hashlib.sha256(image_bytes).hexdigest()[:12]
        filename = f"{timestamp}_{file_hash}{ext}"
        filepath = IMAGE_DIR / filename
        
        # 保存文件
        with open(filepath, 'wb') as f:
            f.write(image_bytes)
        
        logger.info(f"[IMAGE] 下载成功: {filepath} ({size_mb:.2f}MB)")
        return str(filepath)
    
    except requests.RequestException as e:
        logger.error(f"[IMAGE] 下载失败: {e}")
        return None
    except Exception as e:
        logger.error(f"[IMAGE] 保存失败: {e}")
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _guess_extension(url: str, content_type: str) -> str:
    """
    从 URL 或 Content-Type 推断文件扩展名
    """
    # 从 URL 提取
    url_lower = url.lower()
    for ext in SUPPORTED_FORMATS:
        if url_lower.endswith(ext):
            return ext
    
    # 从 Content-Type 提取
    if 'image/' in content_type:
        format_name = content_type.split('/')[-1].split(';')[0].strip()
        ext = f".{format_name}"
        if ext in SUPPORTED_FORMATS:
            return ext
    
    # 默认 PNG
    return '.png'


# ================= 剪贴板操作 =================

def copy_image_to_clipboard(image_path: str) -> bool:
    """
    复制图片到 Windows 剪贴板
    
    Args:
        image_path: 本地图片路径
    
    Returns:
        是否成功
    """
    try:
        copy_image_to_native_clipboard(image_path)
        logger.debug(f"[CLIPBOARD] 图片已复制: {image_path}")
        return True

    except ClipboardUnsupportedError:
        logger.info("[CLIPBOARD] 当前平台不支持原生图片剪贴板，将依赖网页原生上传入口")
        return False
    except ClipboardDependencyError:
        logger.error("[CLIPBOARD] 缺少 Windows 原生图片剪贴板依赖，请执行: pip install pywin32")
        return False
    except Exception as e:
        logger.error(f"[CLIPBOARD] 复制失败: {e}")
        return False


__all__ = [
    'extract_images_from_messages',
    'copy_image_to_clipboard',
]
