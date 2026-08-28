"""
tests/test_workflow_js_exec_integration.py - 工作流 JS_EXEC 步骤与前后端集成测试

覆盖范围：
1. WorkflowExecutor 执行传统内联 JS 步骤
2. WorkflowExecutor 执行外部脚本文件步骤（含 __CONTEXT__ 与 __ARGS__ 闭包注入）
3. 执行过程中的宏变量模板插值与参数传递
4. 异常捕获与 WorkflowError 包装（脚本未找到、越界、JS 执行异常、空脚本）
5. optional=True 容错支持（缺失文件、空代码、执行异常时不抛错）
6. 后端 API 接口逻辑（_scan_workflow_scripts 与 _load_workflow_script_content）
"""

import unittest
from typing import Any, Dict, List
from fastapi import HTTPException

from app.api.config_workflow_support import (
    _scan_workflow_scripts,
    _load_workflow_script_content,
)
from app.core.config import WorkflowError
from app.core.workflow.executor import WorkflowExecutor


class _MockTab:
    """用于测试 JS 执行的模拟 DrissionPage Tab"""

    def __init__(self):
        self.executed_scripts: List[str] = []
        self.run_js_result: Any = "mock_result"
        self.should_fail: bool = False

    def run_js(self, script: str, *args, **kwargs) -> Any:
        self.executed_scripts.append(script)
        if self.should_fail and "bad_syntax_here" in script:
            raise RuntimeError("JavaScript execution failed: syntax error")
        return self.run_js_result


class TestWorkflowJsExecIntegration(unittest.TestCase):
    """JS_EXEC 工作流步骤集成测试"""

    def setUp(self):
        self.mock_tab = _MockTab()
        self.executor = WorkflowExecutor(tab=self.mock_tab)

    def _run_step(
        self,
        action: str,
        selector: str = "",
        target_key: str = "",
        value: Any = None,
        context: Dict = None,
        optional: bool = False,
    ):
        """辅助方法：消费 execute_step 生成器"""
        return list(self.executor.execute_step(
            action=action,
            selector=selector,
            target_key=target_key,
            value=value,
            optional=optional,
            context=context,
        ))

    def test_execute_inline_javascript(self):
        """测试执行传统内联 JS 字符串"""
        context = {"model": "test-model"}
        self._run_step(
            action="JS_EXEC",
            selector="",
            target_key="",
            value="return document.title;",
            context=context,
        )

        self.assertTrue(
            any(s == "return document.title;" for s in self.mock_tab.executed_scripts),
            f"Expected 'return document.title;' in executed scripts: {self.mock_tab.executed_scripts}"
        )

    def test_execute_inline_javascript_with_macro(self):
        """测试执行带有宏变量插值的内联 JS 字符串"""
        context = {"model": "gpt-4o"}
        self._run_step(
            action="JS_EXEC",
            selector="",
            target_key="",
            value="console.log('Active model: {{context.model}}');",
            context=context,
        )

        expected = "console.log('Active model: gpt-4o');"
        self.assertTrue(
            any(s == expected for s in self.mock_tab.executed_scripts),
            f"Expected {expected!r} in executed scripts: {self.mock_tab.executed_scripts}"
        )

    def test_execute_external_script_with_args(self):
        """测试执行外部脚本文件（传入 target 与 args）"""
        context = {"model": "claude-3-5-sonnet", "session_id": "sess-999"}
        args = {
            "target_endpoint": "/api/chat",
            "override_model": "{{context.model}}"
        }

        self._run_step(
            action="JS_EXEC",
            selector="",
            target_key="custom_scripts/examples/arena_payload_interceptor.js",
            value=args,
            context=context,
        )

        matching_scripts = [s for s in self.mock_tab.executed_scripts if "[ArenaInterceptor]" in s]
        self.assertTrue(len(matching_scripts) > 0, "No script containing [ArenaInterceptor] was executed")
        executed = matching_scripts[0]

        # 验证闭包包装与入参注入
        self.assertTrue(executed.startswith("return (async function(__CONTEXT__, __ARGS__) {"))
        self.assertIn('"override_model": "claude-3-5-sonnet"', executed)
        self.assertIn('"session_id": "sess-999"', executed)
        self.assertIn("[ArenaInterceptor]", executed)

    def test_empty_js_raises_workflow_error(self):
        """测试空 JS 代码在 optional=False 时抛出 WorkflowError"""
        with self.assertRaises(WorkflowError) as cm:
            self._run_step(
                action="JS_EXEC",
                selector="",
                target_key="",
                value="",
                optional=False,
            )
        self.assertIn("js_exec_empty", str(cm.exception))

    def test_missing_script_file_raises_workflow_error(self):
        """测试引用的脚本文件不存在时抛出 WorkflowError"""
        with self.assertRaises(WorkflowError) as cm:
            self._run_step(
                action="JS_EXEC",
                selector="",
                target_key="custom_scripts/not_found_test.js",
                value={},
                optional=False,
            )
        self.assertIn("js_script_not_found", str(cm.exception))

    def test_forbidden_script_path_raises_workflow_error(self):
        """测试越界路径遍历抛出 WorkflowError"""
        with self.assertRaises(WorkflowError) as cm:
            self._run_step(
                action="JS_EXEC",
                selector="",
                target_key="../../passwords.js",
                value={},
                optional=False,
            )
        self.assertIn("js_script_path_forbidden", str(cm.exception))

    def test_js_runtime_error_wrapped_in_workflow_error(self):
        """测试页面内 JS 执行异常被捕获并包装为 WorkflowError"""
        self.mock_tab.should_fail = True
        with self.assertRaises(WorkflowError) as cm:
            self._run_step(
                action="JS_EXEC",
                selector="",
                target_key="",
                value="bad_syntax_here",
                optional=False,
            )
        self.assertIn("js_exec_failed", str(cm.exception))

    def test_optional_js_exec_tolerates_errors(self):
        """测试 optional=True 时，即使脚本缺失或执行失败也不中断工作流"""
        # 1. 缺失文件时安全跳过
        self._run_step(
            action="JS_EXEC",
            selector="",
            target_key="custom_scripts/missing_opt.js",
            value={},
            optional=True,
        )

        # 2. 空代码时安全跳过
        self._run_step(
            action="JS_EXEC",
            selector="",
            target_key="",
            value="",
            optional=True,
        )

        # 3. 运行期 JS 报错时安全跳过
        self.mock_tab.should_fail = True
        self._run_step(
            action="JS_EXEC",
            selector="",
            target_key="",
            value="bad_syntax_here",
            optional=True,
        )

    def test_api_scan_workflow_scripts(self):
        """测试扫描 API 返回格式"""
        res = _scan_workflow_scripts()
        self.assertIn("scripts", res)
        scripts = res["scripts"]
        self.assertIsInstance(scripts, list)
        self.assertTrue(any(s["name"] == "arena_payload_interceptor.js" for s in scripts))

    def test_api_load_workflow_script_content(self):
        """测试读取脚本内容与元数据 API"""
        target = "custom_scripts/examples/arena_payload_interceptor.js"
        res = _load_workflow_script_content(target)
        self.assertEqual(res["name"], "arena_payload_interceptor.js")
        self.assertIn("content", res)
        self.assertIn("window.__ARENA_PAYLOAD_INTERCEPTOR_INSTALLED__", res["content"])
        self.assertIn("通用 Arena 请求拦截", res["description"])

    def test_api_load_forbidden_script_content(self):
        """测试读取非法路径脚本抛出 400 异常"""
        with self.assertRaises(HTTPException) as cm:
            _load_workflow_script_content("../main.py")
        self.assertEqual(cm.exception.status_code, 400)

    def test_arena_payload_interceptor_model_and_field_rules(self):
        """模型 UUID 由拦截器解析，页面原生 modality 保持不变。"""
        target = "custom_scripts/examples/arena_payload_interceptor.js"
        res = _load_workflow_script_content(target)
        content = res["content"]

        # 1. 确保 kimi-k3 UUID 准确映射
        self.assertIn("'kimi-k3': '019faec3-b3a8-7871-9059-8eea66f9f279'", content)

        # 2. 模态属于页面/预设上下文，拦截器不得按模型名猜测或覆盖它。
        self.assertNotIn("targetModality", content)
        self.assertNotIn("parsed.modality =", content)

        # 3. 确保 rewritePayloadObject 仅精准替换模型字段，不篡改控制字段
        self.assertIn("rewritePayloadObject", content)
        self.assertIn("modelAId", content)
        self.assertIn("modelBId", content)
        self.assertIn("modelId", content)
        self.assertNotIn("parsed.mode =", content)
        self.assertNotIn("parsed.id =", content)
        self.assertNotIn("parsed.userMessageId =", content)


if __name__ == "__main__":
    unittest.main()
