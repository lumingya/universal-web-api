"""Copilot ChatHub WebSocket monitor.

A private CDP connection observes only this tab. Payloads are sanitized in
callbacks: tokens, user messages, URLs, cookies and conversation metadata are
not queued or logged. Once output has begun, failures are terminal rather than
falling back to DOM whose character prefix need not equal raw Markdown.
"""
from __future__ import annotations
import json
import queue
import threading
import time
from urllib.parse import urlsplit

from DrissionPage._base.driver import Driver
from app.core.config import SSEFormatter, logger
from app.core.network_monitor import NetworkMonitorError, NetworkMonitorTimeout, NetworkMonitorTerminalError
from app.core.parsers.copilot_chathub import CopilotChatHubParser

_MAX_EVENTS = 2048
_MAX_FRAME = 4 * 1024 * 1024


def _clean_message(m):
    if not isinstance(m, dict) or m.get('author') != 'bot': return None
    if m.get('contentOrigin') != 'DeepLeo' or not isinstance(m.get('text'), str): return None
    return {k: m[k] for k in ('author', 'contentOrigin', 'messageType', 'text') if k in m}


def _sanitize(event):
    """Keep only fields necessary for parsing; never retain credentials/history."""
    if not isinstance(event, dict): return None
    if event.get('type') == 4 and event.get('target') == 'chat':
        args = event.get('arguments')
        if not isinstance(args,list) or not args or not isinstance(args[0],dict): return None
        arg=args[0]; msg=arg.get('message') or {}
        rid=arg.get('requestId'); invocation=event.get('invocationId')
        if (not isinstance(rid,str) or not rid or not isinstance(invocation,str)
                or not isinstance(msg,dict) or msg.get('author') != 'user'
                or (msg.get('requestId') and msg['requestId'] != rid)):
            return None
        return {'type':4,'target':'chat','requestId':rid,'invocationId':invocation}
    if event.get('type') == 1 and event.get('target') == 'update':
        args=[]
        for arg in event.get('arguments') or []:
            if not isinstance(arg,dict): continue
            clean={k:arg[k] for k in ('requestId','nonce','writeAtCursor','isLastUpdate') if k in arg}
            cursor=arg.get('cursor')
            if isinstance(cursor,dict): clean['cursor']={k:cursor[k] for k in ('j','p') if k in cursor}
            msgs=[x for m in arg.get('messages') or [] if (x:=_clean_message(m)) is not None]
            if msgs: clean['messages']=msgs
            args.append(clean)
        return {'type':1,'target':'update','arguments':args}
    if event.get('type') == 2:
        item=event.get('item') or {}
        if not isinstance(item,dict): return None
        result=item.get('result') or {}
        msgs=[x for m in item.get('messages') or [] if (x:=_clean_message(m)) is not None]
        return {'type':2,'invocationId':event.get('invocationId'),'item':{'requestId':item.get('requestId'), 'messages':msgs,'result':{'value':result.get('value') if isinstance(result,dict) else None}}}
    return None

class CopilotWebSocketMonitor:
    def __init__(self, tab, formatter, stream_config, stop_checker=None, **kwargs):
        self.tab=tab; self.formatter=formatter; self.config=stream_config or {}
        self.stop_checker=stop_checker or (lambda:False)
        self.driver=None; self.events=queue.Queue(maxsize=_MAX_EVENTS)
        self.lock=threading.RLock(); self.signal=threading.Event()
        self.armed=False; self.socket=None; self.invocation_id=None; self.socket_urls={}; self.activity=False
        self.failed=None; self.chars=0; self.parser=CopilotChatHubParser()
        self._partial_received=''
        self.started_at=0
        self.last_content_at=None

    def _fail(self,reason):
        with self.lock:
            if not self.failed:self.failed=reason
            self.signal.set()

    def _created(self, requestId=None, url='', **kw):
        try:
            u=urlsplit(url)
            self.socket_urls[requestId] = (u.hostname=='substrate.office.com' and '/m365Copilot/Chathub/' in u.path)
        except Exception: pass

    def _on_sent(self, requestId=None, response=None, **kw):
        self._frame('sent',requestId,response)

    def _on_received(self, requestId=None, response=None, **kw):
        self._frame('received',requestId,response)

    def _frame(self,direction,socket,response):
        with self.lock:
            if not self.armed or self.failed: return
            data=(response or {}).get('payloadData')
            if not isinstance(data,str):return
            if len(data)>_MAX_FRAME:
                self._fail('copilot_frame_too_large');return
            if direction == 'received':
                if self.socket is None or socket != self.socket:return
                self._partial_received += data
                if len(self._partial_received) > _MAX_FRAME:
                    self._partial_received = ''
                    self._fail('copilot_record_too_large');return
                records = self._partial_received.split('\x1e')
                self._partial_received = records.pop()
            else:
                records = data.split('\x1e')
            for raw in records:
                if not raw.strip():continue
                try: obj=_sanitize(json.loads(raw))
                except (ValueError,TypeError,AttributeError): continue
                if not obj: continue
                if direction=='sent' and obj.get('target')=='chat':
                    if self.socket_urls.get(socket) is False: continue
                    if self.socket is not None:
                        self._fail('copilot_ambiguous_socket' if self.socket != socket else 'copilot_multiple_send_attempts')
                        continue
                    self.socket=socket
                    self.invocation_id=obj['invocationId']
                    self.parser.expected_request_id=obj['requestId']
                    self.activity=True;self.signal.set()
                    continue
                if direction!='received' or self.socket is None or socket!=self.socket: continue
                if obj.get('type')==2 and obj.get('invocationId') != self.invocation_id: continue
                if obj.get('type')==1:
                    for arg in obj.get('arguments',[]):
                        if arg.get('requestId'):
                            self.activity=True;self.signal.set();break
                try:self.events.put_nowait(obj)
                except queue.Full:self._fail('copilot_event_overflow')

    def pre_start(self):
        with self.lock:
            if self.driver is not None:return
            try:
                d=Driver(self.tab.tab_id,self.tab.driver.address)
                d.set_callback('Network.webSocketCreated',self._created)
                d.set_callback('Network.webSocketFrameSent',self._on_sent)
                d.set_callback('Network.webSocketFrameReceived',self._on_received)
                d.run('Network.enable')
                self.driver=d
                logger.info('[CopilotWS] WebSocket listener ready')
            except Exception:
                try:d.stop()
                except Exception:pass
                self.failed='copilot_listener_start_failed'

    def mark_send_attempt(self):
        self.pre_start()
        with self.lock:
            if self.armed and (self.socket is not None or self.activity):
                self._fail('copilot_multiple_send_attempts');return
            self.armed=True;self.started_at=time.monotonic()

    def poll_send_activity(self,timeout=0.25):
        self.signal.wait(max(0.01,float(timeout or .01)))
        return {'seen':self.activity,'matched':self.activity,'source':'copilot_ws_update' if self.activity else 'timeout'}

    def rebuild_after_external_interruption(self, reason='external_interrupt'):
        # An interrupted request cannot safely be rebound to another turn.
        self._fail('copilot_interrupted')

    def get_total_content_chars(self):return self.chars
    def get_media_generation_state(self):return {}
    def get_stream_media_items(self):return []

    def monitor(self,selector=None,user_input='',completion_id=None):
        if completion_id is None:completion_id=SSEFormatter._generate_id()
        net=self.config.get('network') or {}
        try:first=max(1.0,float(net.get('first_response_timeout',30)))
        except (TypeError,ValueError):first=30.0
        try:hard=max(first,float(self.config.get('hard_timeout',300)))
        except (TypeError,ValueError):hard=300.0
        try:first_content=max(first,float(net.get('first_content_timeout',60)))
        except (TypeError,ValueError):first_content=60.0
        try:silence=max(1.0,float(net.get('silence_threshold',60)))
        except (TypeError,ValueError):silence=60.0
        started=self.started_at or time.monotonic()
        try:
            while True:
                if self.stop_checker():return
                if self.failed:
                    self._raise(self.failed)
                elapsed=time.monotonic()-started
                if elapsed>hard:self._raise('copilot_hard_timeout',timeout=True)
                if not self.activity and elapsed>first:self._raise('copilot_first_response_timeout',timeout=True)
                if not self.chars and elapsed>first_content:self._raise('copilot_first_content_timeout',timeout=True)
                if self.last_content_at and time.monotonic()-self.last_content_at>silence:
                    self._raise('copilot_content_silence_timeout',timeout=True)
                try:event=self.events.get(timeout=.2)
                except queue.Empty:continue
                result=self.parser.feed_frame(json.dumps(event,ensure_ascii=False)+'\x1e')
                if result['error']:self._raise(result['error'])
                content=result['content']
                if content:
                    self.chars+=len(content)
                    self.last_content_at=time.monotonic()
                    yield self.formatter.pack_chunk(content,completion_id=completion_id)
                if result['done']:
                    logger.info('[CopilotWS] Network stream complete (%s chars)' % self.chars)
                    return
        finally:self.cleanup()

    def _raise(self,reason,timeout=False):
        if self.chars:raise NetworkMonitorTerminalError(reason)
        if timeout:raise NetworkMonitorTimeout(reason)
        raise NetworkMonitorError(reason)

    def cleanup(self):
        with self.lock:
            self.armed=False;d=self.driver;self.driver=None
        if d is not None:
            try:d.stop()
            except Exception:pass
        while True:
            try:self.events.get_nowait()
            except queue.Empty:break
        self.parser=CopilotChatHubParser();self.socket=None;self.invocation_id=None;self._partial_received='';self.socket_urls.clear()
