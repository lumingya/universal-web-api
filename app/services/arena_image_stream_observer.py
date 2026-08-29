"""
app/services/arena_image_stream_observer.py - Arena 生图守卫与流式监控观察者

将 Arena 专属的生图状态机、跳过比较逻辑与原生停止按钮守卫封装为 StreamObserver，
供 StreamMonitor 在生图会话中挂载。
"""

from typing import Any, Dict, List, Optional, Tuple

from app.core.config import logger
from app.core.stream_observer import (
    StreamObserver,
    register_stream_observer_factory,
)
from app.services.arena_image_generation import (
    ARENA_NATIVE_STOP_SELECTOR as ARENA_IMAGE_NATIVE_STOP_SELECTOR,
    ArenaImageGenerationError,
    ArenaImageGenerationGuard,
    auto_skip_arena_direct_comparison,
    evaluate_arena_direct_generation_state,
    is_arena_image_generation_request,
)


class ArenaImageStreamObserver(StreamObserver):
    """Arena 生图守卫与流式状态机观察者。"""

    def __init__(
        self,
        guard: Optional[ArenaImageGenerationGuard] = None,
        image_config: Optional[Dict[str, Any]] = None,
    ):
        self.guard = guard
        self.image_config = image_config or {}
        self.last_observation = None
        self.last_direct_eval: Optional[Dict[str, Any]] = None

    def on_stream_start(self, monitor: Any, ctx: Any) -> None:
        if self.guard is None and hasattr(monitor, "tab"):
            selector = getattr(monitor, "selector", "")
            token = self.image_config.get("arena_result_baseline_token", "")
            prop = self.image_config.get("arena_result_baseline_property", "")
            self.guard = ArenaImageGenerationGuard(
                monitor.tab,
                result_selector=selector,
                baseline_token=token,
                baseline_property=prop,
            )

    def _evaluate_direct_state(
        self,
        monitor: Any,
        ctx: Any,
        current_has_new_rendered_image: bool,
        still_generating: bool,
    ) -> Tuple[bool, bool, Optional[Dict[str, Any]]]:
        image_config = getattr(monitor, "_image_config", None) or self.image_config or {}
        parser_id = str(image_config.get("_parser_id", "") or getattr(monitor, "_workflow_parser_id", "") or "").lower()
        parser_side = str(image_config.get("_parser_target_side", "") or "").strip().lower()
        is_battle = "battle" in parser_id or parser_side in {"left", "right"}
        tab = getattr(monitor, "tab", None)
        if is_battle or not tab:
            return current_has_new_rendered_image, still_generating, None

        try:
            auto_skip_arena_direct_comparison(tab)
            baseline_depth = int(ctx.baseline_snapshot.get("groups_count") or 0) if ctx.baseline_snapshot else 0
            prompt_text = str(getattr(monitor, "_prompt", "") or getattr(monitor, "prompt", "") or getattr(ctx, "prompt", "") or "")
            eval_data = evaluate_arena_direct_generation_state(
                tab,
                baseline_depth=baseline_depth,
                current_prompt=prompt_text,
                stop_selector=ARENA_IMAGE_NATIVE_STOP_SELECTOR,
            )
            if isinstance(eval_data, dict):
                if eval_data.get("error_code"):
                    raise ArenaImageGenerationError(
                        str(eval_data.get("error_code")),
                        str(eval_data.get("error_msg") or "Arena image generation error"),
                    )
                status = eval_data.get("status")
                if status == "SUCCESS" and eval_data.get("image_urls"):
                    urls = [u for u in eval_data.get("image_urls") or [] if isinstance(u, str) and u.strip()]
                    if urls:
                        monitor._final_image_urls = list(urls)
                        if hasattr(monitor, "_prefetch_snapshot_image_urls"):
                            monitor._prefetch_snapshot_image_urls({"image_urls": urls, "image_references": urls})
                    return True, False, eval_data
                if status in {"GENERATING", "WAITING_HYDRATION"}:
                    return current_has_new_rendered_image, True, eval_data
                return current_has_new_rendered_image, still_generating, eval_data
            return current_has_new_rendered_image, still_generating, None
        except ArenaImageGenerationError:
            raise
        except Exception as exc:
            logger.debug(f"Direct generation evaluation non-fatal error: {exc}")
            return current_has_new_rendered_image, still_generating, None

    def on_poll_tick(
        self,
        monitor: Any,
        ctx: Any,
        snapshot: Dict[str, Any],
        state: Dict[str, Any],
    ) -> None:
        current_has_new_rendered_image = state.get("current_has_new_rendered_image", False)
        still_generating = state.get("still_generating", False)
        interrupted_stream_recovery = state.get("interrupted_stream_recovery", False)
        stream_recovery_refresh_done = state.get("stream_recovery_refresh_done", False)
        post_refresh_settled = state.get("post_refresh_settled", True)

        try:
            (
                current_has_new_rendered_image,
                still_generating,
                direct_eval,
            ) = self._evaluate_direct_state(
                monitor,
                ctx,
                current_has_new_rendered_image,
                still_generating,
            )
        except ArenaImageGenerationError as exc:
            state["terminal_error"] = exc
            state["terminal_break"] = True
            return
        self.last_direct_eval = direct_eval
        state["current_has_new_rendered_image"] = current_has_new_rendered_image

        if self.guard is not None:
            arena_observation = self.guard.observe(current_has_new_rendered_image)
            self.last_observation = arena_observation

            if direct_eval is not None:
                still_generating = bool(direct_eval.get("still_generating", arena_observation.still_generating))
            else:
                still_generating = arena_observation.still_generating
            state["still_generating"] = still_generating

            recovery_has_output = bool(arena_observation.has_new_image or arena_observation.terminal_error)
            state["recovery_has_output"] = recovery_has_output

            if direct_eval is not None:
                recovery_confirmed = bool(
                    direct_eval.get("is_complete")
                    and (not stream_recovery_refresh_done or post_refresh_settled)
                )
            else:
                recovery_confirmed = bool(
                    arena_observation.is_complete
                    and (
                        (
                            interrupted_stream_recovery
                            and stream_recovery_refresh_done
                            and (post_refresh_settled or arena_observation.has_new_image)
                        )
                        or not interrupted_stream_recovery
                    )
                )
            state["recovery_confirmed"] = recovery_confirmed

            if arena_observation.terminal_error is not None:
                state["terminal_error"] = arena_observation.terminal_error

            if recovery_confirmed:
                state["terminal_break"] = True
                logger.info("[Arena Image] 原生停止按钮已消失且已确认新图片，生成完成")

    def get_active_stop_recovery_grace_seconds(self, default: float) -> Optional[float]:
        cfg = getattr(self.guard, "image_config", None) or self.image_config
        val = cfg.get("arena_active_stop_recovery_grace_seconds", 60.0)
        try:
            return float(val)
        except Exception:
            return 60.0

    def get_stream_recovery_max_refreshes(self, default: int) -> Optional[int]:
        cfg = getattr(self.guard, "image_config", None) or self.image_config
        hard_timeout = default * 20.0
        return ArenaImageGenerationGuard.max_refreshes(cfg, hard_timeout)

    def get_interrupted_refresh_interval(self, default: float) -> Optional[float]:
        cfg = getattr(self.guard, "image_config", None) or self.image_config
        return ArenaImageGenerationGuard.refresh_interval_seconds(cfg)


def _arena_image_observer_factory(
    tab: Any,
    user_input: Any,
    image_config: Dict[str, Any],
) -> Optional[ArenaImageStreamObserver]:
    try:
        url = str(getattr(tab, "url", "") or "")
        uploaded = image_config.get("uploaded_image_paths") or []
        if is_arena_image_generation_request(url, user_input, image_config, uploaded):
            return ArenaImageStreamObserver(image_config=image_config)
    except Exception as exc:
        logger.debug(f"[StreamObserver] Arena 生图观察者工厂检查失败（忽略）: {exc}")
    return None


register_stream_observer_factory(_arena_image_observer_factory)

__all__ = ["ArenaImageStreamObserver"]
