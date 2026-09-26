"""R2-6：API 进程与 worker 之间的协议约定。

执行接口的响应是 NDJSON：每行一个 JSON 对象——
``{"chunk": "<浏览器层产出的一段 SSE 文本>"}``、``{"hb": 1}``（心跳，约每秒一次，
让 API 侧在长时间没有数据时也能及时检查停止请求）、``{"error": "..."}``。
停止信号通过断开连接传递：API 侧关闭响应，worker 检测到断开后置位 stop_checker。
"""

from __future__ import annotations

EXECUTE_METHODS = frozenset({
    "execute_workflow",
    "execute_workflow_for_tab_index",
    "execute_workflow_for_route_domain",
    "execute_workflow_for_route_group",
    "execute_workflow_for_exact_url",
})

# 执行参数中允许跨进程传递的键（stop_checker 为回调，改由断开连接传递）
EXECUTE_KWARGS = frozenset({
    "messages", "stream", "task_id", "preset_name", "workflow_priority", "allow_media_postprocess",
    "requested_model", "workflow_variables", "allocation_mode", "resolved_tab_index",
    "tab_index", "route_domain", "group_id", "exact_url",
})

# 标签页池中参数与返回值均可 JSON 化、允许远程调用的方法
TAB_POOL_METHODS = frozenset({
    "get_tabs_with_index", "set_tab_model_name", "apply_runtime_config", "terminate_by_index",
    "set_tab_preset", "get_tab_preset", "get_route_groups_snapshot", "get_status", "get_watchdog_summary",
})

# 标签页池中允许远程读取的配置属性
TAB_POOL_ATTRS = ("allocation_mode", "excluded_urls", "preserve_error_tabs", "auto_remember_url_presets", "route_groups")

# 浏览器对象上允许远程调用的方法
BROWSER_METHODS = frozenset({"get_pool_status", "health_check"})

HEARTBEAT_SEC = 1.0
