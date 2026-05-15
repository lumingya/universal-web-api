"""
app/core/session_manager.py - 对话会话状态管理器（v1.0）

职责：
- 跟踪每个站点域名的最后请求时间戳
- 根据可配置的时间阈值判断是否需要创建新对话
- 支持上下文保持与自动清除
- 线程安全实现
"""

import threading
import time
from typing import Dict, Optional

from app.core.config import BrowserConstants, get_logger

logger = get_logger("SESSION")


class SessionManager:
    """
    对话会话管理器（单例）

    核心逻辑：
    - 记录每个域名最近一次请求的时间戳
    - 当新请求到达时，判断距上次请求的时间差是否超过阈值
    - 若超过阈值，则认为会话已过期，需要创建新对话
    - 若未超过阈值，则保持当前对话上下文

    默认阈值：300 秒（5 分钟），可通过 CONVERSATION_TIMEOUT_THRESHOLD 配置
    """

    _instance: Optional['SessionManager'] = None
    _instance_lock = threading.Lock()

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

        self._sessions: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._initialized = True

    @classmethod
    def get_instance(cls) -> 'SessionManager':
        return cls()

    def _get_threshold(self) -> float:
        try:
            threshold = BrowserConstants.get('CONVERSATION_TIMEOUT_THRESHOLD')
            if threshold is not None:
                return float(threshold)
        except Exception:
            pass
        return 300.0

    def record_request(self, domain: str):
        try:
            if not domain or not isinstance(domain, str):
                return
            now = time.time()
            with self._lock:
                self._sessions[domain.strip()] = now
                logger.debug(f"[{domain}] 记录请求时间戳: {now}")
        except Exception as e:
            logger.warning(f"[{domain}] 记录请求时间戳失败: {e}")

    def get_last_request_time(self, domain: str) -> Optional[float]:
        try:
            with self._lock:
                return self._sessions.get(domain.strip() if domain else "")
        except Exception:
            return None

    def should_start_new_conversation(self, domain: str) -> bool:
        try:
            if not domain or not isinstance(domain, str):
                return True

            threshold = self._get_threshold()
            domain = domain.strip()

            with self._lock:
                last_time = self._sessions.get(domain)

            if last_time is None:
                logger.debug(f"[{domain}] 首次请求，创建新对话")
                return True

            elapsed = time.time() - last_time
            needs_new = elapsed >= threshold

            if needs_new:
                logger.debug(f"[{domain}] 距上次请求已过 {elapsed:.1f}s (阈值 {threshold}s)，创建新对话")
            else:
                logger.debug(f"[{domain}] 距上次请求仅 {elapsed:.1f}s (阈值 {threshold}s)，保持当前对话")

            return needs_new
        except Exception as e:
            logger.warning(f"判断对话状态失败，默认创建新对话: {e}")
            return True

    def clear_session(self, domain: str):
        try:
            if not domain:
                return
            with self._lock:
                self._sessions.pop(domain.strip(), None)
        except Exception as e:
            logger.warning(f"[{domain}] 清除会话状态失败: {e}")

    def get_all_sessions(self) -> Dict[str, Dict]:
        """获取所有会话状态（线程安全）"""
        now = time.time()
        threshold = self._get_threshold()
        with self._lock:
            result = {}
            for domain, last_time in self._sessions.items():
                elapsed = now - last_time
                result[domain] = {
                    "last_request_time": last_time,
                    "elapsed_seconds": round(elapsed, 1),
                    "threshold_seconds": threshold,
                    "will_new_conversation": elapsed >= threshold,
                }
            return result

    def reset(self):
        with self._lock:
            self._sessions.clear()


session_manager = SessionManager()
