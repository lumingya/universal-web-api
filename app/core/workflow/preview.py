"""Side-effect-free path preview. Uses the production compiler, never a browser."""
from app.core.workflow.flow_runtime import FlowProgram, FlowValidationError, validate_inputs, validate_workflow


def preview_workflow(data):
    workflow = data.get("workflow")
    validate_workflow(workflow)
    inputs = validate_inputs(data.get("workflow_variables", {}))
    captures = validate_inputs(data.get("capture_values", {}))

    def capture(spec, target):
        name = spec["name"]
        if name not in captures:
            # A missing fixture is not a page failure and must not trigger TRY.
            raise FlowValidationError(f"请提供页面状态样例：{name}（模拟不会访问网页）")
        return captures[name]

    program = FlowProgram(workflow, inputs=inputs,
                          context={"model": str(data.get("model") or ""), "prompt": str(data.get("prompt") or "")},
                          capture=capture, deadline_seconds=5)
    planned = []
    index = 0
    try:
        while index < len(program.steps):
            index = program.next_leaf(index)
            if index >= len(program.steps):
                break
            step, _ = program.prepare_leaf(index, {})
            planned.append({"path": step["_path"], "action": step["action"]})
            # Deliberately do not invoke events(): planned is not completed,
            # and no fictitious success/output/side-effect flags are recorded.
            program._record(step, "planned")
            index += 1
    except Exception as exc:
        return {"success": False, "mode": "simulation", "message": str(exc),
                "trace": program.trace, "planned_actions": planned}
    return {"success": True, "mode": "simulation",
            "message": "模拟完成 · 只计算分支路径，未操作网页；不验证网页动作、重试或兜底效果。",
            "trace": program.trace, "planned_actions": planned}
