"""
app/core/tab_pool.py - 标签页池管理器 (v1.05)

修复：
- 添加卡死检测和自动释放
- 初始化时重置状态
- 不自动创建空白标签页
- 🆕 动态扫描新标签页（基于时间间隔）
"""

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import Enum
from contextlib import asynccontextmanager

from app.core.config import logger


class TabStatus(Enum):
    """标签页状态"""
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    CLOSED = "closed"

@dataclass
class TabSession:
    """标签页会话"""
    id: str
    tab: Any
    status: TabStatus = TabStatus.IDLE
    current_task_id: Optional[str] = None
    current_domain: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    request_count: int = 0
    error_count: int = 0
    persistent_index: int = 0  # 🆕 持久化编号（重启前不变）
    preset_name: Optional[str] = None  # 🆕 当前使用的预设名称（None = 主预设）
    
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    
    def is_available(self) -> bool:
        return self.status == TabStatus.IDLE
    
    def is_healthy(self) -> bool:
        """检查标签页是否健康（增强版 + 无效协议过滤）"""
        if self.status == TabStatus.CLOSED:
            return False
    
        try:
            url = self.tab.url
        
            # 检查是否能获取 URL
            if url is None:
                return False
        
            # 检查 URL 是否有效
            invalid_patterns = (
                "about:blank", 
                "chrome://newtab/",
                "chrome://new-tab-page/",
                "chrome-error://",
                "about:neterror"
            )
            for pattern in invalid_patterns:
                if pattern in url:
                    return False
        
            # 🆕 检查无效协议
            invalid_protocols = ("javascript:", "data:", "blob:about:")
            for protocol in invalid_protocols:
                if url.startswith(protocol):
                    return False
        
            # 检查是否有有效协议
            if "://" not in url:
                return False
            
            # 🆕 只允许 http/https/ws/wss 协议
            valid_protocols = ("http://", "https://", "ws://", "wss://")
            if not any(url.startswith(p) for p in valid_protocols):
                return False
        
            return True
        
        except Exception:
            return False
    
    def acquire(self, task_id: str) -> bool:
        with self._lock:
            if self.status != TabStatus.IDLE:
                return False
            
            self.status = TabStatus.BUSY
            self.current_task_id = task_id
            self.last_used_at = time.time()
            self.request_count += 1
            return True
    
    def release(self, clear_page: bool = False):
        with self._lock:
            self.status = TabStatus.IDLE
            self.current_task_id = None
            self.last_used_at = time.time()
            
            if clear_page:
                try:
                    self.tab.get("about:blank")
                    self.current_domain = None
                except Exception as e:
                    logger.debug(f"清空页面失败: {e}")
    
    def force_release(self):
        """强制释放（不管当前状态）- 修复版：尝试重置页面，失败则标记为 ERROR"""
        # 先标记为 ERROR，防止被其他线程获取
        with self._lock:
            self.status = TabStatus.ERROR
            self.current_task_id = None
            self.last_used_at = time.time()
        
        # 在锁外尝试重置页面（避免阻塞其他线程）
        reset_success = False
        try:
            self.tab.get("about:blank")
            self.current_domain = None
            reset_success = True
        except Exception as e:
            logger.warning(f"[{self.id}] 重置页面失败: {e}")
        
        # 根据重置结果更新状态
        with self._lock:
            if reset_success:
                self.status = TabStatus.IDLE
                logger.info(f"[{self.id}] 强制释放成功，已重置为空白页")
            else:
                self.error_count += 1
                logger.warning(f"[{self.id}] 强制释放失败，标记为 ERROR（将被移出池）")
    
    def activate(self) -> bool:
        """激活标签页（使其成为浏览器焦点）"""
        try:
            self.tab.set.activate()
            logger.debug(f"[{self.id}] 已激活")
            return True
        except Exception as e:
            logger.warning(f"[{self.id}] 激活失败: {e}")
            return False
    
    def mark_error(self, reason: str = None):
        with self._lock:
            self.status = TabStatus.ERROR
            self.error_count += 1
            logger.warning(f"[{self.id}] 标记为错误: {reason}")
    
    def get_info(self) -> Dict:
        busy_duration = None
        if self.status == TabStatus.BUSY:
            busy_duration = round(time.time() - self.last_used_at, 1)
        
        return {
            "id": self.id,
            "persistent_index": self.persistent_index,
            "status": self.status.value,
            "current_task": self.current_task_id,
            "current_domain": self.current_domain,
            "url": self._safe_get_url(),
            "request_count": self.request_count,
            "busy_duration": busy_duration,
            "preset_name": self.preset_name,  # 🆕
        }
    
    def _safe_get_url(self) -> str:
        try:
            return self.tab.url or ""
        except:
            return ""


class TabPoolManager:
    """标签页池管理器"""
    
    DOMAIN_ABBR_MAP = {
        "chatgpt": "gpt",
        "openai": "gpt",
        "gemini": "gemini", 
        "aistudio": "aistudio",
        "claude": "claude",
        "anthropic": "claude",
        "poe": "poe",
        "bing": "bing",
        "copilot": "copilot",
        "perplexity": "pplx",
        "lmarena": "lmarena",
        "chat": "chat",
    }
    
    # 卡死超时时间（秒）
    STUCK_TIMEOUT = 180
    
    # 新标签页扫描间隔（秒）
    SCAN_INTERVAL = 10
    
    def __init__(
        self,
        browser_page,
        max_tabs: int = 5,
        min_tabs: int = 1,
        idle_timeout: float = 300,
        acquire_timeout: float = 60,
    ):
        self.page = browser_page
        self.max_tabs = max_tabs
        self.min_tabs = min_tabs
        self.idle_timeout = idle_timeout
        self.acquire_timeout = acquire_timeout
        
        self._tabs: Dict[str, TabSession] = {}
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        
        self._initialized = False
        self._shutdown = False
        self._tab_counter = 0
        
        self._last_scan_time: float = 0
        
        # 记录已知的标签页底层 ID（用于检测新标签页）
        self._known_tab_ids: set = set()
        # 🆕 记录当前活动的标签页 ID（避免重复激活）
        self._active_session_id: Optional[str] = None
        
        # 🆕 持久化编号系统
        self._next_persistent_index: int = 1  # 下一个可分配的编号
        self._raw_id_to_persistent: Dict[str, int] = {}  # raw_tab_id → persistent_index
        self._persistent_to_session_id: Dict[int, str] = {}  # persistent_index → session.id
        
        logger.debug(f"TabPoolManager 初始化 (max={max_tabs})")
        
    def _get_domain_abbr(self, url: str) -> str:
        try:
            if not url or "://" not in url:
                return "tab"
            
            domain = url.split("//")[-1].split("/")[0].lower()
            clean_domain = domain.replace("www.", "")
            
            for key, abbr in self.DOMAIN_ABBR_MAP.items():
                if key in clean_domain:
                    return abbr
            
            first_part = clean_domain.split(".")[0]
            return first_part[:10]
            
        except Exception:
            return "tab"
    
    def _wrap_tab(self, tab, raw_tab_id: str = None) -> TabSession:
        self._tab_counter += 1
        
        url = ""
        try:
            url = tab.url or ""
        except:
            pass
        
        abbr = self._get_domain_abbr(url)
        tab_id = f"{abbr}_{self._tab_counter}"
        
        session = TabSession(id=tab_id, tab=tab)
        
        try:
            if url and "://" in url:
                session.current_domain = url.split("//")[-1].split("/")[0]
        except:
            pass
        
        # 记录底层标签页 ID
        if raw_tab_id:
            self._known_tab_ids.add(raw_tab_id)
            
            # 🆕 分配持久化编号
            if raw_tab_id not in self._raw_id_to_persistent:
                persistent_idx = self._next_persistent_index
                self._next_persistent_index += 1
                self._raw_id_to_persistent[raw_tab_id] = persistent_idx
            else:
                persistent_idx = self._raw_id_to_persistent[raw_tab_id]
            
            session.persistent_index = persistent_idx
            self._persistent_to_session_id[persistent_idx] = session.id
            logger.debug(f"标签页 {session.id} 分配编号 #{persistent_idx}")
        
        return session
    
    def _should_scan(self) -> bool:
        """检查是否需要扫描新标签页"""
        return time.time() - self._last_scan_time >= self.SCAN_INTERVAL
    
    def _scan_new_tabs(self):
        """扫描并添加新标签页（已持有锁）- 修复版"""
        try:
            current_tab_ids = self.page.get_tabs()
            
            # 明确永久跳过的页面（这些不会变成有效页面）
            permanent_skip_patterns = [
                "chrome://newtab/", 
                "chrome://new-tab-page/", 
                "chrome-error://",
                "chrome://crashes/",
                "chrome://settings/",
            ]
            
            # 临时跳过的页面（可能正在加载，下次再检查）
            temporary_skip_patterns = [
                "about:blank",
            ]
            
            new_count = 0
            for raw_tab_id in current_tab_ids:
                # 检查是否已达到最大数量
                if len(self._tabs) >= self.max_tabs:
                    break
                
                # 检查是否已知
                if raw_tab_id in self._known_tab_ids:
                    continue
                
                try:
                    tab = self.page.get_tab(raw_tab_id)
                    if not tab:
                        continue
                    
                    # 获取 URL
                    url = ""
                    try:
                        url = tab.url or ""
                    except Exception:
                        pass
                    
                    # 情况 1：永久无效页面 - 记录并跳过
                    if any(p in url for p in permanent_skip_patterns):
                        self._known_tab_ids.add(raw_tab_id)
                        continue
                    
                    # 情况 2：临时无效（空 URL 或 about:blank）- 跳过但不记录
                    # 下次扫描时会重新检查
                    if not url or any(p in url for p in temporary_skip_patterns):
                        # 不加入 _known_tab_ids，允许下次重新扫描
                        continue
                    
                    # 情况 3：有效页面 - 添加到池
                    session = self._wrap_tab(tab, raw_tab_id)
                    self._tabs[session.id] = session
                    new_count += 1
                    
                    display_url = url[:50] + "..." if len(url) > 50 else url
                    logger.debug(f"🆕 发现新标签页: {session.id} -> {display_url}")
                    
                except Exception as e:
                    logger.debug(f"处理标签页出错: {e}")
                    continue
            
            self._last_scan_time = time.time()
            
            if new_count > 0:
                logger.debug(f"扫描完成: +{new_count} 个，当前共 {len(self._tabs)} 个标签页")
                
        except Exception as e:
            logger.debug(f"扫描标签页失败: {e}")
    
    def initialize(self):
        """初始化池 - 修复版"""
        with self._lock:
            if self._initialized:
                return
            
            # 永久跳过的页面
            permanent_skip_patterns = [
                "chrome://newtab/", 
                "chrome://new-tab-page/",
                "chrome-error://",
            ]
            
            try:
                existing_tab_ids = self.page.get_tabs()
                logger.debug(f"检测到 {len(existing_tab_ids)} 个标签页")
                
                for raw_tab_id in existing_tab_ids:
                    if len(self._tabs) >= self.max_tabs:
                        break
                    
                    try:
                        tab = self.page.get_tab(raw_tab_id)
                        if not tab:
                            continue
                        
                        url = ""
                        try:
                            url = tab.url or ""
                        except Exception:
                            pass
                        
                        # 永久无效页面 - 记录并跳过
                        if any(p in url for p in permanent_skip_patterns):
                            self._known_tab_ids.add(raw_tab_id)
                            continue
                        
                        # 临时无效（空 URL 或 about:blank）- 跳过但不记录
                        if not url or "about:blank" in url:
                            continue
                        
                        # 有效页面 - 添加到池并记录
                        session = self._wrap_tab(tab, raw_tab_id)
                        self._tabs[session.id] = session
                        
                        display_url = url[:50] + "..." if len(url) > 50 else url
                        logger.debug(f"TabPool: {session.id} -> {display_url}")                        
                    except Exception as e:
                        logger.debug(f"处理标签页出错: {e}")
                        continue
                        
            except Exception as e:
                logger.warning(f"扫描标签页失败: {e}")
            
            # 重置所有状态为 IDLE
            for session in self._tabs.values():
                session.status = TabStatus.IDLE
                session.current_task_id = None
            
            self._initialized = True
            self._last_scan_time = time.time()
            logger.debug(f"TabPool 就绪: {len(self._tabs)} 个标签页")    
    def _check_stuck_tabs(self):
        """检查并释放卡死的标签页"""
        now = time.time()
        
        for session in self._tabs.values():
            if session.status == TabStatus.BUSY:
                busy_duration = now - session.last_used_at
                
                if busy_duration > self.STUCK_TIMEOUT:
                    logger.warning(
                        f"[{session.id}] 卡死 {busy_duration:.0f}s，强制释放 "
                        f"(任务: {session.current_task_id})"
                    )
                    session.force_release()
    
    def _cleanup_unhealthy_tabs(self):
        """清理不健康的空闲标签页和错误状态的标签页"""
        to_remove = []
    
        for tab_id, session in self._tabs.items():
            # 清理 ERROR 状态的标签页（包括强制释放失败的）
            if session.status == TabStatus.ERROR:
                to_remove.append(tab_id)
            # 清理空闲但不健康的标签页
            elif session.status == TabStatus.IDLE and not session.is_healthy():
                to_remove.append(tab_id)
    
        for tab_id in to_remove:
            logger.warning(f"[{tab_id}] 不健康或错误状态，从池中移除")
            del self._tabs[tab_id]
    
    def acquire(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """获取一个可用的标签页（增强版）"""
        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout
        logged_waiting = False

        with self._condition:
            while True:
                if self._shutdown:
                    return None
                
                # 定期扫描新标签页
                if self._should_scan():
                    self._scan_new_tabs()
                
                # 检查卡死的标签页
                self._check_stuck_tabs()
                
                # 清理不健康的空闲标签页
                self._cleanup_unhealthy_tabs()
                
                # 寻找空闲且健康的标签页
                for session in self._tabs.values():
                    if session.status == TabStatus.IDLE:
                        # 检查健康状态
                        if not session.is_healthy():
                            logger.warning(f"[{session.id}] 标签页不健康，跳过")
                            continue
                        
                        if session.acquire(task_id):
                            # 🆕 仅当不是当前活动标签页时才激活
                            if session.id != self._active_session_id:
                                session.activate()
                                self._active_session_id = session.id
                            
                            # task_id 已在上下文中，无需重复
                            if logged_waiting:
                                logger.info(f"等待结束 → {session.id}")
                            else:
                                logger.info(f"TabPool → {session.id}")
                            return session
                
                # 检查超时
                remaining = deadline - time.time()
                if remaining <= 0:
                    busy_info = [
                        f"{s.id}({s.current_task_id})" 
                        for s in self._tabs.values() 
                        if s.status == TabStatus.BUSY
                    ]
                    unhealthy_count = sum(
                        1 for s in self._tabs.values() 
                        if s.status == TabStatus.IDLE and not s.is_healthy()
                    )
                    logger.warning(
                        f"获取标签页超时 (忙碌: {', '.join(busy_info) or 'none'}, "
                        f"不健康: {unhealthy_count})"
                    )
                    return None
                
                # 等待
                if not logged_waiting:
                    busy_tabs = [s.id for s in self._tabs.values() if s.status == TabStatus.BUSY]
                    if busy_tabs:
                        logger.debug(f"排队等待 (忙碌: {', '.join(busy_tabs)})")
                    logged_waiting = True
                
                self._condition.wait(timeout=min(remaining, 1.0))
    
    async def acquire_async(self, task_id: str, timeout: float = None) -> Optional[TabSession]:
        return await asyncio.to_thread(self.acquire, task_id, timeout)
    
    def release(self, tab_id: str, clear_page: bool = False):
        """释放标签页"""
        with self._condition:
            session = self._tabs.get(tab_id)
            if session:
                session.release(clear_page=clear_page)
                self._condition.notify_all()
                logger.debug(f"[{tab_id}] 已释放")
    
    def force_release_all(self):
        """强制释放所有标签页（调试用）"""
        with self._condition:
            count = 0
            for session in self._tabs.values():
                if session.status == TabStatus.BUSY:
                    session.force_release()
                    count += 1
            self._condition.notify_all()
            logger.info(f"强制释放 {count} 个标签页")
            return count
    
    def refresh_tabs(self) -> int:
        """手动刷新标签页列表（供外部调用）"""
        with self._lock:
            old_count = len(self._tabs)
            self._last_scan_time = 0  # 强制下次扫描
            self._scan_new_tabs()
            new_count = len(self._tabs) - old_count
            return new_count
    
    @asynccontextmanager
    async def get_tab(self, task_id: str, timeout: float = None):
        session = await self.acquire_async(task_id, timeout)
        if session is None:
            raise TimeoutError(f"获取标签页超时 (task: {task_id})")
        
        try:
            yield session
        except Exception as e:
            session.mark_error(str(e))
            raise
        finally:
            self.release(session.id)
    def acquire_by_index(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """
        根据持久化编号获取指定标签页
        
        Args:
            persistent_index: 持久化编号（1, 2, 3...）
            task_id: 任务 ID
            timeout: 超时时间
            
        Returns:
            TabSession 或 None（如果编号无效或标签页不可用）
        """
        timeout = timeout or self.acquire_timeout
        deadline = time.time() + timeout
        
        with self._condition:
            while True:
                if self._shutdown:
                    return None
                
                # 定期扫描新标签页
                if self._should_scan():
                    self._scan_new_tabs()
                
                # 查找对应的 session
                session_id = self._persistent_to_session_id.get(persistent_index)
                if not session_id:
                    logger.warning(f"持久编号 #{persistent_index} 不存在")
                    return None
                
                session = self._tabs.get(session_id)
                if not session:
                    logger.warning(f"标签页 {session_id} (#{persistent_index}) 已被移除")
                    return None
                
                # 检查健康状态
                if not session.is_healthy():
                    logger.warning(f"[{session.id}] 标签页不健康")
                    return None
                
                # 尝试获取
                if session.status == TabStatus.IDLE:
                    if session.acquire(task_id):
                        # 🆕 仅当不是当前活动标签页时才激活
                        if session.id != self._active_session_id:
                            session.activate()
                            self._active_session_id = session.id
                        logger.info(f"TabPool → {session.id} (#{persistent_index})")
                        return session
                
                # 标签页忙碌，等待
                remaining = deadline - time.time()
                if remaining <= 0:
                    logger.warning(f"获取标签页 #{persistent_index} 超时（当前状态: {session.status.value}）")
                    return None
                
                logger.debug(f"等待标签页 #{persistent_index} 释放...")
                self._condition.wait(timeout=min(remaining, 1.0))
    
    async def acquire_by_index_async(self, persistent_index: int, task_id: str, timeout: float = None) -> Optional[TabSession]:
        """异步版本的按编号获取"""
        return await asyncio.to_thread(self.acquire_by_index, persistent_index, task_id, timeout)
    
    def get_tabs_with_index(self) -> List[Dict]:
        """获取所有标签页及其持久编号（供 API 调用）"""
        with self._lock:
            # 先扫描确保最新
            if self._should_scan():
                self._scan_new_tabs()
            
            result = []
            for session in self._tabs.values():
                info = session.get_info()
                # 构建路由前缀
                info["route_prefix"] = f"/tab/{session.persistent_index}"
                result.append(info)
            
            # 按编号排序
            result.sort(key=lambda x: x.get("persistent_index", 0))
            return result

    # ================= 预设管理 =================
    
    def set_tab_preset(self, persistent_index: int, preset_name: str) -> bool:
        """
        为指定标签页设置预设
        
        Args:
            persistent_index: 标签页持久化编号
            preset_name: 预设名称（None 或空字符串表示恢复为主预设）
        
        Returns:
            是否成功
        """
        with self._lock:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                logger.warning(f"标签页 #{persistent_index} 不存在")
                return False
            
            session = self._tabs.get(session_id)
            if not session:
                logger.warning(f"标签页 {session_id} 已被移除")
                return False
            
            old_preset = session.preset_name
            session.preset_name = preset_name if preset_name else None
            
            logger.info(
                f"[{session.id}] 预设切换: "
                f"'{old_preset or '主预设'}' → '{preset_name or '主预设'}'"
            )
            return True
    
    def get_tab_preset(self, persistent_index: int) -> Optional[str]:
        """获取指定标签页的当前预设名称"""
        with self._lock:
            session_id = self._persistent_to_session_id.get(persistent_index)
            if not session_id:
                return None
            
            session = self._tabs.get(session_id)
            if not session:
                return None
            
            return session.preset_name

    # ================= 状态查询 =================

    def get_status(self) -> Dict:
        with self._lock:
            tabs_info = [s.get_info() for s in self._tabs.values()]
            
            return {
                "total": len(self._tabs),
                "idle": sum(1 for s in self._tabs.values() if s.status == TabStatus.IDLE),
                "busy": sum(1 for s in self._tabs.values() if s.status == TabStatus.BUSY),
                "max_tabs": self.max_tabs,
                "known_raw_tabs": len(self._known_tab_ids),
                "last_scan": round(time.time() - self._last_scan_time, 1),
                "tabs": tabs_info
            }
    
    def shutdown(self):
        with self._lock:
            self._shutdown = True
            self._tabs.clear()
            self._known_tab_ids.clear()
            self._active_session_id = None  # 🆕 重置活动标签页记录
            # 🆕 清理编号映射
            self._raw_id_to_persistent.clear()
            self._persistent_to_session_id.clear()
            self._next_persistent_index = 1
            logger.info("TabPoolManager 已关闭")


# 剪贴板锁
_clipboard_lock = threading.Lock()

def get_clipboard_lock() -> threading.Lock:
    return _clipboard_lock


__all__ = [
    'TabStatus',
    'TabSession',
    'TabPoolManager',
    'get_clipboard_lock',
]