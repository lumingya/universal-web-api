"""
app/utils/file_paste.py - 文件粘贴工具

职责：
- 创建临时 txt 文件（存放超长文本）
- 通过 Win32 CF_HDROP 格式将文件复制到系统剪贴板
- 管理 temp 目录的生命周期（启动时清理、退出时清理）
"""

import os
import struct
import tempfile
import shutil
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger('file_paste')

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
        import win32clipboard
        
        # 确保文件存在
        abs_path = os.path.abspath(filepath)
        if not os.path.exists(abs_path):
            logger.error(f"文件不存在: {abs_path}")
            return False
        
        # 构建 DROPFILES 结构体
        # 参考: https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/ns-shlobj_core-dropfiles
        #
        # typedef struct _DROPFILES {
        #     DWORD pFiles;    // 文件名列表相对于结构体起始的偏移量
        #     POINT pt;        // 放置点 (x, y)
        #     BOOL  fNC;       // 是否在非客户区
        #     BOOL  fWide;     // 是否使用宽字符 (Unicode)
        # } DROPFILES;
        #
        # 结构体大小: 4 + 4 + 4 + 4 + 4 = 20 字节
        
        # 使用 Unicode 路径（fWide=1）
        # 文件列表: 路径以 \0 分隔，整体以 \0\0 结尾
        file_list = abs_path + '\0'  # 单个文件 + 终结符
        file_list += '\0'            # 列表结束的额外 \0
        
        # 编码为 UTF-16LE（Windows 宽字符）
        file_list_bytes = file_list.encode('utf-16-le')
        
        # DROPFILES 结构体头部
        # pFiles = 20 (结构体本身大小，文件列表紧跟其后)
        # pt.x = 0, pt.y = 0
        # fNC = 0
        # fWide = 1 (使用 Unicode)
        header = struct.pack('IIIii', 20, 0, 0, 0, 1)
        
        # 完整数据 = 头部 + 文件列表
        data = header + file_list_bytes
        
        # CF_HDROP = 15
        CF_HDROP = 15
        
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(CF_HDROP, data)
        finally:
            win32clipboard.CloseClipboard()
        
        logger.debug(f"文件已复制到剪贴板: {abs_path}")
        return True
    
    except ImportError:
        logger.error("pywin32 未安装，无法使用文件粘贴功能")
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