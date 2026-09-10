"""Protocol-neutral input attachments. No browser interaction or implicit conversion.

Canonical wire parts remain image_url / file, so existing image clients keep working.
File IDs and local paths are deliberately not supported without a file registry.
"""
from __future__ import annotations

import ast
import base64
import binascii
import hashlib
import json
import mimetypes
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.utils.remote_resource import get_public_remote_resource

ROOT = Path(__file__).resolve().parents[2] / "temp" / "attachment_inputs"
MIB = 1024 * 1024
DEFAULTS = {
    "enabled": True,
    "max_count": 8,
    "max_file_mb": 20,
    "max_total_mb": 50,
    "allowed_types": [],
    "error_selectors": [],
    "transport_order": ["file_input", "cdp_drop", "js_drop", "file_clipboard"],
    "ready_timeout": 30,
}
TRANSPORTS = frozenset((*DEFAULTS["transport_order"], "image_clipboard"))
ATTACHMENT_TYPES = frozenset({"image_url", "image", "input_image", "file", "input_file", "document", "input_audio", "audio_url", "input_video", "video_url"})
_active: set[Path] = set()
_lock = threading.RLock()


class AttachmentError(ValueError):
    def __init__(self, code: str, message: str, *, message_index=None, part_index=None):
        super().__init__(message)
        self.code, self.message = code, message
        self.message_index, self.part_index = message_index, part_index

    def detail(self):
        return {"code": self.code, "message": self.message,
                "message_index": self.message_index, "part_index": self.part_index}


def attachment_config(raw=None):
    """Safe effective config; old presets without this section use defaults."""
    raw = raw if isinstance(raw, dict) else {}
    result = {**DEFAULTS, "allowed_types": [], "transport_order": list(DEFAULTS["transport_order"])}
    if isinstance(raw.get("enabled"), bool):
        result["enabled"] = raw["enabled"]
    for key, ceiling in (("max_count", 32), ("max_file_mb", 100), ("max_total_mb", 200), ("ready_timeout", 180)):
        try:
            result[key] = max(1, min(ceiling, int(raw.get(key, result[key]))))
        except (TypeError, ValueError, OverflowError):
            pass
    values = raw.get("allowed_types")
    if isinstance(values, list):
        # Invalid nonempty allowlists must never silently turn into allow-all.
        result["allowed_types"] = list(dict.fromkeys(str(v).strip().lower() for v in values if str(v).strip()))[:100]
    if isinstance(raw.get("error_selectors"), list):
        result["error_selectors"] = [str(v).strip() for v in raw["error_selectors"] if str(v).strip()][:30]
    else:
        result["error_selectors"] = []
    values = raw.get("transport_order")
    if isinstance(values, list):
        result["transport_order"] = list(dict.fromkeys(v for v in values if isinstance(v, str) and v in TRANSPORTS))
    return result


def content_parts(content):
    if isinstance(content, dict):
        return [content]
    if isinstance(content, (list, tuple)):
        return list(content)
    if isinstance(content, str) and content.strip().startswith("["):
        for parse in (json.loads, ast.literal_eval):
            try:
                value = parse(content)
                if isinstance(value, list) and value and all(isinstance(v, dict) and "type" in v for v in value):
                    return value
            except (ValueError, SyntaxError, TypeError):
                pass
    return content


def safe_filename(value, default="attachment.bin"):
    name = unquote(str(value or "")).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', "_", name).strip(" .")
    if not name:
        name = default
    # Keep suffix on long names; storage directory is always server-generated.
    suffix = Path(name).suffix[:20]
    name = name[:160 - len(suffix)] + suffix if len(name) > 160 else name
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        name = "_" + name
    return name


def normalize_attachment_part(part):
    """Return canonical attachment or None for non-attachment blocks; never stringify bytes."""
    if not isinstance(part, dict):
        return None
    kind = str(part.get("type") or "").lower().strip()
    if kind not in ATTACHMENT_TYPES:
        source = part.get("source")
        if any(key in part for key in ("file_data", "file_id")) or (isinstance(source, dict) and source.get("type") == "base64"):
            raise AttachmentError("attachment_block_unsupported", "不支持的附件内容块类型，未将二进制内容转换为提示词")
        return None
    image = kind in {"image", "image_url", "input_image"}
    value = part.get("file") if isinstance(part.get("file"), dict) else part
    source = part.get("source") if isinstance(part.get("source"), dict) else {}
    if value.get("file_id") or part.get("file_id") or source.get("file_id"):
        raise AttachmentError("attachment_file_id_unsupported", "暂不支持 file_id，请传文件 URL 或完整 base64 数据")
    mime = str(source.get("media_type") or value.get("mime_type") or part.get("mime_type") or "").split(";", 1)[0].strip().lower()
    filename = value.get("filename") or part.get("filename") or part.get("title")
    ref = None
    if image:
        ref = part.get("image_url") or part.get("url")
        if isinstance(ref, dict):
            ref = ref.get("url")
    elif kind == "input_audio":
        audio = part.get("input_audio")
        if not isinstance(audio, dict):
            raise AttachmentError("attachment_invalid_source", "input_audio 必须包含 data 和 format")
        audio_mimes = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "flac": "audio/flac", "webm": "audio/webm", "mp4": "audio/mp4", "m4a": "audio/mp4", "aac": "audio/aac"}
        fmt = str(audio.get("format") or "").lower()
        if fmt not in audio_mimes:
            raise AttachmentError("attachment_audio_format_unsupported", "不支持的 input_audio.format")
        mime = audio_mimes[fmt]
        data = audio.get("data")
        ref = f"data:{mime};base64,{data}" if isinstance(data, str) and data else None
        filename = filename or f"audio.{fmt}"
    else:
        ref = value.get("file_data") or value.get("file_url") or part.get("audio_url") or part.get("video_url") or part.get("url")
        if isinstance(ref, dict):
            ref = ref.get("url")
    if source:
        source_type = source.get("type")
        if source_type == "url":
            ref = source.get("url")
        elif source_type == "base64":
            data = source.get("data")
            ref = f"data:{mime or ('image/png' if image else 'application/octet-stream')};base64,{data}" if isinstance(data, str) and data else None
        elif source_type == "text" and kind == "document":
            text = source.get("data")
            if not isinstance(text, str):
                raise AttachmentError("attachment_invalid_source", "document text source 必须为字符串")
            ref = "data:text/plain;base64," + base64.b64encode(text.encode("utf-8")).decode("ascii")
            filename, mime = filename or "document.txt", "text/plain"
        else:
            raise AttachmentError("attachment_source_unsupported", "不支持的附件 source.type")
    if not isinstance(ref, str) or not ref.strip():
        raise AttachmentError("attachment_missing_data", "附件缺少 URL 或完整 base64 数据")
    ref = ref.strip()
    if not ref.startswith(("https://", "http://", "data:")):
        raise AttachmentError("attachment_source_unsupported", "附件仅支持 HTTP(S) URL 或 base64 data URI，不允许本地路径")
    if ref.startswith("data:") and (";base64," not in ref or not ref.split(";base64,", 1)[1].strip()):
        raise AttachmentError("attachment_invalid_base64", "附件 data URI 必须包含完整 base64 数据")
    if image:
        payload = {"url": ref}
        detail = (part.get("image_url") or {}).get("detail") if isinstance(part.get("image_url"), dict) else part.get("detail")
        if detail:
            payload["detail"] = detail
        return {"type": "image_url", "image_url": payload}
    return {"type": "file", "file": {"file_data" if ref.startswith("data:") else "file_url": ref,
            **({"filename": safe_filename(filename)} if filename else {}), **({"mime_type": mime} if mime else {})}}


def normalize_messages(messages):
    result = []
    for mi, message in enumerate(messages or []):
        if not isinstance(message, dict):
            result.append(message)
            continue
        content = content_parts(message.get("content"))
        if isinstance(content, list):
            normalized = []
            for pi, part in enumerate(content):
                try:
                    normalized.append(normalize_attachment_part(part) or part)
                except AttachmentError as exc:
                    exc.message_index, exc.part_index = mi, pi
                    raise
            content = normalized
        result.append({**message, "content": content})
    return result


def has_attachments(content):
    parts = content_parts(content)
    return isinstance(parts, list) and any(isinstance(p, dict) and p.get("type") in ATTACHMENT_TYPES for p in parts)


def type_allowed(filename, mime, rules):
    if not rules:
        return True
    suffix = Path(filename).suffix.lower()
    mime = str(mime or "").lower().split(";", 1)[0]
    return any(rule == "*/*" or (rule.startswith(".") and suffix == rule) or rule == mime or
               (rule.endswith("/*") and mime.startswith(rule[:-1])) for rule in rules)


@dataclass(frozen=True)
class PreparedAttachment:
    id: str
    path: Path
    filename: str
    mime: str
    byte_size: int
    sha256: str
    message_index: int
    part_index: int
    role: str
    is_image: bool = False


def cleanup_attachment_inputs(now=None):
    now = time.time() if now is None else now
    if not ROOT.exists():
        return
    with _lock:
        for directory in ROOT.iterdir():
            if directory in _active or not directory.is_dir():
                continue
            try:
                marker = directory / ".expires"
                if marker.exists() and float(marker.read_text()) < now:
                    shutil.rmtree(directory)
                elif not marker.exists() and now - directory.stat().st_mtime > 86400:
                    shutil.rmtree(directory)  # crash orphan; never an active lease
            except (OSError, ValueError):
                continue


class AttachmentBatch:
    """Request-owned lease. Potentially dispatched files get a grace period, not per-file timers."""
    def __init__(self):
        self.directory = None
        self.items = []
        self.may_be_reading = False

    def adopt_generated(self, filepath):
        """Transfer an internally generated temp file to this request's lease."""
        if self.directory is None:
            cleanup_attachment_inputs()
            ROOT.mkdir(parents=True, exist_ok=True)
            with _lock:
                self.directory = Path(tempfile.mkdtemp(prefix="request_", dir=ROOT))
                _active.add(self.directory)
        source = Path(filepath)
        directory = self.directory / uuid.uuid4().hex
        directory.mkdir()
        target = directory / safe_filename(source.name)
        source.replace(target)
        return target

    def prepare(self, messages, config=None, cancelled=lambda: False):
        config = attachment_config(config)
        normalized = normalize_messages(messages)
        specs = [(mi, pi, m.get("role", ""), p) for mi, m in enumerate(normalized) if isinstance(m, dict)
                 for pi, p in enumerate(m.get("content") if isinstance(m.get("content"), list) else [])
                 if isinstance(p, dict) and p.get("type") in {"image_url", "file"}]
        if not specs:
            return []
        if not config["enabled"]:
            raise AttachmentError("attachments_disabled", "当前预设禁止上传附件")
        if len(specs) > config["max_count"]:
            raise AttachmentError("attachment_count_exceeded", f"附件数量超过限制 {config['max_count']}")
        cleanup_attachment_inputs()
        ROOT.mkdir(parents=True, exist_ok=True)
        with _lock:
            self.directory = Path(tempfile.mkdtemp(prefix="request_", dir=ROOT))
            _active.add(self.directory)
        total = 0
        try:
            for mi, pi, role, part in specs:
                if cancelled():
                    raise AttachmentError("attachment_cancelled", "附件准备已取消")
                budget = min(config["max_file_mb"] * MIB, config["max_total_mb"] * MIB - total)
                item = self._materialize(part, mi, pi, role, budget, cancelled)
                if not type_allowed(item.filename, item.mime, config["allowed_types"]):
                    raise AttachmentError("attachment_type_unsupported", f"当前预设不接受附件类型：{item.mime}", message_index=mi, part_index=pi)
                total += item.byte_size
                self.items.append(item)
            return list(self.items)
        except BaseException:
            self.close()
            raise

    def _materialize(self, part, mi, pi, role, budget, cancelled):
        image = part["type"] == "image_url"
        payload = part["image_url"] if image else part["file"]
        ref = payload.get("url") or payload.get("file_data") or payload.get("file_url")
        aid = uuid.uuid4().hex
        directory = self.directory / aid
        directory.mkdir()
        path = directory / "payload"
        response = None
        mime = payload.get("mime_type") or ""
        filename = payload.get("filename")
        try:
            if ref.startswith("data:"):
                header, encoded = ref.split(";base64,", 1)
                mime = header[5:].split(";", 1)[0].lower() or "application/octet-stream"
                if len(encoded) > ((max(0, budget) + 2) // 3) * 4 + 65536:
                    raise AttachmentError("attachment_size_exceeded", "附件编码内容超过大小限制")
                compact = re.sub(r"\s+", "", encoded)
                if len(compact) > ((max(0, budget) + 2) // 3) * 4:
                    raise AttachmentError("attachment_size_exceeded", "附件超过单文件或请求总大小限制")
                try:
                    data = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=True)
                except (ValueError, binascii.Error):
                    raise AttachmentError("attachment_invalid_base64", "附件 base64 无效") from None
                chunks = (data,)
            else:
                response = get_public_remote_resource(ref, timeout=(8, 30), stream=True)
                response.raise_for_status()
                mime = mime or str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower().strip()
                filename = filename or urlsplit(ref).path.rsplit("/", 1)[-1]
                if str(response.headers.get("Content-Length", "")).isdigit() and int(response.headers["Content-Length"]) > budget:
                    raise AttachmentError("attachment_size_exceeded", "附件超过单文件或请求总大小限制")
                chunks = response.iter_content(chunk_size=65536)
            size, digest = 0, hashlib.sha256()
            with path.open("wb") as stream:
                for chunk in chunks:
                    if cancelled():
                        raise AttachmentError("attachment_cancelled", "附件准备已取消")
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > budget:
                        raise AttachmentError("attachment_size_exceeded", "附件超过单文件或请求总大小限制")
                    stream.write(chunk)
                    digest.update(chunk)
            if not size:
                raise AttachmentError("attachment_empty", "附件内容为空")
            # Image validation is also applied to images sent as generic files.
            guessed = mimetypes.guess_type(str(filename or ""))[0] or ""
            if image or mime.startswith("image/") or guessed.startswith("image/"):
                from app.utils.image_handler import _validate_image_bytes
                extension = _validate_image_bytes(path.read_bytes())
                if not extension:
                    raise AttachmentError("attachment_invalid_image", "图片格式、尺寸或内容无效")
                mime = mimetypes.guess_type("image" + extension)[0] or "image/png"
                filename = str(Path(safe_filename(filename, "image")).stem) + extension
                image = True
            else:
                # Conservative sniffing: no parsing, unpacking or conversion of arbitrary files.
                with path.open("rb") as stream:
                    head = stream.read(16)
                if head.startswith(b"%PDF-"):
                    mime = "application/pdf"
                elif head.startswith(b"RIFF") and head[8:12] == b"WAVE":
                    mime = "audio/wav"
                mime = mime or guessed or "application/octet-stream"
            filename = safe_filename(filename, "attachment" + (mimetypes.guess_extension(mime) or ".bin"))
            target = directory / filename
            path.replace(target)
            return PreparedAttachment(aid, target, filename, mime, size, digest.hexdigest(), mi, pi, role, image)
        except AttachmentError as exc:
            exc.message_index, exc.part_index = mi, pi
            raise
        except Exception:
            # Never include signed URLs / response bodies in client errors or logs.
            raise AttachmentError("attachment_fetch_failed", "附件下载或读取失败，请检查 URL、权限及网络", message_index=mi, part_index=pi) from None
        finally:
            if response is not None:
                response.close()

    def close(self):
        if self.directory is None:
            return
        with _lock:
            try:
                if self.may_be_reading:
                    (self.directory / ".expires").write_text(str(time.time() + 900))
                else:
                    shutil.rmtree(self.directory, ignore_errors=True)
            finally:
                _active.discard(self.directory)
                self.directory = None


def attachment_scope(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        batch = AttachmentBatch()
        try:
            yield from fn(*args, **kwargs, _attachment_batch=batch)
        finally:
            batch.close()
    return wrapped
