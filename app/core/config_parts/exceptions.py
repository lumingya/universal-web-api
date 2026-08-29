"""
app/core/config_parts/exceptions.py - 异常定义模块
"""


class BrowserError(Exception):
    """浏览器相关错误基类"""
    pass


class BrowserConnectionError(BrowserError):
    """浏览器连接错误"""
    pass


class ElementNotFoundError(BrowserError):
    """元素未找到错误"""
    pass


class WorkflowError(BrowserError):
    """工作流执行错误"""
    pass


class WorkflowCancelledError(WorkflowError):
    """工作流被取消"""
    pass


class ConfigurationError(BrowserError):
    """配置错误"""
    pass
