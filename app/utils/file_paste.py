"""
app/utils/file_paste.py - 文件粘贴工具

职责：
- 创建临时 txt 文件（存放超长文本）
- 通过 Win32 CF_HDROP 格式将文件复制到系统剪贴板
- 管理 temp 目录的生命周期（启动时清理、退出时清理）
"""

import os
import tempfile
import shutil
from app.core.config import get_logger
from pathlib import Path
from typing import Optional
from app.utils.system_clipboard import (
    ClipboardDependencyError,
    ClipboardUnsupportedError,
    copy_file_to_native_clipboard,
)

logger = get_logger("FPASTE")

# ================= 临时目录管理 =================

# 项目根目录下的 temp 文件夹
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMP_DIR = _PROJECT_ROOT / "temp"


def ensure_temp_dir() -> Path:
    """确保 temp 目录存在"""
    TEMP_DIR.mkdir(exist_ok=True)
    return TEMP_DIR


def cleanup_temp_dir():
    """
    清理 temp 目录中的所有内容
    
    调用时机：
    - 程序启动时
    - 程序退出时
    """
    if not TEMP_DIR.exists():
        return
    
    try:
        count = 0
        for item in TEMP_DIR.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                    count += 1
                elif item.is_dir():
                    shutil.rmtree(item)
                    count += 1
            except Exception as e:
                logger.debug(f"清理临时文件失败: {item} - {e}")
        
        if count > 0:
            logger.info(f"已清理 {count} 个临时文件")
    except Exception as e:
        logger.warning(f"清理临时目录失败: {e}")


# ================= 临时文件创建 =================

def create_temp_txt(text: str, prefix: str = "paste_") -> Optional[str]:
    """
    将文本写入临时 txt 文件
    
    Args:
        text: 要写入的文本内容
        prefix: 文件名前缀
    
    Returns:
        文件的绝对路径，失败返回 None
    """
    try:
        ensure_temp_dir()
        
        # 使用 tempfile 创建唯一文件名，放在项目的 temp 目录下
        fd, filepath = tempfile.mkstemp(
            suffix=".txt",
            prefix=prefix,
            dir=str(TEMP_DIR)
        )
        
        # 写入内容（UTF-8 编码）
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        
        logger.debug(f"临时文件已创建: {filepath} ({len(text)} 字符)")
        return filepath
    
    except Exception as e:
        logger.error(f"创建临时文件失败: {e}")
        return None


# ================= Win32 剪贴板文件复制 =================

def copy_file_to_clipboard(filepath: str) -> bool:
    """
    将文件以 CF_HDROP 格式复制到系统剪贴板
    
    这模拟了用户在文件管理器中「复制」文件的操作。
    之后在网页输入框中 Ctrl+V 即可粘贴文件。
    
    Args:
        filepath: 文件的绝对路径
    
    Returns:
        是否成功
    """
    try:
        abs_path = os.path.abspath(filepath)
        if not os.path.exists(abs_path):
            logger.error(f"文件不存在: {abs_path}")
            return False

        copy_file_to_native_clipboard(abs_path)
        logger.debug(f"文件已复制到剪贴板: {abs_path}")
        return True

    except ClipboardUnsupportedError:
        logger.info("当前平台不支持原生文件剪贴板，将依赖 file input / drop_zone 上传")
        return False
    except ClipboardDependencyError:
        logger.error("缺少 Windows 原生文件剪贴板依赖，请执行: pip install pywin32")
        return False
    except Exception as e:
        logger.error(f"复制文件到剪贴板失败: {e}")
        return False


# ================= 组合操作 =================

def prepare_file_paste(text: str) -> Optional[str]:
    """
    完整的文件粘贴准备流程：
    1. 创建临时 txt 文件
    2. 将文件复制到剪贴板
    
    Args:
        text: 要粘贴的文本内容
    
    Returns:
        临时文件路径（成功时），失败返回 None
    """
    filepath = create_temp_txt(text)
    if not filepath:
        return None
    
    if not copy_file_to_clipboard(filepath):
        # 清理失败的临时文件
        try:
            os.unlink(filepath)
        except Exception:
            pass
        return None
    
    return filepath


__all__ = [
    'TEMP_DIR',
    'ensure_temp_dir',
    'cleanup_temp_dir',
    'create_temp_txt',
    'copy_file_to_clipboard',
    'prepare_file_paste',
]
