import json
from typing import Any, Dict, List


def _parse_sse_field(line: str) -> tuple[str, str]:
    if ":" not in line:
        return line.strip(), ""
    name, value = line.split(":", 1)
    if value.startswith(" "):
        value = value[1:]
    return name.strip(), value


def sse_frame_data_text(frame: Any) -> str:
    if not isinstance(frame, str):
        return ""

    data_lines: List[str] = []
    for raw_line in frame.splitlines():
        line = raw_line.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        field_name, field_value = _parse_sse_field(line)
        if field_name == "data":
            data_lines.append(field_value)
    return "\n".join(data_lines)


def iter_sse_payloads(chunk: Any) -> List[Dict[str, Any]]:
    if not isinstance(chunk, str):
        return []

    payloads: List[Dict[str, Any]] = []
    frames = chunk.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
    for frame in frames:
        frame = frame.strip()
        if not frame:
            continue

        data_text = sse_frame_data_text(frame)
        if not data_text or data_text == "[DONE]":
            continue

        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)

    return payloads


def extract_sse_error_message(chunk: Any) -> str:
    for payload in iter_sse_payloads(chunk):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            if message:
                return message
            continue
        if isinstance(error, str) and error.strip():
            return error.strip()
    return ""
