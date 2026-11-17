"""Monitor lifecycle and privacy tests with a fake CDP driver; no site imports."""
import importlib.util, json, pathlib, sys, types, unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

class FakeDriver:
    def __init__(self,*a):self.callbacks={};self.stopped=False
    def set_callback(self,name,cb):self.callbacks[name]=cb
    def run(self,*a):return {}
    def stop(self):self.stopped=True
from app.core import copilot_ws_monitor as M
from app.core.network_monitor import NetworkMonitorError as MonitorError
from app.core.network_monitor import NetworkMonitorTerminalError as Terminal

class Formatter:
 def pack_chunk(self,content,completion_id=None):return content
class Tab:
 tab_id='tab';driver=types.SimpleNamespace(address='ws://test')
def frame(obj):return {'payloadData':json.dumps(obj)+'\x1e'}
def update(**kwargs):return {'type':1,'target':'update','arguments':[kwargs]}
def bot(t):return {'author':'bot','contentOrigin':'DeepLeo','text':t}
def send():return {'type':4,'target':'chat','invocationId':'0','arguments':[{'requestId':'r','message':{'author':'user','requestId':'r','text':'USER_SECRET'},'token':'SECRET_TOKEN'}]}
def complete(text,request='r'):return {'type':2,'invocationId':'0','item':{'requestId':request,'messages':[{'author':'user','text':'USER_SECRET'},bot(text)],'result':{'value':'Success'},'conversationTransferToken':'SECRET_TOKEN'}}
def initial(text='a'):return update(requestId='r',nonce='n',cursor={'j':"$['x'].adaptiveCards[0].body[0].text",'p':-1},messages=[bot(text)])
class MonitorTests(unittest.TestCase):
 def setUp(self):
  self.driver_patch=patch.object(M, "Driver", FakeDriver);self.driver_patch.start();self.addCleanup(self.driver_patch.stop)
 def monitor(self):
  m=M.CopilotWebSocketMonitor(Tab(),Formatter(),{'hard_timeout':10,'network':{'first_response_timeout':1}});m.pre_start();m.mark_send_attempt();return m
 def test_full_lifecycle_and_privacy(self):
  m=self.monitor();driver=m.driver
  m._on_sent(requestId='ws',response=frame(send()))
  m._on_received(requestId='other',response=frame(initial('bad')))
  m._on_received(requestId='ws',response=frame(initial('a')))
  m._on_received(requestId='ws',response=frame(update(nonce='n',writeAtCursor='bc')))
  m._on_received(requestId='ws',response=frame(complete('abc')))
  self.assertTrue(m.poll_send_activity(.01)['matched'])
  self.assertNotIn('SECRET',repr(list(m.events.queue)))
  self.assertEqual('abc',''.join(m.monitor(completion_id='id')))
  self.assertEqual(m.get_total_content_chars(),3);self.assertTrue(driver.stopped)
 def test_split_received_record_is_buffered(self):
  m=self.monitor();m._on_sent(requestId='ws',response=frame(send()))
  payload=frame(initial('split'))['payloadData']
  m._on_received(requestId='ws',response={'payloadData':payload[:15]})
  self.assertEqual(m.events.qsize(),0)
  m._on_received(requestId='ws',response={'payloadData':payload[15:]})
  m._on_received(requestId='ws',response=frame(complete('split')))
  self.assertEqual('split',''.join(m.monitor()))
  self.assertEqual(m._partial_received,'')
 def test_error_after_output_is_terminal(self):
  m=self.monitor();m._on_sent(requestId='ws',response=frame(send()));m._on_received(requestId='ws',response=frame(initial()))
  m._on_received(requestId='ws',response=frame(update(nonce='n',requestId='r',messages=[bot('different')])))
  g=m.monitor();self.assertEqual(next(g),'a')
  with self.assertRaises(Terminal):next(g)
 def test_unstarted_error_allows_fallback(self):
  m=self.monitor();m._fail('broken')
  with self.assertRaises(MonitorError):list(m.monitor())
 def test_competing_sockets_and_retries_fail_closed(self):
  m=self.monitor();m._on_sent(requestId='one',response=frame(send()));m._on_sent(requestId='two',response=frame(send()))
  self.assertEqual(m.failed,'copilot_ambiguous_socket');m.cleanup()
  m=self.monitor();m._on_sent(requestId='one',response=frame(send()));m.mark_send_attempt()
  self.assertEqual(m.failed,'copilot_multiple_send_attempts');m.cleanup()
 def test_other_invocation_and_unrelated_handshake_ignored(self):
  m=self.monitor()
  m._on_sent(requestId='ws',response=frame({'type':1,'target':'send','arguments':[{'token':'SECRET'}]}))
  self.assertIsNone(m.socket)
  m._on_sent(requestId='ws',response=frame(send()))
  m._on_received(requestId='ws',response=frame(initial()))
  m._on_received(requestId='ws',response=frame({'type':2,'invocationId':'other','item':{'requestId':'r','messages':[bot('a')],'result':{'value':'Success'}}}))
  m._on_received(requestId='ws',response=frame(complete('abc')))
  self.assertEqual('abc',''.join(m.monitor()))
 def test_wrong_completion_not_accepted(self):
  m=self.monitor();m._on_sent(requestId='one',response=frame(send()));m._on_received(requestId='one',response=frame(initial()))
  m._on_received(requestId='one',response=frame(complete('a','wrong')))
  m._on_received(requestId='one',response=frame(complete('a')))
  self.assertEqual('a',''.join(m.monitor()))

class FactoryIntegrationTests(unittest.TestCase):
 def test_factory_selects_websocket_monitor(self):
  from app.core.network_monitor import create_network_monitor
  from app.core.parsers import ParserRegistry
  config={'network': {'parser': 'copilot_ws', 'listen_pattern': '/m365Copilot/Chathub/'}}
  monitor=create_network_monitor(Tab(),Formatter(),config)
  self.assertIsInstance(monitor,M.CopilotWebSocketMonitor)
  self.assertEqual(ParserRegistry.get('copilot_ws').get_id(),'copilot_ws')

if __name__=='__main__':unittest.main()
