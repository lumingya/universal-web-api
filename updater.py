#!/usr/bin/env python3
"""
Universal Web-to-API 自动更新模块 v2.1
修复：临时文件处理 + GitHub 重定向处理
"""

import os
import sys
import json
import shutil
import zipfile
import tempfile
import urllib.request
import urllib.error
import time
import re
import ssl
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple
from http.client import IncompleteRead
from update_preserve import (
    build_effective_preserve_patterns,
    get_default_update_preserve_patterns,
    load_update_preserve_settings,
)

# ============ 配置常量 ============
GITHUB_API_BASE = "https://api.github.com/repos"
GITHUB_DOWNLOAD_BASE = "https://github.com"
DEFAULT_REPO = "lumingya/universal-web-api"

# 网络配置
MAX_RETRIES = 3
RETRY_DELAY = 3
CHUNK_SIZE = 8192
API_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 300

# 更新时保留的文件/目录
DEFAULT_PRESERVE = get_default_update_preserve_patterns()

SITES_CONFIG_PATH = Path("config") / "sites.json"
COMMANDS_CONFIG_PATH = Path("config") / "commands.json"
COMMAND_PRESERVE_FIELDS = ("enabled", "group_name", "last_triggered", "trigger_count")
BACKUP_EXCLUDE_NAMES = {
    ".git",
    ".update_temp",
    "__pycache__",
    "venv",
    "logs",
    "temp",
    "chrome_profile",
}

class Colors:
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def colored(text: str, color: str) -> str:
    if os.name == 'nt':
        os.system('')
    return f"{color}{text}{Colors.END}"

def log_info(msg: str):
    print(f"[{colored('INFO', Colors.CYAN)}] {msg}")

def log_success(msg: str):
    print(f"[{colored('OK', Colors.GREEN)}] {msg}")

def log_warning(msg: str):
    print(f"[{colored('WARN', Colors.YELLOW)}] {msg}")

def log_error(msg: str):
    print(f"[{colored('ERROR', Colors.RED)}] {msg}")

def log_debug(msg: str):
    if os.getenv('DEBUG', '').lower() in ('1', 'true', 'yes'):
        print(f"[{colored('DEBUG', Colors.YELLOW)}] {msg}")

def create_ssl_context():
    """创建默认的 SSL 上下文，启用证书链和主机名校验。"""
    return ssl.create_default_context()

def get_opener():
    """创建使用默认 TLS 校验的 opener。"""
    https_handler = urllib.request.HTTPSHandler(context=create_ssl_context())
    opener = urllib.request.build_opener(https_handler)
    return opener

def http_request_with_retry(url: str, headers: dict = None, timeout: int = API_TIMEOUT) -> Optional[bytes]:
    """HTTP 请求，带重试机制"""
    if headers is None:
        headers = {}
    
    headers.setdefault('User-Agent', 'Universal-Web-API-Updater/2.1')
    
    for attempt in range(MAX_RETRIES):
        try:
            if attempt > 0:
                log_info(f"重试请求 ({attempt + 1}/{MAX_RETRIES})...")
                time.sleep(RETRY_DELAY)
            
            req = urllib.request.Request(url, headers=headers)
            
            opener = get_opener()
            with opener.open(req, timeout=timeout) as response:
                return response.read()
                    
        except IncompleteRead as e:
            log_warning(f"数据读取不完整: {len(e.partial)} bytes")
            if e.partial and len(e.partial) > 100:
                return e.partial
        except urllib.error.HTTPError as e:
            log_error(f"HTTP {e.code}: {e.reason}")
            if e.code == 404:
                return None
        except urllib.error.URLError as e:
            log_error(f"网络错误: {e.reason}")
        except Exception as e:
            log_error(f"请求失败: {e}")
    
    return None

def normalize_version(version: str) -> str:
    """标准化版本号格式"""
    version = version.strip().lstrip('vV').strip()
    
    match = re.match(r'^(\d+)\.(\d{2,})$', version)
    if match:
        major = match.group(1)
        rest = match.group(2)
        if len(rest) == 2:
            version = f"{major}.{rest[0]}.{rest[1]}"
    
    parts = version.split('.')
    while len(parts) < 3:
        parts.append('0')
    
    result_parts = []
    for p in parts[:3]:
        try:
            result_parts.append(str(int(p)))
        except ValueError:
            result_parts.append('0')
    
    return '.'.join(result_parts)

def get_current_version() -> str:
    """获取当前版本号"""
    version_file = Path(__file__).parent / "VERSION"
    
    if version_file.exists():
        return normalize_version(version_file.read_text().strip())
    
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("CURRENT_VERSION="):
                return normalize_version(line.split("=", 1)[1].strip())
    
    return "0.0.0"

def parse_version(version: str) -> Tuple[int, ...]:
    """解析版本号为元组"""
    normalized = normalize_version(version)
    try:
        return tuple(int(p) for p in normalized.split('.')[:3])
    except (ValueError, IndexError):
        return (0, 0, 0)

def compare_versions(v1: str, v2: str) -> int:
    """比较版本号"""
    p1 = parse_version(v1)
    p2 = parse_version(v2)
    
    if p1 > p2:
        return 1
    elif p1 < p2:
        return -1
    return 0

def fetch_latest_release(repo: str) -> Optional[dict]:
    """获取最新 Release 信息"""
    api_url = f"{GITHUB_API_BASE}/{repo}/releases/latest"
    
    log_info(f"请求 GitHub API: {api_url}")
    
    data = http_request_with_retry(
        api_url,
        headers={'Accept': 'application/vnd.github.v3+json'}
    )
    
    if data:
        try:
            release_info = json.loads(data.decode('utf-8'))
            log_debug(f"Release: tag={release_info.get('tag_name')}, assets={len(release_info.get('assets', []))}")
            return release_info
        except json.JSONDecodeError as e:
            log_error(f"JSON 解析失败: {e}")
    
    return None

def get_download_url_from_release(release: dict, repo: str) -> Tuple[Optional[str], str]:
    """从 release 信息中获取下载 URL"""
    tag_name = release.get('tag_name', '')
    
    # 优先从 Assets 中查找
    assets = release.get('assets', [])
    log_info(f"Release 包含 {len(assets)} 个 Assets")
    
    for asset in assets:
        name = asset.get('name', '')
        download_url = asset.get('browser_download_url', '')
        size = asset.get('size', 0)
        
        log_debug(f"  Asset: {name} ({size} bytes)")
        
        if name.endswith('.zip'):
            log_success(f"找到 Release Asset: {name}")
            return (download_url, 'asset')
    
    for asset in assets:
        name = asset.get('name', '')
        download_url = asset.get('browser_download_url', '')
        
        if name.endswith('.tar.gz') or name.endswith('.tgz'):
            log_success(f"找到 Release Asset (tar.gz): {name}")
            return (download_url, 'asset')
    
    # 使用 zipball_url
    zipball_url = release.get('zipball_url')
    if zipball_url:
        log_warning("未找到 Release Assets，使用源代码压缩包")
        return (zipball_url, 'zipball')
    
    # 构造 tag 下载链接
    if tag_name:
        fallback_url = f"{GITHUB_DOWNLOAD_BASE}/{repo}/archive/refs/tags/{tag_name}.zip"
        log_warning(f"使用 tag 下载链接")
        return (fallback_url, 'tag')
    
    return (None, 'none')

def download_file_robust(url: str, dest_path: Path) -> bool:
    """
    健壮的文件下载函数
    修复：临时文件处理 + GitHub 重定向
    """
    log_info(f"下载 URL: {url}")
    
    # 确保目标目录存在
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    for attempt in range(MAX_RETRIES):
        try:
            if attempt > 0:
                log_info(f"重试下载 ({attempt + 1}/{MAX_RETRIES})...")
                time.sleep(RETRY_DELAY * attempt)  # 递增延迟
            
            # 构建请求
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/octet-stream, application/zip, */*',
            }
            req = urllib.request.Request(url, headers=headers)
            
            # 使用默认 TLS 校验与标准重定向处理
            opener = get_opener()
            response = opener.open(req, timeout=DOWNLOAD_TIMEOUT)
            
            # 获取最终 URL（处理重定向后）
            final_url = response.geturl()
            if final_url != url:
                log_info(f"重定向到: {final_url[:80]}...")
            
            # 获取文件信息
            total_size = int(response.headers.get('content-length', 0))
            content_type = response.headers.get('content-type', 'unknown')
            
            log_debug(f"Content-Type: {content_type}")
            log_debug(f"Content-Length: {total_size}")
            
            # 检查是否是 HTML（可能是错误页面）
            if 'text/html' in content_type.lower():
                log_warning("服务器返回 HTML，可能是错误页面")
                # 仍然尝试下载，后面会验证
            
            # 使用临时文件下载
            temp_path = dest_path.parent / f".downloading_{dest_path.name}"
            downloaded = 0
            
            try:
                with open(temp_path, 'wb') as f:
                    while True:
                        try:
                            chunk = response.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            
                            # 显示进度
                            if total_size > 0:
                                percent = min(100, downloaded * 100 // total_size)
                                bar_len = 30
                                filled = int(bar_len * percent / 100)
                                bar = '=' * filled + '-' * (bar_len - filled)
                                size_mb = downloaded / 1024 / 1024
                                total_mb = total_size / 1024 / 1024
                                print(f"\r    [{bar}] {percent:3d}% ({size_mb:.2f}/{total_mb:.2f} MB)", end='', flush=True)
                            else:
                                size_kb = downloaded / 1024
                                print(f"\r    已下载: {size_kb:.1f} KB", end='', flush=True)
                                
                        except IncompleteRead as e:
                            if e.partial:
                                f.write(e.partial)
                                downloaded += len(e.partial)
                            log_warning(f"\n读取中断，已下载 {downloaded} bytes")
                            break
                
                print()  # 换行
                response.close()
                
            except Exception as e:
                log_error(f"写入文件失败: {e}")
                if temp_path.exists():
                    temp_path.unlink()
                continue
            
            # 检查下载的文件
            if not temp_path.exists():
                log_error("临时文件不存在")
                continue
            
            actual_size = temp_path.stat().st_size
            log_info(f"下载完成: {actual_size:,} bytes")
            
            # 检查文件大小
            if actual_size < 1000:
                log_error(f"文件太小，可能是错误响应")
                # 显示文件内容
                try:
                    with open(temp_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read(500)
                    log_error(f"文件内容: {content[:200]}")
                except:
                    pass
                temp_path.unlink()
                continue
            
            if total_size > 0 and actual_size < total_size * 0.9:
                log_warning(f"文件可能不完整: {actual_size}/{total_size}")
                temp_path.unlink()
                continue
            
            # 检查文件类型（魔数）
            with open(temp_path, 'rb') as f:
                header = f.read(10)
            
            log_debug(f"文件头 (hex): {header[:10].hex()}")
            
            # ZIP: PK\x03\x04 或 PK\x05\x06 (空zip) 或 PK\x07\x08
            if header[:2] == b'PK':
                log_debug("检测到 ZIP 文件格式")
            elif header[:2] == b'\x1f\x8b':
                log_debug("检测到 GZIP 文件格式")
            elif header[:5] == b'<!DOC' or header[:5] == b'<html' or header[:5] == b'<HTML':
                log_error("下载的是 HTML 页面！")
                try:
                    with open(temp_path, 'r', encoding='utf-8', errors='ignore') as f:
                        html_content = f.read(500)
                    if 'Not Found' in html_content:
                        log_error("GitHub 返回 404 Not Found")
                    elif 'rate limit' in html_content.lower():
                        log_error("GitHub API 速率限制")
                    else:
                        log_error(f"HTML 内容: {html_content[:200]}")
                except:
                    pass
                temp_path.unlink()
                continue
            
            # 验证 ZIP 完整性
            try:
                with zipfile.ZipFile(temp_path, 'r') as zf:
                    # 测试 ZIP 完整性
                    bad_file = zf.testzip()
                    if bad_file:
                        log_error(f"ZIP 文件损坏: {bad_file}")
                        temp_path.unlink()
                        continue
                    
                    file_list = zf.namelist()
                    log_info(f"ZIP 包含 {len(file_list)} 个文件")
                    
                    if len(file_list) == 0:
                        log_error("ZIP 文件为空")
                        temp_path.unlink()
                        continue
                    
                    # 显示前几个文件
                    for f in file_list[:3]:
                        log_debug(f"  - {f}")
                        
            except zipfile.BadZipFile as e:
                log_error(f"不是有效的 ZIP 文件: {e}")
                # 显示文件开头内容用于调试
                try:
                    with open(temp_path, 'rb') as f:
                        start = f.read(100)
                    log_debug(f"文件开头: {start[:50]}")
                except:
                    pass
                temp_path.unlink()
                continue
            
            # 一切正常，移动到目标位置
            if dest_path.exists():
                dest_path.unlink()
            shutil.move(str(temp_path), str(dest_path))
            
            log_success(f"文件保存到: {dest_path.name}")
            return True
            
        except urllib.error.HTTPError as e:
            print()
            log_error(f"HTTP 错误 {e.code}: {e.reason}")
            if e.code == 404:
                log_error("文件不存在，请检查 Release Asset 是否已上传")
                return False  # 404 不需要重试
            elif e.code == 403:
                log_error("访问被拒绝，可能是 GitHub 速率限制")
        except urllib.error.URLError as e:
            print()
            log_error(f"URL 错误: {e.reason}")
        except Exception as e:
            print()
            log_error(f"下载错误: {type(e).__name__}: {e}")
            import traceback
            log_debug(traceback.format_exc())
        
        # 清理临时文件
        temp_path = dest_path.parent / f".downloading_{dest_path.name}"
        if temp_path.exists():
            try:
                temp_path.unlink()
            except:
                pass
    
    return False

def _iter_project_backup_items(project_dir: Path, backup_dir: Optional[Path] = None) -> list[Path]:
    items = []
    backup_name = backup_dir.name if backup_dir else ""
    for item in project_dir.iterdir():
        name = item.name
        if name in BACKUP_EXCLUDE_NAMES:
            continue
        if backup_name and name == backup_name:
            continue
        if name.startswith("backup_"):
            continue
        items.append(item)
    return items

def backup_current(project_dir: Path) -> Optional[Path]:
    """备份当前项目快照，便于更新失败后回滚。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = project_dir / f"backup_{timestamp}"
    
    try:
        backup_dir.mkdir(exist_ok=True)
        backup_items = _iter_project_backup_items(project_dir, backup_dir)
        
        count = 0
        for src in backup_items:
            if src.exists():
                dst = backup_dir / src.name
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                count += 1
        
        if count > 0:
            log_success(f"已备份 {count} 项到: {backup_dir.name}")
            return backup_dir
        else:
            backup_dir.rmdir()
            return None
    except Exception as e:
        log_warning(f"备份失败: {e}")
        return None

def restore_from_backup(project_dir: Path, backup_dir: Optional[Path]) -> bool:
    """从备份目录恢复项目快照。"""
    if not backup_dir or not backup_dir.exists():
        log_error("没有可用备份，无法回滚")
        return False

    try:
        restore_items = [item for item in backup_dir.iterdir()]
        restored = 0
        for src in restore_items:
            if not src.exists():
                continue

            dst = project_dir / src.name
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()

            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            restored += 1

        if restored <= 0:
            log_error(f"备份目录中没有可恢复项目: {backup_dir}")
            return False

        log_success(f"已从备份回滚 {restored} 项: {backup_dir.name}")
        return True
    except Exception as e:
        log_error(f"回滚失败: {e}")
        return False

def should_preserve(path: Path, preserve_patterns: list) -> bool:
    """检查是否应保留"""
    path_str = str(path).replace("\\", "/")
    name = path.name
    
    for pattern in preserve_patterns:
        if pattern.startswith('*'):
            if name.endswith(pattern[1:]):
                return True
        elif pattern.endswith('*'):
            if name.startswith(pattern[:-1]):
                return True
        elif pattern in path_str or name == pattern:
            return True
    
    return False

def load_commands_config(path: Path) -> list:
    """加载命令配置，兼容 list 和 {commands: [...]} 两种格式"""
    if not path.exists():
        return []

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if isinstance(data, dict):
            data = data.get("commands", [])

        if isinstance(data, list):
            return [cmd for cmd in data if isinstance(cmd, dict)]

        raise ValueError(f"命令配置格式无效: {path}")
    except Exception as e:
        raise RuntimeError(f"加载命令配置失败: {path} ({e})") from e

def load_sites_config(path: Path) -> dict:
    """加载站点配置。解析失败时中断合并，避免误覆盖本地数据。"""
    if not path.exists():
        return {}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        raise ValueError(f"站点配置格式无效: {path}")
    except Exception as e:
        raise RuntimeError(f"加载站点配置失败: {path} ({e})") from e

def merge_site_records(existing: dict, incoming: dict) -> dict:
    """递归合并站点配置，优先保留用户本地值。"""
    if not isinstance(incoming, dict):
        return dict(existing) if isinstance(existing, dict) else {}
    if not isinstance(existing, dict):
        return dict(incoming)

    merged = dict(incoming)
    for key, existing_value in existing.items():
        incoming_value = merged.get(key)
        if isinstance(existing_value, dict) and isinstance(incoming_value, dict):
            merged[key] = merge_site_records(existing_value, incoming_value)
        else:
            merged[key] = existing_value
    return merged

def merge_sites(existing: dict, incoming: dict) -> tuple[dict, dict]:
    """
    合并站点配置。

    - 相同站点：优先保留本地配置，并补入发布版新增字段
    - 本地独有站点：保留
    - 发布版独有站点：新增
    """
    existing_sites = existing if isinstance(existing, dict) else {}
    incoming_sites = incoming if isinstance(incoming, dict) else {}

    merged = {}
    updated = 0
    added = 0
    preserved = 0

    incoming_keys = list(incoming_sites.keys())
    for key in incoming_keys:
        incoming_value = incoming_sites.get(key)
        if key in existing_sites:
            merged[key] = merge_site_records(existing_sites[key], incoming_value)
            updated += 1
        else:
            merged[key] = incoming_value
            added += 1

    for key, existing_value in existing_sites.items():
        if key in merged:
            continue
        merged[key] = existing_value
        preserved += 1

    return merged, {
        "updated": updated,
        "added": added,
        "preserved": preserved,
    }

def merge_sites_file(src_path: Path, dst_path: Path):
    """合并站点配置文件，优先保留用户已有站点配置。"""
    incoming = load_sites_config(src_path)
    existing = load_sites_config(dst_path)
    merged, stats = merge_sites(existing, incoming)
    had_existing = dst_path.exists()

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, 'w', encoding='utf-8') as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    if had_existing:
        log_info(
            "站点配置已合并: "
            f"覆盖 {stats['updated']} 个已有站点，"
            f"新增 {stats['added']} 个发布站点，"
            f"保留 {stats['preserved']} 个本地站点"
        )
    else:
        log_info(f"站点配置已写入: {len(merged)} 项")

def _same_command(left: dict, right: dict) -> bool:
    """判断两条命令是否代表同一个内置命令。"""
    left_id = str(left.get("id", "")).strip()
    right_id = str(right.get("id", "")).strip()
    if left_id and right_id:
        return left_id == right_id

    left_name = str(left.get("name", "")).strip()
    right_name = str(right.get("name", "")).strip()
    return bool(left_name and right_name and left_name == right_name)

def _merge_command_record(existing: dict, incoming: dict) -> dict:
    """以发布版命令为准，保留本地运行状态与分组状态。"""
    merged = dict(incoming)
    for field in COMMAND_PRESERVE_FIELDS:
        if field in existing:
            merged[field] = existing[field]
    return merged

def merge_commands(existing: list, incoming: list) -> tuple[list, dict]:
    """
    合并命令配置。

    - 发布版内置命令：用新版本覆盖旧版本
    - 本地自定义命令：若发布版中不存在，则保留
    """
    existing_commands = [cmd for cmd in (existing or []) if isinstance(cmd, dict)]
    incoming_commands = [cmd for cmd in (incoming or []) if isinstance(cmd, dict)]

    merged = []
    matched_existing = set()
    updated = 0
    added = 0
    preserved = 0

    for incoming_cmd in incoming_commands:
        match_index = None
        for index, existing_cmd in enumerate(existing_commands):
            if index in matched_existing:
                continue
            if _same_command(existing_cmd, incoming_cmd):
                match_index = index
                break

        if match_index is None:
            merged.append(dict(incoming_cmd))
            added += 1
            continue

        merged.append(_merge_command_record(existing_commands[match_index], incoming_cmd))
        matched_existing.add(match_index)
        updated += 1

    for index, existing_cmd in enumerate(existing_commands):
        if index in matched_existing:
            continue
        merged.append(dict(existing_cmd))
        preserved += 1

    return merged, {
        "updated": updated,
        "added": added,
        "preserved": preserved,
    }

def merge_command_file(src_path: Path, dst_path: Path):
    """合并命令配置文件，更新内置命令并保留用户自定义命令"""
    incoming = load_commands_config(src_path)
    existing = load_commands_config(dst_path)
    merged, stats = merge_commands(existing, incoming)
    had_existing = dst_path.exists()

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, 'w', encoding='utf-8') as f:
        json.dump({"commands": merged}, f, indent=2, ensure_ascii=False)

    if had_existing:
        log_info(
            "命令配置已合并: "
            f"更新 {stats['updated']} 条内置命令，"
            f"新增 {stats['added']} 条发布命令，"
            f"保留 {stats['preserved']} 条本地命令"
        )
    else:
        log_info(f"命令配置已写入: {len(merged)} 条")

def extract_and_update(zip_path: Path, project_dir: Path, preserve: list) -> bool:
    """解压并更新"""
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            log_info("解压更新包...")
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(temp_path)
            
            # 找根目录
            extracted = list(temp_path.iterdir())
            log_debug(f"解压内容: {[e.name for e in extracted]}")
            
            if len(extracted) == 1 and extracted[0].is_dir():
                source_dir = extracted[0]
                log_info(f"使用目录: {source_dir.name}")
            else:
                source_dir = temp_path
            
            log_info("应用更新...")
            updated = 0
            skipped = 0
            
            for src_item in source_dir.rglob('*'):
                if src_item.is_dir():
                    continue
                
                rel_path = src_item.relative_to(source_dir)
                dst_item = project_dir / rel_path

                if should_preserve(rel_path, preserve):
                    skipped += 1
                    continue

                if rel_path == SITES_CONFIG_PATH:
                    merge_sites_file(src_item, dst_item)
                    updated += 1
                    continue

                if rel_path == COMMANDS_CONFIG_PATH:
                    merge_command_file(src_item, dst_item)
                    updated += 1
                    continue
                
                dst_item.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_item, dst_item)
                updated += 1
            
            log_success(f"更新 {updated} 个文件, 保留 {skipped} 个")
            return True
            
    except Exception as e:
        log_error(f"更新失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def update_version_file(project_dir: Path, new_version: str):
    """更新版本文件"""
    normalized = normalize_version(new_version)
    version_file = project_dir / "VERSION"
    version_file.write_text(normalized)
    log_success(f"版本已更新: {normalized}")

def check_and_update(repo: str = None, force: bool = False, preserve: list = None) -> bool:
    """检查并执行更新"""
    project_dir = Path(__file__).parent.resolve()
    
    if repo is None:
        repo = os.getenv('GITHUB_REPO', DEFAULT_REPO)
    
    if preserve is None:
        preserve_str = os.getenv('UPDATE_PRESERVE', '')
        if preserve_str:
            preserve = build_effective_preserve_patterns([p.strip() for p in preserve_str.split(',')])
        else:
            preserve = build_effective_preserve_patterns(
                load_update_preserve_settings().get("selected_patterns", DEFAULT_PRESERVE.copy())
            )
    
    print()
    print("=" * 55)
    print("  Universal Web-to-API Auto Updater v2.1")
    print("=" * 55)
    print()
    
    current_version = get_current_version()
    log_info(f"当前版本: v{current_version}")
    log_info(f"检查仓库: {repo}")
    
    release = fetch_latest_release(repo)
    
    if not release:
        log_warning("无法获取版本信息")
        print()
        return False
    
    latest_raw = release.get('tag_name', 'v0.0.0')
    latest_version = normalize_version(latest_raw)
    log_info(f"最新版本: v{latest_version} (tag: {latest_raw})")
    
    if not force and compare_versions(current_version, latest_version) >= 0:
        log_success("已是最新版本")
        print()
        return False
    
    print()
    print(colored(f"  >>> 发现新版本: v{current_version} -> v{latest_version}", Colors.YELLOW))
    print()
    
    # 显示更新说明
    notes = release.get('body', '')
    if notes:
        print("  [更新说明]")
        for line in notes.split('\n')[:10]:
            print(f"    {line}")
        print()
    
    # 获取下载链接
    download_url, source_type = get_download_url_from_release(release, repo)
    
    if not download_url:
        log_error("无法获取下载链接")
        return False
    
    print()
    log_info(f"下载来源: {source_type}")
    log_info("开始下载...")
    print()
    
    # 创建临时目录用于下载
    download_dir = project_dir / ".update_temp"
    download_dir.mkdir(exist_ok=True)
    
    zip_path = download_dir / "update.zip"
    
    backup_dir = None
    try:
        if not download_file_robust(download_url, zip_path):
            log_error("下载失败")
            return False
        
        print()
        backup_dir = backup_current(project_dir)
        if backup_dir is None:
            log_error("更新前备份失败，已中止更新")
            return False
        
        if not extract_and_update(zip_path, project_dir, preserve):
            restore_from_backup(project_dir, backup_dir)
            return False
        
        update_version_file(project_dir, latest_version)
        
        print()
        print("=" * 55)
        print(colored(f"  ✓ 已成功更新到 v{latest_version}", Colors.GREEN))
        print("=" * 55)
        print()
        
        return True
    except Exception:
        if backup_dir is not None:
            restore_from_backup(project_dir, backup_dir)
        raise
        
    finally:
        # 清理临时目录
        if download_dir.exists():
            try:
                shutil.rmtree(download_dir)
            except:
                pass

def fetch_all_releases(repo: str, per_page: int = 30) -> list:
    """获取所有 Release 列表"""
    api_url = f"{GITHUB_API_BASE}/{repo}/releases?per_page={per_page}"
    log_info(f"请求 GitHub Release 列表: {api_url}")
    data = http_request_with_retry(
        api_url,
        headers={'Accept': 'application/vnd.github.v3+json'}
    )
    if data:
        try:
            releases = json.loads(data.decode('utf-8'))
            if isinstance(releases, list):
                return releases
        except json.JSONDecodeError as e:
            log_error(f"JSON 解析失败: {e}")
    return []


def fetch_release_by_tag(repo: str, tag: str) -> Optional[dict]:
    """按 tag 名称获取特定 Release 信息"""
    api_url = f"{GITHUB_API_BASE}/{repo}/releases/tags/{tag}"
    log_info(f"请求 Release by tag: {api_url}")
    data = http_request_with_retry(
        api_url,
        headers={'Accept': 'application/vnd.github.v3+json'}
    )
    if data:
        try:
            return json.loads(data.decode('utf-8'))
        except json.JSONDecodeError as e:
            log_error(f"JSON 解析失败: {e}")
    return None


def update_to_version(tag: str, repo: str = None, preserve: list = None) -> bool:
    """切换到指定版本（通过 tag 名称）"""
    project_dir = Path(__file__).parent.resolve()

    if repo is None:
        repo = os.getenv('GITHUB_REPO', DEFAULT_REPO)

    if preserve is None:
        preserve_str = os.getenv('UPDATE_PRESERVE', '')
        if preserve_str:
            preserve = build_effective_preserve_patterns(
                [p.strip() for p in preserve_str.split(',')]
            )
        else:
            preserve = build_effective_preserve_patterns(
                load_update_preserve_settings().get("selected_patterns", DEFAULT_PRESERVE.copy())
            )

    print()
    print("=" * 55)
    print(f"  切换到版本: {tag}")
    print("=" * 55)
    print()

    current_version = get_current_version()
    log_info(f"当前版本: v{current_version}")
    log_info(f"目标版本: {tag}")

    release = fetch_release_by_tag(repo, tag)
    if not release:
        log_error(f"无法获取版本 {tag} 的 Release 信息")
        return False

    download_url, source_type = get_download_url_from_release(release, repo)
    if not download_url:
        log_error("无法获取下载链接")
        return False

    log_info(f"下载来源: {source_type}")
    log_info("开始下载...")
    print()

    download_dir = project_dir / ".update_temp"
    download_dir.mkdir(exist_ok=True)
    zip_path = download_dir / "update.zip"

    backup_dir = None
    try:
        if not download_file_robust(download_url, zip_path):
            log_error("下载失败")
            return False

        print()
        backup_dir = backup_current(project_dir)
        if backup_dir is None:
            log_error("切换版本前备份失败，已中止操作")
            return False

        if not extract_and_update(zip_path, project_dir, preserve):
            restore_from_backup(project_dir, backup_dir)
            return False

        # 写入目标版本号
        target_version = normalize_version(tag)
        update_version_file(project_dir, target_version)

        print()
        print("=" * 55)
        print(colored(f"  ✓ 已成功切换到 {tag}", Colors.GREEN))
        print("=" * 55)
        print()

        return True
    except Exception:
        if backup_dir is not None:
            restore_from_backup(project_dir, backup_dir)
        raise

    finally:
        if download_dir.exists():
            try:
                shutil.rmtree(download_dir)
            except Exception:
                pass


def main():

    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='自动更新工具 v2.1')
    parser.add_argument('--force', '-f', action='store_true', help='强制更新')
    parser.add_argument('--repo', '-r', type=str, help='GitHub 仓库')
    parser.add_argument('--check-only', '-c', action='store_true', help='仅检查')
    parser.add_argument('--version', '-v', action='store_true', help='显示版本')
    parser.add_argument('--debug', '-d', action='store_true', help='调试模式')
    
    args = parser.parse_args()
    
    if args.debug:
        os.environ['DEBUG'] = '1'
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    
    if args.version:
        print(f"v{get_current_version()}")
        sys.exit(0)
    
    if args.check_only:
        current = get_current_version()
        release = fetch_latest_release(args.repo or os.getenv('GITHUB_REPO', DEFAULT_REPO))
        
        if release:
            latest = normalize_version(release.get('tag_name', '0'))
            
            print(f"\n[Release 信息]")
            print(f"  Tag: {release.get('tag_name')}")
            assets = release.get('assets', [])
            print(f"  Assets: {len(assets)} 个")
            for asset in assets:
                size_kb = asset.get('size', 0) / 1024
                print(f"    - {asset.get('name')} ({size_kb:.1f} KB)")
            print()
            
            if compare_versions(current, latest) < 0:
                print(f"可更新: v{current} -> v{latest}")
                sys.exit(1)
            else:
                print(f"已是最新: v{current}")
                sys.exit(0)
        else:
            print("检查失败")
            sys.exit(2)
    
    updated = check_and_update(repo=args.repo, force=args.force)
    sys.exit(0 if updated else 1)

if __name__ == '__main__':
    main()
