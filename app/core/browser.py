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
from DrissionPage import ChromiumPage

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
from app.core.workflow import WorkflowExecutor
from app.core.tab_pool import TabPoolManager, TabSession, get_clipboard_lock


# ================= 配置加载 =================

def _load_tab_pool_config() -> Dict:
    """从配置文件和环境变量加载标签页池配置"""
    config = {
        "max_tabs": 5,
        "min_tabs": 1,
        "idle_timeout": 300,
        "acquire_timeout": 60
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
        # 添加调试日志
        content_type = type(content).__name__
        content_preview = ""
        try:
            content_str_temp = str(content)
            content_len = len(content_str_temp)
            # 只取前 100 字符作为预览，避免日志爆炸
            content_preview = repr(content_str_temp[:100]) if content_len > 100 else repr(content_str_temp)
        except:
            content_len = -1
            content_preview = "[无法预览]"
        
        logger.debug(f"[CONTENT_PARSE] 开始解析: 类型={content_type}, 长度={content_len}, 预览={content_preview}")
        
        # 空值处理
        if content is None:
            logger.debug("[CONTENT_PARSE] 内容为 None，返回空字符串")
            return ""
        
        # 情况1：纯字符串
        if isinstance(content, str):
            # 尝试检测是否是多模态消息的字符串形式
            stripped = content.strip()
            if stripped.startswith('[') and stripped.endswith(']'):
                parsed = None
                
                # 方法1：尝试标准 JSON 解析（双引号）
                try:
                    parsed = json.loads(stripped)
                    logger.debug(f"[CONTENT_PARSE] JSON 解析成功")
                except (json.JSONDecodeError, TypeError):
                    pass
                
                # 方法2：尝试 Python 格式解析（单引号）
                if parsed is None:
                    try:
                        import ast
                        parsed = ast.literal_eval(stripped)
                        logger.debug(f"[CONTENT_PARSE] Python literal_eval 解析成功")
                    except (ValueError, SyntaxError):
                        pass
                
                # 如果解析成功且是多模态格式，递归处理
                if parsed and isinstance(parsed, list) and len(parsed) > 0:
                    first_item = parsed[0] if parsed else {}
                    if isinstance(first_item, dict) and 'type' in first_item:
                        logger.debug(f"[CONTENT_PARSE] 检测到多模态列表格式，递归解析（元素数={len(parsed)}）")
                        return self._extract_text_from_content(parsed)
            
            # 安全检查：防止真实 base64 图片数据泄露（排除代码中的字符串）
            if content.startswith('data:image') and 'base64,' in content and len(content) > 1000:
                logger.warning(f"[CONTENT_PARSE] ⚠️ 检测到 base64 图片数据！长度={len(content)}，已替换为占位符")
                return "[图片内容]"
            
            # 普通字符串，直接返回
            logger.debug(f"[CONTENT_PARSE] 纯字符串模式，长度={len(content)}")
            return content
        
        # 情况2：列表或类列表对象（包括 tuple）
        # 注意：字符串也是可迭代的，但已在上面处理
        is_list_like = isinstance(content, (list, tuple))
        if not is_list_like:
            # 检查是否有 __iter__ 但不是字符串/bytes
            try:
                is_list_like = hasattr(content, '__iter__') and not isinstance(content, (str, bytes))
            except:
                is_list_like = False
        
        if is_list_like:
            # 统一转换为 list
            try:
                if not isinstance(content, list):
                    content = list(content)
                    logger.debug(f"[CONTENT_PARSE] 已转换为 list，元素数量={len(content)}")
            except Exception as e:
                logger.warning(f"[CONTENT_PARSE] 转换为 list 失败: {e}")
                return "[内容解析失败]"
            
            text_parts = []
            image_count = 0
            
            for idx, item in enumerate(content):
                # 跳过非字典项
                if not isinstance(item, dict):
                    logger.debug(f"[CONTENT_PARSE] 跳过非字典项 [{idx}]: 类型={type(item).__name__}")
                    continue
                
                item_type = item.get("type", "")
                
                if item_type == "text":
                    text_content = item.get("text", "")
                    text_parts.append(text_content)
                    preview = repr(text_content[:50]) if len(text_content) > 50 else repr(text_content)
                    logger.debug(f"[CONTENT_PARSE] ✓ 提取文本 [{idx}]: {preview}")
                
                elif item_type == "image_url":
                    image_count += 1
                    text_parts.append(f"[图片{image_count}]")
                    # 记录图片信息但不记录 base64 内容
                    image_url_obj = item.get("image_url", {})
                    url_preview = "[data_uri]" if isinstance(image_url_obj, dict) and "base64" in str(image_url_obj.get("url", ""))[:50] else str(image_url_obj)[:50]
                    logger.debug(f"[CONTENT_PARSE] ✓ 图片占位符 [{idx}]: [图片{image_count}], url预览={url_preview}")
                
                else:
                    logger.debug(f"[CONTENT_PARSE] 未知类型 [{idx}]: type={item_type}")
            
            result = " ".join(text_parts)
            if image_count > 0:
                logger.debug(f"[CONTENT_PARSE] ✅ 多模态解析完成: {len(text_parts)} 个文本部分, {image_count} 张图片, 结果长度={len(result)}")
            else:
                logger.debug(f"[CONTENT_PARSE] 多模态解析完成: {len(text_parts)} 个文本部分, {image_count} 张图片, 结果长度={len(result)}")
            return result
        
        # 情况3：其他类型（兜底）
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
            self.page = ChromiumPage(addr_or_opts=f"127.0.0.1:{self.port}")
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
        task_id: str = None
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
            
            # 执行工作流
            if stream:
                yield from self._execute_workflow_stream(session, sanitized_messages)
            else:
                yield from self._execute_workflow_non_stream(session, sanitized_messages)
        
        finally:
            # 释放标签页
            if session:
                self.tab_pool.release(session.id)

    def execute_workflow_for_tab_index(
        self, 
        tab_index: int,
        messages: List[Dict],
        stream: bool = True,
        task_id: str = None
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
            
            # 执行工作流
            if stream:
                yield from self._execute_workflow_stream(session, sanitized_messages)
            else:
                yield from self._execute_workflow_non_stream(session, sanitized_messages)
        
        finally:
            if session:
                self.tab_pool.release(session.id)
   
    def _execute_workflow_stream(
        self, 
        session: TabSession,
        messages: List[Dict]
    ) -> Generator[str, None, None]:
        """流式工作流执行（v2.0）"""
    
        tab = session.tab
    
        if self._should_stop_checker():
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
    
        # 3. 安全解析域名（允许 localhost 和内网短域名）
        try:
            if "://" not in url:
                raise ValueError(f"无效的 URL 格式: {url}")
            domain = url.split("//")[-1].split("/")[0]
            
            if not domain:
                raise ValueError(f"无效的域名: {domain}")
            
            # 提取主机名（排除端口号）
            hostname = domain.split(":")[0].lower()
            
            # 允许的本地/内网主机名
            local_hosts = ("localhost", "127.0.0.1", "::1")
            is_local = hostname in local_hosts
            has_dot = "." in hostname
            
            # 只有既不是本地主机、也不包含点号的才报错
            if not is_local and not has_dot:
                raise ValueError(f"无效的域名: {domain}")
            
            session.current_domain = domain
        except Exception as e:
            logger.warning(f"[{session.id}] URL 解析失败: {url}, 错误: {e}")
            yield self.formatter.pack_error(
                "页面地址异常，请检查是否打开了正确的网站",
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
        site_config = config_engine.get_site_config(domain, tab.html)
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
        
        extractor = config_engine.get_site_extractor(domain)
        logger.debug(f"[{session.id}] 使用提取器: {extractor.get_id()}")
        
        # 创建执行器
        executor = WorkflowExecutor(
            tab=tab,
            stealth_mode=stealth_mode,
            should_stop_checker=self._should_stop_checker,
            extractor=extractor,
            image_config=image_config,
            stream_config=stream_config
        )
        
        result_container_selector = selectors.get("result_container", "")
        
        try:
            for step in workflow:
                if self._should_stop_checker():
                    logger.info(f"[{session.id}] 工作流被用户中断")
                    break
                
                action = step.get('action', '')
                target_key = step.get('target', '')
                optional = step.get('optional', False)
                param_value = step.get('value')
                
                selector = selectors.get(target_key, '')
                
                if not selector and action not in ("WAIT", "KEY_PRESS"):
                    if optional:
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
                    
                    if action in ("STREAM_WAIT", "STREAM_OUTPUT"):
                        result_container_selector = selector
                        
                except (ElementNotFoundError, WorkflowError):
                    break
                except Exception as e:
                    if not optional:
                        yield self.formatter.pack_error(f"执行中断: {str(e)}")
                        break
            
            # 图片提取
            logger.debug(f"[PROBE] Workflow 循环结束，image_enabled={image_extraction_enabled}, should_stop={self._should_stop_checker()}")
            if image_extraction_enabled and not self._should_stop_checker():
                logger.debug("[PROBE] 进入图片提取分支")
                try:
                    images = self._extract_images_after_stream(
                        tab=tab,
                        extractor=extractor,
                        image_config=image_config,
                        result_selector=result_container_selector,
                        completion_id=executor._completion_id
                    )
                    
                    if images:
                        download_urls = image_config.get("download_urls", False)
                        if download_urls:
                            images = self._download_url_images(images, tab=tab)
                        
                        logger.debug(f"[PROBE] 即将发送图片（Markdown），数量={len(images)}")

                        try:
                            first_url = (images[0].get("url") or "").strip() if images else ""
                            if first_url:
                                public_base = os.getenv("PUBLIC_BASE_URL", "").strip()
                                if public_base:
                                    md_url = public_base.rstrip("/") + first_url
                                else:
                                    md_url = f"http://{AppConfig.get_host()}:{AppConfig.get_port()}{first_url}"

                                md = f"\n\n![image]({md_url})\n\n"
                                yield self.formatter.pack_chunk(md, completion_id=executor._completion_id)
                                logger.debug(f"[MD_IMAGE] 已发送 Markdown 图片链接: {md_url}")
                            else:
                                logger.warning("[MD_IMAGE] images[0].url 为空，跳过 Markdown 输出")
                        except Exception as e:
                            logger.warning(f"[MD_IMAGE] 发送 Markdown 图片链接失败: {e}")            
                except Exception as e:
                    logger.warning(f"[{session.id}] 图片提取失败: {e}")
        
        finally:
            yield self.formatter.pack_finish()
    
    def _extract_images_after_stream(
        self,
        tab,
        extractor,
        image_config: Dict,
        result_selector: str,
        completion_id: str = None
    ) -> List[Dict]:
        """流式输出结束后提取图片"""
        from app.core.elements import ElementFinder
        from app.core.extractors.image_extractor import image_extractor
        
        debounce = image_config.get("debounce_seconds", 2.0)
        if debounce > 0:
            elapsed = 0
            step = 0.1
            while elapsed < debounce:
                if self._should_stop_checker():
                    return []
                time.sleep(step)
                elapsed += step
        
        finder = ElementFinder(tab)
        
        try:
            elements = finder.find_all(result_selector, timeout=1)
            if not elements:
                return []
            
            last_element = elements[-1]
                
            if hasattr(extractor, 'extract_images'):
                images = extractor.extract_images(
                    last_element,
                    config=image_config,
                    container_selector_fallback=result_selector
                )
            else:
                images = image_extractor.extract(
                    last_element,
                    config=image_config,
                    container_selector_fallback=result_selector
                )
            
            # 🆕 如果图片是不可直连的外链（如 googleusercontent），尝试截图落盘并替换为本地 URL
            try:
                images = self._try_screenshot_images_to_local(tab, last_element, images, image_config)
            except Exception as e:
                logger.warning(f"截图落盘失败（已忽略）: {e}")

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
    
    def _execute_workflow_non_stream(
        self, 
        session: TabSession,
        messages: List[Dict]
    ) -> Generator[str, None, None]:
        """非流式工作流执行"""
        collected_content = []
        error_data = None
        
        for chunk in self._execute_workflow_stream(session, messages):
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