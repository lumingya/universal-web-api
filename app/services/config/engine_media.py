"""ConfigEngine 的一部分（R2-3 拆分）：图片提取、文件粘贴、提示词填充配置与图片预设。

方法原样从 engine.py 迁出，行为不变；通过 mixin 组合回 ConfigEngine，
实例状态都在 ConfigEngine.__init__（engine.py）里初始化。
"""

import copy
from typing import Any, Dict, List, Optional

from app.models.schemas import (
    get_default_attachment_monitor_config,
    get_default_file_paste_config,
    get_default_image_extraction_config,
    get_default_prompt_padding_config,
    get_default_send_confirmation_config,
    get_enabled_modalities,
    is_modality_enabled,
    normalize_modalities_config,
    normalize_modality_policy,
)

from .engine_common import DEFAULT_PRESET_NAME, _coerce_bool, _merge_config_patch, logger


def _normalize_image_modalities(config: Dict, result: Dict, default: Dict) -> None:
    """规范化 image_extraction 配置：模态开关与 legacy `enabled` 的兼容处理。

    从 _validate_image_config 中原样提出，方法体与提取前逐字节相同
    （仅缩进变化）。只依赖 (config, result, default)，与其它组无耦合，
    因此可以独立测试。
    """
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

# ==================== 图片提取配置的逐键归一化 ====================
#
# _normalize_image_extraction 原本是 19 段 `if '<key>' in config:`，与文件粘贴
# 那段同形（取值 -> 归一化 -> 写回 result），收敛成一张 (键, 归一化函数) 表。
#
# 各段规则并不完全一致，重构时最容易顺手抹平，故逐条记下（均已由
# tests/test_image_extraction_normalization.py 锁定）：
#   - selector 空值回退字面量 "img"，audio/video_selector 回退 default[key]；
#   - mode 只走 str(x).lower()，不 strip、不做 `or ""`，与其它枚举字段不同；
#   - max_size_mb 走 int()（截断），其余数值字段走 float()；
#   - 五个 bool 字段里只有 wait_for_load 的兜底值是 True；
#   - direct_postprocess_modalities / final_target_strategy 等 5 个键不在默认
#     配置里，它们的"保持默认"就是不往 result 里写这个键。

_UNSET = object()


def _norm_image_selector(key: str, fallback=_UNSET):
    """只在值为真时处理：strip 后为空则回退。

    fallback 省略时回退 default[key]；给了字面量就回退字面量。
    """

    def _apply(result: Dict, config: Dict, default: Dict) -> None:
        if not config.get(key):
            return
        value = str(config[key]).strip()
        result[key] = value or (default[key] if fallback is _UNSET else fallback)

    return _apply


def _norm_image_optional_str(key: str):
    """键出现就写：真值 strip 成字符串，假值写 None。"""

    def _apply(result: Dict, config: Dict, default: Dict) -> None:
        val = config.get(key)
        result[key] = str(val).strip() if val else None

    return _apply


def _norm_image_enum(key: str, members):
    """归一化为小写并去空白后须在集合内，否则不写入。"""

    def _apply(result: Dict, config: Dict, default: Dict) -> None:
        val = str(config.get(key) or "").strip().lower()
        if val in members:
            result[key] = val

    return _apply


def _norm_image_bool(key: str, fallback: bool):
    """按 _coerce_bool 的白名单规则转 bool；认不出的值取 fallback。"""

    def _apply(result: Dict, config: Dict, default: Dict) -> None:
        result[key] = _coerce_bool(config.get(key), fallback)

    return _apply


def _norm_image_clamped(key: str, low, high, cast):
    """转数值后钳制到 [low, high]；转换失败则保持默认（不写入）。"""

    def _apply(result: Dict, config: Dict, default: Dict) -> None:
        try:
            result[key] = max(low, min(cast(config[key]), high))
        except (ValueError, TypeError):
            pass

    return _apply


def _norm_image_mode(result: Dict, config: Dict, default: Dict) -> None:
    """mode 是唯一不 strip、不做 `or ""` 的枚举字段，单独处理。"""
    val = str(config.get("mode")).lower()
    if val in ("all", "first", "last"):
        result["mode"] = val


def _norm_image_direct_modalities(result: Dict, config: Dict, default: Dict) -> None:
    """只保留已启用且属于 {image,audio,video} 的模态，去重后非空才写入。"""
    raw = config.get("direct_postprocess_modalities")
    if not isinstance(raw, (list, tuple, set)):
        return
    allowed: List[str] = []
    for item in raw:
        media_type = str(item or "").strip().lower()
        if (
            media_type in {"image", "audio", "video"}
            and is_modality_enabled(result.get("modalities"), media_type)
            and media_type not in allowed
        ):
            allowed.append(media_type)
    if allowed:
        result["direct_postprocess_modalities"] = allowed


def _norm_image_src_allow_patterns(result: Dict, config: Dict, default: Dict) -> None:
    """字符串按换行/分号切分，随后去空白、丢空、去重。"""
    raw = config.get("src_allow_patterns")
    if isinstance(raw, str):
        raw = raw.replace("\r\n", "\n").replace(";", "\n").split("\n")
    if not isinstance(raw, (list, tuple, set)):
        return
    patterns: List[str] = []
    seen = set()
    for item in raw:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        patterns.append(text)
    result["src_allow_patterns"] = patterns


# 逐键归一化表。表中顺序即应用顺序，与原实现一致。
_IMAGE_EXTRACTION_NORMALIZERS = (
    ("selector", _norm_image_selector("selector", "img")),
    ("audio_selector", _norm_image_selector("audio_selector")),
    ("video_selector", _norm_image_selector("video_selector")),
    ("container_selector", _norm_image_optional_str("container_selector")),
    ("final_target_strategy", _norm_image_enum("final_target_strategy", ("container", "latest_reply", "latest_visual_reply"))),
    ("latest_visual_column", _norm_image_enum("latest_visual_column", ("left", "right"))),
    ("allow_container_fallback", _norm_image_bool("allow_container_fallback", False)),
    ("force_postprocess", _norm_image_bool("force_postprocess", False)),
    ("arena_image_generation", _norm_image_bool("arena_image_generation", False)),
    ("direct_postprocess_modalities", _norm_image_direct_modalities),
    ("debounce_seconds", _norm_image_clamped("debounce_seconds", 0, 30, float)),
    ("wait_for_load", _norm_image_bool("wait_for_load", True)),
    ("load_timeout_seconds", _norm_image_clamped("load_timeout_seconds", 1, 60, float)),
    ("download_blobs", _norm_image_bool("download_blobs", False)),
    ("max_size_mb", _norm_image_clamped("max_size_mb", 1, 100, int)),
    ("canvas_export_mime", _norm_image_enum("canvas_export_mime", {"image/jpeg", "image/webp", "image/png"})),
    ("canvas_export_quality", _norm_image_clamped("canvas_export_quality", 0.1, 1.0, float)),
    ("src_allow_patterns", _norm_image_src_allow_patterns),
    ("mode", _norm_image_mode),
)


def _normalize_image_extraction(config: Dict, result: Dict, default: Dict) -> None:
    """规范化 image_extraction 配置：选择器、后处理策略、超时与尺寸等提取参数。

    从 _validate_image_config 中原样提出。只依赖 (config, result, default)，
    与其它组无耦合，因此可以独立测试。
    """
    for key, normalize in _IMAGE_EXTRACTION_NORMALIZERS:
        if key in config:
            normalize(result, config, default)

def _normalize_image_audio_capture(config: Dict, result: Dict, default: Dict) -> None:
    """规范化 image_extraction 配置：音频采集相关参数。

    从 _validate_image_config 中原样提出，方法体与提取前逐字节相同
    （仅缩进变化）。只依赖 (config, result, default)，与其它组无耦合，
    因此可以独立测试。
    """
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

def _normalize_image_network_capture(config: Dict, result: Dict, default: Dict) -> None:
    """规范化 image_extraction 配置：音频网络采集（含 URL 模式白名单）。

    从 _validate_image_config 中原样提出，方法体与提取前逐字节相同
    （仅缩进变化）。只依赖 (config, result, default)，与其它组无耦合，
    因此可以独立测试。
    """
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

def _normalize_image_browser_tts_fallback(config: Dict, result: Dict, default: Dict) -> None:
    """规范化 image_extraction 配置：浏览器 TTS 兜底配置。

    从 _validate_image_config 中原样提出，方法体与提取前逐字节相同
    （仅缩进变化）。只依赖 (config, result, default)，与其它组无耦合，
    因此可以独立测试。
    """
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

def _normalize_image_audio_activity(config: Dict, result: Dict, default: Dict) -> None:
    """规范化 image_extraction 配置：音频活动检测参数，并按启用的模态收敛 result["enabled"]。

    从 _validate_image_config 中原样提出，方法体与提取前逐字节相同
    （仅缩进变化）。只依赖 (config, result, default)，与其它组无耦合，
    因此可以独立测试。
    """
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

_IMAGE_CONFIG_NORMALIZERS = (
    _normalize_image_modalities,
    _normalize_image_extraction,
    _normalize_image_audio_capture,
    _normalize_image_network_capture,
    _normalize_image_browser_tts_fallback,
    _normalize_image_audio_activity,
)


# ==================== 文件粘贴配置的逐键归一化 ====================
#
# _validate_file_paste_config 原本是 12 段 `if '<key>' in config:` 抄同样的
# 模式：取值 -> 归一化 -> 写回 result，转换失败则保持默认。下面把它拆成
# 一张 (键, 归一化函数) 表；表中顺序即应用顺序，与原实现一致。


def _norm_file_paste_bool(key: str):
    """取值强制转 bool 的字段。"""

    def _apply(result: Dict, config: Dict) -> None:
        result[key] = _coerce_bool(config.get(key), False)

    return _apply


def _norm_file_paste_hint(key: str, limit: int = 500):
    """纯文本字段：去空白后截断到 limit 个字符。"""

    def _apply(result: Dict, config: Dict) -> None:
        value = str(config[key]).strip()
        result[key] = value[:limit] if value else ""

    return _apply


def _norm_file_paste_clamped_float(key: str, low: float, high: float):
    """数值窗口字段：转 float 后钳制到 [low, high]；转换失败保持默认。"""

    def _apply(result: Dict, config: Dict) -> None:
        try:
            value = float(config[key])
            result[key] = max(low, min(value, high))
        except (ValueError, TypeError):
            pass

    return _apply


def _norm_file_paste_threshold(result: Dict, config: Dict) -> None:
    """threshold：先 int() 再钳制；转换失败保持默认。"""
    try:
        value = int(config["threshold"])
        result["threshold"] = max(1000, min(value, 10000000))
    except (ValueError, TypeError):
        pass


def _norm_file_paste_temp_file_type(result: Dict, config: Dict) -> None:
    """temp_file_type：归一化后必须落在枚举内，否则保持默认（不报错）。"""
    value = str(config.get("temp_file_type") or "").strip().lower().lstrip(".")
    if value in {"txt", "pdf", "chunk", "error"}:
        result["temp_file_type"] = value


def _norm_file_paste_hint_text(result: Dict, config: Dict) -> None:
    """hint_text：截断后写入，并按 temp_file_type 联动三个分类型文本。

    联动只作用于**未显式给出**的分类型文本，显式给出的稍后由各自的归一化
    函数覆盖——这就是表里 hint_text 必须排在三个分类型键之前的原因。
    """
    value = str(config["hint_text"]).strip()
    hint_value = value[:500] if value else ""
    result["hint_text"] = hint_value

    # 智能向后兼容：结合老配置的策略类型，防止新字段被无关的旧数据污染
    old_temp_type = config.get("temp_file_type", "txt")

    if "txt_hint_text" not in config:
        result["txt_hint_text"] = hint_value if old_temp_type == "txt" else "完全专注于文件内容"
    if "pdf_hint_text" not in config:
        result["pdf_hint_text"] = hint_value if old_temp_type == "pdf" else "完全专注于文件内容"
    if "error_hint_text" not in config:
        result["error_hint_text"] = (
            hint_value if old_temp_type == "error" else "输入文本长度超过限制，已中止发送"
        )


def _norm_file_paste_post_upload_selector(result: Dict, config: Dict) -> None:
    value = str(config["post_upload_input_selector"] or "").strip()
    result["post_upload_input_selector"] = value[:500] if value else ""


# (键, 归一化函数)，顺序与原实现逐字一致
_FILE_PASTE_NORMALIZERS = (
    ("enabled", _norm_file_paste_bool("enabled")),
    ("threshold", _norm_file_paste_threshold),
    ("temp_file_type", _norm_file_paste_temp_file_type),
    ("hint_text", _norm_file_paste_hint_text),
    ("txt_hint_text", _norm_file_paste_hint("txt_hint_text")),
    ("pdf_hint_text", _norm_file_paste_hint("pdf_hint_text")),
    ("error_hint_text", _norm_file_paste_hint("error_hint_text")),
    ("reacquire_input_after_upload", _norm_file_paste_bool("reacquire_input_after_upload")),
    ("post_upload_input_selector", _norm_file_paste_post_upload_selector),
    ("post_upload_settle", _norm_file_paste_clamped_float("post_upload_settle", 0.0, 30.0)),
    ("upload_signal_timeout", _norm_file_paste_clamped_float("upload_signal_timeout", 0.5, 120.0)),
    ("upload_signal_grace", _norm_file_paste_clamped_float("upload_signal_grace", 0.0, 120.0)),
)


def _normalize_file_paste_state_probe(default_state_probe: Dict, raw_state_probe: Any) -> Dict:
    """state_probe 子段：以默认值为基底，逐键覆盖。非 dict 输入等价于缺省。"""
    state_probe = copy.deepcopy(default_state_probe)
    if isinstance(raw_state_probe, dict):
        if "enabled" in raw_state_probe:
            state_probe["enabled"] = _coerce_bool(raw_state_probe.get("enabled"), False)
        if "code" in raw_state_probe:
            code = str(raw_state_probe["code"] or "").strip()
            state_probe["code"] = code[:20000] if code else ""
    return state_probe


def _merge_legacy_subsection(
    legacy_stream_config: Optional[Dict[str, Any]],
    config: Dict,
    key: str,
) -> Dict:
    """老配置的同名子段作基底，当前 config 里的同名子段覆盖它。"""
    merged: Dict = {}
    if isinstance(legacy_stream_config, dict) and isinstance(legacy_stream_config.get(key), dict):
        merged.update(legacy_stream_config.get(key) or {})
    raw = config.get(key)
    if isinstance(raw, dict):
        merged.update(raw)
    return merged


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

        # 各组只依赖 (config, result, default)，按原顺序依次应用
        for apply_group in _IMAGE_CONFIG_NORMALIZERS:
            apply_group(config, result, default)

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

        # 逐键归一化：表里顺序即应用顺序（hint_text 必须排在三个分类型文本之前）
        for key, normalize in _FILE_PASTE_NORMALIZERS:
            if key in config:
                normalize(result, config)

        state_probe = _normalize_file_paste_state_probe(
            copy.deepcopy(default.get("state_probe") or {}),
            config.get("state_probe"),
        )
        if state_probe:
            result["state_probe"] = state_probe

        legacy_send_confirmation = _merge_legacy_subsection(
            legacy_stream_config, config, "send_confirmation"
        )
        if legacy_send_confirmation or include_attachment_defaults:
            result["send_confirmation"] = self._validate_send_confirmation_config(
                legacy_send_confirmation
            )

        legacy_attachment_monitor = _merge_legacy_subsection(
            legacy_stream_config, config, "attachment_monitor"
        )
        if legacy_attachment_monitor or include_attachment_defaults:
            result["attachment_monitor"] = self._validate_attachment_monitor_config(
                legacy_attachment_monitor
            )

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
