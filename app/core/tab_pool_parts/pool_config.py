"""TabPoolManager 的一部分（R2-3 拆分）：配置解析与运行时配置（排除 URL、模型名/预设覆盖、动态路由候选）。

方法原样从 manager.py 迁出，行为不变；通过 mixin 组合回 TabPoolManager，
类属性与实例状态都在 TabPoolManager（manager.py）里定义。
"""

from typing import Any, Dict, List, Optional

from app.core.config import logger
from app.utils.site_url import normalize_exact_tab_url, normalize_route_domain, tab_url_matches
from app.utils.tab_route_groups import normalize_route_groups, route_groups_by_id

from .session import TabSession


class TabPoolConfigMixin:
    """配置解析与运行时配置（排除 URL、模型名/预设覆盖、动态路由候选）"""

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError, OverflowError):
            return default
    @staticmethod
    def _normalize_allocation_mode(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"round_robin", "random"}:
            return normalized
        return "first_idle"
    @staticmethod
    def _normalize_excluded_urls(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []

        normalized: List[str] = []
        seen = set()
        for item in value:
            text = str(item or "").strip()
            normalized_text = normalize_exact_tab_url(text) or text
            if not normalized_text or normalized_text in seen:
                continue
            seen.add(normalized_text)
            normalized.append(normalized_text)
        return normalized
    @staticmethod
    def _normalize_model_name(value: Any) -> str:
        return str(value or "").strip()
    @classmethod
    def _normalize_model_name_overrides(cls, value: Any) -> Dict[str, Dict[str, str]]:
        payload = value if isinstance(value, dict) else {}
        normalized = {"sites": {}, "urls": {}}

        sites = payload.get("sites") if isinstance(payload, dict) else {}
        if isinstance(sites, dict):
            for key, model_name in sites.items():
                route_key = normalize_route_domain(key)
                display_name = cls._normalize_model_name(model_name)
                if route_key and display_name:
                    normalized["sites"][route_key] = display_name

        urls = payload.get("urls") if isinstance(payload, dict) else {}
        if isinstance(urls, dict):
            for key, model_name in urls.items():
                url_key = normalize_exact_tab_url(str(key or "").strip())
                display_name = cls._normalize_model_name(model_name)
                if url_key and display_name:
                    normalized["urls"][url_key] = display_name

        return normalized
    @classmethod
    def _normalize_preset_overrides(cls, value: Any) -> Dict[str, Dict[str, str]]:
        payload = value if isinstance(value, dict) else {}
        normalized = {"urls": {}}

        urls = payload.get("urls") if isinstance(payload, dict) else {}
        if isinstance(urls, dict):
            for key, preset_name in urls.items():
                url_key = normalize_exact_tab_url(str(key or "").strip())
                display_preset = str(preset_name or "").strip()
                if url_key and display_preset:
                    normalized["urls"][url_key] = display_preset

        return normalized
    def _apply_preset_overrides(self, session: TabSession, info: Optional[Dict[str, Any]] = None) -> None:
        """如果开启了自动按 URL 记忆预设，自动为标签页恢复该 URL 上次保存的预设"""
        if not getattr(self, "auto_remember_url_presets", False):
            return

        current_url = str((info.get("url") if info else None) or session.last_known_url or "").strip()
        if not current_url:
            try:
                current_url = str(session.tab.url or "").strip()
            except Exception:
                pass
        normalized_url = normalize_exact_tab_url(current_url)
        if not normalized_url:
            return

        overrides = self._normalize_preset_overrides(
            getattr(self, "preset_overrides", None)
        )
        saved_preset = overrides["urls"].get(normalized_url)
        if saved_preset:
            applied = False
            with session._lock:
                if session.preset_name is None or session.preset_name == saved_preset:
                    session.set_preset(saved_preset, source="url")
                    applied = True
            if applied and info is not None:
                info["preset_name"] = saved_preset
                info["preset_override_source"] = "url"
    @staticmethod
    def _default_model_name_for_info(info: Dict[str, Any]) -> str:
        route_domain = str(info.get("route_domain") or "").strip()
        current_domain = str(info.get("current_domain") or "").strip()
        tab_id = str(info.get("id") or "").strip()
        return route_domain or current_domain or tab_id or "web-browser"
    def _apply_exposed_model_name(self, info: Dict[str, Any]) -> None:
        default_name = self._default_model_name_for_info(info)
        override_source = ""
        override_name = self._normalize_model_name(info.get("model_name_override"))

        if override_name:
            override_source = "tab"
        else:
            overrides = self._normalize_model_name_overrides(
                getattr(self, "model_name_overrides", None)
            )
            current_url = str(info.get("url") or "").strip()
            normalized_url = normalize_exact_tab_url(current_url)
            if normalized_url:
                override_name = overrides["urls"].get(normalized_url, "")
                if override_name:
                    override_source = "url"

            if not override_name:
                route_domain = normalize_route_domain(
                    info.get("route_domain") or info.get("current_domain") or ""
                )
                if route_domain:
                    override_name = overrides["sites"].get(route_domain, "")
                    if override_name:
                        override_source = "site"

        info["default_model_name"] = default_name
        info["exposed_model_name"] = override_name or default_name
        info["model_name_override_source"] = override_source
    def _get_session_override_model_name(self, session: TabSession) -> str:
        """获取 session 当前被用户显式覆盖的重命名模型名（若未重命名则返回空串）。"""
        override_name = self._normalize_model_name(getattr(session, "model_name_override", None))
        if override_name:
            return override_name
        try:
            current_url, route_domain = session.get_cached_route_snapshot()
        except Exception:
            current_url, route_domain = "", ""
        overrides = self._normalize_model_name_overrides(
            getattr(self, "model_name_overrides", None)
        )
        normalized_url = normalize_exact_tab_url(current_url)
        if normalized_url and normalized_url in overrides.get("urls", {}):
            return str(overrides["urls"][normalized_url] or "").strip()
        norm_domain = normalize_route_domain(route_domain)
        if norm_domain and norm_domain in overrides.get("sites", {}):
            return str(overrides["sites"][norm_domain] or "").strip()
        return ""
    def is_url_excluded(self, url: str) -> bool:
        current_url = str(url or "").strip()
        if not current_url:
            return False

        with self._lock:
            excluded_urls = list(self.excluded_urls)

        return any(tab_url_matches(excluded_url, current_url) for excluded_url in excluded_urls)
    def _is_session_excluded_from_dynamic_routing(
        self,
        session: TabSession,
        current_url: Optional[str] = None,
    ) -> bool:
        if current_url is None:
            try:
                current_url, _actual_domain = session.get_cached_route_snapshot()
            except Exception:
                current_url = ""
        return self.is_url_excluded(current_url)
    def _get_sessions_for_dynamic_routing(self) -> List[TabSession]:
        return [
            session
            for session in self._tabs.values()
            if not self._is_session_excluded_from_dynamic_routing(session)
        ]
    def apply_runtime_config(
        self,
        *,
        max_tabs: Optional[int] = None,
        min_tabs: Optional[int] = None,
        idle_timeout: Optional[float] = None,
        acquire_timeout: Optional[float] = None,
        stuck_timeout: Optional[float] = None,
        allocation_mode: Optional[str] = None,
        excluded_urls: Optional[List[str]] = None,
        preserve_error_tabs: Optional[bool] = None,
        auto_remember_url_presets: Optional[bool] = None,
        model_name_overrides: Optional[Dict[str, Any]] = None,
        preset_overrides: Optional[Dict[str, Any]] = None,
        route_groups: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """同步更新运行中的标签页池参数。"""
        with self._lock:
            new_max_tabs = self.max_tabs if max_tabs is None else max(1, int(max_tabs))
            new_min_tabs = self.min_tabs if min_tabs is None else max(1, int(min_tabs))
            if new_min_tabs > new_max_tabs:
                new_min_tabs = new_max_tabs

            self.max_tabs = new_max_tabs
            self.min_tabs = new_min_tabs

            if idle_timeout is not None:
                self.idle_timeout = max(1.0, float(idle_timeout))
            if acquire_timeout is not None:
                self.acquire_timeout = max(1.0, float(acquire_timeout))
            if stuck_timeout is not None:
                self.stuck_timeout = max(1.0, float(stuck_timeout))
            if allocation_mode is not None:
                self.allocation_mode = self._normalize_allocation_mode(allocation_mode)
            if excluded_urls is not None:
                self.excluded_urls = self._normalize_excluded_urls(excluded_urls)
            if preserve_error_tabs is not None:
                self.preserve_error_tabs = self._to_bool(preserve_error_tabs, False)
            if auto_remember_url_presets is not None:
                self.auto_remember_url_presets = self._to_bool(auto_remember_url_presets, False)
            if model_name_overrides is not None:
                self.model_name_overrides = self._normalize_model_name_overrides(model_name_overrides)
            if preset_overrides is not None:
                self.preset_overrides = self._normalize_preset_overrides(preset_overrides)
            if route_groups is not None:
                self.route_groups = normalize_route_groups(route_groups)
                valid_groups = route_groups_by_id(self.route_groups)
                self._route_group_bindings = {
                    group_id: bindings
                    for group_id, bindings in self._route_group_bindings.items()
                    if group_id in valid_groups
                }
                self._condition.notify_all()

            updated = {
                "max_tabs": self.max_tabs,
                "min_tabs": self.min_tabs,
                "idle_timeout": self.idle_timeout,
                "acquire_timeout": self.acquire_timeout,
                "stuck_timeout": self.stuck_timeout,
                "allocation_mode": self.allocation_mode,
                "excluded_urls": list(self.excluded_urls),
                "preserve_error_tabs": self.preserve_error_tabs,
                "auto_remember_url_presets": self.auto_remember_url_presets,
                "model_name_overrides": {
                    "sites": dict(getattr(self, "model_name_overrides", {}).get("sites", {})),
                    "urls": dict(getattr(self, "model_name_overrides", {}).get("urls", {})),
                },
                "preset_overrides": {
                    "urls": dict(getattr(self, "preset_overrides", {}).get("urls", {})),
                },
                "route_groups": normalize_route_groups(self.route_groups),
            }

            logger.info(
                "[TabPool] 运行时配置已更新: "
                f"max_tabs={self.max_tabs}, min_tabs={self.min_tabs}, "
                f"idle_timeout={self.idle_timeout}, acquire_timeout={self.acquire_timeout}, "
                f"stuck_timeout={self.stuck_timeout}, allocation_mode={self.allocation_mode}, "
                f"excluded_urls={len(self.excluded_urls)}, "
                f"preserve_error_tabs={self.preserve_error_tabs}, "
                f"auto_remember_url_presets={self.auto_remember_url_presets}, "
                f"model_name_overrides="
                f"{len(updated['model_name_overrides']['sites'])}/"
                f"{len(updated['model_name_overrides']['urls'])}, "
                f"preset_overrides={len(updated['preset_overrides']['urls'])}"
                f", route_groups={len(updated['route_groups'])}"
            )
            return updated
