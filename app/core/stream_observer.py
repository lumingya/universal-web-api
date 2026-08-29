"""
app/core/stream_observer.py - 通用流式监听观察者与钩子接口

提供针对 StreamMonitor 的扩展观察者抽象，允许各站点（如 Arena 生图守卫）
或特化流式行为以 Observer 模式挂载，解耦通用流监听核心。
"""

from typing import Any, Dict, List, Optional


class StreamObserver:
    """流式监控观察者基类。"""

    def on_stream_start(self, monitor: Any, ctx: Any) -> None:
        """在进入流式监控轮询前触发。"""
        pass

    def on_poll_tick(
        self,
        monitor: Any,
        ctx: Any,
        snapshot: Dict[str, Any],
        state: Dict[str, Any],
    ) -> None:
        """
        在每次 DOM 轮询 tick 中触发。
        可通过读取与修改 state 字典影响监控流转，支持的字段包括：
        - still_generating: bool (当前是否仍在生成)
        - current_has_new_rendered_image: bool (当前是否有新渲染的图片)
        - recovery_confirmed: bool (是否确认恢复完成)
        - recovery_has_output: bool (恢复过程中是否已观测到输出)
        - no_visible_progress: bool (是否处于无可见进展等待状态)
        - terminal_error: Optional[Exception] (若设置则由 monitor 立即抛出)
        - terminal_break: bool (若为 True 则立即退出轮询)
        - hold_unrendered_image: bool (是否拦截未渲染图片的提前完成)
        - custom_eval_data: Optional[Dict[str, Any]] (自定义评估结果)
        """
        pass

    def on_stream_end(self, monitor: Any, ctx: Any) -> None:
        """在退出流式监控轮询后触发。"""
        pass

    def get_active_stop_recovery_grace_seconds(self, default: float) -> Optional[float]:
        """返回当前观察者定制的无进展宽限期（秒）。"""
        return None

    def get_stream_recovery_max_refreshes(self, default: int) -> Optional[int]:
        """返回当前观察者定制的最大恢复刷新次数。"""
        return None

    def get_interrupted_refresh_interval(self, default: float) -> Optional[float]:
        """返回当前观察者定制的断流刷新轮询间隔（秒）。"""
        return None

StreamObserverFactory = Any
_STREAM_OBSERVER_FACTORIES: List[Any] = []


def register_stream_observer_factory(factory: Any) -> None:
    """注册流式监控观察者工厂函数。"""
    if callable(factory) and factory not in _STREAM_OBSERVER_FACTORIES:
        _STREAM_OBSERVER_FACTORIES.append(factory)


def get_stream_observer_factories() -> List[Any]:
    """获取所有已注册的流式监控观察者工厂。"""
    return list(_STREAM_OBSERVER_FACTORIES)


__all__ = [
    "StreamObserver",
    "get_stream_observer_factories",
    "register_stream_observer_factory",
]