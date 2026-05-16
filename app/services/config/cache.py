"""
app/services/config/cache.py - 配置缓存层

职责：
- 为 ConfigEngine 提供内存缓存，减少重复的 JSON 文件 I/O
- 使用 TTL + mtime 双触发失效策略
"""

import os
import time
from typing import Dict, Optional, Any, Tuple


class ConfigCache:
    """配置缓存，使用 TTL + mtime 双触发失效"""

    def __init__(self, ttl: float = 5.0):
        self._ttl = ttl
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._mtime_snapshots: Dict[str, float] = {}

    def get(self, key: str) -> Optional[Any]:
        """获取缓存，如果过期返回 None"""
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, timestamp = entry
        if time.monotonic() - timestamp > self._ttl:
            del self._cache[key]
            return None
        return value

    def set(self, key: str, value: Any):
        """设置缓存"""
        self._cache[key] = (value, time.monotonic())

    def invalidate(self, key: str = None):
        """使特定 key 或全部缓存失效"""
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)

    def check_file_mtime(self, file_path: str) -> bool:
        """检查文件 mtime 是否变化，变化则自动失效并返回 True"""
        if not os.path.exists(file_path):
            return False
        try:
            current_mtime = os.path.getmtime(file_path)
            snapshot = self._mtime_snapshots.get(file_path)
            if snapshot is not None and current_mtime != snapshot:
                self._mtime_snapshots[file_path] = current_mtime
                self.invalidate()
                return True
            self._mtime_snapshots[file_path] = current_mtime
            return False
        except OSError:
            return False
