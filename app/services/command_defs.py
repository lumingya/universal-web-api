"""
app/services/command_defs.py - Shared command definitions

Responsibilities:
- Default command payload
- Trigger/action type metadata
- Internal command-flow control exception
"""

import uuid
from typing import Any, Dict

TRIGGER_TYPES = {
    "request_count": "请求计数",
    "error_count": "错误计数",
    "idle_timeout": "空闲超时",
    "page_check": "页面检查，适合 Cloudflare 场景",
    "command_check": "命令检查",
    "command_triggered": "命令已触发",
    "command_result_match": "命令返回结果",
    "command_result_event": "命令结果事件",
    "network_request_error": "网络异常",
}

ACTION_TYPES = {
    "clear_cookies": "清除 Cookie",
    "refresh_page": "刷新页面",
    "new_chat": "新建对话",
    "run_js": "执行 JavaScript",
    "wait": "等待",
    "execute_preset": "切换预设",
    "execute_workflow": "执行工作流",
    "switch_preset": "切换预设",
    "navigate": "跳转 URL",
    "switch_proxy": "切换 Clash 代理",
    "send_webhook": "发送 Webhook / 通知",
    "send_napcat": "发送 NapCat QQ 消息",
    "execute_command_group": "执行命令组",
    "abort_task": "中断任务",
    "release_tab_lock": "解除标签页占用",
    "click_element": "点击元素",
    "click_coordinates": "点击坐标",
    "write_element": "写入元素",
    "read_element": "读取元素",
    "http_request": "页面内请求",
    "append_file": "追加到文件",
}


class CommandFlowAbort(Exception):
    """用于在动作链中提前中断后续步骤。"""
    pass


def _new_command_id() -> str:
    return f"cmd_{uuid.uuid4().hex[:8]}"


def get_default_command() -> Dict[str, Any]:
    """返回默认命令结构。"""
    return {
        "id": _new_command_id(),
        "name": "新命令",
        "enabled": True,
        "log_enabled": True,
        "log_level": "GLOBAL",
        "mode": "simple",
        "trigger": {
            "type": "request_count",
            "value": 10,
            "command_id": "",
            "action_ref": "",
            "match_rule": "equals",
            "expected_value": "",
            "match_mode": "keyword",
            "status_codes": "403,429,500,502,503,504",
            "abort_on_match": True,
            "command_ids": [],
            "listen_all_commands": False,
            "informative_only": True,
            "scope": "all",
            "domain": "",
            "tab_index": None,
            "priority": 2,
            "allow_during_workflow": True,
            "interrupt_policy": "auto",
            "interrupt_message": "",
        },
        "actions": [
            {"type": "clear_cookies"},
            {"type": "refresh_page"},
        ],
        "group_name": "",
        "script": "",
        "script_lang": "javascript",
        "last_triggered": None,
        "trigger_count": 0,
    }


__all__ = [
    'ACTION_TYPES',
    'CommandFlowAbort',
    'TRIGGER_TYPES',
    '_new_command_id',
    'get_default_command',
]
