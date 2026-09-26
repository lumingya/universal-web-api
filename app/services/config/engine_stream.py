"""ConfigEngine 的一部分（R2-3 拆分）：提取器、流式/网络监听配置及其校验、解析器列表。

方法原样从 engine.py 迁出，行为不变；通过 mixin 组合回 ConfigEngine，
实例状态都在 ConfigEngine.__init__（engine.py）里初始化。
"""

import copy
from typing import Dict, Optional, List, Any
from app.core.parsers import ParserRegistry
from app.models.schemas import get_default_attachment_monitor_config, get_default_send_confirmation_config
from app.services.extractor_manager import extractor_manager
from app.services.parser_manager import parser_manager
from app.core.request_transport import normalize_request_transport_config
from .engine_common import (
    DEFAULT_PRESET_NAME,
    _MISSING,
    _coerce_bool,
    _merge_config_patch,
    get_default_network_config,
    get_default_stream_config,
    logger,
)


class ConfigStreamMixin:
    """提取器、流式/网络监听配置及其校验、解析器列表"""

    def get_site_extractor(self, domain: str, preset_name: str = None):
        """获取站点的提取器实例"""
        self.refresh_if_changed()

        data = self._get_site_data(domain, preset_name)
        if data is not None:
            return extractor_manager.get_extractor_for_site(data)

        return extractor_manager.get_extractor()
    def set_site_extractor(self, domain: str, extractor_id: str,
                           preset_name: str = None) -> bool:
        """为站点设置提取器"""
        self.refresh_if_changed()

        data = self._get_site_data(domain, preset_name)
        if data is None:
            logger.warning(f"站点或预设不存在: {domain}/{preset_name}")
            return False

        from app.core.extractors import ExtractorRegistry
        if not ExtractorRegistry.exists(extractor_id):
            logger.error(f"提取器不存在: {extractor_id}")
            return False

        previous_extractor_id = data.get("extractor_id", _MISSING)
        if previous_extractor_id is not _MISSING:
            previous_extractor_id = copy.deepcopy(previous_extractor_id)
        previous_verified = data.get("extractor_verified", _MISSING)
        if previous_verified is not _MISSING:
            previous_verified = copy.deepcopy(previous_verified)
        data["extractor_id"] = extractor_id
        data["extractor_verified"] = False
        if not self._save_config():
            if previous_extractor_id is _MISSING:
                data.pop("extractor_id", None)
            else:
                data["extractor_id"] = previous_extractor_id
            if previous_verified is _MISSING:
                data.pop("extractor_verified", None)
            else:
                data["extractor_verified"] = previous_verified
            return False

        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 已绑定提取器: {extractor_id}")
        return True
    def set_site_extractor_verified(self, domain: str, verified: bool = True,
                                     preset_name: str = None) -> bool:
        """设置站点提取器验证状态"""
        self.refresh_if_changed()

        data = self._get_site_data(domain, preset_name)
        if data is None:
            return False

        if not self._assign_preset_field_and_save(data, "extractor_verified", verified):
            return False

        return True
    def get_site_stream_config(self, domain: str, preset_name: str = None) -> Dict[str, Any]:
        """
        获取站点的流式配置

        Args:
            domain: 站点域名
            preset_name: 预设名称

        Returns:
            完整的流式配置（包含默认值）
        """
        self.refresh_if_changed()

        cache_key = f"stream_config:{domain}:{preset_name or ''}"
        cached = self._cache.get(cache_key, _MISSING)
        if cached is not _MISSING:
            return copy.deepcopy(cached)

        default_config = get_default_stream_config()

        data = self._get_site_data(domain, preset_name)
        if data is None:
            result = copy.deepcopy(default_config)
            self._cache.set(cache_key, result)
            return copy.deepcopy(result)

        stream_config = data.get("stream_config", {})

        # 合并默认值
        result = copy.deepcopy(default_config)

        # 更新顶层字段
        for key in ["mode", "hard_timeout"]:
            if key in stream_config:
                result[key] = stream_config[key]

        if isinstance(stream_config.get("request_transport"), dict):
            result["request_transport"] = normalize_request_transport_config(
                stream_config.get("request_transport")
            )

        # 处理 send_confirmation 配置
        if isinstance(stream_config.get("send_confirmation"), dict):
            result["send_confirmation"].update(stream_config["send_confirmation"])

        # 处理 attachment_monitor 配置
        if isinstance(stream_config.get("attachment_monitor"), dict):
            result["attachment_monitor"].update(stream_config["attachment_monitor"])

        file_paste_config = data.get("file_paste", {})
        if isinstance(file_paste_config, dict):
            if isinstance(file_paste_config.get("send_confirmation"), dict):
                result["send_confirmation"].update(file_paste_config["send_confirmation"])
            if isinstance(file_paste_config.get("attachment_monitor"), dict):
                result["attachment_monitor"].update(file_paste_config["attachment_monitor"])

        # 处理 network 配置
        if stream_config.get("network"):
            network_default = get_default_network_config()
            network_config = stream_config["network"]

            result["network"] = network_default.copy()
            for key in [
                "listen_pattern",
                "stream_match_mode",
                "stream_match_pattern",
                "parser",
                "hard_timeout",
                "first_response_timeout",
                "first_content_timeout",
                "initial_target_body_wait",
                "silence_threshold",
                "response_interval",
            ]:
                if key in network_config:
                    result["network"][key] = network_config[key]

        self._cache.set(cache_key, copy.deepcopy(result))
        return result
    def set_site_stream_config(self, domain: str, config: Dict[str, Any],
                                preset_name: str = None) -> bool:
        """
        设置站点的流式配置

        Args:
            domain: 站点域名
            config: 流式配置（部分或完整）
            preset_name: 预设名称

        Returns:
            是否成功
        """
        self.refresh_if_changed()

        data = self._get_site_data(domain, preset_name)
        if data is None:
            logger.warning(f"站点或预设不存在: {domain}/{preset_name}")
            return False

        previous_file_paste = data.get("file_paste", _MISSING)
        if previous_file_paste is not _MISSING:
            previous_file_paste = copy.deepcopy(previous_file_paste)
        previous_stream = data.get("stream_config", _MISSING)
        if previous_stream is not _MISSING:
            previous_stream = copy.deepcopy(previous_stream)

        legacy_file_paste_updates: Dict[str, Any] = {}
        if isinstance(config.get("send_confirmation"), dict):
            legacy_file_paste_updates["send_confirmation"] = config.get("send_confirmation") or {}
        if isinstance(config.get("attachment_monitor"), dict):
            legacy_file_paste_updates["attachment_monitor"] = config.get("attachment_monitor") or {}

        # 验证并规范化配置
        current_config = self.get_site_stream_config(domain, preset_name)
        merged_config = _merge_config_patch(current_config, config if isinstance(config, dict) else {})
        validated = self._validate_stream_config(merged_config)
        validated.pop("send_confirmation", None)
        validated.pop("attachment_monitor", None)

        if legacy_file_paste_updates:
            existing_file_paste = self._validate_file_paste_config(
                data.get("file_paste", {}),
                legacy_stream_config=data.get("stream_config"),
            )
            merged_file_paste = _merge_config_patch(existing_file_paste, legacy_file_paste_updates)
            data["file_paste"] = self._validate_file_paste_config(
                merged_file_paste,
                legacy_stream_config=data.get("stream_config"),
            )

        data["stream_config"] = validated
        if not self._save_config():
            if previous_stream is _MISSING:
                data.pop("stream_config", None)
            else:
                data["stream_config"] = previous_stream
            if previous_file_paste is _MISSING:
                data.pop("file_paste", None)
            else:
                data["file_paste"] = previous_file_paste
            return False

        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 流式配置已更新 (mode={validated.get('mode')})")
        return True
    def _validate_stream_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        验证并规范化流式配置

        Args:
            config: 原始配置

        Returns:
            规范化后的配置
        """
        result = get_default_stream_config()

        if not config:
            return result

        mode_explicitly_set = False

        # 验证 mode
        if "mode" in config:
            mode = str(config["mode"]).lower()
            if mode in ("dom", "network"):
                result["mode"] = mode
                mode_explicitly_set = True

        # 验证数值字段
        for key in ["hard_timeout"]:
            if key in config:
                try:
                    val = float(config[key])
                    if key == "hard_timeout":
                        result[key] = max(10, min(val, 600))
                except (ValueError, TypeError):
                    pass

        # 验证 send_confirmation 配置
        if isinstance(config.get("send_confirmation"), dict):
            result["send_confirmation"] = self._validate_send_confirmation_config(
                config["send_confirmation"]
            )

        if isinstance(config.get("request_transport"), dict):
            result["request_transport"] = normalize_request_transport_config(
                config["request_transport"]
            )

        # 验证 attachment_monitor 配置
        if isinstance(config.get("attachment_monitor"), dict):
            result["attachment_monitor"] = self._validate_attachment_monitor_config(
                config["attachment_monitor"]
            )

        # 验证 network 配置
        if config.get("network"):
            network_config = self._validate_network_config(config["network"])
            if network_config:
                result["network"] = network_config
                # 仅在调用方没有显式指定 mode 时，才根据 network 配置自动切到 network。
                # 这样切回 DOM 时可以保留 parser/listen_pattern，而不会被后端强制改回 network。
                if (
                    not mode_explicitly_set
                    and network_config.get("parser")
                    and network_config.get("listen_pattern")
                ):
                    result["mode"] = "network"

        return result
    def _validate_send_confirmation_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """验证发送成功判定配置。"""
        result = get_default_send_confirmation_config()

        if not isinstance(config, dict):
            return result

        numeric_ranges = {
            "post_click_observe_window": (0.0, 15.0),
            "pre_retry_probe_window": (0.0, 5.0),
            "retry_observe_window": (0.0, 15.0),
            "attachment_observe_window": (0.0, 30.0),
            "retry_interval": (0.0, 30.0),
            "retry_cooldown_window": (0.0, 30.0),
        }
        for key, (minimum, maximum) in numeric_ranges.items():
            if key not in config:
                continue
            try:
                value = float(config[key])
            except (TypeError, ValueError):
                continue
            result[key] = max(minimum, min(value, maximum))

        if "max_retry_count" in config:
            try:
                value = int(config["max_retry_count"])
            except (TypeError, ValueError):
                value = None
            if value is not None:
                result["max_retry_count"] = max(0, min(value, 10))

        retry_action = str(config.get("retry_action") or "").strip().lower()
        if retry_action in {"click_send_btn", "key_press"}:
            result["retry_action"] = retry_action

        if "retry_key_combo" in config:
            retry_key_combo = str(config.get("retry_key_combo") or "").strip()
            if retry_key_combo:
                result["retry_key_combo"] = retry_key_combo[:64]

        bool_fields = [
            "retry_on_unconfirmed_send",
            "accept_attachment_change",
            "accept_attachment_disappear",
            "accept_probe_confirmation",
            "retry_block_on_stop_button",
            "retry_block_if_generating",
            "trust_network_activity",
            "trust_generating_indicator",
            "trust_send_disabled_with_input_shrink",
        ]
        for key in bool_fields:
            if key not in config:
                continue
            result[key] = _coerce_bool(config.get(key), bool(result.get(key, False)))

        sensitivity = str(config.get("attachment_sensitivity") or "").strip().lower()
        if sensitivity in {"low", "medium", "high"}:
            result["attachment_sensitivity"] = sensitivity

        return result
    def _validate_attachment_monitor_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """验证附件上传/发送前判定规则。"""
        result = get_default_attachment_monitor_config()

        if not isinstance(config, dict):
            return result

        list_fields = [
            "root_selectors",
            "attachment_selectors",
            "pending_selectors",
            "status_selectors",
            "content_exclusion_selectors",
            "busy_text_markers",
            "ignored_busy_text_markers",
            "send_button_disabled_markers",
        ]
        for key in list_fields:
            raw_value = config.get(key)
            if raw_value is None:
                continue
            if not isinstance(raw_value, list):
                continue
            cleaned = []
            for item in raw_value:
                value = str(item or "").strip()
                if value and value not in cleaned:
                    cleaned.append(value)
            result[key] = cleaned

        numeric_ranges = {
            "idle_timeout": (0.5, 60.0),
            "hard_max_wait": (1.0, 300.0),
        }
        for key, (minimum, maximum) in numeric_ranges.items():
            if key not in config:
                continue
            try:
                value = float(config[key])
            except (TypeError, ValueError):
                continue
            result[key] = max(minimum, min(value, maximum))

        bool_fields = [
            "require_attachment_present",
            "require_upload_signal_before_ready",
            "continue_once_on_unconfirmed_send",
        ]
        for key in bool_fields:
            if key not in config:
                continue
            result[key] = _coerce_bool(config.get(key), bool(result.get(key, False)))

        return result
    def _validate_network_config(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        验证网络监听配置

        Args:
            config: 原始网络配置

        Returns:
            规范化后的配置，无效则返回 None
        """
        if not config:
            return None

        result = get_default_network_config()

        # listen_pattern（必填）
        if "listen_pattern" in config:
            pattern = str(config["listen_pattern"]).strip()
            if pattern:
                result["listen_pattern"] = pattern

        if "stream_match_mode" in config:
            match_mode = str(config["stream_match_mode"]).strip().lower()
            if match_mode in {"keyword", "regex"}:
                result["stream_match_mode"] = match_mode

        if "stream_match_pattern" in config:
            result["stream_match_pattern"] = str(config["stream_match_pattern"]).strip()

        # parser（必填，需验证存在性）
        if "parser" in config:
            parser_id = str(config["parser"]).strip()
            if parser_id:
                # 验证解析器是否存在
                if ParserRegistry.exists(parser_id):
                    result["parser"] = parser_id
                else:
                    logger.warning(f"解析器不存在: {parser_id}")
                    # 仍然保存，允许后续添加解析器
                    result["parser"] = parser_id

        # 验证数值字段
        for key in ["first_response_timeout", "silence_threshold", "response_interval"]:
            if key in config:
                try:
                    val = float(config[key])
                    if key == "first_response_timeout":
                        result[key] = max(0.5, min(val, 300))
                    elif key == "silence_threshold":
                        result[key] = max(0.5, min(val, 30))
                    elif key == "response_interval":
                        result[key] = max(0.1, min(val, 5))
                except (ValueError, TypeError):
                    pass

        # 检查是否有有效配置
        if not result["listen_pattern"] or not result["parser"]:
            return None

        return result
    def list_available_parsers(self) -> List[Dict[str, str]]:
        """
        列出所有可用的响应解析器

        Returns:
            解析器信息列表
        """
        return parser_manager.list_parsers()
    def get_extractor_manager(self):
        """获取提取器管理器实例"""
        return extractor_manager
    def get_parser_manager(self):
        """获取解析器管理器实例"""
        return parser_manager
