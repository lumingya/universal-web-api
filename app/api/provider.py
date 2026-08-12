"""Machine-readable provider capabilities and runtime status endpoints."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.api.deps import verify_service_auth
from app.core import get_browser
from app.core.config import get_logger
from app.services.request_manager import request_manager


logger = get_logger("API.PROVIDER")
router = APIRouter()

_API_PATHS = [
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/models",
    "/v1/messages",
    "/v1/messages/count_tokens",
]


def _model_entries() -> List[Dict[str, Any]]:
    # Keep model discovery identical to /v1/models, including dynamic Arena models.
    from app.api.chat import _collect_model_entries

    return _collect_model_entries()


def _safe_tab_summary(tab: Any) -> Dict[str, Any]:
    return {
        "index": int(tab.get("persistent_index") or 0),
        "id": str(tab.get("id") or ""),
        "status": str(tab.get("status") or "unknown"),
        "route_domain": str(tab.get("route_domain") or ""),
        "preset_name": str(tab.get("preset_name") or ""),
        "model": str(tab.get("model_name") or tab.get("exposed_model_name") or ""),
        "isolated_profile": bool(tab.get("is_isolated_context")),
    }


def _browser_snapshot() -> tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    try:
        browser = get_browser(auto_connect=False)
        health = browser.health_check()
        pool = browser.get_pool_status()
        tabs = pool.get("tabs") if isinstance(pool, dict) else []
        safe_tabs = [_safe_tab_summary(tab) for tab in tabs if isinstance(tab, dict)]
        return (
            health if isinstance(health, dict) else {"connected": False},
            pool if isinstance(pool, dict) else {"initialized": False},
            safe_tabs,
        )
    except Exception as exc:
        logger.debug(f"Provider status browser snapshot failed: {exc}")
        return {"connected": False, "error": "browser_unavailable"}, {"initialized": False}, []


@router.get("/v1/provider/capabilities")
async def provider_capabilities(
    authenticated: bool = Depends(verify_service_auth),
):
    """Describe the local bridge features clients can safely discover."""
    models = await asyncio.to_thread(_model_entries)
    health, pool, _tabs = await asyncio.to_thread(_browser_snapshot)
    return {
        "object": "provider.capabilities",
        "provider": "universal-web-api",
        "api_paths": _API_PATHS,
        "protocols": {
            "openai_chat_completions": True,
            "openai_responses": True,
            "anthropic_messages": True,
            "anthropic_count_tokens": True,
        },
        "features": {
            "streaming": True,
            "tool_calling": True,
            "multimodal_input": True,
            "media_output": True,
            "tab_pool": bool(pool.get("initialized", True)),
            "new_session": False,
            "reasoning_effort": False,
        },
        "models": models,
        "readiness": {
            "browser_connected": bool(health.get("connected")),
            "idle_tabs": int(pool.get("idle") or 0),
            "busy_tabs": int(pool.get("busy") or 0),
        },
        "generated_at": int(time.time()),
    }


@router.get("/v1/provider/status")
async def provider_status(
    authenticated: bool = Depends(verify_service_auth),
):
    """Return a secret-free runtime snapshot for clients and diagnostics."""
    health, pool, tabs = await asyncio.to_thread(_browser_snapshot)
    requests = request_manager.get_status()
    return {
        "object": "provider.status",
        "provider": "universal-web-api",
        "ready": bool(health.get("connected")) and int(pool.get("idle") or 0) > 0,
        "browser": {
            "connected": bool(health.get("connected")),
            "status": str(health.get("status") or "unhealthy"),
        },
        "pool": {
            "initialized": bool(pool.get("initialized", True)),
            "total": int(pool.get("total") or 0),
            "idle": int(pool.get("idle") or 0),
            "busy": int(pool.get("busy") or 0),
            "max_tabs": int(pool.get("max_tabs") or 0),
            "allocation_mode": str(pool.get("allocation_mode") or ""),
            "tabs": tabs,
        },
        "requests": requests,
        "generated_at": int(time.time()),
    }
