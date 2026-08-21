import sys
import os
import json
import traceback

# Add project root to sys.path
PROJECT_ROOT = r"C:\Users\QIU\Desktop\useful\projects\普遍反代\新测试版"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.core.browser.prompt import BrowserPromptMixin
from app.services.tool_calling_prompts import (
    normalize_tool_request,
    has_tool_calling_request,
    build_browser_messages_for_tools,
    summarize_messages_for_debug,
    _build_example_value_from_schema,
    _build_example_arguments_from_tool,
    _generate_tool_few_shot_examples,
    _render_xml_tool_call_example,
    _render_xml_parameters,
    _render_xml_value,
    _wrap_cdata,
    _escape_xml_text,
    _build_tool_system_prompt,
    _format_tool_result_message,
    _describe_tool_choice,
)

print("=== STARTING ADVERSARIAL TEST SUITE ===")
tests_passed = 0
tests_failed = 0
test_results = []

def run_test(name, fn, validator=None):
    global tests_passed, tests_failed
    try:
        res = fn()
        if validator:
            valid, msg = validator(res)
            if not valid:
                print(f"[FAIL_SEMANTIC] {name} -> {msg} | Result: {repr(res)[:100]}")
                tests_failed += 1
                test_results.append({"name": name, "status": "FAIL_SEMANTIC", "error": msg, "res": repr(res)[:200]})
                return
        print(f"[PASS] {name} -> {repr(res)[:100]}")
        tests_passed += 1
        test_results.append({"name": name, "status": "PASS", "res": repr(res)[:200]})
    except Exception as e:
        print(f"[FAIL_EXCEPTION] {name} -> {type(e).__name__}: {e}")
        traceback.print_exc()
        tests_failed += 1
        test_results.append({"name": name, "status": "FAIL_EXCEPTION", "error": f"{type(e).__name__}: {e}"})

mixin = BrowserPromptMixin()

# -------------------------------------------------------------
# Category 1: prompt.py BrowserPromptMixin._build_prompt_from_messages
# -------------------------------------------------------------

# Test 1.1: messages is None
run_test("1.1 prompt.py _build_prompt_from_messages(None)", lambda: mixin._build_prompt_from_messages(None))

# Test 1.2: messages is non-iterable scalar
run_test("1.2 prompt.py _build_prompt_from_messages(123)", lambda: mixin._build_prompt_from_messages(123))

# Test 1.3: messages has elements that are not dicts
run_test("1.3 prompt.py _build_prompt_from_messages([None, 'str', 123])", lambda: mixin._build_prompt_from_messages([None, "str", 123]))

# Test 1.4: assistant message with tool_calls having unserializable arguments (set, bytes, custom class)
class CustomObj:
    def __repr__(self):
        return "<CustomObj>"

run_test(
    "1.4 prompt.py _build_prompt_from_messages with unserializable tool_call arguments",
    lambda: mixin._build_prompt_from_messages([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "test", "arguments": {"s": {1, 2, 3}, "obj": CustomObj()}}}]}
    ])
)

# Test 1.5: assistant message with circular reference in arguments
circ = {}
circ["self"] = circ
run_test(
    "1.5 prompt.py _build_prompt_from_messages with circular tool_call arguments",
    lambda: mixin._build_prompt_from_messages([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "test", "arguments": circ}}]}
    ])
)

# Test 1.6: assistant message with broken JSON string in arguments
run_test(
    "1.6 prompt.py _build_prompt_from_messages with malformed JSON string in arguments",
    lambda: mixin._build_prompt_from_messages([
        {"role": "assistant", "content": "calling tool", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "test", "arguments": '{"broken: unclosed'}}]}
    ])
)

# -------------------------------------------------------------
# Category 2: prompt.py BrowserPromptMixin._extract_text_from_content
# -------------------------------------------------------------

# Test 2.1: content is a dict (e.g. {"type": "text", "text": "hello"} or {"error": "bad"})
run_test(
    "2.1 prompt.py _extract_text_from_content(single dict)",
    lambda: mixin._extract_text_from_content({"type": "text", "text": "hello world"}),
    lambda r: (r == "hello world" or "hello world" in r, f"Expected 'hello world' but got: '{r}'")
)

# Test 2.2: content is an integer or bool
run_test(
    "2.2 prompt.py _extract_text_from_content(int 12345)",
    lambda: mixin._extract_text_from_content(12345),
    lambda r: (r == "12345", f"Expected '12345' but got: '{r}'")
)

# Test 2.3: content is string with leading whitespace before data:image
run_test(
    "2.3 prompt.py _extract_text_from_content(leading space base64)",
    lambda: mixin._extract_text_from_content("  data:image/png;base64," + "A" * 1200),
    lambda r: ("[图片" in r, f"Expected placeholder for base64 with leading whitespace, got: '{r[:50]}'")
)

# Test 2.4: content is multi-modal list containing items without 'type' but with 'text'
run_test(
    "2.4 prompt.py _extract_text_from_content(item with text but no type)",
    lambda: mixin._extract_text_from_content([{"text": "my message"}]),
    lambda r: ("my message" in r, f"Expected 'my message' extracted, got: '{r}'")
)

# Test 2.5: content is multi-modal list containing non-dict items (e.g. None, int, str)
run_test(
    "2.5 prompt.py _extract_text_from_content(mixed list [None, 123, dict])",
    lambda: mixin._extract_text_from_content([None, 123, {"type": "text", "text": "valid"}])
)

# Test 2.6: content is a generator/iterator
def sample_gen():
    yield {"type": "text", "text": "part1"}
    yield {"type": "text", "text": "part2"}

run_test(
    "2.6 prompt.py _extract_text_from_content(generator)",
    lambda: mixin._extract_text_from_content(sample_gen())
)

# Test 2.7: string with malicious ast.literal_eval payload or huge nested brackets
run_test(
    "2.7 prompt.py _extract_text_from_content(deeply nested brackets string)",
    lambda: mixin._extract_text_from_content("[" * 500 + "]" * 500)
)

# -------------------------------------------------------------
# Category 3: tool_calling_prompts.py _build_example_value_from_schema & few-shot
# -------------------------------------------------------------

# Test 3.1: schema has required=None
run_test(
    "3.1 tool_calling_prompts _build_example_value_from_schema(required=None)",
    lambda: _build_example_value_from_schema({"type": "object", "required": None, "properties": {"a": {"type": "string"}}})
)

# Test 3.2: schema has required as int or string
run_test(
    "3.2 tool_calling_prompts _build_example_value_from_schema(required='not_a_list')",
    lambda: _build_example_value_from_schema({"type": "object", "required": "not_a_list", "properties": {"a": {"type": "string"}}})
)

# Test 3.3: schema has properties=None
run_test(
    "3.3 tool_calling_prompts _build_example_value_from_schema(properties=None)",
    lambda: _build_example_value_from_schema({"type": "object", "properties": None})
)

# Test 3.4: schema has items=None
run_test(
    "3.4 tool_calling_prompts _build_example_value_from_schema(items=None)",
    lambda: _build_example_value_from_schema({"type": "array", "items": None})
)

# Test 3.5: schema has enum=None or enum=[]
run_test(
    "3.5 tool_calling_prompts _build_example_value_from_schema(enum=None)",
    lambda: _build_example_value_from_schema({"enum": None})
)

# Test 3.6: schema has anyOf=None or allOf=None
run_test(
    "3.6 tool_calling_prompts _build_example_value_from_schema(anyOf=None)",
    lambda: _build_example_value_from_schema({"anyOf": None})
)

# Test 3.7: schema with recursive reference
recursive_schema = {"type": "object", "properties": {}}
recursive_schema["properties"]["child"] = recursive_schema
run_test(
    "3.7 tool_calling_prompts _build_example_value_from_schema(recursive schema)",
    lambda: _build_example_value_from_schema(recursive_schema)
)

# Test 3.8: full _build_tool_system_prompt with schema with required=None
run_test(
    "3.8 tool_calling_prompts _build_tool_system_prompt with required=None",
    lambda: _build_tool_system_prompt([{"type": "function", "function": {"name": "test_fn", "parameters": {"type": "object", "required": None, "properties": {"x": {"type": "string"}}}}}], "auto", True)
)

# -------------------------------------------------------------
# Category 4: tool_calling_prompts.py build_browser_messages_for_tools
# -------------------------------------------------------------

# Test 4.1: messages is None
run_test(
    "4.1 tool_calling_prompts build_browser_messages_for_tools(messages=None)",
    lambda: build_browser_messages_for_tools(None, [], "auto")
)

# Test 4.2: messages has non-dict items
run_test(
    "4.2 tool_calling_prompts build_browser_messages_for_tools([None, 123, 'hello'])",
    lambda: build_browser_messages_for_tools([None, 123, "hello"], [], "auto")
)

# Test 4.3: assistant message with flat tool_calls (name & arguments at top level)
run_test(
    "4.3 tool_calling_prompts build_browser_messages_for_tools(flat tool_calls)",
    lambda: build_browser_messages_for_tools([
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "name": "web_search", "arguments": '{"query": "antigravity"}'}]}
    ], [], "auto"),
    lambda r: ("web_search" in r[1]["content"] and "antigravity" in r[1]["content"], f"Expected 'web_search' and 'antigravity' in content but got: {r[1]['content']}")
)

# Test 4.4: assistant message with un-serializable arguments (set, bytes, custom class)
run_test(
    "4.4 tool_calling_prompts build_browser_messages_for_tools(unserializable tool_call arguments)",
    lambda: build_browser_messages_for_tools([
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": {"s": {1, 2, 3}, "obj": CustomObj()}}}]}
    ], [], "auto")
)

# Test 4.5: assistant message with circular reference in arguments
run_test(
    "4.5 tool_calling_prompts build_browser_messages_for_tools(circular tool_call arguments)",
    lambda: build_browser_messages_for_tools([
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": circ}}]}
    ], [], "auto")
)

# Test 4.6: tool role message with name=None, tool_call_id=None, content=None
run_test(
    "4.6 tool_calling_prompts build_browser_messages_for_tools(tool role with all None)",
    lambda: build_browser_messages_for_tools([
        {"role": "tool", "name": None, "tool_call_id": None, "content": None}
    ], [], "auto")
)

# Test 4.7: legacy function role message with name="legacy_fn", content="some output"
run_test(
    "4.7 tool_calling_prompts build_browser_messages_for_tools(legacy function role)",
    lambda: build_browser_messages_for_tools([
        {"role": "function", "name": "legacy_fn", "content": "some output"}
    ], [], "auto")
)

# -------------------------------------------------------------
# Category 5: tool_calling_prompts.py XML Rendering Edge Cases
# -------------------------------------------------------------

# Test 5.1: XML rendering with invalid XML tag names in parameter keys (spaces, <, >, numbers, quotes)
run_test(
    "5.1 tool_calling_prompts _render_xml_parameters with special tag names",
    lambda: _render_xml_parameters({"tag with spaces": "val1", "tag<with>brackets": "val2", "123numeric": "val3", "": "val4"}, indent="  ")
)

# Test 5.2: XML value rendering with CDATA containing ]]> and nested CDATA
run_test(
    "5.2 tool_calling_prompts _render_xml_value CDATA edge case with ']]>' and '<![CDATA['",
    lambda: _render_xml_value("hello ]]> world <![CDATA[nested]]> test", indent="  ")
)

# Test 5.3: XML value rendering with None, bool, numbers, empty dict, empty list
run_test(
    "5.3 tool_calling_prompts _render_xml_value various types",
    lambda: _render_xml_value({"a": None, "b": True, "c": False, "d": 0, "e": 3.14, "f": {}, "g": []}, indent="  ")
)

# -------------------------------------------------------------
# Category 6: tool_calling_prompts.py normalize_tool_request & tool_choice
# -------------------------------------------------------------

# Test 6.1: normalize_tool_request with tools=[{"type": "function", "function": {"name": "test", "parameters": None}}]
run_test(
    "6.1 tool_calling_prompts normalize_tool_request(parameters=None)",
    lambda: normalize_tool_request(tools=[{"type": "function", "function": {"name": "test", "parameters": None}}])
)

# Test 6.2: normalize_tool_request with functions=[{"name": "test", "parameters": None}]
run_test(
    "6.2 tool_calling_prompts normalize_tool_request(functions with parameters=None)",
    lambda: normalize_tool_request(functions=[{"name": "test", "parameters": None}])
)

# Test 6.3: normalize_tool_request with tool_choice in uppercase 'REQUIRED', 'NONE', 'AUTO'
run_test(
    "6.3 tool_calling_prompts _describe_tool_choice uppercase 'REQUIRED'",
    lambda: _describe_tool_choice("REQUIRED"),
    lambda r: ("must call at least one tool" in r.lower() or "must call" in r.lower(), f"Expected required instruction, got: '{r}'")
)

# Test 6.4: normalize_tool_request with tool_choice in uppercase 'NONE'
run_test(
    "6.4 tool_calling_prompts _describe_tool_choice uppercase 'NONE'",
    lambda: _describe_tool_choice("NONE"),
    lambda r: ("do not call" in r.lower(), f"Expected do not call instruction, got: '{r}'")
)

# Test 6.5: _describe_tool_choice with flat tool_choice={"name": "my_tool"}
run_test(
    "6.5 tool_calling_prompts _describe_tool_choice with flat dict {'name': 'my_tool'}",
    lambda: _describe_tool_choice({"name": "my_tool"}),
    lambda r: ("my_tool" in r, f"Expected 'my_tool' in instruction, got: '{r}'")
)

# -------------------------------------------------------------
# Category 7: tool_calling_prompts.py summarize_messages_for_debug
# -------------------------------------------------------------

# Test 7.1: summarize_messages_for_debug with None
run_test(
    "7.1 tool_calling_prompts summarize_messages_for_debug(None)",
    lambda: summarize_messages_for_debug(None)
)

# Test 7.2: summarize_messages_for_debug with malformed list
run_test(
    "7.2 tool_calling_prompts summarize_messages_for_debug([None, 123, 'str', {'role': None, 'content': None}])",
    lambda: summarize_messages_for_debug([None, 123, "str", {"role": None, "content": None}])
)

print(f"\n==================================================")
print(f"=== SUMMARY: {tests_passed} PASSED, {tests_failed} FAILED ===")
print(f"==================================================")
