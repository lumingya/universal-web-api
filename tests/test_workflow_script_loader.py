"""
tests/test_workflow_script_loader.py - ScriptLoader 单元测试

覆盖范围：
1. 脚本目标识别 (is_script_target 严格单行内联 JS 排除与合法路径识别)
2. 路径安全解析与沙箱目录防遍历 (resolve_script_path, path traversal defense, scripts:/// 归一化)
3. mtime 缓存与热重载 (load_script_content, mtime cache)
4. 宏变量插值与特殊字符安全转义 (interpolate_macros)
5. 异步闭包包装与 JSON 扩展类型容错 (wrap_script_closure, default=str)
6. JSON 字符串入参智能反序列化 (resolve_and_load)
7. JSDoc 及单行注释描述增强解析 (extract_script_description)
8. 可用脚本文件扫描 (scan_available_scripts)
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from app.core.config import WorkflowError
from app.core.workflow.script_loader import ScriptLoader, script_loader


class TestWorkflowScriptLoader(unittest.TestCase):
    """ScriptLoader 单元测试套件"""

    def setUp(self):
        self.loader = ScriptLoader()
        self.loader.clear_cache()

    def tearDown(self):
        self.loader.clear_cache()

    def test_is_script_target_strict_js_rejection(self):
        """测试脚本目标识别：严格排除单行内联 JS 代码"""
        # 有效脚本文件路径 / URI
        self.assertTrue(self.loader.is_script_target("file://custom_scripts/foo.js"))
        self.assertTrue(self.loader.is_script_target("scripts://bar.js"))
        self.assertTrue(self.loader.is_script_target("scripts:///bar.js"))
        self.assertTrue(self.loader.is_script_target("custom_scripts/interceptor.js"))
        self.assertTrue(self.loader.is_script_target("scripts/test.js"))
        self.assertTrue(self.loader.is_script_target("custom_scripts/sub/dir/test.js"))
        self.assertTrue(self.loader.is_script_target("foo_bar.js"))

        # 必须排除单行内联 JS 语法（含关键字、符号、函数调用、分号等）
        self.assertFalse(self.loader.is_script_target('console.log("hello.js")'))
        self.assertFalse(self.loader.is_script_target('return window.location.href.endsWith(".js")'))
        self.assertFalse(self.loader.is_script_target("var a = 1;"))
        self.assertFalse(self.loader.is_script_target("const foo = 'bar.js';"))
        self.assertFalse(self.loader.is_script_target("let x = 123;"))
        self.assertFalse(self.loader.is_script_target("function test() { return 1; }"))
        self.assertFalse(self.loader.is_script_target("document.title = 'test.js'"))
        self.assertFalse(self.loader.is_script_target("return document.title;"))
        self.assertFalse(self.loader.is_script_target("1 + 1 == 2"))
        self.assertFalse(self.loader.is_script_target(""))
        self.assertFalse(self.loader.is_script_target(None))

    def test_normalize_target_path_str_protocols(self):
        """测试 file://, scripts://, scripts:/// 协议前缀规范化"""
        self.assertEqual(
            self.loader._normalize_target_path_str("file://custom_scripts/foo.js"),
            "custom_scripts/foo.js"
        )
        self.assertEqual(
            self.loader._normalize_target_path_str("scripts://bar.js"),
            os.path.join("scripts", "bar.js")
        )
        self.assertEqual(
            self.loader._normalize_target_path_str("scripts:///sub/bar.js"),
            os.path.join("scripts", "sub/bar.js")
        )

    def test_resolve_script_path_valid(self):
        """测试正常脚本路径解析"""
        example_path = "custom_scripts/examples/arena_payload_interceptor.js"
        resolved = self.loader.resolve_script_path(example_path)
        self.assertTrue(resolved.is_file())
        self.assertEqual(resolved.name, "arena_payload_interceptor.js")

        # 支持 file:// 前缀
        resolved_uri = self.loader.resolve_script_path(f"file://{example_path}")
        self.assertEqual(resolved_uri, resolved)

    def test_resolve_script_path_security_traversal(self):
        """测试跨目录越界与路径遍历拦截"""
        # 尝试使用 .. 越界逃逸
        with self.assertRaises(WorkflowError) as cm:
            self.loader.resolve_script_path("custom_scripts/../../app/main.py")
        self.assertIn("js_script_path_forbidden", str(cm.exception))

        with self.assertRaises(WorkflowError) as cm:
            self.loader.resolve_script_path("../main.py")
        self.assertIn("js_script_path_forbidden", str(cm.exception))

        # 非法后缀拦截
        with self.assertRaises(WorkflowError) as cm:
            self.loader.resolve_script_path("custom_scripts/README.md")
        self.assertIn("js_script_invalid_extension", str(cm.exception))

        # 不存在的文件拦截
        with self.assertRaises(WorkflowError) as cm:
            self.loader.resolve_script_path("custom_scripts/non_existent_file_99999.js")
        self.assertIn("js_script_not_found", str(cm.exception))

    def test_load_script_content_caching_and_hot_reload(self):
        """测试 mtime 内存缓存与热重载"""
        custom_dir = self.loader.base_dir / "custom_scripts"
        custom_dir.mkdir(parents=True, exist_ok=True)
        temp_js = custom_dir / "_temp_test_cache.js"

        try:
            temp_js.write_text("console.log('v1');", encoding="utf-8")
            content1 = self.loader.load_script_content(temp_js)
            self.assertEqual(content1, "console.log('v1');")

            # 再次读取应命中缓存
            content_cached = self.loader.load_script_content(temp_js)
            self.assertEqual(content_cached, "console.log('v1');")

            # 修改文件内容并更新 mtime
            time.sleep(0.05)
            temp_js.write_text("console.log('v2');", encoding="utf-8")
            future_mtime = time.time() + 1
            os.utime(temp_js, (future_mtime, future_mtime))

            content2 = self.loader.load_script_content(temp_js)
            self.assertEqual(content2, "console.log('v2');")
        finally:
            if temp_js.exists():
                temp_js.unlink()

    def test_interpolate_macros_and_special_character_escaping(self):
        """测试宏变量插值以及特殊字符（换行、双引号、反斜杠）在代码中的安全转义"""
        context = {
            "model": "gpt-4o-mini",
            "prompt": '你好\n"世界" \\ 特殊字符',
            "session_id": "session-abc-123",
            "stream": True,
            "extra": {
                "sub_key": "sub_value"
            }
        }

        # 1. 纯数据字典宏插值
        tmpl_dict = {
            "prompt": "{{context.prompt}}",
            "model": "{{context.model}}"
        }
        res_dict = self.loader.interpolate_macros(tmpl_dict, context, is_code_template=False)
        self.assertEqual(res_dict["prompt"], '你好\n"世界" \\ 特殊字符')
        self.assertEqual(res_dict["model"], "gpt-4o-mini")

        # 2. 代码模板插值：特殊字符必须转义，防止破坏 JS 语法
        code_tmpl = 'const prompt = "{{context.prompt}}";'
        interpolated_code = self.loader.interpolate_macros(code_tmpl, context, is_code_template=True)
        self.assertIn('\\"世界\\"', interpolated_code)
        self.assertIn('\\n', interpolated_code)
        self.assertNotIn('\n"世界"', interpolated_code)

        # 3. 嵌套点分属性插值
        tmpl_nested = "sub: {{context.extra.sub_key}}"
        self.assertEqual(
            self.loader.interpolate_macros(tmpl_nested, context),
            "sub: sub_value"
        )

        # 4. 未定义变量安全回退为空字符串
        tmpl_missing = "missing: {{context.not_exist}}"
        self.assertEqual(
            self.loader.interpolate_macros(tmpl_missing, context),
            "missing: "
        )

    def test_wrap_script_closure_with_custom_types(self):
        """测试异步闭包代码包装及 default=str 扩展类型容错"""
        code = "return __ARGS__.foo + __CONTEXT__.model;"
        context = {"model": "claude-3-sonnet", "custom_fn": max}
        args = {"foo": "hello_"}

        wrapped = self.loader.wrap_script_closure(code, context=context, args=args)
        self.assertTrue(wrapped.startswith("return (async function(__CONTEXT__, __ARGS__) {"))
        self.assertIn('"model": "claude-3-sonnet"', wrapped)
        self.assertIn('"foo": "hello_"', wrapped)

    def test_smart_json_string_args_deserialization(self):
        """测试 JSON 字符串入参自动反序列化为结构化对象"""
        context = {"model": "gemini-1.5-pro"}
        json_args = '{"target_endpoint": "/api/chat", "override_model": "{{context.model}}"}'

        # 执行 resolve_and_load
        script_code = self.loader.resolve_and_load(
            script_target="custom_scripts/examples/arena_payload_interceptor.js",
            code_or_args=json_args,
            context=context,
        )

        # 验证闭包接收到的是结构化对象而不是字符串字面量
        self.assertTrue(script_code.startswith("return (async function(__CONTEXT__, __ARGS__) {"))
        self.assertIn('({"model": "gemini-1.5-pro"}, {"target_endpoint": "/api/chat", "override_model": "gemini-1.5-pro"})', script_code)

    def test_extract_script_description_enhanced(self):
        """测试提取单行注释时的有效注释寻找逻辑（过滤 eslint/注释头）"""
        # @description
        sample1 = "/**\n * @description 通用请求拦截器\n * @author uwa\n */\n(function(){})();"
        self.assertEqual(self.loader.extract_script_description(sample1), "通用请求拦截器")

        # 包含 eslint 和注释符的单行注释
        sample2 = "// eslint-disable-next-line\n// @ts-check\n// 这是真实的业务脚本描述\nconsole.log(1);"
        self.assertEqual(self.loader.extract_script_description(sample2), "这是真实的业务脚本描述")

    def test_scan_available_scripts(self):
        """测试脚本自动扫描与元数据提取"""
        scripts = self.loader.scan_available_scripts()
        self.assertIsInstance(scripts, list)

        # 确认示例脚本被扫描到
        example_entry = next((s for s in scripts if s["name"] == "arena_payload_interceptor.js"), None)
        self.assertIsNotNone(example_entry)
        self.assertEqual(example_entry["category"], "custom")
        self.assertEqual(example_entry["path"], "custom_scripts/examples/arena_payload_interceptor.js")
        self.assertEqual(example_entry["uri"], "file://custom_scripts/examples/arena_payload_interceptor.js")
        self.assertIn("通用 Arena 请求拦截", example_entry["description"])
        self.assertGreater(example_entry["mtime"], 0)

    def test_fail_fast_on_invalid_non_empty_target(self):
        """测试非空 target 无法解析时必须 Fail-Fast 抛出异常，绝不静默穿透为内联 JS"""
        context = {"model": "test-model"}
        # 传入一个不存在的文件名与 JSON 参数
        with self.assertRaises(WorkflowError) as cm:
            self.loader.resolve_and_load(
                script_target="custom_scripts/definitely_not_exist_file.js",
                code_or_args='{"target_endpoint": "/api"}',
                context=context,
            )
        self.assertIn("js_script_not_found", str(cm.exception))

    def test_is_script_target_with_spaces(self):
        """测试包含合法空格的文件路径被正确识别为脚本文件"""
        self.assertTrue(self.loader.is_script_target("custom_scripts/my custom hook.js"))
        self.assertTrue(self.loader.is_script_target("scripts/sub dir/my hook.js"))
        self.assertTrue(self.loader.is_script_target("file://custom_scripts/space file.js"))

    def test_arena_direct_guard_ignores_js_exec_target(self):
        """测试 arena_direct_guard 忽略 JS_EXEC 的 target，避免脚本名含 new_chat 时误判"""
        from app.core.workflow.arena_direct_guard import workflow_has_new_chat_step

        wf_with_js_hook = [
            {"action": "CLICK", "target": "input_box", "value": None},
            {"action": "JS_EXEC", "target": "custom_scripts/new_chat_interceptor.js", "value": {}},
            {"action": "CLICK", "target": "send_btn", "value": None},
        ]
        # 即使 JS_EXEC 的 target 包含 'new_chat'，也不应被判定为包含新对话步骤
        self.assertFalse(workflow_has_new_chat_step(wf_with_js_hook))

        wf_with_real_new_chat = [
            {"action": "CLICK", "target": "new_chat_btn", "value": None},
        ]
        self.assertTrue(workflow_has_new_chat_step(wf_with_real_new_chat))


if __name__ == "__main__":
    unittest.main()

