"""
app/core/background_image_downloader.py

线程安全的后台图片下载器：
- 复用主线程提取的 cookies / headers 发起异步下载
- 直接落盘到 download_images/
- 维护 原始 URL -> 本地文件 元数据缓存
"""

import mimetypes
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse, urlsplit, urlunsplit

import requests

from app.core.config import logger


DEFAULT_IMAGE_ACCEPT = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
REMOTE_IMAGE_URL_TRAILING_WRAPPERS = ")]}"
REMOTE_IMAGE_URL_TRAILING_SENTENCE_PUNCTUATION = ".,;:!?"
REMOTE_IMAGE_URL_PATH_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".avif")

_IMAGE_CONTENT_TYPE_EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
}

_PENDING_DOWNLOAD_STATUSES = {"queued", "downloading"}


def _strip_remote_image_url_trailing_punctuation(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""

    suffix_chars = REMOTE_IMAGE_URL_TRAILING_WRAPPERS + REMOTE_IMAGE_URL_TRAILING_SENTENCE_PUNCTUATION
    suffix_start = len(text)
    while suffix_start > 0 and text[suffix_start - 1] in suffix_chars:
        suffix_start -= 1

    if suffix_start == len(text):
        return text

    candidate = text[:suffix_start].rstrip()
    suffix = text[suffix_start:]
    if not candidate:
        return text

    if all(ch in REMOTE_IMAGE_URL_TRAILING_WRAPPERS for ch in suffix):
        return candidate

    try:
        parsed = urlsplit(candidate)
    except Exception:
        return text
    if parsed.query or parsed.fragment:
        return text
    if parsed.path.lower().endswith(REMOTE_IMAGE_URL_PATH_EXTENSIONS):
        return candidate
    return text


def normalize_remote_image_url(url: str) -> str:
    normalized = _strip_remote_image_url_trailing_punctuation(url)
    if not normalized:
        return ""
    try:
        parsed = urlsplit(normalized)
    except Exception:
        return ""
    scheme = str(parsed.scheme or "").lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    netloc = parsed.netloc.lower()
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def extract_tab_cookies(tab) -> Dict[str, str]:
    cookies_dict: Dict[str, str] = {}
    if tab is None:
        return cookies_dict

    try:
        cookies_list = tab.cookies()
    except Exception as exc:
        logger.debug(f"后台图片下载读取 cookies 失败（忽略）: {exc}")
        return cookies_dict

    if not cookies_list:
        return cookies_dict

    for cookie in cookies_list:
        if isinstance(cookie, dict) and "name" in cookie and "value" in cookie:
            cookies_dict[str(cookie["name"])] = str(cookie["value"])
    return cookies_dict


def build_image_request_headers(tab, accept: str = DEFAULT_IMAGE_ACCEPT) -> Dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": str(getattr(tab, "url", "") or "") if tab is not None else "",
        "Accept": str(accept or DEFAULT_IMAGE_ACCEPT),
    }


def build_image_download_request_context(
    tab,
    accept: str = DEFAULT_IMAGE_ACCEPT,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    return extract_tab_cookies(tab), build_image_request_headers(tab, accept=accept)


class BackgroundImageDownloader:
    """线程安全的后台图片下载器。"""

    def __init__(
        self,
        save_dir: str | Path = "download_images",
        *,
        max_workers: int = 4,
        min_bytes: int = 1000,
        max_bytes: int = 10 * 1024 * 1024,
        max_entries: int = 1000,
        max_pending: int = 100,
    ):
        self._save_dir = Path(save_dir)
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers or 1)),
            thread_name_prefix="bg-image",
        )
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._min_bytes = max(1, int(min_bytes or 1))
        self._max_bytes = max(self._min_bytes, int(max_bytes or (10 * 1024 * 1024)))
        self._max_entries = max(1, int(max_entries or 1))
        self._max_pending = max(1, int(max_pending or 1))
        self._pending_count = 0
        self._shutdown = False

    def start_download(
        self,
        url: str,
        *,
        cookies: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        normalized = normalize_remote_image_url(url)
        if not normalized:
            return {}

        with self._lock:
            if self._shutdown:
                return {}

            entry = self._entries.get(normalized)
            if entry is not None:
                self._entries.move_to_end(normalized)

            if entry and str(entry.get("status") or "") == "done":
                local_path = Path(str(entry.get("local_path") or ""))
                if local_path.exists():
                    return self._snapshot_entry(entry)
                entry = None

            if entry and str(entry.get("status") or "") in {"queued", "downloading"}:
                return self._snapshot_entry(entry)

            if self._pending_count >= self._max_pending:
                logger.warning(
                    f"后台图片下载队列已达上限，跳过预取: pending={self._pending_count}, limit={self._max_pending}"
                )
                return {}

            entry = {
                "url": normalized,
                "local_path": None,
                "accessible_url": None,
                "mime": None,
                "byte_size": None,
                "error": None,
                "started_at": time.time(),
                "updated_at": time.time(),
                "_event": threading.Event(),
            }
            self._set_entry_status_locked(entry, "queued")
            self._entries[normalized] = entry
            self._entries.move_to_end(normalized)
            self._prune_entries_locked()
            try:
                entry["_future"] = self._executor.submit(
                    self._download_worker,
                    normalized,
                    dict(cookies or {}),
                    dict(headers or {}),
                )
            except RuntimeError:
                self._set_entry_status_locked(entry, "failed")
                entry["error"] = "downloader_shutdown"
                event = entry.get("_event")
                if isinstance(event, threading.Event):
                    event.set()
                return {}
            return self._snapshot_entry(entry)

    def get_download_result(
        self,
        url: str,
        *,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized = normalize_remote_image_url(url)
        if not normalized:
            return None

        with self._lock:
            entry = self._entries.get(normalized)
            if entry is None:
                return None
            self._entries.move_to_end(normalized)
            event = entry.get("_event")
            status = str(entry.get("status") or "")
            event_pending = isinstance(event, threading.Event) and not event.is_set()

        if wait and isinstance(event, threading.Event) and (
            status in {"queued", "downloading"} or event_pending
        ):
            try:
                event.wait(None if timeout is None else max(0.0, float(timeout)))
            except Exception:
                pass

        with self._lock:
            latest = self._entries.get(normalized)
            if latest is None:
                return None
            self._entries.move_to_end(normalized)
            return self._snapshot_entry(latest)

    def register_downloaded_file(
        self,
        url: str,
        *,
        local_path: str | Path,
        accessible_url: Optional[str] = None,
        mime: Optional[str] = None,
        byte_size: Optional[int] = None,
        source: str = "local_file",
    ) -> Optional[Dict[str, Any]]:
        normalized = normalize_remote_image_url(url)
        if not normalized:
            return None

        path_obj = Path(local_path)
        if not path_obj.exists():
            return None

        if accessible_url is None:
            accessible_url = f"/download_images/{path_obj.name}"
        if mime is None:
            mime = self._guess_mime_type(path_obj)
        if byte_size is None:
            try:
                byte_size = int(path_obj.stat().st_size)
            except Exception:
                byte_size = None

        with self._lock:
            entry = self._entries.get(normalized) or {
                "url": normalized,
                "_event": threading.Event(),
                "started_at": time.time(),
            }
            entry.update({
                "local_path": str(path_obj),
                "accessible_url": str(accessible_url or ""),
                "mime": mime,
                "byte_size": byte_size,
                "error": None,
                "source": str(source or "local_file"),
                "updated_at": time.time(),
            })
            self._set_entry_status_locked(entry, "done")
            event = entry.get("_event")
            if not isinstance(event, threading.Event):
                event = threading.Event()
                entry["_event"] = event
            event.set()
            self._entries[normalized] = entry
            self._entries.move_to_end(normalized)
            self._prune_entries_locked()
            return self._snapshot_entry(entry)

    def _download_worker(
        self,
        url: str,
        cookies: Dict[str, str],
        headers: Dict[str, str],
    ) -> None:
        if self._is_shutdown():
            return

        response = None
        temp_path: Optional[Path] = None
        final_path: Optional[Path] = None

        self._update_entry(url, status="downloading", error=None)

        def _close_response() -> None:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

        try:
            self._save_dir.mkdir(parents=True, exist_ok=True)
            response = requests.get(
                url,
                cookies=cookies or None,
                headers=headers or None,
                timeout=20,
                allow_redirects=True,
                stream=True,
            )
            if response.status_code != 200:
                raise ValueError(f"http_{response.status_code}")

            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type and "image" not in content_type:
                raise ValueError(f"invalid_content_type:{content_type}")
            content_length = str(response.headers.get("Content-Length") or "").strip()
            if content_length:
                try:
                    expected_size = int(content_length)
                except ValueError:
                    expected_size = 0
                if expected_size > self._max_bytes:
                    raise ValueError(f"image_too_large:{expected_size}")

            ext = self._pick_extension(content_type, url)
            filename = f"{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
            temp_path = self._save_dir / f"{filename}.part"
            final_path = self._save_dir / filename

            written = 0
            with temp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if self._is_shutdown():
                        raise RuntimeError("downloader_shutdown")
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > self._max_bytes:
                        raise ValueError(f"image_too_large:{written}")
                    handle.write(chunk)

            if self._is_shutdown():
                raise RuntimeError("downloader_shutdown")

            if written < self._min_bytes:
                raise ValueError(f"image_too_small:{written}")

            temp_path.replace(final_path)
            accessible_url = f"/download_images/{filename}"
            _close_response()
            self._update_entry(
                url,
                status="done",
                local_path=str(final_path),
                accessible_url=accessible_url,
                mime=content_type or self._guess_mime_type(final_path),
                byte_size=written,
                error=None,
                source="background_download",
            )
            if not self._is_shutdown():
                logger.debug(f"后台图片下载完成: {filename} ({written} bytes)")
        except Exception as exc:
            try:
                if temp_path is not None and temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
            _close_response()
            self._update_entry(url, status="failed", error=str(exc))
            if not self._is_shutdown():
                logger.debug(f"后台图片下载失败（忽略）: {str(exc)[:160]}")
        finally:
            _close_response()

    def _update_entry(self, url: str, **changes: Any) -> None:
        normalized = normalize_remote_image_url(url)
        if not normalized:
            return

        with self._lock:
            entry = self._entries.get(normalized)
            if self._shutdown and entry is None:
                return
            if self._shutdown and str(changes.get("status") or "") not in {"failed"}:
                return
            if entry is None:
                entry = {
                    "url": normalized,
                    "_event": threading.Event(),
                    "started_at": time.time(),
                }
                self._entries[normalized] = entry

            next_status = changes.get("status") if "status" in changes else None
            if "status" in changes:
                changes = {key: value for key, value in changes.items() if key != "status"}
            entry.update(changes)
            if next_status is not None:
                self._set_entry_status_locked(entry, next_status)
            entry["updated_at"] = time.time()
            self._entries.move_to_end(normalized)

            event = entry.get("_event")
            if not isinstance(event, threading.Event):
                event = threading.Event()
                entry["_event"] = event

            status = str(entry.get("status") or "")
            if status in {"done", "failed"}:
                event.set()
                self._prune_entries_locked()

    def _set_entry_status_locked(self, entry: Dict[str, Any], status: Any) -> None:
        old_status = str((entry or {}).get("status") or "")
        next_status = str(status or "")
        if old_status != next_status:
            old_pending = old_status in _PENDING_DOWNLOAD_STATUSES
            next_pending = next_status in _PENDING_DOWNLOAD_STATUSES
            if old_pending and not next_pending:
                self._pending_count = max(0, self._pending_count - 1)
            elif next_pending and not old_pending:
                self._pending_count += 1
        entry["status"] = next_status

    def _prune_entries_locked(self) -> None:
        overflow = len(self._entries) - self._max_entries
        if overflow <= 0:
            return

        keys_to_remove = []
        entries_iter = iter(self._entries.items())
        while len(keys_to_remove) < overflow:
            try:
                key, entry = next(entries_iter)
            except StopIteration:
                break
            status = str((entry or {}).get("status") or "")
            if status in {"queued", "downloading"}:
                continue
            keys_to_remove.append(key)

        for key in keys_to_remove:
            self._entries.pop(key, None)

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            for entry in self._entries.values():
                status = str((entry or {}).get("status") or "")
                if status not in {"queued", "downloading"}:
                    continue
                self._set_entry_status_locked(entry, "failed")
                entry["error"] = "downloader_shutdown"
                entry["updated_at"] = time.time()
                event = entry.get("_event")
                if isinstance(event, threading.Event):
                    event.set()

        self._executor.shutdown(wait=False, cancel_futures=True)

    def _is_shutdown(self) -> bool:
        with self._lock:
            return bool(self._shutdown)

    def _snapshot_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(entry, dict):
            return {}
        return {
            key: value
            for key, value in entry.items()
            if not str(key).startswith("_")
        }

    @staticmethod
    def _guess_mime_type(path_obj: Path) -> Optional[str]:
        try:
            guessed, _ = mimetypes.guess_type(path_obj.name)
            return guessed
        except Exception:
            return None

    @staticmethod
    def _pick_extension(content_type: str, url: str) -> str:
        ext = _IMAGE_CONTENT_TYPE_EXT_MAP.get(str(content_type or "").lower())
        if ext:
            return ext

        path_ext = Path(urlparse(url).path or "").suffix.lower()
        if path_ext:
            return path_ext

        return ".png"


background_image_downloader = BackgroundImageDownloader()


__all__ = [
    "BackgroundImageDownloader",
    "DEFAULT_IMAGE_ACCEPT",
    "background_image_downloader",
    "build_image_download_request_context",
    "build_image_request_headers",
    "extract_tab_cookies",
    "normalize_remote_image_url",
]
