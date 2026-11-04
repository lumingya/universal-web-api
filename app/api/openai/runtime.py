"""运行期依赖：浏览器连接、请求管理器与取消风暴护栏。

原先这些对象在 ``app/api/tab_routes.py`` 的模块全局里，测试通过
``monkeypatch.setattr(tab_routes, "get_browser", ...)`` 替换。拆分后实现分布
在多个模块，统一从这里取用可以让替换只发生在一个地方。
"""

from app.core import get_browser
from app.services.request_manager import cancel_storm_guard, request_manager

__all__ = ["get_browser", "cancel_storm_guard", "request_manager"]
