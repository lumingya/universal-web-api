# app/core/browser/prompt.py

import json
import os
import random
from typing import Optional, List, Dict, Any, Callable, TYPE_CHECKING

from app.core.config import logger, BrowserConstants
from app.utils.image_handler import extract_images_from_messages

if TYPE_CHECKING:
    from .main import BrowserCore


class BrowserPromptMixin:
    """与 Prompt 组装、消息解析、内容提取相关的混入类"""

    def _extract_text_from_content(self, content) -> str:
        """
        从消息内容中提取纯文本，图片用占位符替代

        支持格式：
        - 纯字符串: "你好" → "你好"
        - 多模态列表: [{"type":"text","text":"描述"},{"type":"image_url",...}] → "描述 [图片1]"
        - JSON 字符串: '[{"type":"text",...}]' → 解析后处理
        - 类列表对象: tuple/其他可迭代 → 转换为 list 处理
        """
        content_type = type(content).__name__

        if content is None:
            return ""

        if isinstance(content, str):
            stripped = content.strip()
            if stripped.startswith('[') and stripped.endswith(']'):
                parsed = None
                parse_method = ""

                try:
                    parsed = json.loads(stripped)
                    parse_method = "json"
                except (json.JSONDecodeError, TypeError):
                    pass

                if parsed is None:
                    try:
                        import ast
                        parsed = ast.literal_eval(stripped)
                        parse_method = "literal_eval"
                    except (ValueError, SyntaxError):
                        pass

                if parsed and isinstance(parsed, list) and len(parsed) > 0:
                    first_item = parsed[0] if parsed else {}
                    if isinstance(first_item, dict) and 'type' in first_item:
                        logger.debug(
                            "[CONTENT_PARSE] 字符串化多模态: "
                            f"parser={parse_method or 'unknown'}, "
                            f"items={len(parsed)}, raw_len={len(content)}"
                        )
                        return self._extract_text_from_content(parsed)

            if content.startswith('data:image') and 'base64,' in content and len(content) > 1000:
                logger.warning(f"[CONTENT_PARSE] ⚠️ 检测到 base64 图片数据！长度={len(content)}，已替换为占位符")
                return "[图片内容]"

            return content

        is_list_like = isinstance(content, (list, tuple))
        if not is_list_like:
            try:
                is_list_like = hasattr(content, '__iter__') and not isinstance(content, (str, bytes))
            except Exception:
                is_list_like = False
        
        if is_list_like:
            try:
                if not isinstance(content, list):
                    content = list(content)
                    logger.debug(f"[CONTENT_PARSE] 已转换为 list: items={len(content)}")
            except Exception as e:
                logger.warning(f"[CONTENT_PARSE] 转换为 list 失败: {e}")
                return "[内容解析失败]"
            
            text_parts = []
            text_item_count = 0
            image_count = 0
            skipped_count = 0
            unknown_types = []

            for item in content:
                if not isinstance(item, dict):
                    skipped_count += 1
                    continue

                item_type = str(item.get("type", "") or "").strip()

                if item_type == "text":
                    text_content = str(item.get("text", "") or "")
                    text_parts.append(text_content)
                    text_item_count += 1

                elif item_type == "image_url":
                    image_count += 1
                    text_parts.append(f"[图片{image_count}]")

                else:
                    unknown_types.append(item_type or "<empty>")

            result = " ".join(text_parts)
            extras = []
            if skipped_count:
                extras.append(f"skipped={skipped_count}")
            if unknown_types:
                unknown_preview = ", ".join(sorted(set(unknown_types))[:3])
                extras.append(f"unknown={len(unknown_types)}[{unknown_preview}]")
            extra_text = f", {', '.join(extras)}" if extras else ""
            logger.debug(
                "[CONTENT_PARSE] 多模态结果: "
                f"items={len(content)}, text_items={text_item_count}, "
                f"images={image_count}, result_len={len(result)}{extra_text}"
            )
            return result

        logger.warning(f"[CONTENT_PARSE] ⚠️ 未知内容类型: {content_type}，返回占位符")
        return "[内容格式不支持]"

    @staticmethod
    def _format_log_counts(counts: Dict[str, int]) -> str:
        if not counts:
            return "-"
        return ",".join(f"{key}:{counts[key]}" for key in sorted(counts))

    def _build_prompt_from_messages(self, messages: List[Dict]) -> str:
        """从消息列表构建发送给网页的文本"""
        prompt_parts = []
        role_counts: Dict[str, int] = {}
        type_counts: Dict[str, int] = {}
        used_messages = 0
        total_text_len = 0
        image_placeholders = 0

        for m in messages:
            role = m.get('role', 'user')
            content = m.get('content', '')
            role_key = str(role or "unknown")
            type_key = type(content).__name__
            role_counts[role_key] = role_counts.get(role_key, 0) + 1
            type_counts[type_key] = type_counts.get(type_key, 0) + 1

            text = self._extract_text_from_content(content)

            if text:
                used_messages += 1
                total_text_len += len(text)
                image_placeholders += text.count("[图片")
                prompt_parts.append(f"{role}: {text}")

        prompt = "\n\n".join(prompt_parts)
        logger.debug(
            "[CONTENT_PARSE] 消息汇总: "
            f"messages={len(messages)}, used={used_messages}, "
            f"prompt_len={len(prompt)}, text_len={total_text_len}, "
            f"roles={self._format_log_counts(role_counts)}, "
            f"types={self._format_log_counts(type_counts)}, "
            f"images={image_placeholders}"
        )
        return prompt

    def _build_prompt_padding_line(self, config: Dict[str, Any]) -> str:
        marker_text = str(config.get("marker_text") or "").strip()
        segments_per_side = config.get("segments_per_side", 12)
        try:
            segment_count = int(segments_per_side)
        except (TypeError, ValueError):
            segment_count = 12
        segment_count = max(1, min(segment_count, 64))

        fragments = []
        for _ in range(segment_count):
            if random.random() < 0.5:
                fragments.append(random.choice("abcdefghijklmnopq"))
            else:
                fragments.append(str(random.randint(1, 999999)))

        padding_text = "".join(fragments)
        if not marker_text:
            return padding_text
        if marker_text.endswith((':', '：')):
            return f"{marker_text}{padding_text}"
        return f"{marker_text}:{padding_text}"

    def _apply_prompt_padding(self, prompt: str, config: Dict[str, Any]) -> str:
        if not prompt:
            return prompt
        if not isinstance(config, dict) or not bool(config.get("enabled")):
            return prompt

        prefix = self._build_prompt_padding_line(config)
        suffix = self._build_prompt_padding_line(config)
        return f"{prefix}\n{prompt}\n{suffix}"

    def _get_upload_history_images_flag(self, default: bool = True) -> bool:
        """
        获取是否上传历史对话图片的开关。
        优先级：
        1) BrowserConstants.UPLOAD_HISTORY_IMAGES（若存在）
        2) config/browser_config.json 顶层键 UPLOAD_HISTORY_IMAGES（兜底）
        3) default
        """
        try:
            v = getattr(BrowserConstants, "UPLOAD_HISTORY_IMAGES")
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "y", "on")
        except Exception:
            pass

        try:
            cfg_path = "config/browser_config.json"
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                if "UPLOAD_HISTORY_IMAGES" in data:
                    vv = data.get("UPLOAD_HISTORY_IMAGES")
                    if isinstance(vv, bool):
                        return vv
                    if isinstance(vv, (int, float)):
                        return bool(vv)
                    if isinstance(vv, str):
                        return vv.strip().lower() in ("1", "true", "yes", "y", "on")
        except Exception as e:
            logger.debug(f"[IMAGE] 读取 browser_config.json 兜底失败: {e}")

        return default

    def _get_conversation_timeout_threshold(self) -> float:
        try:
            return max(0.0, float(BrowserConstants.get("CONVERSATION_TIMEOUT_THRESHOLD") or 0.0))
        except Exception:
            return 0.0

    @staticmethod
    def _step_submits_conversation_request(action: str, target_key: str, value: Any = None) -> bool:
        action_upper = str(action or "").strip().upper()
        target = str(target_key or "").strip().lower()
        if action_upper == "CLICK" and target in {"send_btn", "send_button", "submit_btn"}:
            return True
        if action_upper == "KEY_PRESS":
            key_name = str(target_key or value or "").strip().lower()
            if key_name in {"enter", "ctrl+enter", "control+enter", "meta+enter", "command+enter"}:
                return True
        return False
