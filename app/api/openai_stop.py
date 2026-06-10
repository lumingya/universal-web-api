from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import SSEFormatter
from app.services.sse_utils import (
    extract_sse_error_message,
    iter_sse_payloads,
    sse_frame_data_text,
)


@dataclass
class StopSequenceStreamState:
    sequences: List[str]
    single_choice: bool = False
    upstream_single_choice: bool = False
    max_sequence_len: int = 0
    tail: str = ""
    stopped: bool = False
    stopped_choice_keys: set[str] = field(default_factory=set)
    seen_choice_keys: set[str] = field(default_factory=set)
    choice_tails: Dict[str, str] = field(default_factory=dict)
    choice_pending_parts: Dict[str, List[Any]] = field(default_factory=dict)
    pending_parts: List[Any] = field(default_factory=list)
    sse_frame_buffer: str = ""


MAX_STOP_SSE_FRAME_BUFFER_CHARS = 262144


def normalize_openai_stop_sequences(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        sequences: List[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item)
            if text:
                sequences.append(text)
        return sequences
    text = str(value)
    return [text] if text else []


def build_stop_sequence_stream_state(
    value: Any,
    *,
    single_choice: bool = False,
    upstream_single_choice: bool = False,
) -> StopSequenceStreamState:
    sequences = normalize_openai_stop_sequences(value)
    return StopSequenceStreamState(
        sequences=sequences,
        single_choice=bool(single_choice),
        upstream_single_choice=bool(upstream_single_choice),
        max_sequence_len=max((len(sequence) for sequence in sequences), default=0),
    )


def find_first_stop_sequence(text: str, stop_sequences: List[str]) -> Optional[tuple[int, str]]:
    first_match: Optional[tuple[int, str]] = None
    for sequence in stop_sequences:
        position = text.find(sequence)
        if position < 0:
            continue
        if first_match is None or position < first_match[0]:
            first_match = (position, sequence)
    return first_match


def split_stream_text_for_stop_sequences(
    text_buffer: str,
    stop_sequences: List[str],
    max_sequence_len: int = 0,
) -> tuple[str, str, Optional[str]]:
    if not stop_sequences:
        return text_buffer, "", None

    match = find_first_stop_sequence(text_buffer, stop_sequences)
    if match is not None:
        position, sequence = match
        return text_buffer[:position], "", sequence

    max_len = max_sequence_len or max(len(sequence) for sequence in stop_sequences)
    keep_len = min(max_len - 1, len(text_buffer))
    if keep_len <= 0:
        return text_buffer, "", None
    emit_len = len(text_buffer) - keep_len
    return text_buffer[:emit_len], text_buffer[emit_len:], None


def apply_stop_sequences_to_text(content: str, stop_sequences: Any) -> str:
    sequences = normalize_openai_stop_sequences(stop_sequences)
    if not sequences:
        return content
    match = find_first_stop_sequence(content or "", sequences)
    if match is None:
        return content
    position, _sequence = match
    return (content or "")[:position]


def sse_chunk_has_done(chunk: Any) -> bool:
    if not isinstance(chunk, str):
        return False
    if "[DONE]" not in chunk:
        return False
    frames = chunk.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
    for frame in frames:
        if sse_frame_data_text(frame).strip() == "[DONE]":
            return True
    return False


def iter_openai_sse_payloads(chunk: Any) -> List[Dict[str, Any]]:
    return iter_sse_payloads(chunk)


def extract_openai_sse_error_message(chunk: Any) -> str:
    return extract_sse_error_message(chunk)


def _pack_sse_payload(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _choice_tail_key(choice: Dict[str, Any], fallback_index: int) -> str:
    index = choice.get("index")
    if index is None:
        index = fallback_index
    return str(index)


def _get_choice_tail(state: StopSequenceStreamState, key: str) -> str:
    return state.choice_tails.get(key, "")


def _set_choice_tail(state: StopSequenceStreamState, key: str, value: str) -> None:
    if value:
        state.choice_tails[key] = value
    else:
        state.choice_tails.pop(key, None)
    if key == "0":
        state.tail = value


def _get_choice_pending_parts(state: StopSequenceStreamState, key: str) -> List[Any]:
    pending_parts = state.choice_pending_parts.get(key)
    if pending_parts is None and key == "0":
        pending_parts = state.pending_parts
    return list(pending_parts or [])


def _set_choice_pending_parts(
    state: StopSequenceStreamState,
    key: str,
    value: List[Any],
) -> None:
    if value:
        state.choice_pending_parts[key] = list(value)
    else:
        state.choice_pending_parts.pop(key, None)
    if key == "0":
        state.pending_parts = list(value or [])


def _choice_index_value(key: Any) -> Any:
    if isinstance(key, str) and key.isdigit():
        try:
            return int(key)
        except ValueError:
            return key
    return key


def _mark_seen_choices(payload: Dict[str, Any], state: StopSequenceStreamState) -> None:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return
    for index, choice in enumerate(choices):
        if isinstance(choice, dict):
            state.seen_choice_keys.add(_choice_tail_key(choice, index))


def _all_seen_choices_stopped(state: StopSequenceStreamState) -> bool:
    if state.single_choice or state.upstream_single_choice:
        return bool(state.seen_choice_keys) and state.seen_choice_keys.issubset(state.stopped_choice_keys)
    return len(state.seen_choice_keys) > 1 and state.seen_choice_keys.issubset(state.stopped_choice_keys)


def _finish_when_all_seen_choices_stopped(
    chunks: List[str],
    state: StopSequenceStreamState,
) -> List[str]:
    if not state.stopped and _all_seen_choices_stopped(state):
        state.stopped = True
        chunks.append("data: [DONE]\n\n")
    return chunks


def _replace_first_delta_content(payload: Dict[str, Any], content: str) -> Dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return payload

    first = choices[0] if isinstance(choices[0], dict) else {}
    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}

    next_payload = dict(payload)
    next_choices = list(choices)
    next_first = dict(first)
    next_delta = dict(delta)
    next_delta["content"] = content
    next_first["delta"] = next_delta
    next_choices[0] = next_first
    next_payload["choices"] = next_choices
    return next_payload


def _replace_first_delta_content_value(payload: Dict[str, Any], content: Any) -> Dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return payload

    first = choices[0] if isinstance(choices[0], dict) else {}
    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}

    next_payload = dict(payload)
    next_choices = list(choices)
    next_first = dict(first)
    next_delta = dict(delta)
    next_delta["content"] = content
    next_first["delta"] = next_delta
    next_choices[0] = next_first
    next_payload["choices"] = next_choices
    return next_payload


def _replace_first_finish_reason(payload: Dict[str, Any], finish_reason: Any) -> Dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return payload

    first = choices[0] if isinstance(choices[0], dict) else {}
    next_payload = dict(payload)
    next_choices = list(choices)
    next_first = dict(first)
    next_first["finish_reason"] = finish_reason
    next_choices[0] = next_first
    next_payload["choices"] = next_choices
    return next_payload


def _empty_choice_for_stopped_stream(choice: Dict[str, Any], fallback_index: int) -> Dict[str, Any]:
    index = choice.get("index")
    if index is None:
        index = fallback_index
    return {
        "index": _choice_index_value(index),
        "delta": {},
    }


def _choice_has_stream_output(choice: Any) -> bool:
    if not isinstance(choice, dict):
        return False
    if choice.get("finish_reason") is not None:
        return True
    if choice.get("media") is not None:
        return True
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    for key, value in delta.items():
        if key != "content":
            return True
        if value not in ("", [], None):
            return True
    return False


def _sanitize_non_primary_choices_for_stop(
    payload: Dict[str, Any],
    state: StopSequenceStreamState,
) -> tuple[Dict[str, Any], set[str], bool]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) <= 1:
        return payload, set(), False

    next_choices = list(choices)
    matched_choice_keys: set[str] = set()
    changed = False

    for index in range(1, len(next_choices)):
        choice = next_choices[index]
        if not isinstance(choice, dict):
            continue

        choice_key = _choice_tail_key(choice, index)
        if choice_key in state.stopped_choice_keys:
            next_choices[index] = _empty_choice_for_stopped_stream(choice, index)
            changed = True
            continue

        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        content = delta.get("content")
        finish_reason = choice.get("finish_reason")
        next_content = content
        matched: Optional[str] = None

        if isinstance(content, str) and content:
            text_buffer = _get_choice_tail(state, choice_key) + content
            if finish_reason:
                match = find_first_stop_sequence(text_buffer, state.sequences)
                if match is None:
                    next_content = text_buffer
                    _set_choice_tail(state, choice_key, "")
                else:
                    position, matched = match
                    next_content = text_buffer[:position]
                    _set_choice_tail(state, choice_key, "")
            else:
                emit_text, next_tail, matched = split_stream_text_for_stop_sequences(
                    text_buffer,
                    state.sequences,
                    state.max_sequence_len,
                )
                next_content = emit_text
                _set_choice_tail(state, choice_key, "" if matched is not None else next_tail)
            if next_content != content or matched is not None:
                changed = True
        elif isinstance(content, list):
            local_state = StopSequenceStreamState(
                sequences=state.sequences,
                tail=_get_choice_tail(state, choice_key),
                pending_parts=_get_choice_pending_parts(state, choice_key),
            )
            next_content, matched = _filter_content_parts_for_stop(
                content,
                local_state,
                flush_tail=bool(finish_reason),
            )
            _set_choice_tail(
                state,
                choice_key,
                "" if matched is not None else local_state.tail,
            )
            _set_choice_pending_parts(
                state,
                choice_key,
                [] if matched is not None else local_state.pending_parts,
            )
            if next_content != content or matched is not None:
                changed = True
        elif finish_reason and _get_choice_tail(state, choice_key):
            next_content = _get_choice_tail(state, choice_key)
            _set_choice_tail(state, choice_key, "")
            changed = True

        if matched is None and next_content is content:
            continue

        if (
            matched is None
            and (next_content == "" or next_content == [])
            and not any(key != "content" for key in delta)
        ):
            next_choices[index] = _empty_choice_for_stopped_stream(choice, index)
            changed = True
            continue

        next_delta = dict(delta)
        if next_content is not None:
            next_delta["content"] = next_content
        next_choice = dict(choice)
        next_choice["delta"] = next_delta
        if matched is not None:
            next_choice["finish_reason"] = "stop"
            state.stopped_choice_keys.add(choice_key)
            _set_choice_tail(state, choice_key, "")
            _set_choice_pending_parts(state, choice_key, [])
        next_choices[index] = next_choice
        if matched is not None:
            matched_choice_keys.add(choice_key)

    if not changed:
        return payload, set(), any(
            _choice_has_stream_output(choice)
            for choice in next_choices[1:]
        )

    next_payload = dict(payload)
    next_payload["choices"] = next_choices
    has_output = any(
        _choice_has_stream_output(choice)
        for choice in next_choices[1:]
    )
    return next_payload, matched_choice_keys, has_output


def _payload_has_non_content_delta(payload: Dict[str, Any]) -> bool:
    if payload.get("media") is not None:
        return True
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first = choices[0] if isinstance(choices[0], dict) else {}
    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
    return any(key != "content" for key in delta)


def _pack_choice_tail_chunk(content: str, model: str, choice_index: Any = 0) -> str:
    payload = json.loads(SSEFormatter.pack_chunk(content, model=model).removeprefix("data: ").strip())
    payload["choices"][0]["index"] = _choice_index_value(choice_index)
    return _pack_sse_payload(payload)


def _pack_choice_content_parts_chunk(
    content: List[Any],
    model: str,
    choice_index: Any = 0,
) -> str:
    payload = json.loads(SSEFormatter.pack_chunk("", model=model).removeprefix("data: ").strip())
    payload["choices"][0]["index"] = _choice_index_value(choice_index)
    payload["choices"][0]["delta"]["content"] = content
    return _pack_sse_payload(payload)


def _pack_finish_chunk(model: str, choice_index: Any = 0) -> str:
    finish_chunk = SSEFormatter.pack_finish(model=model)
    parts = finish_chunk.split("\n\n")
    if not parts:
        return finish_chunk
    data_line = parts[0]
    if not data_line.startswith("data: "):
        return finish_chunk
    try:
        payload = json.loads(data_line[len("data: "):])
    except json.JSONDecodeError:
        return finish_chunk
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choices[0]["index"] = _choice_index_value(choice_index)
    return f"{_pack_sse_payload(payload)}data: [DONE]\n\n"


def _pack_choice_finish_chunk(model: str, choice_index: Any = 0) -> str:
    finish_chunk = _pack_finish_chunk(model=model, choice_index=choice_index)
    frames = finish_chunk.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
    kept_frames = [
        f"{frame}\n\n"
        for frame in frames
        if frame and sse_frame_data_text(frame).strip() != "[DONE]"
    ]
    return "".join(kept_frames)


def _pack_stop_finish_chunk(
    state: StopSequenceStreamState,
    model: str,
    choice_index: Any = 0,
) -> str:
    finish_chunk = _pack_choice_finish_chunk(model=model, choice_index=choice_index)
    if (state.single_choice or state.upstream_single_choice) and str(choice_index) == "0":
        return f"{finish_chunk}data: [DONE]\n\n"
    return finish_chunk


def _flush_tail_chunk(
    state: StopSequenceStreamState,
    model: str,
    choice_key: Optional[str] = None,
) -> List[str]:
    if choice_key is None:
        choice_keys = set(state.choice_tails.keys()) | set(state.choice_pending_parts.keys())
        if choice_keys:
            chunks: List[str] = []
            for key in sorted(
                choice_keys,
                key=lambda value: (
                    0,
                    int(value),
                ) if str(value).isdigit() else (1, str(value)),
            ):
                pending_parts = _get_choice_pending_parts(state, key)
                if pending_parts:
                    chunks.append(_pack_choice_content_parts_chunk(pending_parts, model=model, choice_index=key))
                    _set_choice_pending_parts(state, key, [])
                    _set_choice_tail(state, key, "")
                    continue
                tail = _get_choice_tail(state, key)
                if not tail:
                    continue
                chunks.append(_pack_choice_tail_chunk(tail, model=model, choice_index=key))
            state.choice_tails.clear()
            state.choice_pending_parts.clear()
            state.tail = ""
            state.pending_parts = []
            return chunks
        choice_key = "0"

    pending_parts = _get_choice_pending_parts(state, choice_key)
    if pending_parts:
        _set_choice_pending_parts(state, choice_key, [])
        _set_choice_tail(state, choice_key, "")
        return [_pack_choice_content_parts_chunk(pending_parts, model=model, choice_index=choice_key)]

    tail = _get_choice_tail(state, choice_key) or (state.tail if choice_key == "0" else "")
    if not tail:
        return []
    _set_choice_tail(state, choice_key, "")
    return [_pack_choice_tail_chunk(tail, model=model, choice_index=choice_key)]


def _filter_openai_stop_payload(
    payload: Dict[str, Any],
    state: StopSequenceStreamState,
    model: str,
) -> List[str]:
    if state.stopped:
        return []

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return [_pack_sse_payload(payload)]

    _mark_seen_choices(payload, state)
    payload, secondary_matched_keys, has_secondary_output = _sanitize_non_primary_choices_for_stop(payload, state)
    choices = payload.get("choices")

    def append_secondary_finish(chunks: List[str]) -> List[str]:
        return _finish_when_all_seen_choices_stopped(chunks, state)

    first = choices[0] if isinstance(choices[0], dict) else {}
    first_key = _choice_tail_key(first, 0)
    if first_key in state.stopped_choice_keys:
        next_choices: List[Any] = []
        if isinstance(first, dict):
            next_choices.append(_empty_choice_for_stopped_stream(first, 0))
        for index, choice in enumerate(choices[1:], start=1):
            if not isinstance(choice, dict):
                continue
            choice_key = _choice_tail_key(choice, index)
            if (
                choice_key in state.stopped_choice_keys
                and choice_key not in secondary_matched_keys
            ):
                next_choices.append(_empty_choice_for_stopped_stream(choice, index))
            else:
                next_choices.append(choice)

        if not any(_choice_has_stream_output(choice) for choice in next_choices):
            return append_secondary_finish([])
        next_payload = dict(payload)
        next_payload["choices"] = next_choices
        return append_secondary_finish([_pack_sse_payload(next_payload)])

    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
    content = delta.get("content")
    finish_reason = first.get("finish_reason")

    def maybe_stop_single_choice_stream(choice_key: str) -> None:
        if (state.single_choice or state.upstream_single_choice) and choice_key == "0":
            state.stopped = True

    if isinstance(content, str) and content:
        text_buffer = _get_choice_tail(state, first_key) + content
        if finish_reason:
            match = find_first_stop_sequence(text_buffer, state.sequences)
            if match is None:
                _set_choice_tail(state, first_key, "")
                return append_secondary_finish([
                    _pack_sse_payload(_replace_first_delta_content(payload, text_buffer))
                ])
            position, _sequence = match
            state.stopped_choice_keys.add(first_key)
            _set_choice_tail(state, first_key, "")
            _set_choice_pending_parts(state, first_key, [])
            maybe_stop_single_choice_stream(first_key)
            chunks = []
            if position > 0 or _payload_has_non_content_delta(payload):
                content_payload = _replace_first_delta_content(payload, text_buffer[:position])
                chunks.append(_pack_sse_payload(_replace_first_finish_reason(content_payload, None)))
            chunks.append(_pack_stop_finish_chunk(state, model=model, choice_index=first_key))
            return chunks

        emit_text, next_tail, matched = split_stream_text_for_stop_sequences(
            text_buffer,
            state.sequences,
            state.max_sequence_len,
        )
        _set_choice_tail(state, first_key, "" if matched is not None else next_tail)
        chunks: List[str] = []
        if (
            emit_text
            or _payload_has_non_content_delta(payload)
            or bool(secondary_matched_keys)
            or has_secondary_output
        ):
            chunks.append(_pack_sse_payload(_replace_first_delta_content(payload, emit_text)))
        if matched is not None:
            state.stopped_choice_keys.add(first_key)
            _set_choice_tail(state, first_key, "")
            _set_choice_pending_parts(state, first_key, [])
            maybe_stop_single_choice_stream(first_key)
            chunks.append(_pack_stop_finish_chunk(state, model=model, choice_index=first_key))
        return append_secondary_finish(chunks)

    if isinstance(content, list):
        local_state = StopSequenceStreamState(
            sequences=state.sequences,
            max_sequence_len=state.max_sequence_len,
            tail=_get_choice_tail(state, first_key),
            pending_parts=_get_choice_pending_parts(state, first_key),
        )
        filtered_parts, matched = _filter_content_parts_for_stop(
            content,
            local_state,
            flush_tail=bool(finish_reason),
        )
        _set_choice_tail(
            state,
            first_key,
            "" if matched is not None else local_state.tail,
        )
        _set_choice_pending_parts(
            state,
            first_key,
            [] if matched is not None else local_state.pending_parts,
        )
        chunks: List[str] = []
        if (
            filtered_parts
            or _payload_has_non_content_delta(payload)
            or finish_reason
            or bool(secondary_matched_keys)
            or has_secondary_output
        ):
            content_payload = _replace_first_delta_content_value(payload, filtered_parts)
            chunks.append(_pack_sse_payload(_replace_first_finish_reason(content_payload, None if matched else finish_reason)))
        if matched is not None:
            state.stopped_choice_keys.add(first_key)
            _set_choice_tail(state, first_key, "")
            _set_choice_pending_parts(state, first_key, [])
            maybe_stop_single_choice_stream(first_key)
            chunks.append(_pack_stop_finish_chunk(state, model=model, choice_index=first_key))
        return append_secondary_finish(chunks)

    chunks = []
    if finish_reason:
        chunks.extend(_flush_tail_chunk(state, model, choice_key=first_key))
    chunks.append(_pack_sse_payload(payload))
    return append_secondary_finish(chunks)


_CONTENT_TEXT_PART_TYPES = {"text", "input_text", "output_text"}


def _is_text_content_part(part: Any) -> bool:
    if not isinstance(part, dict):
        return False
    part_type = str(part.get("type") or "").strip().lower()
    return part_type in _CONTENT_TEXT_PART_TYPES


def _content_part_text(part: Any) -> str:
    if not _is_text_content_part(part):
        return ""
    return str(part.get("text") or "")


def _content_parts_text(parts: List[Any]) -> str:
    return "".join(_content_part_text(part) for part in parts)


def _split_content_parts_by_text_length(
    parts: List[Any],
    emit_text_len: int,
) -> tuple[List[Any], List[Any]]:
    emitted: List[Any] = []
    pending: List[Any] = []
    emitted_text_len = 0
    boundary_reached = False

    for part in parts:
        if not _is_text_content_part(part):
            if boundary_reached:
                pending.append(part)
            else:
                emitted.append(part)
            continue

        text = _content_part_text(part)
        if boundary_reached or emitted_text_len >= emit_text_len:
            boundary_reached = True
            pending.append(part)
            continue

        remaining = emit_text_len - emitted_text_len
        if len(text) <= remaining:
            emitted.append(part)
            emitted_text_len += len(text)
            if emitted_text_len >= emit_text_len:
                boundary_reached = True
            continue

        emitted_part = dict(part)
        emitted_part["text"] = text[:remaining]
        emitted.append(emitted_part)

        pending_part = dict(part)
        pending_part["text"] = text[remaining:]
        pending.append(pending_part)
        emitted_text_len = emit_text_len
        boundary_reached = True

    return emitted, pending


def _filter_content_parts_for_stop(
    content: List[Any],
    state: StopSequenceStreamState,
    *,
    flush_tail: bool = False,
) -> tuple[List[Any], Optional[str]]:
    pending_prefix = list(state.pending_parts or [])
    if not pending_prefix and state.tail:
        pending_prefix = [{"type": "text", "text": state.tail}]
    candidate_parts = pending_prefix + list(content)
    text_buffer = _content_parts_text(candidate_parts)

    if flush_tail:
        match = find_first_stop_sequence(text_buffer, state.sequences)
        if match is not None:
            position, matched = match
            filtered_parts, _pending_parts = _split_content_parts_by_text_length(
                candidate_parts,
                position,
            )
            state.tail = ""
            state.pending_parts = []
            return filtered_parts, matched

        state.tail = ""
        state.pending_parts = []
        return candidate_parts, None

    emit_text, next_tail, matched = split_stream_text_for_stop_sequences(
        text_buffer,
        state.sequences,
        state.max_sequence_len,
    )
    if matched is not None:
        filtered_parts, _pending_parts = _split_content_parts_by_text_length(
            candidate_parts,
            len(emit_text),
        )
        state.tail = ""
        state.pending_parts = []
        return filtered_parts, matched

    filtered_parts, pending_parts = _split_content_parts_by_text_length(
        candidate_parts,
        len(emit_text),
    )
    state.tail = next_tail
    state.pending_parts = pending_parts
    return filtered_parts, None


def filter_openai_stop_sse_chunk(
    chunk: Any,
    state: StopSequenceStreamState,
    model: str,
) -> List[str]:
    if state.stopped:
        return []
    if not isinstance(chunk, str):
        return [chunk]
    if not state.sequences:
        return _normalize_done_sse_frames(chunk, state, model)

    combined = f"{state.sse_frame_buffer}{chunk}".replace("\r\n", "\n").replace("\r", "\n")
    if "\n\n" not in combined:
        state.sse_frame_buffer = combined[-MAX_STOP_SSE_FRAME_BUFFER_CHARS:]
        return []

    frames = combined.split("\n\n")
    tail = frames[-1]
    state.sse_frame_buffer = tail[-MAX_STOP_SSE_FRAME_BUFFER_CHARS:] if tail else ""
    output: List[str] = []
    for frame in frames[:-1]:
        frame = frame.strip("\n")
        if not frame:
            continue
        if state.stopped:
            break

        data_text = sse_frame_data_text(frame)
        if frame.startswith(":") and not data_text:
            output.append(f"{frame}\n\n")
            continue

        if not data_text:
            output.append(f"{frame}\n\n")
            continue

        if data_text.strip() == "[DONE]":
            output.extend(_flush_tail_chunk(state, model))
            output.append("data: [DONE]\n\n")
            continue

        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError:
            output.append(f"{frame}\n\n")
            continue

        if isinstance(payload, dict):
            output.extend(_filter_openai_stop_payload(payload, state, model))
        else:
            output.append(f"{frame}\n\n")

    return output


def flush_openai_stop_state(
    state: StopSequenceStreamState,
    model: str,
) -> List[str]:
    """Emit any text held back for cross-chunk stop matching."""
    if state.stopped:
        return []
    output: List[str] = []
    if state.sse_frame_buffer:
        buffered = state.sse_frame_buffer
        state.sse_frame_buffer = ""
        output.extend(filter_openai_stop_sse_chunk(f"{buffered}\n\n", state, model))
        if state.stopped:
            return output
    output.extend(_flush_tail_chunk(state, model))
    return output


def _normalize_done_sse_frames(
    chunk: str,
    state: StopSequenceStreamState,
    model: str,
) -> List[str]:
    combined = f"{state.sse_frame_buffer}{chunk}".replace("\r\n", "\n").replace("\r", "\n")
    if "\n\n" not in combined:
        state.sse_frame_buffer = combined[-MAX_STOP_SSE_FRAME_BUFFER_CHARS:]
        return []

    frames = combined.split("\n\n")
    tail = frames[-1]
    state.sse_frame_buffer = tail[-MAX_STOP_SSE_FRAME_BUFFER_CHARS:] if tail else ""
    output: List[str] = []
    for frame in frames[:-1]:
        frame = frame.strip("\n")
        if not frame:
            continue
        if sse_frame_data_text(frame).strip() == "[DONE]":
            output.extend(_flush_tail_chunk(state, model))
            output.append("data: [DONE]\n\n")
            continue
        output.append(f"{frame}\n\n")
    return output
