import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.command_engine import CommandEngine


class CommandEnginePageCheckMarkerTests(unittest.TestCase):
    def test_page_check_scripts_include_recaptcha_markers(self):
        expected_snippets = [
            'iframe[src*="google.com/recaptcha"]',
            '[name="g-recaptcha-response"]',
            "\\u4eba\\u673a\\u8eab\\u4efd\\u9a8c\\u8bc1",
            "protected by recaptcha",
        ]

        for snippet in expected_snippets:
            self.assertIn(snippet, CommandEngine._PAGE_CHECK_OBSERVER_JS)
            self.assertIn(snippet, CommandEngine._PAGE_CHECK_SNAPSHOT_JS)

    def test_page_check_snapshot_details_include_matched_keywords_and_preview(self):
        engine = CommandEngine.__new__(CommandEngine)
        snapshot = "Header ... Security Verification ... Protected by reCAPTCHA ... Footer"

        result = engine._evaluate_page_check_snapshot(
            snapshot,
            "and",
            ["security verification", "protected by recaptcha"],
        )

        self.assertTrue(result["hit"])
        self.assertEqual(
            result["matched_keywords"],
            ["security verification", "protected by recaptcha"],
        )
        self.assertIn("security verification", result["snapshot_preview"])
        self.assertIn("protected by recaptcha", result["snapshot_preview"])


class ArenaVerifyCommandRoutingTests(unittest.TestCase):
    def setUp(self):
        commands_path = ROOT / "config" / "commands.json"
        self.commands = json.loads(commands_path.read_text(encoding="utf-8"))["commands"]

    def _get_command(self, command_id: str):
        return next(cmd for cmd in self.commands if cmd.get("id") == command_id)

    def test_cf_command_no_longer_uses_generic_security_verification(self):
        command = self._get_command("cmd_cf_pagecheck_arena_verify")
        trigger_value = str(command["trigger"]["value"])
        self.assertNotIn("security verification", trigger_value.lower())
        self.assertIn("cloudflare", trigger_value.lower())

    def test_human_verify_command_uses_recaptcha_markers(self):
        command = self._get_command("cmd_arena_human_verify_clear_cookie")
        trigger_value = str(command["trigger"]["value"]).lower()
        self.assertIn("security verification", trigger_value)
        self.assertIn("protected by recaptcha", trigger_value)
        self.assertNotEqual(trigger_value.strip(), "recaptcha")
        self.assertEqual(command["trigger"]["priority"], 6)

    def test_cookie_banner_command_uses_cookie_popup_markers(self):
        command = self._get_command("cmd_arena_cookie_accept")
        trigger_value = str(command["trigger"]["value"]).lower()
        self.assertIn("this website uses cookies", trigger_value)
        self.assertIn("accept cookies", trigger_value)
        self.assertEqual(command["trigger"]["priority"], 5)
        self.assertEqual(command["actions"][0]["type"], "run_js")
        self.assertEqual(command["actions"][1]["type"], "wait")
        self.assertEqual(command["actions"][2]["type"], "run_js")


if __name__ == "__main__":
    unittest.main()
