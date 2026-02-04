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
DEFAULT_PRESERVE = [
    "chrome_profile",
    "venv", 
    "logs",
    "image",
    "__pycache__",
    ".git",
    "*.pyc",
    "backup_*",
    "updater.py"
]

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
    """创建宽松的 SSL 上下文"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def get_opener():
    """创建支持重定向的 opener"""
    # 创建支持 SSL 的 handler
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
            
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return response.read()
            except ssl.SSLError:
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
            
            # 使用自定义 opener 处理 SSL 和重定向
            ctx = create_ssl_context()
            
            # 打开连接
            response = urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT, context=ctx)
            
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

def backup_current(project_dir: Path) -> Optional[Path]:
    """备份当前配置"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = project_dir / f"backup_{timestamp}"
    
    try:
        backup_items = ['.env', 'config', 'VERSION']
        backup_dir.mkdir(exist_ok=True)
        
        count = 0
        for item in backup_items:
            src = project_dir / item
            if src.exists():
                dst = backup_dir / item
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

def should_preserve(path: Path, preserve_patterns: list) -> bool:
    """检查是否应保留"""
    path_str = str(path)
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
            preserve = [p.strip() for p in preserve_str.split(',')]
        else:
            preserve = DEFAULT_PRESERVE.copy()
    
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
    
    try:
        if not download_file_robust(download_url, zip_path):
            log_error("下载失败")
            return False
        
        print()
        backup_current(project_dir)
        
        if not extract_and_update(zip_path, project_dir, preserve):
            return False
        
        update_version_file(project_dir, latest_version)
        
        print()
        print("=" * 55)
        print(colored(f"  ✓ 已成功更新到 v{latest_version}", Colors.GREEN))
        print("=" * 55)
        print()
        
        return True
        
    finally:
        # 清理临时目录
        if download_dir.exists():
            try:
                shutil.rmtree(download_dir)
            except:
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