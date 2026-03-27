"""
app/services/marketplace_service.py - 配置市场服务
"""

from __future__ import annotations

import base64
import copy
import html
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit, quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import AppConfig, get_logger
from app.services.config_engine import config_engine

logger = get_logger("SERVICE.MARKETPLACE")


class MarketplaceService:
    CACHE_TTL_SECONDS = 300.0
    DEFAULT_TYPE = "site_config"
    DEFAULT_AUTHOR = "社区贡献"
    DEFAULT_SITE_CATEGORY = "站点配置"
    DEFAULT_COMMAND_CATEGORY = "命令系统"
    ISSUE_MARKER = "<!-- marketplace-submission -->"
    ISSUE_TITLE_PREFIX = "[市场投稿]"
    ISSUE_ID_PREFIX = "pending-issue-"

    def __init__(self):
        self._cached_manifest: Optional[Dict[str, Any]] = None
        self._cached_at = 0.0

    def list_catalog(self, force_refresh: bool = False) -> Dict[str, Any]:
        manifest = self._load_manifest(force_refresh=force_refresh)
        items = [self._to_list_item(item) for item in manifest.get("items", [])]
        items.sort(key=lambda item: (-int(item.get("downloads", 0) or 0), str(item.get("name") or "")))
        pending_count = sum(1 for item in items if str(item.get("review_status") or "") == "pending")

        return {
            "source_mode": manifest.get("source_mode", "local"),
            "source_name": manifest.get("source_name", "配置市场"),
            "source_url": manifest.get("source_url", ""),
            "repo_url": manifest.get("repo_url", ""),
            "upload_url": manifest.get("upload_url", ""),
            "warning": manifest.get("warning", ""),
            "submit_mode": manifest.get("submit_mode", "local"),
            "submit_label": manifest.get("submit_label", "投稿上传"),
            "submit_help": manifest.get("submit_help", ""),
            "submit_target": manifest.get("submit_target", ""),
            "default_sort": "downloads",
            "count": len(items),
            "approved_count": max(0, len(items) - pending_count),
            "pending_count": pending_count,
            "total_downloads": sum(int(item.get("downloads", 0) or 0) for item in items),
            "items": items,
        }

    def get_item(self, item_id: str, force_refresh: bool = False) -> Dict[str, Any]:
        manifest = self._load_manifest(force_refresh=force_refresh)
        normalized_id = str(item_id or "").strip()
        if not normalized_id:
            raise KeyError("缺少市场项目 ID")

        item = next(
            (copy.deepcopy(entry) for entry in manifest.get("items", []) if entry.get("id") == normalized_id),
            None,
        )
        if not item:
            raise KeyError(f"未找到市场项目: {normalized_id}")

        if item.get("import_disabled"):
            return item

        item_type = item.get("item_type", self.DEFAULT_TYPE)
        if item_type == "command_bundle":
            item["command_bundle"] = self._resolve_command_bundle(item)
        else:
            item["site_config"] = self._resolve_site_config(item)
        return item

    def submit_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize_submission(payload)
        submit_mode = AppConfig.get_marketplace_submit_mode()
        if submit_mode != "local":
            return self._build_public_submission_response(normalized)

        return self._submit_item_local(normalized)

    def _submit_item_local(self, normalized: Dict[str, Any]) -> Dict[str, Any]:
        manifest = self._load_local_manifest()
        items = manifest.get("items", [])
        items.insert(0, normalized)
        manifest["items"] = items
        manifest.setdefault("source_name", "本地配置市场")
        manifest.setdefault("source_url", "")
        manifest.setdefault("repo_url", AppConfig.get_marketplace_repo_url())
        manifest.setdefault("upload_url", AppConfig.get_marketplace_upload_url())

        path = Path(AppConfig.get_marketplace_file())
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

        self._cached_manifest = None
        self._cached_at = 0.0
        return {
            "mode": "local",
            "item": self._to_list_item(normalized),
            "submission_url": "",
            "message": "投稿已加入当前实例的本地市场",
        }

    def _load_manifest(self, force_refresh: bool = False) -> Dict[str, Any]:
        now_ts = time.time()
        if not force_refresh and self._cached_manifest and (now_ts - self._cached_at) < self.CACHE_TTL_SECONDS:
            return copy.deepcopy(self._cached_manifest)

        local_manifest = self._load_local_manifest()
        remote_url = AppConfig.get_marketplace_index_url()
        repo_url = AppConfig.get_marketplace_repo_url()
        upload_url = AppConfig.get_marketplace_upload_url()
        submit_mode = AppConfig.get_marketplace_submit_mode()
        local_overlay_enabled = AppConfig.is_marketplace_local_overlay_enabled()
        try:
            remote_cache_manifest = self._load_remote_cache_manifest() if remote_url else None
        except Exception as exc:
            remote_cache_manifest = None
            logger.warning(f"[marketplace] 本地公共缓存读取失败: {exc}")
        warning_messages: List[str] = []
        pending_items: List[Dict[str, Any]] = []

        if remote_url:
            try:
                remote_manifest = self._load_remote_manifest(remote_url)
                if local_overlay_enabled and local_manifest.get("items"):
                    manifest = self._merge_manifests(remote_manifest, local_manifest)
                    manifest["source_mode"] = "hybrid"
                else:
                    manifest = remote_manifest
                    manifest["source_mode"] = "remote"
                manifest.setdefault("source_name", remote_manifest.get("source_name") or "GitHub 配置市场")
                manifest.setdefault("source_url", remote_url)
            except Exception as exc:
                logger.warning(f"[marketplace] 远程索引加载失败: {exc}")
                if remote_cache_manifest:
                    manifest = copy.deepcopy(remote_cache_manifest)
                    manifest["source_mode"] = "cache"
                    cached_at = str(remote_cache_manifest.get("cached_at") or "").strip()
                    if cached_at:
                        warning_messages.append(f"GitHub 索引读取失败，已改用本地缓存（缓存时间 {cached_at}）: {exc}")
                    else:
                        warning_messages.append(f"GitHub 索引读取失败，已改用本地缓存: {exc}")
                else:
                    warning_messages.append(f"GitHub 索引读取失败，已回退到本地市场: {exc}")
                    manifest = local_manifest
                    manifest["source_mode"] = "local"
        else:
            manifest = local_manifest
            manifest["source_mode"] = "local"

        if AppConfig.is_marketplace_pending_enabled() and manifest.get("source_mode") != "cache":
            try:
                pending_items = self._fetch_pending_issue_items()
            except Exception as exc:
                logger.warning(f"[marketplace] 待审核投稿加载失败: {exc}")
                cached_pending_items = self._extract_cached_pending_items(remote_cache_manifest)
                if cached_pending_items:
                    pending_items = cached_pending_items
                    cached_at = str((remote_cache_manifest or {}).get("cached_at") or "").strip()
                    if cached_at:
                        warning_messages.append(f"GitHub 待审核投稿读取失败，已使用本地缓存（缓存时间 {cached_at}）: {exc}")
                    else:
                        warning_messages.append(f"GitHub 待审核投稿读取失败，已使用本地缓存: {exc}")
                else:
                    warning_messages.append(f"GitHub 待审核投稿读取失败: {exc}")

        if pending_items:
            pending_items = self._filter_pending_items_against_manifest(
                manifest.get("items", []),
                pending_items,
            )
            manifest["items"] = self._merge_item_lists(manifest.get("items", []), pending_items)

        manifest["items"] = self._enrich_item_submitters(manifest.get("items", []))

        default_source_name = "公共插件市场" if remote_url else "本地配置市场"
        manifest.setdefault("source_name", default_source_name)
        manifest.setdefault("source_url", remote_url if remote_url else "")
        manifest.setdefault("repo_url", repo_url)
        manifest.setdefault("upload_url", upload_url)
        manifest["warning"] = "；".join(
            part for part in [str(manifest.get("warning") or ""), *warning_messages] if part
        )
        manifest["submit_mode"] = submit_mode
        manifest["submit_label"] = "投稿到公共市场" if submit_mode != "local" and upload_url else "投稿上传"
        manifest["submit_help"] = (
            "投稿会打开 GitHub 公共页面，页面会自动填好基本信息；提交时复制到剪贴板的只有 JSON 代码块，打开后粘贴到“预览 JSON”下面即可。已提交但未收录的内容会以“待审核”状态显示在列表里。"
            if submit_mode != "local" and upload_url
            else "投稿会直接写入当前实例的本地市场清单。"
        )
        manifest["submit_target"] = "GitHub 公共投稿" if submit_mode != "local" and upload_url else "本地市场"

        if remote_url and manifest.get("source_mode") in {"remote", "hybrid"}:
            try:
                self._save_remote_cache_manifest(manifest)
            except Exception as exc:
                logger.warning(f"[marketplace] 本地公共缓存写入失败: {exc}")

        self._cached_manifest = copy.deepcopy(manifest)
        self._cached_at = now_ts
        return copy.deepcopy(manifest)

    def _load_remote_cache_manifest(self) -> Optional[Dict[str, Any]]:
        path = Path(AppConfig.get_marketplace_cache_file())
        if not path.exists():
            return None

        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

        manifest = self._normalize_manifest(data)
        manifest.setdefault("source_name", "公共插件市场")
        manifest.setdefault("source_url", AppConfig.get_marketplace_index_url())
        manifest.setdefault("repo_url", AppConfig.get_marketplace_repo_url())
        manifest.setdefault("upload_url", AppConfig.get_marketplace_upload_url())
        manifest["cached_at"] = str(data.get("cached_at") or "")
        return manifest

    def _save_remote_cache_manifest(self, manifest: Dict[str, Any]) -> None:
        path = Path(AppConfig.get_marketplace_cache_file())
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "source_name": manifest.get("source_name") or "公共插件市场",
            "source_url": manifest.get("source_url") or AppConfig.get_marketplace_index_url(),
            "repo_url": manifest.get("repo_url") or AppConfig.get_marketplace_repo_url(),
            "upload_url": manifest.get("upload_url") or AppConfig.get_marketplace_upload_url(),
            "cached_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "items": copy.deepcopy(manifest.get("items", [])),
        }
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _extract_cached_pending_items(self, manifest: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(manifest, dict):
            return []
        items = []
        for item in manifest.get("items", []):
            if str((item or {}).get("review_status") or "") != "pending":
                continue
            items.append(copy.deepcopy(item))
        return items

    def _load_local_manifest(self) -> Dict[str, Any]:
        path = Path(AppConfig.get_marketplace_file())
        if not path.exists():
            return self._normalize_manifest({
                "source_name": "本地配置市场",
                "source_url": "",
                "repo_url": AppConfig.get_marketplace_repo_url(),
                "upload_url": AppConfig.get_marketplace_upload_url(),
                "items": [],
            })

        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        manifest = self._normalize_manifest(data)
        manifest.setdefault("source_name", "本地配置市场")
        manifest.setdefault("source_url", "")
        manifest.setdefault("repo_url", AppConfig.get_marketplace_repo_url())
        manifest.setdefault("upload_url", AppConfig.get_marketplace_upload_url())
        return manifest

    def _merge_manifests(self, remote_manifest: Dict[str, Any], local_manifest: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "source_name": remote_manifest.get("source_name") or local_manifest.get("source_name") or "配置市场",
            "source_url": remote_manifest.get("source_url") or local_manifest.get("source_url") or "",
            "repo_url": remote_manifest.get("repo_url") or local_manifest.get("repo_url") or "",
            "upload_url": local_manifest.get("upload_url") or remote_manifest.get("upload_url") or "",
            "warning": local_manifest.get("warning") or remote_manifest.get("warning") or "",
            "items": self._merge_item_lists(local_manifest.get("items", []), remote_manifest.get("items", [])),
        }

    def _merge_item_lists(self, *sources: Any) -> List[Dict[str, Any]]:
        merged_items: List[Dict[str, Any]] = []
        seen_ids = set()
        for source in sources:
            for item in source or []:
                item_id = str(item.get("id") or "").strip()
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                merged_items.append(copy.deepcopy(item))
        return merged_items

    def _fetch_json(self, url: str) -> Dict[str, Any]:
        payload = self._fetch_json_payload(url)
        if not isinstance(payload, dict):
            raise ValueError("市场索引必须是 JSON 对象")
        return payload

    def _load_remote_manifest(self, remote_url: str) -> Dict[str, Any]:
        try:
            return self._normalize_manifest(self._fetch_json(remote_url))
        except Exception as raw_exc:
            if self._can_use_repo_index_api_fallback(remote_url):
                try:
                    manifest, _ = self._load_remote_manifest_from_repo_api()
                    logger.warning(f"[marketplace] raw GitHub 索引读取失败，已自动切换到 Contents API: {raw_exc}")
                    return manifest
                except Exception as api_exc:
                    raise RuntimeError(
                        f"{raw_exc}；GitHub Contents API 兜底也失败: {api_exc}"
                    ) from api_exc
            raise

    def _can_use_repo_index_api_fallback(self, remote_url: str) -> bool:
        repo = str(AppConfig.get_marketplace_repo() or "").strip()
        branch = AppConfig.get_marketplace_branch()
        index_path = AppConfig.get_marketplace_index_path().lstrip("/")
        parsed = urlsplit(str(remote_url or "").strip())
        if not repo or "/" not in repo:
            return False
        if parsed.scheme != "https" or parsed.netloc.lower() != "raw.githubusercontent.com":
            return False
        expected_path = f"/{repo}/{branch}/{index_path}"
        return parsed.path == expected_path

    def _build_request_headers(self, url: str, accept: str, github_token: str = "") -> Dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": "Universal-Web-to-API-Marketplace/1.0",
        }
        token = str(github_token or "").strip() or str(AppConfig.get_marketplace_github_token() or "").strip()
        host = urlsplit(str(url or "")).netloc.lower()
        if token and host == "api.github.com":
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _is_transient_fetch_error(self, exc: Exception) -> bool:
        message = str(getattr(exc, "reason", exc) or "").lower()
        if isinstance(exc, HTTPError):
            return int(getattr(exc, "code", 0) or 0) in {429, 500, 502, 503, 504}
        return any(
            hint in message
            for hint in (
                "unexpected eof while reading",
                "eof occurred in violation of protocol",
                "connection reset",
                "connection aborted",
                "timed out",
                "timeout",
                "temporarily unavailable",
                "tlsv1",
                "ssl",
            )
        )

    def _fetch_text_payload(
        self,
        url: str,
        accept: str = "text/html",
        github_token: str = "",
        method: str = "GET",
        payload: Optional[Any] = None,
    ) -> str:
        method_name = str(method or "GET").upper()
        timeout = max(1.0, float(AppConfig.get_marketplace_timeout()))
        attempts = 2 if method_name == "GET" else 1

        for attempt in range(1, attempts + 1):
            request_body = None
            headers = self._build_request_headers(url, accept=accept, github_token=github_token)
            if payload is not None:
                request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"
            request = Request(
                str(url or "").strip(),
                data=request_body,
                headers=headers,
                method=method_name,
            )
            try:
                with urlopen(request, timeout=timeout) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return response.read().decode(charset)
            except HTTPError as exc:
                if attempt < attempts and self._is_transient_fetch_error(exc):
                    time.sleep(0.35 * attempt)
                    continue
                detail = ""
                try:
                    charset = exc.headers.get_content_charset() or "utf-8"
                    body_text = exc.read().decode(charset)
                    if body_text:
                        parsed = json.loads(body_text)
                        if isinstance(parsed, dict):
                            detail = str(parsed.get("message") or parsed.get("error_description") or "").strip()
                except Exception:
                    detail = ""
                if detail:
                    raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
                raise RuntimeError(f"HTTP {exc.code}") from exc
            except URLError as exc:
                if attempt < attempts and self._is_transient_fetch_error(exc):
                    time.sleep(0.35 * attempt)
                    continue
                raise RuntimeError(str(getattr(exc, "reason", exc))) from exc
            except Exception as exc:
                if attempt < attempts and self._is_transient_fetch_error(exc):
                    time.sleep(0.35 * attempt)
                    continue
                raise RuntimeError(str(exc)) from exc

        raise RuntimeError("远程请求失败")

    def _fetch_json_payload(
        self,
        url: str,
        github_token: str = "",
        method: str = "GET",
        payload: Optional[Any] = None,
    ) -> Any:
        text = self._fetch_text_payload(
            url,
            accept="application/vnd.github+json, application/json",
            github_token=github_token,
            method=method,
            payload=payload,
        )

        return json.loads(text)

    def _normalize_manifest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("市场清单必须是对象")

        items = []
        for raw_item in payload.get("items", []):
            normalized = self._normalize_item(raw_item)
            if normalized:
                items.append(normalized)

        return {
            "source_name": str(payload.get("source_name") or "配置市场"),
            "source_url": str(payload.get("source_url") or ""),
            "repo_url": str(payload.get("repo_url") or ""),
            "upload_url": str(payload.get("upload_url") or ""),
            "warning": str(payload.get("warning") or ""),
            "items": items,
        }

    def get_review_status(self, github_token: str) -> Dict[str, Any]:
        context = self._resolve_review_context(github_token)
        return {
            "connected": True,
            "can_review": bool(context.get("can_review")),
            "repo": context.get("repo", ""),
            "repo_url": context.get("repo_url", ""),
            "login": context.get("login", ""),
            "role_name": context.get("role_name", ""),
            "permission_label": context.get("permission_label", ""),
            "permissions": copy.deepcopy(context.get("permissions") or {}),
        }

    def approve_pending_issue(self, issue_number: int, github_token: str) -> Dict[str, Any]:
        context = self._resolve_review_context(github_token, require_review=True)
        issue_payload = self._fetch_github_issue(issue_number, github_token)
        if not self._is_marketplace_issue(issue_payload):
            raise ValueError(f"GitHub issue #{issue_number} 不是待审核投稿")
        approved_item = self._build_approved_item_from_issue(issue_payload)

        manifest, manifest_sha = self._load_remote_manifest_for_review(github_token)
        existing_items = list(manifest.get("items", []))
        existing_item = next(
            (
                entry for entry in existing_items
                if str((entry or {}).get("id") or "").strip() == str(approved_item.get("id") or "").strip()
            ),
            None,
        )
        if isinstance(existing_item, dict):
            approved_item["downloads"] = self._coerce_int(existing_item.get("downloads"))
            approved_item["stars"] = self._coerce_int(existing_item.get("stars"))

        manifest["items"] = [
            entry for entry in existing_items
            if str((entry or {}).get("id") or "").strip() != str(approved_item.get("id") or "").strip()
        ]
        manifest["items"].insert(0, approved_item)
        manifest.setdefault("repo_url", context.get("repo_url") or AppConfig.get_marketplace_repo_url())
        manifest.setdefault("source_url", AppConfig.get_marketplace_index_url())
        manifest.setdefault("upload_url", AppConfig.get_marketplace_upload_url())

        self._save_remote_manifest_for_review_with_fallback(
            manifest,
            manifest_sha,
            github_token,
            message=f"Approve marketplace submission from issue #{issue_number}",
        )
        self._close_github_issue_with_fallback(issue_number, github_token)

        self._cached_manifest = None
        self._cached_at = 0.0
        try:
            self._save_remote_cache_manifest(manifest)
        except Exception as exc:
            logger.warning(f"[marketplace] 审核通过后写入本地缓存失败: {exc}")

        return {
            "success": True,
            "action": "approve",
            "issue_number": int(issue_number),
            "item": self._to_list_item(approved_item),
            "message": f"已通过并收录投稿 #{issue_number}",
        }

    def reject_pending_issue(self, issue_number: int, github_token: str) -> Dict[str, Any]:
        self._resolve_review_context(github_token, require_review=True)
        issue_payload = self._fetch_github_issue(issue_number, github_token)
        if not self._is_marketplace_issue(issue_payload):
            raise ValueError(f"GitHub issue #{issue_number} 不是待审核投稿")

        self._close_github_issue_with_fallback(issue_number, github_token)
        self._cached_manifest = None
        self._cached_at = 0.0

        return {
            "success": True,
            "action": "reject",
            "issue_number": int(issue_number),
            "message": f"已拒绝并关闭投稿 #{issue_number}",
        }

    def remove_item(self, item_id: str, github_token: str) -> Dict[str, Any]:
        context = self._resolve_review_context(github_token, require_review=False)
        actor_login = str(context.get("login") or "").strip()
        if not actor_login:
            raise ValueError("无法识别当前 GitHub 账号，请确认 token 可读取当前用户信息")

        manifest = self._load_manifest(force_refresh=True)
        target = next(
            (copy.deepcopy(entry) for entry in manifest.get("items", []) if str(entry.get("id") or "").strip() == str(item_id or "").strip()),
            None,
        )
        if not target:
            raise KeyError(f"未找到市场项目: {item_id}")

        if not self._can_manage_item(target, context, github_token):
            raise ValueError("只有投稿作者本人或仓库管理员可以撤回 / 下架这个项目")

        if str(target.get("review_status") or "") == "pending":
            issue_number = self._coerce_int(target.get("issue_number"))
            if issue_number <= 0:
                raise ValueError("待审核投稿缺少 issue 编号，无法撤回")
            self._close_github_issue_with_fallback(issue_number, github_token)
            self._cached_manifest = None
            self._cached_at = 0.0
            return {
                "success": True,
                "action": "withdraw",
                "item_id": str(item_id or ""),
                "message": f"已撤回投稿 #{issue_number}",
            }

        remote_manifest, manifest_sha = self._load_remote_manifest_for_review(github_token)
        existing_items = list(remote_manifest.get("items", []))
        next_items = [
            entry for entry in existing_items
            if str((entry or {}).get("id") or "").strip() != str(item_id or "").strip()
        ]
        if len(next_items) == len(existing_items):
            raise KeyError(f"远程市场里没有找到项目: {item_id}")

        remote_manifest["items"] = next_items
        self._save_remote_manifest_for_review_with_fallback(
            remote_manifest,
            manifest_sha,
            github_token,
            message=f"Remove marketplace item {item_id}",
        )

        self._cached_manifest = None
        self._cached_at = 0.0
        try:
            self._save_remote_cache_manifest(remote_manifest)
        except Exception as exc:
            logger.warning(f"[marketplace] 下架后写入本地缓存失败: {exc}")

        return {
            "success": True,
            "action": "remove",
            "item_id": str(item_id or ""),
            "message": "已从公共市场下架这个项目",
        }

    def _iter_mutation_tokens(self, github_token: str) -> List[str]:
        tokens: List[str] = []
        for candidate in [github_token, AppConfig.get_marketplace_github_token()]:
            token = str(candidate or "").strip()
            if token and token not in tokens:
                tokens.append(token)
        return tokens

    def _save_remote_manifest_for_review_with_fallback(
        self,
        manifest: Dict[str, Any],
        manifest_sha: str,
        github_token: str,
        message: str,
    ) -> None:
        last_error: Optional[Exception] = None
        for token in self._iter_mutation_tokens(github_token):
            try:
                self._save_remote_manifest_for_review(
                    manifest,
                    manifest_sha,
                    token,
                    message=message,
                )
                return
            except Exception as exc:
                last_error = exc

        if last_error:
            raise last_error
        raise ValueError("当前没有可用于写入公共市场的 GitHub Token")

    def _close_github_issue_with_fallback(self, issue_number: int, github_token: str) -> None:
        last_error: Optional[Exception] = None
        for token in self._iter_mutation_tokens(github_token):
            try:
                self._close_github_issue(issue_number, token)
                return
            except Exception as exc:
                last_error = exc

        if last_error:
            raise last_error
        raise ValueError("当前没有可用于关闭投稿 issue 的 GitHub Token")

    def _can_manage_item(self, item: Dict[str, Any], context: Dict[str, Any], github_token: str) -> bool:
        if bool(context.get("can_review")):
            return True

        actor_login = str(context.get("login") or "").strip().lower()
        if not actor_login:
            return False

        submitted_by = str((item or {}).get("submitted_by") or "").strip().lower()
        if submitted_by and submitted_by == actor_login:
            return True

        issue_number = self._coerce_int((item or {}).get("issue_number"))
        if issue_number <= 0:
            return False

        try:
            issue_payload = self._fetch_github_issue(issue_number, github_token)
        except Exception:
            return False

        issue_submitter = self._extract_issue_submitter(issue_payload).strip().lower()
        return bool(issue_submitter) and issue_submitter == actor_login

    def _resolve_review_context(self, github_token: str, require_review: bool = False) -> Dict[str, Any]:
        token = str(github_token or "").strip()
        if not token:
            raise ValueError("请先提供 GitHub Token")

        repo = str(AppConfig.get_marketplace_repo() or "").strip()
        if not repo or "/" not in repo:
            raise ValueError("当前未配置公共市场 GitHub 仓库")

        repo_payload = self._fetch_json_payload(f"https://api.github.com/repos/{repo}", github_token=token)
        if not isinstance(repo_payload, dict):
            raise ValueError("GitHub 仓库信息读取失败")

        permissions = repo_payload.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
        normalized_permissions = {
            "admin": bool(permissions.get("admin")),
            "maintain": bool(permissions.get("maintain")),
            "push": bool(permissions.get("push")),
            "triage": bool(permissions.get("triage")),
            "pull": bool(permissions.get("pull")),
        }
        role_name = str(repo_payload.get("role_name") or "").strip().lower()
        can_review = bool(
            normalized_permissions.get("admin")
            or normalized_permissions.get("maintain")
            or normalized_permissions.get("push")
            or role_name in {"admin", "maintain", "write"}
        )
        permission_label = self._format_review_permission_label(normalized_permissions, role_name)

        login = ""
        try:
            user_payload = self._fetch_json_payload("https://api.github.com/user", github_token=token)
            if isinstance(user_payload, dict):
                login = str(user_payload.get("login") or "").strip()
        except Exception:
            login = ""

        context = {
            "repo": repo,
            "repo_url": AppConfig.get_marketplace_repo_url(),
            "branch": AppConfig.get_marketplace_branch(),
            "index_path": AppConfig.get_marketplace_index_path(),
            "permissions": normalized_permissions,
            "role_name": role_name,
            "permission_label": permission_label,
            "can_review": can_review,
            "login": login,
        }
        if require_review and not can_review:
            raise ValueError("当前 GitHub 账号没有这个仓库的维护权限，无法审核投稿")
        return context

    def _format_review_permission_label(self, permissions: Dict[str, bool], role_name: str) -> str:
        if permissions.get("admin") or role_name == "admin":
            return "管理员"
        if permissions.get("maintain") or role_name == "maintain":
            return "维护者"
        if permissions.get("push") or role_name == "write":
            return "可写成员"
        if permissions.get("triage") or role_name == "triage":
            return "分诊成员"
        if permissions.get("pull") or role_name == "read":
            return "只读成员"
        return "未知权限"

    def _extract_issue_submitter(self, issue: Any) -> str:
        if not isinstance(issue, dict):
            return ""

        user = issue.get("user")
        if isinstance(user, dict):
            login = str(user.get("login") or user.get("name") or "").strip()
            if login:
                return login
        elif isinstance(user, list):
            for entry in user:
                if not isinstance(entry, dict):
                    continue
                login = str(entry.get("login") or entry.get("name") or "").strip()
                if login:
                    return login
        elif isinstance(user, str):
            login = str(user).strip()
            if login:
                return login

        author = issue.get("author")
        if isinstance(author, dict):
            name = str(author.get("name") or author.get("login") or "").strip()
            if name:
                return name
        elif isinstance(author, list):
            for entry in author:
                if isinstance(entry, dict):
                    name = str(entry.get("name") or entry.get("login") or "").strip()
                else:
                    name = str(entry or "").strip()
                if name:
                    return name
        elif isinstance(author, str):
            name = str(author).strip()
            if name:
                return name

        return ""

    def _should_replace_display_author(self, author_name: str) -> bool:
        normalized = str(author_name or "").strip().lower()
        return normalized in {"", "本地投稿", "社区贡献", "local", "anonymous", "匿名"}

    def _apply_issue_submitter_to_item_payload(self, item_payload: Dict[str, Any], issue: Any) -> Dict[str, Any]:
        payload = copy.deepcopy(item_payload)
        submitter = self._extract_issue_submitter(issue)
        if not submitter:
            return payload

        payload["submitted_by"] = submitter
        if self._should_replace_display_author(payload.get("author")):
            payload["author"] = submitter
        return payload

    def _fetch_github_issue(self, issue_number: int, github_token: str) -> Dict[str, Any]:
        repo = str(AppConfig.get_marketplace_repo() or "").strip()
        if not repo or "/" not in repo:
            raise ValueError("当前未配置公共市场 GitHub 仓库")

        payload = self._fetch_json_payload(
            f"https://api.github.com/repos/{repo}/issues/{int(issue_number)}",
            github_token=github_token,
        )
        if not isinstance(payload, dict):
            raise ValueError(f"GitHub issue #{issue_number} 读取失败")
        return payload

    def _build_approved_item_from_issue(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        item_payload, json_payload = self._extract_issue_item_payload(issue)
        if not isinstance(item_payload, dict):
            raise ValueError("待审核投稿内容无法解析")
        if json_payload is None:
            raise ValueError("该投稿缺少可导入的 JSON 内容，暂时不能直接收录")
        item_payload = self._apply_issue_submitter_to_item_payload(item_payload, issue)

        issue_number = self._coerce_int(issue.get("number"))
        issue_title = str(issue.get("title") or "").strip()
        issue_url = str(issue.get("html_url") or "").strip()
        item_payload["downloads"] = self._coerce_int(item_payload.get("downloads"))
        item_payload["stars"] = self._coerce_int(item_payload.get("stars"))
        item_payload["updated_at"] = str(issue.get("updated_at") or issue.get("created_at") or "")
        item_payload["repo_url"] = issue_url

        normalized = self._normalize_item(item_payload)
        if not normalized:
            raise ValueError("待审核投稿规范化失败")

        normalized["review_status"] = ""
        normalized["review_label"] = ""
        normalized["issue_number"] = issue_number
        normalized["issue_url"] = issue_url
        normalized["issue_title"] = issue_title
        normalized["import_disabled"] = False
        return normalized

    def _extract_issue_item_payload(self, issue: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        issue_title = str(issue.get("title") or "").strip()
        issue_body = str(issue.get("body") or "")
        json_payload = self._extract_issue_json_payload(issue_body)
        if isinstance(json_payload, dict):
            return copy.deepcopy(json_payload), json_payload
        metadata_payload = self._extract_issue_metadata_payload(issue_title, issue_body)
        return metadata_payload if isinstance(metadata_payload, dict) else None, None

    def _filter_pending_items_against_manifest(
        self,
        manifest_items: List[Dict[str, Any]],
        pending_items: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        seen_issue_numbers = {
            self._coerce_int((item or {}).get("issue_number"))
            for item in (manifest_items or [])
            if self._coerce_int((item or {}).get("issue_number")) > 0
        }
        filtered: List[Dict[str, Any]] = []
        for item in pending_items or []:
            issue_number = self._coerce_int((item or {}).get("issue_number"))
            if issue_number > 0 and issue_number in seen_issue_numbers:
                continue
            filtered.append(copy.deepcopy(item))
        return filtered

    def _enrich_item_submitters(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        enriched_items: List[Dict[str, Any]] = []
        for raw_item in items or []:
            item = copy.deepcopy(raw_item)
            if not self._needs_submitter_enrichment(item):
                enriched_items.append(item)
                continue

            issue_payload = self._fetch_issue_for_submitter_enrichment(item)
            if isinstance(issue_payload, dict):
                item = self._apply_issue_submitter_to_item_payload(item, issue_payload)
            enriched_items.append(item)
        return enriched_items

    def _needs_submitter_enrichment(self, item: Dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        if str(item.get("submitted_by") or "").strip():
            return False
        return self._coerce_int(item.get("issue_number")) > 0

    def _fetch_issue_for_submitter_enrichment(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        issue_number = self._coerce_int((item or {}).get("issue_number"))
        issue_url = str((item or {}).get("issue_url") or (item or {}).get("repo_url") or "").strip()

        if issue_number > 0:
            try:
                return self._fetch_github_issue(issue_number, "")
            except Exception:
                pass

        if issue_url:
            try:
                return self._fetch_issue_payload_from_web(issue_url)
            except Exception:
                return None
        return None

    def _load_remote_manifest_for_review(self, github_token: str) -> tuple[Dict[str, Any], str]:
        return self._load_remote_manifest_from_repo_api(github_token=github_token)

    def _load_remote_manifest_from_repo_api(self, github_token: str = "") -> tuple[Dict[str, Any], str]:
        repo = str(AppConfig.get_marketplace_repo() or "").strip()
        branch = AppConfig.get_marketplace_branch()
        index_path = AppConfig.get_marketplace_index_path()
        if not repo or "/" not in repo:
            raise ValueError("当前未配置公共市场 GitHub 仓库")

        payload = self._fetch_json_payload(
            f"https://api.github.com/repos/{repo}/contents/{quote(index_path)}?ref={quote(branch)}",
            github_token=github_token,
        )
        if not isinstance(payload, dict):
            raise ValueError("公共市场索引文件读取失败")

        content_text = self._decode_github_file_content(payload)
        manifest = self._normalize_manifest(json.loads(content_text))
        manifest.setdefault("source_name", "公共插件市场")
        manifest.setdefault("source_url", AppConfig.get_marketplace_index_url())
        manifest.setdefault("repo_url", AppConfig.get_marketplace_repo_url())
        manifest.setdefault("upload_url", AppConfig.get_marketplace_upload_url())
        return manifest, str(payload.get("sha") or "")

    def _save_remote_manifest_for_review(
        self,
        manifest: Dict[str, Any],
        manifest_sha: str,
        github_token: str,
        message: str,
    ) -> None:
        repo = str(AppConfig.get_marketplace_repo() or "").strip()
        branch = AppConfig.get_marketplace_branch()
        index_path = AppConfig.get_marketplace_index_path()
        if not repo or "/" not in repo:
            raise ValueError("当前未配置公共市场 GitHub 仓库")

        normalized_manifest = {
            "source_name": str(manifest.get("source_name") or "公共插件市场"),
            "source_url": str(manifest.get("source_url") or AppConfig.get_marketplace_index_url()),
            "repo_url": str(manifest.get("repo_url") or AppConfig.get_marketplace_repo_url()),
            "upload_url": str(manifest.get("upload_url") or AppConfig.get_marketplace_upload_url()),
            "items": copy.deepcopy(manifest.get("items", [])),
        }
        content = json.dumps(normalized_manifest, ensure_ascii=False, indent=2) + "\n"
        body = {
            "message": str(message or "Update marketplace manifest"),
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if str(manifest_sha or "").strip():
            body["sha"] = str(manifest_sha).strip()

        self._fetch_json_payload(
            f"https://api.github.com/repos/{repo}/contents/{quote(index_path)}",
            github_token=github_token,
            method="PUT",
            payload=body,
        )

    def _decode_github_file_content(self, payload: Dict[str, Any]) -> str:
        content = str(payload.get("content") or "").replace("\n", "")
        if not content:
            raise ValueError("GitHub 索引文件内容为空")
        encoding = str(payload.get("encoding") or "").strip().lower()
        if encoding and encoding != "base64":
            raise ValueError(f"暂不支持的 GitHub 文件编码: {encoding}")
        try:
            return base64.b64decode(content).decode("utf-8")
        except Exception as exc:
            raise ValueError(f"GitHub 索引文件解码失败: {exc}") from exc

    def _close_github_issue(self, issue_number: int, github_token: str) -> None:
        repo = str(AppConfig.get_marketplace_repo() or "").strip()
        if not repo or "/" not in repo:
            raise ValueError("当前未配置公共市场 GitHub 仓库")
        self._fetch_json_payload(
            f"https://api.github.com/repos/{repo}/issues/{int(issue_number)}",
            github_token=github_token,
            method="PATCH",
            payload={"state": "closed"},
        )

    def _fetch_pending_issue_items(self) -> List[Dict[str, Any]]:
        issues_api_url = AppConfig.get_marketplace_issues_api_url()
        last_error: Optional[Exception] = None

        if issues_api_url:
            try:
                payload = self._fetch_json_payload(issues_api_url)
                if not isinstance(payload, list):
                    raise ValueError("GitHub issues API 必须返回数组")
                return self._normalize_pending_issue_list(payload)
            except Exception as exc:
                last_error = exc
                logger.warning(f"[marketplace] GitHub issues API 不可用，准备切换公开页面抓取: {exc}")

        try:
            return self._fetch_pending_issue_items_from_web()
        except Exception:
            if last_error:
                raise last_error
            raise

    def _normalize_pending_issue_list(self, issues: List[Any]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for issue in issues:
            normalized = self._normalize_pending_issue(issue)
            if normalized:
                items.append(normalized)
        return items

    def _fetch_pending_issue_items_from_web(self) -> List[Dict[str, Any]]:
        issues_web_url = AppConfig.get_marketplace_issues_web_url()
        repo_url = AppConfig.get_marketplace_repo_url()
        if not issues_web_url or not repo_url:
            return []

        issue_urls = self._collect_issue_urls_from_web(issues_web_url, repo_url)
        items: List[Dict[str, Any]] = []
        for issue_url in issue_urls:
            issue_payload = self._fetch_issue_payload_from_web(issue_url)
            normalized = self._normalize_pending_issue(issue_payload)
            if normalized:
                items.append(normalized)
        return items

    def _collect_issue_urls_from_web(self, issues_web_url: str, repo_url: str) -> List[str]:
        repo_path = urlsplit(repo_url).path.strip("/")
        if not repo_path:
            return []

        collected: List[str] = []
        seen_urls = set()
        next_url = issues_web_url
        page_count = 0

        while next_url and page_count < 4:
            html_text = self._fetch_text_payload(next_url, accept="text/html,application/xhtml+xml")
            for match in re.finditer(rf'href="/{re.escape(repo_path)}/issues/(\d+)"', html_text):
                issue_number = str(match.group(1) or "").strip()
                if not issue_number:
                    continue
                issue_url = f"{repo_url}/issues/{issue_number}"
                if issue_url in seen_urls:
                    continue
                seen_urls.add(issue_url)
                collected.append(issue_url)

            next_url = self._extract_next_page_url(html_text, current_url=next_url)
            page_count += 1

        return collected

    def _extract_next_page_url(self, html_text: str, current_url: str) -> str:
        match = re.search(r'<a[^>]+href="([^"]+)"[^>]+rel="next"', str(html_text or ""), re.IGNORECASE)
        if not match:
            return ""
        return urljoin(current_url, html.unescape(str(match.group(1) or "").strip()))

    def _fetch_issue_payload_from_web(self, issue_url: str) -> Dict[str, Any]:
        html_text = self._fetch_text_payload(issue_url, accept="text/html,application/xhtml+xml")
        script_match = re.search(
            r'<script type="application/ld\+json">\s*(\{.*?"@type":"DiscussionForumPosting".*?\})\s*</script>',
            html_text,
            re.DOTALL,
        )
        if not script_match:
            script_match = re.search(
                r'<script type="application/ld\+json">\s*(\{.*?"articleBody":.*?\})\s*</script>',
                html_text,
                re.DOTALL,
            )
        if not script_match:
            raise ValueError("GitHub issue 页面缺少可解析的 JSON-LD 数据")

        try:
            metadata = json.loads(html.unescape(str(script_match.group(1) or "")))
        except Exception as exc:
            raise ValueError(f"GitHub issue 页面 JSON-LD 解析失败: {exc}") from exc

        issue_number = self._coerce_int(re.search(r"/issues/(\d+)", issue_url).group(1) if re.search(r"/issues/(\d+)", issue_url) else 0)
        issue_body = str(metadata.get("articleBody") or "")
        issue_payload = {
            "number": issue_number,
            "state": "open",
            "title": str(metadata.get("headline") or ""),
            "body": issue_body,
            "html_url": issue_url,
            "updated_at": str(metadata.get("dateModified") or metadata.get("datePublished") or ""),
            "created_at": str(metadata.get("datePublished") or ""),
            "labels": [],
        }
        submitter = self._extract_issue_submitter({"author": metadata.get("author")})
        if submitter:
            issue_payload["user"] = {"login": submitter}
        return issue_payload

    def _normalize_pending_issue(self, issue: Any) -> Optional[Dict[str, Any]]:
        if not self._is_marketplace_issue(issue):
            return None

        issue_number = self._coerce_int(issue.get("number"))
        issue_title = str(issue.get("title") or "").strip()
        issue_url = str(issue.get("html_url") or "")
        item_payload, json_payload = self._extract_issue_item_payload(issue)

        if not isinstance(item_payload, dict):
            return None

        item_payload = self._apply_issue_submitter_to_item_payload(item_payload, issue)
        item_payload["id"] = f"{self.ISSUE_ID_PREFIX}{issue_number}"
        item_payload["downloads"] = 0
        item_payload["stars"] = 0
        item_payload["updated_at"] = str(issue.get("updated_at") or issue.get("created_at") or "")
        item_payload["repo_url"] = issue_url
        normalized = self._normalize_item(item_payload)
        if not normalized:
            return None

        normalized["review_status"] = "pending"
        normalized["review_label"] = "待审核"
        normalized["issue_number"] = issue_number
        normalized["issue_url"] = issue_url
        normalized["issue_title"] = issue_title
        normalized["downloads"] = 0
        normalized["stars"] = 0
        if json_payload is None:
            normalized["import_disabled"] = True
            normalized["summary"] = normalized.get("summary") or "这个投稿缺少可导入的 JSON 代码块，请去 GitHub issue 里补充。"
        return normalized

    def _is_marketplace_issue(self, issue: Any) -> bool:
        if not isinstance(issue, dict):
            return False
        if issue.get("pull_request"):
            return False
        if str(issue.get("state") or "").lower() != "open":
            return False

        title = str(issue.get("title") or "").strip()
        body = str(issue.get("body") or "")
        labels = [
            str((label or {}).get("name") or "").strip().lower()
            for label in issue.get("labels", [])
            if isinstance(label, dict)
        ]
        if "marketplace" in labels:
            return True
        if title.startswith(self.ISSUE_TITLE_PREFIX):
            return True
        return self.ISSUE_MARKER in body

    def _extract_issue_json_payload(self, body: str) -> Optional[Dict[str, Any]]:
        text = str(body or "")
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.IGNORECASE | re.DOTALL)
        if not match:
            match = re.search(r"```\s*(\{.*?\})\s*```", text, re.DOTALL)
        if not match:
            return None

        try:
            payload = json.loads(match.group(1))
        except Exception as exc:
            logger.warning(f"[marketplace] 待审核投稿 JSON 解析失败: {exc}")
            return None
        return payload if isinstance(payload, dict) else None

    def _extract_issue_metadata_payload(self, title: str, body: str) -> Dict[str, Any]:
        cleaned_title = str(title or "").strip()
        if cleaned_title.startswith(self.ISSUE_TITLE_PREFIX):
            cleaned_title = cleaned_title[len(self.ISSUE_TITLE_PREFIX):].strip()

        metadata: Dict[str, str] = {}
        for line in str(body or "").splitlines():
            match = re.match(r"^\s*-\s*([^:：]+)\s*[:：]\s*(.+?)\s*$", line)
            if not match:
                continue
            metadata[str(match.group(1)).strip()] = str(match.group(2)).strip()

        summary = ""
        summary_match = re.search(r"##\s*简介\s*(.+?)(?:\n##|\Z)", str(body or ""), re.DOTALL)
        if summary_match:
            summary = str(summary_match.group(1)).strip()

        item_type_text = metadata.get("类型", "站点配置")
        item_type = "command_bundle" if "命令" in item_type_text else "site_config"
        tags = [
            part.strip()
            for part in re.split(r"[，,]", metadata.get("标签", ""))
            if part.strip()
        ]

        return {
            "item_type": item_type,
            "title": metadata.get("标题") or cleaned_title or "待审核投稿",
            "summary": summary,
            "author": metadata.get("作者") or self.DEFAULT_AUTHOR,
            "category": metadata.get("分类") or metadata.get("站点") or (
                self.DEFAULT_COMMAND_CATEGORY if item_type == "command_bundle" else self.DEFAULT_SITE_CATEGORY
            ),
            "site_domain": metadata.get("站点", ""),
            "preset_name": metadata.get("预设", ""),
            "version": metadata.get("版本", ""),
            "compatibility": metadata.get("兼容", ""),
            "tags": tags,
        }

    def _normalize_item(self, raw_item: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_item, dict):
            return None

        item_type = str(raw_item.get("item_type") or self.DEFAULT_TYPE).strip() or self.DEFAULT_TYPE
        site_domain = str(raw_item.get("site_domain") or raw_item.get("domain") or "").strip()
        preset_name = str(raw_item.get("preset_name") or "主预设").strip() or "主预设"
        base_id = str(raw_item.get("id") or "").strip()
        if not base_id:
            suffix = site_domain or item_type
            base_id = f"{item_type}-{suffix}-{preset_name}"
        item_id = re.sub(r"[^a-zA-Z0-9._-\u4e00-\u9fff]+", "-", base_id).strip("-") or f"market-{int(time.time() * 1000)}"

        raw_tags = raw_item.get("tags")
        tags = raw_tags if isinstance(raw_tags, list) else []
        category = str(raw_item.get("category") or "").strip()
        if not category:
            category = site_domain if item_type == "site_config" and site_domain else (
                self.DEFAULT_COMMAND_CATEGORY if item_type == "command_bundle" else self.DEFAULT_SITE_CATEGORY
            )

        item = {
            "id": item_id,
            "item_type": item_type,
            "name": str(raw_item.get("name") or raw_item.get("title") or item_id),
            "summary": str(raw_item.get("summary") or raw_item.get("description") or ""),
            "author": str(raw_item.get("author") or self.DEFAULT_AUTHOR),
            "submitted_by": str(raw_item.get("submitted_by") or "").strip(),
            "category": category,
            "site_domain": site_domain,
            "domain": site_domain,
            "preset_name": preset_name,
            "downloads": self._coerce_int(raw_item.get("downloads")),
            "stars": self._coerce_int(raw_item.get("stars")),
            "updated_at": str(raw_item.get("updated_at") or ""),
            "version": str(raw_item.get("version") or ""),
            "compatibility": str(raw_item.get("compatibility") or ""),
            "repo_url": str(raw_item.get("repo_url") or ""),
            "package_url": str(raw_item.get("package_url") or raw_item.get("download_url") or ""),
            "review_status": str(raw_item.get("review_status") or ""),
            "review_label": str(raw_item.get("review_label") or ""),
            "issue_number": self._coerce_int(raw_item.get("issue_number")),
            "issue_url": str(raw_item.get("issue_url") or ""),
            "issue_title": str(raw_item.get("issue_title") or ""),
            "import_disabled": bool(raw_item.get("import_disabled")),
            "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
        }

        if isinstance(raw_item.get("site_config"), dict):
            item["site_config"] = copy.deepcopy(raw_item["site_config"])
        if isinstance(raw_item.get("command_bundle"), dict):
            item["command_bundle"] = copy.deepcopy(raw_item["command_bundle"])

        return item

    def _build_public_submission_response(self, normalized: Dict[str, Any]) -> Dict[str, Any]:
        submission_url = self._build_public_submission_url(normalized)
        return {
            "mode": "external",
            "item": self._to_list_item(normalized),
            "submission_url": submission_url,
            "message": "已生成 GitHub 公共投稿页，页面基础信息已自动填写，请把已复制的 JSON 代码块粘贴到“预览 JSON”下面。",
        }

    def _build_public_submission_url(self, item: Dict[str, Any]) -> str:
        base_url = str(AppConfig.get_marketplace_upload_url() or "").strip()
        if not base_url:
            raise ValueError("当前未配置公共投稿入口")

        title = f"[市场投稿] {str(item.get('name') or '未命名项目').strip()}"
        body = self._build_public_submission_body(item)
        return self._append_query_params(base_url, {
            "title": title,
            "body": body,
        })

    def _build_public_submission_body(self, item: Dict[str, Any]) -> str:
        item_type = str(item.get("item_type") or self.DEFAULT_TYPE).strip()
        item_type_label = "命令系统" if item_type == "command_bundle" else "站点配置"
        tags = item.get("tags") or []

        lines = [
            self.ISSUE_MARKER,
            "",
            "## 基本信息",
            f"- 类型: {item_type_label}",
            f"- 标题: {item.get('name') or ''}",
            f"- 作者: {item.get('author') or self.DEFAULT_AUTHOR}",
            f"- 分类: {item.get('category') or ''}",
        ]

        if item.get("site_domain"):
            lines.append(f"- 站点: {item.get('site_domain')}")
        if item.get("preset_name"):
            lines.append(f"- 预设: {item.get('preset_name')}")
        if item.get("version"):
            lines.append(f"- 版本: {item.get('version')}")
        if item.get("compatibility"):
            lines.append(f"- 兼容: {item.get('compatibility')}")
        if tags:
            lines.append(f"- 标签: {', '.join(str(tag) for tag in tags)}")

        lines.extend([
            "",
            "## 简介",
            str(item.get("summary") or "请补充简介").strip(),
            "",
            "## 预览 JSON",
            "请把应用里已经复制的 JSON 代码块粘贴到这里，不需要重复粘贴上面的基本信息和简介。",
        ])
        return "\n".join(lines)

    @staticmethod
    def _append_query_params(url: str, params: Dict[str, Any]) -> str:
        parts = urlsplit(str(url or "").strip())
        existing = dict(parse_qsl(parts.query, keep_blank_values=True))
        for key, value in (params or {}).items():
            if value is None:
                continue
            existing[str(key)] = str(value)
        return urlunsplit((
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(existing),
            parts.fragment,
        ))

    def _to_list_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        result = copy.deepcopy(item)
        result.pop("site_config", None)
        result.pop("command_bundle", None)
        result.pop("package_url", None)
        return result

    def _resolve_site_config(self, item: Dict[str, Any]) -> Dict[str, Any]:
        embedded = item.get("site_config")
        if isinstance(embedded, dict):
            return self._normalize_site_config_payload(
                embedded,
                domain_hint=item.get("site_domain"),
                preset_name_hint=item.get("preset_name"),
            )

        package_url = str(item.get("package_url") or "").strip()
        if package_url:
            payload = self._fetch_json(package_url)
            return self._normalize_site_config_payload(
                payload,
                domain_hint=item.get("site_domain"),
                preset_name_hint=item.get("preset_name"),
            )

        site_domain = str(item.get("site_domain") or "").strip()
        if not site_domain:
            raise ValueError("站点配置项目缺少站点域名")

        site = copy.deepcopy(config_engine.sites.get(site_domain))
        if not isinstance(site, dict):
            raise ValueError(f"本地没有找到站点配置: {site_domain}")

        preset_name = str(item.get("preset_name") or "").strip()
        if preset_name:
            presets = site.get("presets") or {}
            preset_data = presets.get(preset_name)
            if not isinstance(preset_data, dict):
                raise ValueError(f"本地没有找到预设: {site_domain} / {preset_name}")
            site["presets"] = {preset_name: preset_data}
            site["default_preset"] = preset_name

        return {site_domain: site}

    def _resolve_command_bundle(self, item: Dict[str, Any]) -> Dict[str, Any]:
        embedded = item.get("command_bundle")
        if isinstance(embedded, dict):
            return self._normalize_command_bundle(embedded)

        package_url = str(item.get("package_url") or "").strip()
        if package_url:
            payload = self._fetch_json(package_url)
            return self._normalize_command_bundle(payload)

        raise ValueError("命令包缺少可导入内容")

    def _normalize_site_config_payload(
        self,
        payload: Dict[str, Any],
        domain_hint: Optional[str] = None,
        preset_name_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("站点配置包必须是对象")

        for key in ("site_config", "config", "payload"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                return self._normalize_site_config_payload(
                    nested,
                    domain_hint=domain_hint,
                    preset_name_hint=preset_name_hint,
                )

        if "presets" in payload or "selectors" in payload or "workflow" in payload:
            if not domain_hint:
                raise ValueError("单站点配置包缺少站点域名")

            if "presets" in payload:
                return {str(domain_hint): copy.deepcopy(payload)}

            preset_name = str(payload.get("preset_name") or preset_name_hint or "主预设").strip() or "主预设"
            preset_payload = {
                key: copy.deepcopy(value)
                for key, value in payload.items()
                if key != "preset_name"
            }
            return {
                str(domain_hint): {
                    "default_preset": preset_name,
                    "presets": {
                        preset_name: preset_payload
                    }
                }
            }

        if all(isinstance(value, dict) for value in payload.values()):
            return copy.deepcopy(payload)

        raise ValueError("无法识别站点配置包结构")

    def _normalize_command_bundle(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("命令包必须是对象")

        commands = payload.get("commands")
        if isinstance(commands, list):
            normalized_commands = [copy.deepcopy(item) for item in commands if isinstance(item, dict)]
            return {
                "commands": normalized_commands,
                "group_name": str(payload.get("group_name") or ""),
            }

        nested = payload.get("command_bundle")
        if isinstance(nested, dict):
            return self._normalize_command_bundle(nested)

        raise ValueError("命令包必须包含 commands 数组")

    def _normalize_submission(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("投稿内容必须是对象")

        item_type = str(payload.get("item_type") or self.DEFAULT_TYPE).strip() or self.DEFAULT_TYPE
        title = str(payload.get("title") or payload.get("name") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        author = str(payload.get("author") or "本地投稿").strip() or "本地投稿"
        compatibility = str(payload.get("compatibility") or "").strip()
        version = str(payload.get("version") or "1.0.0").strip() or "1.0.0"
        preset_name = str(payload.get("preset_name") or "主预设").strip() or "主预设"

        if not title:
            raise ValueError("标题不能为空")
        if not summary:
            raise ValueError("简介不能为空")

        raw_tags = payload.get("tags")
        tags = raw_tags if isinstance(raw_tags, list) else []
        normalized_tags = [str(tag).strip() for tag in tags if str(tag).strip()]

        item_id = self._build_submission_id(title)
        updated_at = datetime.now().strftime("%Y-%m-%d")

        item = {
            "id": item_id,
            "item_type": item_type,
            "name": title,
            "summary": summary,
            "author": author,
            "downloads": 0,
            "stars": 0,
            "updated_at": updated_at,
            "version": version,
            "compatibility": compatibility,
            "tags": normalized_tags,
            "repo_url": "",
            "package_url": "",
        }

        if item_type == "command_bundle":
            bundle = self._normalize_command_bundle(payload.get("command_bundle"))
            item["category"] = str(payload.get("category") or self.DEFAULT_COMMAND_CATEGORY).strip() or self.DEFAULT_COMMAND_CATEGORY
            item["site_domain"] = ""
            item["domain"] = ""
            item["preset_name"] = ""
            item["command_bundle"] = bundle
            return item

        site_domain = str(payload.get("site_domain") or payload.get("domain") or "").strip()
        if not site_domain:
            raise ValueError("站点配置投稿必须填写站点域名")

        site_config = self._normalize_site_config_payload(
            payload.get("site_config"),
            domain_hint=site_domain,
            preset_name_hint=preset_name,
        )
        item["category"] = str(payload.get("category") or site_domain).strip() or site_domain
        item["site_domain"] = site_domain
        item["domain"] = site_domain
        item["preset_name"] = preset_name
        item["site_config"] = site_config
        return item

    @staticmethod
    def _build_submission_id(title: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-\u4e00-\u9fff]+", "-", str(title or "").strip().lower()).strip("-")
        if not slug:
            slug = "market-item"
        return f"{slug}-{int(time.time() * 1000)}"

    @staticmethod
    def _coerce_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except Exception:
            return 0


marketplace_service = MarketplaceService()
