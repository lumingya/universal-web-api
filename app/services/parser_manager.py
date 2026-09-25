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
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import get_logger
from app.core.config import atomic_write_json
from app.core.parsers import ParserRegistry

logger = get_logger("PARSER_MGR")

# ---------------------------------------------------------------------------
# S3：运行时安装解析器 = 把一段 Python 写进 app/core/parsers 并立即 import。
# 1) 默认关闭，只有可信管理员显式设置 PARSER_INSTALL_ENABLED=true 才允许；
# 2) 导入期「无副作用」静态约束：模块顶层 / 类体只允许 import、函数/类定义、
#    字面量赋值，以及极少数纯函数调用（re.compile 等，且参数必须是字面量）；
#    装饰器只允许 staticmethod/classmethod/property 等；默认参数必须是字面量。
#    这样 import 本身不会执行任意代码——解析逻辑只会在解析器被调用时运行；
# 3) 配置中的模块路径必须位于 app.core.parsers 包内。
# 这不是沙箱：解析器方法仍以服务进程权限运行，所以安装入口必须只对可信管理员开放。
# ---------------------------------------------------------------------------

PARSER_INSTALL_ENV = "PARSER_INSTALL_ENABLED"
PARSER_MODULE_PREFIX = "app.core.parsers."

_IMPORT_TIME_SAFE_CALLS = frozenset({
    "re.compile", "set", "frozenset", "tuple", "list", "dict", "str.maketrans",
    "get_logger", "logging.getLogger",
})
_SAFE_DECORATORS = frozenset({
    "staticmethod", "classmethod", "property", "abstractmethod", "abc.abstractmethod",
    "dataclass", "dataclasses.dataclass", "functools.cached_property", "cached_property",
})


def parser_install_enabled() -> bool:
    value = str(os.getenv(PARSER_INSTALL_ENV, "") or "").strip().lower()
    return value in ("true", "1", "yes", "on")


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _import_time_value_ok(node: Optional[ast.AST]) -> bool:
    """导入期会被求值的表达式：只允许字面量/名字引用/白名单纯函数调用。"""
    if node is None:
        return True
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, ast.Attribute):
        return not node.attr.startswith("__") and _import_time_value_ok(node.value)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_import_time_value_ok(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(_import_time_value_ok(k) for k in node.keys if k is not None) and all(
            _import_time_value_ok(v) for v in node.values
        )
    if isinstance(node, ast.UnaryOp):
        return _import_time_value_ok(node.operand)
    if isinstance(node, ast.BinOp):
        return _import_time_value_ok(node.left) and _import_time_value_ok(node.right)
    if isinstance(node, ast.JoinedStr):
        return all(_import_time_value_ok(v) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return _import_time_value_ok(node.value)
    if isinstance(node, ast.Call):
        if _dotted_name(node.func) not in _IMPORT_TIME_SAFE_CALLS:
            return False
        return all(_import_time_value_ok(a) for a in node.args) and all(
            _import_time_value_ok(k.value) for k in node.keywords
        )
    return False


def _decorator_ok(node: ast.AST) -> bool:
    name = _dotted_name(node)
    if name in _SAFE_DECORATORS:
        return True
    # @x.setter / @x.getter / @x.deleter（property 访问器）
    return isinstance(node, ast.Attribute) and node.attr in {"setter", "getter", "deleter"} and bool(name)


def _function_signature_ok(node: ast.AST) -> bool:
    args = node.args  # type: ignore[attr-defined]
    defaults = list(args.defaults) + [d for d in args.kw_defaults if d is not None]
    annotations = [a.annotation for a in (*args.posonlyargs, *args.args, *args.kwonlyargs) if a.annotation]
    if args.vararg and args.vararg.annotation:
        annotations.append(args.vararg.annotation)
    if args.kwarg and args.kwarg.annotation:
        annotations.append(args.kwarg.annotation)
    returns = getattr(node, "returns", None)
    if returns is not None:
        annotations.append(returns)
    return all(_import_time_value_ok(d) for d in defaults) and all(
        _annotation_ok(a) for a in annotations
    )


def _annotation_ok(node: ast.AST) -> bool:
    # 注解在没有 `from __future__ import annotations` 时也会在导入期求值；
    # 允许 Name/Attribute/字符串/下标（Optional[str]、Dict[str, Any]）/ | 联合。
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, ast.Attribute):
        return not node.attr.startswith("__") and _annotation_ok(node.value)
    if isinstance(node, ast.Subscript):
        return _annotation_ok(node.value) and _annotation_ok(node.slice)
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_annotation_ok(item) for item in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_ok(node.left) and _annotation_ok(node.right)
    return False


def check_import_time_side_effects(tree: ast.Module) -> List[str]:
    """返回导入期可能执行任意代码的位置描述；空列表表示通过。"""
    problems: List[str] = []

    def visit_body(body: List[ast.stmt], where: str) -> None:
        for index, stmt in enumerate(body):
            line = getattr(stmt, "lineno", "?")
            if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Pass)):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                continue  # docstring
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not all(_decorator_ok(d) for d in stmt.decorator_list):
                    problems.append(f"{where}:{line} 不允许的装饰器")
                if not _function_signature_ok(stmt):
                    problems.append(f"{where}:{line} 默认参数/注解必须是字面量")
                continue
            if isinstance(stmt, ast.ClassDef):
                if not all(_decorator_ok(d) for d in stmt.decorator_list):
                    problems.append(f"{where}:{line} 不允许的类装饰器")
                if stmt.keywords or not all(isinstance(b, (ast.Name, ast.Attribute)) for b in stmt.bases):
                    problems.append(f"{where}:{line} 类继承列表只允许名字引用")
                visit_body(stmt.body, f"class {stmt.name}")
                continue
            if isinstance(stmt, ast.Assign):
                if all(isinstance(t, (ast.Name, ast.Tuple)) for t in stmt.targets) and _import_time_value_ok(stmt.value):
                    continue
            if isinstance(stmt, ast.AnnAssign):
                if isinstance(stmt.target, ast.Name) and _annotation_ok(stmt.annotation) and _import_time_value_ok(stmt.value):
                    continue
            if isinstance(stmt, ast.If) and index == len(body) - 1 and where == "module":
                test = stmt.test
                # 允许末尾的 `if __name__ == "__main__":`（被 import 时不会执行）
                if (
                    isinstance(test, ast.Compare)
                    and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"
                    and len(test.comparators) == 1
                    and isinstance(test.comparators[0], ast.Constant)
                    and test.comparators[0].value == "__main__"
                ):
                    continue
            problems.append(f"{where}:{line} 导入期会执行的语句 ({type(stmt).__name__})")

    visit_body(tree.body, "module")
    return problems


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
            next_mtime = self.CONFIG_FILE.stat().st_mtime
            with self.CONFIG_FILE.open("r", encoding="utf-8") as handle:
                loaded_config = json.load(handle)
            if not isinstance(loaded_config, dict):
                logger.warning("Parsers config file must be an object; using defaults")
                loaded_config = {"version": "1.0", "parsers": {}}
            parsers_config = loaded_config.get("parsers", {})
            if not isinstance(parsers_config, dict):
                logger.warning("Parsers config field must be an object; ignoring invalid value")
                parsers_config = {}
            loaded_config["parsers"] = parsers_config
            loaded_config.setdefault("version", "1.0")
            self._config = loaded_config
            self._parsers_config = parsers_config
            self._last_mtime = next_mtime
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
            if not isinstance(config, dict):
                logger.warning(f"Skip invalid parser config [{parser_id}]: entry must be an object")
                continue
            if not config.get("enabled", True):
                continue
            try:
                self._load_parser_entry(parser_id, config)
            except Exception as exc:
                logger.warning(f"Failed to register parser [{parser_id}]: {exc}")

    def _save_config(self) -> None:
        atomic_write_json(self.CONFIG_FILE, self._config)
        try:
            self._last_mtime = self.CONFIG_FILE.stat().st_mtime
        except Exception as exc:
            logger.warning(f"Parsers config saved but failed to update mtime: {exc}")

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        tmp_path: Optional[Path] = None

        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                fd = None
                handle.write(content)
                if not content.endswith("\n"):
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
        except Exception:
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            raise

    def _load_parser_entry(self, parser_id: str, config: Dict[str, Any]) -> None:
        module_path = str(config.get("module") or "").strip()
        class_name = str(config.get("class") or "").strip()
        if not module_path or not class_name:
            raise ValueError("Parser config is missing module/class")
        # S3：配置里的模块只能来自解析器包本身，不能借 parsers.json 导入任意模块
        suffix = module_path[len(PARSER_MODULE_PREFIX):] if module_path.startswith(PARSER_MODULE_PREFIX) else ""
        if not suffix or not self._NAME_PATTERN.match(suffix):
            raise ValueError(f"Parser module must be inside {PARSER_MODULE_PREFIX[:-1]}: {module_path}")
        ParserRegistry.load_from_module(module_path, class_name, parser_id)

    def list_parsers(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for info in ParserRegistry.list_all():
            parser_id = str(info.get("id") or "").strip()
            config = self._parsers_config.get(parser_id, {})
            if not isinstance(config, dict):
                config = {}
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
        if not parser_install_enabled():
            raise PermissionError(
                f"运行时安装解析器默认关闭（会以服务进程权限执行源码）；"
                f"仅可信管理员可设置 {PARSER_INSTALL_ENV}=true 后使用"
            )
        normalized = self._normalize_parser_package(payload)
        parser_id = normalized["parser_id"]
        module_name = normalized["module_name"]
        target_path = self.PARSER_DIR / normalized["filename"]
        had_previous_entry = parser_id in self._parsers_config
        previous_entry = copy.deepcopy(self._parsers_config.get(parser_id)) if had_previous_entry else None
        existing_entry = previous_entry if isinstance(previous_entry, dict) else None
        registry_snapshot = dict(getattr(ParserRegistry, "_parsers", {}))

        if ParserRegistry.exists(parser_id) and existing_entry is None:
            raise ValueError(f"解析器 ID 已被内置解析器占用: {parser_id}")

        for other_id, other_entry in self._parsers_config.items():
            if other_id == parser_id:
                continue
            if not isinstance(other_entry, dict):
                logger.warning(f"Skip invalid parser config [{other_id}]: entry must be an object")
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
            "installed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "supported_patterns": normalized["supported_patterns"],
        }

        try:
            self._atomic_write_text(target_path, normalized["source_code"])

            self._parsers_config[parser_id] = entry
            self._config["parsers"] = self._parsers_config
            self._config.setdefault("version", "1.0")
            self._save_config()
            self._load_parser_entry(parser_id, entry)
        except Exception:
            self._rollback_install(
                parser_id=parser_id,
                target_path=target_path,
                previous_entry=previous_entry,
                previous_source=previous_source,
                registry_snapshot=registry_snapshot,
                had_previous_entry=had_previous_entry,
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
            "supported_patterns": copy.deepcopy(normalized["supported_patterns"]),
        }

    def _rollback_install(
        self,
        parser_id: str,
        target_path: Path,
        previous_entry: Optional[Any],
        previous_source: Optional[str],
        registry_snapshot: Optional[Dict[str, Any]] = None,
        had_previous_entry: bool = False,
    ) -> None:
        try:
            if registry_snapshot is not None:
                ParserRegistry._parsers = dict(registry_snapshot)
            if previous_source is None:
                if target_path.exists():
                    target_path.unlink()
            else:
                self._atomic_write_text(target_path, previous_source)
            if previous_entry is None and not had_previous_entry:
                self._parsers_config.pop(parser_id, None)
            else:
                self._parsers_config[parser_id] = previous_entry
            self._config["parsers"] = self._parsers_config
            self._save_config()
            if isinstance(previous_entry, dict) and previous_entry:
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

        class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        if class_name not in class_names:
            raise ValueError(f"解析器源码里没有找到类: {class_name}")

        problems = check_import_time_side_effects(tree)
        if problems:
            raise ValueError(
                "解析器源码在导入期会执行代码，已拒绝安装: " + "; ".join(problems[:5])
            )

        try:
            compile(source_code, filename, "exec")
        except SyntaxError as exc:
            raise ValueError(f"解析器源码编译失败: {exc}") from exc


parser_manager = ParserConfigManager()


__all__ = ["ParserConfigManager", "parser_manager"]
