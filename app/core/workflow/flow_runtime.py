"""Structured workflow v2: bounded compiler, request-local variables and safe control flow.

The compiler emits ordinary legacy action dictionaries interleaved with private
instructions. BrowserWorkflowMixin still executes EVERY leaf in its existing
media/ownership/cancellation lifecycle. No eval, Python attribute access or
arbitrary jumps. Public schema remains a list of {action,target,optional,value}.
"""
from __future__ import annotations

import copy
import json
import math
import re
import time
from urllib.parse import quote
from typing import Any

from app.core.config import WorkflowError, WorkflowCancelledError

CONTROL_ACTIONS = frozenset({"SET", "CAPTURE", "IF", "SWITCH", "GROUP", "GUARD", "TRY", "LABEL"})
LEAF_ACTIONS = frozenset({"FILL_INPUT", "SELECT_MODEL", "CLICK", "COORD_CLICK", "COORD_SCROLL", "STREAM_WAIT", "STREAM_OUTPUT", "KEY_PRESS", "WAIT", "JS_EXEC", "READONLY_HINT", "PAGE_FETCH"})
OPS = {"eq", "ne", "contains", "starts_with", "ends_with", "matches", "exists", "not_exists", "gt", "gte", "lt", "lte", "in"}
TRANSFORMS = {"trim", "lower", "upper", "string", "number", "boolean", "urlencode", "css_escape", "json"}
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
TOKEN = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}|(?<!\{)\{([A-Za-z_][\w.]*)\}(?!\})")
MISSING = object()
MAX_NODES, MAX_DEPTH, MAX_BYTES = 1000, 16, 1_048_576


class FlowValidationError(WorkflowError):
    pass


class FlowVariableError(WorkflowError):
    pass


class FlowLimitError(WorkflowError):
    pass


def _size(value, limit=MAX_BYTES):
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limit:
            raise FlowValidationError("工作流数据超出大小限制")
    except (TypeError, ValueError, RecursionError) as exc:
        raise FlowValidationError("工作流必须是有限、可序列化的 JSON 数据") from exc


def validate_inputs(value):
    if not isinstance(value, dict) or len(value) > 128:
        raise FlowValidationError("workflow_variables 必须为不超过 128 个键的对象")
    if any(not isinstance(k, str) or not NAME.fullmatch(k) or k.startswith("__") for k in value):
        raise FlowValidationError("变量名只能包含字母、数字及下划线，不能以数字或双下划线开头")
    _size(value, 65536)
    return value


def has_control_flow(workflow):
    return any(isinstance(step, dict) and (step.get("action") in CONTROL_ACTIONS or step.get("flow_version") == 2 or "selector" in step) for step in (workflow or []))


def _condition_valid(condition, depth=0):
    if depth > MAX_DEPTH or not isinstance(condition, dict):
        raise FlowValidationError("条件必须为对象，嵌套不能超过 16 层")
    for mode in ("all", "any"):
        if mode in condition:
            if not isinstance(condition[mode], list) or not 1 <= len(condition[mode]) <= 64:
                raise FlowValidationError("组合条件需要 1–64 个子条件")
            for child in condition[mode]:
                _condition_valid(child, depth + 1)
            return
    if "not" in condition:
        _condition_valid(condition["not"], depth + 1)
        return
    if condition.get("op") not in OPS or "left" not in condition:
        raise FlowValidationError("条件缺少 left 或使用了未知运算符")
    if condition["op"] not in {"exists", "not_exists"} and "right" not in condition:
        raise FlowValidationError("比较条件缺少 right")


def validate_workflow(workflow):
    """Validate all branches BEFORE any page action; never mutate user JSON."""
    if not isinstance(workflow, list):
        raise FlowValidationError("workflow 必须为数组")
    _size(workflow)
    count = 0

    def walk(nodes, depth=0):
        nonlocal count
        if depth > MAX_DEPTH or not isinstance(nodes, list):
            raise FlowValidationError("分支必须为数组，嵌套不能超过 16 层")
        labels = {}
        for i, node in enumerate(nodes):
            count += 1
            if count > MAX_NODES or not isinstance(node, dict):
                raise FlowValidationError("步骤必须为对象，步骤总数不能超过 1000")
            action = node.get("action")
            if action not in CONTROL_ACTIONS | LEAF_ACTIONS:
                raise FlowValidationError(f"无效工作流动作: {action}")
            if "retry_safe" in node and not isinstance(node["retry_safe"], bool):
                raise FlowValidationError("retry_safe 必须为布尔值")
            if action not in CONTROL_ACTIONS:
                continue
            v = node.get("value")
            if not isinstance(v, dict):
                raise FlowValidationError(f"{action}.value 必须为对象")
            if action in {"SET", "CAPTURE", "LABEL"}:
                name = v.get("name", "")
                if not isinstance(name, str) or not NAME.fullmatch(name) or name.startswith("__"):
                    raise FlowValidationError(f"{action} 需要合法的 name")
            if action in {"SET", "CAPTURE"} and v.get("scope", "local") not in {"local", "global"}:
                raise FlowValidationError("变量 scope 必须为 local 或 global")
            if action == "SET":
                if "value" not in v or v.get("mode", "assign") not in {"assign", "default"}:
                    raise FlowValidationError("SET 需要 value，mode 为 assign/default")
            if action == "CAPTURE":
                if v.get("source", "text") not in {"text", "attribute", "class", "url", "exists", "value"}:
                    raise FlowValidationError("不支持的 CAPTURE.source")
                if v.get("source") == "attribute" and not v.get("attribute"):
                    raise FlowValidationError("捕获属性必须指定 attribute")
                if not isinstance(v.get("timeout", 2), (int, float)) or not 0 <= v.get("timeout", 2) <= 30:
                    raise FlowValidationError("捕获超时需为 0–30 秒")
            if action in {"IF", "GUARD"}:
                _condition_valid(v.get("condition"))
            if action == "SWITCH":
                cases = v.get("cases")
                if not isinstance(cases, list) or not 1 <= len(cases) <= 32:
                    raise FlowValidationError("多分支需要 1–32 个有序情况")
                for case in cases:
                    if not isinstance(case, dict):
                        raise FlowValidationError("情况必须是对象")
                    if "label" in case and (not isinstance(case["label"], str) or len(case["label"]) > 160):
                        raise FlowValidationError("情况名称必须是 160 字以内的文本")
                    _condition_valid(case.get("condition"))
                    walk(case.get("steps", []), depth + 1)
                walk(v.get("default", []), depth + 1)
            if action == "IF":
                walk(v.get("then", []), depth + 1)
                walk(v.get("else", []), depth + 1)
            if action in {"GROUP", "TRY"}:
                walk(v.get("steps", []), depth + 1)
            if action == "GROUP":
                validate_inputs(v.get("variables", {}))
            if action == "TRY":
                attempts = v.get("attempts", 1)
                if type(attempts) is not int or not 1 <= attempts <= 5:
                    raise FlowValidationError("TRY.attempts 必须为 1–5 的整数")
                delay = v.get("delay", 0.3)
                if type(delay) not in (int, float) or not 0 <= delay <= 5:
                    raise FlowValidationError("TRY.delay 必须为 0–5 秒")
                if "retry_side_effects" in v and not isinstance(v["retry_side_effects"], bool):
                    raise FlowValidationError("retry_side_effects 必须为布尔值")
                if "errors" in v and (not isinstance(v["errors"], list) or not v["errors"] or any(not isinstance(x, str) for x in v["errors"])):
                    raise FlowValidationError("TRY.errors 必须为非空错误类型数组")
                if "fallback" in v:
                    walk(v["fallback"], depth + 1)
            if action == "GUARD" and v.get("mode", "skip_group") not in {"skip_group", "goto"}:
                raise FlowValidationError("GUARD.mode 必须为 skip_group/goto")
            if action == "LABEL":
                if v["name"] in labels:
                    raise FlowValidationError("同一分支内的锚点名称不能重复")
                labels[v["name"]] = i
        for i, node in enumerate(nodes):
            if node.get("action") == "GUARD" and node["value"].get("mode") == "goto":
                target = labels.get(node["value"].get("anchor"), -1)
                if target <= i:
                    raise FlowValidationError("只能前往同一分支内位于后方的锚点")
    walk(workflow)
    return {"valid": True, "nodes": count, "version": 2 if has_control_flow(workflow) else 1}


class Variables:
    """Request globals, explicit lexical locals, read-only inputs/context namespaces."""
    def __init__(self, context=None, inputs=None):
        self.context = context if context is not None else {}
        self.inputs = copy.deepcopy(validate_inputs(inputs if inputs is not None else {}))
        self.globals = copy.deepcopy(self.inputs)
        self.locals = []
        self.error = {}

    def merged(self):
        result = dict(self.globals)
        for scope in self.locals:
            result.update(scope["values"])
        return result

    def lookup(self, path, missing=False):
        parts = str(path).split(".")
        if any(not NAME.fullmatch(p) or p.startswith("__") for p in parts):
            raise FlowVariableError("不合法的变量路径")
        namespaces = {"vars": self.merged(), "inputs": self.inputs, "context": self.context, "error": self.error}
        if parts[0] in namespaces:
            obj = namespaces[parts.pop(0)]
        else:
            obj = self.merged()
            if parts[0] not in obj and parts[0] in self.context:
                obj = self.context
        for part in parts:
            if not isinstance(obj, dict) or part not in obj:
                if missing:
                    return MISSING
                raise FlowVariableError(f"变量未定义: {path}")
            obj = obj[part]
        return copy.deepcopy(obj)

    def resolve(self, value, depth=0):
        if depth > 32:
            raise FlowLimitError("表达式嵌套超过限制")
        if isinstance(value, dict):
            if "$literal" in value:
                return copy.deepcopy(value["$literal"])
            if "$var" in value:
                result = self.lookup(value["$var"], missing="default" in value)
                return self.resolve(value.get("default"), depth + 1) if result is MISSING else result
            if "$map" in value:
                source = self.resolve(value.get("input"), depth + 1)
                table = value["$map"]
                if not isinstance(table, dict):
                    raise FlowVariableError("$map 必须为字典")
                key = str(source)
                if key in table:
                    return self.resolve(table[key], depth + 1)
                if "default" in value:
                    return self.resolve(value["default"], depth + 1)
                raise FlowVariableError("映射中没有匹配值且未设置 default")
            if "$transform" in value:
                op = value["$transform"]
                if op not in TRANSFORMS:
                    raise FlowVariableError("未知转换操作")
                source = self.resolve(value.get("input"), depth + 1)
                text = str(source) if source is not None else ""
                if op in {"trim", "lower", "upper"}:
                    return {"trim": str.strip, "lower": str.lower, "upper": str.upper}[op](text)
                if op == "string": return text
                if op == "json": return json.dumps(source, ensure_ascii=False, allow_nan=False)
                if op == "urlencode": return quote(text, safe="")
                if op == "css_escape":
                    return "".join(c if (c.isascii() and (c.isalpha() or c in "_-" or (i > 0 and c.isdigit()))) else f"\\{ord(c):x} " for i, c in enumerate(text))
                if op == "number":
                    try:
                        number = float(source)
                        if not math.isfinite(number): raise ValueError()
                        return number
                    except (ValueError, TypeError) as exc:
                        raise FlowVariableError("无法转换为有限数字") from exc
                if op == "boolean":
                    if source in (True, False) and type(source) is bool: return source
                    if text.lower() in {"true", "1"}: return True
                    if text.lower() in {"false", "0", ""}: return False
                    raise FlowVariableError("布尔转换只接受 true/false/1/0")
            return {k: self.resolve(v, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v, depth + 1) for v in value]
        if not isinstance(value, str):
            return value
        exact = TOKEN.fullmatch(value)
        if exact:
            return self.lookup(exact.group(1) or exact.group(2))
        return TOKEN.sub(lambda m: str(self.lookup(m.group(1) or m.group(2))), value)

    def set(self, name, value, scope="local", mode="assign"):
        target = self.locals[-1]["values"] if scope == "local" and self.locals else self.globals
        if mode == "default" and self.lookup("vars." + name, missing=True) is not MISSING:
            return
        _size(value, 65536)
        target[name] = copy.deepcopy(value)
        _size(self.merged(), 262144)

    def condition(self, condition):
        if "all" in condition: return all(self.condition(c) for c in condition["all"])
        if "any" in condition: return any(self.condition(c) for c in condition["any"])
        if "not" in condition: return not self.condition(condition["not"])
        op, left = condition["op"], condition["left"]
        if op in {"exists", "not_exists"}:
            path = left.get("$var") if isinstance(left, dict) else None
            if path is None and isinstance(left, str):
                match = TOKEN.fullmatch(left)
                path = (match.group(1) or match.group(2)) if match else None
            result = self.lookup(path, missing=True) if path else self.resolve(left)
            exists = result is not MISSING and result is not None
            return exists if op == "exists" else not exists
        a, b = self.resolve(left), self.resolve(condition["right"])
        if op == "eq": return type(a) is type(b) and a == b
        if op == "ne": return not (type(a) is type(b) and a == b)
        if op in {"gt", "gte", "lt", "lte"}:
            if type(a) not in (float, int) or type(b) not in (float, int):
                raise FlowVariableError("大小比较要求数字，请先使用 number 转换")
            return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
        if op == "contains":
            if not isinstance(a, (str, list, dict)): raise FlowVariableError("contains 需要字符串、数组或对象")
            return b in a
        if op == "in":
            if not isinstance(b, (str, list, dict)): raise FlowVariableError("in 需要字符串、数组或对象")
            return a in b
        if op in {"starts_with", "ends_with"}:
            if not isinstance(a, str) or not isinstance(b, str): raise FlowVariableError("前后缀比较需要字符串")
            return a.startswith(b) if op == "starts_with" else a.endswith(b)
        if op == "matches":
            if not isinstance(a, str) or not isinstance(b, str) or len(b) > 512 or len(a) > 65536:
                raise FlowVariableError("正则需要字符串，模式≤512字符、输入≤65536字符")
            import regex
            try:
                return bool(regex.search(b, a, timeout=0.025))
            except (regex.error, TimeoutError) as exc:
                raise FlowVariableError("正则无效或超过 25ms 求值预算") from exc
        raise FlowVariableError("未知条件运算符")


class FlowProgram:
    def __init__(self, workflow, context=None, inputs=None, capture=None, stop_checker=None, max_transitions=10000, deadline_seconds=300):
        validate_workflow(workflow)
        self.variables = Variables(context, inputs)
        self.capture = capture
        self.stop_checker = stop_checker or (lambda: False)
        self.steps, self.trace, self.tries = [], [], []
        self.output_emitted = False
        self.transitions = 0
        self.max_transitions = min(10000, max(1, max_transitions))
        self.deadline = time.monotonic() + min(3600, max(1, deadline_seconds))
        self._compile(copy.deepcopy(workflow), "root", None)

    def _emit(self, op, path, **kwargs):
        self.steps.append({"action": "__FLOW_INTERNAL", "target": "", "optional": False, "_op": op, "_path": path, **kwargs})
        return len(self.steps) - 1

    def _compile(self, nodes, path, group_end):
        labels, jumps = {}, []
        for n, node in enumerate(nodes):
            p = f"{path}.{n}"
            action, v = node["action"], node.get("value", {})
            if action not in CONTROL_ACTIONS:
                self.steps.append({**node, "_path": p})
            elif action == "IF":
                test = self._emit("if", p, condition=v["condition"])
                self._compile(v.get("then", []), p + ".then", group_end)
                jump = self._emit("jump", p)
                self.steps[test]["to"] = len(self.steps)
                self._compile(v.get("else", []), p + ".else", group_end)
                self.steps[jump]["to"] = self._emit("merge", p)
            elif action == "SWITCH":
                test = self._emit("switch", p, cases=[])
                exits = []
                for case_index, case in enumerate(v["cases"]):
                    branch = f"case_{case_index}"
                    self.steps[test]["cases"].append({"condition": case["condition"], "to": len(self.steps), "branch": branch})
                    self._compile(case.get("steps", []), p + "." + branch, group_end)
                    exits.append(self._emit("jump", p))
                self.steps[test]["default_to"] = len(self.steps)
                self._compile(v.get("default", []), p + ".default", group_end)
                merge = self._emit("merge", p)
                for exit_index in exits:
                    self.steps[exit_index]["to"] = merge
            elif action == "GROUP":
                ref = {}
                enter = self._emit("enter", p, variables=v.get("variables", {}), end=ref)
                self._compile(v.get("steps", []), p + ".steps", ref)
                ref["index"] = self._emit("leave", p, start=enter)
            elif action == "TRY":
                start = self._emit("try", p, policy=v)
                self._compile(v.get("steps", []), p + ".steps", group_end)
                done = self._emit("try_done", p, start=start)
                self.steps[start]["fallback"] = len(self.steps) if "fallback" in v else None
                self._compile(v.get("fallback", []), p + ".fallback", group_end)
                end = self._emit("catch_done", p, start=start)
                self.steps[start]["end"] = end
                self.steps[done]["to"] = end + 1
            elif action == "GUARD":
                idx = self._emit("guard", p, condition=v["condition"], end=group_end)
                if v.get("mode") == "goto": jumps.append((idx, v["anchor"]))
            elif action == "LABEL":
                labels[v["name"]] = self._emit("label", p)
            else:
                self._emit(action.lower(), p, spec=v, target=node.get("target", ""))
        for idx, name in jumps:
            self.steps[idx]["to"] = labels[name]

    def _check(self, transition=True):
        if self.stop_checker(): raise WorkflowCancelledError("workflow_cancelled")
        if time.monotonic() > self.deadline: raise FlowLimitError("工作流运行时间超过控制流预算")
        self.transitions += int(transition)
        if self.transitions > self.max_transitions: raise FlowLimitError("工作流转换次数超过限制")

    def _record(self, step, status, **meta):
        # Deliberately exclude prompt, values, capture contents, selectors and error text.
        if len(self.trace) < 500:
            self.trace.append({"path": step.get("_path"), "action": step.get("_op", step.get("action")), "status": status, **meta})

    def _seek(self, index):
        self.variables.locals[:] = [s for s in self.variables.locals if s["start"] < index <= s["end"]]
        while self.tries and not (self.tries[-1]["start"] < index <= self.tries[-1]["end"]):
            frame = self.tries.pop()
            self.variables.error = frame["previous_error"]

    def next_leaf(self, index):
        while index < len(self.steps):
            self._check()
            self._seek(index)
            step = self.steps[index]
            op = step.get("_op")
            if not op:
                return index
            try:
                next_index = index + 1
                if op == "if":
                    result = self.variables.condition(step["condition"])
                    self._record(step, "branch", branch="then" if result else "else")
                    if not result: next_index = step["to"]
                elif op == "switch":
                    next_index = step["default_to"]
                    branch = "default"
                    for case in step["cases"]:
                        self._check()
                        if self.variables.condition(case["condition"]):
                            next_index, branch = case["to"], case["branch"]
                            break
                    self._record(step, "branch", branch=branch)
                elif op == "guard":
                    if self.variables.condition(step["condition"]):
                        next_index = step.get("to", (step["end"]["index"] if step.get("end") is not None else len(self.steps)))
                        self._record(step, "skipped")
                    else: self._record(step, "continued")
                elif op == "jump": next_index = step["to"]
                elif op == "merge": self._record(step, "merged")
                elif op == "label": self._record(step, "label")
                elif op == "enter":
                    values = self.variables.resolve(step["variables"])
                    self.variables.locals.append({"start": index, "end": step["end"]["index"], "values": values})
                    _size(self.variables.merged(), 262144)
                    self._record(step, "entered")
                elif op == "leave":
                    if self.variables.locals and self.variables.locals[-1]["start"] == step["start"]:
                        self.variables.locals.pop()
                    self._record(step, "merged")
                elif op == "set":
                    v = step["spec"]
                    if v.get("mode") != "default" or self.variables.lookup("vars." + v["name"], missing=True) is MISSING:
                        self.variables.set(v["name"], self.variables.resolve(v["value"]), v.get("scope", "local"), v.get("mode", "assign"))
                    self._record(step, "assigned", variable=v["name"])
                elif op == "capture":
                    if self.capture is None: raise WorkflowError("缺少页面状态捕获适配器")
                    v = step["spec"]
                    spec = self.variables.resolve({k: val for k, val in v.items() if k not in {"name", "scope"}})
                    spec["name"] = v["name"]  # Adapter metadata, never evaluated as page code.
                    result = self.capture(spec, str(self.variables.resolve(step["target"])))
                    self.variables.set(v["name"], result, v.get("scope", "local"))
                    self._record(step, "captured", variable=v["name"])
                elif op == "try":
                    self.tries.append({"start": index, "end": step["end"], "fallback": step["fallback"], "policy": step["policy"],
                        "attempt": 1, "output": False, "effect": False, "catching": False, "previous_error": self.variables.error,
                        "snapshot": copy.deepcopy((self.variables.globals, self.variables.locals))})
                    self._record(step, "attempt", attempt=1)
                elif op == "try_done":
                    next_index = step["to"]
                elif op == "catch_done":
                    pass
                index = next_index
            except (FlowValidationError, FlowLimitError, WorkflowCancelledError):
                raise
            except Exception as exc:
                recovered = self.recover(exc, index)
                if recovered is None: raise
                index = recovered
        self._seek(index)
        return index

    def prepare_leaf(self, index, selectors):
        self._check()
        step = copy.deepcopy(self.steps[index])
        step["target"] = str(self.variables.resolve(step.get("target", "")))
        if step["action"] != "JS_EXEC":
            step["value"] = self.variables.resolve(step.get("value"))
        step["execution"] = self.variables.resolve(step.get("execution"))
        raw = step.get("selector", selectors.get(step["target"], ""))
        selector = self.variables.resolve(raw)
        if selector is not None and not isinstance(selector, str): raise FlowVariableError("定位器必须解析为字符串")
        return step, selector or ""

    def leaf_context(self, step):
        result = {**self.variables.context, "vars": self.variables.merged(), "inputs": self.variables.inputs, "error": self.variables.error}
        if step["action"] == "FILL_INPUT" and step.get("value") is not None:
            result["prompt"] = str(step["value"])
        return result

    def events(self, iterator, index):
        step = self.steps[index]
        self._record(step, "started")
        safe = step.get("retry_safe") is True or step["action"] in {"WAIT", "READONLY_HINT"}
        for frame in self.tries:
            frame["effect"] |= not safe
        error = None
        try:
            for chunk in iterator:
                self._check(transition=False)
                try:
                    payload = json.loads(str(chunk).removeprefix("data: ").strip())
                except (ValueError, TypeError): payload = None
                if isinstance(payload, dict) and payload.get("error"):
                    error = payload["error"]
                    continue  # A recovered failure must never leak an error SSE to the client.
                self.output_emitted = True
                for frame in self.tries: frame["output"] = True
                yield chunk
        finally:
            if hasattr(iterator, "close"):
                iterator.close()
        self._check(transition=False)
        if error:
            raise WorkflowError(str(error.get("message", "workflow_step_failed")) if isinstance(error, dict) else str(error))
        self._record(step, "completed")

    def recover(self, error, index):
        self._record(self.steps[index], "failed", error_type=type(error).__name__)
        self._check()
        if self.output_emitted:
            return None
        if isinstance(error, (FlowValidationError, FlowLimitError, WorkflowCancelledError)):
            return None
        message = str(error)
        if message.startswith(("stream_terminal_error:", "attachment", "file_paste_length_error", "workflow_cancelled")) or message in {"new_chat_transition_timeout", "send_unconfirmed", "arena_send_no_target", "stream_recovery_exhausted", "arena_direct_unexpected_battle_redirect"}:
            return None
        for frame in reversed(self.tries):
            policy = frame["policy"]
            allowed = policy.get("errors", ["*"])
            if frame["catching"] or ("*" not in allowed and type(error).__name__ not in allowed): continue
            if frame["output"]: return None
            if frame["effect"] and not policy.get("retry_side_effects", False): return None
            if frame["attempt"] < policy.get("attempts", 1) and (not frame["effect"] or policy.get("retry_side_effects", False)):
                frame["attempt"] += 1
                self.variables.globals, self.variables.locals = copy.deepcopy(frame["snapshot"])
                self.tries[:] = self.tries[:self.tries.index(frame) + 1]
                end = time.monotonic() + policy.get("delay", 0.3)
                while time.monotonic() < end:
                    self._check()
                    time.sleep(min(0.05, max(0, end - time.monotonic())))
                self._record(self.steps[frame["start"]], "retry", attempt=frame["attempt"])
                return frame["start"] + 1
            if frame["fallback"] is not None:
                frame["catching"] = True
                self.tries[:] = self.tries[:self.tries.index(frame) + 1]
                self.variables.error = {"type": type(error).__name__, "attempt": frame["attempt"]}
                self._record(self.steps[frame["start"]], "fallback")
                return frame["fallback"]
        return None


def capture_page_state(executor, spec, target, variables=None):
    """Read-only capture using ElementFinder, not interpolated JavaScript source."""
    if executor._check_cancelled(): raise WorkflowCancelledError("workflow_cancelled")
    source = spec.get("source", "text")
    if source == "url": return str(getattr(executor.tab, "url", ""))[:65536]
    selector = spec.get("selector") or executor._selectors.get(target, "")
    if variables is not None:
        selector = variables.resolve(selector)
    if not selector: raise WorkflowError("CAPTURE 需要 target 或 selector")
    element = executor.finder.find(selector, timeout=spec.get("timeout", 2))
    if source == "exists": return bool(element)
    if not element:
        if "default" in spec: return spec["default"]
        raise WorkflowError("CAPTURE 未找到页面元素")
    if source == "text": result = element.text
    elif source == "class": result = element.attr("class")
    elif source == "attribute": result = element.attr(spec["attribute"])
    elif source == "value": result = element.run_js("return this.value;")
    else: raise FlowValidationError("不支持的捕获类型")
    if result is None and "default" in spec: return spec["default"]
    if isinstance(result, str) and len(result) > 65536: raise FlowLimitError("捕获结果超过 65536 字符")
    return result
