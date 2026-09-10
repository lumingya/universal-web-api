"""
app/utils/file_paste.py - 文件粘贴工具

职责：
- 创建临时 txt/pdf 文件（存放超长文本）
- 通过 Win32 CF_HDROP 格式将文件复制到系统剪贴板
- 管理 temp 目录的生命周期（启动时清理、退出时清理）
"""

import os
import tempfile
import shutil
import time
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
TRANSCODED_CACHE_DIR = _PROJECT_ROOT / "download_images" / "_transcoded"
SUPPORTED_TEMP_FILE_TYPES = ("txt", "pdf")
DEFAULT_TEMP_FILE_TYPE = "txt"


def _get_temp_cleanup_min_age_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("TEMP_CLEANUP_MIN_AGE_SECONDS", "3600")))
    except Exception:
        return 3600.0


def _get_paste_temp_cleanup_min_age_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("PASTE_TEMP_CLEANUP_MIN_AGE_SECONDS", "900")))
    except Exception:
        return 900.0


TEMP_CLEANUP_MIN_AGE_SECONDS = _get_temp_cleanup_min_age_seconds()
PASTE_TEMP_CLEANUP_MIN_AGE_SECONDS = _get_paste_temp_cleanup_min_age_seconds()


def _get_cleanup_min_age_seconds(path: Path) -> float:
    try:
        if (
            path.is_file()
            and path.name.startswith("paste_")
            and path.suffix.lower() in {".txt", ".pdf"}
        ):
            return PASTE_TEMP_CLEANUP_MIN_AGE_SECONDS
    except Exception:
        pass
    return TEMP_CLEANUP_MIN_AGE_SECONDS


def _is_path_old_enough_for_cleanup(path: Path, now: float, min_age_seconds: float) -> bool:
    try:
        if now - path.stat().st_mtime < min_age_seconds:
            return False
        if path.is_dir():
            for child in path.rglob("*"):
                try:
                    if now - child.stat().st_mtime < min_age_seconds:
                        return False
                except Exception:
                    return False
        return True
    except Exception:
        return False


def _temp_log_label(filepath: str) -> str:
    """Return a short temp-file label for logs without exposing absolute paths."""
    name = Path(str(filepath or "")).name
    return f"temp/{name}" if name else "temp/<unknown>"


def ensure_temp_dir() -> Path:
    """确保 temp 目录存在"""
    TEMP_DIR.mkdir(exist_ok=True)
    return TEMP_DIR


def normalize_temp_file_type(file_type: str) -> str:
    """Normalize the configured temporary attachment type."""
    normalized = str(file_type or DEFAULT_TEMP_FILE_TYPE).strip().lower().lstrip(".")
    if normalized in SUPPORTED_TEMP_FILE_TYPES:
        return normalized
    return DEFAULT_TEMP_FILE_TYPE


def cleanup_temp_dir():
    """
    清理 temp 目录中的过期内容

    调用时机：
    - 程序启动时
    - 程序退出时
    """
    try:
        count = 0
        now = time.time()
        for target_dir, label_prefix in (
            (TEMP_DIR, "temp"),
            (TRANSCODED_CACHE_DIR, "download_images/_transcoded"),
        ):
            if not target_dir.exists():
                continue
            for item in target_dir.iterdir():
                if item.name == "attachment_inputs" and target_dir == TEMP_DIR:
                    from app.utils.attachments import cleanup_attachment_inputs
                    cleanup_attachment_inputs()
                    continue
                try:
                    min_age_seconds = _get_cleanup_min_age_seconds(item)
                    if not _is_path_old_enough_for_cleanup(item, now, min_age_seconds):
                        continue
                    if item.is_file():
                        item.unlink()
                        count += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        count += 1
                except Exception as e:
                    short_name = item.name or "<unknown>"
                    logger.debug(f"清理临时文件失败: {label_prefix}/{short_name} - {e}")

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
    fd: Optional[int] = None
    filepath: Optional[str] = None
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
            fd = None
            f.write(text)
        
        logger.debug(f"临时文件已创建: {_temp_log_label(filepath)} ({len(text)} 字符)")
        return filepath
    
    except Exception as e:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if filepath:
            try:
                os.unlink(filepath)
                logger.debug(f"已清理创建失败的临时文件: {_temp_log_label(filepath)}")
            except Exception:
                pass
        logger.error(f"创建临时文件失败: {e}")
        return None


def _pdf_font_candidates() -> list[str]:
    """Return likely local fonts that can render Chinese and Latin text."""
    env_path = os.getenv("FILE_PASTE_PDF_FONT_PATH", "").strip()
    candidates = []
    if env_path:
        candidates.append(env_path)

    candidates.extend([
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ])
    return candidates


def _find_pdf_font_path(*, prefer_single_face: bool = False) -> Optional[str]:
    """Find a local font for generated PDFs."""
    candidates = _pdf_font_candidates()
    if prefer_single_face:
        candidates = sorted(
            candidates,
            key=lambda path: 0 if Path(str(path)).suffix.lower() in {".ttf", ".otf"} else 1,
        )
    for path in candidates:
        try:
            if path and os.path.exists(path):
                return path
        except Exception:
            continue
    return None


def _write_pdf_with_reportlab(text: str, filepath: str) -> None:
    """Write a text-layer PDF when reportlab is available."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    page_width, page_height = A4
    margin_x, margin_y = 12, 12
    font_size = 4.0
    line_height = 4.8
    font_name = "Helvetica"
    font_path = _find_pdf_font_path(prefer_single_face=True)
    if font_path:
        font_name = "FilePasteFont"
        pdfmetrics.registerFont(TTFont(font_name, font_path))

    max_width = page_width - (margin_x * 2)

    def string_width(value: str) -> float:
        return pdfmetrics.stringWidth(value, font_name, font_size)

    def wrap_paragraph(paragraph: str) -> list[str]:
        if paragraph == "":
            return [""]
        lines = []
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and string_width(candidate) > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        lines.append(current)
        return lines

    pdf = canvas.Canvas(filepath, pagesize=A4)
    pdf.setTitle("Temporary context")
    pdf.setAuthor("Universal Web-to-API")

    y = page_height - margin_y
    normalized_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    for paragraph in normalized_text.split("\n") or [""]:
        for line in wrap_paragraph(paragraph):
            if y < margin_y:
                pdf.showPage()
                y = page_height - margin_y
            pdf.setFont(font_name, font_size)
            pdf.drawString(margin_x, y, line)
            y -= line_height

    pdf.save()


def _write_temp_pdf(text: str, filepath: str) -> None:
    """Generate text-layer PDF only; never silently degrade context to raster pages."""
    _write_pdf_with_reportlab(text, filepath)


def create_temp_pdf(text: str, prefix: str = "paste_") -> Optional[str]:
    """
    将文本写入临时 pdf 文件

    Args:
        text: 要写入的文本内容
        prefix: 文件名前缀

    Returns:
        文件的绝对路径，失败返回 None
    """
    fd: Optional[int] = None
    filepath: Optional[str] = None
    try:
        ensure_temp_dir()

        fd, filepath = tempfile.mkstemp(
            suffix=".pdf",
            prefix=prefix,
            dir=str(TEMP_DIR)
        )
        os.close(fd)
        fd = None

        _write_temp_pdf(text, filepath)

        logger.debug(f"PDF 临时文件已创建: {_temp_log_label(filepath)} ({len(text)} 字符)")
        return filepath

    except Exception as e:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if filepath:
            try:
                os.unlink(filepath)
                logger.debug(f"已清理创建失败的临时文件: {_temp_log_label(filepath)}")
            except Exception:
                pass
        logger.error(f"创建 PDF 临时文件失败: {e}")
        return None


def create_temp_file(text: str, prefix: str = "paste_", file_type: str = DEFAULT_TEMP_FILE_TYPE) -> Optional[str]:
    """Create a temporary attachment for large text using the configured file type."""
    normalized_type = normalize_temp_file_type(file_type)
    if normalized_type == "pdf":
        return create_temp_pdf(text, prefix=prefix)
    return create_temp_txt(text, prefix=prefix)


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
            logger.error(f"文件不存在: {_temp_log_label(abs_path)}")
            return False

        copy_file_to_native_clipboard(abs_path)
        logger.debug(f"文件已复制到剪贴板: {_temp_log_label(abs_path)}")
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

def prepare_file_paste(text: str, file_type: str = DEFAULT_TEMP_FILE_TYPE) -> Optional[str]:
    """
    完整的文件粘贴准备流程：
    1. 创建临时 txt/pdf 文件
    2. 将文件复制到剪贴板
    
    Args:
        text: 要粘贴的文本内容
    
    Returns:
        临时文件路径（成功时），失败返回 None
    """
    filepath = create_temp_file(text, file_type=file_type)
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
    'SUPPORTED_TEMP_FILE_TYPES',
    'DEFAULT_TEMP_FILE_TYPE',
    'ensure_temp_dir',
    'cleanup_temp_dir',
    'normalize_temp_file_type',
    'create_temp_file',
    'create_temp_txt',
    'create_temp_pdf',
    'copy_file_to_clipboard',
    'prepare_file_paste',
]
