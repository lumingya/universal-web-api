"""运行期依赖：浏览器连接与请求管理器。

``health.py`` 与 ``diagnostics.py`` 都会读取浏览器/请求状态；集中在
这里引用，测试替换（``monkeypatch.setattr(runtime, "get_browser", ...)``）
只有一个落点，不会因为拆分而静默失效。
"""

from app.core import get_browser
from app.services.request_manager import request_manager

__all__ = ["get_browser", "request_manager"]
