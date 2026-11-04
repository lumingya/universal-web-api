"""OpenAI 兼容流式响应的**纯函数**辅助（无 I/O、无浏览器、无请求上下文）。

这些函数原先在 ``app/api/chat.py`` 与 ``app/api/tab_routes.py`` 里各存了一份
（逐字节相同），改动时容易只改一处。统一收在这里，两个路由模块直接复用。

约定：
- 只做数据结构转换，不访问网络、文件或全局状态；
- 不依赖 FastAPI / 请求对象，方便单测与复用；
- ``_is_single_choice_request`` 的入参类型只用于注解（``TYPE_CHECKING``），
  避免与 ``app.api.chat`` 形成循环导入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.api.openai_stop import extract_openai_sse_error_message
from app.core.config import SSEFormatter
from app.services.tool_calling import extract_tool_calling_assistant_content

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from app.api.chat import ChatRequest

__all__ = [
    "_dedupe_media_items",
    "_extract_assistant_content",
    "_extract_chunk_media_items",
    "_extract_content_part_media_items",
    "_extract_delta_content_text",
    "_extract_media_part_ref",
    "_extract_stream_error_message",
    "_is_absolute_request_timeout_error",
    "_is_single_choice_request",
    "_pack_done",
    "_pack_error",
    "_pack_error_done",
]


def _is_absolute_request_timeout_error(error: Any) -> bool:
    return str(error or "").strip() == "absolute_request_timeout"


def _extract_stream_error_message(chunk: Any) -> str:
    return extract_openai_sse_error_message(chunk)


def _extract_chunk_media_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    media_items: List[Dict[str, Any]] = []

    top_level_media = data.get("media")
    if isinstance(top_level_media, list):
        media_items.extend(item for item in top_level_media if isinstance(item, dict))

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        delta = choices[0].get("delta", {})
        if isinstance(delta, dict):
            delta_media = delta.get("media")
            if isinstance(delta_media, list):
                media_items.extend(item for item in delta_media if isinstance(item, dict))
            media_items.extend(_extract_content_part_media_items(delta.get("content")))

    return media_items


def _extract_media_part_ref(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("url") or value.get("data_uri") or "").strip()
    return str(value or "").strip()


def _extract_content_part_media_items(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return []

    media_items: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue

        part_type = str(part.get("type") or "").strip().lower()
        ref = ""
        media_type = ""
        if part_type in {"image_url", "input_image", "output_image"}:
            ref = _extract_media_part_ref(
                part.get("image_url") or part.get("url") or part.get("data_uri")
            )
            media_type = "image"
        elif part_type in {"audio_url", "input_audio", "output_audio"}:
            ref = _extract_media_part_ref(
                part.get("audio_url") or part.get("input_audio") or part.get("url")
            )
            media_type = "audio"
        elif part_type in {"video_url", "input_video", "output_video"}:
            ref = _extract_media_part_ref(
                part.get("video_url") or part.get("input_video") or part.get("url")
            )
            media_type = "video"

        if not ref:
            continue

        media_item: Dict[str, Any] = {"media_type": media_type}
        if ref.startswith("data:"):
            media_item["data_uri"] = ref
        else:
            media_item["url"] = ref

        detail = str(part.get("detail") or "").strip()
        if detail:
            media_item["detail"] = detail
        mime = str(part.get("mime_type") or part.get("mime") or "").strip()
        if mime:
            media_item["mime"] = mime
        label = str(part.get("label") or "").strip()
        if label:
            media_item["label"] = label
        media_items.append(media_item)

    return media_items


def _extract_delta_content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type in {"text", "input_text", "output_text"}:
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    if isinstance(content, dict):
        item_type = str(content.get("type") or "").strip().lower()
        if item_type in {"text", "input_text", "output_text"}:
            return str(content.get("text") or "")
    return ""


def _dedupe_media_items(media_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()

    for item in media_items or []:
        media_type = str(item.get("media_type") or "").strip().lower()
        ref = str(item.get("url") or item.get("data_uri") or "").strip()
        key = (media_type, ref)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped


def _is_single_choice_request(body: ChatRequest) -> bool:
    try:
        return int(getattr(body, "n", 1) or 1) <= 1
    except (TypeError, ValueError):
        return True


def _extract_assistant_content(response: Dict[str, Any]) -> str:
    try:
        return extract_tool_calling_assistant_content(response)
    except Exception:
        return ""


def _pack_error(
    message: str,
    code: str = "error",
    *,
    error_type: str = "execution_error",
    status_code: Optional[int] = None,
    retryable: Optional[bool] = None,
    param: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    return SSEFormatter.pack_error(
        message=message,
        error_type=error_type,
        code=code,
        status_code=status_code,
        retryable=retryable,
        param=param,
        extra=extra,
    )


def _pack_done() -> str:
    return "data: [DONE]\n\n"


def _pack_error_done(message: str, code: str = "error") -> str:
    return f"{_pack_error(message, code)}{_pack_done()}"
