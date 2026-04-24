"""
app/core/browser.py - 浏览器核心连接和调度（v2.0 多标签页版）

职责：
- 浏览器连接管理
- 标签页池管理
- 工作流调度
- 对外统一接口

v2.0 改动：
- 集成 TabPoolManager 支持多任务并发
- 移除旧的 TabManager
- execute_workflow 改为接收 tab_session 参数
"""

import json
import os
import socket
import threading
import time
import contextlib
from typing import Optional, List, Dict, Any, Generator, Callable
from DrissionPage import Chromium, ChromiumPage, ChromiumOptions

from app.core.config import (
    logger,
    AppConfig,
    BrowserConstants,
    BrowserConnectionError,
    ElementNotFoundError,
    WorkflowError,
    SSEFormatter,
    MessageValidator,
)
from app.utils.image_handler import extract_images_from_messages
from app.utils.site_url import extract_remote_site_domain
from app.core.workflow import WorkflowExecutor
from app.core.tab_pool import TabPoolManager, TabSession, get_clipboard_lock


def _looks_like_transient_local_debug_error(error: Any) -> bool:
    text = str(error or "").strip().lower()
    if not text:
        return False
    if "winerror 10048" in text:
        return True
    if "max retries exceeded" in text and "127.0.0.1" in text and "/json" in text:
        return True
    if "failed to establish a new connection" in text and "127.0.0.1" in text:
        return True
    return False


# ================= 配置加载 =================

def _load_tab_pool_config() -> Dict:
    """从配置文件和环境变量加载标签页池配置"""
    config = {
        "max_tabs": 5,
        "min_tabs": 1,
        "idle_timeout": 300,
        "acquire_timeout": 60,
        "stuck_timeout": 180,
        "allocation_mode": "first_idle",
    }
    
    # 从 browser_config.json 加载
    try:
        config_path = "config/browser_config.json"
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
                pool_config = file_config.get("tab_pool", {})
                config.update(pool_config)
    except Exception as e:
        logger.debug(f"加载 tab_pool 配置失败: {e}")
    
    # 环境变量覆盖
    if os.getenv("MAX_TABS"):
        config["max_tabs"] = int(os.getenv("MAX_TABS"))
    if os.getenv("MIN_TABS"):
        config["min_tabs"] = int(os.getenv("MIN_TABS"))
    
    return config


# ================= 浏览器核心 =================

class BrowserCore:
    """浏览器核心类 - 单例模式（v2.0）"""
    
    _instance: Optional['BrowserCore'] = None
    _lock = threading.Lock()
    
    def __new__(cls, port: int = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance
    
    def __init__(self, port: int = None):
        if self._initialized:
            return
        
        self.port = port or BrowserConstants.DEFAULT_PORT
        self.browser_handle: Optional[Chromium] = None
        self.page: Optional[ChromiumPage] = None
        
        self._connected = False
        self._should_stop_checker: Callable[[], bool] = lambda: False
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()
        self._last_watchdog_port_open: Optional[bool] = None
        self._last_watchdog_session_alive: Optional[bool] = None
        self._last_watchdog_health: Optional[str] = None
        self._last_debug_port_error: str = ""
        self._get_tabs_retry_after: float = 0.0
        self._last_get_tabs_fallback_log_at: float = 0.0
        
        self.formatter = SSEFormatter()
        self.config_engine = None
        
        # v2.0: 使用 TabPoolManager 替代 TabManager
        self._tab_pool: Optional[TabPoolManager] = None
        
        self._initialized = True
        logger.debug("BrowserCore 初始化 (v2.0 多标签页版)")
        # ================= 消息处理方法 =================
    
    def _extract_text_from_content(self, content) -> str:
        """
        从消息内容中提取纯文本，图片用占位符替代
        
        支持格式：
        - 纯字符串: "你好" → "你好"
        - 多模态列表: [{"type":"text","text":"描述"},{"type":"image_url",...}] → "描述 [图片1]"
        - JSON 字符串: '[{"type":"text",...}]' → 解析后处理
        - 类列表对象: tuple/其他可迭代 → 转换为 list 处理
        """
        content_type = type(content).__name__
        content_preview = ""
        try:
            content_str_temp = str(content)
            content_len = len(content_str_temp)
            content_preview = (
                repr(content_str_temp[:120])
                if content_len > 120
                else repr(content_str_temp)
            )
        except Exception:
            content_len = -1
            content_preview = "[无法预览]"
        
        logger.debug(
            f"[CONTENT_PARSE] 开始解析: type={content_type}, "
            f"raw_len={content_len}, preview={content_preview}"
        )
        
        if content is None:
            logger.debug("[CONTENT_PARSE] 内容为 None，返回空字符串")
            return ""
        
        if isinstance(content, str):
            stripped = content.strip()
            if stripped.startswith('[') and stripped.endswith(']'):
                parsed = None
                parse_method = ""
                
                try:
                    parsed = json.loads(stripped)
                    parse_method = "json"
                except (json.JSONDecodeError, TypeError):
                    pass
                
                if parsed is None:
                    try:
                        import ast
                        parsed = ast.literal_eval(stripped)
                        parse_method = "literal_eval"
                    except (ValueError, SyntaxError):
                        pass
                
                if parsed and isinstance(parsed, list) and len(parsed) > 0:
                    first_item = parsed[0] if parsed else {}
                    if isinstance(first_item, dict) and 'type' in first_item:
                        logger.debug(
                            "[CONTENT_PARSE] 识别为字符串化多模态内容，递归解析 "
                            f"(parser={parse_method or 'unknown'}, items={len(parsed)})"
                        )
                        return self._extract_text_from_content(parsed)
            
            if content.startswith('data:image') and 'base64,' in content and len(content) > 1000:
                logger.warning(f"[CONTENT_PARSE] ⚠️ 检测到 base64 图片数据！长度={len(content)}，已替换为占位符")
                return "[图片内容]"
            
            logger.debug(f"[CONTENT_PARSE] 纯字符串返回: len={len(content)}")
            return content
        
        is_list_like = isinstance(content, (list, tuple))
        if not is_list_like:
            try:
                is_list_like = hasattr(content, '__iter__') and not isinstance(content, (str, bytes))
            except Exception:
                is_list_like = False
        
        if is_list_like:
            try:
                if not isinstance(content, list):
                    content = list(content)
                    logger.debug(f"[CONTENT_PARSE] 已转换为 list: items={len(content)}")
            except Exception as e:
                logger.warning(f"[CONTENT_PARSE] 转换为 list 失败: {e}")
                return "[内容解析失败]"
            
            text_parts = []
            text_item_count = 0
            image_count = 0
            skipped_count = 0
            unknown_types = []
            samples = []
            
            for idx, item in enumerate(content):
                if not isinstance(item, dict):
                    skipped_count += 1
                    if len(samples) < 3:
                        samples.append(f"skip[{idx}]={type(item).__name__}")
                    continue
                
                item_type = str(item.get("type", "") or "").strip()
                
                if item_type == "text":
                    text_content = str(item.get("text", "") or "")
                    text_parts.append(text_content)
                    text_item_count += 1
                    if len(samples) < 3:
                        preview = (
                            repr(text_content[:40])
                            if len(text_content) > 40
                            else repr(text_content)
                        )
                        samples.append(f"text[{idx}]={preview}")
                
                elif item_type == "image_url":
                    image_count += 1
                    text_parts.append(f"[图片{image_count}]")
                    image_url_obj = item.get("image_url", {})
                    url_text = (
                        str(image_url_obj.get("url", ""))
                        if isinstance(image_url_obj, dict)
                        else str(image_url_obj)
                    )
                    url_preview = "[data_uri]" if "base64" in url_text[:50] else url_text[:50]
                    if len(samples) < 3:
                        samples.append(f"image[{idx}]={url_preview}")
                
                else:
                    unknown_types.append(item_type or "<empty>")
                    if len(samples) < 3:
                        samples.append(f"unknown[{idx}]={item_type or '<empty>'}")
            
            result = " ".join(text_parts)
            extras = []
            if skipped_count:
                extras.append(f"skipped={skipped_count}")
            if unknown_types:
                unknown_preview = ", ".join(sorted(set(unknown_types))[:3])
                extras.append(f"unknown={len(unknown_types)}[{unknown_preview}]")
            sample_summary = "; ".join(samples) if samples else "none"
            extra_text = f", {', '.join(extras)}" if extras else ""
            logger.debug(
                "[CONTENT_PARSE] 多模态解析完成: "
                f"text_items={text_item_count}, images={image_count}, "
                f"result_len={len(result)}, samples={sample_summary}{extra_text}"
            )
            return result
        
        logger.warning(f"[CONTENT_PARSE] ⚠️ 未知内容类型: {content_type}，返回占位符")
        return "[内容格式不支持]"

    def _build_prompt_from_messages(self, messages: List[Dict]) -> str:
        """从消息列表构建发送给网页的文本"""
        prompt_parts = []
        
        for m in messages:
            role = m.get('role', 'user')
            content = m.get('content', '')
            text = self._extract_text_from_content(content)
            
            if text:
                prompt_parts.append(f"{role}: {text}")
        
        return "\n\n".join(prompt_parts)
    def _get_upload_history_images_flag(self, default: bool = True) -> bool:
        """
        获取是否上传历史对话图片的开关。
        优先级：
        1) BrowserConstants.UPLOAD_HISTORY_IMAGES（若存在）
        2) config/browser_config.json 顶层键 UPLOAD_HISTORY_IMAGES（兜底）
        3) default
        """
        # 1) BrowserConstants
        try:
            v = getattr(BrowserConstants, "UPLOAD_HISTORY_IMAGES")
            # 允许 v 是 bool/int/str
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "y", "on")
        except Exception:
            pass

        # 2) config 文件兜底
        try:
            cfg_path = "config/browser_config.json"
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                if "UPLOAD_HISTORY_IMAGES" in data:
                    vv = data.get("UPLOAD_HISTORY_IMAGES")
                    if isinstance(vv, bool):
                        return vv
                    if isinstance(vv, (int, float)):
                        return bool(vv)
                    if isinstance(vv, str):
                        return vv.strip().lower() in ("1", "true", "yes", "y", "on")
        except Exception as e:
            logger.debug(f"[IMAGE] 读取 browser_config.json 兜底失败: {e}")

        return default
    def set_stop_checker(self, checker: Callable[[], bool]):
        """设置停止检查器"""
        self._should_stop_checker = checker or (lambda: False)

    def _get_request_cancel_reason(self, task_id: str = "") -> str:
        task = str(task_id or "").strip()
        if not task:
            return ""
        try:
            from app.services.request_manager import request_manager

            ctx = request_manager.get_request(task)
            if ctx is None:
                return ""
            return str(getattr(ctx, "cancel_reason", "") or "").strip().lower()
        except Exception:
            return ""

    def _should_rollback_request_count_on_cancel(self, task_id: str = "") -> bool:
        reason = self._get_request_cancel_reason(task_id)
        if not reason:
            return False
        manual_reasons = {
            "manual",
            "manual_terminate",
            "user_cancel",
            "user_cancelled",
            "cancel_button",
        }
        return reason in manual_reasons

    def _build_task_ownership_stop_checker(
        self,
        session: Optional[TabSession],
        task_id: str,
        base_checker: Optional[Callable[[], bool]] = None,
    ) -> Callable[[], bool]:
        expected_task_id = str(task_id or "").strip()
        base = base_checker or self._should_stop_checker
        ownership_lost_logged = False

        def _checker() -> bool:
            nonlocal ownership_lost_logged
            if base():
                return True
            if not session or not expected_task_id:
                return False

            try:
                current_task_id = str(getattr(session, "current_task_id", "") or "").strip()
                session_status = getattr(getattr(session, "status", None), "value", "")
            except Exception:
                current_task_id = ""
                session_status = ""

            ownership_lost = False
            detail = ""
            if current_task_id and current_task_id != expected_task_id:
                ownership_lost = True
                detail = f"current_task={current_task_id}"
            elif session_status in {"error", "closed"}:
                ownership_lost = True
                detail = f"status={session_status}"
            elif not current_task_id:
                ownership_lost = True
                detail = f"missing_task_id,status={session_status or 'unknown'}"

            if ownership_lost:
                if not ownership_lost_logged:
                    self._cancel_request_due_to_ownership_loss(
                        expected_task_id,
                        session,
                        detail=detail,
                    )
                    logger.warning(
                        f"[{session.id}] 检测到工作流所有权丢失，停止当前任务 "
                        f"(expected_task={expected_task_id}, {detail})"
                    )
                    ownership_lost_logged = True
                return True

            return False

        return _checker

    def _cancel_request_due_to_ownership_loss(
        self,
        task_id: str,
        session: Optional[TabSession],
        detail: str = "",
    ) -> None:
        request_id = str(task_id or "").strip()
        if session is not None:
            try:
                setattr(session, "_workflow_stop_reason", "ownership_lost")
            except Exception:
                pass
        if not request_id:
            return

        try:
            from app.services.request_manager import request_manager
            cancelled = bool(request_manager.cancel_request(request_id, "task_ownership_lost"))
            logger.warning(
                f"[{getattr(session, 'id', '-')}] 所有权丢失后取消请求: "
                f"request={request_id}, cancelled={cancelled}, detail={detail or '-'}, "
                f"current_task={str(getattr(session, 'current_task_id', '') or '').strip() or '-'}, "
                f"bound_req={str(getattr(session, '_bound_request_id', '') or '').strip() or '-'}, "
                f"status={getattr(getattr(session, 'status', None), 'value', '') or '-'}"
            )
        except Exception as e:
            logger.debug(
                f"[{getattr(session, 'id', '-')}] 所有权丢失后取消请求失败（忽略）: {e}"
                + (f" ({detail})" if detail else "")
            )

    def _release_workflow_session(
        self,
        session: TabSession,
        *,
        effective_stop_checker: Optional[Callable[[], bool]] = None,
        task_id: str = "",
    ):
        expected_task_id = str(task_id or "").strip()
        current_task_id = str(getattr(session, "current_task_id", "") or "").strip()
        session_status = getattr(getattr(session, "status", None), "value", "")
        if expected_task_id:
            if current_task_id and current_task_id != expected_task_id:
                self._cancel_request_due_to_ownership_loss(
                    expected_task_id,
                    session,
                    detail=f"current_task={current_task_id}",
                )
                logger.warning(
                    f"[{session.id}] 跳过释放：标签页已被其他任务接管 "
                    f"(expected_task={expected_task_id}, current_task={current_task_id})"
                )
                return
            if not current_task_id:
                self._cancel_request_due_to_ownership_loss(
                    expected_task_id,
                    session,
                    detail=f"missing_task_id,status={session_status or 'unknown'}",
                )
                logger.warning(
                    f"[{session.id}] 跳过释放：标签页 task_id 已丢失 "
                    f"(expected_task={expected_task_id}, status={session_status or 'unknown'})"
                )
                return

        cancelled = bool(effective_stop_checker and effective_stop_checker())
        rollback_request_count = cancelled and self._should_rollback_request_count_on_cancel(task_id)
        if cancelled and not rollback_request_count:
            logger.debug(
                f"[{session.id}] stop detected but request_count preserved "
                f"(task={task_id or '-'}, reason={self._get_request_cancel_reason(task_id) or 'unknown'})"
            )

        logger.debug(
            f"[{session.id}] 工作流释放请求: expected_task={expected_task_id or '-'}, "
            f"current_task={current_task_id or '-'}, session_status={session_status or '-'}, "
            f"cancelled={cancelled}, rollback_request_count={rollback_request_count}, "
            f"bound_req={str(getattr(session, '_bound_request_id', '') or '').strip() or '-'}"
        )

        self.tab_pool.release(
            session.id,
            check_triggers=not rollback_request_count,
            rollback_request_count=rollback_request_count,
            expected_task_id=expected_task_id,
        )
    
    @property
    def tab_pool(self) -> TabPoolManager:
        """获取标签页池（延迟初始化 + 线程安全）"""
        if self._tab_pool is None:
            with self._lock:  # 使用类级别的锁
                if self._tab_pool is None:  # 双重检查
                    if not self.ensure_connection():
                        raise BrowserConnectionError("无法连接到浏览器")
                
                    pool_config = _load_tab_pool_config()
                    self._tab_pool = TabPoolManager(
                        browser_page=self.get_browser_handle(),
                        **pool_config
                    )
                    self._tab_pool.initialize()
    
        return self._tab_pool
    
    def _get_config_engine(self):
        if self.config_engine is None:
            from app.services.config_engine import config_engine
            self.config_engine = config_engine
        return self.config_engine
    
    def _connect(self) -> bool:
        try:
            logger.debug(f"连接浏览器 127.0.0.1:{self.port}")
            opts = ChromiumOptions()
            opts.set_address(f"127.0.0.1:{self.port}")
            opts.existing_only()
            self.browser_handle = Chromium(addr_or_opts=opts)
            try:
                self.page = self.browser_handle.latest_tab
            except Exception:
                self.page = None
            self._connected = True
            self._start_connection_watchdog()
            logger.info("浏览器连接成功")
            return True
        except Exception as e:
            logger.error(f"浏览器连接失败: {e}")
            self.browser_handle = None
            self.page = None
            self._connected = False
            return False

    def _is_debug_port_open(self, timeout: float = 0.4) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(self.port)), timeout=float(timeout)):
                self._last_debug_port_error = ""
                return True
        except Exception as e:
            self._last_debug_port_error = str(e)
            return False

    def _probe_browser_connection_via_cdp(self) -> bool:
        """Prefer the long-lived CDP session over spawning /json short connections."""
        browser = self.get_browser_handle()
        if not browser or not hasattr(browser, "_run_cdp"):
            return False

        try:
            result = browser._run_cdp("Target.getTargets") or {}
            target_infos = result.get("targetInfos")
            return isinstance(target_infos, list)
        except Exception:
            return False

    def _list_tabs_via_cdp(self) -> List[Any]:
        browser = self.get_browser_handle()
        if not browser or not hasattr(browser, "_run_cdp"):
            return []

        try:
            result = browser._run_cdp("Target.getTargets") or {}
            target_infos = result.get("targetInfos") or []
        except Exception:
            return []

        tabs: List[Any] = []
        for info in target_infos:
            if not isinstance(info, dict):
                continue
            if str(info.get("type") or "").strip().lower() != "page":
                continue
            target_id = str(info.get("targetId") or "").strip()
            if not target_id:
                continue
            try:
                resolved = browser.get_tab(target_id)
            except Exception:
                resolved = None
            tabs.append(resolved or target_id)
        return tabs

    def _log_get_tabs_fallback(self, message: str, error: Any = None) -> None:
        now = time.time()
        if now - self._last_get_tabs_fallback_log_at < 10.0:
            return
        self._last_get_tabs_fallback_log_at = now
        if _looks_like_transient_local_debug_error(error):
            logger.debug(message)
        else:
            logger.warning(message)

    def _probe_browser_connection(self) -> bool:
        """Lightweight liveness probe for the underlying browser session."""
        return self._probe_browser_connection_via_cdp()

    def get_browser_handle(self):
        if self.browser_handle is not None:
            return self.browser_handle
        browser = getattr(self.page, "browser", None)
        if browser is not None:
            self.browser_handle = browser
            return browser
        return self.page

    def get_tabs(self):
        browser = self.get_browser_handle()
        if browser is None:
            raise BrowserConnectionError("无法连接到浏览器")
        cdp_tabs = self._list_tabs_via_cdp()
        if cdp_tabs:
            self._get_tabs_retry_after = 0.0
            return cdp_tabs
        now = time.time()
        if now < self._get_tabs_retry_after:
            return []
        try:
            tabs = browser.get_tabs()
            self._get_tabs_retry_after = 0.0
            return tabs
        except Exception as e:
            self._get_tabs_retry_after = max(self._get_tabs_retry_after, time.time() + 5.0)
            fallback_tabs = self._list_tabs_via_cdp()
            if fallback_tabs:
                self._log_get_tabs_fallback(
                    f"[BrowserCore] browser.get_tabs() 失败，回退到 Target.getTargets: {e}",
                    error=e,
                )
                return fallback_tabs
            raise

    def get_tab_ids(self) -> List[str]:
        browser = self.get_browser_handle()
        if browser is None:
            raise BrowserConnectionError("无法连接到浏览器")
        return list(getattr(browser, "tab_ids", []) or [])

    def get_tab(self, id_or_num=None):
        browser = self.get_browser_handle()
        if browser is None:
            raise BrowserConnectionError("无法连接到浏览器")
        return browser.get_tab(id_or_num)

    def get_latest_tab(self):
        browser = self.get_browser_handle()
        if browser is None:
            raise BrowserConnectionError("无法连接到浏览器")
        return browser.latest_tab

    def _get_watchdog_tab_snapshot(self) -> str:
        try:
            if not self._tab_pool:
                return "tab_pool=uninitialized"
            status = self._tab_pool.get_status()
            total = int(status.get("total", 0) or 0)
            idle = int(status.get("idle", 0) or 0)
            busy = int(status.get("busy", 0) or 0)
            tabs = status.get("tabs") or []
            labels = []
            for item in tabs[:4]:
                if not isinstance(item, dict):
                    continue
                labels.append(
                    f"{item.get('id', '?')}:{item.get('status', '?')}:{'iso' if item.get('is_isolated_context') else 'shared'}"
                )
            extra = f", tabs=[{', '.join(labels)}]" if labels else ""
            return f"tab_pool(total={total}, idle={idle}, busy={busy}{extra})"
        except Exception as e:
            return f"tab_pool=error({e})"

    def _connection_watchdog_loop(self) -> None:
        while not self._watchdog_stop.wait(1.0):
            port_open = self._is_debug_port_open()
            session_alive = self._probe_browser_connection()
            health = "alive" if session_alive else ("port_only" if port_open else "dead")

            if health == self._last_watchdog_health:
                continue

            snapshot = self._get_watchdog_tab_snapshot()
            if health == "alive":
                if port_open:
                    logger.info(
                        f"[BrowserWatchdog] 受控浏览器连接正常 (port={self.port}, session=alive, {snapshot})"
                    )
                elif _looks_like_transient_local_debug_error(self._last_debug_port_error):
                    logger.debug(
                        f"[BrowserWatchdog] 长连接正常，已忽略瞬时调试端口探测失败 "
                        f"(port={self.port}, session=alive, {snapshot})"
                    )
                else:
                    logger.info(
                        f"[BrowserWatchdog] 受控浏览器连接正常 "
                        f"(port={self.port}, session=alive, port_probe=failed, {snapshot})"
                    )
            elif health == "port_only":
                logger.warning(
                    f"[BrowserWatchdog] 调试端口可达，但当前页面会话已失效 "
                    f"(port={self.port}, session=dead, connected={self._connected}, {snapshot})"
                )
            else:
                detail = ""
                if self._last_debug_port_error and not _looks_like_transient_local_debug_error(self._last_debug_port_error):
                    detail = f", probe_error={self._last_debug_port_error}"
                logger.error(
                    f"[BrowserWatchdog] 受控浏览器调试端口不可达 "
                    f"(port={self.port}, connected={self._connected}, {snapshot}{detail})"
                )

            self._last_watchdog_port_open = port_open
            self._last_watchdog_session_alive = session_alive
            self._last_watchdog_health = health

    def _start_connection_watchdog(self) -> None:
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return

        self._watchdog_stop.clear()
        self._last_watchdog_port_open = None
        self._last_watchdog_session_alive = None
        self._last_watchdog_health = None
        self._watchdog_thread = threading.Thread(
            target=self._connection_watchdog_loop,
            daemon=True,
            name="browser-connection-watchdog",
        )
        self._watchdog_thread.start()
    
    def health_check(self) -> Dict[str, Any]:
        result = {
            "status": "unhealthy",
            "connected": False,
            "port": self.port,
            "tab_pool": None,
            "error": None
        }
        
        try:
            if not self.get_browser_handle():
                if not self._connect():
                    result["error"] = "无法连接到浏览器"
                    return result
            elif not self._probe_browser_connection():
                self._connected = False
                self.browser_handle = None
                self.page = None
                if not self._connect():
                    result["error"] = "无法连接到浏览器"
                    return result
            
            result["status"] = "healthy"
            result["connected"] = True
            
            # v2.0: 返回标签页池状态
            if self._tab_pool:
                result["tab_pool"] = self._tab_pool.get_status()
        
        except Exception as e:
            result["error"] = str(e)
            self._connected = False
        
        return result
    
    def ensure_connection(self) -> bool:
        if self._connected:
            if self._probe_browser_connection():
                return True
            self._connected = False
            self.browser_handle = None
            self.page = None
        
        return self._connect()
    
    def get_active_tab(self):
        """
        获取一个可用的标签页（兼容旧接口）
        
        注意：新代码应使用 execute_workflow_with_session
        """
        # 生成临时任务 ID
        task_id = f"legacy_{int(time.time() * 1000)}"
        session = self.tab_pool.acquire(task_id, timeout=30)
        if session is None:
            raise BrowserConnectionError("无法获取可用标签页")
        return session.tab
    @contextlib.contextmanager
    def get_temporary_tab(self, timeout: int = 30):
        """
        获取临时标签页的上下文管理器（推荐使用）
    
        使用方式:
            with browser.get_temporary_tab() as tab:
                elements = tab.eles(selector)
            # 退出 with 块后自动释放
    
        Args:
            timeout: 获取标签页的超时时间（秒）
    
        Yields:
            tab: 浏览器标签页对象
    
        Raises:
            BrowserConnectionError: 无法获取可用标签页时抛出
        """
        task_id = f"temp_{int(time.time() * 1000)}"
        session = None
    
        try:
            session = self.tab_pool.acquire(task_id, timeout=timeout)
        
            if session is None:
                raise BrowserConnectionError("无法获取可用标签页，服务繁忙请稍后重试")
        
            logger.debug(f"[{session.id}] 临时标签页已分配")
            yield session.tab
        
        finally:
            if session is not None:
                self.tab_pool.release(session.id)
                logger.debug(f"[{session.id}] 临时标签页已释放")    
    def execute_workflow(
        self, 
        messages: List[Dict],
        stream: bool = True,
        task_id: str = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
    ) -> Generator[str, None, None]:
        """
        工作流执行入口（v2.0 改进版）
        
        改动：
        - 自动从池中获取标签页
        - 执行完自动释放
        """
        # 验证输入
        is_valid, error_msg, sanitized_messages = MessageValidator.validate(messages)
        
        if not is_valid:
            yield self.formatter.pack_error(
                f"无效请求: {error_msg}",
                error_type="invalid_request_error",
                code="invalid_messages"
            )
            return
        
        # 生成任务 ID（如果没有提供）
        if task_id is None:
            task_id = f"task_{int(time.time() * 1000)}"
        effective_stop_checker = stop_checker or self._should_stop_checker
        
        # 从池中获取标签页
        session = None
        try:
            session = self.tab_pool.acquire(task_id, timeout=60)
            
            if session is None:
                yield self.formatter.pack_error(
                    "服务繁忙，请稍后重试",
                    error_type="capacity_error",
                    code="no_available_tab"
                )
                yield self.formatter.pack_finish()
                return

            self._bind_request_tab_id(task_id, session)
            effective_stop_checker = self._build_task_ownership_stop_checker(
                session,
                task_id,
                effective_stop_checker,
            )
             
            # 执行工作流
            if stream:
                yield from self._execute_workflow_stream(
                    session,
                    sanitized_messages,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                )
            else:
                yield from self._execute_workflow_non_stream(
                    session,
                    sanitized_messages,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                )
        
        finally:
            # 释放标签页
            if session:
                self._release_workflow_session(
                    session,
                    effective_stop_checker=effective_stop_checker,
                    task_id=task_id,
                )
                try:
                    from app.services.command_engine import command_engine
                    command_engine.schedule_deferred_workflow_commands(session, delay_sec=0.25)
                except Exception:
                    pass

    def execute_workflow_for_tab_index(
        self, 
        tab_index: int,
        messages: List[Dict],
        stream: bool = True,
        task_id: str = None,
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
    ) -> Generator[str, None, None]:
        """
        使用指定编号的标签页执行工作流
        
        Args:
            tab_index: 持久化标签页编号（1, 2, 3...）
            messages: 消息列表
            stream: 是否流式输出
            task_id: 任务 ID
        """
        # 验证输入
        is_valid, error_msg, sanitized_messages = MessageValidator.validate(messages)
        
        if not is_valid:
            yield self.formatter.pack_error(
                f"无效请求: {error_msg}",
                error_type="invalid_request_error",
                code="invalid_messages"
            )
            return
        
        # 生成任务 ID
        if task_id is None:
            task_id = f"tab{tab_index}_{int(time.time() * 1000)}"
        effective_stop_checker = stop_checker or self._should_stop_checker
        
        # 按编号获取标签页
        session = None
        try:
            session = self.tab_pool.acquire_by_index(tab_index, task_id, timeout=60)
            
            if session is None:
                yield self.formatter.pack_error(
                    f"标签页 #{tab_index} 不可用或不存在",
                    error_type="not_found_error",
                    code="tab_not_found"
                )
                yield self.formatter.pack_finish()
                return

            self._bind_request_tab_id(task_id, session)
            effective_stop_checker = self._build_task_ownership_stop_checker(
                session,
                task_id,
                effective_stop_checker,
            )
             
            # 执行工作流
            if stream:
                yield from self._execute_workflow_stream(
                    session,
                    sanitized_messages,
                    preset_name=preset_name,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                )
            else:
                yield from self._execute_workflow_non_stream(
                    session,
                    sanitized_messages,
                    preset_name=preset_name,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                )
        
        finally:
            if session:
                self._release_workflow_session(
                    session,
                    effective_stop_checker=effective_stop_checker,
                    task_id=task_id,
                )
                try:
                    from app.services.command_engine import command_engine
                    command_engine.schedule_deferred_workflow_commands(session, delay_sec=0.25)
                except Exception:
                    pass

    def execute_workflow_for_route_domain(
        self,
        route_domain: str,
        messages: List[Dict],
        stream: bool = True,
        task_id: str = None,
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
    ) -> Generator[str, None, None]:
        """
        使用指定域名路由匹配的标签页执行工作流。

        Args:
            route_domain: 域名路由（例如 gemini.com）
            messages: 消息列表
            stream: 是否流式输出
            task_id: 任务 ID
        """
        is_valid, error_msg, sanitized_messages = MessageValidator.validate(messages)

        if not is_valid:
            yield self.formatter.pack_error(
                f"无效请求: {error_msg}",
                error_type="invalid_request_error",
                code="invalid_messages"
            )
            return

        normalized_route_domain = str(route_domain or "").strip()
        if not normalized_route_domain:
            yield self.formatter.pack_error(
                "域名路由不能为空",
                error_type="invalid_request_error",
                code="invalid_route_domain"
            )
            return

        if task_id is None:
            safe_route_key = normalized_route_domain.replace(".", "_")
            task_id = f"url_{safe_route_key}_{int(time.time() * 1000)}"
        effective_stop_checker = stop_checker or self._should_stop_checker

        session = None
        try:
            session = self.tab_pool.acquire_by_route_domain(normalized_route_domain, task_id, timeout=60)

            if session is None:
                yield self.formatter.pack_error(
                    f"域名路由 '{normalized_route_domain}' 没有可用标签页",
                    error_type="not_found_error",
                    code="route_domain_not_found"
                )
                yield self.formatter.pack_finish()
                return

            self._bind_request_tab_id(task_id, session)
            effective_stop_checker = self._build_task_ownership_stop_checker(
                session,
                task_id,
                effective_stop_checker,
            )

            if stream:
                yield from self._execute_workflow_stream(
                    session,
                    sanitized_messages,
                    preset_name=preset_name,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                )
            else:
                yield from self._execute_workflow_non_stream(
                    session,
                    sanitized_messages,
                    preset_name=preset_name,
                    stop_checker=effective_stop_checker,
                    workflow_priority=workflow_priority,
                )

        finally:
            if session:
                self._release_workflow_session(
                    session,
                    effective_stop_checker=effective_stop_checker,
                    task_id=task_id,
                )
                try:
                    from app.services.command_engine import command_engine
                    command_engine.schedule_deferred_workflow_commands(session, delay_sec=0.25)
                except Exception:
                    pass

    def _bind_request_tab_id(self, task_id: str, session: Optional[TabSession]):
        if not session:
            return
        request_id = str(task_id or "").strip()
        if not request_id:
            return
        try:
            setattr(session, "_bound_request_id", request_id)
            from app.services.request_manager import request_manager
            bind_ok = bool(request_manager.bind_tab(request_id, session.id))
            logger.debug(
                f"[{session.id}] 绑定请求标签页: request={request_id}, "
                f"bind_ok={bind_ok}, current_task={str(getattr(session, 'current_task_id', '') or '').strip() or '-'}, "
                f"status={getattr(getattr(session, 'status', None), 'value', '') or '-'}, "
                f"idx=#{getattr(session, 'persistent_index', 0) or '-'}"
            )
        except Exception as e:
            logger.debug(f"[{session.id}] 绑定请求标签页失败（忽略）: {e}")
   
    def _execute_workflow_stream(
        self,
        session: TabSession,
        messages: List[Dict],
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
    ) -> Generator[str, None, None]:
        max_terminal_retries = 1
        attempt = 0

        while True:
            stream = self._execute_workflow_stream_once(
                session,
                messages,
                preset_name=preset_name,
                stop_checker=stop_checker,
                workflow_priority=workflow_priority,
            )
            saw_content = False
            retry_requested = False

            try:
                for chunk in stream:
                    if self._chunk_has_stream_content(chunk):
                        saw_content = True

                    is_terminal_error = self._is_retriable_stream_terminal_error_chunk(chunk)
                    if (
                        not saw_content
                        and attempt < max_terminal_retries
                        and is_terminal_error
                        and not (stop_checker or self._should_stop_checker)()
                    ):
                        retry_requested = True
                        logger.warning(
                            self._build_stream_terminal_alert_message(
                                session.id,
                                chunk,
                                retrying=True,
                                attempt=attempt + 1,
                                max_attempts=max_terminal_retries,
                            )
                        )
                        break

                    if is_terminal_error:
                        logger.error(
                            self._build_stream_terminal_alert_message(
                                session.id,
                                chunk,
                                retrying=False,
                                saw_content=saw_content,
                            )
                        )
                        self._emit_stream_terminal_alert_event(
                            session,
                            chunk,
                            saw_content=saw_content,
                        )

                    yield chunk
            finally:
                with contextlib.suppress(Exception):
                    stream.close()

            if not retry_requested:
                return

            attempt += 1
            setattr(session, "_workflow_stop_reason", None)
            setattr(session, "_workflow_user_stop_logged", False)
            time.sleep(0.5)

    @staticmethod
    def _extract_stream_error_payload(chunk: str) -> Optional[Dict]:
        if not isinstance(chunk, str) or not chunk.startswith("data: "):
            return None
        data_str = chunk[6:].strip()
        if not data_str or data_str == "[DONE]":
            return None
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            return None
        error = payload.get("error")
        return error if isinstance(error, dict) else None

    @classmethod
    def _is_retriable_stream_terminal_error_chunk(cls, chunk: str) -> bool:
        error = cls._extract_stream_error_payload(chunk)
        if not error:
            return False
        message = str(error.get("message") or "").strip().lower()
        return "stream_terminal_error:" in message

    @classmethod
    def _get_stream_terminal_error_detail(cls, chunk: str) -> str:
        error = cls._extract_stream_error_payload(chunk)
        if not error:
            return ""

        message = " ".join(str(error.get("message") or "").split())
        if not message:
            return ""

        marker = "stream_terminal_error:"
        lowered = message.lower()
        marker_index = lowered.find(marker)
        if marker_index >= 0:
            detail = message[marker_index + len(marker):].strip()
            return detail or message

        return message

    @classmethod
    def _summarize_stream_terminal_alert(
        cls,
        chunk: str,
        *,
        retrying: bool,
        saw_content: bool = False,
        attempt: int = 0,
        max_attempts: int = 0,
    ) -> str:
        detail = cls._get_stream_terminal_error_detail(chunk) or "unknown stream terminal error"
        lowered = detail.lower()
        category = "限流终止" if ("too many requests" in lowered or "429" in lowered) else "异常终止"

        if retrying:
            return (
                f"目标流告警：检测到{category}（{detail}），"
                f"自动重试工作流 ({attempt}/{max_attempts})"
            )

        suffix = "当前工作流将报错结束（已有部分输出）" if saw_content else "当前工作流将报错结束"
        return f"目标流告警：检测到{category}（{detail}），{suffix}"

    @classmethod
    def _build_stream_terminal_alert_message(
        cls,
        session_id: str,
        chunk: str,
        *,
        retrying: bool,
        saw_content: bool = False,
        attempt: int = 0,
        max_attempts: int = 0,
    ) -> str:
        summary = cls._summarize_stream_terminal_alert(
            chunk,
            retrying=retrying,
            saw_content=saw_content,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        return f"[ALERT][{session_id}] {summary}"

    def _emit_stream_terminal_alert_event(
        self,
        session: TabSession,
        chunk: str,
        *,
        saw_content: bool = False,
    ) -> None:
        summary = self._summarize_stream_terminal_alert(
            chunk,
            retrying=False,
            saw_content=saw_content,
        )
        detail = self._get_stream_terminal_error_detail(chunk)
        if not summary:
            return

        try:
            from app.services.command_engine import command_engine

            command_engine.emit_external_command_result_event(
                session,
                source_command_id="evt_stream_terminal_error",
                source_command_name="ARENA_STREAM_TERMINAL_ALERT",
                summary=summary,
                result=detail or summary,
                informative=True,
                mode="external_alert",
                group_name="arena_commands",
            )
        except Exception as e:
            logger.debug(f"[{session.id}] stream terminal alert event skipped: {e}")

    @staticmethod
    def _chunk_has_stream_content(chunk: str) -> bool:
        if not isinstance(chunk, str) or not chunk.startswith("data: "):
            return False
        data_str = chunk[6:].strip()
        if not data_str or data_str == "[DONE]":
            return False
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            return False
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return False
        delta = choices[0].get("delta", {}) if isinstance(choices[0], dict) else {}
        content = delta.get("content", "") if isinstance(delta, dict) else ""
        return bool(content)

    def _execute_workflow_stream_once(
        self,
        session: TabSession,
        messages: List[Dict],
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
    ) -> Generator[str, None, None]:
        """流式工作流执行（v2.0）"""
    
        tab = session.tab
        effective_stop_checker = stop_checker or self._should_stop_checker
        workflow_priority_value = 2
        workflow_runtime = None
        workflow_aborted = False
        workflow_abort_message = ""
        command_engine = None
        try:
            from app.services.command_engine import command_engine as _command_engine
            command_engine = _command_engine
            workflow_priority_value = command_engine._normalize_priority(
                workflow_priority, command_engine._get_request_priority_baseline()
            )
        except Exception:
            workflow_priority_value = 2
    
        if effective_stop_checker():
            yield self.formatter.pack_error("请求已取消", code="cancelled")
            yield self.formatter.pack_finish()
            return
    
        # ===== 增强的 URL 检查（替换原来的 try-except）=====
        # 1. 先检查标签页基本有效性
        try:
            url = tab.url
        except Exception as e:
            logger.warning(f"[{session.id}] 标签页访问失败: {e}")
            session.mark_error("tab_access_failed")
            yield self.formatter.pack_error(
                "标签页已关闭或失效，请刷新页面后重试",
                code="tab_closed"
            )
            yield self.formatter.pack_finish()
            return
    
        # 2. 检查 URL 有效性
        if not url:
            yield self.formatter.pack_error(
                "请先打开目标AI网站",
                code="no_page"
            )
            yield self.formatter.pack_finish()
            return
    
        invalid_urls = ("about:blank", "chrome://newtab/", "chrome://new-tab-page/")
        if url in invalid_urls:
            yield self.formatter.pack_error(
                "当前是空白页，请先打开目标AI网站",
                code="blank_page"
            )
            yield self.formatter.pack_finish()
            return
    
        if "chrome-error://" in url or "about:neterror" in url:
            yield self.formatter.pack_error(
                "页面加载错误，请刷新后重试",
                code="page_error"
            )
            yield self.formatter.pack_finish()
            return
    
        # 3. 只允许真实远程站点，拒绝本地链接和内网地址。
        try:
            domain = extract_remote_site_domain(url)
            if not domain:
                raise ValueError(f"not a remote site url: {url}")
            session.current_domain = domain
        except Exception as e:
            logger.warning(f"[{session.id}] URL 解析失败: {url}, 错误: {e}")
            yield self.formatter.pack_error(
                "当前页面不是可解析的网站，请打开真实的远程站点页面后再试",
                code="invalid_url"
            )
            yield self.formatter.pack_finish()
            return
        # ===== 增强的 URL 检查结束 =====
    
        logger.debug(f"[{session.id}] 域名: {domain}")
        
        page_status = self._check_page_status(tab)
        if not page_status["ready"]:
            yield self.formatter.pack_error(
                f"页面未就绪: {page_status['reason']}",
                code="page_not_ready"
            )
            yield self.formatter.pack_finish()
            return
        
        config_engine = self._get_config_engine()
        effective_preset_name = preset_name if preset_name is not None else session.preset_name
        resolved_preset_name = effective_preset_name or config_engine.get_default_preset(domain) or "主预设"
        site_config = config_engine.get_site_config(domain, tab.html, preset_name=effective_preset_name)
        if not site_config:
            yield self.formatter.pack_error(
                "配置加载失败",
                code="config_error"
            )
            yield self.formatter.pack_finish()
            return
        
        selectors = site_config.get("selectors", {})
        workflow = site_config.get("workflow", [])
        stealth_mode = site_config.get("stealth", False)
        
        image_config = site_config.get("image_extraction", {})
        modalities = image_config.get("modalities") or {}
        image_extraction_enabled = bool(image_config.get("enabled", False)) or any(
            bool(modalities.get(key)) for key in ("image", "audio", "video")
        )
        stream_config = site_config.get("stream_config", {}) or {}
        file_paste_config = site_config.get("file_paste", {}) or {}

        # 🆕 提取用户发送的图片：可配置是否包含历史对话图片
        upload_history = self._get_upload_history_images_flag(default=True)
        logger.debug(f"图片历史上传: {upload_history}")
        image_source_messages = messages
        if not upload_history:
            # 只取最后一条 user 消息的图片
            last_user = None
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user = m
                    break
            image_source_messages = [last_user] if last_user else []

        logger.debug(f"图片源消息数: {len(image_source_messages)}/{len(messages)}")
        user_images = extract_images_from_messages(image_source_messages)

        # 🆕 如果消息结构里声明了图片，但实际没拿到任何可用图片，直接报错
        has_declared_image = False
        try:
            for mm in image_source_messages:
                c = mm.get("content")
                # content 可能是字符串形式的 list，这里只做“粗略包含”判断即可
                if isinstance(c, str):
                    if '"type"' in c and "image_url" in c:
                        has_declared_image = True
                        break
                elif isinstance(c, (list, tuple)):
                    for it in c:
                        if isinstance(it, dict) and it.get("type") == "image_url":
                            has_declared_image = True
                            break
                    if has_declared_image:
                        break
        except Exception:
            pass

        if has_declared_image and not user_images:
            # 上游声明了图片，但我们没有拿到任何可用图片：这里仅记录警告，继续走纯文本流程
            logger.warning(
                "收到图片占位符但没有实际图片数据：image_url.url 为空或无效，"
                "已自动忽略图片并继续执行纯文本对话。"
            )
        
        context = {
            "prompt": self._build_prompt_from_messages(messages),
            "images": user_images
        }
        
        extractor = config_engine.get_site_extractor(domain, preset_name=effective_preset_name)
        logger.debug(f"[{session.id}] 使用提取器: {extractor.get_id()} [预设: {resolved_preset_name}]")

        if command_engine is not None:
            try:
                workflow_runtime = command_engine.begin_workflow_runtime(
                    session,
                    task_id=str(getattr(session, "current_task_id", "") or ""),
                    preset_name=resolved_preset_name,
                    priority=workflow_priority_value,
                )
            except Exception as e:
                logger.debug(f"[{session.id}] 工作流运行时注册失败（忽略）: {e}")

        def _combined_stop_checker() -> bool:
            if effective_stop_checker():
                return True
            if command_engine is not None and command_engine.workflow_interrupt_requested(session):
                setattr(session, "_workflow_stop_reason", "command_interrupt")
                return True
            return False
        
        # 创建执行器
        executor = WorkflowExecutor(
            tab=tab,
            stealth_mode=stealth_mode,
            should_stop_checker=_combined_stop_checker,
            extractor=extractor,
            image_config=image_config,
            stream_config=stream_config,
            file_paste_config=file_paste_config,
            selectors=selectors,
            session=session,
        )
        
        result_container_selector = selectors.get("result_container", "")
        setattr(session, "_workflow_stop_reason", None)
        if not effective_stop_checker():
            setattr(session, "_workflow_user_stop_logged", False)
        
        try:
            step_index = 0
            while step_index < len(workflow):
                step = workflow[step_index]
                if command_engine is not None:
                    command_engine.update_workflow_runtime_step(session, step_index, step)

                stop_reason = str(getattr(session, "_workflow_stop_reason", "") or "").strip()
                if stop_reason == "command_interrupt" or (
                    command_engine is not None and command_engine.workflow_interrupt_requested(session)
                ):
                    interrupt_result = (
                        command_engine.handle_pending_workflow_interrupts(session)
                        if command_engine is not None
                        else {"handled": False, "abort": False, "message": ""}
                    )
                    if interrupt_result.get("abort"):
                        workflow_aborted = True
                        workflow_abort_message = str(
                            interrupt_result.get("message") or "工作流已被命令打断"
                        )
                        logger.warning(
                            f"[{session.id}] 工作流被命令打断: "
                            f"{interrupt_result.get('abort_by') or 'unknown'}"
                        )
                        yield self.formatter.pack_error(
                            workflow_abort_message,
                            code="workflow_interrupted",
                        )
                        break
                    if interrupt_result.get("handled"):
                        logger.info(f"[{session.id}] 工作流恢复执行")
                        continue

                if effective_stop_checker():
                    if getattr(session, "_workflow_user_stop_logged", False):
                        break
                    if stop_reason == "timeout":
                        logger.warning(f"[{session.id}] 工作流因超时停止")
                    else:
                        logger.info(f"[{session.id}] 工作流被用户中断")
                    setattr(session, "_workflow_user_stop_logged", True)
                    break
                
                action = step.get('action', '')
                target_key = step.get('target', '')
                optional = step.get('optional', False)
                param_value = step.get('value')
                
                selector = selectors.get(target_key, '')
                
                if not selector and action not in ("WAIT", "KEY_PRESS", "COORD_CLICK", "COORD_SCROLL", "JS_EXEC"):
                    if optional:
                        step_index += 1
                        continue
                    else:
                        yield self.formatter.pack_error(
                            f"缺少配置: {target_key}",
                            code="missing_selector"
                        )
                        break
                
                try:
                    yield from executor.execute_step(
                        action=action,
                        selector=selector,
                        target_key=target_key,
                        value=param_value,
                        optional=optional,
                        context=context
                    )
                    
                    logger.debug(f"[PROBE] execute_step 完成: action={action}, target={target_key}")

                    if effective_stop_checker():
                        logger.info(f"[{session.id}] 步骤完成后检测到取消，提前结束工作流")
                        break
                    
                    if action in ("STREAM_WAIT", "STREAM_OUTPUT"):
                        result_container_selector = selector
                    step_index += 1
                        
                except (ElementNotFoundError, WorkflowError):
                    break
                except Exception as e:
                    if effective_stop_checker():
                        logger.info(f"[{session.id}] 取消后忽略步骤异常: {e}")
                        break
                    if not optional:
                        yield self.formatter.pack_error(f"执行中断: {str(e)}")
                        break
            
            if (
                not workflow_aborted
                and command_engine is not None
                and command_engine.workflow_interrupt_requested(session)
            ):
                interrupt_result = command_engine.handle_pending_workflow_interrupts(session)
                if interrupt_result.get("abort"):
                    workflow_aborted = True
                    workflow_abort_message = str(
                        interrupt_result.get("message") or "工作流已被命令打断"
                    )
                    logger.warning(
                        f"[{session.id}] 工作流收尾阶段被命令打断: "
                        f"{interrupt_result.get('abort_by') or 'unknown'}"
                    )
                    yield self.formatter.pack_error(
                        workflow_abort_message,
                        code="workflow_interrupted",
                    )
                elif interrupt_result.get("handled"):
                    logger.info(f"[{session.id}] 工作流收尾阶段已执行挂起命令")

            # 多模态提取
            logger.debug(f"[PROBE] Workflow 循环结束，image_enabled={image_extraction_enabled}, should_stop={effective_stop_checker()}")
            if image_extraction_enabled and not effective_stop_checker() and not workflow_aborted:
                logger.debug("[PROBE] 进入多模态提取分支")
                try:
                    media_items = self._extract_media_after_stream(
                        tab=tab,
                        extractor=extractor,
                        image_config=image_config,
                        result_selector=result_container_selector,
                        message_wrapper_selector=selectors.get("message_wrapper", ""),
                        completion_id=executor._completion_id,
                        stop_checker=_combined_stop_checker,
                        media_generation_state=getattr(executor, "_last_stream_media_state", None),
                    )
                    
                    if media_items:
                        download_urls = image_config.get("download_urls", False)
                        if download_urls:
                            image_items = [item for item in media_items if item.get("media_type") == "image"]
                            other_items = [item for item in media_items if item.get("media_type") != "image"]
                            image_items = self._download_url_images(image_items, tab=tab)
                            media_items = image_items + other_items

                        media_items = self._persist_remote_media_urls_to_local(
                            media_items,
                            tab=tab,
                            max_size_mb=int(image_config.get("max_size_mb", 10) or 10),
                        )
                        
                        logger.debug(f"[PROBE] 即将发送多模态资源（Markdown），数量={len(media_items)}")

                        try:
                            response_media = self._prepare_media_items_for_response(media_items)
                            md = self._build_media_markdown_block(media_items)
                            if md:
                                yield self.formatter.pack_chunk(
                                    md,
                                    completion_id=executor._completion_id,
                                    media=response_media,
                                )
                                logger.debug(f"[MD_MEDIA] 已发送结构化多模态资源，共 {len(media_items)} 项")
                            else:
                                yield self.formatter.pack_chunk(
                                    "",
                                    completion_id=executor._completion_id,
                                    media=response_media,
                                )
                                logger.debug(f"[MD_MEDIA] 已发送纯结构化多模态资源，共 {len(media_items)} 项")
                        except Exception as e:
                            logger.warning(f"[MD_MEDIA] 发送 Markdown 媒体链接失败: {e}")
                except Exception as e:
                    logger.warning(f"[{session.id}] 多模态提取失败: {e}")
        
        finally:
            if command_engine is not None and workflow_runtime is not None:
                try:
                    stop_reason = str(getattr(session, "_workflow_stop_reason", "") or "").strip()
                    externally_stopped = bool(effective_stop_checker()) and stop_reason != "command_interrupt"
                    command_engine.finish_workflow_runtime(
                        session,
                        aborted=workflow_aborted or bool(workflow_abort_message) or externally_stopped,
                    )
                except Exception as e:
                    logger.debug(f"[{session.id}] 工作流运行时清理失败（忽略）: {e}")
            yield self.formatter.pack_finish()
    
    def _get_pending_detection_markers(
        self,
        image_config: Dict,
        media_type: str,
        marker_key: str,
    ) -> List[str]:
        default_markers = {
            "audio": {
                "text_contains": [
                    "generated_music_content/",
                    "googleusercontent.com/generated_music_content/",
                ],
                "url_contains": [
                    "generated_music_content/",
                    "googleusercontent.com/generated_music_content/",
                ],
                "label_contains": [],
            },
            "video": {
                "text_contains": [
                    "video_gen_chip/",
                    "googleusercontent.com/video_gen_chip/",
                    "正在生成视频",
                    "视频已准备就绪",
                    "come back later to check",
                    "i'm generating your video",
                    "your video is being generated",
                    "your video is ready",
                ],
                "url_contains": [
                    "video_gen_chip/",
                    "googleusercontent.com/video_gen_chip/",
                ],
                "label_contains": [],
            },
        }

        normalized_media_type = str(media_type or "").strip().lower()
        normalized_marker_key = str(marker_key or "").strip()
        config_root = dict((image_config or {}).get("pending_detection") or {})
        type_config = dict(config_root.get(normalized_media_type) or {})
        configured = type_config.get(normalized_marker_key)

        if isinstance(configured, list):
            markers = [str(item or "").strip().lower() for item in configured if str(item or "").strip()]
            if markers:
                return markers

        return [
            str(item or "").strip().lower()
            for item in default_markers.get(normalized_media_type, {}).get(normalized_marker_key, [])
            if str(item or "").strip()
        ]

    def _is_pending_media_text(self, text: str, image_config: Dict, media_type: str = "") -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False

        media_types = [str(media_type or "").strip().lower()] if media_type else ["audio", "video"]
        for current_type in media_types:
            markers = self._get_pending_detection_markers(
                image_config,
                current_type,
                "text_contains",
            )
            if any(marker in lowered for marker in markers):
                return True
        return False

    def _is_pending_media_item(self, media_item: Dict, image_config: Dict) -> bool:
        media_type = str(media_item.get("media_type") or "").strip().lower()
        ref = str(media_item.get("url") or media_item.get("data_uri") or "").strip().lower()
        label = str(media_item.get("label") or "").strip().lower()

        if not media_type:
            return False

        url_markers = self._get_pending_detection_markers(
            image_config,
            media_type,
            "url_contains",
        )
        label_markers = self._get_pending_detection_markers(
            image_config,
            media_type,
            "label_contains",
        )

        if media_type not in {"audio", "video"}:
            return False

        return any(marker in ref for marker in url_markers) or any(
            marker in label for marker in label_markers
        )

    def _filter_ready_media_items(self, media_items: List[Dict], image_config: Dict) -> List[Dict]:
        return [
            item for item in (media_items or [])
            if not self._is_pending_media_item(item, image_config)
        ]

    def _extract_media_after_stream(
        self,
        tab,
        extractor,
        image_config: Dict,
        result_selector: str,
        message_wrapper_selector: str = "",
        completion_id: str = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        response_text_hint: str = "",
        media_generation_state: Optional[Dict[str, Any]] = None,
    ) -> List[Dict]:
        """流式输出结束后提取多模态资源。"""
        from app.core.elements import ElementFinder
        from app.core.extractors.media_extractor import media_extractor
        
        debounce = image_config.get("debounce_seconds", 2.0)
        effective_stop_checker = stop_checker or self._should_stop_checker
        if debounce > 0:
            elapsed = 0
            step = 0.1
            while elapsed < debounce:
                if effective_stop_checker():
                    return []
                time.sleep(step)
                elapsed += step
        
        finder = ElementFinder(tab)

        def _find_candidate_elements(timeout: float = 1.0):
            primary_elements = []
            fallback_elements = []

            try:
                if result_selector:
                    primary_elements = finder.find_all(result_selector, timeout=timeout) or []
            except Exception as e:
                logger.debug(f"主结果容器查找失败（忽略）: {e}")
                primary_elements = []

            if primary_elements:
                return primary_elements, "result_container"

            try:
                if message_wrapper_selector:
                    fallback_elements = finder.find_all(message_wrapper_selector, timeout=timeout) or []
            except Exception as e:
                logger.debug(f"消息包装容器查找失败（忽略）: {e}")
                fallback_elements = []

            if fallback_elements:
                logger.debug(
                    "主结果容器为空，回退到消息包装容器继续等待媒体渲染: "
                    f"primary={result_selector!r}, fallback={message_wrapper_selector!r}"
                )
                return fallback_elements, "message_wrapper"

            return [], ""
        
        try:
            elements, container_mode = _find_candidate_elements(timeout=1)
            if not elements:
                return []
            
            last_element = elements[-1]

            def _extract_media_once(target_element):
                if hasattr(extractor, 'extract_media'):
                    return extractor.extract_media(
                        target_element,
                        config=image_config,
                        container_selector_fallback=result_selector
                    )
                if hasattr(extractor, 'extract_images'):
                    return media_extractor.extract(
                        target_element,
                        config=image_config,
                        container_selector_fallback=result_selector
                    )
                return media_extractor.extract(
                    target_element,
                    config=image_config,
                    container_selector_fallback=result_selector
                )

            media_items = _extract_media_once(last_element)
            ready_media_items = self._filter_ready_media_items(media_items, image_config)
            pending_media_items = [
                item for item in media_items
                if self._is_pending_media_item(item, image_config)
            ]

            image_items = [item for item in ready_media_items if item.get("media_type") == "image"]
            audio_items = [item for item in ready_media_items if item.get("media_type") == "audio"]
            video_items = [item for item in ready_media_items if item.get("media_type") == "video"]

            placeholder_text = ""
            try:
                if hasattr(extractor, 'extract_text'):
                    placeholder_text = str(extractor.extract_text(last_element) or "")
                else:
                    placeholder_text = str(
                        last_element.run_js("return this.innerText || this.textContent || ''") or ""
                    )
            except Exception:
                placeholder_text = ""

            placeholder_text_lower = placeholder_text.lower()
            response_text_hint_lower = str(response_text_hint or "").strip().lower()
            media_state = dict(media_generation_state or {})
            media_state_pending = bool(media_state.get("pending"))
            media_state_type = str(media_state.get("media_type") or "").strip().lower()
            media_state_hint_text = str(media_state.get("hint_text") or "").strip().lower()
            combined_pending_text = "\n".join(
                text for text in (placeholder_text_lower, response_text_hint_lower, media_state_hint_text)
                if text
            )

            has_generated_image_hint = any(
                marker in placeholder_text_lower
                for marker in (
                    "image_generation_content/",
                    "googleusercontent.com/image_generation_content/",
                )
            )

            if not has_generated_image_hint:
                try:
                    has_generated_image_hint = bool(
                        last_element.run_js(
                            """
                            return !!this.querySelector(
                                '.attachment-container.generated-images, '
                                + '.generated-images, generated-image, single-image, '
                                + '.image-button, img[src^="blob:"], img[src^="data:image"]'
                            );
                            """
                        )
                    )
                except Exception:
                    has_generated_image_hint = False

            pending_audio_hint = self._is_pending_media_text(combined_pending_text, image_config, "audio")
            pending_video_hint = self._is_pending_media_text(combined_pending_text, image_config, "video")

            if pending_media_items:
                pending_audio_hint = pending_audio_hint or any(
                    item.get("media_type") == "audio" for item in pending_media_items
                )
                pending_video_hint = pending_video_hint or any(
                    item.get("media_type") == "video" for item in pending_media_items
                )
                logger.debug(
                    "检测到占位媒体，继续等待真实结果: "
                    f"pending={len(pending_media_items)}, ready={len(ready_media_items)}"
                )

            pending_kinds = set()
            if (has_generated_image_hint or (media_state_pending and media_state_type == "image")) and not image_items:
                pending_kinds.add("image")
            if media_state_pending and media_state_type == "audio" and not (audio_items or video_items):
                pending_kinds.add("audio")
            if media_state_pending and media_state_type == "video" and not video_items:
                pending_kinds.add("video")
            if pending_audio_hint and not (audio_items or video_items):
                pending_kinds.add("audio")
            if pending_video_hint and not video_items:
                pending_kinds.add("video")

            if pending_kinds and not effective_stop_checker():
                base_timeout = float(image_config.get("load_timeout_seconds", 5.0) or 5.0)
                late_wait_timeout = float(
                    image_config.get("late_render_timeout_seconds")
                    or max(30.0, base_timeout * 6.0)
                )
                state_wait_timeout = media_state.get("wait_timeout_seconds")
                try:
                    if state_wait_timeout is not None:
                        late_wait_timeout = max(late_wait_timeout, float(state_wait_timeout))
                except Exception:
                    pass
                if media_state_pending and media_state_type in pending_kinds:
                    logger.debug(
                        "检测到解析器上报的待渲染媒体任务，延长媒体渲染等待窗口: "
                        f"{late_wait_timeout:.1f}s"
                    )
                poll_interval = float(image_config.get("late_render_poll_seconds") or 1.0)
                deadline = time.time() + late_wait_timeout
                wait_satisfied = False

                while time.time() < deadline and not effective_stop_checker():
                    time.sleep(max(0.2, poll_interval))
                    elements, container_mode = _find_candidate_elements(timeout=0.5)
                    if not elements:
                        continue
                    last_element = elements[-1]
                    media_items = _extract_media_once(last_element)
                    ready_media_items = self._filter_ready_media_items(media_items, image_config)
                    image_items = [item for item in ready_media_items if item.get("media_type") == "image"]
                    audio_items = [item for item in ready_media_items if item.get("media_type") == "audio"]
                    video_items = [item for item in ready_media_items if item.get("media_type") == "video"]

                    satisfied = True
                    if "image" in pending_kinds and not image_items:
                        satisfied = False
                    if "audio" in pending_kinds and not (audio_items or video_items):
                        satisfied = False
                    if "video" in pending_kinds and not video_items:
                        satisfied = False

                    if satisfied:
                        kinds_label = ",".join(sorted(pending_kinds))
                        logger.debug(
                            f"延迟媒体渲染已捕获: kinds={kinds_label} "
                            f"(late_wait={late_wait_timeout:.1f}s, container={container_mode or 'unknown'})"
                        )
                        wait_satisfied = True
                        break

                if pending_kinds and not wait_satisfied and not effective_stop_checker():
                    logger.warning(
                        "等待待渲染媒体超时，仍未拿到最终结果: "
                        f"kinds={','.join(sorted(pending_kinds))}, late_wait={late_wait_timeout:.1f}s"
                    )
            
            # 🆕 如果图片是不可直连的外链（如 googleusercontent），尝试截图落盘并替换为本地 URL
            media_items = ready_media_items

            try:
                if image_items:
                    converted_images = self._try_screenshot_images_to_local(tab, last_element, image_items, image_config)
                    other_items = [item for item in media_items if item.get("media_type") != "image"]
                    media_items = converted_images + other_items
            except Exception as e:
                logger.warning(f"截图落盘失败（已忽略）: {e}")

            try:
                media_items = self._persist_data_uri_media_to_local(media_items)
            except Exception as e:
                logger.warning(f"data uri 落盘失败（已忽略）: {e}")

            return media_items
            
        except Exception as e:
            logger.warning(f"多模态提取异常: {e}")
            return []

    def _resolve_media_ref(self, media_item: Dict) -> str:
        ref = str(media_item.get("url") or media_item.get("data_uri") or "").strip()
        if not ref:
            return ""

        if ref.startswith("/") and not ref.startswith("//"):
            public_base = os.getenv("PUBLIC_BASE_URL", "").strip()
            if public_base:
                return public_base.rstrip("/") + ref
            return f"http://{AppConfig.get_host()}:{AppConfig.get_port()}{ref}"

        return ref

    def _build_media_markdown_block(self, media_items: List[Dict]) -> str:
        image_blocks = []
        audio_lines = []
        video_lines = []

        for item in media_items or []:
            ref = self._resolve_media_ref(item)
            if not ref:
                continue

            media_type = str(item.get("media_type") or "image").lower()
            if media_type == "image":
                image_blocks.append(f"\n\n![image_{len(image_blocks)}]({ref})")
                continue

            label = item.get("label") or item.get("mime") or ""
            label_suffix = f" - {label}" if label else ""
            if media_type == "audio":
                audio_lines.append(f"[audio_{len(audio_lines)}]({ref}){label_suffix}")
            elif media_type == "video":
                video_lines.append(f"[video_{len(video_lines)}]({ref}){label_suffix}")

        blocks = []
        if image_blocks:
            blocks.append("".join(image_blocks))
        if audio_lines:
            blocks.append("\n\n" + "\n".join(audio_lines))
        if video_lines:
            blocks.append("\n\n" + "\n".join(video_lines))

        if not blocks:
            return ""

        return "".join(blocks) + "\n\n"

    def _prepare_media_items_for_response(self, media_items: List[Dict]) -> List[Dict]:
        result = []
        for item in media_items or []:
            entry = dict(item)
            ref = self._resolve_media_ref(item)
            if entry.get("kind") == "url":
                entry["url"] = ref or entry.get("url")
            entry.pop("local_path", None)
            result.append(entry)
        return result

    def _persist_remote_media_urls_to_local(
        self,
        media_items: List[Dict],
        tab=None,
        max_size_mb: int = 10,
    ) -> List[Dict]:
        """Download remote audio/video URLs to local files so downstream clients get stable local URLs."""
        import time as time_module
        import uuid
        from pathlib import Path
        from urllib.parse import urlparse
        import requests

        if not media_items or tab is None:
            return media_items

        save_dir = Path("download_images")
        save_dir.mkdir(exist_ok=True)
        max_bytes = max(1, int(max_size_mb)) * 1024 * 1024

        ext_map = {
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/ogg": ".ogg",
            "audio/webm": ".webm",
            "audio/mp4": ".m4a",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "video/ogg": ".ogv",
            "video/quicktime": ".mov",
        }

        cookies_dict = {}
        try:
            cookies_list = tab.cookies()
            if cookies_list:
                for cookie in cookies_list:
                    if isinstance(cookie, dict) and "name" in cookie and "value" in cookie:
                        cookies_dict[cookie["name"]] = cookie["value"]
        except Exception as exc:
            logger.debug(f"媒体下载读取 cookies 失败（忽略）: {exc}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": getattr(tab, "url", "") or "",
            "Accept": "*/*",
        }

        result = []
        for item in media_items:
            filepath = None
            if item.get("kind") != "url":
                result.append(item)
                continue

            media_type = str(item.get("media_type") or "").lower()
            if media_type not in {"audio", "video"}:
                result.append(item)
                continue

            url = str(item.get("url") or "").strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                result.append(item)
                continue

            try:
                response = requests.get(
                    url,
                    cookies=cookies_dict,
                    headers=headers,
                    timeout=30,
                    allow_redirects=True,
                    stream=True,
                )
            except Exception as exc:
                logger.warning(f"{media_type} 下载失败，保留远程链接: {exc}")
                result.append(item)
                continue

            try:
                if response.status_code != 200:
                    logger.warning(f"{media_type} 下载失败，HTTP {response.status_code}")
                    result.append(item)
                    continue

                content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if media_type == "audio" and "audio" not in content_type:
                    logger.warning(f"音频下载返回非音频类型，保留远程链接: {content_type or 'unknown'}")
                    result.append(item)
                    continue
                if media_type == "video" and "video" not in content_type:
                    logger.warning(f"视频下载返回非视频类型，保留远程链接: {content_type or 'unknown'}")
                    result.append(item)
                    continue

                ext = ext_map.get(content_type)
                if not ext:
                    path_ext = Path(urlparse(url).path).suffix.lower()
                    ext = path_ext or (".mp4" if media_type == "video" else ".mp3")

                filename = f"{int(time_module.time())}_{uuid.uuid4().hex[:8]}{ext}"
                filepath = save_dir / filename

                written = 0
                with filepath.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 64):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValueError(f"media_too_large:{written}")
                        handle.write(chunk)

                new_item = dict(item)
                new_item["url"] = f"/download_images/{filename}"
                new_item["mime"] = content_type or item.get("mime")
                new_item["byte_size"] = written
                new_item["source"] = "local_file"
                new_item["local_path"] = str(filepath)
                result.append(new_item)
                logger.debug(f"✅ {media_type} 已保存到本地: {filename} ({written} bytes)")
            except Exception as exc:
                logger.warning(f"{media_type} 落盘失败，保留远程链接: {exc}")
                try:
                    if filepath is not None and filepath.exists():
                        filepath.unlink()
                except Exception:
                    pass
                result.append(item)
            finally:
                response.close()

        return result

    def _try_screenshot_images_to_local(self, tab, last_element, images: List[Dict], image_config: Dict = None) -> List[Dict]:
        """
        优先下载图片（更精准），下载失败才截图。
        基于实测 API：img_ele.attr('src'), page.cookies(), get_screenshot(path)
        """
        from pathlib import Path
        import time as time_module
        import uuid
        from urllib.parse import urlparse
        import requests

        if not images:
            return images

        remote_indexes = []
        for idx, item in enumerate(images):
            if str(item.get("kind") or "").strip().lower() != "url":
                continue
            url = str(item.get("url") or "").strip()
            if url.startswith("http://") or url.startswith("https://"):
                remote_indexes.append(idx)

        if not remote_indexes:
            return images

        out_dir = Path("download_images")
        out_dir.mkdir(exist_ok=True)

        image_config = image_config or {}
        selector = image_config.get("selector", "img")
        ext_map = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "image/svg+xml": ".svg",
            "image/avif": ".avif",
        }

        img_eles = []
        try:
            if selector and selector != "img":
                img_eles = tab.eles(f"css:{selector}", timeout=0.5)
                logger.debug(f"图片定位：使用 '{selector}'，找到 {len(img_eles) if img_eles else 0} 个")
            else:
                img_eles = last_element.eles("css:img", timeout=0.5)
                logger.debug(f"图片定位：使用默认选择器，找到 {len(img_eles) if img_eles else 0} 个")
        except Exception as e:
            logger.warning(f"图片定位失败，将仅尝试直连下载: {e}")
            img_eles = []

        cookies_dict = {}
        try:
            cookies_list = tab.cookies()
            if cookies_list:
                for c in cookies_list:
                    if isinstance(c, dict) and 'name' in c and 'value' in c:
                        cookies_dict[c['name']] = c['value']
        except Exception:
            pass

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': tab.url,
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        }

        img_ele_entries = []
        for ele in img_eles or []:
            try:
                img_src = str(ele.attr('src') or ele.link or "").strip()
            except Exception as e:
                logger.debug(f"读取图片元素地址失败（忽略）: {e}")
                img_src = ""
            img_ele_entries.append({
                "element": ele,
                "src": img_src,
                "used": False,
            })

        def _claim_image_element(target_url: str):
            normalized_target = str(target_url or "").strip()
            if normalized_target:
                for entry in reversed(img_ele_entries):
                    if entry["used"]:
                        continue
                    src = entry["src"]
                    if src and src == normalized_target:
                        entry["used"] = True
                        return entry["element"]

                for entry in reversed(img_ele_entries):
                    if entry["used"]:
                        continue
                    src = entry["src"]
                    if src and (normalized_target in src or src in normalized_target):
                        entry["used"] = True
                        return entry["element"]

                target_path = urlparse(normalized_target).path or ""
                for entry in reversed(img_ele_entries):
                    if entry["used"]:
                        continue
                    src_path = urlparse(entry["src"]).path if entry["src"] else ""
                    if src_path and target_path and src_path == target_path:
                        entry["used"] = True
                        return entry["element"]

            for entry in reversed(img_ele_entries):
                if entry["used"]:
                    continue
                entry["used"] = True
                return entry["element"]

            return None

        new_images = list(images)
        localized_count = 0

        for target_index in reversed(remote_indexes):
            target_image = images[target_index]
            target_url = str(target_image.get("url") or "").strip()
            img_ele = _claim_image_element(target_url)
            saved = False

            base_name = f"{int(time_module.time())}_{uuid.uuid4().hex[:8]}"
            ext = ".png"
            filename = f"{base_name}{ext}"
            out_path = out_dir / filename

            response = None
            try:
                logger.debug(f"尝试下载图片[{target_index}]: {target_url[:80]}...")
                response = requests.get(
                    target_url,
                    cookies=cookies_dict,
                    headers=headers,
                    timeout=15,
                    allow_redirects=True
                )

                if response.status_code == 200:
                    content = response.content
                    content_type = str(response.headers.get('Content-Type') or '').split(";", 1)[0].strip().lower()

                    if len(content) > 1000 and 'image' in content_type:
                        ext = ext_map.get(content_type, ext)
                        filename = f"{base_name}{ext}"
                        out_path = out_dir / filename
                        out_path.write_bytes(content)
                        saved = True
                        logger.debug(f"✅ 下载成功: {filename} ({len(content)} bytes)")
                    else:
                        logger.debug(f"下载内容无效: {len(content)} bytes, type: {content_type}")
                else:
                    logger.debug(f"下载失败: HTTP {response.status_code}")
            except Exception as e:
                logger.debug(f"下载异常，将尝试截图: {str(e)[:100]}")
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass

            if not saved and img_ele is not None:
                logger.debug(f"图片[{target_index}] 回退到截图方式")
                try:
                    img_ele.get_screenshot(str(out_path))
                    if out_path.exists() and out_path.stat().st_size > 0:
                        saved = True
                        logger.debug(f"✅ 截图成功: {filename} ({out_path.stat().st_size} bytes)")
                except Exception as e:
                    logger.warning(f"截图失败: {e}")

            if not saved:
                logger.warning(f"图片[{target_index}] 保存失败：下载和截图均失败")
                continue

            local_url = f"/download_images/{filename}"
            new_item = dict(target_image)
            new_item["kind"] = "url"
            new_item["url"] = local_url
            new_item["source"] = "local_file"
            new_item["local_path"] = str(out_path)
            new_item["byte_size"] = out_path.stat().st_size
            new_images[target_index] = new_item
            localized_count += 1

        if localized_count > 0:
            logger.debug(f"✅ 图片本地化完成: {localized_count}/{len(remote_indexes)} 张")

        return new_images

    def _persist_data_uri_media_to_local(self, media_items: List[Dict]) -> List[Dict]:
        """Persist extracted data-uri media so downstream Markdown can reuse the existing URL flow."""
        import base64
        import binascii
        import uuid
        from pathlib import Path
        from datetime import datetime

        if not media_items:
            return media_items

        save_dir = Path("download_images")
        save_dir.mkdir(exist_ok=True)

        ext_map = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "video/ogg": ".ogv",
            "video/quicktime": ".mov",
        }

        result = []
        for item in media_items:
            if item.get("kind") != "data_uri":
                result.append(item)
                continue

            data_uri = str(item.get("data_uri") or "").strip()
            if not data_uri.startswith("data:"):
                result.append(item)
                continue

            try:
                header, b64_data = data_uri.split(",", 1)
                mime = header.split(";", 1)[0].split(":", 1)[1].lower()
                ext = ext_map.get(mime, ".png")
                media_bytes = base64.b64decode(b64_data)
            except (ValueError, IndexError, binascii.Error) as e:
                logger.warning(f"data uri 解析失败，保留原媒体数据: {e}")
                result.append(item)
                continue

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{uuid.uuid4().hex[:8]}{ext}"
            filepath = save_dir / filename

            try:
                filepath.write_bytes(media_bytes)
            except Exception as e:
                logger.warning(f"data uri 保存失败，保留原媒体数据: {e}")
                result.append(item)
                continue

            new_item = dict(item)
            new_item["kind"] = "url"
            new_item["url"] = f"/download_images/{filename}"
            new_item["data_uri"] = None
            new_item["mime"] = mime
            new_item["byte_size"] = len(media_bytes)
            new_item["source"] = "local_file"
            new_item["local_path"] = str(filepath)
            result.append(new_item)

        return result
    
    def _execute_workflow_non_stream(
        self, 
        session: TabSession,
        messages: List[Dict],
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        workflow_priority: Optional[int] = None,
    ) -> Generator[str, None, None]:
        """非流式工作流执行"""
        collected_content = []
        collected_media = []
        error_data = None
        
        stream = self._execute_workflow_stream(
            session,
            messages,
            preset_name=preset_name,
            stop_checker=stop_checker,
            workflow_priority=workflow_priority,
        )

        try:
            for chunk in stream:
                if chunk.startswith("data: [DONE]"):
                    continue
                
                if chunk.startswith("data: "):
                    try:
                        data_str = chunk[6:].strip()
                        if not data_str:
                            continue
                        data = json.loads(data_str)
                        
                        if "error" in data:
                            error_data = data
                            break

                        media_items = data.get("media")
                        if isinstance(media_items, list):
                            collected_media.extend(media_items)
                        
                        if "choices" in data and data["choices"]:
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                collected_content.append(content)
                    except json.JSONDecodeError:
                        continue
        except GeneratorExit:
            with contextlib.suppress(Exception):
                stream.close()
            raise
        finally:
            with contextlib.suppress(Exception):
                stream.close()
        
        if error_data:
            yield json.dumps(error_data, ensure_ascii=False)
        else:
            full_content = "".join(collected_content)
            if not collected_media and full_content.strip():
                extra_media_items = self._retry_pending_media_from_response_text(
                    session,
                    full_content,
                    preset_name=preset_name,
                    stop_checker=stop_checker,
                )
                if extra_media_items:
                    collected_media.extend(extra_media_items)
            response = self.formatter.pack_non_stream(
                full_content,
                media=self._dedupe_media_items(collected_media),
            )
            yield json.dumps(response, ensure_ascii=False)

    def _dedupe_media_items(self, media_items: List[Dict]) -> List[Dict]:
        result = []
        seen = set()
        for item in media_items or []:
            media_type = str(item.get("media_type") or "")
            ref = str(item.get("url") or item.get("data_uri") or "")
            key = (media_type, ref)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _retry_pending_media_from_response_text(
        self,
        session: TabSession,
        full_content: str,
        preset_name: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
    ) -> List[Dict]:
        hint_text = str(full_content or "").strip()
        if not hint_text:
            return []

        try:
            domain = str(getattr(session, "current_domain", "") or "").strip()
            if not domain:
                tab = getattr(session, "tab", None)
                current_url = str(getattr(tab, "url", "") or "").strip()
                domain = extract_remote_site_domain(current_url)
            if not domain:
                return []

            config_engine = self._get_config_engine()
            effective_preset_name = preset_name if preset_name is not None else session.preset_name
            site_config = config_engine.get_site_config(domain, preset_name=effective_preset_name)
            if not site_config:
                return []

            image_config = site_config.get("image_extraction", {}) or {}
            modalities = image_config.get("modalities") or {}
            image_extraction_enabled = bool(image_config.get("enabled", False)) or any(
                bool(modalities.get(key)) for key in ("image", "audio", "video")
            )
            if not image_extraction_enabled:
                return []

            pending_hit = any(
                self._is_pending_media_text(hint_text, image_config, media_type)
                for media_type in ("audio", "video")
            )
            if not pending_hit:
                return []

            selectors = site_config.get("selectors", {}) or {}
            result_selector = str(selectors.get("result_container", "") or "").strip()
            if not result_selector:
                return []

            extractor = config_engine.get_site_extractor(domain, preset_name=effective_preset_name)
            tab = getattr(session, "tab", None)
            if tab is None:
                return []

            logger.debug(f"[{session.id}] 非流式响应命中占位媒体文本，触发二次提取等待")
            return self._extract_media_after_stream(
                tab=tab,
                extractor=extractor,
                image_config=image_config,
                result_selector=result_selector,
                message_wrapper_selector=str(selectors.get("message_wrapper", "") or "").strip(),
                stop_checker=stop_checker,
                response_text_hint=hint_text,
            )
        except Exception as e:
            logger.warning(f"[{session.id}] 非流式二次媒体提取失败（已忽略）: {e}")
            return []

    def _download_url_images(self, images: List[Dict], tab=None) -> List[Dict]:
        """
        在浏览器内通过 Canvas 压缩图片，保存到本地并返回可访问 URL
        
        流程：
        1. 浏览器 Canvas 压缩 → base64
        2. 后端解码 → 保存到 download_images/
        3. 返回 /download_images/xxx.jpg URL
        """
        import base64
        import uuid
        from pathlib import Path
        from datetime import datetime
        
        result = []
        
        # 确保目录存在
        save_dir = Path("download_images")
        save_dir.mkdir(exist_ok=True)
        
        for img in images:
            if img.get('kind') != 'url':
                result.append(img)
                continue
            
            url = img.get('url')
            if not url:
                result.append(img)
                continue
            
            if not tab:
                result.append(img)
                continue
            
            try:
                # 🔑 在浏览器中用 Canvas 加载并压缩图片
                js_code = """
                (async function(imageUrl) {
                    return new Promise((resolve) => {
                        const img = new Image();
                        img.crossOrigin = 'anonymous';
                        
                        img.onload = function() {
                            try {
                                // 限制最大尺寸
                                const MAX_SIZE = 1024;
                                let width = img.naturalWidth;
                                let height = img.naturalHeight;
                                
                                if (width > MAX_SIZE || height > MAX_SIZE) {
                                    if (width > height) {
                                        height = Math.round(height * MAX_SIZE / width);
                                        width = MAX_SIZE;
                                    } else {
                                        width = Math.round(width * MAX_SIZE / height);
                                        height = MAX_SIZE;
                                    }
                                }
                                
                                const canvas = document.createElement('canvas');
                                canvas.width = width;
                                canvas.height = height;
                                
                                const ctx = canvas.getContext('2d');
                                ctx.drawImage(img, 0, 0, width, height);
                                
                                // 转为 JPEG
                                const dataUri = canvas.toDataURL('image/jpeg', 0.85);
                                
                                resolve({
                                    success: true,
                                    dataUri: dataUri,
                                    width: width,
                                    height: height
                                });
                            } catch (e) {
                                resolve({ success: false, error: 'Canvas: ' + e.message });
                            }
                        };
                        
                        img.onerror = function() {
                            resolve({ success: false, error: 'Load failed' });
                        };
                        
                        setTimeout(() => resolve({ success: false, error: 'Timeout' }), 15000);
                        img.src = imageUrl;
                    });
                })(arguments[0]);
                """
                
                # ===== PROBE: 验证 run_js 是否等待 Promise，并检查图片/Fetch 可用性 =====
                probe_js = """
                (function(u){
                    try {
                        // 1) 最小同步返回测试
                        const sync_ok = { ok: true, type: typeof u, head: String(u).slice(0, 40) };

                        // 2) Promise 返回测试（不返回大对象）
                        const promise_test = Promise.resolve({ promise_ok: true });

                        // 3) 图片加载测试（不画 canvas，不导 dataUri，避免大返回）
                        const img_test = new Promise((resolve) => {
                            const img = new Image();
                            let done = false;

                            img.onload = () => {
                                if (done) return;
                                done = true;
                                resolve({ img_onload: true, w: img.naturalWidth, h: img.naturalHeight });
                            };
                            img.onerror = () => {
                                if (done) return;
                                done = true;
                                resolve({ img_onerror: true });
                            };

                            setTimeout(() => {
                                if (done) return;
                                done = true;
                                resolve({ img_timeout: true });
                            }, 6000);

                            img.src = u;
                        });

                        // 4) fetch 测试（只返回 status，不读 body）
                        const fetch_test = (async () => {
                            try {
                                const r = await fetch(u, { method: 'GET' });
                                return { fetch_ok: true, status: r.status, redirected: r.redirected };
                            } catch (e) {
                                return { fetch_error: String(e).slice(0, 120) };
                            }
                        })();

                        // 关键：返回一个对象，包含同步字段 + Promise 字段
                        // 如果 run_js 不等待 Promise，你只能拿到一个“未解析”的东西或 None
                        return Promise.all([promise_test, img_test, fetch_test]).then(all => {
                            return {
                                sync: sync_ok,
                                promise: all[0],
                                img: all[1],
                                fetch: all[2]
                            };
                        });
                    } catch(e) {
                        return { probe_exception: String(e).slice(0, 160) };
                    }
                })(arguments[0]);
                """

                probe_result = tab.run_js(probe_js, url)
                logger.info(f"[PROBE_JS] probe_result_type={type(probe_result).__name__}, value={str(probe_result)[:500]}")

                download_result = tab.run_js(js_code, url)

                logger.info(f"[PROBE_JS] canvas_result_type={type(download_result).__name__}, value={str(download_result)[:300]}")                
                if download_result and download_result.get('success'):
                    data_uri = download_result['dataUri']
                    
                    # 解析 base64
                    # 格式: data:image/jpeg;base64,/9j/4AAQSkZJRg...
                    if ',' in data_uri:
                        header, b64_data = data_uri.split(',', 1)
                        mime = 'image/jpeg'
                        if 'png' in header:
                            mime = 'image/png'
                            ext = '.png'
                        else:
                            ext = '.jpg'
                        
                        # 解码并保存
                        image_bytes = base64.b64decode(b64_data)
                        
                        # 生成唯一文件名
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        unique_id = uuid.uuid4().hex[:8]
                        filename = f"{timestamp}_{unique_id}{ext}"
                        filepath = save_dir / filename
                        
                        # 写入文件
                        with open(filepath, 'wb') as f:
                            f.write(image_bytes)
                        
                        # 构建可访问的 URL
                        accessible_url = f"/download_images/{filename}"
                        
                        new_img = img.copy()
                        new_img['kind'] = 'url'
                        new_img['url'] = accessible_url
                        new_img['data_uri'] = None
                        new_img['mime'] = mime
                        new_img['width'] = download_result['width']
                        new_img['height'] = download_result['height']
                        new_img['byte_size'] = len(image_bytes)
                        new_img['source'] = 'local_file'
                        new_img['local_path'] = str(filepath)
                        
                        result.append(new_img)
                        logger.info(f"✅ 图片已保存: {filename} ({len(image_bytes)} bytes)")
                        continue
                
                error_msg = download_result.get('error', 'Unknown') if download_result else 'No result'
                logger.warning(f"⚠️ 图片处理失败: {error_msg}")
            
            except Exception as e:
                logger.warning(f"⚠️ 图片保存异常: {str(e)[:100]}")
            
            # 失败时保留原 URL
            result.append(img)
        
        return result

    def _check_page_status(self, tab) -> Dict[str, Any]:
        """检查页面状态"""
        result = {"ready": True, "reason": None}
        
        try:
            url = tab.url or ""
            
            if not url or url in ("about:blank", "chrome://newtab/"):
                result["ready"] = False
                result["reason"] = "请先打开目标AI网站"
                return result
            
            error_indicators = ["chrome-error://", "about:neterror"]
            for indicator in error_indicators:
                if indicator in url:
                    result["ready"] = False
                    result["reason"] = "页面加载错误"
                    return result
        
        except Exception as e:
            logger.debug(f"页面状态检查异常: {e}")
        
        return result
    
    def get_pool_status(self) -> Dict:
        """获取标签页池状态"""
        if self._tab_pool:
            return self._tab_pool.get_status()
        return {"initialized": False}
    
    def close(self):
        """关闭浏览器连接"""
        logger.info("关闭浏览器连接")
        self._watchdog_stop.set()
        
        if self._tab_pool:
            self._tab_pool.shutdown()
            self._tab_pool = None
        
        self._connected = False
        self.browser_handle = None
        self.page = None
        
        with self._lock:
            BrowserCore._instance = None
            self._initialized = False


# ================= 工厂函数 =================

_browser_instance: Optional[BrowserCore] = None
_browser_lock = threading.Lock()


def get_browser(port: int = None, auto_connect: bool = True) -> BrowserCore:
    """获取浏览器实例"""
    global _browser_instance
    
    if _browser_instance is not None:
        return _browser_instance
    
    with _browser_lock:
        if _browser_instance is None:
            instance = BrowserCore(port)
            
            if auto_connect:
                if not instance.ensure_connection():
                    raise BrowserConnectionError(
                        f"无法连接到浏览器 (端口: {instance.port})"
                    )
            
            _browser_instance = instance
    
    return _browser_instance


class _LazyBrowser:
    """浏览器延迟初始化代理"""
    
    def __getattr__(self, name):
        return getattr(get_browser(auto_connect=False), name)
    
    def __call__(self, *args, **kwargs):
        return get_browser(*args, **kwargs)


browser = _LazyBrowser()


__all__ = [
    'BrowserCore',
    'get_browser',
    'browser',
]
