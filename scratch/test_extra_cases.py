import sys
import os
import json
import traceback

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

mixin = BrowserPromptMixin()

print("--- Additional Edge Case Tests ---")

# Edge case A: Claude-style image in content
claude_img_content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}}]
res_claude = mixin._extract_text_from_content(claude_img_content)
print(f"A. Claude-style image: {repr(res_claude)}")

# Edge case B: Nested json string inside stringified array
nested_json_str = '[{"type": "text", "text": "[\\"nested string\\"]"}]'
res_nested = mixin._extract_text_from_content(nested_json_str)
print(f"B. Nested JSON string: {repr(res_nested)}")

# Edge case C: Tool choice with whitespace e.g. " required "
res_choice_ws = _describe_tool_choice("  required  ")
print(f"C. Tool choice with whitespace: {repr(res_choice_ws)}")

# Edge case D: Tool choice with dict containing type="function", name="my_fn"
res_choice_flat_dict = _describe_tool_choice({"type": "function", "name": "my_fn"})
print(f"D. Tool choice flat dict: {repr(res_choice_flat_dict)}")

# Edge case E: Assistant message with empty tool_calls list and empty content
prompt_empty_assistant = mixin._build_prompt_from_messages([{"role": "assistant", "content": "", "tool_calls": []}])
print(f"E. Empty assistant message: {repr(prompt_empty_assistant)}")

# Edge case F: Assistant message with tool_calls having None id and None name
prompt_none_id_name = mixin._build_prompt_from_messages([{"role": "assistant", "tool_calls": [{"id": None, "function": {"name": None, "arguments": None}}]}])
print(f"F. None id/name assistant tool_calls: {repr(prompt_none_id_name)}")

# Edge case G: Tool message with tool_call_id but no name
tool_no_name = [{"role": "tool", "tool_call_id": "call_123", "content": "result"}]
prompt_tool_no_name = mixin._build_prompt_from_messages(tool_no_name)
print(f"G. Tool msg no name (prompt.py): {repr(prompt_tool_no_name)}")

browser_msgs_tool_no_name = build_browser_messages_for_tools(tool_no_name, [], "auto")
print(f"G2. Tool msg no name (tool_calling_prompts.py): {repr(browser_msgs_tool_no_name[1])}")
