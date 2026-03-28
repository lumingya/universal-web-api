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
import threading
import time
import contextlib
from typing import Optional, List, Dict, Any, Generator, Callable
from DrissionPage import ChromiumPage, ChromiumOptions

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


# ================= 配置加载 =================

def _load_tab_pool_config() -> Dict:
    """从配置文件和环境变量加载标签页池配置"""
    config = {
        "max_tabs": 5,
        "min_tabs": 1,
        "idle_timeout": 300,
        "acquire_timeout": 60,
        "stuck_timeout": 180,
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
        self.page: Optional[ChromiumPage] = None
        
        self._connected = False
        self._should_stop_checker: Callable[[], bool] = lambda: False
        
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

    def _release_workflow_session(
        self,
        session: TabSession,
        *,
        effective_stop_checker: Optional[Callable[[], bool]] = None,
        task_id: str = "",
    ):
        cancelled = bool(effective_stop_checker and effective_stop_checker())
        rollback_request_count = cancelled and self._should_rollback_request_count_on_cancel(task_id)
        if cancelled and not rollback_request_count:
            logger.debug(
                f"[{session.id}] stop detected but request_count preserved "
                f"(task={task_id or '-'}, reason={self._get_request_cancel_reason(task_id) or 'unknown'})"
            )

        self.tab_pool.release(
            session.id,
            check_triggers=not rollback_request_count,
            rollback_request_count=rollback_request_count,
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
                        browser_page=self.page,
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
            self.page = ChromiumPage(addr_or_opts=opts)
            self._connected = True
            logger.info("浏览器连接成功")
            return True
        except Exception as e:
            logger.error(f"浏览器连接失败: {e}")
            self._connected = False
            return False
    
    def health_check(self) -> Dict[str, Any]:
        result = {
            "status": "unhealthy",
            "connected": False,
            "port": self.port,
            "tab_pool": None,
            "error": None
        }
        
        try:
            if not self.page:
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
            try:
                _ = self.page.latest_tab
                return True
            except Exception:
                self._connected = False
        
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
            from app.services.request_manager import request_manager
            request_manager.bind_tab(request_id, session.id)
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
        image_extraction_enabled = image_config.get("enabled", False)
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
                
                if not selector and action not in ("WAIT", "KEY_PRESS", "COORD_CLICK", "JS_EXEC"):
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

            # 图片提取
            logger.debug(f"[PROBE] Workflow 循环结束，image_enabled={image_extraction_enabled}, should_stop={effective_stop_checker()}")
            if image_extraction_enabled and not effective_stop_checker() and not workflow_aborted:
                logger.debug("[PROBE] 进入图片提取分支")
                try:
                    images = self._extract_images_after_stream(
                        tab=tab,
                        extractor=extractor,
                        image_config=image_config,
                        result_selector=result_container_selector,
                        completion_id=executor._completion_id,
                        stop_checker=_combined_stop_checker
                    )
                    
                    if images:
                        download_urls = image_config.get("download_urls", False)
                        if download_urls:
                            images = self._download_url_images(images, tab=tab)
                        
                        logger.debug(f"[PROBE] 即将发送图片（Markdown），数量={len(images)}")

                        try:
                            public_base = os.getenv("PUBLIC_BASE_URL", "").strip()
                            image_refs = []
                            for img in images:
                                ref = str(img.get("url") or img.get("data_uri") or "").strip()
                                if not ref:
                                    continue
                                if ref.startswith("/") and not ref.startswith("//"):
                                    if public_base:
                                        ref = public_base.rstrip("/") + ref
                                    else:
                                        ref = f"http://{AppConfig.get_host()}:{AppConfig.get_port()}{ref}"
                                image_refs.append(ref)

                            if image_refs:
                                md = "".join(f"\n\n![image_{idx}]({ref})" for idx, ref in enumerate(image_refs))
                                md += "\n\n"
                                yield self.formatter.pack_chunk(
                                    md,
                                    completion_id=executor._completion_id,
                                    images=image_refs,
                                )
                                logger.debug(f"[MD_IMAGE] 已发送结构化图片，共 {len(image_refs)} 张")
                            else:
                                logger.warning("[MD_IMAGE] 未构建出任何图片引用，跳过 Markdown 输出")
                        except Exception as e:
                            logger.warning(f"[MD_IMAGE] 发送 Markdown 图片链接失败: {e}")            
                except Exception as e:
                    logger.warning(f"[{session.id}] 图片提取失败: {e}")
        
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
    
    def _extract_images_after_stream(
        self,
        tab,
        extractor,
        image_config: Dict,
        result_selector: str,
        completion_id: str = None,
        stop_checker: Optional[Callable[[], bool]] = None
    ) -> List[Dict]:
        """流式输出结束后提取图片"""
        from app.core.elements import ElementFinder
        from app.core.extractors.image_extractor import image_extractor
        
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
        
        try:
            elements = finder.find_all(result_selector, timeout=1)
            if not elements:
                return []
            
            last_element = elements[-1]

            def _extract_images_once(target_element):
                if hasattr(extractor, 'extract_images'):
                    return extractor.extract_images(
                        target_element,
                        config=image_config,
                        container_selector_fallback=result_selector
                    )
                return image_extractor.extract(
                    target_element,
                    config=image_config,
                    container_selector_fallback=result_selector
                )

            images = _extract_images_once(last_element)

            if not images and not effective_stop_checker():
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

                has_generated_image_hint = any(
                    marker in placeholder_text.lower()
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

                if has_generated_image_hint:
                    late_wait_timeout = float(
                        image_config.get("late_render_timeout_seconds")
                        or max(8.0, float(image_config.get("load_timeout_seconds", 5.0)))
                    )
                    deadline = time.time() + late_wait_timeout

                    while time.time() < deadline and not effective_stop_checker():
                        time.sleep(0.5)
                        elements = finder.find_all(result_selector, timeout=0.5)
                        if not elements:
                            continue
                        last_element = elements[-1]
                        images = _extract_images_once(last_element)
                        if images:
                            logger.debug(
                                f"图片延迟渲染已捕获: {len(images)} 张 "
                                f"(late_wait={late_wait_timeout:.1f}s)"
                            )
                            break
            
            # 🆕 如果图片是不可直连的外链（如 googleusercontent），尝试截图落盘并替换为本地 URL
            try:
                images = self._try_screenshot_images_to_local(tab, last_element, images, image_config)
            except Exception as e:
                logger.warning(f"截图落盘失败（已忽略）: {e}")

            try:
                images = self._persist_data_uri_images_to_local(images)
            except Exception as e:
                logger.warning(f"data uri 落盘失败（已忽略）: {e}")

            return images
            
        except Exception as e:
            logger.warning(f"图片提取异常: {e}")
            return []

    def _try_screenshot_images_to_local(self, tab, last_element, images: List[Dict], image_config: Dict = None) -> List[Dict]:
        """
        优先下载图片（更精准），下载失败才截图。
        基于实测 API：img_ele.attr('src'), page.cookies(), get_screenshot(path)
        """
        from pathlib import Path
        import time as time_module
        import uuid
        import requests

        if not images:
            return images

        img0 = images[0]
        url0 = (img0.get("url") or "").strip()

        # 仅当是 http(s) 外链时才处理
        if not (url0.startswith("http://") or url0.startswith("https://")):
            return images

        # 准备目录与文件名
        out_dir = Path("download_images")
        out_dir.mkdir(exist_ok=True)
        filename = f"{int(time_module.time())}_{uuid.uuid4().hex[:8]}.png"
        out_path = out_dir / filename

        # 从站点配置获取图片选择器
        image_config = image_config or {}
        selector = image_config.get("selector", "img")

        # ===== 1. 定位图片元素 =====
        try:
            if selector and selector != "img":
                img_eles = tab.eles(f"css:{selector}", timeout=0.5)
                logger.debug(f"图片定位：使用 '{selector}'，找到 {len(img_eles) if img_eles else 0} 个")
            else:
                img_eles = last_element.eles("css:img", timeout=0.5)
                logger.debug(f"图片定位：使用默认选择器，找到 {len(img_eles) if img_eles else 0} 个")

            if not img_eles:
                logger.warning(f"图片定位：未找到元素 (selector: {selector})")
                return images

            img_ele = img_eles[-1]
        except Exception as e:
            logger.warning(f"图片定位失败: {e}")
            return images

        saved = False

        # ===== 2. 优先下载图片（精准且小文件）=====
        try:
            # 获取图片 URL（实测：attr 和 link 都可用）
            img_src = img_ele.attr('src') or img_ele.link

            if img_src and img_src.startswith('http'):
                logger.debug(f"尝试下载: {img_src[:80]}...")

                # 获取 cookies（实测：返回字典列表）
                cookies_dict = {}
                try:
                    cookies_list = tab.cookies()
                    if cookies_list:
                        for c in cookies_list:
                            if isinstance(c, dict) and 'name' in c and 'value' in c:
                                cookies_dict[c['name']] = c['value']
                except:
                    pass

                # 下载图片
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': tab.url,
                    'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                }

                response = requests.get(
                    img_src,
                    cookies=cookies_dict,
                    headers=headers,
                    timeout=15,
                    allow_redirects=True
                )

                if response.status_code == 200:
                    content = response.content
                    content_type = response.headers.get('Content-Type', '')

                    # 检查是否是有效图片
                    if len(content) > 1000 and 'image' in content_type:
                        # 根据 Content-Type 调整扩展名
                        if 'jpeg' in content_type or 'jpg' in content_type:
                            filename = filename.replace('.png', '.jpg')
                            out_path = out_dir / filename
                        elif 'webp' in content_type:
                            filename = filename.replace('.png', '.webp')
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

        # ===== 3. 回退到截图（文件更大但稳定）=====
        if not saved:
            logger.debug("回退到截图方式")
            try:
                # 实测：get_screenshot(path) 返回路径字符串
                result = img_ele.get_screenshot(str(out_path))
                if out_path.exists() and out_path.stat().st_size > 0:
                    saved = True
                    logger.debug(f"✅ 截图成功: {filename} ({out_path.stat().st_size} bytes)")
            except Exception as e:
                logger.warning(f"截图失败: {e}")

        if not saved:
            logger.warning("图片保存失败：下载和截图均失败")
            return images

        local_url = f"/download_images/{filename}"

        # 覆写第 1 张图片为本地 URL
        new0 = dict(img0)
        new0["kind"] = "url"
        new0["url"] = local_url
        new0["source"] = "local_file"
        new0["local_path"] = str(out_path)
        new0["byte_size"] = out_path.stat().st_size

        new_images = [new0] + images[1:]
        logger.debug(f"✅ 图片已保存: {local_url} ({new0['byte_size']} bytes)")
        return new_images

    def _persist_data_uri_images_to_local(self, images: List[Dict]) -> List[Dict]:
        """Persist extracted data-uri images so downstream Markdown can reuse the existing URL flow."""
        import base64
        import binascii
        import uuid
        from pathlib import Path
        from datetime import datetime

        if not images:
            return images

        save_dir = Path("download_images")
        save_dir.mkdir(exist_ok=True)

        ext_map = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/bmp": ".bmp",
        }

        result = []
        for img in images:
            if img.get("kind") != "data_uri":
                result.append(img)
                continue

            data_uri = str(img.get("data_uri") or "").strip()
            if not data_uri.startswith("data:image"):
                result.append(img)
                continue

            try:
                header, b64_data = data_uri.split(",", 1)
                mime = header.split(";", 1)[0].split(":", 1)[1].lower()
                ext = ext_map.get(mime, ".png")
                image_bytes = base64.b64decode(b64_data)
            except (ValueError, IndexError, binascii.Error) as e:
                logger.warning(f"data uri 解析失败，保留原图数据: {e}")
                result.append(img)
                continue

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{uuid.uuid4().hex[:8]}{ext}"
            filepath = save_dir / filename

            try:
                filepath.write_bytes(image_bytes)
            except Exception as e:
                logger.warning(f"data uri 保存失败，保留原图数据: {e}")
                result.append(img)
                continue

            new_img = dict(img)
            new_img["kind"] = "url"
            new_img["url"] = f"/download_images/{filename}"
            new_img["data_uri"] = None
            new_img["mime"] = mime
            new_img["byte_size"] = len(image_bytes)
            new_img["source"] = "local_file"
            new_img["local_path"] = str(filepath)
            result.append(new_img)

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
            response = self.formatter.pack_non_stream(full_content)
            yield json.dumps(response, ensure_ascii=False)

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
        
        if self._tab_pool:
            self._tab_pool.shutdown()
            self._tab_pool = None
        
        self._connected = False
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
