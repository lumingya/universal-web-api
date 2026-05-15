"""
app/services/parser_manager.py - Runtime parser installation manager.

Parser packages can be written into app/core/parsers and then
registered through ParserRegistry via config/parsers.json.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import get_logger
from app.core.parsers import ParserRegistry

logger = get_logger("PARSER_MGR")


class ParserConfigManager:
    """Manage runtime-installed response parsers."""

    _PROJECT_ROOT = Path(__file__).resolve().parents[2]
    CONFIG_FILE = Path(os.getenv("PARSERS_CONFIG_FILE", _PROJECT_ROOT / "config" / "parsers.json"))
    PARSER_DIR = _PROJECT_ROOT / "app" / "core" / "parsers"

    _ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
    _NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._parsers_config: Dict[str, Dict[str, Any]] = {}
        self._last_mtime: float = 0.0
        self._load_config()

    def _load_config(self) -> None:
        if not self.CONFIG_FILE.exists():
            self._use_defaults()
            return

        try:
            self._last_mtime = self.CONFIG_FILE.stat().st_mtime
            with self.CONFIG_FILE.open("r", encoding="utf-8") as handle:
                self._config = json.load(handle)
            self._parsers_config = self._config.get("parsers", {}) or {}
            self._register_from_config()
        except json.JSONDecodeError as exc:
            logger.error(f"Failed to decode parsers config: {exc}")
            self._use_defaults()
        except Exception as exc:
            logger.error(f"Failed to load parsers config: {exc}")
            self._use_defaults()

    def _use_defaults(self) -> None:
        self._config = {
            "version": "1.0",
            "parsers": {},
        }
        self._parsers_config = {}

    def _register_from_config(self) -> None:
        for parser_id, config in self._parsers_config.items():
            if not config.get("enabled", True):
                continue
            try:
                self._load_parser_entry(parser_id, config)
            except Exception as exc:
                logger.warning(f"Failed to register parser [{parser_id}]: {exc}")

    def _save_config(self) -> None:
        self.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with self.CONFIG_FILE.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(self._config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        self._last_mtime = self.CONFIG_FILE.stat().st_mtime

    def _load_parser_entry(self, parser_id: str, config: Dict[str, Any]) -> None:
        module_path = str(config.get("module") or "").strip()
        class_name = str(config.get("class") or "").strip()
        if not module_path or not class_name:
            raise ValueError("Parser config is missing module/class")
        ParserRegistry.load_from_module(module_path, class_name, parser_id)

    def list_parsers(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for info in ParserRegistry.list_all():
            parser_id = str(info.get("id") or "").strip()
            config = self._parsers_config.get(parser_id, {})
            result.append({
                **info,
                "managed": bool(config),
                "module": str(config.get("module") or ""),
                "class": str(config.get("class") or ""),
                "filename": str(config.get("filename") or ""),
                "installed_at": str(config.get("installed_at") or ""),
            })
        result.sort(key=lambda item: str(item.get("id") or ""))
        return result

    def get_parser_config(self, parser_id: str) -> Optional[Dict[str, Any]]:
        config = self._parsers_config.get(str(parser_id or "").strip())
        return copy.deepcopy(config) if isinstance(config, dict) else None

    def install_parser_package(self, payload: Dict[str, Any], overwrite: bool = False) -> Dict[str, Any]:
        normalized = self._normalize_parser_package(payload)
        parser_id = normalized["parser_id"]
        module_name = normalized["module_name"]
        target_path = self.PARSER_DIR / normalized["filename"]
        existing_entry = copy.deepcopy(self._parsers_config.get(parser_id))

        if ParserRegistry.exists(parser_id) and existing_entry is None:
            raise ValueError(f"解析器 ID 已被内置解析器占用: {parser_id}")

        for other_id, other_entry in self._parsers_config.items():
            if other_id == parser_id:
                continue
            if str(other_entry.get("filename") or "") == normalized["filename"]:
                raise ValueError(f"解析器模块名已被 {other_id} 占用: {module_name}")

        if target_path.exists():
            owned_filename = str((existing_entry or {}).get("filename") or "")
            if not owned_filename or owned_filename != normalized["filename"]:
                raise ValueError(f"解析器文件已存在，且不受市场安装器管理: {target_path.name}")

        if existing_entry and str(existing_entry.get("filename") or "") != normalized["filename"]:
            raise ValueError("暂不支持修改已安装解析器的模块名")

        if existing_entry and not overwrite:
            raise FileExistsError(f"解析器已存在: {parser_id}")

        previous_source: Optional[str] = None
        if target_path.exists():
            previous_source = target_path.read_text(encoding="utf-8")

        self._validate_parser_source(
            source_code=normalized["source_code"],
            class_name=normalized["class_name"],
            filename=target_path.name,
        )

        self.PARSER_DIR.mkdir(parents=True, exist_ok=True)

        entry = {
            "id": parser_id,
            "name": normalized["name"],
            "description": normalized["description"],
            "module": normalized["module_path"],
            "class": normalized["class_name"],
            "module_name": module_name,
            "filename": normalized["filename"],
            "enabled": True,
            "installed_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "supported_patterns": normalized["supported_patterns"],
        }

        try:
            with target_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(normalized["source_code"])
                if not normalized["source_code"].endswith("\n"):
                    handle.write("\n")

            self._parsers_config[parser_id] = entry
            self._config["parsers"] = self._parsers_config
            self._config.setdefault("version", "1.0")
            self._save_config()
            self._load_parser_entry(parser_id, entry)
        except Exception:
            self._rollback_install(
                parser_id=parser_id,
                target_path=target_path,
                previous_entry=existing_entry,
                previous_source=previous_source,
            )
            raise

        return {
            "id": parser_id,
            "name": normalized["name"],
            "description": normalized["description"],
            "class_name": normalized["class_name"],
            "module_name": module_name,
            "module": normalized["module_path"],
            "filename": normalized["filename"],
            "supported_patterns": normalized["supported_patterns"],
        }

    def _rollback_install(
        self,
        parser_id: str,
        target_path: Path,
        previous_entry: Optional[Dict[str, Any]],
        previous_source: Optional[str],
    ) -> None:
        try:
            if previous_source is None:
                if target_path.exists():
                    target_path.unlink()
            else:
                with target_path.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(previous_source)
            if previous_entry is None:
                self._parsers_config.pop(parser_id, None)
            else:
                self._parsers_config[parser_id] = previous_entry
            self._config["parsers"] = self._parsers_config
            self._save_config()
            if previous_entry:
                self._load_parser_entry(parser_id, previous_entry)
        except Exception as rollback_exc:
            logger.error(f"Failed to rollback parser install [{parser_id}]: {rollback_exc}")

    def _normalize_parser_package(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("解析器包必须是对象")

        nested = payload.get("parser_package")
        if isinstance(nested, dict):
            return self._normalize_parser_package(nested)

        parser_id = str(payload.get("parser_id") or payload.get("id") or "").strip()
        class_name = str(payload.get("class_name") or payload.get("class") or "").strip()
        module_name = str(payload.get("module_name") or payload.get("module") or payload.get("filename") or "").strip()
        source_code = str(payload.get("source_code") or payload.get("content") or payload.get("code") or "")
        name = str(payload.get("name") or parser_id or class_name or "").strip()
        description = str(payload.get("description") or "").strip()
        supported_patterns = payload.get("supported_patterns") or payload.get("patterns") or []

        if module_name.endswith(".py"):
            module_name = module_name[:-3]
        module_name = module_name.replace("-", "_").strip()

        if not parser_id:
            raise ValueError("缺少 parser_id")
        if not class_name:
            raise ValueError("缺少 class_name")
        if not module_name:
            raise ValueError("缺少 module_name")
        if not source_code.strip():
            raise ValueError("缺少 source_code")
        if not self._ID_PATTERN.match(parser_id):
            raise ValueError(f"解析器 ID 不合法: {parser_id}")
        if not self._NAME_PATTERN.match(class_name):
            raise ValueError(f"解析器类名不合法: {class_name}")
        if not self._NAME_PATTERN.match(module_name):
            raise ValueError(f"解析器模块名不合法: {module_name}")

        normalized_patterns = [
            str(pattern).strip()
            for pattern in (supported_patterns if isinstance(supported_patterns, list) else [])
            if str(pattern).strip()
        ]

        return {
            "parser_id": parser_id,
            "class_name": class_name,
            "module_name": module_name,
            "module_path": f"app.core.parsers.{module_name}",
            "filename": f"{module_name}.py",
            "name": name or parser_id,
            "description": description,
            "supported_patterns": normalized_patterns,
            "source_code": source_code,
        }

    @staticmethod
    def _validate_parser_source(source_code: str, class_name: str, filename: str) -> None:
        try:
            tree = ast.parse(source_code, filename=filename)
        except SyntaxError as exc:
            raise ValueError(f"解析器源码存在语法错误: {exc}") from exc

        class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
        if class_name not in class_names:
            raise ValueError(f"解析器源码里没有找到类: {class_name}")

        try:
            compile(source_code, filename, "exec")
        except SyntaxError as exc:
            raise ValueError(f"解析器源码编译失败: {exc}") from exc


parser_manager = ParserConfigManager()


__all__ = ["ParserConfigManager", "parser_manager"]
