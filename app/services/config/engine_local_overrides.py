"""ConfigEngine 的一部分（R2-3 拆分）：本地站点覆盖（sites.local.json）与默认预设状态。

方法原样从 engine.py 迁出，行为不变；通过 mixin 组合回 ConfigEngine，
实例状态都在 ConfigEngine.__init__（engine.py）里初始化。
"""

import json
import os
import copy
from typing import Dict, Optional, Any
from .engine_common import logger


class ConfigLocalOverridesMixin:
    """本地站点覆盖（sites.local.json）与默认预设状态"""

    def _load_local_site_overrides(self) -> Dict[str, str]:
        with self._io_lock:
            return self._load_local_site_overrides_locked()
    def _load_local_site_overrides_locked(self) -> Dict[str, str]:
        """加载本地站点覆盖配置，并保留未识别字段。"""
        if not os.path.exists(self.local_sites_file):
            self.last_local_mtime = 0.0
            self._local_sites_payload = {}
            return {}

        try:
            with open(self.local_sites_file, "r", encoding="utf-8-sig") as f:
                data = json.load(f)

            self.last_local_mtime = os.path.getmtime(self.local_sites_file)
            self._local_sites_payload = data if isinstance(data, dict) else {}
            defaults = self._local_sites_payload.get("default_presets", {})
            if not isinstance(defaults, dict):
                return {}

            return {
                str(domain).strip(): str(preset).strip()
                for domain, preset in defaults.items()
                if str(domain).strip() and str(preset).strip()
            }
        except json.JSONDecodeError as e:
            logger.error(f"本地站点覆盖配置格式错误: {e}")
            return copy.deepcopy(self._local_default_presets or {})
        except Exception as e:
            logger.error(f"加载本地站点覆盖配置失败: {e}")
            return copy.deepcopy(self._local_default_presets or {})
    def _prune_default_preset_maps(self) -> None:
        active_domains = {
            domain for domain, site in self.sites.items()
            if not domain.startswith('_') and isinstance(site, dict)
        }
        self._global_default_presets = {
            domain: preset
            for domain, preset in self._global_default_presets.items()
            if domain in active_domains and str(preset or "").strip()
        }
        self._local_default_presets = {
            domain: preset
            for domain, preset in self._local_default_presets.items()
            if domain in active_domains and str(preset or "").strip()
        }
    def _refresh_global_default_presets_from_sites(self) -> None:
        next_defaults: Dict[str, str] = {}
        for domain, site in self.sites.items():
            if domain.startswith('_') or not isinstance(site, dict):
                continue
            resolved = self._resolve_default_preset_name(site)
            if resolved:
                next_defaults[domain] = resolved
        self._global_default_presets = next_defaults
    def _get_persisted_default_preset(self, domain: str, site: Dict[str, Any]) -> Optional[str]:
        presets = site.get("presets", {})
        if not isinstance(presets, dict) or not presets:
            self._global_default_presets.pop(domain, None)
            return None

        candidate = str(self._global_default_presets.get(domain, "") or "").strip()
        if candidate and candidate in presets:
            return candidate

        resolved = self._resolve_default_preset_name(site)
        if resolved:
            self._global_default_presets[domain] = resolved
            return resolved

        self._global_default_presets.pop(domain, None)
        return None
    def _sync_site_default_preset_state(self, domain: str, site: Dict[str, Any]) -> bool:
        presets = site.get("presets", {})
        if not isinstance(presets, dict) or not presets:
            self._global_default_presets.pop(domain, None)
            self._local_default_presets.pop(domain, None)
            if "default_preset" in site:
                del site["default_preset"]
                return True
            return False

        persisted_default = self._get_persisted_default_preset(domain, site)

        local_default = str(self._local_default_presets.get(domain, "") or "").strip()
        if local_default and local_default not in presets:
            self._local_default_presets.pop(domain, None)
            local_default = ""

        if local_default and persisted_default and local_default == persisted_default:
            self._local_default_presets.pop(domain, None)
            local_default = ""

        effective_default = local_default or persisted_default
        if not effective_default:
            effective_default = self._resolve_default_preset_name(site)

        if effective_default and site.get("default_preset") != effective_default:
            site["default_preset"] = effective_default
            return True

        if not effective_default and "default_preset" in site:
            del site["default_preset"]
            return True

        return False
    def _apply_local_site_overrides(self):
        """将本地默认预设选择覆盖到当前站点配置。"""
        self._local_default_presets = self._load_local_site_overrides()
        applied = 0
        for domain, site in self.sites.items():
            if not isinstance(site, dict):
                continue
            self._sync_site_default_preset_state(domain, site)
            local_default = self._local_default_presets.get(domain)
            if local_default and site.get("default_preset") == local_default:
                applied += 1

        if applied > 0:
            logger.debug(f"已应用 {applied} 个本地默认预设覆盖")
    def _save_local_site_overrides(self) -> bool:
        with self._io_lock:
            return self._save_local_site_overrides_locked()
    def _save_local_site_overrides_locked(self) -> bool:
        """保存本地站点覆盖配置。"""
        tmp_file = self.local_sites_file + ".tmp"
        self._prune_default_preset_maps()
        defaults = {}
        for domain, preset_name in self._local_default_presets.items():
            site = self.sites.get(domain)
            if not isinstance(site, dict):
                continue
            presets = site.get("presets", {})
            preset_value = str(preset_name or "").strip()
            if isinstance(presets, dict) and preset_value and preset_value in presets:
                defaults[domain] = preset_value

        payload = dict(self._local_sites_payload or {})
        if os.path.exists(self.local_sites_file):
            try:
                with open(self.local_sites_file, "r", encoding="utf-8-sig") as f:
                    latest_payload = json.load(f)
                if isinstance(latest_payload, dict):
                    payload.update(latest_payload)
            except Exception:
                pass
        payload["default_presets"] = defaults

        try:
            os.makedirs(os.path.dirname(self.local_sites_file), exist_ok=True)
            with open(tmp_file, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_file, self.local_sites_file)
            try:
                self.last_local_mtime = os.path.getmtime(self.local_sites_file)
            except Exception as e:
                logger.warning(f"本地站点覆盖已保存但更新时间戳失败: {e}")
            self._local_sites_payload = payload
            return True
        except Exception as e:
            logger.error(f"保存本地站点覆盖配置失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
            return False
    def _snapshot_local_site_overrides_locked(self) -> Optional[tuple[bool, bytes, Dict[str, Any], Dict[str, str]]]:
        try:
            payload_snapshot = copy.deepcopy(self._local_sites_payload or {})
            defaults_snapshot = copy.deepcopy(self._local_default_presets or {})
            if not os.path.exists(self.local_sites_file):
                return (False, b"", payload_snapshot, defaults_snapshot)
            with open(self.local_sites_file, "rb") as f:
                return (True, f.read(), payload_snapshot, defaults_snapshot)
        except Exception as e:
            logger.error(f"读取本地站点覆盖快照失败: {e}")
            return None
    def _restore_local_site_overrides_locked(self, snapshot: Optional[tuple[bool, bytes, Dict[str, Any], Dict[str, str]]]) -> None:
        if snapshot is None:
            return

        tmp_file = self.local_sites_file + ".restore.tmp"
        existed, payload, payload_snapshot, defaults_snapshot = snapshot
        try:
            if existed:
                os.makedirs(os.path.dirname(self.local_sites_file), exist_ok=True)
                with open(tmp_file, "wb") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, self.local_sites_file)
            elif os.path.exists(self.local_sites_file):
                os.remove(self.local_sites_file)
            try:
                self.last_local_mtime = os.path.getmtime(self.local_sites_file) if os.path.exists(self.local_sites_file) else 0.0
            except Exception as e:
                logger.warning(f"本地站点覆盖已恢复但更新时间戳失败: {e}")
            self._local_sites_payload = copy.deepcopy(payload_snapshot)
            self._local_default_presets = copy.deepcopy(defaults_snapshot)
        except Exception as e:
            logger.error(f"恢复本地站点覆盖失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
