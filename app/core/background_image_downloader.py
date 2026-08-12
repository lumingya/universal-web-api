"""
app/core/background_image_downloader.py

线程安全的后台图片下载器：
- 复用主线程提取的 cookies / headers 发起异步下载
- 直接落盘到 download_images/
- 维护 原始 URL -> 本地文件 元数据缓存
"""

import hashlib
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
from app.utils.remote_resource import (
    get_public_remote_resource,
    normalize_remote_http_url,
    remote_url_origin,
)


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
    "image/avif": ".avif",
}

_SAFE_IMAGE_EXTENSIONS = frozenset(_IMAGE_CONTENT_TYPE_EXT_MAP.values())

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
    return normalize_remote_http_url(normalized)


def extract_tab_cookies(tab):
    cookie_jar = requests.cookies.RequestsCookieJar()
    if tab is None:
        return cookie_jar

    source_url = str(getattr(tab, "url", "") or "")
    source_host = str(urlsplit(source_url).hostname or "").strip().lower().rstrip(".")

    try:
        cookies_list = tab.cookies()
    except Exception as exc:
        logger.debug(f"后台图片下载读取 cookies 失败（忽略）: {exc}")
        return cookie_jar

    if not cookies_list:
        return cookie_jar

    for cookie in cookies_list:
        if isinstance(cookie, dict) and "name" in cookie and "value" in cookie:
            domain = str(cookie.get("domain") or source_host).strip().lower()
            path = str(cookie.get("path") or "/").strip() or "/"
            if not domain:
                continue
            cookie_jar.set(
                str(cookie["name"]),
                str(cookie["value"]),
                domain=domain,
                path=path,
                secure=bool(cookie.get("secure", False)),
            )
    return cookie_jar


def build_image_request_headers(tab, accept: str = DEFAULT_IMAGE_ACCEPT) -> Dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": str(getattr(tab, "url", "") or "") if tab is not None else "",
        "Accept": str(accept or DEFAULT_IMAGE_ACCEPT),
    }


def build_image_download_request_context(
    tab,
    accept: str = DEFAULT_IMAGE_ACCEPT,
) -> Tuple[Any, Dict[str, str]]:
    return extract_tab_cookies(tab), build_image_request_headers(tab, accept=accept)


def build_image_download_partition(cookies: Any, headers: Optional[Dict[str, str]] = None) -> str:
    source_origin = remote_url_origin((headers or {}).get("Referer")) or "no-origin"
    cookie_parts = []
    try:
        for cookie in cookies or []:
            cookie_parts.append(
                "\x1f".join((
                    str(getattr(cookie, "domain", "") or ""),
                    str(getattr(cookie, "path", "") or "/"),
                    str(getattr(cookie, "name", "") or ""),
                    str(getattr(cookie, "value", "") or ""),
                ))
            )
    except Exception:
        cookie_parts = [repr(cookies)]
    if source_origin == "no-origin" and not cookie_parts:
        return "public"
    material = source_origin + "\x1e" + "\x1e".join(sorted(cookie_parts))
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:24]


def get_image_download_partition(
    url: Any,
    cookies: Any,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    """Use one cache entry for signed public object-store URLs across browser views."""
    normalized = normalize_remote_image_url(url)
    try:
        parsed = urlsplit(normalized)
        host = str(parsed.hostname or "").lower().rstrip(".")
        query = str(parsed.query or "").lower()
    except Exception:
        host = ""
        query = ""
    if host.endswith(".r2.cloudflarestorage.com") and "x-amz-signature=" in query:
        return "signed-public"
    return build_image_download_partition(cookies, headers)


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
        max_age_seconds: float = 24 * 60 * 60,
        max_total_bytes: int = 1024 * 1024 * 1024,
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
        self._max_age_seconds = max(60.0, float(max_age_seconds or (24 * 60 * 60)))
        self._max_total_bytes = max(self._max_bytes, int(max_total_bytes or (1024 * 1024 * 1024)))
        self._pending_count = 0
        self._shutdown = False
        self._last_disk_cleanup_at = 0.0

    def start_download(
        self,
        url: str,
        *,
        cookies: Any = None,
        headers: Optional[Dict[str, str]] = None,
        partition_key: str = "",
        max_bytes: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized = normalize_remote_image_url(url)
        if not normalized:
            return {}
        partition = str(
            partition_key or get_image_download_partition(normalized, cookies, headers)
        ).strip() or "public"
        cache_key = self._cache_key(normalized, partition)
        effective_max_bytes = self._max_bytes
        if max_bytes is not None:
            try:
                effective_max_bytes = max(self._min_bytes, int(max_bytes))
            except (TypeError, ValueError):
                effective_max_bytes = self._max_bytes

        with self._lock:
            if self._shutdown:
                return {}

            self._cleanup_disk_if_due_locked()

            entry = self._entries.get(cache_key)
            if entry is not None:
                self._entries.move_to_end(cache_key)

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
                "partition_key": partition,
                "local_path": None,
                "accessible_url": None,
                "mime": None,
                "byte_size": None,
                "error": None,
                "max_bytes": effective_max_bytes,
                "started_at": time.time(),
                "updated_at": time.time(),
                "_event": threading.Event(),
            }
            self._set_entry_status_locked(entry, "queued")
            self._entries[cache_key] = entry
            self._entries.move_to_end(cache_key)
            self._prune_entries_locked()
            try:
                entry["_future"] = self._executor.submit(
                    self._download_worker,
                    cache_key,
                    normalized,
                    cookies,
                    dict(headers or {}),
                    effective_max_bytes,
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
        partition_key: str = "",
    ) -> Optional[Dict[str, Any]]:
        normalized = normalize_remote_image_url(url)
        if not normalized:
            return None
        cache_key = self._cache_key(normalized, str(partition_key or "public"))

        with self._lock:
            entry = self._entries.get(cache_key)
            if entry is None:
                return None
            self._entries.move_to_end(cache_key)
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
            latest = self._entries.get(cache_key)
            if latest is None:
                return None
            self._entries.move_to_end(cache_key)
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
        partition_key: str = "",
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

        partition = str(partition_key or "public").strip() or "public"
        cache_key = self._cache_key(normalized, partition)

        with self._lock:
            entry = self._entries.get(cache_key) or {
                "url": normalized,
                "partition_key": partition,
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
            self._entries[cache_key] = entry
            self._entries.move_to_end(cache_key)
            self._prune_entries_locked()
            return self._snapshot_entry(entry)

    def _download_worker(
        self,
        cache_key: str,
        url: str,
        cookies: Any,
        headers: Dict[str, str],
        max_bytes: int,
    ) -> None:
        if self._is_shutdown():
            return

        response = None
        temp_path: Optional[Path] = None
        final_path: Optional[Path] = None
        started_at = time.monotonic()
        download_id = hashlib.sha256(cache_key.encode("utf-8", errors="replace")).hexdigest()[:12]

        self._update_entry(cache_key, url, status="downloading", error=None)

        def _close_response() -> None:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

        try:
            self._save_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"[IMG_DOWNLOAD] start id={download_id}")
            response = get_public_remote_resource(
                url,
                cookies=cookies,
                headers=headers or None,
                credential_origin_url=headers.get("Referer"),
                timeout=(8, 20),
                stream=True,
            )
            response_at = time.monotonic()
            if response.status_code != 200:
                raise ValueError(f"http_{response.status_code}")

            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type and not content_type.startswith("image/"):
                raise ValueError(f"invalid_content_type:{content_type}")
            path_ext = Path(urlparse(url).path or "").suffix.lower()
            if content_type == "image/svg+xml" or (not content_type and path_ext == ".svg"):
                raise ValueError("unsafe_image_type:image/svg+xml")
            content_length = str(response.headers.get("Content-Length") or "").strip()
            if content_length:
                try:
                    expected_size = int(content_length)
                except ValueError:
                    expected_size = 0
                if expected_size > max_bytes:
                    raise ValueError(f"image_too_large:{expected_size}")

            logger.debug(
                f"[IMG_DOWNLOAD] headers id={download_id}, status={response.status_code}, "
                f"content_length={content_length or 'unknown'}, "
                f"response_wait={response_at - started_at:.3f}s"
            )

            ext = self._pick_extension(content_type, url)
            filename = f"{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
            temp_path = self._save_dir / f"{filename}.part"
            final_path = self._save_dir / filename

            written = 0
            first_chunk_at: Optional[float] = None
            with temp_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if self._is_shutdown():
                        raise RuntimeError("downloader_shutdown")
                    if not chunk:
                        continue
                    if first_chunk_at is None:
                        first_chunk_at = time.monotonic()
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(f"image_too_large:{written}")
                    handle.write(chunk)

            read_finished_at = time.monotonic()

            if self._is_shutdown():
                raise RuntimeError("downloader_shutdown")

            if written < self._min_bytes:
                raise ValueError(f"image_too_small:{written}")

            try:
                with temp_path.open("rb") as handle:
                    detected_ext = self._detect_safe_image_extension(handle.read(32))
            except Exception:
                detected_ext = None
            if not detected_ext:
                raise ValueError("invalid_image_payload")
            if detected_ext != ext:
                final_path = self._save_dir / f"{Path(filename).stem}{detected_ext}"
                filename = final_path.name

            temp_path.replace(final_path)
            accessible_url = f"/download_images/{filename}"
            _close_response()
            self._update_entry(
                cache_key,
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
                first_byte_wait = (
                    f"{first_chunk_at - response_at:.3f}s"
                    if first_chunk_at is not None
                    else "none"
                )
                logger.debug(
                    f"后台图片下载完成: {filename} ({written} bytes), "
                    f"id={download_id}, first_byte_wait={first_byte_wait}, "
                    f"read={read_finished_at - response_at:.3f}s, "
                    f"total={read_finished_at - started_at:.3f}s"
                )
        except Exception as exc:
            try:
                if temp_path is not None and temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
            _close_response()
            self._update_entry(cache_key, url, status="failed", error=str(exc))
            if not self._is_shutdown():
                logger.debug(
                    f"后台图片下载失败（忽略）: id={download_id}, "
                    f"elapsed={time.monotonic() - started_at:.3f}s, error={str(exc)[:160]}"
                )
        finally:
            _close_response()

    def _update_entry(self, cache_key: str, url: str, **changes: Any) -> None:
        normalized = normalize_remote_image_url(url)
        if not normalized:
            return

        with self._lock:
            entry = self._entries.get(cache_key)
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
                self._entries[cache_key] = entry

            next_status = changes.get("status") if "status" in changes else None
            if "status" in changes:
                changes = {key: value for key, value in changes.items() if key != "status"}
            entry.update(changes)
            if next_status is not None:
                self._set_entry_status_locked(entry, next_status)
            entry["updated_at"] = time.time()
            self._entries.move_to_end(cache_key)

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
        now = time.time()
        expired_keys = [
            key
            for key, entry in self._entries.items()
            if str((entry or {}).get("status") or "") in {"done", "failed"}
            and now - float((entry or {}).get("updated_at") or (entry or {}).get("started_at") or now)
            > self._max_age_seconds
        ]
        for key in expired_keys:
            self._remove_entry_locked(key)

        overflow = len(self._entries) - self._max_entries
        if overflow > 0:
            keys_to_remove = []
            for key, entry in self._entries.items():
                if len(keys_to_remove) >= overflow:
                    break
                status = str((entry or {}).get("status") or "")
                if status in {"queued", "downloading"}:
                    continue
                keys_to_remove.append(key)
            for key in keys_to_remove:
                self._remove_entry_locked(key)

        total_bytes = sum(
            max(0, int((entry or {}).get("byte_size") or 0))
            for entry in self._entries.values()
            if str((entry or {}).get("status") or "") == "done"
        )
        if total_bytes > self._max_total_bytes:
            for key, entry in list(self._entries.items()):
                if total_bytes <= self._max_total_bytes:
                    break
                if str((entry or {}).get("status") or "") != "done":
                    continue
                total_bytes -= max(0, int((entry or {}).get("byte_size") or 0))
                self._remove_entry_locked(key)

    def _remove_entry_locked(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if not isinstance(entry, dict):
            return
        local_path = str(entry.get("local_path") or "").strip()
        if not local_path:
            return
        if any(
            str((other or {}).get("local_path") or "").strip() == local_path
            for other in self._entries.values()
        ):
            return
        self._unlink_managed_file(local_path)

    def _unlink_managed_file(self, local_path: str | Path) -> None:
        try:
            root = self._save_dir.resolve()
            target = Path(local_path).resolve()
            target.relative_to(root)
            if target.is_file():
                target.unlink(missing_ok=True)
        except (OSError, ValueError):
            return

    def _cleanup_disk_if_due_locked(self) -> None:
        now = time.time()
        if now - self._last_disk_cleanup_at < 300:
            return
        self._last_disk_cleanup_at = now
        cutoff = now - self._max_age_seconds
        try:
            for path in self._save_dir.iterdir():
                if not path.is_file():
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue
        except OSError:
            return

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
    def _cache_key(url: str, partition_key: str) -> str:
        return f"{str(partition_key or 'public').strip() or 'public'}\x1f{url}"

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
        if path_ext in _SAFE_IMAGE_EXTENSIONS:
            return path_ext

        return ".png"

    @staticmethod
    def _detect_safe_image_extension(header: bytes) -> Optional[str]:
        data = bytes(header or b"")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if data.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if data.startswith(b"BM"):
            return ".bmp"
        if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return ".webp"
        if len(data) >= 16 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
            return ".avif"
        return None


background_image_downloader = BackgroundImageDownloader()


__all__ = [
    "BackgroundImageDownloader",
    "DEFAULT_IMAGE_ACCEPT",
    "background_image_downloader",
    "build_image_download_partition",
    "get_image_download_partition",
    "build_image_download_request_context",
    "build_image_request_headers",
    "extract_tab_cookies",
    "normalize_remote_image_url",
]
