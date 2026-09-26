"""CommandEngine 的一部分（R2-3 拆分）：页面内容检查：表达式解析、文本匹配、快照求值与锁存。

方法原样从 command_engine.py 迁出，行为不变；通过 mixin 组合回 CommandEngine，
类属性与实例状态都在 CommandEngine（command_engine.py）里定义。
"""

import re
import time
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from app.services.command_engine_common import _NON_ASCII_RE, _WHITESPACE_RE, _WORD_LIKE_KEYWORD_RE, logger

if TYPE_CHECKING:
    from app.core.tab_pool import TabSession  # noqa: F401


class CommandEnginePageCheckMixin:
    """页面内容检查：表达式解析、文本匹配、快照求值与锁存"""

    @staticmethod
    def _parse_page_check_expression(value: str):
        """解析 page_check 的 value 字段，支持 || (OR) 和 && (AND) 语法。

        返回 (operator, keywords_list):
        - ("or",  [kw1, kw2, ...])  — 任意一个关键词命中即触发
        - ("and", [kw1, kw2, ...])  — 所有关键词都命中才触发
        - ("single", [value])       — 单关键词（向后兼容）

        当 || 和 && 同时出现时，|| 优先拆分。
        """
        raw = str(value or "").strip()
        if not raw:
            return ("single", [])
        if "||" in raw:
            parts = [p.strip() for p in raw.split("||") if p.strip()]
            return ("or", parts) if parts else ("single", [])
        if "&&" in raw:
            parts = [p.strip() for p in raw.split("&&") if p.strip()]
            return ("and", parts) if parts else ("single", [])
        return ("single", [raw])
    @staticmethod
    def _normalize_match_text(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return _WHITESPACE_RE.sub(" ", text)
    def _get_compact_haystack(self, hay: str) -> str:
        """去空白版页面文本（带缓存）。

        page_check 每个非 ASCII 关键词都要一份去空白文本，
        而页面快照往往有几十上百 KB；原实现按关键词重复做全文 re.sub，
        一次检查里同一段文本会被重复扫描 N 次。这里按内容缓存一份即可。
        """
        if not hay:
            return ""
        cached = self._compact_haystack_cache
        # 用对象身份比较：同一轮检查里各个关键词拿到的 hay 是同一个字符串对象，
        # 比 (len, hash) 更简单，也不存在哈希碰撞返回错误结果的可能。
        if cached is not None and cached[0] is hay:
            return cached[1]
        compact = _WHITESPACE_RE.sub("", hay)
        self._compact_haystack_cache = (hay, compact)
        return compact
    def _text_contains_needle(
        self,
        haystack: str,
        needle: str,
        pre_normalized_haystack: Optional[str] = None,
    ) -> bool:
        hay = pre_normalized_haystack if pre_normalized_haystack is not None else self._normalize_match_text(haystack)
        ned = self._normalize_match_text(needle)
        if not hay or not ned:
            return False

        if _NON_ASCII_RE.search(ned):
            compact_hay = self._get_compact_haystack(hay)
            compact_ned = _WHITESPACE_RE.sub("", ned)
            if compact_hay and compact_ned and compact_ned in compact_hay:
                return True

        # For plain word-like keywords (for example "battle"), prefer whole-word
        # matching to reduce accidental substring hits.
        if _WORD_LIKE_KEYWORD_RE.fullmatch(ned):
            pattern = rf"(?<![a-z0-9]){re.escape(ned)}(?![a-z0-9])"
            return re.search(pattern, hay) is not None

        return ned in hay
    def _get_page_check_snapshot_text(self, session: 'TabSession') -> str:
        if self._is_session_closed(session):
            return ""
        if self._is_page_check_refreshing(session):
            return ""
        now = time.time()
        cached = getattr(session, "_pc_snapshot_cached", None)
        if isinstance(cached, tuple) and len(cached) == 2:
            cached_ts, cached_text = cached
            try:
                cached_ts = float(cached_ts or 0.0)
            except Exception:
                cached_ts = 0.0
            if cached_ts > 0.0 and now - cached_ts < 0.5:
                return str(cached_text or "")

        if self._is_page_check_backing_off(session):
            return ""

        self._try_wake_tab(session, reason="page_check")

        try:
            snapshot = self._run_page_check_js(
                session,
                "return window.__pcObserver ? String(window.__pcSnapshot || '') : null"
            )
            self._record_page_check_js_success(session)
            if snapshot is not None:
                snapshot_text = str(snapshot or "")
                if snapshot_text.strip():
                    setattr(session, "_pc_snapshot_cached", (now, snapshot_text))
                    return snapshot_text
        except Exception as e:
            if self._is_session_closed(session):
                return ""
            self._record_page_check_js_failure(session, e, "snapshot_observer")
            if self._mark_session_closed_if_disconnected(session, e, "page_check_snapshot_observer"):
                return ""
            if self._is_page_check_refreshing(session):
                return ""
            pass

        try:
            page_text = str(self._run_page_check_js(session, self._PAGE_CHECK_SNAPSHOT_JS) or "")
            self._record_page_check_js_success(session)
            if page_text.strip():
                setattr(session, "_pc_snapshot_cached", (now, page_text))
                return page_text
        except Exception as e:
            if self._is_session_closed(session):
                return ""
            self._record_page_check_js_failure(session, e, "snapshot_body")
            if self._mark_session_closed_if_disconnected(session, e, "page_check_snapshot_body"):
                return ""
            if self._is_page_check_refreshing(session):
                return ""
            pass

        try:
            title_text = str(self._run_page_check_js(session, "return document.title || '';") or "")
            self._record_page_check_js_success(session)
            setattr(session, "_pc_snapshot_cached", (now, title_text))
            return title_text
        except Exception as e:
            if self._is_session_closed(session):
                return ""
            self._record_page_check_js_failure(session, e, "snapshot_title")
            self._mark_session_closed_if_disconnected(session, e, "page_check_snapshot_title")
            return ""
    def _build_page_check_snapshot_preview(
        self,
        snapshot: str,
        matched_keywords: Optional[List[str]] = None,
        limit: int = 180,
    ) -> str:
        normalized_snapshot = self._normalize_match_text(snapshot)
        if not normalized_snapshot:
            return ""

        start = 0
        for keyword in matched_keywords or []:
            normalized_keyword = self._normalize_match_text(keyword)
            if not normalized_keyword:
                continue
            index = normalized_snapshot.find(normalized_keyword)
            if index != -1:
                start = max(0, index - 60)
                break

        end = min(len(normalized_snapshot), start + max(40, limit))
        preview = normalized_snapshot[start:end]
        if start > 0:
            preview = "..." + preview
        if end < len(normalized_snapshot):
            preview = preview + "..."
        return preview.replace("'", '"')
    def _evaluate_page_check_snapshot(
        self,
        snapshot: str,
        op: str,
        keywords: List[str],
    ) -> Dict[str, Any]:
        if not keywords:
            return {
                "hit": False,
                "matched_keywords": [],
                "snapshot_preview": "",
            }

        normalized_snapshot = self._normalize_match_text(snapshot)
        matched_keywords = [
            keyword for keyword in keywords
            if self._text_contains_needle(
                snapshot,
                keyword,
                pre_normalized_haystack=normalized_snapshot,
            )
        ]
        if op == "or":
            hit = bool(matched_keywords)
        else:
            hit = len(matched_keywords) == len(keywords)

        return {
            "hit": hit,
            "matched_keywords": matched_keywords,
            "snapshot_preview": self._build_page_check_snapshot_preview(snapshot, matched_keywords),
        }
    def _evaluate_page_check_expr(
        self,
        session: 'TabSession',
        op: str,
        keywords: List[str],
    ) -> Dict[str, Any]:
        snapshot = self._get_page_check_snapshot_text(session)
        result = self._evaluate_page_check_snapshot(snapshot, op, keywords)
        result["snapshot_text"] = snapshot
        return result
    def _evaluate_page_check_probe(
        self,
        session: 'TabSession',
        code: str,
    ) -> Dict[str, Any]:
        self._try_wake_tab(session, reason="page_check_probe")
        context = {
            "parser_id": str(getattr(session, "_workflow_parser_id", "") or "").strip().lower(),
            "target_side": str(getattr(session, "_workflow_target_side", "") or "").strip().lower(),
            "runtime_id": str(getattr(session, "_workflow_runtime_id", "") or "").strip(),
        }
        try:
            try:
                session.tab.run_js(
                    "return (() => { window.__codexWorkflowContext = arguments[0] || {}; return true; })()",
                    context,
                )
            except Exception as context_error:
                logger.debug(f"[CMD] 页面检查上下文注入失败（继续探测）: {context_error}")
            result = self._run_command_js(session.tab, code)
        except Exception as e:
            message = f"probe_js_failed: {e}"
            return {
                "hit": False,
                "result": message,
                "summary": message,
            }

        if isinstance(result, dict):
            hit = bool(result.get("hit"))
            summary = str(
                result.get("summary", result.get("result", result))
                or ""
            ).strip()
        else:
            hit = bool(result)
            summary = "" if result in (None, False, "", 0) else str(result).strip()

        return {
            "hit": hit,
            "result": result,
            "summary": summary[:180],
        }
    def _log_page_check_hit_details(
        self,
        command: Dict,
        session: 'TabSession',
        match_info: Dict[str, Any],
    ):
        matched_keywords = match_info.get("matched_keywords") or []
        preview = str(match_info.get("snapshot_preview") or "").strip()
        probe_summary = str(match_info.get("probe_summary") or "").strip()
        probe_suffix = f", JS探测='{probe_summary}'" if probe_summary else ""
        logger.info(
            f"[CMD] 页面检查命中详情: {command.get('name')} "
            f"(标签页={session.id}, 命中关键词={matched_keywords}, 快照预览='{preview}'{probe_suffix})"
        )
    def _check_page_content_expr(
        self, session: 'TabSession', op: str, keywords: list
    ) -> bool:
        """根据解析后的表达式 (op, keywords) 检查页面内容。

        - op="or"    任意一个关键词命中即返回 True
        - op="and"   所有关键词都命中才返回 True
        - op="single" 等同于 and（只有一个关键词）
        """
        return bool(self._evaluate_page_check_expr(session, op, keywords).get("hit"))
    def _check_page_content(self, session: 'TabSession', text: str) -> bool:
        needle = str(text or "").strip()
        if not needle:
            return False
        snapshot = self._get_page_check_snapshot_text(session)
        return self._text_contains_needle(snapshot, needle)
    def _reset_page_check_latch(self, command: Dict, session: 'TabSession', reason: str = ""):
        """Allow page_check commands to retrigger when previous execution did not complete successfully."""
        trigger = command.get("trigger", {}) or {}
        if str(trigger.get("type", "")).strip().lower() != "page_check":
            return
        if not trigger.get("reset_latch_on_failure", True):
            # Only block latch reset if the command actually started executing (not blocked by session acquire timeout, scheduling failures, or page navigation)
            if reason not in {"acquire_timeout", "workflow_schedule_failed"} and not (
                reason.startswith("navigated") or reason.startswith("tab_navigated")
            ):
                return

        key = (command.get("id"), getattr(session, "id", ""))
        if not key[0] or not key[1]:
            return

        normalized_text = str(trigger.get("value", "") or "").strip().lower()
        with self._lock:
            state = self._trigger_states.get(key)
            if not state:
                return
            state["page_key"] = normalized_text
            state["page_hit"] = False
            state["page_stable"] = False
            state["page_hit_since"] = 0.0

            if bool(trigger.get("once_per_request", False)):
                current_request_id = ""
                try:
                    for attr_name in ("_command_request_id", "_bound_request_id"):
                        req_id = str(getattr(session, attr_name, "") or "").strip()
                        if req_id:
                            current_request_id = req_id
                            break
                    if not current_request_id:
                        task_id = str(getattr(session, "current_task_id", "") or "").strip()
                        if task_id.startswith("req-"):
                            current_request_id = task_id
                except Exception:
                    pass
                if current_request_id and "triggered_requests" in state:
                    state["triggered_requests"].discard(current_request_id)

        if reason:
            logger.debug(
                f"[CMD] 页面检查锁存已重置: {command.get('name')} "
                f"(标签页={session.id}, 原因={reason})"
            )
