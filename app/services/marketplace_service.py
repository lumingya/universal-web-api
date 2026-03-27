"""
app/services/marketplace_service.py - 配置市场服务
"""

from __future__ import annotations

import copy
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
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

    def __init__(self):
        self._cached_manifest: Optional[Dict[str, Any]] = None
        self._cached_at = 0.0

    def list_catalog(self, force_refresh: bool = False) -> Dict[str, Any]:
        manifest = self._load_manifest(force_refresh=force_refresh)
        items = [self._to_list_item(item) for item in manifest.get("items", [])]
        items.sort(key=lambda item: (-int(item.get("downloads", 0) or 0), str(item.get("name") or "")))

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
        warning = ""

        if remote_url:
            try:
                remote_manifest = self._normalize_manifest(self._fetch_json(remote_url))
                if local_overlay_enabled and local_manifest.get("items"):
                    manifest = self._merge_manifests(remote_manifest, local_manifest)
                    manifest["source_mode"] = "hybrid"
                else:
                    manifest = remote_manifest
                    manifest["source_mode"] = "remote"
                manifest.setdefault("source_name", remote_manifest.get("source_name") or "GitHub 配置市场")
                manifest.setdefault("source_url", remote_url)
            except Exception as exc:
                warning = f"GitHub 索引读取失败，已回退到本地市场: {exc}"
                logger.warning(f"[marketplace] 远程索引加载失败: {exc}")
                manifest = local_manifest
                manifest["source_mode"] = "local"
        else:
            manifest = local_manifest
            manifest["source_mode"] = "local"

        default_source_name = "公共插件市场" if remote_url else "本地配置市场"
        manifest.setdefault("source_name", default_source_name)
        manifest.setdefault("source_url", remote_url if remote_url else "")
        manifest.setdefault("repo_url", repo_url)
        manifest.setdefault("upload_url", upload_url)
        manifest["warning"] = warning or str(manifest.get("warning") or "")
        manifest["submit_mode"] = submit_mode
        manifest["submit_label"] = "投稿到公共市场" if submit_mode != "local" and upload_url else "投稿上传"
        manifest["submit_help"] = (
            "投稿会打开 GitHub 公共页面，完整预览 JSON 会先复制到剪贴板，打开后直接粘贴即可。"
            if submit_mode != "local" and upload_url
            else "投稿会直接写入当前实例的本地市场清单。"
        )
        manifest["submit_target"] = "GitHub 公共投稿" if submit_mode != "local" and upload_url else "本地市场"

        self._cached_manifest = copy.deepcopy(manifest)
        self._cached_at = now_ts
        return copy.deepcopy(manifest)

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
        merged_items: List[Dict[str, Any]] = []
        seen_ids = set()
        for source in (local_manifest.get("items", []), remote_manifest.get("items", [])):
            for item in source:
                item_id = str(item.get("id") or "").strip()
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                merged_items.append(copy.deepcopy(item))

        return {
            "source_name": remote_manifest.get("source_name") or local_manifest.get("source_name") or "配置市场",
            "source_url": remote_manifest.get("source_url") or local_manifest.get("source_url") or "",
            "repo_url": remote_manifest.get("repo_url") or local_manifest.get("repo_url") or "",
            "upload_url": local_manifest.get("upload_url") or remote_manifest.get("upload_url") or "",
            "warning": local_manifest.get("warning") or remote_manifest.get("warning") or "",
            "items": merged_items,
        }

    def _fetch_json(self, url: str) -> Dict[str, Any]:
        request = Request(
            str(url or "").strip(),
            headers={
                "Accept": "application/json",
                "User-Agent": "Universal-Web-to-API-Marketplace/1.0",
            },
        )
        timeout = max(1.0, float(AppConfig.get_marketplace_timeout()))
        try:
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                text = response.read().decode(charset)
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(str(getattr(exc, "reason", exc))) from exc

        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("市场索引必须是 JSON 对象")
        return payload

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
            "message": "已生成 GitHub 公共投稿页，请在打开的页面里粘贴已复制的完整 JSON 预览。",
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
            "请把应用里已经复制的完整 JSON 预览粘贴到这里。",
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
