"""Length-aware prompt chunk planning for sequential browser workflows."""

from dataclasses import dataclass
from math import ceil
from typing import Callable, List


CHUNK_SEPARATOR = "\n\n"


class InputChunkingError(ValueError):
    """Raised when the configured limit cannot hold a valid chunk."""


@dataclass(frozen=True)
class PromptChunk:
    index: int
    total: int
    content: str
    instruction: str
    prompt: str


def render_chunk_instruction(index: int, total: int) -> str:
    if index < total:
        return (
            f"该内容为第 {index}/{total} 部分，请只回复“理解”，等待第 {index + 1}/{total} "
            "部分内容后，再联系上下文进行完整回复。"
        )
    return (
        f"该内容为第 {index}/{total} 部分，也是最后一部分。"
        "请联系此前收到的所有分块内容，对完整请求进行回复。"
    )


def _instruction_suffix(instruction: str) -> str:
    return f"{CHUNK_SEPARATOR}{instruction}" if instruction else ""


def _capacities(
    total: int,
    limit: int,
    instruction_renderer: Callable[[int, int], str],
) -> tuple[List[str], List[int]]:
    instructions = [instruction_renderer(index, total) for index in range(1, total + 1)]
    # Keep one character of headroom for chunked sends. This matches the
    # configured-limit semantics used by sites that reject an exact-boundary input.
    capacities = [limit - 1 - len(_instruction_suffix(instruction)) for instruction in instructions]
    return instructions, capacities


def plan_prompt_chunks(
    content: str,
    limit: int,
    *,
    instruction_renderer: Callable[[int, int], str] = render_chunk_instruction,
) -> List[PromptChunk]:
    """Split content into the fewest valid prompts without exceeding ``limit``.

    Once the minimum chunk count is known, early chunks start near ``limit / n``.
    They grow only when required to keep the remaining content inside later chunks.
    """
    text = str(content or "")
    try:
        max_length = int(limit)
    except (TypeError, ValueError) as exc:
        raise InputChunkingError("分块限制必须是整数") from exc

    if max_length <= 0:
        raise InputChunkingError("分块限制必须大于 0")
    if len(text) <= max_length:
        return [PromptChunk(1, 1, text, "", text)]
    if not text:
        return [PromptChunk(1, 1, "", "", "")]

    total = max(2, ceil(len(text) / max_length))
    instructions: List[str] = []
    capacities: List[int] = []
    while total <= len(text):
        instructions, capacities = _capacities(total, max_length, instruction_renderer)
        if any(capacity <= 0 for capacity in capacities):
            raise InputChunkingError("分块限制过小，无法容纳分块说明词")
        total_capacity = sum(capacities)
        if total_capacity >= len(text):
            break
        capacity_deficit = len(text) - total_capacity
        total += max(1, ceil(capacity_deficit / max(capacities)))
    else:
        raise InputChunkingError("分块限制过小，无法容纳分块说明词")

    chunks: List[PromptChunk] = []
    offset = 0
    remaining = len(text)
    target_prompt_length = max_length // total
    future_capacity = sum(capacities[1:])

    for position in range(total):
        capacity = capacities[position]
        minimum_content = max(1, remaining - future_capacity)
        suffix = _instruction_suffix(instructions[position])
        preferred_content = max(1, target_prompt_length - len(suffix))

        if position == total - 1:
            content_length = remaining
        else:
            content_length = min(capacity, max(minimum_content, preferred_content))

        if content_length <= 0 or content_length > capacity:
            raise InputChunkingError("无法在限制内生成有效分块")

        part_content = text[offset : offset + content_length]
        prompt = f"{part_content}{suffix}"
        chunks.append(
            PromptChunk(
                index=position + 1,
                total=total,
                content=part_content,
                instruction=instructions[position],
                prompt=prompt,
            )
        )
        offset += content_length
        remaining -= content_length
        if position + 1 < total:
            future_capacity -= capacities[position + 1]

    if offset != len(text) or "".join(chunk.content for chunk in chunks) != text:
        raise InputChunkingError("分块内容校验失败")
    if any(len(chunk.prompt) > max_length for chunk in chunks):
        raise InputChunkingError("分块结果超过长度限制")

    return chunks


__all__ = [
    "InputChunkingError",
    "PromptChunk",
    "plan_prompt_chunks",
    "render_chunk_instruction",
]
