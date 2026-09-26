"""ConfigEngine 的一部分（R2-3 拆分）：站点目录、起始页推断、站点高级配置与选择器定义。

方法原样从 engine.py 迁出，行为不变；通过 mixin 组合回 ConfigEngine，
实例状态都在 ConfigEngine.__init__（engine.py）里初始化。
"""

import copy
from typing import Dict, Optional, List, Any, Set, Union, Callable
from urllib.parse import urljoin
from app.models.schemas import (
    SiteConfig,
    SelectorDefinition,
    ADVANCED_FIELDS,
    PRESET_ADVANCED_FIELDS,
    SITE_ADVANCED_FIELDS,
    get_default_image_extraction_config,
    get_default_file_paste_config,
    get_default_prompt_padding_config,
    get_default_site_advanced_config,
)
from app.utils.site_rules import derive_site_card_id, get_site_rule
from app.utils.site_discovery import (
    automatic_discovery_allowed,
    specific_chat_selectors,
    selectors_match_chat_html,
)
from .engine_common import (
    DEFAULT_PRESET_NAME,
    DEFAULT_WORKFLOW,
    _MISSING,
    _SITE_STARTUP_JS_PATTERNS,
    _coerce_bool,
    get_default_stream_config,
    logger,
)


class ConfigSitesMixin:
    """站点目录、起始页推断、站点高级配置与选择器定义"""

    def _extract_startup_url_from_script(self, script: str) -> str:
        text = str(script or "").strip()
        if not text:
            return ""

        for pattern in _SITE_STARTUP_JS_PATTERNS:
            match = pattern.search(text)
            if match:
                return str(match.group(1) or "").strip()
        return ""
    def _normalize_startup_url(self, domain: str, raw_url: str) -> str:
        normalized_domain = str(domain or "").strip().lower().strip(".")
        text = str(raw_url or "").strip()
        if not normalized_domain:
            return text
        if not text:
            return f"https://{normalized_domain}"
        if text.startswith(("http://", "https://")):
            return text
        return urljoin(f"https://{normalized_domain}", text)
    def _infer_site_startup_url(self, domain: str, site: Dict[str, Any]) -> str:
        preset_name = self._resolve_default_preset_name(site)
        preset_data = self._get_site_data_readonly(domain, preset_name)
        workflow = preset_data.get("workflow", []) if isinstance(preset_data, dict) else []

        if isinstance(workflow, list):
            for step in workflow:
                if str((step or {}).get("action") or "").strip().upper() != "JS_EXEC":
                    continue
                startup_url = self._extract_startup_url_from_script((step or {}).get("value"))
                if startup_url:
                    return self._normalize_startup_url(domain, startup_url)

        return self._normalize_startup_url(domain, "")
    def get_site_catalog_entry(self, domain: str, fallback_order: int = 0) -> Optional[Dict[str, Any]]:
        self.refresh_if_changed()

        site = self.sites.get(domain)
        if not isinstance(site, dict) or str(domain or "").startswith("_"):
            return None

        rule = get_site_rule(domain)
        startup_url = rule.get("startup_url") or self._infer_site_startup_url(domain, site)
        display_name = str(rule.get("display_name") or domain).strip() or str(domain)
        card_id = str(rule.get("card_id") or derive_site_card_id(domain)).strip() or derive_site_card_id(domain)
        guide_priority = rule.get("guide_priority")
        if not isinstance(guide_priority, int):
            guide_priority = int(fallback_order)

        entry = {
            "domain": str(domain),
            "display_name": display_name,
            "url": self._normalize_startup_url(domain, startup_url),
            "card_id": card_id,
            "guide_priority": guide_priority,
        }

        if isinstance(rule.get("route_aliases"), list):
            entry["route_aliases"] = [str(item) for item in rule.get("route_aliases", [])]
        if "stealth_default" in rule:
            entry["stealth_default"] = bool(rule.get("stealth_default"))

        return entry
    def list_site_catalog(self) -> List[Dict[str, Any]]:
        self.refresh_if_changed()

        ordered_domains = [
            domain for domain, site in self.sites.items()
            if not domain.startswith("_") and isinstance(site, dict)
        ]
        entries: List[Dict[str, Any]] = []

        for index, domain in enumerate(ordered_domains):
            entry = self.get_site_catalog_entry(domain, fallback_order=index)
            if entry:
                entries.append(entry)

        entries.sort(key=lambda item: (int(item.get("guide_priority", 0)), str(item.get("domain") or "")))
        return entries
    def _normalize_site_advanced_config(
        self,
        raw_config: Optional[Dict[str, Any]] = None,
        *,
        base_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """合并并规范化高级配置。"""
        normalized = {
            **get_default_site_advanced_config(),
        }
        if isinstance(base_config, dict):
            normalized.update(copy.deepcopy(base_config))
        if isinstance(raw_config, dict):
            normalized.update(copy.deepcopy(raw_config))

        normalized["independent_cookies"] = _coerce_bool(normalized.get("independent_cookies"), False)
        normalized["independent_cookies_auto_takeover"] = _coerce_bool(
            normalized.get("independent_cookies_auto_takeover"), False
        )
        normalized["input_box_stability_wait_enabled"] = _coerce_bool(
            normalized.get("input_box_stability_wait_enabled"), False
        )
        normalized["input_box_stability_wait_after_new_chat_only"] = _coerce_bool(
            normalized.get("input_box_stability_wait_after_new_chat_only"), True
        )
        try:
            timeout_value = float(normalized.get("input_box_stability_wait_timeout", 1.5))
        except Exception:
            timeout_value = 1.5
        normalized["input_box_stability_wait_timeout"] = max(0.1, min(timeout_value, 10.0))
        normalized["url_transition_wait_on_new_chat"] = _coerce_bool(
            normalized.get("url_transition_wait_on_new_chat"), False
        )
        raw_patterns = normalized.get("url_transition_wait_patterns") or []
        if isinstance(raw_patterns, str):
            raw_patterns = raw_patterns.replace("\n", ",").replace(";", ",").split(",")
        if not isinstance(raw_patterns, (list, tuple, set)):
            raw_patterns = []
        normalized["url_transition_wait_patterns"] = [
            str(pattern or "").strip()
            for pattern in raw_patterns
            if str(pattern or "").strip()
        ]
        normalized["send_confirmation_check_enabled"] = _coerce_bool(
            normalized.get("send_confirmation_check_enabled"), False
        )
        try:
            send_timeout_value = float(normalized.get("send_confirmation_check_timeout", 1.5))
        except Exception:
            send_timeout_value = 1.5
        normalized["send_confirmation_check_timeout"] = max(0.1, min(send_timeout_value, 10.0))
        normalized["skip_new_chat_on_retry"] = _coerce_bool(
            normalized.get("skip_new_chat_on_retry"), False
        )
        return normalized
    def get_site_advanced_config(self, domain: str, preset_name: str = None) -> Dict[str, Any]:
        """获取站点高级配置；传入 preset_name 时叠加对应预设的覆盖项。"""
        self.refresh_if_changed()

        site = self.sites.get(domain)
        if not site:
            return self._normalize_site_advanced_config()

        raw_config = site.get("advanced") if isinstance(site, dict) else None
        normalized = self._normalize_site_advanced_config(
            raw_config if isinstance(raw_config, dict) else {}
        )

        requested_preset = str(preset_name or "").strip()
        if requested_preset:
            preset_data = self._get_preset_data_exact(domain, requested_preset)
            if preset_data is None:
                logger.warning(f"站点高级配置预设不存在: {domain}/{requested_preset}")
                return normalized
            preset_advanced = (
                preset_data.get("advanced")
                if isinstance(preset_data, dict)
                else None
            )
            if isinstance(preset_advanced, dict):
                preset_advanced = {
                    key: value
                    for key, value in preset_advanced.items()
                    if key in PRESET_ADVANCED_FIELDS
                }
                normalized = self._normalize_site_advanced_config(
                    preset_advanced,
                    base_config=normalized,
                )

        return normalized
    def set_site_advanced_config(self, domain: str, config: Dict[str, Any]) -> bool:
        """设置站点级高级配置。"""
        with self._io_lock:
            self.refresh_if_changed()

            site = self.sites.get(domain)
            if not site:
                logger.warning(f"站点不存在: {domain}")
                return False

            previous_advanced = site.get("advanced", _MISSING)
            if previous_advanced is not _MISSING:
                previous_advanced = copy.deepcopy(previous_advanced)

            existing = site.get("advanced") if isinstance(site.get("advanced"), dict) else {}
            stored = copy.deepcopy(existing)
            normalized = self._normalize_site_advanced_config(config or {}, base_config=existing)

            provided_keys = {
                key
                for key in ((config or {}).keys() if isinstance(config, dict) else set())
                if key in ADVANCED_FIELDS
            }
            if not provided_keys and previous_advanced is _MISSING:
                return True
            for key in ADVANCED_FIELDS:
                if key in provided_keys:
                    stored[key] = normalized[key]

            site["advanced"] = stored
            if self._save_config_locked():
                return True
            if previous_advanced is _MISSING:
                site.pop("advanced", None)
            else:
                site["advanced"] = previous_advanced
            return False
    def set_preset_advanced_config(
        self,
        domain: str,
        config: Dict[str, Any],
        preset_name: str = None,
        *,
        prune_inherited_fields: Optional[Set[str]] = None,
    ) -> bool:
        """设置当前预设的高级配置；拒绝混入站点级字段。"""
        with self._io_lock:
            self.refresh_if_changed()

            site = self.sites.get(domain)
            if not isinstance(site, dict):
                logger.warning(f"站点不存在: {domain}")
                return False

            requested_preset = str(preset_name or "").strip()
            if not requested_preset:
                logger.warning(f"预设级高级配置缺少 preset_name: {domain}")
                return False

            data = self._get_preset_data_exact(domain, requested_preset)
            if data is None:
                logger.warning(f"站点或预设不存在: {domain}/{requested_preset}")
                return False

            raw_config = config if isinstance(config, dict) else {}

            invalid_site_keys = {
                key
                for key in raw_config.keys()
                if key in SITE_ADVANCED_FIELDS
            }
            if invalid_site_keys:
                joined = ", ".join(sorted(invalid_site_keys))
                logger.warning(f"预设级高级配置不能包含站点级字段: {domain}/{requested_preset} ({joined})")
                return False

            previous_advanced = data.get("advanced", _MISSING)
            if previous_advanced is not _MISSING:
                previous_advanced = copy.deepcopy(previous_advanced)

            stored = copy.deepcopy(data.get("advanced") or {})
            if not isinstance(stored, dict):
                stored = {}

            for key in list(SITE_ADVANCED_FIELDS):
                stored.pop(key, None)

            site_advanced = site.get("advanced") if isinstance(site.get("advanced"), dict) else {}
            inherited = self._normalize_site_advanced_config(site_advanced)

            normalized = self._normalize_site_advanced_config(
                raw_config,
                base_config=stored,
            )

            provided_keys = {
                key
                for key in raw_config.keys()
                if key in PRESET_ADVANCED_FIELDS
            }
            if not provided_keys and previous_advanced is _MISSING:
                return True
            prune_inherited_keys = set(prune_inherited_fields or set())
            for key in PRESET_ADVANCED_FIELDS:
                if key in provided_keys:
                    if (
                        key in prune_inherited_keys
                        and key not in stored
                        and normalized[key] == inherited.get(key)
                    ):
                        stored.pop(key, None)
                    else:
                        stored[key] = normalized[key]

            if not stored and previous_advanced is _MISSING:
                return True

            data["advanced"] = stored
            if self._save_config_locked():
                return True
            if previous_advanced is _MISSING:
                data.pop("advanced", None)
            else:
                data["advanced"] = previous_advanced
            return False
    def list_sites(self) -> Dict[str, Any]:
        """获取所有站点配置（过滤内部键）"""
        self.refresh_if_changed()

        with self._io_lock:
            return copy.deepcopy({
                domain: config
                for domain, config in self.sites.items()
                if not domain.startswith('_')
            })
    def get_site_config(self, domain: str, html_content: Optional[Union[str, Callable[[], str]]] = None,
                        preset_name: str = None) -> Optional[SiteConfig]:
        """
        获取站点配置（缓存 + AI 分析）

        Args:
            domain: 站点域名
            html_content: 页面 HTML 或用于延迟获取 HTML 的可调用对象（用于 AI 分析未知站点）
            preset_name: 预设名称，None 则使用默认预设
        """
        self.refresh_if_changed()

        if domain in self.sites:
            cache_key = f"site_config:{domain}:{preset_name or ''}"
            cached = self._cache.get(cache_key, _MISSING)
            if cached is not _MISSING:
                return copy.deepcopy(cached)

            site = self.sites.get(domain, {})
            config = self._get_site_data(domain, preset_name)

            if config is None:
                logger.warning(f"站点 {domain} 无可用预设")
                return None

            used_preset = preset_name or self._resolve_default_preset_name(site) or DEFAULT_PRESET_NAME
            previous_config = None

            # 补充缺失字段
            changed = False
            if "workflow" not in config:
                if previous_config is None:
                    previous_config = copy.deepcopy(config)
                config["workflow"] = DEFAULT_WORKFLOW
                changed = True

            if "image_extraction" not in config:
                if previous_config is None:
                    previous_config = copy.deepcopy(config)
                config["image_extraction"] = get_default_image_extraction_config()
                changed = True

            if "file_paste" not in config:
                if previous_config is None:
                    previous_config = copy.deepcopy(config)
                config["file_paste"] = get_default_file_paste_config()
                changed = True
            else:
                normalized_file_paste = self._validate_file_paste_config(
                    config.get("file_paste", {}),
                    legacy_stream_config=config.get("stream_config"),
                )
                if normalized_file_paste != config.get("file_paste"):
                    if previous_config is None:
                        previous_config = copy.deepcopy(config)
                    config["file_paste"] = normalized_file_paste
                    changed = True

            if "prompt_padding" not in config:
                if previous_config is None:
                    previous_config = copy.deepcopy(config)
                config["prompt_padding"] = get_default_prompt_padding_config()
                changed = True
            else:
                normalized_prompt_padding = self._validate_prompt_padding_config(
                    config.get("prompt_padding", {})
                )
                if normalized_prompt_padding != config.get("prompt_padding"):
                    if previous_config is None:
                        previous_config = copy.deepcopy(config)
                    config["prompt_padding"] = normalized_prompt_padding
                    changed = True

            if changed:
                completed_config = copy.deepcopy(config)
                if not self._save_config():
                    config.clear()
                    config.update(previous_config)
                    logger.warning(
                        f"站点 {domain} [{used_preset}] 配置自动补全保存失败，"
                        "已仅对当前请求返回补全配置"
                    )
                    return completed_config

            logger.debug(f"使用缓存配置: {domain} [预设: {used_preset}]")
            result = copy.deepcopy(config)
            self._cache.set(cache_key, result)
            return copy.deepcopy(result)

        if not automatic_discovery_allowed(domain):
            logger.info(f"跳过非聊天站点的自动收录: {domain}（已有手动配置不受影响）")
            return None

        resolved_html = ""
        if callable(html_content):
            try:
                resolved_html = str(html_content() or "")
            except Exception as e:
                logger.warning(f"获取页面 HTML 失败: {e}")
                resolved_html = ""
        elif html_content is not None:
            resolved_html = str(html_content or "")

        if resolved_html.strip():
            logger.info(f"🔍 未知域名 {domain}，启动 AI 识别...")
            clean_html = self.html_cleaner.clean(resolved_html)
            selectors = self.ai_analyzer.analyze(clean_html)

            if specific_chat_selectors(selectors):
                selectors = self.validator.validate(selectors)
            if selectors_match_chat_html(selectors, clean_html):
                new_preset: SiteConfig = {
                    "selectors": selectors,
                    "workflow": DEFAULT_WORKFLOW,
                    "stealth": self._guess_stealth(domain),
                    "stream_config": copy.deepcopy(get_default_stream_config()),
                    "image_extraction": get_default_image_extraction_config(),
                    "file_paste": get_default_file_paste_config(),
                    "prompt_padding": get_default_prompt_padding_config(),
                }

                self.sites[domain] = {
                    "default_preset": DEFAULT_PRESET_NAME,
                    "presets": {
                        DEFAULT_PRESET_NAME: new_preset
                    }
                }
                if self._save_config():
                    logger.info(f"✅ 配置已生成并保存: {domain}")
                else:
                    logger.warning(f"⚠️ 配置已生成但保存失败，仅保留在当前运行内存: {domain}")
                return copy.deepcopy(new_preset)

            logger.warning(f"⚠️  AI 分析失败，使用通用回退配置: {domain}")
        else:
            logger.warning(f"⚠️  未知域名 {domain} 且未提供网页 HTML，使用通用回退配置")

        fallback_selectors = self.global_config.get_fallback_selectors()

        fallback_preset: SiteConfig = {
            "selectors": fallback_selectors,
            "workflow": DEFAULT_WORKFLOW,
            "stealth": False,
            "stream_config": copy.deepcopy(get_default_stream_config()),
            "image_extraction": get_default_image_extraction_config(),
            "file_paste": get_default_file_paste_config(),
            "prompt_padding": get_default_prompt_padding_config(),
        }

        # A generic fallback is only for this caller. It is not proof that this
        # domain hosts an AI chat product, and must never enter the site catalog.
        logger.info(f"通用回退仅用于当前请求，未自动添加站点: {domain}")
        return copy.deepcopy(fallback_preset)
    def delete_site_config(self, domain: str) -> bool:
        """删除指定站点配置"""
        self.refresh_if_changed()

        if domain in self.sites:
            removed_site = copy.deepcopy(self.sites[domain])
            previous_global_default = copy.deepcopy(self._global_default_presets)
            previous_local_default = copy.deepcopy(self._local_default_presets)
            del self.sites[domain]
            if not self._save_config():
                self.sites[domain] = removed_site
                self._global_default_presets = previous_global_default
                self._local_default_presets = previous_local_default
                return False
            logger.info(f"已删除配置: {domain}")
            return True
        return False
    def get_selector_definitions(self) -> List[SelectorDefinition]:
        """获取元素定义列表"""
        return self.global_config.get_selector_definitions()
    def set_selector_definitions(self, definitions: List[SelectorDefinition]) -> bool:
        """设置元素定义列表并保存"""
        previous_definitions = self.global_config.get_selector_definitions()
        previous_fallback_selectors = copy.deepcopy(self.validator.fallback_selectors)
        self.global_config.set_selector_definitions(definitions)

        # 更新验证器的回退选择器
        self.validator.fallback_selectors = self.global_config.get_fallback_selectors()

        # 保存配置
        if not self._save_config():
            self.global_config.set_selector_definitions(previous_definitions)
            self.validator.fallback_selectors = previous_fallback_selectors
            return False

        logger.info(f"元素定义已更新: {len(definitions)} 个")
        return True
