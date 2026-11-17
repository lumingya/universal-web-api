"""Copilot ChatHub frame parser, bound to an isolated WebSocket turn.

SignalR JSON records end in U+001E. Only the bot's answer and cursor
append operations are consumed; progress, user messages and metadata are
never emitted. The caller must isolate the WebSocket and conversation turn.
"""
from __future__ import annotations
import json
from typing import Any, Optional

class CopilotChatHubParser:
    """Parse one turn of observed ChatHub updates conservatively."""
    def __init__(self, request_id: Optional[str] = None):
        self.expected_request_id = request_id
        self.pending = ''
        self.text = ''
        self.started = False
        self.cursor_path = None
        self.done = False
        self.error = None

    @staticmethod
    def _bot_message(msg: Any) -> bool:
        if not isinstance(msg, dict) or msg.get('author') != 'bot':
            return False
        if msg.get('messageType') not in (None, '', 'Chat'):
            return False
        return msg.get('contentOrigin') == 'DeepLeo' and isinstance(msg.get('text'), str)

    def _match(self, arg: dict, *, terminal: bool = False) -> bool:
        # nonce is a per-update sequence value, NOT a stable turn identifier.
        # The socket is already bound to the send; requestId binds the turn.
        request_id = arg.get('requestId')
        if self.expected_request_id and request_id and request_id != self.expected_request_id:
            if self.started and not terminal:
                self.error = 'copilot_concurrent_turn'
            return False
        return True

    def _sync(self, text: str) -> str:
        if not text.startswith(self.text):
            self.error = 'copilot_non_append_update'
            return ''
        delta = text[len(self.text):]
        self.text = text
        return delta

    def feed_frame(self, payload: str) -> dict:
        out = {'content': '', 'done': self.done, 'error': self.error}
        if self.done or self.error:
            return out
        if not isinstance(payload, str):
            return {'content': '', 'done': False, 'error': 'copilot_non_text_frame'}
        self.pending += payload
        records = self.pending.split('\x1e')
        self.pending = records.pop()
        for raw in records:
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except (ValueError, TypeError):
                self.error = 'copilot_invalid_frame'
                break
            if not isinstance(event, dict):
                continue
            typ = event.get('type')
            if typ == 1 and event.get('target') == 'update':
                args = event.get('arguments') or []
                for arg in args if isinstance(args, list) else []:
                    if not isinstance(arg, dict):
                        continue
                    if not self._match(arg):
                        if self.error:
                            break
                        continue
                    if self.expected_request_id is None and arg.get('requestId'):
                        self.expected_request_id = arg['requestId']
                    cursor = arg.get('cursor')
                    if cursor is not None:
                        if (not isinstance(cursor, dict) or cursor.get('p') != -1
                                or not isinstance(cursor.get('j'), str)
                                or not cursor['j'].endswith('.adaptiveCards[0].body[0].text')):
                            self.error = 'copilot_unsupported_cursor'
                            break
                        if self.cursor_path and cursor['j'] != self.cursor_path:
                            self.error = 'copilot_cursor_changed'
                            break
                        self.cursor_path = cursor['j']
                    messages = arg.get('messages') or []
                    for msg in messages if isinstance(messages, list) else []:
                        if not self._bot_message(msg):
                            continue
                        if not self.started:
                            self.expected_request_id = self.expected_request_id or arg.get('requestId')
                            if not self.expected_request_id:
                                self.error = 'copilot_missing_turn_identity'
                                break
                            self.started = True
                        out['content'] += self._sync(msg['text'])
                        if self.error:
                            break
                    if self.error:
                        break
                    write = arg.get('writeAtCursor')
                    if self.started and isinstance(write, str):
                        if not self.cursor_path:
                            self.error = 'copilot_missing_cursor'
                            break
                        self.text += write
                        out['content'] += write
            elif typ == 2:
                item = event.get('item') or {}
                if (not isinstance(item, dict) or not self.expected_request_id
                        or item.get('requestId') != self.expected_request_id
                        or not self._match(item, terminal=True)):
                    continue
                result = item.get('result') or {}
                if not isinstance(result, dict) or result.get('value') != 'Success':
                    self.error = 'copilot_remote_error'
                    break
                candidates = [m for m in (item.get('messages') or []) if self._bot_message(m)]
                if candidates:
                    if not self.started:
                        self.started = True
                    out['content'] += self._sync(candidates[-1]['text'])
                if self.error:
                    break
                if self.started:
                    self.done = True
                    break
        out['done'], out['error'] = self.done, self.error
        return out


from .base import ResponseParser


class CopilotWebSocketParser(ResponseParser):
    """Registry metadata and incremental SignalR parser for Copilot ChatHub."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._parser = CopilotChatHubParser()

    def parse_chunk(self, raw_response):
        result = self._parser.feed_frame(raw_response)
        return {**result, "images": []}

    @classmethod
    def get_id(cls):
        return "copilot_ws"

    @classmethod
    def get_name(cls):
        return "Copilot ChatHub WebSocket"

    @classmethod
    def get_description(cls):
        return "Incremental Copilot Markdown over the ChatHub WebSocket"

    @classmethod
    def get_supported_patterns(cls):
        return ["/m365Copilot/Chathub/"]
