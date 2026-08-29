"""
app/core/config_parts/log_redaction.py - 日志敏感信息过滤与脱敏模块
"""
import re
from typing import Any, Match, Optional

_SENSITIVE_KEY_HINTS = (
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "client_secret",
    "refresh_token",
    "session",
    "csrf",
)
_REDACTED_TEXT = "******"


def _redact_data_uri_for_log(match: Match) -> str:
    media_type = str(match.group(1) or "media").lower()
    mime_suffix = str(match.group(2) or "octet-stream").lower()
    payload_len = len(str(match.group(3) or ""))
    return f"data:{media_type}/{mime_suffix};base64,[omitted {payload_len} chars]"


def _redact_long_base64_for_log(match: Match) -> str:
    return f"[base64 omitted: {len(match.group(0))} chars]"


_BASE64_LOG_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-")
_SENSITIVE_TEXT_SCAN_HINT_RE = re.compile(
    r"(?i)data:(?:image|audio|video)/|bearer\s+|authorization|set-cookie|cookie|"
    r"x[-_]api[-_]key|x[-_]github[-_]token|github[-_]token|"
    r"access_token|refresh_token|id_token|api_key|apikey|access_key|client_secret|"
    r"secret|password|passwd|token|session|csrf|sk-[A-Za-z0-9_-]{8,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"https?://[^/\s:@]+:[^/\s@]+@"
)
_SENSITIVE_TEXT_PRECHECK_MIN_CHARS = 4096
_SENSITIVE_TEXT_LARGE_OMIT_THRESHOLD = 512 * 1024
_SENSITIVE_TEXT_LARGE_EDGE_CHARS = 4096


def _has_long_base64_candidate(text: str, min_chars: int = 1024) -> bool:
    run_len = 0
    for char in text:
        if char in _BASE64_LOG_CHARS:
            run_len += 1
            if run_len >= min_chars:
                return True
        elif char in "\r\n" and run_len:
            continue
        else:
            run_len = 0
    return False


def _redact_long_base64_runs_for_log(text: str, min_chars: int = 1024) -> str:
    raw_text = str(text or "")
    if not raw_text:
        return raw_text

    parts = []
    copy_from = 0
    run_start = None
    run_payload_chars = 0

    for index, char in enumerate(raw_text):
        if char in _BASE64_LOG_CHARS:
            if run_start is None:
                run_start = index
                run_payload_chars = 0
            run_payload_chars += 1
            continue

        if char in "\r\n" and run_start is not None:
            continue

        if run_start is not None:
            if run_payload_chars >= min_chars:
                parts.append(raw_text[copy_from:run_start])
                parts.append(f"[base64 omitted: {index - run_start} chars]")
                copy_from = index
            run_start = None
            run_payload_chars = 0

    if run_start is not None and run_payload_chars >= min_chars:
        parts.append(raw_text[copy_from:run_start])
        parts.append(f"[base64 omitted: {len(raw_text) - run_start} chars]")
        copy_from = len(raw_text)

    if not parts:
        return raw_text
    parts.append(raw_text[copy_from:])
    return "".join(parts)


def _should_scan_sensitive_text(text: str) -> bool:
    if len(text) <= _SENSITIVE_TEXT_PRECHECK_MIN_CHARS:
        return True
    if _SENSITIVE_TEXT_SCAN_HINT_RE.search(text):
        return True
    return _has_long_base64_candidate(text)


_SENSITIVE_TEXT_PATTERNS = (
    (
        re.compile(
            r"(?i)data:(image|audio|video)/([a-zA-Z0-9.+-]{1,100});base64,"
            r"([A-Za-z0-9+/=_\-\r\n]{64,})"
        ),
        _redact_data_uri_for_log,
    ),
    (
        re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{1024,}(?![A-Za-z0-9+/=_-])"),
        _redact_long_base64_for_log,
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9+/=_-])"
            r"(?:[A-Za-z0-9+/=_-]{64,}[\r\n]+){7,}[A-Za-z0-9+/=_-]{64,}"
            r"(?![A-Za-z0-9+/=_-])"
        ),
        _redact_long_base64_for_log,
    ),
    (
        re.compile(r"(?i)\b(Bearer)\s+([A-Za-z0-9._~+/=-]{8,})"),
        r"\1 ******",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_-])"
            r"(sk-(?:proj-)?[A-Za-z0-9_-]{16,})"
            r"(?![A-Za-z0-9_-])"
        ),
        "sk-******",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_-])"
            r"(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,})"
            r"(?![A-Za-z0-9_-])"
        ),
        "[jwt omitted]",
    ),
    (
        re.compile(r"(?i)(https?://[^/\s:@]+):([^/\s@]+)(@)"),
        r"\1:******\3",
    ),
    (
        re.compile(
            r"(?i)\b(Authorization|Cookie|Set-Cookie|X[-_]Api[-_]Key|"
            r"X[-_]GitHub[-_]Token|GitHub[-_]Token)\s*:\s*[^\r\n;]+(?:;[^\r\n]*)?"
        ),
        lambda match: f"{match.group(1)}: ******",
    ),
    (
        re.compile(
            r"(?i)([?&](?:access_token|refresh_token|id_token|token|api_key|apikey|"
            r"access_key|client_secret|session|csrf|"
            r"x[-_]api[-_]key|x[-_]github[-_]token|github[-_]token|key|secret|password|passwd)=)"
            r"[^&\s]+"
        ),
        r"\1******",
    ),
    (
        re.compile(
            r"(?i)(\b(?:access_token|refresh_token|id_token|token|api_key|apikey|"
            r"access_key|client_secret|session|csrf|"
            r"x[-_]api[-_]key|x[-_]github[-_]token|github[-_]token|secret|password|passwd)"
            r"\s*=\s*)[^\s,&;]+"
        ),
        r"\1******",
    ),
    (
        re.compile(
            r'(?i)("(?:authorization|cookie|set-cookie|password|passwd|secret|token|api_key|apikey|'
            r'access_token|refresh_token|access_key|client_secret|session|csrf|'
            r'x[-_]api[-_]key|x[-_]github[-_]token|'
            r'github[-_]token)"\s*:\s*)"[^"]*"'
        ),
        r'\1"******"',
    ),
    (
        re.compile(
            r'(?i)("(?:authorization|cookie|set-cookie|password|passwd|secret|token|api_key|apikey|'
            r'access_token|refresh_token|access_key|client_secret|session|csrf|'
            r'x[-_]api[-_]key|x[-_]github[-_]token|'
            r'github[-_]token)"\s*:\s*")[^"\r\n]*$'
        ),
        r'\1******',
    ),
    (
        re.compile(
            r"(?i)('(?:authorization|cookie|set-cookie|password|passwd|secret|token|api_key|apikey|"
            r"access_token|refresh_token|access_key|client_secret|session|csrf|"
            r"x[-_]api[-_]key|x[-_]github[-_]token|"
            r"github[-_]token)'\s*:\s*)'[^']*'"
        ),
        r"\1'******'",
    ),
    (
        re.compile(
            r"(?i)('(?:authorization|cookie|set-cookie|password|passwd|secret|token|api_key|apikey|"
            r"access_token|refresh_token|access_key|client_secret|session|csrf|"
            r"x[-_]api[-_]key|x[-_]github[-_]token|"
            r"github[-_]token)'\s*:\s*)'[^'\r\n]*$"
        ),
        r"\1'******",
    ),
)
_SENSITIVE_BEARER_PATTERN = _SENSITIVE_TEXT_PATTERNS[3]
_SENSITIVE_STANDALONE_TOKEN_PATTERNS = _SENSITIVE_TEXT_PATTERNS[4:7]
_SENSITIVE_HEADER_PATTERN = _SENSITIVE_TEXT_PATTERNS[7]
_SENSITIVE_PARAM_PATTERNS = _SENSITIVE_TEXT_PATTERNS[8:]


def _sanitize_standalone_sensitive_tokens(text: str) -> str:
    if "sk-" not in text and "eyJ" not in text and "://" not in text:
        return text
    sanitized = text
    for pattern, replacement in _SENSITIVE_STANDALONE_TOKEN_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    return any(hint.replace("-", "_") in normalized for hint in _SENSITIVE_KEY_HINTS)


def _sanitize_sensitive_text(text: str) -> str:
    sanitized = str(text or "")
    if not sanitized:
        return sanitized

    if len(sanitized) > _SENSITIVE_TEXT_LARGE_OMIT_THRESHOLD:
        edge_chars = max(0, int(_SENSITIVE_TEXT_LARGE_EDGE_CHARS))
        head = sanitized[:edge_chars]
        tail = sanitized[-edge_chars:] if edge_chars else ""
        omitted = max(0, len(sanitized) - len(head) - len(tail))
        safe_head = _sanitize_sensitive_text(head)
        safe_tail = _sanitize_sensitive_text(tail) if tail else ""
        if safe_tail:
            return (
                f"{safe_head}... [omitted {omitted} chars from oversized log payload] "
                f"...{safe_tail}"
            )
        return f"{safe_head}... [omitted {omitted} chars from oversized log payload]"

    sanitized = _sanitize_standalone_sensitive_tokens(sanitized)

    if not _should_scan_sensitive_text(sanitized):
        return sanitized

    lower_text = sanitized.lower()

    # 1. 只有可能有 Base64 候选字符或包含 'data:' 才做前三个正则替换
    if "data:" in lower_text or "base64" in lower_text or len(sanitized) >= 1024:
        if "data:" in lower_text and "base64," in lower_text:
            sanitized = _SENSITIVE_TEXT_PATTERNS[0][0].sub(_SENSITIVE_TEXT_PATTERNS[0][1], sanitized)
            lower_text = sanitized.lower()
        if len(sanitized) >= 1024 and _has_long_base64_candidate(sanitized):
            sanitized = _redact_long_base64_runs_for_log(sanitized)
            lower_text = sanitized.lower()

    # 2. 仅在包含 bearer 时替换 Bearer token
    if "bearer" in lower_text:
        sanitized = _SENSITIVE_BEARER_PATTERN[0].sub(_SENSITIVE_BEARER_PATTERN[1], sanitized)

    # 3. 仅在包含相关 header 名时替换 Auth, Cookie 等头部
    if any(k in lower_text for k in ("authorization", "cookie", "set-cookie", "x-api-key", "x_api_key", "x-github-token", "x_github_token", "github-token", "github_token")):
        sanitized = _SENSITIVE_HEADER_PATTERN[0].sub(_SENSITIVE_HEADER_PATTERN[1], sanitized)

    # 4. 仅在包含敏感关键字时执行 query/json param 相关的敏感信息提取正则
    if any(k in lower_text for k in ("access_token", "refresh_token", "id_token", "token", "api_key", "apikey", "access_key", "client_secret", "key", "secret", "password", "passwd", "session", "csrf", "x-api-key", "x_api_key", "x-github-token", "x_github_token", "github-token", "github_token")):
        for pattern, replacement in _SENSITIVE_PARAM_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)

    return sanitized


def sanitize_sensitive_data(value: Any, *, _depth: int = 0) -> Any:
    """Return a sanitized copy suitable for logs and debug artifacts."""
    if _depth > 8:
        return "[max-depth]"

    if isinstance(value, dict):
        sanitized_dict = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                sanitized_dict[key] = _REDACTED_TEXT
            else:
                sanitized_dict[key] = sanitize_sensitive_data(item, _depth=_depth + 1)
        return sanitized_dict

    if isinstance(value, list):
        return [sanitize_sensitive_data(item, _depth=_depth + 1) for item in value]

    if isinstance(value, tuple):
        return tuple(sanitize_sensitive_data(item, _depth=_depth + 1) for item in value)

    if isinstance(value, str):
        return _sanitize_sensitive_text(value)

    return value
