"""
app/core/config_parts/sse_formatter.py - SSE 响应格式化器模块
"""
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional


class SSEFormatter:
    """SSE 响应格式化器"""
    
    _sequence = 0
    _sequence_lock = threading.Lock()
    
    @classmethod
    def _generate_id(cls) -> str:
        timestamp = int(time.time() * 1000)
        with cls._sequence_lock:
            cls._sequence += 1
            seq = cls._sequence
        short_uuid = uuid.uuid4().hex[:6]
        return f"chatcmpl-{timestamp}-{seq}-{short_uuid}"
    
    @classmethod
    def pack_chunk(
        cls,
        content: str | None = None,
        model: str = "web-browser",
        completion_id: str | None = None,
        images: list[str] | None = None,
        media: list[dict] | None = None,
        reasoning_content: str | None = None,
    ) -> str:
        """打包流式 chunk。

        为兼容现有前端，content 仍保留 Markdown 媒体链接。
        同时补充自定义 media 字段，供需要结构化媒体数据的前端直接消费。
        """
        chunk_id = completion_id or cls._generate_id()
        delta = {}
        if content is not None:
            delta["content"] = content
        if reasoning_content:
            delta["reasoning_content"] = reasoning_content
        if media is not None:
            delta["media"] = media
        data = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": None
            }]
        }
        if media is not None:
            data["media"] = media
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    
    @classmethod
    def pack_finish(cls, model: str = "web-browser") -> str:
        data = {
            "id": cls._generate_id(),
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        }
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\ndata: [DONE]\n\n"

    @staticmethod
    def pack_comment(comment: str = "keepalive") -> str:
        """打包 SSE 注释帧，用于长连接保活。"""
        safe_comment = " ".join(str(comment or "keepalive").splitlines()).strip() or "keepalive"
        return f": {safe_comment}\n\n"

    @classmethod
    def pack_keepalive(
        cls,
        model: str = "web-browser",
        completion_id: Optional[str] = None,
    ) -> str:
        """打包客户端可解析的空 OpenAI chunk 作为长连接保活。"""
        return cls.pack_chunk(
            content="",
            model=model,
            completion_id=completion_id,
        )
    
    @staticmethod
    def pack_error(
        message: str,
        error_type: str = "execution_error",
        code: str = "workflow_failed",
        *,
        status_code: Optional[int] = None,
        retryable: Optional[bool] = None,
        param: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        clean_message = str(message or "")
        clean_code = str(code or "workflow_failed")
        clean_type = str(error_type or "execution_error")
        clean_status_code = status_code
        clean_retryable = retryable
        clean_param = param
        clean_extra = dict(extra or {})

        try:
            from app.services.error_metadata import resolve_error_metadata
            meta = resolve_error_metadata(clean_message)
            if meta:
                clean_message = meta.message
                if clean_code in {"workflow_failed", "error", "arena_page_error"} and meta.code:
                    clean_code = meta.code
                if clean_type in {"execution_error", "invalid_request_error"} and meta.error_type:
                    clean_type = meta.error_type
                if clean_status_code is None:
                    clean_status_code = meta.status_code
                if clean_retryable is None:
                    clean_retryable = meta.retryable
                if clean_param is None and meta.param is not None:
                    clean_param = meta.param
                if meta.extra:
                    for k, v in meta.extra.items():
                        if k not in clean_extra:
                            clean_extra[k] = v
        except Exception:
            pass

        error: Dict[str, Any] = {
            "message": clean_message,
            "type": clean_type,
            "code": clean_code,
        }
        if clean_status_code is not None:
            error["status_code"] = int(clean_status_code)
        if clean_retryable is not None:
            error["retryable"] = bool(clean_retryable)
        if clean_param is not None:
            error["param"] = clean_param
        if clean_extra:
            for k, v in clean_extra.items():
                if k not in error:
                    error[k] = v

        data = {
            "id": f"chatcmpl-error-{int(time.time() * 1000)}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "web-browser",
            "choices": [{
                "index": 0,
                "delta": {"content": f"[错误] {clean_message}"},
                "finish_reason": None
            }],
            "error": error
        }
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    
    @staticmethod
    def pack_error_json(
        message: str,
        error_type: str = "execution_error",
        code: str = "workflow_failed",
        *,
        status_code: Optional[int] = None,
        retryable: Optional[bool] = None,
        param: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_message = str(message or "")
        clean_code = str(code or "workflow_failed")
        clean_type = str(error_type or "execution_error")
        clean_status_code = status_code
        clean_retryable = retryable
        clean_param = param
        clean_extra = dict(extra or {})

        try:
            from app.services.error_metadata import resolve_error_metadata
            meta = resolve_error_metadata(clean_message)
            if meta:
                clean_message = meta.message
                if clean_code in {"workflow_failed", "error", "arena_page_error"} and meta.code:
                    clean_code = meta.code
                if clean_type in {"execution_error", "invalid_request_error"} and meta.error_type:
                    clean_type = meta.error_type
                if clean_status_code is None:
                    clean_status_code = meta.status_code
                if clean_retryable is None:
                    clean_retryable = meta.retryable
                if clean_param is None and meta.param is not None:
                    clean_param = meta.param
                if meta.extra:
                    for k, v in meta.extra.items():
                        if k not in clean_extra:
                            clean_extra[k] = v
        except Exception:
            pass

        error: Dict[str, Any] = {
            "message": clean_message,
            "type": clean_type,
            "code": clean_code,
        }
        if clean_status_code is not None:
            error["status_code"] = int(clean_status_code)
        if clean_retryable is not None:
            error["retryable"] = bool(clean_retryable)
        if clean_param is not None:
            error["param"] = clean_param
        if clean_extra:
            for k, v in clean_extra.items():
                if k not in error:
                    error[k] = v

        return {"error": error}
    
    @staticmethod
    def pack_non_stream(content: str, model: str = "web-browser", media: list | None = None) -> Dict:
        message = {
            "role": "assistant",
            "content": content
        }
        if media is not None:
            message["media"] = media

        data = {
            "id": f"chatcmpl-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }
        if media is not None:
            data["media"] = media
        return data

    @staticmethod
    def _build_markdown_image_block(images: list) -> str:
        refs = []
        for item in images or []:
            if isinstance(item, dict):
                ref = str(item.get("url") or item.get("data_uri") or "").strip()
            else:
                ref = str(item or "").strip()
            if ref:
                refs.append(ref)

        if not refs:
            return ""

        return "".join(f"\n\n![image_{idx}]({ref})" for idx, ref in enumerate(refs)) + "\n\n"

    @classmethod
    def pack_images_chunk(cls, images: list, completion_id: str = None) -> str:
        """
        打包携带图片的 SSE chunk。

        为保持 OpenAI 兼容性，图片会转成 Markdown 内容，而不是放进 delta.images。
        
        Args:
            images: 图片数据列表，每项符合 ImageData 格式
            completion_id: 补全 ID
        
        Returns:
            SSE 格式的字符串
        
        Example:
            >>> chunk = SSEFormatter.pack_images_chunk([{"kind": "url", "url": "..."}])
        """
        markdown = cls._build_markdown_image_block(images)
        if not markdown:
            return ""
        return cls.pack_chunk(markdown, completion_id=completion_id)

    def pack_final_chunk_with_images(self, images: list, completion_id: str = None) -> str:
        """
        打包包含图片的最终 chunk。

        为保持 OpenAI 兼容性，图片会转成 Markdown 内容，而不是放进 delta.images。
        """
        markdown = self._build_markdown_image_block(images)
        if not markdown:
            return ""
        return self.pack_chunk(markdown, completion_id=completion_id)
