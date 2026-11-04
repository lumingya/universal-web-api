"""``app/api/system`` 的 updates 分组（原 app/api/system.py 的一部分）。"""

import asyncio
import json
import os
import threading as _threading
import time
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request

from app import __version__ as APP_VERSION
from app.api.deps import verify_dashboard_auth as verify_auth
from app.api.system.common import (
    _read_json_object_or_400,
    logger,
    router,
)

_version_switch_lock = _threading.Lock()

_version_switch_state: dict = {
    "running": False,
    "tag": None,
    "success": None,
    "error": None,
}

_update_check_lock = _threading.Lock()

_update_check_state: dict = {
    "checked": False,
    "checking": False,
    "available": False,
    "current_version": APP_VERSION,
    "latest_version": "",
    "latest_tag": "",
    "published_at": "",
    "repo": "",
    "checked_at": None,
    "error": "",
}

_startup_update_check_scheduled = False


def _get_update_check_state() -> Dict[str, Any]:
    with _update_check_lock:
        return dict(_update_check_state)


def _set_update_check_state(**changes: Any) -> Dict[str, Any]:
    with _update_check_lock:
        _update_check_state.update(changes)
        return dict(_update_check_state)


def _build_update_check_payload(release: Optional[dict], repo: str) -> Dict[str, Any]:
    from app.update.engine import compare_versions, get_current_version, normalize_version

    current_version = get_current_version()
    latest_tag = ""
    latest_version = ""
    published_at = ""

    if release:
        latest_tag = str(release.get("tag_name") or "").strip()
        published_at = str(release.get("published_at") or "").strip()
        if latest_tag:
            latest_version = normalize_version(latest_tag)

    return {
        "checked": True,
        "checking": False,
        "available": bool(latest_version and compare_versions(current_version, latest_version) < 0),
        "current_version": current_version,
        "latest_version": latest_version,
        "latest_tag": latest_tag,
        "published_at": published_at,
        "repo": repo,
        "checked_at": int(time.time()),
        "error": "",
    }


def _run_update_check(repo: Optional[str] = None) -> Dict[str, Any]:
    from app.update.engine import DEFAULT_REPO, fetch_latest_release, get_current_version

    target_repo = str(repo or os.getenv("GITHUB_REPO", DEFAULT_REPO) or DEFAULT_REPO).strip()
    if not target_repo:
        target_repo = DEFAULT_REPO

    _set_update_check_state(checking=True, repo=target_repo, error="")
    try:
        release = fetch_latest_release(target_repo)
        if not release:
            raise RuntimeError("无法获取最新版本信息")
        payload = _build_update_check_payload(release, target_repo)
        logger.info(
            f"[startup] 版本检查完成: 当前 v{payload['current_version']}，"
            f"最新 {payload['latest_tag'] or '未知'}，"
            f"{'有新版本' if payload['available'] else '已是最新'}"
        )
        return _set_update_check_state(**payload)
    except Exception as exc:
        logger.warning(f"版本检查失败: {exc}")
        # 修复：_set_update_check_state 是 merge 语义，失败时若不显式清空这三项，
        # 上一次成功检查的 latest_* 会残留下来，前端会同时显示"最新版本号"和"检查失败"。
        return _set_update_check_state(
            checked=True,
            checking=False,
            available=False,
            current_version=get_current_version(),
            latest_version="",
            latest_tag="",
            published_at="",
            checked_at=int(time.time()),
            error=str(exc),
        )


def _consume_bootstrap_update_check() -> Optional[Dict[str, Any]]:
    from app.update.engine import DEFAULT_REPO

    raw = os.environ.pop("UWAPI_UPDATE_CHECK_RESULT", "")
    try:
        cached = json.loads(raw)
        repo = str(os.getenv("GITHUB_REPO", DEFAULT_REPO) or DEFAULT_REPO).strip()
        age = time.time() - float(cached["checked_at"])
        release = cached["release"]
        if not 0 <= age <= 300 or cached["repo"] != repo:
            return None
        if not isinstance(release, dict) or not isinstance(release.get("tag_name"), str) or not release["tag_name"].strip():
            return None
        return _build_update_check_payload(release, repo)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None


def schedule_startup_update_check() -> None:
    """启动时只触发一次后台版本检查，供控制面板红点读取。"""
    global _startup_update_check_scheduled

    with _update_check_lock:
        if _startup_update_check_scheduled or _update_check_state.get("checking"):
            return
        _startup_update_check_scheduled = True
        cached = _consume_bootstrap_update_check()
        if cached is not None:
            _update_check_state.update(cached)
            logger.info("[startup] 已复用引导阶段版本检查结果")
            return
        _update_check_state["checking"] = True
        _update_check_state["error"] = ""

    t = _threading.Thread(
        target=_run_update_check,
        daemon=True,
        name="startup-update-check",
    )
    t.start()


def _apply_release_list_update_check(releases_raw: list, repo: str) -> Dict[str, Any]:
    release = releases_raw[0] if releases_raw else None
    try:
        payload = _build_update_check_payload(release, repo)
    except Exception as exc:
        logger.warning(f"更新版本提示状态失败: {exc}")
        return _get_update_check_state()
    return _set_update_check_state(**payload)


@router.get("/api/update/check")
async def get_update_check(authenticated: bool = Depends(verify_auth)):
    """读取启动时缓存的版本检查状态。不会访问 GitHub。"""
    return _get_update_check_state()


@router.post("/api/update/check")
async def refresh_update_check(
    repo: Optional[str] = None,
    authenticated: bool = Depends(verify_auth),
):
    """手动触发一次版本检查。"""
    return await asyncio.to_thread(_run_update_check, repo)


@router.get("/api/update/releases")
async def get_releases(
    repo: Optional[str] = None,
    authenticated: bool = Depends(verify_auth),
):
    """获取 GitHub Release 列表（供控制面板手动选择版本）"""
    try:
        from app.update.engine import DEFAULT_REPO, fetch_all_releases, get_current_version, normalize_version
        target_repo = repo or os.getenv("GITHUB_REPO", DEFAULT_REPO)
        releases_raw = await asyncio.to_thread(fetch_all_releases, target_repo)
        current_version = get_current_version()
        update_check = _apply_release_list_update_check(releases_raw, target_repo)
        result = []
        for r in releases_raw:
            tag = str(r.get("tag_name") or "").strip()
            if not tag:
                continue
            result.append({
                "tag": tag,
                "published_at": r.get("published_at") or "",
                "body": r.get("body") or "",
                "zipball_url": r.get("zipball_url") or "",
                "is_current": normalize_version(tag) == normalize_version(current_version),
            })
        return {"releases": result, "current_version": current_version, "update_check": update_check}
    except Exception as e:
        logger.error(f"获取 Release 列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取失败: {e!s}")


@router.post("/api/update/switch")
async def switch_version(
    request: Request,
    authenticated: bool = Depends(verify_auth),
):
    """切换到指定版本（在后台线程中执行，完成后触发服务重启）"""
    global _version_switch_state

    with _version_switch_lock:
        if _version_switch_state.get("running"):
            raise HTTPException(status_code=409, detail="已有版本切换任务正在运行，请稍候")

    data = await _read_json_object_or_400(request)
    tag = str(data.get("tag") or "").strip()
    if not tag:
        raise HTTPException(status_code=400, detail="缺少 tag 参数")

    def _run_switch():
        global _version_switch_state
        try:
            from app.update.engine import update_to_version
            success = update_to_version(tag)
            with _version_switch_lock:
                _version_switch_state = {"running": False, "tag": tag, "success": success, "error": None}
            if success:
                import time as _time
                logger.warning(f"版本切换成功: {tag}，2 秒后重启服务")
                _time.sleep(2.0)
                from app.runtime.relaunch import restart_service
                restart_service()
        except Exception as exc:
            logger.error(f"版本切换失败: {exc}")
            with _version_switch_lock:
                _version_switch_state = {"running": False, "tag": tag, "success": False, "error": str(exc)}

    with _version_switch_lock:
        _version_switch_state = {"running": True, "tag": tag, "success": None, "error": None}

    t = _threading.Thread(target=_run_switch, daemon=True, name=f"version-switch-{tag}")
    t.start()

    logger.info(f"版本切换任务已启动: {tag}")
    return {"status": "started", "tag": tag}


@router.get("/api/update/status")
async def get_version_switch_status(authenticated: bool = Depends(verify_auth)):
    """查询当前版本切换任务状态"""
    with _version_switch_lock:
        return dict(_version_switch_state)
