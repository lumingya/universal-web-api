"""ConfigEngine 的一部分（R2-3 拆分）：图片提取、文件粘贴、提示词填充配置与图片预设。

方法原样从 engine.py 迁出，行为不变；通过 mixin 组合回 ConfigEngine，
实例状态都在 ConfigEngine.__init__（engine.py）里初始化。
"""

import copy
from typing import Dict, Optional, Any
from app.models.schemas import (
    get_default_image_extraction_config,
    get_default_file_paste_config,
    get_default_prompt_padding_config,
    get_default_attachment_monitor_config,
    get_default_send_confirmation_config,
    get_enabled_modalities,
    is_modality_enabled,
    normalize_modalities_config,
    normalize_modality_policy,
)
from .engine_common import DEFAULT_PRESET_NAME, _coerce_bool, _merge_config_patch, logger


class ConfigMediaMixin:
    """图片提取、文件粘贴、提示词填充配置与图片预设"""

    def get_site_image_config(self, domain: str, preset_name: str = None) -> Dict:
        """获取站点的图片提取配置"""
        self.refresh_if_changed()

        default_config = get_default_image_extraction_config()

        data = self._get_site_data(domain, preset_name)
        if data is None:
            return copy.deepcopy(default_config)

        image_config = data.get("image_extraction", {})
        return self._validate_image_config(image_config)
    def set_site_image_config(self, domain: str, config: Dict,
                              preset_name: str = None) -> bool:
        """设置站点的图片提取配置"""
        self.refresh_if_changed()

        data = self._get_site_data(domain, preset_name)
        if data is None:
            logger.warning(f"站点或预设不存在: {domain}/{preset_name}")
            return False

        current_config = self._validate_image_config(data.get("image_extraction", {}))
        merged_config = _merge_config_patch(current_config, config if isinstance(config, dict) else {})
        validated = self._validate_image_config(merged_config)

        if not self._assign_preset_field_and_save(data, "image_extraction", validated):
            return False

        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 多模态提取配置已更新")
        return True
    def _validate_image_config(self, config: Dict) -> Dict:
        """验证并规范化多模态提取配置"""
        default = get_default_image_extraction_config()
        result = copy.deepcopy(default)

        if not config:
            return result

        raw_modalities = config.get("modalities")
        if isinstance(raw_modalities, dict):
            result["modalities"] = normalize_modalities_config(raw_modalities)

        legacy_enabled = None
        if "enabled" in config:
            legacy_enabled = _coerce_bool(config.get("enabled"), False)
            result["enabled"] = legacy_enabled

        if (
            legacy_enabled is not None
            and not isinstance(raw_modalities, dict)
        ):
            result["modalities"]["image"] = normalize_modality_policy("image", legacy_enabled)
        elif legacy_enabled is not None and isinstance(raw_modalities, dict):
            if not legacy_enabled:
                for key in ("image", "audio", "video"):
                    result["modalities"][key] = normalize_modality_policy(key, False)
            elif not get_enabled_modalities(result.get("modalities")):
                result["modalities"]["image"] = normalize_modality_policy("image", True)

        if "selector" in config and config["selector"]:
            result["selector"] = str(config["selector"]).strip()
            if not result["selector"]:
                result["selector"] = "img"

        if "audio_selector" in config and config["audio_selector"]:
            result["audio_selector"] = str(config["audio_selector"]).strip()
            if not result["audio_selector"]:
                result["audio_selector"] = default["audio_selector"]

        if "video_selector" in config and config["video_selector"]:
            result["video_selector"] = str(config["video_selector"]).strip()
            if not result["video_selector"]:
                result["video_selector"] = default["video_selector"]

        if "container_selector" in config:
            val = config["container_selector"]
            result["container_selector"] = str(val).strip() if val else None

        if "final_target_strategy" in config:
            val = str(config["final_target_strategy"] or "").strip().lower()
            if val in ("container", "latest_reply", "latest_visual_reply"):
                result["final_target_strategy"] = val

        if "latest_visual_column" in config:
            val = str(config["latest_visual_column"] or "").strip().lower()
            if val in ("left", "right"):
                result["latest_visual_column"] = val

        if "allow_container_fallback" in config:
            result["allow_container_fallback"] = _coerce_bool(config.get("allow_container_fallback"), False)

        if "force_postprocess" in config:
            result["force_postprocess"] = _coerce_bool(config.get("force_postprocess"), False)

        if "arena_image_generation" in config:
            result["arena_image_generation"] = _coerce_bool(
                config.get("arena_image_generation"), False
            )

        if "direct_postprocess_modalities" in config:
            raw_direct_modalities = config.get("direct_postprocess_modalities")
            if isinstance(raw_direct_modalities, (list, tuple, set)):
                allowed_direct_modalities = []
                for item in raw_direct_modalities:
                    media_type = str(item or "").strip().lower()
                    if (
                        media_type in {"image", "audio", "video"}
                        and is_modality_enabled(result.get("modalities"), media_type)
                        and media_type not in allowed_direct_modalities
                    ):
                        allowed_direct_modalities.append(media_type)
                if allowed_direct_modalities:
                    result["direct_postprocess_modalities"] = allowed_direct_modalities

        if "debounce_seconds" in config:
            try:
                val = float(config["debounce_seconds"])
                result["debounce_seconds"] = max(0, min(val, 30))
            except (ValueError, TypeError):
                pass

        if "wait_for_load" in config:
            result["wait_for_load"] = _coerce_bool(config.get("wait_for_load"), True)

        if "load_timeout_seconds" in config:
            try:
                val = float(config["load_timeout_seconds"])
                result["load_timeout_seconds"] = max(1, min(val, 60))
            except (ValueError, TypeError):
                pass

        if "download_blobs" in config:
            result["download_blobs"] = _coerce_bool(config.get("download_blobs"), False)

        if "max_size_mb" in config:
            try:
                val = int(config["max_size_mb"])
                result["max_size_mb"] = max(1, min(val, 100))
            except (ValueError, TypeError):
                pass

        if "canvas_export_mime" in config:
            val = str(config["canvas_export_mime"] or "").strip().lower()
            if val in {"image/jpeg", "image/webp", "image/png"}:
                result["canvas_export_mime"] = val

        if "canvas_export_quality" in config:
            try:
                val = float(config["canvas_export_quality"])
                result["canvas_export_quality"] = max(0.1, min(val, 1.0))
            except (ValueError, TypeError):
                pass

        if "src_allow_patterns" in config:
            raw_patterns = config.get("src_allow_patterns")
            if isinstance(raw_patterns, str):
                raw_patterns = raw_patterns.replace("\r\n", "\n").replace(";", "\n").split("\n")
            if isinstance(raw_patterns, (list, tuple, set)):
                patterns = []
                seen = set()
                for item in raw_patterns:
                    text = str(item or "").strip()
                    if not text or text in seen:
                        continue
                    seen.add(text)
                    patterns.append(text)
                result["src_allow_patterns"] = patterns

        if "mode" in config:
            val = str(config["mode"]).lower()
            if val in ("all", "first", "last"):
                result["mode"] = val

        if "audio_capture_enabled" in config:
            result["audio_capture_enabled"] = _coerce_bool(config.get("audio_capture_enabled"), False)

        if "audio_capture_mute_playback" in config:
            result["audio_capture_mute_playback"] = _coerce_bool(config.get("audio_capture_mute_playback"), False)

        if "audio_capture_preload_enabled" in config:
            result["audio_capture_preload_enabled"] = _coerce_bool(config.get("audio_capture_preload_enabled"), False)

        if "audio_capture_reload_before_workflow" in config:
            result["audio_capture_reload_before_workflow"] = _coerce_bool(config.get("audio_capture_reload_before_workflow"), False)

        if "audio_capture_preserve_graph" in config:
            result["audio_capture_preserve_graph"] = _coerce_bool(config.get("audio_capture_preserve_graph"), False)

        if "audio_capture_terminal_settle_seconds" in config:
            try:
                val = float(config["audio_capture_terminal_settle_seconds"])
                result["audio_capture_terminal_settle_seconds"] = max(0.0, min(val, 5.0))
            except (ValueError, TypeError):
                pass

        if "audio_trigger_selector" in config:
            result["audio_trigger_selector"] = str(config["audio_trigger_selector"] or "").strip()

        if "audio_trigger_labels" in config:
            raw_labels = config.get("audio_trigger_labels")
            if isinstance(raw_labels, (list, tuple)):
                labels = [
                    str(item).strip()
                    for item in raw_labels
                    if str(item).strip()
                ]
                if labels:
                    result["audio_trigger_labels"] = labels

        if "audio_capture_max_wait_seconds" in config:
            try:
                val = float(config["audio_capture_max_wait_seconds"])
                result["audio_capture_max_wait_seconds"] = max(1.0, min(val, 120.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_min_wait_seconds" in config:
            try:
                val = float(config["audio_capture_min_wait_seconds"])
                result["audio_capture_min_wait_seconds"] = max(0.2, min(val, 30.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_hard_max_wait_seconds" in config:
            try:
                val = float(config["audio_capture_hard_max_wait_seconds"])
                result["audio_capture_hard_max_wait_seconds"] = max(1.0, min(val, 180.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_estimated_chars_per_second" in config:
            try:
                val = float(config["audio_capture_estimated_chars_per_second"])
                result["audio_capture_estimated_chars_per_second"] = max(1.0, min(val, 20.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_wait_padding_seconds" in config:
            try:
                val = float(config["audio_capture_wait_padding_seconds"])
                result["audio_capture_wait_padding_seconds"] = max(0.0, min(val, 10.0))
            except (ValueError, TypeError):
                pass

        network_capture = dict(result.get("audio_network_capture") or {})
        raw_network_capture = config.get("audio_network_capture")
        if isinstance(raw_network_capture, dict):
            if "enabled" in raw_network_capture:
                network_capture["enabled"] = _coerce_bool(raw_network_capture.get("enabled"), False)
            if "timeout_seconds" in raw_network_capture:
                try:
                    val = float(raw_network_capture["timeout_seconds"])
                    network_capture["timeout_seconds"] = max(0.1, min(val, 15.0))
                except (ValueError, TypeError):
                    pass
            if "transport" in raw_network_capture:
                val = str(raw_network_capture["transport"] or "").strip()
                if val in {"page_websocket_probe"}:
                    network_capture["transport"] = val
            if "extractor" in raw_network_capture:
                val = str(raw_network_capture["extractor"] or "").strip()
                if val in {"voicegenie_ogg_pages", "voicegenie_binary_stream"}:
                    network_capture["extractor"] = val
            if "settle_seconds" in raw_network_capture:
                try:
                    val = float(raw_network_capture["settle_seconds"])
                    network_capture["settle_seconds"] = max(0.05, min(val, 5.0))
                except (ValueError, TypeError):
                    pass
            if "url_patterns" in raw_network_capture:
                raw_patterns = raw_network_capture.get("url_patterns")
                if isinstance(raw_patterns, (list, tuple)):
                    patterns = [
                        str(item).strip()
                        for item in raw_patterns
                        if str(item).strip()
                    ]
                    if patterns:
                        network_capture["url_patterns"] = patterns
            if "max_payload_bytes" in raw_network_capture:
                # 修复：此前漏处理该键，导致前端设置的单条载荷上限被静默重置为默认 10MB
                try:
                    val = int(raw_network_capture["max_payload_bytes"])
                    network_capture["max_payload_bytes"] = max(65536, min(val, 104857600))
                except (ValueError, TypeError):
                    pass

        # 兼容旧平铺字段，最终统一收口到新对象
        if "audio_network_capture_enabled" in config:
            network_capture["enabled"] = _coerce_bool(config.get("audio_network_capture_enabled"), False)

        if "audio_network_capture_timeout_seconds" in config:
            try:
                val = float(config["audio_network_capture_timeout_seconds"])
                network_capture["timeout_seconds"] = max(0.1, min(val, 15.0))
            except (ValueError, TypeError):
                pass

        if "audio_network_url_patterns" in config:
            raw_patterns = config.get("audio_network_url_patterns")
            if isinstance(raw_patterns, (list, tuple)):
                patterns = [
                    str(item).strip()
                    for item in raw_patterns
                    if str(item).strip()
                ]
                if patterns:
                    network_capture["url_patterns"] = patterns

        result["audio_network_capture"] = network_capture

        browser_tts_fallback = dict(result.get("audio_browser_tts_fallback") or {})
        raw_browser_tts_fallback = config.get("audio_browser_tts_fallback")
        if isinstance(raw_browser_tts_fallback, dict):
            if "enabled" in raw_browser_tts_fallback:
                browser_tts_fallback["enabled"] = _coerce_bool(raw_browser_tts_fallback.get("enabled"), False)
            if "provider" in raw_browser_tts_fallback:
                val = str(raw_browser_tts_fallback["provider"] or "").strip()
                if val in {"doubao_samantha"}:
                    browser_tts_fallback["provider"] = val
            if "speaker" in raw_browser_tts_fallback:
                val = str(raw_browser_tts_fallback["speaker"] or "").strip()
                if val:
                    browser_tts_fallback["speaker"] = val
            if "speech_rate" in raw_browser_tts_fallback:
                try:
                    val = int(raw_browser_tts_fallback["speech_rate"])
                    browser_tts_fallback["speech_rate"] = max(-100, min(val, 100))
                except (ValueError, TypeError):
                    pass
            if "pitch" in raw_browser_tts_fallback:
                try:
                    val = int(raw_browser_tts_fallback["pitch"])
                    browser_tts_fallback["pitch"] = max(-100, min(val, 100))
                except (ValueError, TypeError):
                    pass
            if "format" in raw_browser_tts_fallback:
                val = str(raw_browser_tts_fallback["format"] or "").strip().lower()
                if val in {"aac"}:
                    browser_tts_fallback["format"] = val
            if "timeout_seconds" in raw_browser_tts_fallback:
                try:
                    val = float(raw_browser_tts_fallback["timeout_seconds"])
                    browser_tts_fallback["timeout_seconds"] = max(3.0, min(val, 120.0))
                except (ValueError, TypeError):
                    pass
            for key in (
                "pc_version",
                "aid",
                "real_aid",
                "language",
                "device_platform",
                "pkg_type",
                "region",
                "sys_region",
                "use_olympus_account",
                "samantha_web",
            ):
                if key in raw_browser_tts_fallback:
                    val = str(raw_browser_tts_fallback[key] or "").strip()
                    if val:
                        browser_tts_fallback[key] = val

        result["audio_browser_tts_fallback"] = browser_tts_fallback

        if "audio_capture_poll_seconds" in config:
            try:
                val = float(config["audio_capture_poll_seconds"])
                result["audio_capture_poll_seconds"] = max(0.05, min(val, 5.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_silence_seconds" in config:
            try:
                val = float(config["audio_capture_silence_seconds"])
                result["audio_capture_silence_seconds"] = max(0.2, min(val, 30.0))
            except (ValueError, TypeError):
                pass

        if "audio_capture_activity_threshold" in config:
            try:
                val = float(config["audio_capture_activity_threshold"])
                result["audio_capture_activity_threshold"] = max(0.0001, min(val, 0.2))
            except (ValueError, TypeError):
                pass

        if "audio_capture_activity_silence_seconds" in config:
            try:
                val = float(config["audio_capture_activity_silence_seconds"])
                result["audio_capture_activity_silence_seconds"] = max(0.2, min(val, 10.0))
            except (ValueError, TypeError):
                pass

        result["enabled"] = bool(get_enabled_modalities(result.get("modalities")))

        return result
    def get_site_file_paste_config(self, domain: str, preset_name: str = None) -> dict:
        """获取站点的文件粘贴配置"""
        self.refresh_if_changed()

        default_config = get_default_file_paste_config()

        data = self._get_site_data(domain, preset_name)
        if data is None:
            result = copy.deepcopy(default_config)
            result["send_confirmation"] = get_default_send_confirmation_config()
            result["attachment_monitor"] = get_default_attachment_monitor_config()
            return result

        return self._validate_file_paste_config(
            data.get("file_paste", {}),
            legacy_stream_config=data.get("stream_config"),
            include_attachment_defaults=True,
        )
    def set_site_file_paste_config(self, domain: str, config: dict,
                                    preset_name: str = None) -> bool:
        """设置站点的文件粘贴配置"""
        self.refresh_if_changed()

        data = self._get_site_data(domain, preset_name)
        if data is None:
            logger.warning(f"站点或预设不存在: {domain}/{preset_name}")
            return False

        current_config = self._validate_file_paste_config(
            data.get("file_paste", {}),
            legacy_stream_config=data.get("stream_config"),
        )
        merged_config = _merge_config_patch(current_config, config if isinstance(config, dict) else {})
        validated = self._validate_file_paste_config(
            merged_config,
            legacy_stream_config=data.get("stream_config"),
        )

        if not self._assign_preset_field_and_save(data, "file_paste", validated):
            return False

        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 文件粘贴配置已更新")
        return True
    def get_all_file_paste_configs(self) -> dict:
        """获取所有站点的文件粘贴配置（使用各站点当前默认预设）"""
        self.refresh_if_changed()
        result = {}

        for domain in self.sites:
            if domain.startswith('_'):
                continue

            data = self._get_site_data(domain)
            if data is None:
                continue

            result[domain] = self._validate_file_paste_config(
                data.get("file_paste", {}),
                legacy_stream_config=data.get("stream_config"),
                include_attachment_defaults=True,
            )

        return result
    def get_site_prompt_padding_config(self, domain: str, preset_name: str = None) -> Dict[str, Any]:
        """获取站点的提示词开头注入配置"""
        self.refresh_if_changed()

        data = self._get_site_data(domain, preset_name)
        if data is None:
            return copy.deepcopy(get_default_prompt_padding_config())

        return self._validate_prompt_padding_config(data.get("prompt_padding", {}))
    def set_site_prompt_padding_config(
        self,
        domain: str,
        config: Dict[str, Any],
        preset_name: str = None,
    ) -> bool:
        """设置站点的提示词开头注入配置"""
        self.refresh_if_changed()

        data = self._get_site_data(domain, preset_name)
        if data is None:
            logger.warning(f"站点或预设不存在: {domain}/{preset_name}")
            return False

        current_config = self._validate_prompt_padding_config(data.get("prompt_padding", {}))
        merged_config = _merge_config_patch(current_config, config if isinstance(config, dict) else {})
        validated = self._validate_prompt_padding_config(merged_config)
        if not self._assign_preset_field_and_save(data, "prompt_padding", validated):
            return False

        logger.info(f"站点 {domain} [{preset_name or DEFAULT_PRESET_NAME}] 提示词开头注入配置已更新")
        return True
    def _validate_file_paste_config(
        self,
        config: dict,
        *,
        legacy_stream_config: Optional[Dict[str, Any]] = None,
        include_attachment_defaults: bool = False,
    ) -> dict:
        """验证并规范化文件粘贴配置"""
        default = get_default_file_paste_config()
        result = copy.deepcopy(default)

        if not isinstance(config, dict):
            config = {}

        if "enabled" in config:
            result["enabled"] = _coerce_bool(config.get("enabled"), False)

        if "threshold" in config:
            try:
                val = int(config["threshold"])
                result["threshold"] = max(1000, min(val, 10000000))
            except (ValueError, TypeError):
                pass

        if "temp_file_type" in config:
            val = str(config.get("temp_file_type") or "").strip().lower().lstrip(".")
            if val in {"txt", "pdf", "chunk", "error"}:
                result["temp_file_type"] = val

        if "hint_text" in config:
            val = str(config["hint_text"]).strip()
            # 限制长度，避免过长的引导文本
            hint_val = val[:500] if val else ""
            result["hint_text"] = hint_val
            
            # 智能向后兼容：结合老配置的策略类型，防止新字段被无关的旧数据污染
            old_temp_type = config.get("temp_file_type", "txt")
            
            if "txt_hint_text" not in config:
                result["txt_hint_text"] = hint_val if old_temp_type == "txt" else "完全专注于文件内容"
            if "pdf_hint_text" not in config:
                result["pdf_hint_text"] = hint_val if old_temp_type == "pdf" else "完全专注于文件内容"
            if "error_hint_text" not in config:
                result["error_hint_text"] = hint_val if old_temp_type == "error" else "输入文本长度超过限制，已中止发送"

        if "txt_hint_text" in config:
            val = str(config["txt_hint_text"]).strip()
            result["txt_hint_text"] = val[:500] if val else ""

        if "pdf_hint_text" in config:
            val = str(config["pdf_hint_text"]).strip()
            result["pdf_hint_text"] = val[:500] if val else ""

        if "error_hint_text" in config:
            val = str(config["error_hint_text"]).strip()
            result["error_hint_text"] = val[:500] if val else ""

        if "reacquire_input_after_upload" in config:
            result["reacquire_input_after_upload"] = _coerce_bool(config.get("reacquire_input_after_upload"), False)

        if "post_upload_input_selector" in config:
            val = str(config["post_upload_input_selector"] or "").strip()
            result["post_upload_input_selector"] = val[:500] if val else ""

        if "post_upload_settle" in config:
            try:
                val = float(config["post_upload_settle"])
                result["post_upload_settle"] = max(0.0, min(val, 30.0))
            except (ValueError, TypeError):
                pass

        if "upload_signal_timeout" in config:
            try:
                val = float(config["upload_signal_timeout"])
                result["upload_signal_timeout"] = max(0.5, min(val, 120.0))
            except (ValueError, TypeError):
                pass

        if "upload_signal_grace" in config:
            try:
                val = float(config["upload_signal_grace"])
                result["upload_signal_grace"] = max(0.0, min(val, 120.0))
            except (ValueError, TypeError):
                pass

        default_state_probe = copy.deepcopy(default.get("state_probe") or {})
        raw_state_probe = config.get("state_probe")
        state_probe = copy.deepcopy(default_state_probe)
        if isinstance(raw_state_probe, dict):
            if "enabled" in raw_state_probe:
                state_probe["enabled"] = _coerce_bool(raw_state_probe.get("enabled"), False)
            if "code" in raw_state_probe:
                code = str(raw_state_probe["code"] or "").strip()
                state_probe["code"] = code[:20000] if code else ""
        if state_probe:
            result["state_probe"] = state_probe

        legacy_send_confirmation = {}
        if isinstance(legacy_stream_config, dict) and isinstance(legacy_stream_config.get("send_confirmation"), dict):
            legacy_send_confirmation.update(legacy_stream_config.get("send_confirmation") or {})
        raw_send_confirmation = config.get("send_confirmation")
        if isinstance(raw_send_confirmation, dict):
            legacy_send_confirmation.update(raw_send_confirmation)
        if legacy_send_confirmation or include_attachment_defaults:
            result["send_confirmation"] = self._validate_send_confirmation_config(legacy_send_confirmation)

        legacy_attachment_monitor = {}
        if isinstance(legacy_stream_config, dict) and isinstance(legacy_stream_config.get("attachment_monitor"), dict):
            legacy_attachment_monitor.update(legacy_stream_config.get("attachment_monitor") or {})
        raw_attachment_monitor = config.get("attachment_monitor")
        if isinstance(raw_attachment_monitor, dict):
            legacy_attachment_monitor.update(raw_attachment_monitor)
        if legacy_attachment_monitor or include_attachment_defaults:
            result["attachment_monitor"] = self._validate_attachment_monitor_config(legacy_attachment_monitor)

        from app.utils.attachments import attachment_config
        if "attachments" in config or include_attachment_defaults:
            result["attachments"] = attachment_config(config.get("attachments"))

        return result
    def _validate_prompt_padding_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """验证并规范化提示词开头注入配置。"""
        result = copy.deepcopy(get_default_prompt_padding_config())

        if not isinstance(config, dict):
            return result

        if "enabled" in config:
            result["enabled"] = _coerce_bool(config.get("enabled"), False)

        if "marker_text" in config:
            marker_text = str(config.get("marker_text") or "").strip()
            result["marker_text"] = marker_text

        if "segments_per_side" in config:
            try:
                segments_per_side = int(config.get("segments_per_side"))
            except (TypeError, ValueError):
                segments_per_side = int(result["segments_per_side"])
            result["segments_per_side"] = max(0, min(segments_per_side, 64))

        if "random_insert_enabled" in config:
            result["random_insert_enabled"] = _coerce_bool(
                config.get("random_insert_enabled"), False
            )

        if "random_insert_chars" in config:
            result["random_insert_chars"] = str(
                config.get("random_insert_chars") or ""
            )

        return result
    def list_image_presets(self):
        """列出所有可用的图片配置预设"""
        return self.image_presets.list_presets()
    def get_image_preset(self, domain: str):
        """获取指定站点的预设信息"""
        return self.image_presets.get_preset_for_display(domain)
    def apply_image_preset(self, domain: str, preset_domain: str):
        """将预设配置应用到站点"""
        preset_config = self.image_presets.get_preset(preset_domain)

        if not preset_config:
            raise ValueError(f"找不到预设: {preset_domain}")

        return self.set_site_image_config(domain, preset_config)
    def reload_presets(self):
        """重新加载图片预设"""
        self.image_presets.reload()
