"""ConfigEngine 的一部分（R2-3 拆分）：预设的解析、创建、改名、删除及引用同步。

方法原样从 engine.py 迁出，行为不变；通过 mixin 组合回 ConfigEngine，
实例状态都在 ConfigEngine.__init__（engine.py）里初始化。
"""

import json
import copy
from typing import Dict, Optional, List, Any
from .engine_common import DEFAULT_PRESET_NAME, DEFAULT_WORKFLOW, _MISSING, logger


class ConfigPresetsMixin:
    """预设的解析、创建、改名、删除及引用同步"""

    def _resolve_default_preset_name(self, site: Dict[str, Any]) -> Optional[str]:
        """解析站点有效默认预设名（不修改原对象）"""
        presets = site.get("presets", {})
        if not presets:
            return None

        configured_default = site.get("default_preset")
        if isinstance(configured_default, str):
            configured_default = configured_default.strip()
        else:
            configured_default = None

        if configured_default and configured_default in presets:
            return configured_default

        if DEFAULT_PRESET_NAME in presets:
            return DEFAULT_PRESET_NAME

        return next(iter(presets))
    def _normalize_site_default_preset(self, domain: str, site: Dict[str, Any]) -> bool:
        """
        规范化站点 default_preset 字段

        Returns:
            是否发生修改
        """
        return self._sync_site_default_preset_state(domain, site)
    def _get_site_data(self, domain: str, preset_name: str = None) -> Optional[Dict]:
        """
        获取指定站点的预设配置数据（可变引用）

        查找顺序:
        - preset_name 显式提供时：只匹配该预设（含别名）
        - preset_name 为空时：按站点默认预设 / 主预设 / 第一个可用预设回退

        Args:
            domain: 站点域名
            preset_name: 预设名称，None 则使用默认

        Returns:
            预设配置字典的引用（可直接修改），或 None
        """
        if domain not in self.sites:
            return None

        site = self.sites[domain]
        presets = site.get("presets", {})

        if not presets:
            return None

        requested_preset = str(preset_name or "").strip()
        if requested_preset:
            resolved_target = self._resolve_preset_alias_key(requested_preset, presets)
            if resolved_target != requested_preset:
                logger.debug(f"预设别名命中: '{requested_preset}' -> '{resolved_target}'")
            if resolved_target in presets:
                return presets[resolved_target]
            return None

        resolved_default = self._resolve_default_preset_name(site)
        if resolved_default and resolved_default in presets:
            return presets[resolved_default]

        if DEFAULT_PRESET_NAME in presets:
            return presets[DEFAULT_PRESET_NAME]

        first_key = next(iter(presets))
        logger.warning(f"默认预设不存在，使用第一个预设: '{first_key}'")
        return presets[first_key]
    @staticmethod
    def _resolve_preset_alias_key(target: Any, presets: Dict[str, Any]) -> str:
        """兼容历史命名：允许不带“预设_”前缀的名字命中真实预设键。"""
        normalized = str(target or "").strip()
        if not normalized or not isinstance(presets, dict):
            return normalized

        if normalized in presets:
            return normalized

        candidates = []
        if normalized.startswith("预设_"):
            stripped = normalized[len("预设_"):].strip()
            if stripped:
                candidates.append(stripped)
        else:
            candidates.append(f"预设_{normalized}")

        for candidate in candidates:
            if candidate in presets:
                return candidate

        return normalized
    def _get_site_data_readonly(self, domain: str, preset_name: str = None) -> Optional[Dict]:
        """获取预设配置的深拷贝（只读用途）"""
        with self._io_lock:
            data = self._get_site_data(domain, preset_name)
            if data is None:
                return None
            return copy.deepcopy(data)
    def _get_preset_data_exact(self, domain: str, preset_name: str = None) -> Optional[Dict]:
        """获取预设配置引用；显式预设不存在时不回退到默认预设。"""
        site = self.sites.get(domain)
        if not isinstance(site, dict):
            return None

        presets = site.get("presets", {})
        if not isinstance(presets, dict) or not presets:
            return None

        if preset_name is None or not str(preset_name).strip():
            target = self._resolve_default_preset_name(site)
        else:
            target = str(preset_name).strip()

        resolved_target = self._resolve_preset_alias_key(target, presets)
        if resolved_target and resolved_target in presets:
            return presets[resolved_target]

        return None
    def list_presets(self, domain: str) -> List[str]:
        """获取指定站点的所有预设名称"""
        self.refresh_if_changed()

        if domain not in self.sites:
            return []

        site = self.sites[domain]
        presets = site.get("presets", {})
        return list(presets.keys())
    def get_default_preset(self, domain: str) -> Optional[str]:
        """获取指定站点的默认预设名称（已解析回退）"""
        self.refresh_if_changed()

        cache_key = f"default_preset:{domain}"
        cached = self._cache.get(cache_key, _MISSING)
        if cached is not _MISSING:
            return cached

        site = self.sites.get(domain)
        if not site:
            self._cache.set(cache_key, None)
            return None

        result = self._resolve_default_preset_name(site)
        self._cache.set(cache_key, result)
        return result
    def _assign_preset_field_and_save(
        self,
        preset_data: Dict[str, Any],
        field_name: str,
        value: Any,
    ) -> bool:
        previous_value = preset_data.get(field_name, _MISSING)
        if previous_value is not _MISSING:
            previous_value = copy.deepcopy(previous_value)
        preset_data[field_name] = value
        if self._save_config():
            return True
        if previous_value is _MISSING:
            preset_data.pop(field_name, None)
        else:
            preset_data[field_name] = previous_value
        return False
    def set_default_preset(self, domain: str, preset_name: str) -> bool:
        """设置指定站点的默认预设"""
        self.refresh_if_changed()

        site = self.sites.get(domain)
        if not site:
            return False

        presets = site.get("presets", {})
        if preset_name not in presets:
            logger.warning(f"默认预设设置失败，预设不存在: {domain}/{preset_name}")
            return False

        previous_default = site.get("default_preset", _MISSING)
        if previous_default is not _MISSING:
            previous_default = copy.deepcopy(previous_default)
        previous_default_maps = (
            copy.deepcopy(getattr(self, "_global_default_presets", {})),
            copy.deepcopy(getattr(self, "_local_default_presets", {})),
        )
        persisted_default = self._get_persisted_default_preset(domain, site)
        if persisted_default == preset_name:
            self._local_default_presets.pop(domain, None)
        else:
            self._local_default_presets[domain] = preset_name
        site["default_preset"] = preset_name
        if not self._save_local_site_overrides():
            if previous_default is _MISSING:
                site.pop("default_preset", None)
            else:
                site["default_preset"] = previous_default
            self._global_default_presets, self._local_default_presets = previous_default_maps
            return False
        logger.info(f"✅ 站点 {domain} 默认预设已设置为: '{preset_name}'（仅本地覆盖）")
        return True
    def create_preset(self, domain: str, new_name: str,
                      source_name: str = None) -> bool:
        """
        创建新预设（克隆自现有预设）

        Args:
            domain: 站点域名
            new_name: 新预设名称
            source_name: 要克隆的源预设名称，None 则克隆主预设

        Returns:
            是否成功
        """
        self.refresh_if_changed()

        if domain not in self.sites:
            logger.warning(f"站点不存在: {domain}")
            return False

        site = self.sites[domain]
        presets = site.get("presets", {})

        if new_name in presets:
            logger.warning(f"预设已存在: {new_name}")
            return False

        # 获取源预设
        if source_name is not None and str(source_name or "").strip():
            requested_source = str(source_name or "").strip()
            source = self._resolve_preset_alias_key(requested_source, presets)
            source_data = presets.get(source)
            if not source_data:
                logger.warning(f"源预设不存在: {requested_source}")
                return False
        else:
            source = DEFAULT_PRESET_NAME
            source_data = presets.get(source)
            if not source_data:
                # 未显式指定源时才尝试第一个可用预设
                if presets:
                    source = next(iter(presets))
                    source_data = presets[source]
                else:
                    logger.warning(f"没有可克隆的源预设")
                    return False

        previous_default = site.get("default_preset", _MISSING)
        if previous_default is not _MISSING:
            previous_default = copy.deepcopy(previous_default)
        previous_presets = copy.deepcopy(presets)
        previous_global_default = copy.deepcopy(self._global_default_presets)
        previous_local_default = copy.deepcopy(self._local_default_presets)

        # 深拷贝创建新预设
        presets[new_name] = copy.deepcopy(source_data)
        self._normalize_site_default_preset(domain, site)
        if not self._save_config():
            site["presets"] = previous_presets
            if previous_default is _MISSING:
                site.pop("default_preset", None)
            else:
                site["default_preset"] = previous_default
            self._global_default_presets = previous_global_default
            self._local_default_presets = previous_local_default
            return False

        logger.info(f"✅ 站点 {domain} 创建预设: '{new_name}' (克隆自 '{source}')")
        return True
    @staticmethod
    def _build_preset_rename_map(old_name: Any, new_name: Any) -> Dict[str, str]:
        old_raw = str(old_name or "").strip()
        new_raw = str(new_name or "").strip()
        if not old_raw or not new_raw:
            return {}

        def _strip_prefix(value: str) -> str:
            if value.startswith("预设_"):
                return value[len("预设_"):].strip()
            return value

        def _add_prefix(value: str) -> str:
            return value if value.startswith("预设_") else f"预设_{value}"

        mapping: Dict[str, str] = {}
        old_plain = _strip_prefix(old_raw)
        new_plain = _strip_prefix(new_raw)
        old_prefixed = _add_prefix(old_raw)
        new_prefixed = _add_prefix(new_raw)

        for src, dst in (
            (old_raw, new_raw),
            (old_plain, new_plain),
            (old_prefixed, new_prefixed),
        ):
            src_text = str(src or "").strip()
            dst_text = str(dst or "").strip()
            if src_text and dst_text:
                mapping[src_text] = dst_text

        return mapping
    @staticmethod
    def _command_targets_domain(command: Dict[str, Any], domain: str) -> bool:
        normalized_domain = str(domain or "").strip().lower()
        if not normalized_domain or not isinstance(command, dict):
            return False

        trigger = command.get("trigger", {}) or {}
        command_domain = str(trigger.get("domain", "") or "").strip().lower()
        if not command_domain:
            return False

        return (
            command_domain == normalized_domain
            or command_domain.endswith(f".{normalized_domain}")
            or normalized_domain.endswith(f".{command_domain}")
        )
    def _rename_preset_references_in_commands(
        self,
        domain: str,
        rename_map: Dict[str, str],
    ) -> int:
        if not rename_map:
            return 0

        commands = self._load_commands_file()
        if not commands:
            return 0

        updated = 0
        for command in commands:
            if not self._command_targets_domain(command, domain):
                continue

            actions = command.get("actions", [])
            if not isinstance(actions, list):
                continue

            for action in actions:
                if not isinstance(action, dict):
                    continue
                current_preset = str(action.get("preset_name", "") or "").strip()
                replacement = rename_map.get(current_preset)
                if not replacement or replacement == current_preset:
                    continue
                action["preset_name"] = replacement
                updated += 1

        if updated > 0:
            self._save_commands_file(commands)

        return updated
    def _rename_preset_references_in_active_tabs(
        self,
        domain: str,
        rename_map: Dict[str, str],
    ) -> int:
        if not rename_map:
            return 0

        try:
            from app.core import browser as browser_module

            instance = getattr(browser_module, "_browser_instance", None)
            if instance is None:
                return 0

            pool = getattr(instance, "_tab_pool", None)
            if pool is None or not hasattr(pool, "get_sessions_snapshot"):
                return 0

            updated = 0
            for session in pool.get_sessions_snapshot():
                session_domain = str(getattr(session, "current_domain", "") or "").strip().lower()
                if session_domain != str(domain or "").strip().lower():
                    continue

                current_preset = str(getattr(session, "preset_name", "") or "").strip()
                replacement = rename_map.get(current_preset)
                if not replacement or replacement == current_preset:
                    continue

                if hasattr(session, "set_preset"):
                    session.set_preset(replacement, source=getattr(session, "preset_override_source", None))
                else:
                    session.preset_name = replacement
                updated += 1

            return updated
        except Exception as e:
            logger.debug(f"同步活动标签页预设引用失败（忽略）: {e}")
            return 0
    def _clear_preset_references_in_active_tabs(self, domain: str, preset_name: str) -> int:
        """预设被删除后，把在用标签页里指向它的 preset_name 置为 None（回落站点默认预设）。"""
        target = str(preset_name or "").strip()
        if not target:
            return 0

        # 兼容历史命名：带/不带“预设_”前缀都视为命中同一个预设
        targets = {target}
        if target.startswith("预设_"):
            stripped = target[len("预设_"):].strip()
            if stripped:
                targets.add(stripped)
        else:
            targets.add(f"预设_{target}")

        try:
            from app.core import browser as browser_module

            instance = getattr(browser_module, "_browser_instance", None)
            if instance is None:
                return 0

            pool = getattr(instance, "_tab_pool", None)
            if pool is None or not hasattr(pool, "get_sessions_snapshot"):
                return 0

            cleared = 0
            for session in pool.get_sessions_snapshot():
                session_domain = str(getattr(session, "current_domain", "") or "").strip().lower()
                if session_domain != str(domain or "").strip().lower():
                    continue

                current_preset = str(getattr(session, "preset_name", "") or "").strip()
                if current_preset not in targets:
                    continue

                if hasattr(session, "set_preset"):
                    session.set_preset(None, source=None)
                else:
                    session.preset_name = None
                cleared += 1

            return cleared
        except Exception as e:
            logger.debug(f"清理活动标签页预设引用失败（忽略）: {e}")
            return 0
    def _sync_preset_overrides_file(self, domain: str, old_preset: str, new_preset: Optional[str] = None) -> int:
        """当预设重命名或删除时，同步清理/更新 preset_overrides.local.json"""
        target = str(old_preset or "").strip()
        if not target:
            return 0
        from pathlib import Path
        config_path = Path("config/preset_overrides.local.json")
        if not config_path.exists():
            return 0
        from app.utils.site_url import extract_remote_site_domain
        import json
        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                return 0
            urls = payload.get("urls", {})
            if not isinstance(urls, dict):
                return 0
            
            domain_norm = str(domain or "").strip().lower()
            changed = 0
            keys_to_delete = []
            for url_key, p_name in list(urls.items()):
                url_domain = str(extract_remote_site_domain(url_key) or "").strip().lower()
                if domain_norm and url_domain != domain_norm:
                    continue
                if p_name == target or p_name == f"预设_{target}" or (target.startswith("预设_") and p_name == target[3:]):
                    if new_preset:
                        urls[url_key] = new_preset
                        changed += 1
                    else:
                        keys_to_delete.append(url_key)
                        changed += 1
            for k in keys_to_delete:
                urls.pop(k, None)
            
            if changed > 0:
                from app.core.config import atomic_write_json
                atomic_write_json(config_path, payload)
                try:
                    from app.core import browser as browser_module
                    instance = getattr(browser_module, "_browser_instance", None)
                    pool = getattr(instance, "_tab_pool", None)
                    if pool is not None and hasattr(pool, "apply_runtime_config"):
                        pool.apply_runtime_config(preset_overrides=payload)
                except Exception:
                    pass
            return changed
        except Exception as e:
            logger.debug(f"同步预设本地配置失败: {e}")
            return 0
    def delete_preset(self, domain: str, preset_name: str) -> bool:
        """
        删除预设（不允许删除最后一个预设）

        Args:
            domain: 站点域名
            preset_name: 要删除的预设名称

        Returns:
            是否成功
        """
        self.refresh_if_changed()

        if domain not in self.sites:
            return False

        site = self.sites[domain]
        presets = site.get("presets", {})

        resolved_preset_name = self._resolve_preset_alias_key(preset_name, presets)
        if resolved_preset_name not in presets:
            logger.warning(f"预设不存在: {preset_name}")
            return False

        if len(presets) <= 1:
            logger.warning(f"不能删除最后一个预设")
            return False

        previous_default = site.get("default_preset", _MISSING)
        if previous_default is not _MISSING:
            previous_default = copy.deepcopy(previous_default)
        removed_preset = copy.deepcopy(presets[resolved_preset_name])
        previous_global_default = copy.deepcopy(self._global_default_presets)
        previous_local_default = copy.deepcopy(self._local_default_presets)

        del presets[resolved_preset_name]
        self._normalize_site_default_preset(domain, site)
        if not self._save_config():
            presets[resolved_preset_name] = removed_preset
            if previous_default is _MISSING:
                site.pop("default_preset", None)
            else:
                site["default_preset"] = previous_default
            self._global_default_presets = previous_global_default
            self._local_default_presets = previous_local_default
            return False

        # 预设已删除并落盘：清理在用标签页对它的残留引用，否则 session.preset_name 会指向不存在的预设
        cleared_tab_refs = 0
        try:
            cleared_tab_refs = self._clear_preset_references_in_active_tabs(domain, resolved_preset_name)
        except Exception as e:
            logger.warning(f"清理活动标签页预设引用失败（忽略）: {e}")

        # 同步清理持久化 preset_overrides.local.json
        try:
            self._sync_preset_overrides_file(domain, resolved_preset_name, new_preset=None)
        except Exception as e:
            logger.warning(f"清理本地预设记忆文件失败（忽略）: {e}")

        logger.info(
            f"✅ 站点 {domain} 删除预设: '{resolved_preset_name}' "
            f"(活动标签页同步 {cleared_tab_refs} 处)"
        )
        return True
    def rename_preset(self, domain: str, old_name: str, new_name: str) -> bool:
        """重命名预设"""
        self.refresh_if_changed()

        if domain not in self.sites:
            return False

        site = self.sites[domain]
        presets = site.get("presets", {})

        resolved_old_name = self._resolve_preset_alias_key(old_name, presets)
        if resolved_old_name not in presets:
            return False

        if new_name in presets:
            logger.warning(f"预设名已存在: {new_name}")
            return False

        rename_map = self._build_preset_rename_map(resolved_old_name, new_name)
        default_preset = site.get("default_preset")
        previous_default = site.get("default_preset", _MISSING)
        if previous_default is not _MISSING:
            previous_default = copy.deepcopy(previous_default)
        previous_presets = copy.deepcopy(presets)
        previous_global_default = copy.deepcopy(self._global_default_presets)
        previous_local_default = copy.deepcopy(self._local_default_presets)
        local_default = str(self._local_default_presets.get(domain, "") or "").strip()

        # 保持顺序：创建有序副本
        new_presets = {}
        for key, value in presets.items():
            if key == resolved_old_name:
                new_presets[new_name] = value
            else:
                new_presets[key] = value

        site["presets"] = new_presets
        if default_preset == resolved_old_name:
            site["default_preset"] = new_name
        if local_default:
            local_replacement = rename_map.get(local_default)
            if local_replacement:
                self._local_default_presets[domain] = local_replacement
        self._normalize_site_default_preset(domain, site)
        if not self._save_config():
            site["presets"] = previous_presets
            if previous_default is _MISSING:
                site.pop("default_preset", None)
            else:
                site["default_preset"] = previous_default
            self._global_default_presets = previous_global_default
            self._local_default_presets = previous_local_default
            return False

        updated_command_refs = self._rename_preset_references_in_commands(domain, rename_map)
        updated_tab_refs = self._rename_preset_references_in_active_tabs(domain, rename_map)
        try:
            self._sync_preset_overrides_file(domain, resolved_old_name, new_preset=new_name)
        except Exception as e:
            logger.warning(f"同步本地预设记忆文件失败（忽略）: {e}")

        logger.info(
            f"站点 {domain} 重命名预设: '{resolved_old_name}' → '{new_name}' "
            f"(命令引用同步 {updated_command_refs} 处, 活动标签页同步 {updated_tab_refs} 处)"
        )
        return True
    def get_preset_selectors(self, domain: str, preset_name: str = None) -> Dict:
        """获取指定预设的选择器配置"""
        data = self._get_site_data_readonly(domain, preset_name)
        return data.get("selectors", {}) if data else {}
    def set_preset_selectors(self, domain: str, selectors: Dict,
                             preset_name: str = None) -> bool:
        """设置指定预设的选择器配置"""
        self.refresh_if_changed()
        data = self._get_site_data(domain, preset_name)
        if data is None:
            return False
        if not self._assign_preset_field_and_save(data, "selectors", selectors):
            return False
        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 选择器已更新")
        return True
    def get_preset_workflow(self, domain: str, preset_name: str = None) -> List:
        """获取指定预设的工作流配置"""
        data = self._get_site_data_readonly(domain, preset_name)
        return data.get("workflow", DEFAULT_WORKFLOW) if data else DEFAULT_WORKFLOW
    def set_preset_workflow(self, domain: str, workflow: List,
                            preset_name: str = None) -> bool:
        """设置指定预设的工作流配置"""
        from app.core.workflow.flow_runtime import has_control_flow, validate_workflow
        if has_control_flow(workflow):
            validate_workflow(workflow)
        self.refresh_if_changed()
        data = self._get_site_data(domain, preset_name)
        if data is None:
            return False
        if not self._assign_preset_field_and_save(data, "workflow", workflow):
            return False
        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 工作流已更新")
        return True
