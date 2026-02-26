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
from app.core.config import get_logger
import requests
import base64
import json
import io
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from PIL import Image


logger = get_logger("IMG_HDL")

# ================= 配置常量 =================

IMAGE_DIR = Path("image")
MAX_IMAGE_SIZE_MB = 20  # 单张图片最大 20MB
DOWNLOAD_TIMEOUT = 30   # 下载超时 30 秒
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}


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
    IMAGE_DIR.mkdir(exist_ok=True)
    
    image_paths = []
    
    for msg in messages:
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
                        local_path = _process_single_image(url)
                        if local_path:
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
        
        # 情况3：本地文件路径（直接返回）
        elif os.path.exists(url):
            logger.info(f"[IMAGE] 使用本地文件: {url}")
            return url
        
        else:
            logger.warning(f"[IMAGE] 不支持的图片格式: {url[:100]}")
            return None
    
    except Exception as e:
        logger.error(f"[IMAGE] 处理失败: {e}")
        return None


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
        base64_data = match.group(2)
        # 🆕 空数据直接拒绝（AstrBot 常见：只给前缀 data:image/...;base64,）
        if not base64_data or not base64_data.strip():
            logger.warning("[IMAGE] Base64 数据为空（仅有 data:image/...;base64, 前缀）")
            return None
        # 解码
        image_bytes = base64.b64decode(base64_data)
        
        # 大小检查
        size_mb = len(image_bytes) / (1024 * 1024)
        if size_mb > MAX_IMAGE_SIZE_MB:
            logger.warning(f"[IMAGE] Base64 图片过大: {size_mb:.2f}MB")
            return None
        
        # 生成文件名
        timestamp = int(time.time() * 1000)
        file_hash = hashlib.md5(image_bytes).hexdigest()[:8]
        filename = f"{timestamp}_{file_hash}.{image_format}"
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
    try:
        logger.debug(f"[IMAGE] 开始下载: {url[:100]}")
        
        # 下载
        response = requests.get(
            url,
            timeout=DOWNLOAD_TIMEOUT,
            headers={'User-Agent': 'Mozilla/5.0'},
            stream=True
        )
        response.raise_for_status()
        
        # 获取内容类型
        content_type = response.headers.get('Content-Type', '')
        
        # 读取内容
        image_bytes = response.content
        
        # 大小检查
        size_mb = len(image_bytes) / (1024 * 1024)
        if size_mb > MAX_IMAGE_SIZE_MB:
            logger.warning(f"[IMAGE] 下载图片过大: {size_mb:.2f}MB")
            return None
        
        # 尝试从 URL 或 Content-Type 推断格式
        ext = _guess_extension(url, content_type)
        
        # 验证图片格式
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.verify()
            ext = f".{img.format.lower()}" if img.format else ext
        except Exception:
            logger.warning("[IMAGE] 图片格式验证失败")
        
        # 生成文件名
        timestamp = int(time.time() * 1000)
        file_hash = hashlib.md5(image_bytes).hexdigest()[:8]
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
        import win32clipboard
        from PIL import Image
        
        # 打开图片
        image = Image.open(image_path)
        
        # 转换为 RGB（剪贴板不支持透明通道）
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # 转换为 DIB 格式（Windows 剪贴板标准）
        output = io.BytesIO()
        image.save(output, 'BMP')
        data = output.getvalue()[14:]  # 跳过 BMP 文件头（14 字节）
        
        # 写入剪贴板
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        finally:
            win32clipboard.CloseClipboard()
        
        logger.debug(f"[CLIPBOARD] 图片已复制: {image_path}")
        return True
    
    except ImportError:
        logger.error("[CLIPBOARD] 缺少 pywin32 依赖，请执行: pip install pywin32")
        return False
    except Exception as e:
        logger.error(f"[CLIPBOARD] 复制失败: {e}")
        return False


__all__ = [
    'extract_images_from_messages',
    'copy_image_to_clipboard',
]