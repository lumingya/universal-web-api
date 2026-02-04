"""
request_manager.py - 请求生命周期管理器（v2.0）

v2.0 改动：
- 移除全局执行锁（锁转移到 TabPoolManager）
- 保留请求追踪、状态管理、取消信号功能
- acquire/release 改为标记状态，不再阻塞
"""

import asyncio
import threading
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from collections import OrderedDict

from app.core.config import get_logger, _request_context

logger = get_logger("REQUEST")


class RequestStatus(Enum):
    """请求状态枚举"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class RequestContext:
    """请求上下文"""
    request_id: str
    status: RequestStatus = RequestStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    
    _cancel_flag: bool = field(default=False, repr=False)
    cancel_reason: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    
    # v2.0 新增：关联的标签页 ID
    tab_id: Optional[str] = None
    
    def should_stop(self) -> bool:
        with self._lock:
            return self._cancel_flag
    
    def request_cancel(self, reason: str = "unknown"):
        with self._lock:
            if self._cancel_flag:
                return
            
            self._cancel_flag = True
            self.cancel_reason = reason
            
            if self.status == RequestStatus.RUNNING:
                self.status = RequestStatus.CANCELLED
            
            logger.info(f"[{self.request_id}] 取消 ({reason})")
    
    def mark_running(self, tab_id: str = None):
        with self._lock:
            self.status = RequestStatus.RUNNING
            self.started_at = time.time()
            self.tab_id = tab_id
    
    def mark_completed(self):
        with self._lock:
            if self.status == RequestStatus.RUNNING:
                self.status = RequestStatus.COMPLETED
            self.finished_at = time.time()
    
    def mark_failed(self, reason: str = None):
        with self._lock:
            self.status = RequestStatus.FAILED
            self.finished_at = time.time()
            if reason:
                self.cancel_reason = reason
    
    def get_duration(self) -> float:
        end = self.finished_at or time.time()
        start = self.started_at or self.created_at
        return end - start
    
    def is_terminal(self) -> bool:
        return self.status in (
            RequestStatus.COMPLETED,
            RequestStatus.CANCELLED,
            RequestStatus.FAILED
        )


class RequestManager:
    """
    请求管理器（v2.0 - 纯追踪模式）
    
    v2.0 改动：
    - 不再持有执行锁
    - 只负责请求追踪和取消信号
    """
    
    _instance: Optional['RequestManager'] = None
    _instance_lock = threading.Lock()
        
    # 僵尸请求超时时间（秒）- 超过此时间的 RUNNING 请求将被强制清理
    ZOMBIE_TTL = 3600
    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._requests: OrderedDict[str, RequestContext] = OrderedDict()
        self._requests_lock = threading.Lock()
        self._request_counter = 0  # 请求计数器
        
        self._max_history = 100
        self._initialized = True
        
        logger.debug("RequestManager 初始化完成")
    
    def create_request(self) -> RequestContext:
        """创建新请求"""
        request_id = self._generate_id()
        ctx = RequestContext(request_id=request_id)
        
        with self._requests_lock:
            self._requests[request_id] = ctx
            self._cleanup_old_requests()
        
        # 设置上下文后记录日志
        token = _request_context.set(request_id)
        try:
            logger.info("创建")
        finally:
            _request_context.reset(token)
        
        return ctx
    
    def _generate_id(self) -> str:
        """生成简短的请求 ID"""
        with self._requests_lock:
            self._request_counter += 1
            return f"req-{self._request_counter:03d}"
    
    def _cleanup_old_requests(self):
        """清理旧请求（修复版：不因单个未完成请求阻塞所有清理）"""
        if len(self._requests) <= self._max_history:
            return
        
        now = time.time()
        to_delete = []
        
        for req_id, ctx in list(self._requests.items()):
            # 已终态的可以删除
            if ctx.is_terminal():
                to_delete.append(req_id)
            # 超时的 RUNNING 请求视为僵尸，强制标记失败
            elif ctx.status == RequestStatus.RUNNING:
                started = ctx.started_at or ctx.created_at
                if now - started > self.ZOMBIE_TTL:
                    logger.warning(
                        f"[{req_id}] 僵尸请求 (运行 {now - started:.0f}s)，强制清理"
                    )
                    ctx.mark_failed("zombie_timeout")
                    to_delete.append(req_id)
            
            # 收集足够数量后停止遍历
            if len(self._requests) - len(to_delete) <= self._max_history:
                break
        
        # 批量删除
        for req_id in to_delete:
            del self._requests[req_id]
        
        if to_delete:
            logger.debug(f"清理了 {len(to_delete)} 个旧请求")
    
    def start_request(self, ctx: RequestContext, tab_id: str = None):
        """标记请求开始执行"""
        ctx.mark_running(tab_id)
        # 日志由调用方在上下文中记录，这里不再重复
    
    def finish_request(self, ctx: RequestContext, success: bool = True):
        """标记请求结束"""
        if ctx.status == RequestStatus.RUNNING:
            if success:
                ctx.mark_completed()
            else:
                ctx.mark_failed()
        
        duration = ctx.get_duration()
        # 设置上下文后记录日志
        token = _request_context.set(ctx.request_id)
        try:
            logger.info(f"完成 ({duration:.1f}s)")
        finally:
            _request_context.reset(token)
    
    def cancel_request(self, request_id: str, reason: str = "manual") -> bool:
        """取消指定请求"""
        with self._requests_lock:
            ctx = self._requests.get(request_id)
        
        if not ctx:
            return False
        
        if ctx.is_terminal():
            return False
        
        ctx.request_cancel(reason)
        return True
    
    def get_request(self, request_id: str) -> Optional[RequestContext]:
        with self._requests_lock:
            return self._requests.get(request_id)
    
    def get_running_requests(self) -> list:
        """获取所有正在执行的请求"""
        with self._requests_lock:
            return [
                ctx for ctx in self._requests.values()
                if ctx.status == RequestStatus.RUNNING
            ]
    
    def get_status(self) -> Dict[str, Any]:
        """获取管理器状态"""
        with self._requests_lock:
            status_counts = {}
            for ctx in self._requests.values():
                s = ctx.status.value
                status_counts[s] = status_counts.get(s, 0) + 1
            
            running = [
                {
                    "request_id": ctx.request_id, 
                    "tab_id": ctx.tab_id, 
                    "duration": round(ctx.get_duration(), 1)
                }
                for ctx in self._requests.values()
                if ctx.status == RequestStatus.RUNNING
            ]
            
            return {
                "running_count": len(running),
                "running_requests": running,
                "total_tracked": len(self._requests),
                "status_counts": status_counts
            }
    
    # ================= 兼容旧接口 =================
    
    def is_locked(self) -> bool:
        """兼容旧接口 - 检查是否有正在执行的请求"""
        with self._requests_lock:
            return any(
                ctx.status == RequestStatus.RUNNING 
                for ctx in self._requests.values()
            )
    
    def get_current_request_id(self) -> Optional[str]:
        """兼容旧接口 - 获取当前执行的请求ID（返回第一个）"""
        with self._requests_lock:
            for ctx in self._requests.values():
                if ctx.status == RequestStatus.RUNNING:
                    return ctx.request_id
            return None
    
    def cancel_current(self, reason: str = "manual") -> bool:
        """取消当前正在执行的请求（取消所有运行中的）"""
        cancelled = False
        for ctx in self.get_running_requests():
            if self.cancel_request(ctx.request_id, reason):
                cancelled = True
        return cancelled
    
    def force_release(self) -> bool:
        """兼容旧接口 - 强制取消所有运行中的请求"""
        return self.cancel_current("force_release")


# ================= 全局单例 =================

request_manager = RequestManager()


# ================= 辅助函数 =================

async def watch_client_disconnect(request, ctx: RequestContext,
                                   check_interval: float = 0.5):
    """监控客户端连接状态"""
    try:
        while not ctx.is_terminal():
            if await request.is_disconnected():
                ctx.request_cancel("client_disconnected")
                break
            
            await asyncio.sleep(check_interval)
    
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"断开检测异常: {e}")