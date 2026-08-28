"""
app/core/workflow/script_loader.py - 通用工作流 JavaScript 脚本加载与执行器辅助类

职责：
1. 安全路径解析与沙箱目录限制（仅允许 custom_scripts/ 与 scripts/ 目录，杜绝路径遍历）
2. 基于文件 mtime 的内存脚本缓存与热重载
3. 请求上下文宏变量插值（{{context.model}}, {{context.prompt}}, {{context.session_id}} 等）
4. 运行时入参闭包包装 (async function(__CONTEXT__, __ARGS__) { ... })(context, args)
5. 脚本文件自动扫描与 JSDoc 元数据解析
6. 100% 向后兼容传统内联 JS 字符串
"""

import json
import os
import re
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import get_logger, WorkflowError

logger = get_logger("WORKFLOW.SCRIPT_LOADER")

# 宏变量匹配正则：{{ context.xxx }} 或 {{context.xxx}}
MACRO_PATTERN = re.compile(r"\{\{\s*context\.([a-zA-Z0-9_.]+)\s*\}\}")

# JSDoc @description 或首行注释匹配正则
JSDOC_DESC_PATTERN = re.compile(
    r"@description\s+([^\r\n*]+)",
    re.IGNORECASE,
)
BLOCK_COMMENT_FIRST_LINE_PATTERN = re.compile(
    r"/\*\*\s*[\r\n]+\s*\*\s*([^\r\n*]+)",
)
SINGLE_LINE_COMMENT_PATTERN = re.compile(
    r"^\s*//\s*([^\r\n]+)",
    re.MULTILINE,
)


class ScriptLoader:
    """工作流脚本安全加载与模板处理器"""

    # 常见 JS 语法特征字符与关键字，用于防止单行内联 JS 被误判为文件路径
    JS_SYNTAX_CHARS = frozenset("();{}\"'=><+*!?[]|&,")
    JS_KEYWORDS = (
        "return ", "const ", "let ", "var ", "function", "async ", "await ",
        "if ", "for ", "while ", "throw ", "try ", "catch ", "import ", "export ",
        "document.", "window.", "console.", "math.", "json."
    )

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            # 当前文件路径为 app/core/workflow/script_loader.py -> 项目根目录为 3 层上级
            self.base_dir = Path(__file__).resolve().parents[3]
        else:
            self.base_dir = Path(base_dir).resolve()

        # 允许加载脚本的根目录集合
        self.allowed_dirs: List[Path] = [
            (self.base_dir / "custom_scripts").resolve(),
            (self.base_dir / "scripts").resolve(),
        ]

        # 内存缓存：{ absolute_path_str: (mtime, content) }
        self._cache: Dict[str, Tuple[float, str]] = {}

    def clear_cache(self) -> None:
        """清空脚本内存缓存"""
        self._cache.clear()

    def _normalize_target_path_str(self, script_target: str) -> str:
        """去除 URI 前缀并将反斜杠归一化"""
        raw = str(script_target or "").strip()
        if raw.startswith("file://"):
            raw = raw[7:].lstrip("/\\")
        elif raw.startswith("scripts://"):
            raw = raw[10:].lstrip("/\\")
            raw = os.path.join("scripts", raw)
        else:
            raw = raw.lstrip("/\\")
        return raw

    def is_script_target(self, target_or_code: Any) -> bool:
        """
        严格判断 target_or_code 是否为外部脚本文件路径或 URI。
        拒绝任何包含换行符、常见 JS 语法符号或关键字的内联代码。
        """
        if not isinstance(target_or_code, str):
            return False
        s = target_or_code.strip()
        if not s or "\n" in s or "\r" in s:
            return False

        # 明确协议前缀：file:// 或 scripts://
        if s.startswith("file://") or s.startswith("scripts://"):
            return True

        # 若包含常见 JS 语法字符或关键字，绝非文件路径
        if any(c in s for c in self.JS_SYNTAX_CHARS):
            return False
        s_lower = s.lower()
        if any(kw in s_lower for kw in self.JS_KEYWORDS):
            return False

        norm = s.replace("\\", "/")
        # 若以目录前缀开头且以 .js 结尾
        if (norm.startswith("custom_scripts/") or norm.startswith("scripts/")) and norm.lower().endswith(".js"):
            return True
        # 若以 .js 结尾且为合法单文件名（无空格，无路径分隔符）
        if norm.lower().endswith(".js") and "/" not in norm and " " not in norm:
            return True

        return False

    def resolve_script_path(self, script_target: str) -> Path:
        """
        解析并校验脚本路径安全性，防止路径遍历攻击。
        支持相对路径、URI 以及单文件名在 allowed_dirs 中自动寻址。
        若路径越界或不合法，抛出 WorkflowError。
        若文件不存在，抛出 WorkflowError。
        """
        norm_str = self._normalize_target_path_str(script_target)
        if not norm_str:
            raise WorkflowError("js_script_target_empty")

        # 1. 尝试直接以 base_dir 构造候选路径
        candidate_path = (self.base_dir / norm_str).resolve()

        # 检查是否命中允许的沙箱目录
        matched_safe_dir = False
        for allowed_dir in self.allowed_dirs:
            try:
                candidate_path.relative_to(allowed_dir)
                matched_safe_dir = True
                break
            except ValueError:
                continue

        # 2. 若未命中且 norm_str 为无目录结构的纯单文件名，按优先级在 allowed_dirs 中寻找
        if not matched_safe_dir and "/" not in norm_str.replace("\\", "/"):
            for allowed_dir in self.allowed_dirs:
                sub_candidate = (allowed_dir / norm_str).resolve()
                try:
                    sub_candidate.relative_to(allowed_dir)
                    if sub_candidate.is_file():
                        candidate_path = sub_candidate
                        matched_safe_dir = True
                        break
                except ValueError:
                    continue

        if not matched_safe_dir:
            logger.warning(
                f"[ScriptLoader] 拒绝非法的跨目录脚本路径: {script_target!r} -> {candidate_path}"
            )
            raise WorkflowError(f"js_script_path_forbidden: {script_target}")

        if not candidate_path.suffix.lower() == ".js":
            logger.warning(
                f"[ScriptLoader] 拒绝非 .js 后缀文件: {script_target!r} -> {candidate_path}"
            )
            raise WorkflowError(f"js_script_invalid_extension: {script_target}")

        if not candidate_path.is_file():
            logger.warning(
                f"[ScriptLoader] 脚本文件不存在: {script_target!r} -> {candidate_path}"
            )
            raise WorkflowError(f"js_script_not_found: {script_target}")

        return candidate_path

    def load_script_content(self, file_path: Path) -> str:
        """
        基于 mtime 内存缓存读取脚本文件内容，修改即时重载。
        """
        path_key = str(file_path.resolve())
        try:
            mtime = file_path.stat().st_mtime
        except Exception as e:
            raise WorkflowError(f"js_script_stat_failed: {e}")

        if path_key in self._cache:
            cached_mtime, cached_content = self._cache[path_key]
            if cached_mtime == mtime:
                return cached_content

        try:
            content = file_path.read_text(encoding="utf-8-sig")
            self._cache[path_key] = (mtime, content)
            logger.debug(f"[ScriptLoader] 已加载脚本文件 (mtime={mtime}): {file_path.name}")
            return content
        except Exception as e:
            raise WorkflowError(f"js_script_read_failed: {e}")

    @staticmethod
    def _lookup_context_value(context: Dict[str, Any], key_path: str) -> Any:
        """根据点分路径从 context 字典中查找值"""
        parts = key_path.split(".")
        current = context
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def interpolate_macros(
        self,
        data: Any,
        context: Optional[Dict[str, Any]],
        is_code_template: bool = False,
    ) -> Any:
        """
        递归对字符串、字典或列表中的 {{context.key}} 进行宏变量插值替换。
        当 is_code_template 为 True 时，对字符串值进行安全转义（json.dumps[1:-1]），
        防止破坏外层 JS 代码中的字符串字面量或注入非法换行。
        """
        if context is None:
            context = {}

        if isinstance(data, str):
            if "{{" not in data:
                return data

            # 如果整个字符串恰好就是一个 {{context.xxx}}，且不是代码模板模式，直接返回上下文中的原始值
            exact_match = re.fullmatch(r"\{\{\s*context\.([a-zA-Z0-9_.]+)\s*\}\}", data.strip())
            if exact_match and not is_code_template:
                val = self._lookup_context_value(context, exact_match.group(1).strip())
                return val if val is not None else ""

            def _repl(match: re.Match) -> str:
                key_path = match.group(1).strip()
                val = self._lookup_context_value(context, key_path)
                if val is None:
                    return ""
                if isinstance(val, (dict, list, bool, int, float)):
                    return json.dumps(val, ensure_ascii=False, default=str)
                val_str = str(val)
                if is_code_template:
                    return json.dumps(val_str, ensure_ascii=False)[1:-1]
                return val_str

            return MACRO_PATTERN.sub(_repl, data)

        elif isinstance(data, dict):
            return {
                k: self.interpolate_macros(v, context, is_code_template=is_code_template)
                for k, v in data.items()
            }

        elif isinstance(data, list):
            return [
                self.interpolate_macros(item, context, is_code_template=is_code_template)
                for item in data
            ]

        return data

    def wrap_script_closure(
        self,
        code: str,
        context: Optional[Dict[str, Any]] = None,
        args: Optional[Any] = None,
        script_id: str = "",
        lifecycle: str = "workflow",
        owner_id: str = "",
    ) -> str:
        """
        将脚本代码包装为标准的异步闭包执行体：
        return (async function(__CONTEXT__, __ARGS__) {
            <code>
        })(context_json, args_json)
        """
        safe_context = context if isinstance(context, dict) else {}
        safe_args = args if (args is not None and args != "") else {}

        # 若 args 为 JSON 字符串，尝试反序列化
        if isinstance(safe_args, str):
            trimmed = safe_args.strip()
            if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
                try:
                    safe_args = json.loads(trimmed)
                except Exception:
                    pass

        context_json = json.dumps(safe_context, ensure_ascii=False, default=str)
        args_json = json.dumps(safe_args, ensure_ascii=False, default=str)

        # The page-side registry gives managed scripts an explicit disposal scope.
        # It cannot undo arbitrary side effects automatically; scripts must register
        # cleanup callbacks for globals, listeners, timers, and other resources.
        lifecycle_value = str(lifecycle or "workflow").strip().lower()
        if lifecycle_value not in {"workflow", "resident", "step"}:
            lifecycle_value = "workflow"
        safe_script_id = str(script_id or "inline").strip() or "inline"
        safe_owner_id = str(owner_id or "default").strip() or "default"
        lifecycle_bootstrap = (
            "const __uwaLifecycle = (window.__UWA_SCRIPT_LIFECYCLE__ "
            "|| (window.__UWA_SCRIPT_LIFECYCLE__ = (function(){"
            "const entries = new Map(); let current = null; let activeOwner = null;"
            "const disposeEntry = (entry) => { if (!entry || entry.disposed) return; entry.disposed = true;"
            "try { if (entry.controller) entry.controller.abort(); } catch (_) {}"
            "try { for (const fn of entry.cleanups.splice(0)) { try { fn(); } catch (_) {} } } catch (_) {}"
            "if (entries.get(entry.id) === entry) entries.delete(entry.id); };"
            "const activate = (id, policy, owner) => { const active = !activeOwner || activeOwner === owner;"
            "if (!active) return {id, policy, owner, active:false, controller:null, signal:null, cleanups:[], disposed:true};"
            "const previous = entries.get(id); if (previous) disposeEntry(previous);"
            "const controller = typeof AbortController === 'function' ? new AbortController() : null;"
            "const entry = {id, policy, owner, active, controller, signal: controller ? controller.signal : null, cleanups: [], disposed:false};"
            "entries.set(id, entry); current = entry; return entry; };"
            "const registerCleanup = (fn) => { if (current && typeof fn === 'function') {"
            "current.cleanups.push(fn); return fn; } return null; };"
            "const cleanup = (id) => disposeEntry(entries.get(id));"
            "const beginWorkflow = (owner) => { activeOwner = owner; for (const entry of Array.from(entries.values())) {"
            "if (entry.policy !== 'resident' && entry.owner !== owner) disposeEntry(entry); } };"
            "const cleanupOwner = (owner) => { for (const entry of Array.from(entries.values())) {"
            "if (entry.owner === owner && entry.policy !== 'resident') disposeEntry(entry); } };"
            "const cleanupWorkflow = () => { for (const entry of Array.from(entries.values())) {"
            "if (entry.policy !== 'resident') disposeEntry(entry); } };"
            "const cleanupAll = () => { for (const entry of Array.from(entries.values())) disposeEntry(entry); };"
            "return {activate, registerCleanup, cleanup, beginWorkflow, cleanupOwner, cleanupWorkflow, cleanupAll};"
            "})()));"
            f"const __uwaScope = __uwaLifecycle.activate({json.dumps(safe_script_id, ensure_ascii=False)}, {json.dumps(lifecycle_value)}, {json.dumps(safe_owner_id, ensure_ascii=False)});"
            "const __uwaPreviousScope = window.__UWA_ACTIVE_SCRIPT_SCOPE__;"
            "window.__UWA_ACTIVE_SCRIPT_SCOPE__ = __uwaScope;"
            "const __uwaRegisterCleanup = (fn) => { if (__uwaScope && typeof fn === 'function') {"
            "__uwaScope.cleanups.push(fn); return fn; } return null; };"
            "const __uwaSignal = __uwaScope.signal;"
        )
        lifecycle_restore = (
            "window.__UWA_ACTIVE_SCRIPT_SCOPE__ = __uwaPreviousScope;"
        )

        # 包装为闭包
        wrapped = (
            f"return (async function(__CONTEXT__, __ARGS__) {{\n"
            f"{lifecycle_bootstrap}\n"
            "if (__uwaScope.active) { try {\n"
            f"{code}\n"
            "} finally {\n"
            f"{lifecycle_restore}\n"
            "} }\n"
            f"}})({context_json}, {args_json});"
        )
        return wrapped

    def resolve_and_load(
        self,
        script_target: Optional[str] = None,
        code_or_args: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
        script_id: str = "",
        lifecycle: str = "workflow",
        owner_id: str = "",
    ) -> str:
        """
        统一解析并准备可直接在页面执行的 JavaScript 代码。

        分支 1：外部脚本模式（script_target 显式非空）
        - 强制按外部脚本文件加载与解析（若路径不合法或文件不存在，立即 Fail-Fast 抛错，绝不静默穿透回退为内联 JS）；
        - 插值替换宏变量；
        - 将 code_or_args 作为 __ARGS__、context 作为 __CONTEXT__ 包装进闭包。

        分支 2：向后兼容传统内联 JS 代码（script_target 为空，code_or_args 承载代码）
        - 如果 code_or_args 形式为脚本文件路径，则按文件加载；
        - 否则对内联代码进行宏变量插值，并直接返回该代码字符串执行。
        """
        ctx = context if isinstance(context, dict) else {}
        raw_target = str(script_target or "").strip()
        ctx_model = ctx.get("model", "")
        ctx_prompt = str(ctx.get("prompt", "") or "")[:80]
        ctx_session = ctx.get("session_id", "")
        logger.debug(
            f"[ScriptLoader:RESOLVE] 开始解析脚本: target={raw_target!r}, "
            f"context={{model: {ctx_model!r}, session_id: {ctx_session!r}, prompt_preview: {ctx_prompt!r}}}, "
            f"code_or_args_preview={str(code_or_args)[:120]!r}"
        )

        # 分支 1：显式指定了 script_target，严格按照外部脚本执行（Fail-Fast）
        if raw_target:
            file_path = self.resolve_script_path(raw_target)
            relative_id = file_path.relative_to(self.base_dir).as_posix()
            effective_script_id = script_id or relative_id
            raw_code = self.load_script_content(file_path)
            interpolated_code = self.interpolate_macros(raw_code, ctx, is_code_template=True)

            # 智能解析 code_or_args（若为 JSON 字符串则先反序列化）
            parsed_args = code_or_args
            if isinstance(parsed_args, str):
                trimmed = parsed_args.strip()
                if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
                    try:
                        parsed_args = json.loads(trimmed)
                    except Exception:
                        pass

            # 对入参 args 进行宏变量插值
            interpolated_args = self.interpolate_macros(parsed_args, ctx, is_code_template=False)
            logger.debug(
                f"[ScriptLoader:MACRO] 外部脚本宏插值完成: file={file_path.name}, "
                f"code_len={len(interpolated_code)}, args={str(interpolated_args)[:150]}"
            )
            wrapped = self.wrap_script_closure(
                code=interpolated_code,
                context=ctx,
                args=interpolated_args,
                script_id=effective_script_id,
                lifecycle=lifecycle,
                owner_id=owner_id,
            )
            logger.debug(
                f"[ScriptLoader:WRAP] 闭包构建完成: file={file_path.name}, "
                f"wrapped_len={len(wrapped)}, context_keys={list(ctx.keys())}"
            )
            return wrapped

        # 分支 2：script_target 为空，检查 code_or_args 是否为脚本文件路径
        if isinstance(code_or_args, str) and self.is_script_target(code_or_args):
            file_path = self.resolve_script_path(code_or_args)
            relative_id = file_path.relative_to(self.base_dir).as_posix()
            effective_script_id = script_id or relative_id
            raw_code = self.load_script_content(file_path)
            interpolated_code = self.interpolate_macros(raw_code, ctx, is_code_template=True)
            logger.debug(
                f"[ScriptLoader:MACRO] 纯路径脚本宏插值完成: file={file_path.name}, code_len={len(interpolated_code)}"
            )
            wrapped = self.wrap_script_closure(
                code=interpolated_code,
                context=ctx,
                args={},
                script_id=effective_script_id,
                lifecycle=lifecycle,
                owner_id=owner_id,
            )
            logger.debug(
                f"[ScriptLoader:WRAP] 闭包构建完成: file={file_path.name}, wrapped_len={len(wrapped)}"
            )
            return wrapped

        # 传统内联 JS 字符串
        inline_code = str(code_or_args or "").strip()
        if not inline_code:
            raise WorkflowError("js_exec_empty")

        # 对内联代码执行宏变量插值（代码模式）
        interpolated_inline = str(self.interpolate_macros(inline_code, ctx, is_code_template=True))
        logger.debug(
            f"[ScriptLoader:INLINE] 内联脚本宏插值完成: original={inline_code[:80]!r}, "
            f"interpolated={interpolated_inline[:80]!r}"
        )
        # Inline JS also participates in the lifecycle registry. Hashing keeps the
        # identifier stable without exposing the full source in page globals.
        # Preserve the historical raw-inline execution unless lifecycle control
        # was explicitly requested for this inline step.
        if not script_id and str(lifecycle or "workflow").strip().lower() == "workflow":
            return interpolated_inline
        effective_script_id = script_id or (
            "inline:" + hashlib.sha256(interpolated_inline.encode("utf-8")).hexdigest()[:16]
        )
        return self.wrap_script_closure(
            code=interpolated_inline,
            context=ctx,
            args={},
            script_id=effective_script_id,
            lifecycle=lifecycle,
            owner_id=owner_id,
        )

    def extract_script_description(self, content: str, default_name: str = "") -> str:
        """从 JavaScript 脚本头部注释提取描述信息"""
        head_sample = content[:2000]
        # 1. 优先提取 @description
        m = JSDOC_DESC_PATTERN.search(head_sample)
        if m:
            return m.group(1).strip()

        # 2. 提取 JSDoc 首行
        m = BLOCK_COMMENT_FIRST_LINE_PATTERN.search(head_sample)
        if m:
            desc = m.group(1).strip()
            if desc and not desc.startswith("@"):
                return desc

        # 3. 提取单行注释 // ...
        for match in SINGLE_LINE_COMMENT_PATTERN.finditer(head_sample):
            desc = match.group(1).strip()
            # 排除常见非描述单行注释（如 ==, --, eslint, @ts-check, jshint, / 等）
            if (
                desc
                and not desc.startswith("==")
                and not desc.startswith("--")
                and not desc.startswith("@")
                and not desc.lower().startswith("eslint")
                and not desc.lower().startswith("jshint")
                and not desc.lower().startswith("istanbul")
            ):
                return desc

        return default_name or "未提供描述"

    def scan_available_scripts(self) -> List[Dict[str, Any]]:
        """
        安全扫描 custom_scripts/ 与 scripts/ 目录下的所有 .js 文件，
        返回相对路径、URI、文件名、所属分类、修改时间与 JSDoc 描述。
        """
        results: List[Dict[str, Any]] = []

        scan_roots = [
            ("custom_scripts", self.base_dir / "custom_scripts", "custom"),
            ("scripts", self.base_dir / "scripts", "scripts"),
        ]

        seen_rel_paths = set()

        for folder_name, folder_path, category in scan_roots:
            if not folder_path.is_dir():
                continue

            for root, dirs, files in os.walk(folder_path):
                # 排除隐藏目录与 node_modules
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules" and d != "__pycache__"]

                for file_name in files:
                    if not file_name.lower().endswith(".js"):
                        continue

                    full_path = Path(root) / file_name
                    try:
                        rel_path = full_path.relative_to(self.base_dir).as_posix()
                    except ValueError:
                        continue

                    if rel_path in seen_rel_paths:
                        continue
                    seen_rel_paths.add(rel_path)

                    try:
                        stat = full_path.stat()
                        mtime = int(stat.st_mtime)
                        content = self.load_script_content(full_path)
                        description = self.extract_script_description(content, file_name)
                    except Exception as e:
                        logger.debug(f"[ScriptLoader] 扫描脚本元数据失败 {rel_path}: {e}")
                        mtime = 0
                        description = "脚本元数据读取失败"

                    results.append({
                        "path": rel_path,
                        "uri": f"file://{rel_path}",
                        "name": file_name,
                        "category": category,
                        "description": description,
                        "mtime": mtime,
                    })

        # 按分类与路径排序
        results.sort(key=lambda x: (0 if x["category"] == "custom" else 1, x["path"]))
        return results


# 全局单例
script_loader = ScriptLoader()
