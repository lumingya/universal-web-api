"""ConfigEngine 的一部分（R2-3 拆分）：历史配置迁移与命令文件读写。

方法原样从 engine.py 迁出，行为不变；通过 mixin 组合回 ConfigEngine，
实例状态都在 ConfigEngine.__init__（engine.py）里初始化。
"""

import json
import os
import copy
from typing import Dict, List, Any
from app.models.schemas import (
    PRESET_ADVANCED_FIELDS,
    SITE_ADVANCED_FIELDS,
    get_default_image_extraction_config,
    get_default_file_paste_config,
    get_default_prompt_padding_config,
)
from app.utils.site_rules import get_site_rule
from .engine_common import ConfigConstants, DEFAULT_PRESET_NAME, PRESET_FIELDS, logger


class ConfigMigrationsMixin:
    """历史配置迁移与命令文件读写"""

    def _load_commands_file(self) -> List[Dict[str, Any]]:
        """加载独立命令配置文件"""
        commands_file = ConfigConstants.COMMANDS_FILE
        if not os.path.exists(commands_file):
            return []

        try:
            with open(commands_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                data = data.get("commands", [])

            if isinstance(data, list):
                return data

            logger.warning(f"命令配置文件格式无效: {commands_file}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"命令配置文件格式错误: {e}")
            return []
        except Exception as e:
            logger.error(f"加载命令配置失败: {e}")
            return []
    def _save_commands_file(self, commands: List[Dict[str, Any]]) -> bool:
        """保存独立命令配置文件"""
        commands_file = ConfigConstants.COMMANDS_FILE
        tmp_file = commands_file + ".tmp"

        try:
            os.makedirs(os.path.dirname(commands_file), exist_ok=True)
            payload = {"commands": commands}

            with open(tmp_file, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_file, commands_file)
            logger.info(f"命令配置已保存: {commands_file}")
            return True
        except Exception as e:
            logger.error(f"保存命令配置失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
            return False
    @staticmethod
    def _merge_commands(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """保留已有命令，仅追加不存在的命令"""
        merged = list(existing or [])
        seen_ids = {
            str(cmd.get("id", "")).strip()
            for cmd in merged
            if isinstance(cmd, dict) and str(cmd.get("id", "")).strip()
        }
        seen_names = {
            str(cmd.get("name", "")).strip()
            for cmd in merged
            if isinstance(cmd, dict) and str(cmd.get("name", "")).strip()
        }

        for cmd in incoming or []:
            if not isinstance(cmd, dict):
                continue

            command_id = str(cmd.get("id", "")).strip()
            command_name = str(cmd.get("name", "")).strip()

            if command_id and command_id in seen_ids:
                continue
            if command_name and command_name in seen_names:
                continue

            merged.append(cmd)
            if command_id:
                seen_ids.add(command_id)
            if command_name:
                seen_names.add(command_name)

        return merged
    def _migrate_global_commands(self):
        """将旧版 _global.commands 迁移到独立文件"""
        legacy_commands = self.global_config.get("commands", [])
        existing_commands = self._load_commands_file()

        if not isinstance(legacy_commands, list):
            legacy_commands = []

        merged_commands = self._merge_commands(existing_commands, legacy_commands)

        if legacy_commands or not os.path.exists(ConfigConstants.COMMANDS_FILE):
            self._save_commands_file(merged_commands)

        if self.global_config.remove("commands"):
            self._save_config()
    def _migrate_to_presets(self):
        """
        将旧格式（扁平）站点配置迁移为预设格式

        旧格式: { "selectors": {...}, "workflow": [...], ... }
        新格式: { "presets": { "主预设": { "selectors": {...}, ... } } }
        """
        migrated_count = 0

        for domain in list(self.sites.keys()):
            if domain.startswith('_'):
                continue

            site_config = self.sites[domain]

            # 已经是预设格式，跳过
            if "presets" in site_config:
                continue

            # 将所有已知配置字段提取到主预设中
            preset_data = {}
            remaining = {}

            for key, value in site_config.items():
                if key in PRESET_FIELDS:
                    preset_data[key] = value
                elif key == "advanced":
                    remaining[key] = value
                else:
                    # 未知字段也放入预设（保留用户自定义数据）
                    preset_data[key] = value

            # 构建新格式
            self.sites[domain] = {
                "default_preset": DEFAULT_PRESET_NAME,
                "presets": {
                    DEFAULT_PRESET_NAME: preset_data
                },
                **remaining,
            }

            migrated_count += 1
            logger.debug(f"迁移站点配置: {domain} → 预设格式")

        if migrated_count > 0:
            self._save_config()
            logger.info(f"✅ 已迁移 {migrated_count} 个站点配置为预设格式")
    def _cleanup_preset_residuals(self):
        """
        清理站点配置中预设外的残留字段

        当站点已有 presets 结构时，顶层不应再有 selectors/workflow/file_paste 等字段。
        这些残留通常由旧版 bug 或手动编辑产生。
        """
        cleaned_count = 0
        default_fixed_count = 0

        for domain in list(self.sites.keys()):
            if domain.startswith('_'):
                continue

            site_config = self.sites[domain]

            # 只处理已有 presets 结构的站点
            if "presets" not in site_config:
                continue

            # 找出预设外的残留字段
            residual_keys = []
            for key in list(site_config.keys()):
                if key == "presets":
                    continue
                if key in PRESET_FIELDS:
                    residual_keys.append(key)

            # 删除残留
            for key in residual_keys:
                del site_config[key]
                cleaned_count += 1
                logger.debug(f"清理残留: {domain}.{key}")

            if self._normalize_site_default_preset(domain, site_config):
                default_fixed_count += 1
                logger.debug(f"修正默认预设: {domain} -> {site_config.get('default_preset')}")

        if cleaned_count > 0 or default_fixed_count > 0:
            self._save_config()
            logger.info(
                f"✅ 已清理 {cleaned_count} 个预设外残留字段，"
                f"修正 {default_fixed_count} 个站点默认预设"
            )
    def _migrate_site_advanced_to_presets(self):
        """
        规范化已有预设级 advanced，避免启动时改写站点级 advanced 语义。

        站点根级 advanced 仍作为所有预设的共享基线；预设级 advanced 只保存
        覆盖项。因此这里不再把站点级时序字段复制到所有预设，也不从站点级
        删除这些字段，只清理明显放错位置的 Cookie 字段并规范化已有覆盖项。
        """
        cleaned_count = 0
        changed = False

        for domain, site_config in self.sites.items():
            if domain.startswith("_") or not isinstance(site_config, dict):
                continue

            presets = site_config.get("presets", {})
            if not isinstance(presets, dict) or not presets:
                continue

            for preset_name, preset_data in presets.items():
                if not isinstance(preset_data, dict):
                    continue

                preset_advanced = preset_data.get("advanced")
                if not isinstance(preset_advanced, dict):
                    continue

                for key in list(SITE_ADVANCED_FIELDS):
                    if key in preset_advanced:
                        del preset_advanced[key]
                        cleaned_count += 1
                        changed = True

                normalized_preset_advanced = self._normalize_site_advanced_config(
                    preset_advanced
                )
                for key in list(preset_advanced.keys()):
                    if key in PRESET_ADVANCED_FIELDS:
                        value = normalized_preset_advanced[key]
                        if preset_advanced.get(key) != value:
                            preset_advanced[key] = value
                            cleaned_count += 1
                            changed = True

                if preset_advanced != preset_data.get("advanced"):
                    logger.debug(f"规范化预设高级配置: {domain}/{preset_name}")

                if not preset_advanced:
                    del preset_data["advanced"]
                    cleaned_count += 1
                    changed = True

        if changed:
            self._save_config()
            logger.info(
                f"✅ 已规范化预设高级配置，清理 {cleaned_count} 个残留项"
            )
    def _guess_stealth(self, domain: str) -> bool:
        """Guess whether stealth mode should default to enabled."""
        rule = get_site_rule(domain)
        if "stealth_default" in rule:
            enabled = bool(rule.get("stealth_default"))
            if enabled:
                logger.info(f"检测到默认启用低熵模式的域名: {domain}")
            return enabled
        return False
    def migrate_site_configs(self):
        """迁移旧版站点配置，补充各预设中缺失的字段"""
        migrated_count = 0
        default_image_config = get_default_image_extraction_config()
        default_file_paste = get_default_file_paste_config()
        default_prompt_padding = get_default_prompt_padding_config()
        obsolete_stream_keys = {
            "silence_threshold",
            "initial_wait",
            "enable_wrapper_search",
            "rerender_wait",
            "content_shrink_tolerance",
        }
        obsolete_network_keys = set()

        for domain, site_config in self.sites.items():
            if domain.startswith("_"):
                continue

            presets = site_config.get("presets", {})

            for preset_name, preset_data in presets.items():
                if "image_extraction" not in preset_data:
                    preset_data["image_extraction"] = copy.deepcopy(default_image_config)
                    migrated_count += 1
                    logger.debug(f"迁移: {domain}/{preset_name} (添加 image_extraction)")
                else:
                    normalized_image_config = self._validate_image_config(preset_data.get("image_extraction", {}))
                    if normalized_image_config != preset_data.get("image_extraction"):
                        preset_data["image_extraction"] = normalized_image_config
                        migrated_count += 1
                        logger.debug(f"迁移: {domain}/{preset_name} (规范化 image_extraction)")

                if "file_paste" not in preset_data:
                    preset_data["file_paste"] = copy.deepcopy(default_file_paste)
                    migrated_count += 1
                    logger.debug(f"迁移: {domain}/{preset_name} (添加 file_paste)")

                if "prompt_padding" not in preset_data:
                    preset_data["prompt_padding"] = copy.deepcopy(default_prompt_padding)
                    migrated_count += 1
                    logger.debug(f"迁移: {domain}/{preset_name} (添加 prompt_padding)")
                else:
                    normalized_prompt_padding = self._validate_prompt_padding_config(
                        preset_data.get("prompt_padding", {})
                    )
                    if normalized_prompt_padding != preset_data.get("prompt_padding"):
                        preset_data["prompt_padding"] = normalized_prompt_padding
                        migrated_count += 1
                        logger.debug(f"迁移: {domain}/{preset_name} (规范化 prompt_padding)")

                stream_config = preset_data.get("stream_config")
                moved_attachment_rules = False
                if not isinstance(stream_config, dict):
                    stream_config = {}
                    preset_data["stream_config"] = stream_config

                normalized_file_paste = self._validate_file_paste_config(
                    preset_data.get("file_paste", {}),
                    legacy_stream_config=stream_config,
                )
                if normalized_file_paste != preset_data.get("file_paste"):
                    preset_data["file_paste"] = normalized_file_paste
                    migrated_count += 1
                    logger.debug(f"迁移: {domain}/{preset_name} (规范化 file_paste)")

                for legacy_key in ("send_confirmation", "attachment_monitor"):
                    if legacy_key in stream_config:
                        del stream_config[legacy_key]
                        moved_attachment_rules = True

                if isinstance(stream_config, dict):
                    removed_stream = False
                    for key in list(obsolete_stream_keys):
                        if key in stream_config:
                            del stream_config[key]
                            removed_stream = True

                    network_config = stream_config.get("network")
                    removed_network = False
                    if isinstance(network_config, dict):
                        for key in list(obsolete_network_keys):
                            if key in network_config:
                                del network_config[key]
                                removed_network = True

                    if removed_stream or removed_network or moved_attachment_rules:
                        migrated_count += 1
                        logger.debug(f"迁移: {domain}/{preset_name} (清理废弃的流式配置字段)")

        if migrated_count > 0:
            self._save_config()
            logger.info(f"已迁移 {migrated_count} 个预设配置")

        return migrated_count
