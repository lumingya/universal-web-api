"""
app/core/workflow - 工作流执行模块

职责：
- 导出 WorkflowExecutor（保持向后兼容）
"""

from .executor import WorkflowExecutor

__all__ = ['WorkflowExecutor']