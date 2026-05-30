# app/core/browser/media.py

import json
import time
import os
import shutil
import subprocess
import base64
import uuid
import binascii
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from typing import Optional, List, Dict, Any, Callable, TYPE_CHECKING
import requests

from app.core.config import logger, AppConfig, BrowserConstants
from app.models.schemas import (
    get_enabled_modalities,
    get_modality_policy,
    get_modality_run_policy,
    is_modality_enabled,
)
from app.core.background_image_downloader import (
    background_image_downloader,
    build_image_download_request_context,
    normalize_remote_image_url,
)
from app.utils.site_url import extract_remote_site_domain
from app.core.tab_pool import TabSession

if TYPE_CHECKING:
    from .main import BrowserCore


class BrowserMediaMixin:
    """媒体文件提取、音频后处理（FFmpeg/FFprobe）、本地落盘、视觉/截图本地化混入类"""

    @staticmethod
    def _media_modalities(image_config: Dict[str, Any]) -> Dict[str, Any]:
        return dict((image_config or {}).get("modalities") or {})

    @staticmethod
    def _media_enabled_types(image_config: Dict[str, Any]) -> List[str]:
        return sorted(get_enabled_modalities((image_config or {}).get("modalities") or {}))

    @staticmethod
    def _media_policy(image_config: Dict[str, Any], media_type: str) -> Dict[str, Any]:
        return dict(get_modality_policy((image_config or {}).get("modalities") or {}, media_type))

    @staticmethod
    def _media_run_policy(image_config: Dict[str, Any], media_type: str) -> str:
        return get_modality_run_policy((image_config or {}).get("modalities") or {}, media_type)

    @staticmethod
    def _media_policy_allows_signal_wait(image_config: Dict[str, Any], media_type: str) -> bool:
        return BrowserMediaMixin._media_run_policy(image_config, media_type) in {
            "on_signal",
            "probe_if_trigger_found",
            "always_probe",
        }

    @staticmethod
    def _media_policy_allows_audio_probe(
        image_config: Dict[str, Any],
        *,
        signal_seen: bool = False,
    ) -> bool:
        policy = BrowserMediaMixin._media_run_policy(image_config, "audio")
        if policy == "always_probe":
            return True
        if policy == "probe_if_trigger_found":
            return True
        if policy == "on_signal" and signal_seen:
            return True
        return False

    @staticmethod
    def _media_quick_probe_timeout(image_config: Dict[str, Any], default: float = 1.0) -> float:
        values = []
        for media_type in BrowserMediaMixin._media_enabled_types(image_config):
            policy = BrowserMediaMixin._media_policy(image_config, media_type)
            try:
                values.append(float(policy.get("quick_probe_timeout_seconds") or default))
            except (TypeError, ValueError):
                pass
        if not values:
            return default
        return max(0.1, min(max(values), 10.0))

    @staticmethod
    def _media_late_wait_timeout(
        image_config: Dict[str, Any],
        media_type: str,
        fallback: float,
    ) -> float:
        policy = BrowserMediaMixin._media_policy(image_config, media_type)
        try:
            return max(0.2, min(float(policy.get("late_wait_timeout_seconds") or fallback), 300.0))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _media_blind_wait_timeout(
        image_config: Dict[str, Any],
        media_type: str,
        fallback: float,
    ) -> float:
        policy = BrowserMediaMixin._media_policy(image_config, media_type)
        raw_value = policy.get("blind_wait_timeout_seconds")
        if raw_value is None:
            raw_value = image_config.get("blind_wait_timeout_seconds")
        try:
            return max(0.0, min(float(raw_value if raw_value is not None else fallback), 300.0))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _media_audio_capture_timeout(image_config: Dict[str, Any], fallback: float) -> float:
        policy = BrowserMediaMixin._media_policy(image_config, "audio")
        try:
            return max(0.2, min(float(policy.get("capture_timeout_seconds") or fallback), 180.0))
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _build_localized_image_item(
        original_item: Dict[str, Any],
        local_path: Path,
        accessible_url: str,
        *,
        mime: Optional[str] = None,
        byte_size: Optional[int] = None,
        source: str = "local_file",
    ) -> Dict[str, Any]:
        new_item = dict(original_item or {})
        new_item["kind"] = "url"
        new_item["url"] = accessible_url
        new_item["data_uri"] = None
        new_item["source"] = source
        new_item["local_path"] = str(local_path)
        if mime is not None:
            new_item["mime"] = mime
        if byte_size is None:
            try:
                byte_size = int(local_path.stat().st_size)
            except Exception:
                byte_size = None
        if byte_size is not None:
            new_item["byte_size"] = byte_size
        return new_item

    def _localize_image_item_from_background_result(
        self,
        original_item: Dict[str, Any],
        background_result: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(background_result, dict):
            return None
        if str(background_result.get("status") or "") != "done":
            return None

        local_path_text = str(background_result.get("local_path") or "").strip()
        accessible_url = str(background_result.get("accessible_url") or "").strip()
        if not local_path_text or not accessible_url:
            return None

        local_path = Path(local_path_text)
        if not local_path.exists():
            return None

        return self._build_localized_image_item(
            original_item,
            local_path,
            accessible_url,
            mime=str(background_result.get("mime") or "").strip() or None,
            byte_size=background_result.get("byte_size"),
            source=str(background_result.get("source") or "background_download"),
        )

    def _prefetch_remote_image_urls(
        self,
        tab,
        image_urls: List[str],
    ) -> int:
        normalized_urls = []
        seen = set()
        for raw_url in image_urls or []:
            normalized = normalize_remote_image_url(raw_url)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_urls.append(normalized)

        if not normalized_urls or tab is None:
            return 0

        cookies_dict, headers = build_image_download_request_context(tab)
        started = 0
        for target_url in normalized_urls:
            result = background_image_downloader.start_download(
                target_url,
                cookies=cookies_dict,
                headers=headers,
            )
            if result:
                started += 1
        if started:
            logger.debug(f"已提交后台图片预下载任务: {started} 个")
        return started

    def _localize_images_with_background_cache(
        self,
        images: List[Dict],
        *,
        wait_seconds: float = 0.0,
    ) -> List[Dict]:
        if not images:
            return images

        localized = list(images)
        hit_count = 0
        for index, item in enumerate(images):
            if str(item.get("kind") or "").strip().lower() != "url":
                continue
            target_url = normalize_remote_image_url(item.get("url"))
            if not target_url:
                continue

            result = background_image_downloader.get_download_result(
                target_url,
                wait=wait_seconds > 0,
                timeout=wait_seconds if wait_seconds > 0 else None,
            )
            new_item = self._localize_image_item_from_background_result(item, result)
            if new_item is None:
                continue
            localized[index] = new_item
            hit_count += 1

        if hit_count:
            logger.debug(f"命中后台图片缓存: {hit_count} 张")
        return localized

    @staticmethod
    def _append_audio_tail_silence(filepath, duration_seconds: float = 0.3):
        """为音频尾部追加一小段静音，避免播放时戛然而止。"""
        from pathlib import Path

        try:
            target = Path(filepath)
        except Exception:
            return filepath

        if duration_seconds <= 0 or not target.exists() or not target.is_file():
            return filepath

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            logger.debug(f"音频尾静音跳过：未找到 ffmpeg，file={target}")
            return filepath

        ffprobe_path = shutil.which("ffprobe")

        def _probe_duration(path_obj: Path) -> float:
            if not ffprobe_path:
                return -1.0
            cmd = [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path_obj),
            ]
            try:
                completed = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if completed.returncode != 0:
                    return -1.0
                return float(str(completed.stdout or "").strip() or -1.0)
            except Exception:
                return -1.0

        duration_text = f"{float(duration_seconds):.3f}".rstrip("0").rstrip(".")
        temp_path = target.with_name(f"{target.stem}_tail{target.suffix}")
        suffix = target.suffix.lower()
        codec_args: list[str] = []
        if suffix in {".ogg", ".oga"}:
            codec_args = ["-c:a", "libvorbis", "-q:a", "5"]
        elif suffix == ".mp3":
            codec_args = ["-c:a", "libmp3lame", "-b:a", "128k"]
        elif suffix in {".m4a", ".mp4"}:
            codec_args = ["-c:a", "aac", "-b:a", "128k"]
        elif suffix == ".wav":
            codec_args = ["-c:a", "pcm_s16le"]
        elif suffix == ".webm":
            codec_args = ["-c:a", "libopus", "-b:a", "96k"]

        original_duration = _probe_duration(target)
        logger.debug(
            f"音频尾静音开始: file={target.name}, ext={suffix or '<none>'}, "
            f"append={duration_text}s, duration_before={original_duration:.3f}"
        )

        if original_duration <= 0:
            logger.debug(
                f"音频尾静音跳过：无法可靠探测原始时长，保留原文件。file={target.name}"
            )
            return filepath

        if suffix == ".webm":
            logger.debug(
                f"音频尾静音跳过：暂不改写 webm，保留原文件。file={target.name}"
            )
            return filepath

        command_variants = [[
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(target),
            "-af",
            f"apad=pad_dur={duration_text}",
            "-t",
            f"{max(0.0, original_duration) + float(duration_seconds):.3f}" if original_duration > 0 else duration_text,
            *codec_args,
            str(temp_path),
        ]]

        for cmd in command_variants:
            try:
                completed = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except Exception as exc:
                logger.debug(f"音频尾静音追加失败（ffmpeg 调用异常）: {exc}")
                continue

            if completed.returncode == 0 and temp_path.exists() and temp_path.stat().st_size > 0:
                try:
                    new_duration = _probe_duration(temp_path)
                    logger.debug(
                        f"音频尾静音生成成功: file={target.name}, "
                        f"duration_after={new_duration:.3f}, size={temp_path.stat().st_size}"
                    )
                    temp_path.replace(target)
                    return str(target)
                except Exception as exc:
                    logger.debug(f"音频尾静音替换失败: {exc}")
                    try:
                        if temp_path.exists():
                            temp_path.unlink()
                    except Exception:
                        pass
                    return filepath

        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        logger.debug(f"音频尾静音未生效，保留原文件: {target.name}")
        return filepath

    def _get_pending_detection_markers(
        self,
        image_config: Dict,
        media_type: str,
        marker_key: str,
    ) -> List[str]:
        default_markers = {
            "audio": {
                "text_contains": [
                    "generated_music_content/",
                    "googleusercontent.com/generated_music_content/",
                ],
                "url_contains": [
                    "generated_music_content/",
                    "googleusercontent.com/generated_music_content/",
                ],
                "label_contains": [],
            },
            "video": {
                "text_contains": [
                    "video_gen_chip/",
                    "googleusercontent.com/video_gen_chip/",
                    "正在生成视频",
                    "视频已准备就绪",
                    "come back later to check",
                    "i'm generating your video",
                    "your video is being generated",
                    "your video is ready",
                ],
                "url_contains": [
                    "video_gen_chip/",
                    "googleusercontent.com/video_gen_chip/",
                ],
                "label_contains": [],
            },
        }

        normalized_media_type = str(media_type or "").strip().lower()
        normalized_marker_key = str(marker_key or "").strip()
        config_root = dict((image_config or {}).get("pending_detection") or {})
        type_config = dict(config_root.get(normalized_media_type) or {})
        configured = type_config.get(normalized_marker_key)

        if isinstance(configured, list):
            markers = [str(item or "").strip().lower() for item in configured if str(item or "").strip()]
            if markers:
                return markers

        return [
            str(item or "").strip().lower()
            for item in default_markers.get(normalized_media_type, {}).get(normalized_marker_key, [])
            if str(item or "").strip()
        ]

    def _is_pending_media_text(self, text: str, image_config: Dict, media_type: str = "") -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False

        media_types = [str(media_type or "").strip().lower()] if media_type else ["audio", "video"]
        for current_type in media_types:
            markers = self._get_pending_detection_markers(
                image_config,
                current_type,
                "text_contains",
            )
            if any(marker in lowered for marker in markers):
                return True
        return False

    def _is_pending_media_item(self, media_item: Dict, image_config: Dict) -> bool:
        media_type = str(media_item.get("media_type") or "").strip().lower()
        ref = str(media_item.get("url") or media_item.get("data_uri") or "").strip().lower()
        label = str(media_item.get("label") or "").strip().lower()

        if not media_type:
            return False

        url_markers = self._get_pending_detection_markers(
            image_config,
            media_type,
            "url_contains",
        )
        label_markers = self._get_pending_detection_markers(
            image_config,
            media_type,
            "label_contains",
        )

        if media_type not in {"audio", "video"}:
            return False

        return any(marker in ref for marker in url_markers) or any(
            marker in label for marker in label_markers
        )

    def _filter_ready_media_items(self, media_items: List[Dict], image_config: Dict) -> List[Dict]:
        return [
            item for item in (media_items or [])
            if not self._is_pending_media_item(item, image_config)
        ]

    def _should_run_media_postprocess(
        self,
        image_config: Dict,
        *,
        request_text_hint: str = "",
        response_text_hint: str = "",
        media_generation_state: Optional[Dict[str, Any]] = None,
        stream_media_items: Optional[List[Dict[str, Any]]] = None,
        dom_stream_media_items: Optional[List[Dict[str, Any]]] = None,
        dom_image_detected: bool = False,
        dom_final_image_urls: Optional[List[str]] = None,
    ) -> tuple[bool, Dict[str, Any]]:
        modalities = self._media_modalities(image_config)
        enabled_types = self._media_enabled_types(image_config)
        stream_media_count = sum(1 for item in (stream_media_items or []) if isinstance(item, dict))
        dom_stream_media_count = sum(1 for item in (dom_stream_media_items or []) if isinstance(item, dict))
        dom_final_image_url_count = sum(1 for item in (dom_final_image_urls or []) if str(item or "").strip())
        force_postprocess = bool((image_config or {}).get("force_postprocess"))
        direct_postprocess_modalities = [
            media_type
            for media_type in (image_config or {}).get("direct_postprocess_modalities", [])
            if media_type in enabled_types
        ]
        media_state = dict(media_generation_state or {})
        media_state_pending = bool(media_state.get("pending"))
        media_state_type = str(media_state.get("media_type") or "").strip().lower()
        media_state_hint = str(media_state.get("hint_text") or "").strip()
        response_hint = str(response_text_hint or "").strip()
        combined_hint = "\n".join(part for part in (response_hint, media_state_hint) if part).lower()
        request_likely_image = self._looks_like_image_generation_request(request_text_hint)
        av_pending_text_signal = (
            ("audio" in enabled_types or "video" in enabled_types)
            and self._is_pending_media_text(combined_hint, image_config)
        )
        image_markers = (
            "image_generation_content/",
            "googleusercontent.com/image_generation_content/",
            "generated image",
            "generating image",
            "images are being generated",
        )
        image_marker_hit = any(marker in combined_hint for marker in image_markers)
        diagnostics: Dict[str, Any] = {
            "enabled_types": enabled_types,
            "run_policies": {
                media_type: self._media_run_policy(image_config, media_type)
                for media_type in enabled_types
            },
            "stream_media_count": stream_media_count,
            "dom_stream_media_count": dom_stream_media_count,
            "dom_image_detected": bool(dom_image_detected),
            "dom_final_image_url_count": dom_final_image_url_count,
            "force_postprocess": force_postprocess,
            "direct_postprocess_modalities": direct_postprocess_modalities,
            "media_state_pending": media_state_pending,
            "media_state_type": media_state_type,
            "media_state_hint_len": len(media_state_hint),
            "response_hint_len": len(response_hint),
            "request_hint_len": len(str(request_text_hint or "").strip()),
            "request_likely_image": request_likely_image,
            "av_pending_text_signal": av_pending_text_signal,
            "image_marker_hit": image_marker_hit,
            "decision": "",
        }

        if not enabled_types:
            diagnostics["decision"] = "disabled"
            return False, diagnostics

        if stream_media_count > 0:
            diagnostics["decision"] = "stream_media_items"
            return True, diagnostics
        if dom_stream_media_count > 0:
            diagnostics["decision"] = "dom_stream_media_items"
            return True, diagnostics
        if dom_final_image_url_count > 0 and "image" in enabled_types:
            diagnostics["decision"] = "dom_final_image_urls"
            return True, diagnostics
        if bool(dom_image_detected) and "image" in enabled_types:
            diagnostics["decision"] = "dom_image_detected"
            return True, diagnostics

        if force_postprocess:
            diagnostics["decision"] = "force_postprocess"
            return True, diagnostics

        if media_state_pending:
            diagnostics["decision"] = "media_state_pending"
            return True, diagnostics

        if av_pending_text_signal:
            diagnostics["decision"] = "audio_video_pending_text_signal"
            return True, diagnostics

        if "image" in enabled_types:
            if request_likely_image:
                diagnostics["decision"] = "request_likely_image"
                return True, diagnostics

            if image_marker_hit:
                diagnostics["decision"] = "image_marker_hint"
                return True, diagnostics

        if direct_postprocess_modalities:
            diagnostics["decision"] = "direct_postprocess_modalities"
            return True, diagnostics

        diagnostics["decision"] = "generic_dom_quick_scan"
        return True, diagnostics

    def _extract_media_after_stream(
        self,
        tab,
        extractor,
        image_config: Dict,
        result_selector: str,
        message_wrapper_selector: str = "",
        completion_id: str = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        response_text_hint: str = "",
        request_text_hint: str = "",
        media_generation_state: Optional[Dict[str, Any]] = None,
        stream_media_items: Optional[List[Dict[str, Any]]] = None,
        direct_modalities: Optional[List[str]] = None,
    ) -> List[Dict]:
        """流式输出结束后提取多模态资源。"""
        from app.core.elements import ElementFinder
        from app.core.extractors.media_extractor import media_extractor

        image_config = dict(image_config or {})
        direct_scan_only = False
        if direct_modalities:
            requested_direct_modalities = {
                str(item or "").strip().lower()
                for item in direct_modalities
                if str(item or "").strip().lower() in {"image", "audio", "video"}
            }
            if requested_direct_modalities:
                direct_scan_only = True
                scoped_modalities = {}
                for media_type in ("image", "audio", "video"):
                    policy = self._media_policy(image_config, media_type)
                    if media_type not in requested_direct_modalities:
                        policy["enabled"] = False
                        policy["run_policy"] = "disabled"
                    scoped_modalities[media_type] = policy
                image_config["modalities"] = scoped_modalities
                image_config["wait_for_load"] = False
                image_config["audio_capture_enabled"] = False
        modalities = self._media_modalities(image_config)
        only_audio_mode = (
            is_modality_enabled(modalities, "audio")
            and not is_modality_enabled(modalities, "image")
            and not is_modality_enabled(modalities, "video")
        )
        debounce = 0.0 if only_audio_mode else image_config.get("debounce_seconds", 2.0)
        effective_stop_checker = stop_checker or self._should_stop_checker
        if debounce > 0:
            elapsed = 0
            step = 0.1
            while elapsed < debounce:
                if effective_stop_checker():
                    return []
                time.sleep(step)
                elapsed += step
        
        finder = ElementFinder(tab)
        fallback_stream_media_items = self._dedupe_media_items(
            [dict(item) for item in (stream_media_items or []) if isinstance(item, dict)]
        )

        def _apply_stream_media_fallback(dom_media_items: List[Dict]) -> List[Dict]:
            if not fallback_stream_media_items:
                return list(dom_media_items or [])

            merged = list(dom_media_items or [])
            ready_types = {
                str(item.get("media_type") or "").strip().lower()
                for item in merged
                if str(item.get("url") or item.get("data_uri") or "").strip()
            }

            appended = 0
            for item in fallback_stream_media_items:
                media_type = str(item.get("media_type") or "").strip().lower()
                if not media_type or media_type in ready_types:
                    continue
                merged.append(dict(item))
                ready_types.add(media_type)
                appended += 1

            if appended:
                logger.debug(f"DOM 媒体缺失，已回退合并网络流媒体: {appended} 项")

            return self._dedupe_media_items(merged)

        def _find_candidate_elements(timeout: float = 1.0):
            primary_elements = []
            fallback_elements = []

            try:
                if result_selector:
                    primary_elements = finder.find_all(result_selector, timeout=timeout) or []
            except Exception as e:
                logger.debug(f"主结果容器查找失败（忽略）: {e}")
                primary_elements = []

            if primary_elements:
                return primary_elements, "result_container"

            try:
                if message_wrapper_selector:
                    fallback_elements = finder.find_all(message_wrapper_selector, timeout=timeout) or []
            except Exception as e:
                logger.debug(f"消息包装容器查找失败（忽略）: {e}")
                fallback_elements = []

            if fallback_elements:
                logger.debug(
                    "主结果容器为空，回退到消息包装容器继续等待媒体渲染: "
                    f"primary={result_selector!r}, fallback={message_wrapper_selector!r}"
                )
                return fallback_elements, "message_wrapper"

            return [], ""

        def _audio_trigger_available(target_element=None, *, signal_seen: bool = False) -> bool:
            if not is_modality_enabled(image_config.get("modalities") or {}, "audio"):
                return False
            if not bool(image_config.get("audio_capture_enabled", True)):
                return False
            if not self._media_policy_allows_audio_probe(image_config, signal_seen=signal_seen):
                return False
            policy = self._media_run_policy(image_config, "audio")
            if policy == "always_probe":
                return True
            for current_target in [target_element, tab]:
                if current_target is None:
                    continue
                try:
                    probe_result = media_extractor.probe_audio_trigger(current_target, image_config)
                except Exception:
                    probe_result = {}
                if bool(probe_result.get("found")) or int(probe_result.get("candidate_count") or 0) > 0:
                    logger.debug(
                        "页面音频触发入口快速探测命中: "
                        f"policy={policy}, signal_seen={signal_seen}, "
                        f"candidate_count={probe_result.get('candidate_count')}, "
                        f"selector={probe_result.get('selector_used')!r}"
                    )
                    return True
            logger.debug(
                "页面音频触发入口快速探测未命中，跳过播放录音等待: "
                f"policy={policy}, signal_seen={signal_seen}"
            )
            return False
        
        try:
            quick_probe_timeout = self._media_quick_probe_timeout(image_config, default=1.0)
            elements, container_mode = _find_candidate_elements(timeout=quick_probe_timeout)
            if not elements:
                should_try_page_audio_capture = (
                    _audio_trigger_available(None, signal_seen=False)
                    and not effective_stop_checker()
                )
                if should_try_page_audio_capture:
                    logger.debug("结果容器为空，尝试页面级音频播放捕获回退")
                    captured_audio_items = self._capture_audio_via_page_playback(
                        tab=tab,
                        target_element=None,
                        image_config=image_config,
                        stop_checker=effective_stop_checker,
                    )
                    if captured_audio_items:
                        return _apply_stream_media_fallback(captured_audio_items)

                if fallback_stream_media_items:
                    logger.debug("结果容器为空，直接回退到网络流媒体结果")
                    return list(fallback_stream_media_items)
                return []
            
            def _select_target_element(candidates):
                if not candidates:
                    return None

                strategy = str(image_config.get("final_target_strategy", "container") or "container").strip().lower()
                if strategy not in ("latest_reply", "latest_visual_reply"):
                    return candidates[-1]

                selector = str(image_config.get("selector") or "img").strip() or "img"

                def _has_media(candidate) -> bool:
                    try:
                        return bool(
                            candidate.run_js(
                                """
                                const selector = String(arguments[0] || "img");
                                try {
                                    if (this instanceof Element && typeof this.matches === "function" && this.matches(selector)) {
                                        return true;
                                    }
                                } catch {}
                                try {
                                    return !!(this.querySelector && this.querySelector(selector));
                                } catch {
                                    return false;
                                }
                                """,
                                selector,
                            )
                        )
                    except Exception:
                        return False

                if strategy == "latest_visual_reply":
                    scored_candidates = []
                    column = str(image_config.get("latest_visual_column", "left") or "left").strip().lower()
                    if column not in {"left", "right"}:
                        column = "left"
                    for index, candidate in enumerate(candidates):
                        has_media = _has_media(candidate)
                        try:
                            rect = candidate.run_js(
                                """
                                const rect = this.getBoundingClientRect();
                                return {
                                    bottom: Number(rect && rect.bottom || 0) + Number(window.scrollY || 0),
                                    left: Number(rect && rect.left || 0) + Number(window.scrollX || 0),
                                    width: Number(rect && rect.width || 0),
                                    height: Number(rect && rect.height || 0),
                                };
                                """
                            ) or {}
                            bottom = float(rect.get("bottom") or 0)
                            left = float(rect.get("left") or 0)
                            area = float(rect.get("width") or 0) * float(rect.get("height") or 0)
                        except Exception:
                            bottom = 0.0
                            left = 0.0
                            area = 0.0
                        horizontal_score = left if column == "right" else -left
                        scored_candidates.append((bottom, horizontal_score, area, -index, index, left, has_media, candidate))

                    if scored_candidates:
                        scored_candidates.sort(key=lambda item: item[:4], reverse=True)
                        best = scored_candidates[0]
                        logger.debug(
                            "[latest_visual_reply] 选中视觉最新媒体容器: "
                            f"index={best[4]}, column={column}, has_media={best[6]}, "
                            f"bottom={best[0]:.1f}, left={best[5]:.1f}, total={len(candidates)}"
                        )
                        return best[7]

                for candidate in reversed(candidates):
                    if _has_media(candidate):
                        return candidate

                return candidates[-1]

            last_element = _select_target_element(elements)
            if last_element is None:
                return []

            def _extract_media_once(target_element, quiet=False):
                cfg = dict(image_config or {})
                if quiet:
                    cfg["quiet"] = True
                if hasattr(extractor, 'extract_media'):
                    return extractor.extract_media(
                        target_element,
                        config=cfg,
                        container_selector_fallback=result_selector
                    )
                if hasattr(extractor, 'extract_images'):
                    return media_extractor.extract(
                        target_element,
                        config=cfg,
                        container_selector_fallback=result_selector
                    )
                return media_extractor.extract(
                    target_element,
                    config=cfg,
                    container_selector_fallback=result_selector
                )

            media_items = [] if only_audio_mode else _extract_media_once(last_element)
            ready_media_items = _apply_stream_media_fallback(
                self._filter_ready_media_items(media_items, image_config)
            )
            pending_media_items = [
                item for item in media_items
                if self._is_pending_media_item(item, image_config)
            ]

            image_items = [item for item in ready_media_items if item.get("media_type") == "image"]
            audio_items = [item for item in ready_media_items if item.get("media_type") == "audio"]
            video_items = [item for item in ready_media_items if item.get("media_type") == "video"]

            placeholder_text = ""
            if not only_audio_mode:
                try:
                    if hasattr(extractor, 'extract_text'):
                        placeholder_text = str(extractor.extract_text(last_element) or "")
                    else:
                        placeholder_text = str(
                            last_element.run_js("return this.innerText || this.textContent || ''") or ""
                        )
                except Exception:
                    placeholder_text = ""

            placeholder_text_lower = placeholder_text.lower()
            response_text_hint_lower = str(response_text_hint or "").strip().lower()
            request_likely_image = self._looks_like_image_generation_request(request_text_hint)
            media_state = dict(media_generation_state or {})
            media_state_pending = bool(media_state.get("pending"))
            media_state_type = str(media_state.get("media_type") or "").strip().lower()
            media_state_hint_text = str(media_state.get("hint_text") or "").strip().lower()
            combined_pending_text = "\n".join(
                text for text in (placeholder_text_lower, response_text_hint_lower, media_state_hint_text)
                if text
            )

            has_generated_image_hint = any(
                marker in placeholder_text_lower
                for marker in (
                    "image_generation_content/",
                    "googleusercontent.com/image_generation_content/",
                )
            )

            if not has_generated_image_hint:
                try:
                    has_generated_image_hint = bool(
                        last_element.run_js(
                            """
                            return !!this.querySelector(
                                '.attachment-container.generated-images, '
                                + '.generated-images, generated-image, single-image, '
                                + '.image-button, img[src^="blob:"], img[src^="data:image"]'
                            );
                            """
                        )
                    )
                except Exception:
                    has_generated_image_hint = False

            pending_audio_hint = self._is_pending_media_text(combined_pending_text, image_config, "audio")
            pending_video_hint = self._is_pending_media_text(combined_pending_text, image_config, "video")

            if pending_media_items:
                pending_audio_hint = pending_audio_hint or any(
                    item.get("media_type") == "audio" for item in pending_media_items
                )
                pending_video_hint = pending_video_hint or any(
                    item.get("media_type") == "video" for item in pending_media_items
                )
                logger.debug(
                    "检测到占位媒体，继续等待真实结果: "
                    f"pending={len(pending_media_items)}, ready={len(ready_media_items)}"
                )

            pending_kinds = set()
            if (
                has_generated_image_hint
                or (media_state_pending and media_state_type == "image")
            ) and not image_items and self._media_policy_allows_signal_wait(image_config, "image"):
                pending_kinds.add("image")
            if (
                media_state_pending
                and media_state_type == "audio"
                and not (audio_items or video_items)
                and self._media_policy_allows_signal_wait(image_config, "audio")
            ):
                pending_kinds.add("audio")
            if (
                media_state_pending
                and media_state_type == "video"
                and not video_items
                and self._media_policy_allows_signal_wait(image_config, "video")
            ):
                pending_kinds.add("video")
            if (
                pending_audio_hint
                and not (audio_items or video_items)
                and self._media_policy_allows_signal_wait(image_config, "audio")
            ):
                pending_kinds.add("audio")
            if (
                pending_video_hint
                and not video_items
                and self._media_policy_allows_signal_wait(image_config, "video")
            ):
                pending_kinds.add("video")

            if pending_kinds and not direct_scan_only and not effective_stop_checker():
                base_timeout = float(image_config.get("load_timeout_seconds", 5.0) or 5.0)
                late_wait_timeout = float(
                    image_config.get("late_render_timeout_seconds")
                    or max(30.0, base_timeout * 6.0)
                )
                for pending_kind in pending_kinds:
                    late_wait_timeout = max(
                        late_wait_timeout,
                        self._media_late_wait_timeout(image_config, pending_kind, late_wait_timeout),
                    )
                state_wait_timeout = media_state.get("wait_timeout_seconds")
                try:
                    if state_wait_timeout is not None:
                        late_wait_timeout = max(late_wait_timeout, float(state_wait_timeout))
                except Exception:
                    pass
                if media_state_pending and media_state_type in pending_kinds:
                    logger.debug(
                        "检测到解析器上报的待渲染媒体任务，延长媒体渲染等待窗口: "
                        f"{late_wait_timeout:.1f}s"
                    )
                poll_interval = float(image_config.get("late_render_poll_seconds") or 1.0)
                deadline = time.time() + late_wait_timeout
                wait_satisfied = False

                while time.time() < deadline and not effective_stop_checker():
                    time.sleep(max(0.2, poll_interval))
                    elements, container_mode = _find_candidate_elements(timeout=0.5)
                    if not elements:
                        continue
                    selected = _select_target_element(elements)
                    if selected is None:
                        continue
                    last_element = selected
                    media_items = _extract_media_once(last_element, quiet=True)
                    ready_media_items = _apply_stream_media_fallback(
                        self._filter_ready_media_items(media_items, image_config)
                    )
                    image_items = [item for item in ready_media_items if item.get("media_type") == "image"]
                    audio_items = [item for item in ready_media_items if item.get("media_type") == "audio"]
                    video_items = [item for item in ready_media_items if item.get("media_type") == "video"]

                    satisfied = True
                    if "image" in pending_kinds and not image_items:
                        satisfied = False
                    if "audio" in pending_kinds and not (audio_items or video_items):
                        satisfied = False
                    if "video" in pending_kinds and not video_items:
                        satisfied = False

                    if satisfied:
                        kinds_label = ",".join(sorted(pending_kinds))
                        logger.debug(
                            f"延迟媒体渲染已捕获: kinds={kinds_label} "
                            f"(late_wait={late_wait_timeout:.1f}s, container={container_mode or 'unknown'})"
                        )
                        wait_satisfied = True
                        break

                if pending_kinds and not wait_satisfied and not effective_stop_checker():
                    logger.warning(
                        "等待待渲染媒体超时，仍未拿到最终结果: "
                        f"kinds={','.join(sorted(pending_kinds))}, late_wait={late_wait_timeout:.1f}s"
                    )

            should_probe_late_image_render = (
                is_modality_enabled(modalities, "image")
                and self._media_policy_allows_signal_wait(image_config, "image")
                and not image_items
                and not pending_kinds
                and not only_audio_mode
                and not effective_stop_checker()
                and request_likely_image
                and len(str(response_text_hint or "").strip()) <= 32
            )
            if should_probe_late_image_render:
                late_image_wait_timeout = float(
                    image_config.get("late_image_render_timeout_seconds")
                    or max(45.0, float(image_config.get("load_timeout_seconds", 5.0) or 5.0) * 8.0)
                )
                late_image_wait_timeout = self._media_blind_wait_timeout(
                    image_config,
                    "image",
                    self._media_late_wait_timeout(
                        image_config,
                        "image",
                        late_image_wait_timeout,
                    ),
                )
                if late_image_wait_timeout <= 0:
                    logger.debug("无显式占位信号，图片盲等已按配置跳过")
                    should_probe_late_image_render = False

            if should_probe_late_image_render:
                poll_interval = float(image_config.get("late_render_poll_seconds") or 1.0)
                deadline = time.time() + late_image_wait_timeout

                while time.time() < deadline and not effective_stop_checker():
                    time.sleep(max(0.2, poll_interval))
                    elements, container_mode = _find_candidate_elements(timeout=0.5)
                    if not elements:
                        continue

                    selected = _select_target_element(elements)
                    if selected is None:
                        continue
                    last_element = selected
                    media_items = _extract_media_once(last_element, quiet=True)
                    ready_media_items = _apply_stream_media_fallback(
                        self._filter_ready_media_items(media_items, image_config)
                    )
                    image_items = [item for item in ready_media_items if item.get("media_type") == "image"]
                    audio_items = [item for item in ready_media_items if item.get("media_type") == "audio"]
                    video_items = [item for item in ready_media_items if item.get("media_type") == "video"]

                    if image_items:
                        logger.debug(
                            "无显式占位信号，但已在延迟轮询中捕获到图片结果: "
                            f"late_wait={late_image_wait_timeout:.1f}s, container={container_mode or 'unknown'}"
                        )
                        break

            should_try_audio_capture = (
                _audio_trigger_available(
                    last_element,
                    signal_seen=bool("audio" in pending_kinds or pending_audio_hint or media_state_type == "audio"),
                )
                and not audio_items
                and not effective_stop_checker()
            )
            if should_try_audio_capture:
                captured_audio_items = self._capture_audio_via_page_playback(
                    tab=tab,
                    target_element=last_element,
                    image_config=image_config,
                    stop_checker=effective_stop_checker,
                    response_text_hint=response_text_hint,
                )
                if captured_audio_items:
                    ready_media_items = self._dedupe_media_items(list(ready_media_items) + captured_audio_items)
                    audio_items = [item for item in ready_media_items if item.get("media_type") == "audio"]
            
            media_items = ready_media_items

            try:
                if image_items:
                    self._prefetch_remote_image_urls(
                        tab,
                        [
                            str(item.get("url") or "").strip()
                            for item in image_items
                            if isinstance(item, dict)
                        ],
                    )
                    converted_images = self._try_screenshot_images_to_local(tab, last_element, image_items, image_config)
                    other_items = [item for item in media_items if item.get("media_type") != "image"]
                    media_items = converted_images + other_items
            except Exception as e:
                logger.warning(f"截图落盘失败（已忽略）: {e}")

            try:
                media_items = self._persist_data_uri_media_to_local(media_items)
            except Exception as e:
                logger.warning(f"data uri 落盘失败（已忽略）: {e}")

            return media_items
            
        except Exception as e:
            logger.warning(f"多模态提取异常: {e}")
            return []

    def _capture_audio_via_page_playback(
        self,
        tab,
        target_element,
        image_config: Dict,
        stop_checker: Optional[Callable[[], bool]] = None,
        response_text_hint: str = "",
    ) -> List[Dict]:
        """回退方案：触发页面播放按钮并从隐藏音频/ WebAudio 输出中捕获音频。"""
        from app.core.extractors.media_extractor import media_extractor

        if not tab:
            return []

        effective_stop_checker = stop_checker or self._should_stop_checker
        if effective_stop_checker():
            return []

        prepared = media_extractor.prepare_page_audio_capture(tab, image_config)
        if not prepared:
            return []

        network_capture = dict(image_config.get("audio_network_capture") or {})
        network_capture_enabled = bool(network_capture.get("enabled", False))
        if network_capture_enabled and not media_extractor.install_audio_network_probe(tab, image_config):
            network_capture_enabled = False

        activation_result = media_extractor.activate_audio_trigger_surface(target_element or tab)
        if activation_result:
            logger.debug(
                "页面音频操作区激活结果: "
                f"ok={bool(activation_result.get('ok'))}, "
                f"activated_count={activation_result.get('activated_count')}"
            )

        trigger_target = target_element or tab
        trigger_result: Dict[str, Any] = {}
        trigger_targets = [trigger_target]
        if target_element is not None and target_element is not tab:
            trigger_targets.append(tab)

        for attempt in range(3):
            if effective_stop_checker():
                return []
            if attempt > 0:
                time.sleep(0.35)
                media_extractor.activate_audio_trigger_surface(target_element or tab)

            for current_target in trigger_targets:
                trigger_result = media_extractor.trigger_audio_playback(current_target, image_config)
                if bool(trigger_result.get("clicked")):
                    trigger_result["attempt"] = attempt + 1
                    trigger_result["target_scope"] = "tab" if current_target is tab else "message"
                    break
            if bool(trigger_result.get("clicked")):
                break

        if not bool(trigger_result.get("clicked")):
            logger.debug(
                "页面音频捕获未触发: "
                f"selector={trigger_result.get('selector_used')!r}, "
                f"labels={trigger_result.get('labels_used')!r}, "
                f"candidate_count={trigger_result.get('candidate_count')}, "
                f"debug_matches={trigger_result.get('debug_matches')!r}, "
                f"nearby_candidates={trigger_result.get('nearby_candidates')!r}, "
                f"visible_button_samples={trigger_result.get('visible_button_samples')!r}"
            )
            return []

        logger.debug(
            "页面音频捕获已触发: "
            f"attempt={trigger_result.get('attempt')}, "
            f"scope={trigger_result.get('target_scope')}, "
            f"text={trigger_result.get('text')!r}, "
            f"score={trigger_result.get('score')}"
        )

        effective_response_text_hint = str(response_text_hint or "").strip()
        if target_element is not None and len(effective_response_text_hint) <= 8:
            try:
                dom_response_text = str(
                    target_element.run_js(
                        """
                        const text = (this.innerText || this.textContent || "").trim();
                        return text;
                        """
                    ) or ""
                ).strip()
            except Exception:
                dom_response_text = ""
            if len(dom_response_text) > len(effective_response_text_hint):
                logger.debug(
                    "页面音频捕获文本提示已回退 to 当前回复 DOM 文本: "
                    f"old_len={len(effective_response_text_hint)}, new_len={len(dom_response_text)}"
                )
                effective_response_text_hint = dom_response_text

        if network_capture_enabled:
            network_audio_items = media_extractor.capture_network_audio(
                tab=tab,
                config=image_config,
                stop_checker=effective_stop_checker,
                response_text_hint=effective_response_text_hint,
            )
            if network_audio_items:
                logger.debug(f"网络音频捕获成功: {len(network_audio_items)} 项")
                return network_audio_items

        max_wait = float(
            image_config.get("audio_capture_max_wait_seconds")
            or max(8.0, float(image_config.get("load_timeout_seconds", 5.0) or 5.0) * 1.8)
        )
        max_wait = self._media_audio_capture_timeout(image_config, max_wait)
        hint_text = str(effective_response_text_hint or "").strip()
        if hint_text:
            try:
                chars_per_second = max(
                    1.0,
                    float(image_config.get("audio_capture_estimated_chars_per_second") or 4.8),
                )
                min_wait = max(
                    1.0,
                    float(image_config.get("audio_capture_min_wait_seconds") or 2.0),
                )
                padding_seconds = max(
                    0.0,
                    float(image_config.get("audio_capture_wait_padding_seconds") or 1.2),
                )
                hard_cap = max(
                    min_wait,
                    float(image_config.get("audio_capture_hard_max_wait_seconds") or max_wait),
                )
                fallback_wait = max_wait
                estimated_wait = min_wait + (len(hint_text) / chars_per_second) + padding_seconds
                max_wait = min(max(min_wait, estimated_wait), hard_cap)
                logger.debug(
                    "页面音频捕获动态等待窗口: "
                    f"text_len={len(hint_text)}, max_wait={max_wait:.1f}s, "
                    f"fallback_wait={fallback_wait:.1f}s, "
                    f"chars_per_second={chars_per_second:.1f}"
                )
            except (TypeError, ValueError):
                pass
        poll_interval = max(0.1, float(image_config.get("audio_capture_poll_seconds") or 0.25))
        silence_seconds = max(0.4, float(image_config.get("audio_capture_silence_seconds") or 1.2))
        activity_silence_seconds = max(
            0.2,
            float(image_config.get("audio_capture_activity_silence_seconds") or 0.65),
        )
        terminal_settle_seconds = max(
            0.0,
            float(image_config.get("audio_capture_terminal_settle_seconds") or 0.35),
        )
        deadline = time.time() + max_wait
        has_seen_data = False
        has_seen_activity = False
        terminal_deadline = 0.0

        while time.time() < deadline and not effective_stop_checker():
            time.sleep(poll_interval)
            status = media_extractor.get_page_audio_capture_status(tab)
            if not isinstance(status, dict):
                continue

            if bool(status.get("has_data")):
                has_seen_data = True

            active_recordings = int(status.get("active_recordings") or 0)
            last_data_at_ms = int(status.get("last_data_at") or 0)
            last_active_at_ms = int(status.get("last_active_at") or 0)
            playing_media_elements = int(status.get("playing_media_elements") or 0)
            terminal_playback_elements = int(status.get("terminal_playback_elements") or 0)
            if last_active_at_ms:
                has_seen_activity = True
            if has_seen_data and active_recordings <= 0:
                break
            if has_seen_data and playing_media_elements <= 0 and terminal_playback_elements > 0:
                if terminal_deadline <= 0:
                    terminal_deadline = time.time() + terminal_settle_seconds
                elif time.time() >= terminal_deadline:
                    break
            else:
                terminal_deadline = 0.0

            if has_seen_activity and last_active_at_ms:
                activity_silence_elapsed = (time.time() * 1000.0 - last_active_at_ms) / 1000.0
                if activity_silence_elapsed >= activity_silence_seconds:
                    break

            if has_seen_data and last_data_at_ms:
                silence_elapsed = (time.time() * 1000.0 - last_data_at_ms) / 1000.0
                if silence_elapsed >= silence_seconds:
                    break

        if effective_stop_checker():
            return []

        captured_items = media_extractor.export_page_audio_capture(tab, image_config)
        if captured_items:
            status = media_extractor.get_page_audio_capture_status(tab)
            if isinstance(status, dict):
                logger.debug(
                    "页面播放音频捕获成功: "
                    f"{len(captured_items)} 项, "
                    f"version={status.get('version')}, "
                    f"tracked_media={status.get('tracked_media_elements')}, "
                    f"tracked_web_audio={status.get('tracked_web_audio')}, "
                    f"active={status.get('active_recordings')}, "
                    f"chunks={status.get('total_chunks')}, "
                    f"last_data_at={status.get('last_data_at')}, "
                    f"last_active_at={status.get('last_active_at')}, "
                    f"peak_rms={status.get('peak_rms')}"
                )
        if not captured_items:
            status = media_extractor.get_page_audio_capture_status(tab)
            if isinstance(status, dict):
                logger.debug(
                    "页面音频捕获未导出到任何音频数据: "
                    f"version={status.get('version')}, "
                    f"tracked_media={status.get('tracked_media_elements')}, "
                    f"tracked_web_audio={status.get('tracked_web_audio')}, "
                    f"active={status.get('active_recordings')}, "
                    f"chunks={status.get('total_chunks')}, "
                    f"has_data={status.get('has_data')}, "
                    f"errors={status.get('recent_errors')}, "
                    f"events={status.get('recent_events')}"
                )
            else:
                logger.debug("页面音频捕获未导出到任何音频数据")
            if network_capture_enabled:
                try:
                    media_extractor.capture_network_audio(
                        tab=tab,
                        config=image_config,
                        stop_checker=lambda: True,
                        response_text_hint=effective_response_text_hint,
                    )
                except Exception:
                    pass
            try:
                browser_tts_items = media_extractor.capture_browser_tts_fallback(
                    tab=tab,
                    config=image_config,
                    stop_checker=effective_stop_checker,
                    response_text_hint=effective_response_text_hint,
                )
            except Exception as exc:
                logger.debug(f"浏览器 TTS 兜底失败（已忽略）: {exc}")
                browser_tts_items = []
            if browser_tts_items:
                return browser_tts_items
        return captured_items

    def _resolve_media_ref(self, media_item: Dict) -> str:
        ref = str(media_item.get("url") or media_item.get("data_uri") or "").strip()
        if not ref:
            return ""

        if ref.startswith("/") and not ref.startswith("//"):
            public_base = os.getenv("PUBLIC_BASE_URL", "").strip()
            if public_base:
                return public_base.rstrip("/") + ref
            return f"http://{AppConfig.get_host()}:{AppConfig.get_port()}{ref}"

        return ref

    def _build_media_markdown_block(self, media_items: List[Dict]) -> str:
        image_blocks = []
        audio_lines = []
        video_lines = []

        for item in media_items or []:
            ref = self._resolve_media_ref(item)
            if not ref:
                continue

            media_type = str(item.get("media_type") or "image").lower()
            if media_type == "image":
                image_blocks.append(f"\n\n![image_{len(image_blocks)}]({ref})")
                continue

            label = item.get("label") or item.get("mime") or ""
            label_suffix = f" - {label}" if label else ""
            if media_type == "audio":
                audio_lines.append(f"[audio_{len(audio_lines)}]({ref}){label_suffix}")
            elif media_type == "video":
                video_lines.append(f"[video_{len(video_lines)}]({ref}){label_suffix}")

        blocks = []
        if image_blocks:
            blocks.append("".join(image_blocks))
        if audio_lines:
            blocks.append("\n\n" + "\n".join(audio_lines))
        if video_lines:
            blocks.append("\n\n" + "\n".join(video_lines))

        if not blocks:
            return ""

        return "".join(blocks) + "\n\n"

    def _prepare_media_items_for_response(self, media_items: List[Dict]) -> List[Dict]:
        result = []
        for item in media_items or []:
            entry = dict(item)
            ref = self._resolve_media_ref(item)
            if entry.get("kind") == "url":
                entry["url"] = ref or entry.get("url")
            if ref and entry.get("kind") != "url":
                entry["kind"] = "url"
                entry["url"] = ref
            entry.pop("data_uri", None)
            entry.pop("local_path", None)
            result.append(entry)
        return result

    def _persist_remote_media_urls_to_local(
        self,
        media_items: List[Dict],
        tab=None,
        max_size_mb: int = 10,
    ) -> List[Dict]:
        """Download remote audio/video URLs to local files so downstream clients get stable local URLs."""
        if not media_items or tab is None:
            return media_items

        save_dir = Path("download_images")
        save_dir.mkdir(exist_ok=True)
        max_bytes = max(1, int(max_size_mb)) * 1024 * 1024

        ext_map = {
            "audio/aac": ".aac",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/ogg": ".ogg",
            "audio/webm": ".webm",
            "audio/webm;codecs=opus": ".webm",
            "audio/mp4": ".m4a",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "video/ogg": ".ogv",
            "video/quicktime": ".mov",
        }

        cookies_dict = {}
        try:
            cookies_list = tab.cookies()
            if cookies_list:
                for cookie in cookies_list:
                    if isinstance(cookie, dict) and "name" in cookie and "value" in cookie:
                        cookies_dict[cookie["name"]] = cookie["value"]
        except Exception as exc:
            logger.debug(f"媒体下载读取 cookies 失败（忽略）: {exc}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": getattr(tab, "url", "") or "",
            "Accept": "*/*",
        }

        result = []
        for item in media_items:
            filepath = None
            if item.get("kind") != "url":
                result.append(item)
                continue

            media_type = str(item.get("media_type") or "").lower()
            if media_type not in {"audio", "video"}:
                if media_type == "image":
                    target_url = normalize_remote_image_url(item.get("url"))
                    background_result = background_image_downloader.get_download_result(
                        target_url,
                        wait=True,
                        timeout=1.0,
                    ) if target_url else None
                    localized_item = self._localize_image_item_from_background_result(item, background_result)
                    result.append(localized_item if localized_item is not None else item)
                    continue
                result.append(item)
                continue

            url = str(item.get("url") or "").strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                result.append(item)
                continue

            try:
                response = requests.get(
                    url,
                    cookies=cookies_dict,
                    headers=headers,
                    timeout=30,
                    allow_redirects=True,
                    stream=True,
                )
            except Exception as exc:
                logger.warning(f"{media_type} 下载失败，保留远程链接: {exc}")
                result.append(item)
                continue

            try:
                if response.status_code != 200:
                    logger.warning(f"{media_type} 下载失败，HTTP {response.status_code}")
                    result.append(item)
                    continue

                content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if media_type == "audio" and "audio" not in content_type:
                    logger.warning(f"音频下载返回非音频类型，保留远程链接: {content_type or 'unknown'}")
                    result.append(item)
                    continue
                if media_type == "video" and "video" not in content_type:
                    logger.warning(f"视频下载返回非视频类型，保留远程链接: {content_type or 'unknown'}")
                    result.append(item)
                    continue

                ext = ext_map.get(content_type)
                if not ext:
                    path_ext = Path(urlparse(url).path).suffix.lower()
                    ext = path_ext or (".mp4" if media_type == "video" else ".mp3")

                filename = f"{int(time.time())}_{uuid.uuid4().hex[:8]}{ext}"
                filepath = save_dir / filename

                written = 0
                with filepath.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 64):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValueError(f"media_too_large:{written}")
                        handle.write(chunk)

                new_item = dict(item)
                new_item["url"] = f"/media/{filename}"
                new_item["mime"] = content_type or item.get("mime")
                new_item["byte_size"] = written
                new_item["source"] = "local_file"
                new_item["local_path"] = str(filepath)
                result.append(new_item)
                logger.debug(f"✅ {media_type} 已保存到本地: {filename} ({written} bytes)")
            except Exception as exc:
                logger.warning(f"{media_type} 落盘失败，保留远程链接: {exc}")
                try:
                    if filepath is not None and filepath.exists():
                        filepath.unlink()
                except Exception:
                    pass
                result.append(item)
            finally:
                response.close()

        return result

    def _try_screenshot_images_to_local(self, tab, last_element, images: List[Dict], image_config: Dict = None) -> List[Dict]:
        """
        优先下载图片（更精准），下载失败才截图。
        基于实测 API：img_ele.attr('src'), page.cookies(), get_screenshot(path)
        """
        if not images:
            return images

        remote_indexes = []
        for idx, item in enumerate(images):
            if str(item.get("kind") or "").strip().lower() != "url":
                continue
            url = str(item.get("url") or "").strip()
            if url.startswith("http://") or url.startswith("https://"):
                remote_indexes.append(idx)

        if not remote_indexes:
            return images

        out_dir = Path("download_images")
        out_dir.mkdir(exist_ok=True)

        image_config = image_config or {}
        selector = image_config.get("selector", "img")
        ext_map = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "image/svg+xml": ".svg",
            "image/avif": ".avif",
        }

        img_eles = []
        try:
            scoped_selector = selector or "img"
            img_eles = last_element.eles(f"css:{scoped_selector}", timeout=0.5)
            logger.debug(
                f"图片定位：在当前回复内使用 '{scoped_selector}'，"
                f"找到 {len(img_eles) if img_eles else 0} 个"
            )
        except Exception as e:
            logger.warning(f"图片定位失败，将仅尝试直连下载: {e}")
            img_eles = []

        cookies_dict, headers = build_image_download_request_context(tab)
        prefetch_wait_seconds = float(
            image_config.get("background_download_wait_seconds")
            or image_config.get("download_wait_seconds")
            or 1.0
        )

        new_images = self._localize_images_with_background_cache(
            images,
            wait_seconds=max(0.0, prefetch_wait_seconds),
        )

        img_ele_entries = []
        for ele in img_eles or []:
            try:
                img_src = str(
                    ele.run_js(
                        """
                        return String(
                            this.currentSrc
                            || this.getAttribute('src')
                            || this.src
                            || ''
                        ).trim();
                        """
                    ) or ele.attr('src') or ele.link or ""
                ).strip()
            except Exception as e:
                logger.debug(f"读取图片元素地址失败（忽略）: {e}")
                img_src = ""
            img_ele_entries.append({
                "element": ele,
                "src": img_src,
                "used": False,
            })

        def _claim_image_element(target_url: str):
            normalized_target = str(target_url or "").strip()
            if normalized_target:
                for entry in reversed(img_ele_entries):
                    if entry["used"]:
                        continue
                    src = entry["src"]
                    if src and src == normalized_target:
                        entry["used"] = True
                        return entry["element"]

                for entry in reversed(img_ele_entries):
                    if entry["used"]:
                        continue
                    src = entry["src"]
                    if src and (normalized_target in src or src in normalized_target):
                        entry["used"] = True
                        return entry["element"]

                target_path = urlparse(normalized_target).path or ""
                for entry in reversed(img_ele_entries):
                    if entry["used"]:
                        continue
                    src_path = urlparse(entry["src"]).path if entry["src"] else ""
                    if src_path and target_path and src_path == target_path:
                        entry["used"] = True
                        return entry["element"]

            for entry in reversed(img_ele_entries):
                if entry["used"]:
                    continue
                entry["used"] = True
                return entry["element"]

            return None
        localized_count = 0

        for target_index in reversed(remote_indexes):
            target_image = new_images[target_index]
            target_url = normalize_remote_image_url(target_image.get("url"))
            if not target_url:
                continue

            background_result = background_image_downloader.get_download_result(
                target_url,
                wait=False,
            )
            localized_item = self._localize_image_item_from_background_result(
                target_image,
                background_result,
            )
            if localized_item is not None:
                new_images[target_index] = localized_item
                localized_count += 1
                continue

            img_ele = _claim_image_element(target_url)
            saved = False
            saved_mime = None

            base_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
            ext = ".png"
            filename = f"{base_name}{ext}"
            out_path = out_dir / filename

            response = None
            try:
                logger.debug(f"尝试下载图片[{target_index}]: {target_url[:80]}...")
                response = requests.get(
                    target_url,
                    cookies=cookies_dict,
                    headers=headers,
                    timeout=15,
                    allow_redirects=True
                )

                if response.status_code == 200:
                    content = response.content
                    content_type = str(response.headers.get('Content-Type') or '').split(";", 1)[0].strip().lower()

                    if len(content) > 1000 and 'image' in content_type:
                        ext = ext_map.get(content_type, ext)
                        filename = f"{base_name}{ext}"
                        out_path = out_dir / filename
                        out_path.write_bytes(content)
                        saved = True
                        saved_mime = content_type or None
                        background_image_downloader.register_downloaded_file(
                            target_url,
                            local_path=out_path,
                            accessible_url=f"/download_images/{filename}",
                            mime=content_type or None,
                            byte_size=len(content),
                            source="inline_download",
                        )
                        logger.debug(f"✅ 下载成功: {filename} ({len(content)} bytes)")
                    else:
                        logger.debug(f"下载内容无效: {len(content)} bytes, type: {content_type}")
                else:
                    logger.debug(f"下载失败: HTTP {response.status_code}")
            except Exception as e:
                logger.debug(f"下载异常，将尝试截图: {str(e)[:100]}")
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass

            if not saved and img_ele is not None:
                logger.debug(f"图片[{target_index}] 回退到截图方式")
                try:
                    img_ele.get_screenshot(str(out_path))
                    if out_path.exists() and out_path.stat().st_size > 0:
                        saved = True
                        background_image_downloader.register_downloaded_file(
                            target_url,
                            local_path=out_path,
                            accessible_url=f"/download_images/{out_path.name}",
                            mime=None,
                            byte_size=int(out_path.stat().st_size),
                            source="screenshot_fallback",
                        )
                        logger.debug(f"✅ 截图成功: {filename} ({out_path.stat().st_size} bytes)")
                except Exception as e:
                    logger.warning(f"截图失败: {e}")

            if not saved:
                logger.warning(f"图片[{target_index}] 保存失败：下载和截图均失败")
                continue

            local_url = f"/download_images/{out_path.name}"
            new_images[target_index] = self._build_localized_image_item(
                target_image,
                out_path,
                local_url,
                mime=saved_mime,
                byte_size=int(out_path.stat().st_size),
                source="local_file",
            )
            localized_count += 1

        if localized_count > 0:
            logger.debug(f"✅ 图片本地化完成: {localized_count}/{len(remote_indexes)} 张")

        return new_images

    def _persist_data_uri_media_to_local(self, media_items: List[Dict]) -> List[Dict]:
        """Persist extracted data-uri media so downstream Markdown can reuse the existing URL flow."""
        if not media_items:
            return media_items

        save_dir = Path("download_images")
        save_dir.mkdir(exist_ok=True)

        ext_map = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "audio/aac": ".aac",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/ogg": ".ogg",
            "audio/webm": ".webm",
            "audio/webm;codecs=opus": ".webm",
            "audio/mp4": ".m4a",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "video/ogg": ".ogv",
            "video/quicktime": ".mov",
        }

        result = []
        for item in media_items:
            if item.get("kind") != "data_uri":
                result.append(item)
                continue

            data_uri = str(item.get("data_uri") or "").strip()
            if not data_uri.startswith("data:"):
                result.append(item)
                continue

            try:
                header, b64_data = data_uri.split(",", 1)
                mime = header.split(";", 1)[0].split(":", 1)[1].lower()
                ext = ext_map.get(mime, ".png")
                media_bytes = base64.b64decode(b64_data)
            except (ValueError, IndexError, binascii.Error) as e:
                logger.warning(f"data uri 解析失败，保留原媒体数据: {e}")
                result.append(item)
                continue

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{uuid.uuid4().hex[:8]}{ext}"
            filepath = save_dir / filename

            try:
                filepath.write_bytes(media_bytes)
                if str(item.get("media_type") or "").strip().lower() == "audio":
                    logger.debug(f"data-uri 音频已落盘，准备追加尾静音: {filepath.name}")
                    self._append_audio_tail_silence(filepath, duration_seconds=0.3)
            except Exception as e:
                logger.warning(f"data uri 保存失败，保留原媒体数据: {e}")
                result.append(item)
                continue

            new_item = dict(item)
            new_item["kind"] = "url"
            media_type = str(item.get("media_type") or "").strip().lower()
            if media_type in {"audio", "video"}:
                new_item["url"] = f"/media/{filename}"
            else:
                new_item["url"] = f"/download_images/{filename}"
            new_item["data_uri"] = None
            new_item["mime"] = mime
            new_item["byte_size"] = len(media_bytes)
            new_item["source"] = "local_file"
            new_item["local_path"] = str(filepath)
            result.append(new_item)

        return result

    def _dedupe_media_items(self, media_items: List[Dict]) -> List[Dict]:
        result = []
        seen = set()
        for item in media_items or []:
            media_type = str(item.get("media_type") or "")
            ref = str(item.get("url") or item.get("data_uri") or "")
            key = (media_type, ref)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _retry_pending_media_from_response_text(
        self,
        session: TabSession,
        full_content: str,
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> List[Dict]:
        hint_text = str(full_content or "").strip()
        if not hint_text:
            return []

        try:
            domain = str(getattr(session, "current_domain", "") or "").strip()
            if not domain:
                tab = getattr(session, "tab", None)
                current_url = str(getattr(tab, "url", "") or "").strip()
                domain = extract_remote_site_domain(current_url)
            if not domain:
                return []

            config_engine = self._get_config_engine()
            effective_preset_name = preset_name if preset_name is not None else session.preset_name
            tab = getattr(session, "tab", None)
            if tab is None:
                return []

            site_config = config_engine.get_site_config(
                domain,
                getattr(tab, "html", "") or "",
                preset_name=effective_preset_name,
            )
            if not site_config:
                return []

            image_config = site_config.get("image_extraction", {}) or {}
            modalities = image_config.get("modalities") or {}
            image_extraction_enabled = bool(image_config.get("enabled", False)) or any(
                is_modality_enabled(modalities, key) for key in ("image", "audio", "video")
            )
            if not image_extraction_enabled:
                return []

            pending_hit = any(
                self._is_pending_media_text(hint_text, image_config, media_type)
                for media_type in ("audio", "video")
            )
            if not pending_hit:
                return []

            selectors = site_config.get("selectors", {}) or {}
            result_selector = str(selectors.get("result_container", "") or "").strip()
            if not result_selector:
                return []

            extractor = config_engine.get_site_extractor(domain, preset_name=effective_preset_name)

            logger.debug(f"[{session.id}] 非流式响应命中占位媒体文本，触发二次提取等待")
            return self._extract_media_after_stream(
                tab=tab,
                extractor=extractor,
                image_config=image_config,
                result_selector=result_selector,
                message_wrapper_selector=str(selectors.get("message_wrapper", "") or "").strip(),
                stop_checker=stop_checker,
                response_text_hint=hint_text,
            )
        except Exception as e:
            logger.warning(f"[{session.id}] 非流式二次媒体提取失败（已忽略）: {e}")
            return []

    def _download_url_images(self, images: List[Dict], tab=None) -> List[Dict]:
        """
        在浏览器内通过 Canvas 压缩图片，保存到本地并返回可访问 URL
        
        流程：
        1. 浏览器 Canvas 压缩 → base64
        2. 后端解码 → 保存到 download_images/
        3. 返回 /download_images/xxx.jpg URL
        """
        import base64
        import uuid
        from pathlib import Path
        from datetime import datetime
        
        result = []
        
        # 确保目录存在
        save_dir = Path("download_images")
        save_dir.mkdir(exist_ok=True)
        canvas_image_max_size = AppConfig.get_canvas_image_max_size()

        for img in images:
            if img.get('kind') != 'url':
                result.append(img)
                continue
            
            url = img.get('url')
            if not url:
                result.append(img)
                continue
            
            if not tab:
                result.append(img)
                continue
            
            try:
                # 🔑 在浏览器中用 Canvas 加载并压缩图片
                js_code = """
                (async function(imageUrl, configuredMaxSize) {
                    return new Promise((resolve) => {
                        const img = new Image();
                        img.crossOrigin = 'anonymous';

                        img.onload = function() {
                            try {
                                // 限制最大尺寸
                                const MAX_SIZE = Math.max(1, Math.floor(Number(configuredMaxSize) || 1024));
                                let width = img.naturalWidth;
                                let height = img.naturalHeight;
                                
                                if (width > MAX_SIZE || height > MAX_SIZE) {
                                    if (width > height) {
                                        height = Math.round(height * MAX_SIZE / width);
                                        width = MAX_SIZE;
                                    } else {
                                        width = Math.round(width * MAX_SIZE / height);
                                        height = MAX_SIZE;
                                    }
                                }
                                
                                const canvas = document.createElement('canvas');
                                canvas.width = width;
                                canvas.height = height;
                                
                                const ctx = canvas.getContext('2d');
                                ctx.drawImage(img, 0, 0, width, height);
                                
                                // 转为 JPEG
                                const dataUri = canvas.toDataURL('image/jpeg', 0.85);
                                
                                resolve({
                                    success: true,
                                    dataUri: dataUri,
                                    width: width,
                                    height: height
                                });
                            } catch (e) {
                                resolve({ success: false, error: 'Canvas: ' + e.message });
                            }
                        };
                        
                        img.onerror = function() {
                            resolve({ success: false, error: 'Load failed' });
                        };
                        
                        setTimeout(() => resolve({ success: false, error: 'Timeout' }), 15000);
                        img.src = imageUrl;
                    });
                })(arguments[0], arguments[1]);
                """
                
                # ===== PROBE: 验证 run_js 是否等待 Promise，并检查图片/Fetch 可用性 =====
                probe_js = """
                (function(u){
                    try {
                        // 1) 最小同步返回测试
                        const sync_ok = { ok: true, type: typeof u, head: String(u).slice(0, 40) };

                        // 2) Promise 返回测试（不返回大对象）
                        const promise_test = Promise.resolve({ promise_ok: true });

                        // 3) 图片加载测试（不画 canvas，不导 dataUri，避免大返回）
                        const img_test = new Promise((resolve) => {
                            const img = new Image();
                            let done = false;

                            img.onload = () => {
                                if (done) return;
                                done = true;
                                resolve({ img_onload: true, w: img.naturalWidth, h: img.naturalHeight });
                            };
                            img.onerror = () => {
                                if (done) return;
                                done = true;
                                resolve({ img_onerror: true });
                            };

                            setTimeout(() => {
                                if (done) return;
                                done = true;
                                resolve({ img_timeout: true });
                            }, 6000);

                            img.src = u;
                        });

                        // 4) fetch 测试（只返回 status，不读 body）
                        const fetch_test = (async () => {
                            try {
                                const r = await fetch(u, { method: 'GET' });
                                return { fetch_ok: true, status: r.status, redirected: r.redirected };
                            } catch (e) {
                                return { fetch_error: String(e).slice(0, 120) };
                            }
                        })();

                        // 关键：返回一个对象，包含同步字段 + Promise 字段
                        // 如果 run_js 不等待 Promise，你只能拿到一个“未解析”的东西或 None
                        return Promise.all([promise_test, img_test, fetch_test]).then(all => {
                            return {
                                sync: sync_ok,
                                promise: all[0],
                                img: all[1],
                                fetch: all[2]
                            };
                        });
                    } catch(e) {
                        return { probe_exception: String(e).slice(0, 160) };
                    }
                })(arguments[0]);
                """

                probe_result = tab.run_js(probe_js, url)
                logger.info(f"[PROBE_JS] probe_result_type={type(probe_result).__name__}, value={str(probe_result)[:500]}")

                download_result = tab.run_js(js_code, url, canvas_image_max_size)

                logger.info(f"[PROBE_JS] canvas_result_type={type(download_result).__name__}, value={str(download_result)[:300]}")                
                if download_result and download_result.get('success'):
                    data_uri = download_result['dataUri']
                    
                    # 解析 base64
                    # 格式: data:image/jpeg;base64,/9j/4AAQSkZJRg...
                    if ',' in data_uri:
                        header, b64_data = data_uri.split(',', 1)
                        mime = 'image/jpeg'
                        if 'png' in header:
                            mime = 'image/png'
                            ext = '.png'
                        else:
                            ext = '.jpg'
                        
                        # 解码并保存
                        image_bytes = base64.b64decode(b64_data)
                        
                        # 生成唯一文件名
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        unique_id = uuid.uuid4().hex[:8]
                        filename = f"{timestamp}_{unique_id}{ext}"
                        filepath = save_dir / filename
                        
                        # 写入文件
                        with open(filepath, 'wb') as f:
                            f.write(image_bytes)
                        
                        # 构建可访问的 URL
                        accessible_url = f"/download_images/{filename}"
                        
                        new_img = img.copy()
                        new_img['kind'] = 'url'
                        new_img['url'] = accessible_url
                        new_img['data_uri'] = None
                        new_img['mime'] = mime
                        new_img['width'] = download_result['width']
                        new_img['height'] = download_result['height']
                        new_img['byte_size'] = len(image_bytes)
                        new_img['source'] = 'local_file'
                        new_img['local_path'] = str(filepath)
                        
                        result.append(new_img)
                        logger.info(f"✅ 图片已保存: {filename} ({len(image_bytes)} bytes)")
                        continue
                
                error_msg = download_result.get('error', 'Unknown') if download_result else 'No result'
                logger.warning(f"⚠️ 图片处理失败: {error_msg}")
            
            except Exception as e:
                logger.warning(f"⚠️ 图片保存异常: {str(e)[:100]}")
            
            # 失败时保留原 URL
            result.append(img)
        
        return result
