"""Offline tests based on a sanitized ChatHub exchange, never real credentials."""
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.parsers.copilot_chathub import CopilotChatHubParser

def record(x):
    return json.dumps(x, ensure_ascii=False) + '\x1e'

def update(**arg):
    return record({'type':1, 'target':'update', 'arguments':[arg]})

def message(text, **kwargs):
    return {'author':'bot','contentOrigin':'DeepLeo','text':text, **kwargs}

class ChatHubTests(unittest.TestCase):
    def test_stream_preserves_raw_markdown_and_no_duplicate_final(self):
        p = CopilotChatHubParser('request-1')
        pieces = ['NET', '_MD', '_5381\n\n|', ' A | B |\n|---|---|\n|', ' X | Y |\n\n```ts', ' {preview="false"}\nconst x = 1;\n```']
        result = p.feed_frame(update(nonce='n1',requestId='request-1',messages=[{'author':'bot','messageType':'Progress','text':'Thinking'}]))
        self.assertEqual('',result['content'])
        chunks = [p.feed_frame(update(nonce='n1',requestId='request-1',cursor={'j':"$['synthetic'].adaptiveCards[0].body[0].text",'p':-1},messages=[message(pieces[0])]))['content']]
        for i,text in enumerate(pieces[1:]): chunks.append(p.feed_frame(update(nonce='n'+str(i+2),writeAtCursor=text))['content'])
        full = ''.join(pieces)
        final_update = p.feed_frame(update(nonce='n1',requestId='request-1',messages=[message(full)],isLastUpdate=True))
        self.assertEqual('', final_update['content']); self.assertFalse(final_update['done'])
        completed = p.feed_frame(record({'type':2, 'item':{'requestId':'request-1','messages':[{'author':'user','text':'ignored'},message(full)],'result':{'value':'Success'}}}))
        self.assertEqual('',completed['content']); self.assertTrue(completed['done'])
        self.assertEqual(full, ''.join(chunks))
    def test_wrong_turn_and_progress_are_ignored(self):
        p=CopilotChatHubParser('one')
        self.assertEqual('',p.feed_frame(update(nonce='n', requestId='two', messages=[message('foreign')]))['content'])
        self.assertEqual('',p.feed_frame(update(nonce='n', requestId='one', messages=[{'author':'bot','messageType':'Progress','contentOrigin':'DeepLeo','text':'private'}]))['content'])
        self.assertEqual('abc',p.feed_frame(update(nonce='n', requestId='one', cursor={'j':"$['x'].adaptiveCards[0].body[0].text",'p':-1}, messages=[message('abc')]))['content'])
        self.assertEqual('d',p.feed_frame(update(nonce='different-per-frame', writeAtCursor='d'))['content'])
        self.assertFalse(p.feed_frame(record({'type':2,'item':{'requestId':'two','messages':[message('foreign')]}}))['done'])
        r=p.feed_frame(update(nonce='new',requestId='two', messages=[message('foreign')]))
        self.assertEqual(r['error'],'copilot_concurrent_turn')
    def test_split_records_unicode_and_final_remainder(self):
        p=CopilotChatHubParser('r')
        frame=update(nonce='n',requestId='r',messages=[message('你好')])
        self.assertEqual('',p.feed_frame(frame[:10])['content'])
        self.assertEqual('你好',p.feed_frame(frame[10:])['content'])
        r=p.feed_frame(record({'type':2,'item':{'requestId':'r','messages':[message('你好世界')],'result':{'value':'Success'}}}))
        self.assertEqual('世界',r['content']); self.assertTrue(r['done'])
    def test_non_append_and_errors_never_silently_pass(self):
        p=CopilotChatHubParser('r'); p.feed_frame(update(nonce='n',requestId='r',messages=[message('abc')]))
        r=p.feed_frame(update(nonce='n',requestId='r',messages=[message('xyz')]))
        self.assertEqual('copilot_non_append_update',r['error'])
        p=CopilotChatHubParser('r'); p.feed_frame(update(nonce='n',requestId='r',messages=[message('abc')]))
        r=p.feed_frame(record({'type':2,'item':{'requestId':'r','result':{'value':'Error'}}}))
        self.assertEqual('copilot_remote_error',r['error'])
    def test_observed_completion_without_correlation_does_not_claim_done(self):
        # Real completion items may have only invocationId outside the item.
        # Until send-side invocation correlation exists, fail closed.
        p=CopilotChatHubParser('r')
        p.feed_frame(update(nonce='n',requestId='r',messages=[message('abc')]))
        r=p.feed_frame(record({'type':2,'invocationId':'0',
            'item':{'messages':[message('abc')],'result':{'value':'Success'}}}))
        self.assertFalse(r['done'])

    def test_unsupported_cursor_and_missing_cursor_fail_closed(self):
        p=CopilotChatHubParser('r')
        r=p.feed_frame(update(nonce='n',requestId='r',cursor={'j':"$['x'].adaptiveCards[0].body[0].text",'p':0},messages=[message('a')]))
        self.assertEqual(r['error'],'copilot_unsupported_cursor')
        p=CopilotChatHubParser('r');p.feed_frame(update(nonce='n',requestId='r',messages=[message('a')]))
        self.assertEqual(p.feed_frame(update(nonce='n',writeAtCursor='b'))['error'],'copilot_missing_cursor')

    def test_ping_and_unknown_data_not_emitted(self):
        p=CopilotChatHubParser('r')
        self.assertEqual('',p.feed_frame(record({'type':6})+update(nonce='n',writeAtCursor='orphan'))['content'])
        self.assertFalse(p.done)

if __name__ == '__main__': unittest.main()
