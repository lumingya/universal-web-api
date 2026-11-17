"""Offline checks for the Copilot adapter. Set COPILOT_SNAPSHOT to test a saved page."""
import json
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

UWA = Path(__file__).resolve().parents[1]

SITE_FILE = UWA / "config" / "sites" / "copilot.com.json"
FIXTURE = """<html><body>
<a class="fai-CopilotNavItem" aria-label="New chat" href="/chat">New chat</a>
<a class="fai-CopilotNavSubItem" aria-label="New chat" href="/chat/conversation/old">Old chat</a>
<div role="feed"><div><div data-testid="m365-chat-llm-web-ui-chat-message">
  <div data-testid="chatQuestion"><div data-testid="chatOutput">Question</div></div>
  <div data-testid="copilot-message-reply-div"><div data-testid="markdown-reply"><p>First answer</p></div></div>
</div></div>
<div><div data-testid="m365-chat-llm-web-ui-chat-message">
  <div data-testid="chatQuestion"><div data-testid="chatOutput">Another question</div></div>
  <div data-testid="copilot-message-reply-div"><div data-testid="markdown-reply"><p>Second answer</p></div></div>
</div></div></div>
<span id="m365-chat-editor-target-element" role="textbox" contenteditable="true" data-lexical-editor="true"></span>
</body></html>"""

def latest_replies(soup, selector):
    # SoupSieve rejects nested :has(), although Chromium supports it. Assert the
    # browser selector and evaluate its equivalent structure in this fixture.
    expected = ('[role="feed"] > div:has(> [data-testid="m365-chat-llm-web-ui-chat-message"])'
                ':not(:has(~ div > [data-testid="m365-chat-llm-web-ui-chat-message"])) '
                '[data-testid="markdown-reply"]')
    assert selector == expected, selector
    wrappers = soup.select('[role="feed"] > div:has(> [data-testid="m365-chat-llm-web-ui-chat-message"])')
    return wrappers[-1].select('[data-testid="markdown-reply"]') if wrappers else []

class CopilotSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(SITE_FILE.read_text(encoding="utf-8"))
        cls.preset = cls.payload["config"]["presets"][cls.payload["config"]["default_preset"]]

    def test_schema_and_discovery(self):
        self.assertEqual("copilot.com", self.payload["site"])
        # Importing UWA's config package constructs a singleton that can migrate
        # and save site files. Run it in a subprocess with isolated paths.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sites = root / "sites"
            sites.mkdir()
            (sites / SITE_FILE.name).write_bytes(SITE_FILE.read_bytes())
            env = os.environ.copy()
            env.update({
                "PYTHONPATH": str(UWA),
                "SITES_CONFIG_DIR": str(sites),
                "SITES_CONFIG_FILE": str(root / "sites.json"),
                "SITES_LOCAL_FILE": str(root / "sites.local.json"),
                "COMMANDS_CONFIG_FILE": str(root / "commands.json"),
                "COMMANDS_LOCAL_FILE": str(root / "commands.local.json"),
            })
            check = r"""
import json, pathlib
from app.services.config.site_schema import site_file_errors
from app.services.config.site_store import SiteStore
from app.core.workflow.flow_runtime import validate_workflow
sites = pathlib.Path(__import__('os').environ['SITES_CONFIG_DIR'])
payload = json.loads((sites / 'copilot.com.json').read_text(encoding='utf-8'))
assert not site_file_errors(payload), site_file_errors(payload)
store = SiteStore(sites, sites / '__no_legacy__.json')
assert store.load()['copilot.com'] == payload['config']
preset = payload['config']['presets'][payload['config']['default_preset']]
validate_workflow(preset['workflow'])
"""
            result = subprocess.run([sys.executable, "-B", "-c", check], env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("network", self.preset["stream_config"]["mode"])
        self.assertEqual("copilot_ws", self.preset["stream_config"]["network"]["parser"])
        self.assertIn({"action": "CLICK", "target": "send_btn", "optional": False, "value": None}, self.preset["workflow"])

    def test_selectors_on_sanitized_multiturn_fixture(self):
        soup = BeautifulSoup(FIXTURE, "html.parser")
        selectors = self.preset["selectors"]
        self.assertEqual(1, len(soup.select(selectors["input_box"])))
        self.assertEqual(["/chat"], [x.get("href") for x in soup.select(selectors["new_chat_btn"])])
        self.assertEqual(["Second answer"], [x.get_text(strip=True) for x in latest_replies(soup, selectors["result_container"])])
        # Do not expose an old answer when the new user turn has no reply yet.
        feed = soup.select_one('[role="feed"]')
        feed.append(BeautifulSoup('<div><div data-testid="m365-chat-llm-web-ui-chat-message"><div data-testid="chatQuestion">Pending</div></div></div>', "html.parser"))
        self.assertEqual([], latest_replies(soup, selectors["result_container"]))

    @unittest.skipUnless(os.environ.get("COPILOT_SNAPSHOT"), "COPILOT_SNAPSHOT not provided")
    def test_selectors_on_reference_snapshot(self):
        soup = BeautifulSoup(Path(os.environ["COPILOT_SNAPSHOT"]).read_text(encoding="utf-8"), "html.parser")
        selectors = self.preset["selectors"]
        self.assertEqual(1, len(soup.select(selectors["input_box"])))
        new_chat = soup.select(selectors["new_chat_btn"])
        self.assertEqual(1, len(new_chat))
        self.assertNotIn("/conversation/", new_chat[0].get("href", ""))
        replies = latest_replies(soup, selectors["result_container"])
        self.assertGreaterEqual(len(replies), 1)
        self.assertTrue(replies[-1].get_text(strip=True))
        self.assertTrue(all(node.find_parent(attrs={"data-testid": "chatQuestion"}) is None for node in replies))

if __name__ == "__main__":
    unittest.main()
