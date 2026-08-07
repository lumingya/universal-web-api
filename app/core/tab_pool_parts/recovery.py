"""
app/core/tab_pool_parts/recovery.py - 错误标签页隔离与 AI 恢复服务

职责：
- TabQuarantineEntry：错误标签页的隔离记录（由 TabPoolManager 在池锁内维护）
- TabRecoveryService：单线程后台服务，等旧 worker 退出后按配置决定
  截图 → AI 判断 → 刷新 → 解除隔离 / 永久隔离

锁纪律：
- 本模块所有阻塞操作（等待旧 worker、截图、AI HTTP 调用、刷新）都发生在
  自持的 ThreadPoolExecutor(max_workers=1) 线程里，绝不在事件循环线程执行，
  也绝不持有 TabPoolManager 的池锁。
- 与 TabPoolManager 的交互只通过构造时注入的两个回调
  （resolve_quarantine / get_quarantine_entry），本模块不 import manager，
  避免循环导入；request_manager 的 import 放在函数内。
"""
import base64
import json
import threading
import time
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from app.core.config import AppConfig, logger


# DrissionPage/CDP 调用可能在底层连接断开时永久阻塞。超时后 daemon
# 线程无法被强制终止，因此限制同时存活的超时调用数量，避免每次异常
# 都累积一个永不退出的线程。
_RECOVERY_CALL_SLOTS = threading.BoundedSemaphore(4)


@dataclass
class TabQuarantineEntry:
    """错误标签页的隔离记录（在 TabPoolManager 的池锁保护下读写）"""
    raw_tab_id: str
    persistent_index: int = 0
    task_id: str = ""
    tab: Any = None
    reason: str = ""
    since: float = field(default_factory=time.monotonic)
    permanent: bool = False
    attempts: int = 0
    session_id: str = ""
    url: str = ""


class TabRecoveryService:
    """错误标签页 AI 恢复服务（由 TabPoolManager 懒创建并持有，单例风格）"""

    WORKER_POLL_INTERVAL_SEC = 5.0
    # 拿不到旧 worker 线程引用时的时间宽限：隔离时刻起 180s 视为已退出
    WORKER_EXIT_FALLBACK_GRACE_SEC = 180.0
    SCREENSHOT_TIMEOUT_SEC = 30.0
    REFRESH_TIMEOUT_SEC = 30.0
    POST_REFRESH_SETTLE_SEC = 3.0
    MAX_ATTEMPT_CACHE_ENTRIES = 4096

    def __init__(
        self,
        *,
        resolve_quarantine: Callable[[str, bool], bool],
        get_quarantine_entry: Callable[[str], Optional[TabQuarantineEntry]],
    ):
        self._resolve_quarantine = resolve_quarantine
        self._get_quarantine_entry = get_quarantine_entry
        self._attempts: OrderedDict[str, int] = OrderedDict()
        self._attempts_lock = threading.Lock()
        self._shutdown = False
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="tab-recovery",
        )

    # ================= 对外接口 =================

    def submit(self, entry: TabQuarantineEntry) -> bool:
        """受理一个隔离标签页；仅入队，不阻塞调用线程。"""
        if entry is None or not str(getattr(entry, "raw_tab_id", "") or "").strip():
            return False
        if self._shutdown:
            return False
        try:
            self._executor.submit(self._process_safe, entry)
            logger.info(
                f"[TabRecovery] 已受理隔离标签页，排队处理 "
                f"(raw={entry.raw_tab_id}, tab={entry.session_id or '-'}, "
                f"idx=#{entry.persistent_index or '-'}, task={entry.task_id or '-'}, "
                f"reason={entry.reason or '-'})"
            )
            return True
        except RuntimeError as e:
            logger.debug(f"[TabRecovery] submit failed ({entry.raw_tab_id}): {e}")
            return False

    def shutdown(self) -> None:
        self._shutdown = True
        with self._attempts_lock:
            self._attempts.clear()
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            logger.debug(f"[TabRecovery] executor shutdown failed: {e}")

    # ================= 主流程 =================

    def _process_safe(self, entry: TabQuarantineEntry) -> None:
        """线程池任务入口：吃掉所有异常，绝不向外抛。"""
        try:
            self._process(entry)
        except Exception as e:
            logger.warning(
                f"[TabRecovery] 恢复流程异常 (raw={entry.raw_tab_id}): {e}"
            )
            # 未知内部错误时保守处理：永久隔离，避免把状态不明的标签页重新入池。
            try:
                self._resolve(entry, release=False, reason=f"internal_error:{e}")
            except Exception:
                pass

    def _process(self, entry: TabQuarantineEntry) -> None:
        raw_id = entry.raw_tab_id
        logger.info(
            f"[TabRecovery] 开始处理隔离标签页 "
            f"(raw={raw_id}, tab={entry.session_id or '-'}, "
            f"task={entry.task_id or '-'}, reason={entry.reason or '-'})"
        )

        # 1. 等旧 worker 退出：串扰的根源是旧请求的 worker 线程可能仍阻塞在
        #    对该 tab 的同步 DrissionPage 调用里。
        wait_result = self._wait_for_worker_exit(entry)
        if wait_result == "aborted":
            logger.debug(f"[TabRecovery] 服务关闭，放弃处理 (raw={raw_id})")
            return
        if wait_result == "timeout":
            self._resolve(entry, release=False, reason="worker_never_exited")
            return
        logger.debug(f"[TabRecovery] 旧 worker 已确认退出 (raw={raw_id})")

        # 2. 次数检查：解除隔离后再次错误 → 不再调 API，直接永久隔离。
        with self._attempts_lock:
            attempts = int(self._attempts.get(raw_id, 0))
            if raw_id in self._attempts:
                # 最近使用的 raw id 保留在缓存尾部，便于后续按上限淘汰。
                self._attempts.move_to_end(raw_id)
        entry.attempts = attempts
        max_attempts = AppConfig.get_tab_recovery_max_attempts()
        if attempts >= max_attempts:
            logger.info(
                f"[TabRecovery] 恢复次数已用尽，不再调用 AI "
                f"(raw={raw_id}, attempts={attempts}, max={max_attempts})"
            )
            self._resolve(entry, release=False, reason="attempts_exhausted")
            return

        # 3. 功能开关：未启用时不截图不调 API，直接解除隔离——保持既有的
        #    自动重入池行为，但此时旧 worker 已确认退出，串扰已消除。
        if not AppConfig.is_tab_recovery_enabled():
            self._resolve(entry, release=True, reason="recovery_disabled")
            return

        # 4/5. 截图 + AI 判断；任一失败 → verdict=None（未知）
        verdict: Optional[bool] = None
        detail = ""
        image_b64 = ""
        try:
            image_b64 = self._capture_screenshot(entry)
        except Exception as e:
            detail = f"screenshot_failed:{e}"
            logger.warning(f"[TabRecovery] 截图失败 (raw={raw_id}): {e}")

        if image_b64:
            try:
                verdict, detail = self._judge_recoverable(entry, image_b64)
                logger.info(
                    f"[TabRecovery] AI 判断结果 "
                    f"(raw={raw_id}, recoverable={verdict}, detail={detail or '-'})"
                )
            except Exception as e:
                verdict, detail = None, f"api_error:{e}"
                logger.warning(f"[TabRecovery] AI 判断失败 (raw={raw_id}): {e}")

        # 6. 决策
        if verdict is False:
            self._resolve(
                entry,
                release=False,
                reason=f"ai_judged_unrecoverable:{detail or '-'}",
            )
            return
        if verdict is None:
            if not AppConfig.is_tab_recovery_refresh_on_unknown():
                self._resolve(
                    entry,
                    release=False,
                    reason=f"judgement_unknown:{detail or '-'}",
                )
                return
            refresh_reason = "unknown_refresh"
        else:
            refresh_reason = "ai_judged_recoverable"

        # 7. 刷新与解除
        self._refresh_and_release(entry, refresh_reason)

    # ================= 步骤实现 =================

    def _wait_for_worker_exit(self, entry: TabQuarantineEntry) -> str:
        """轮询旧 worker 是否退出。返回 'exited' / 'timeout' / 'aborted'。"""
        max_wait = AppConfig.get_tab_recovery_worker_exit_wait_sec()
        deadline = time.monotonic() + max(0.0, float(max_wait))
        while True:
            if self._shutdown:
                return "aborted"
            if self._is_worker_exited(entry):
                return "exited"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"
            time.sleep(min(self.WORKER_POLL_INTERVAL_SEC, max(0.1, remaining)))

    def _is_worker_exited(self, entry: TabQuarantineEntry) -> bool:
        task_id = str(entry.task_id or "").strip()
        if not task_id:
            # 隔离时没有绑定任务，无 worker 可等
            return True

        ctx = None
        try:
            from app.services.request_manager import request_manager

            ctx = request_manager.get_request(task_id)
        except Exception as e:
            logger.debug(
                f"[TabRecovery] 查询请求上下文失败 (task={task_id}): {e}"
            )

        thread = getattr(ctx, "worker_thread", None) if ctx is not None else None
        if thread is not None and hasattr(thread, "is_alive"):
            try:
                return not thread.is_alive()
            except Exception as e:
                logger.debug(
                    f"[TabRecovery] 读取 worker 线程状态失败 (task={task_id}): {e}"
                )

        # 拿不到线程引用（非流式请求 worker 在 request_lifecycle 内创建、
        # 或请求上下文已被清理）：回退时间宽限，隔离时刻起 180s 视为已退出。
        elapsed = time.monotonic() - float(entry.since or 0.0)
        return elapsed >= self.WORKER_EXIT_FALLBACK_GRACE_SEC

    def _capture_screenshot(self, entry: TabQuarantineEntry) -> str:
        tab = entry.tab
        if tab is None:
            raise RuntimeError("tab reference unavailable")
        # DrissionPage 4.x：get_screenshot(as_base64=True) 返回 PNG 的 base64 字符串
        data = self._call_with_timeout(
            lambda: tab.get_screenshot(as_base64=True),
            self.SCREENSHOT_TIMEOUT_SEC,
            "screenshot",
        )
        if isinstance(data, (bytes, bytearray)):
            data = base64.b64encode(bytes(data)).decode("ascii")
        text = str(data or "").strip()
        if not text:
            raise RuntimeError("empty screenshot payload")
        return text

    def _judge_recoverable(
        self,
        entry: TabQuarantineEntry,
        image_b64: str,
    ) -> Tuple[Optional[bool], str]:
        """调用 OpenAI 兼容接口判断刷新能否恢复。返回 (verdict, detail)。"""
        api_url = AppConfig.get_tab_recovery_api_url()
        if api_url:
            target_url = api_url
            api_key = AppConfig.get_tab_recovery_api_key()
            local_mode = False
        else:
            # 本地模式：走本服务自身的 /v1/chat/completions，
            # 由其他空闲标签页完成实际判断
            target_url = (
                f"http://127.0.0.1:{AppConfig.get_port()}/v1/chat/completions"
            )
            api_key = (
                AppConfig.get_auth_token() if AppConfig.is_auth_enabled() else ""
            )
            local_mode = True

        site_url = str(entry.url or "").strip() or "未知"
        prompt = (
            "这是反代服务的一个疑似卡死的网页标签页截图，"
            f"站点为 {site_url}，错误原因 {entry.reason or '未知'}。"
            "请判断刷新页面是否可能恢复正常使用。只输出 JSON："
            "{\"recoverable\": true/false, \"reason\": \"...\"}。"
            "登录失效、封号提示、人机验证页输出 false；"
            "加载中、白屏、内容正常、临时错误提示输出 true。"
        )
        payload = {
            "model": AppConfig.get_tab_recovery_model(),
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}",
                            },
                        },
                    ],
                }
            ],
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        timeout_sec = AppConfig.get_tab_recovery_timeout_sec()
        request = urllib.request.Request(
            target_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        logger.debug(
            f"[TabRecovery] 调用 AI 判断 "
            f"(raw={entry.raw_tab_id}, url={target_url}, "
            f"local_mode={local_mode}, timeout={timeout_sec}s)"
        )
        if local_mode:
            # 本地回环地址不应经过环境代理
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            response = opener.open(request, timeout=timeout_sec)
        else:
            response = urllib.request.urlopen(request, timeout=timeout_sec)
        with response:
            body = response.read().decode("utf-8", "replace")

        data = json.loads(body)
        choices = data.get("choices") or []
        if not choices:
            return None, "empty_choices"
        message = choices[0].get("message") or {}
        content = message.get("content")
        text = self._flatten_message_content(content)
        parsed = self._extract_first_json_object(text)
        if not isinstance(parsed, dict):
            return None, f"parse_failed:{text[:120]!r}"
        verdict = self._coerce_bool(parsed.get("recoverable"))
        detail = str(parsed.get("reason") or "").strip()
        if verdict is None:
            return None, f"invalid_recoverable_field:{parsed.get('recoverable')!r}"
        return verdict, detail

    def _refresh_and_release(
        self,
        entry: TabQuarantineEntry,
        refresh_reason: str,
    ) -> None:
        raw_id = entry.raw_tab_id
        try:
            tab = entry.tab
            if tab is None:
                raise RuntimeError("tab reference unavailable")
            self._call_with_timeout(
                lambda: tab.refresh(),
                self.REFRESH_TIMEOUT_SEC,
                "refresh",
            )
            time.sleep(max(0.0, float(self.POST_REFRESH_SETTLE_SEC)))
            current_url = self._call_with_timeout(
                lambda: str(getattr(tab, "url", "") or ""),
                self.REFRESH_TIMEOUT_SEC,
                "health-check",
            )
            if not str(current_url or "").strip():
                raise RuntimeError("empty url after refresh")
        except Exception as e:
            logger.warning(f"[TabRecovery] 刷新/健康检查失败 (raw={raw_id}): {e}")
            self._resolve(entry, release=False, reason=f"refresh_failed:{e}")
            return

        with self._attempts_lock:
            self._attempts[raw_id] = int(self._attempts.get(raw_id, 0)) + 1
            self._attempts.move_to_end(raw_id)
            entry.attempts = self._attempts[raw_id]
            while len(self._attempts) > self.MAX_ATTEMPT_CACHE_ENTRIES:
                self._attempts.popitem(last=False)
        self._resolve(entry, release=True, reason=refresh_reason)
        logger.info(
            f"[TabRecovery] 标签页已恢复并重新入池 "
            f"(raw={raw_id}, idx=#{entry.persistent_index or '-'}, "
            f"url={str(current_url)[:80]}, attempts={entry.attempts}, "
            f"reason={refresh_reason})"
        )

    # ================= 辅助 =================

    def _resolve(
        self,
        entry: TabQuarantineEntry,
        *,
        release: bool,
        reason: str,
    ) -> None:
        entry.reason = str(reason or "").strip() or entry.reason
        try:
            resolved = bool(self._resolve_quarantine(entry.raw_tab_id, release))
        except Exception as e:
            logger.warning(
                f"[TabRecovery] 解除/永久隔离操作失败 "
                f"(raw={entry.raw_tab_id}, release={release}): {e}"
            )
            return
        if release:
            logger.info(
                f"[TabRecovery] 解除隔离，标签页将由下次扫描重新入池 "
                f"(raw={entry.raw_tab_id}, reason={reason}, resolved={resolved})"
            )
        else:
            logger.info(
                f"[TabRecovery] 永久隔离标签页 "
                f"(raw={entry.raw_tab_id}, reason={reason}, resolved={resolved})"
            )

    @staticmethod
    def _call_with_timeout(fn: Callable[[], Any], timeout_sec: float, label: str) -> Any:
        """在守护线程中执行阻塞调用并限时等待，防止 CDP 调用无限挂起。"""
        wait_timeout = max(0.1, float(timeout_sec))
        if not _RECOVERY_CALL_SLOTS.acquire(timeout=wait_timeout):
            raise TimeoutError(f"{label} call slots exhausted")

        result: Dict[str, Any] = {}
        done = threading.Event()

        def _runner():
            try:
                result["value"] = fn()
            except BaseException as e:  # noqa: BLE001 - 结果原样转抛给调用方
                result["error"] = e
            finally:
                done.set()
                _RECOVERY_CALL_SLOTS.release()

        thread = threading.Thread(
            target=_runner,
            name=f"tab-recovery-{label}",
            daemon=True,
        )
        try:
            thread.start()
        except BaseException:
            _RECOVERY_CALL_SLOTS.release()
            raise
        if not done.wait(wait_timeout):
            raise TimeoutError(f"{label} timed out after {timeout_sec}s")
        if "error" in result:
            raise result["error"]
        return result.get("value")

    @staticmethod
    def _flatten_message_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(parts)
        return str(content or "")

    @staticmethod
    def _extract_first_json_object(text: str) -> Optional[Dict[str, Any]]:
        """宽松提取文本中第一个平衡的 {...} 并解析为 dict。"""
        source = str(text or "")
        start = source.find("{")
        while start != -1:
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(source)):
                char = source[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = source[start:index + 1]
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, dict):
                                return parsed
                        except Exception:
                            pass
                        break
            start = source.find("{", start + 1)
        return None

    @staticmethod
    def _coerce_bool(value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
        return None
