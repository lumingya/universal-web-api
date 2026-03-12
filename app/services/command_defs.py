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
    "request_count": "对话次数达到阈值",
    "error_count": "连续错误次数达到阈值",
    "idle_timeout": "标签页空闲超过指定时间（秒）",
    "page_check": "页面出现指定内容（如 Cloudflare 验证）",
    "command_triggered": "当指定命令触发后执行",
    "command_result_match": "命令执行结果匹配",
    "network_request_error": "网络请求异常拦截",
}

ACTION_TYPES = {
    "clear_cookies": "清除当前标签页的 Cookie",
    "refresh_page": "刷新页面",
    "new_chat": "点击新建对话按钮",
    "run_js": "在页面中执行 JavaScript",
    "wait": "等待指定秒数",
    "execute_preset": "切换预设",
    "execute_workflow": "执行工作流",
    "switch_preset": "切换标签页预设",
    "navigate": "导航到指定 URL",
    "switch_proxy": "切换代理节点（Clash）",
    "send_webhook": "发送 Webhook / 外部请求",
    "execute_command_group": "执行命令组",
    "abort_task": "中断当前任务",
    "release_tab_lock": "解除当前标签页占用",
}


class CommandFlowAbort(Exception):
    """用于中断当前命令后续动作的内部控制异常。"""
    pass


def _new_command_id() -> str:
    return f"cmd_{uuid.uuid4().hex[:8]}"


def get_default_command() -> Dict[str, Any]:
    """获取默认命令结构"""
    return {
        "id": _new_command_id(),
        "name": "新命令",
        "enabled": True,
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
            "scope": "all",
            "domain": "",
            "tab_index": None,
            "priority": 2,
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
