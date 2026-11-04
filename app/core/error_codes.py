"""Arena 错误码常量 —— **叶子模块，不得 import 任何 app.* 模块**。

存在的理由（见 docs/plans/REFACTOR_PLAN-2026-10.md 步骤 5）：

这几个常量原先分散定义在 ``app.core.workflow.arena_send_watchdog`` 与
``app.services.arena_image_generation`` 里，而 ``app.services.error_metadata``
需要它们。但 error_metadata 又被 ``app.core.config_parts`` 依赖，于是形成环：

    core.config → config_parts.cute_translator → services.error_metadata
                → core.workflow.arena_send_watchdog → core.config

把纯常量抽到这个不依赖任何东西的叶子模块，error_metadata 就不再需要反向依赖
core / services，上面这条环随之断开。

**新增常量请只放常量**：一旦 import 任何 app.* 模块，本模块的叶子性质失效，
整条环会立刻回来（导入环棘轮会拦住，但请不要尝试）。

原定义处（arena_send_watchdog / arena_image_generation）改为从本模块导入并
再导出，保证既有 import 路径不失效。
"""

from __future__ import annotations

# 原 app.core.workflow.arena_send_watchdog
ARENA_SEND_NO_TARGET_AFTER_RETRY = "arena_send_no_target_after_retry"
ARENA_PAGE_ERROR = "arena_page_error"

# 原 app.services.arena_image_generation
ARENA_PROMPT_REJECTED_CODE = "arena_prompt_rejected"
ARENA_IMAGE_GENERATION_FAILED_CODE = "arena_image_generation_failed"
ARENA_IMAGE_UNCHANGED_CODE = "arena_image_unchanged"

ARENA_NON_RETRYABLE_CODES = frozenset(
    {
        ARENA_PROMPT_REJECTED_CODE,
        ARENA_IMAGE_GENERATION_FAILED_CODE,
        ARENA_IMAGE_UNCHANGED_CODE,
    }
)
