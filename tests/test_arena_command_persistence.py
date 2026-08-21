import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ArenaCommandPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads((PROJECT_ROOT / "config" / "commands.json").read_text(encoding="utf-8"))
        cls.commands = {item.get("id"): item for item in payload.get("commands", [])}

    def test_main_command_persists_each_completed_round(self):
        script = self.commands["cmd_arena_auto_battle"]["script"]
        self.assertIn("record_arena_rule_candidates(\n                reply_info,", script)
        self.assertIn("source=f\"page-final-visible-text/", script)

    def test_arena_commands_persist_current_page_before_manual_stop(self):
        script = self.commands["cmd_arena_auto_battle"]["script"]
        self.assertIn("source='manual-stop-final-snapshot'", script)
        self.assertLess(
            script.index("source='manual-stop-final-snapshot'"),
            script.index("returning collected URLs"),
        )

    def test_context_model_lab_is_disabled_by_default(self):
        self.assertFalse(self.commands["cmd_arena_context_model_lab"]["enabled"])

    def test_deepseek_refresh_does_not_observe_arena_pages(self):
        trigger = self.commands["cmd_72ae0f7d"]["trigger"]
        self.assertEqual(trigger["scope"], "domain")
        self.assertEqual(trigger["domain"], "chat.deepseek.com")

    def test_arena_runtime_cpu_guard_version_matches_command_probe(self):
        command = self.commands["cmd_arena_stop_fix_runtime"]
        source = (PROJECT_ROOT / "js" / "arena-stream-hard-stop.user.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("window.__arenaHardStop.version !== '2.12.1'", command["trigger"]["probe_js"])
        self.assertIn("const VERSION = '2.12.1';", source)
        self.assertIn("const REPAIR_INTERVAL_MS = 1000;", source)
        self.assertEqual(command["trigger"]["periodic_interval_sec"], 30)
        self.assertEqual(command["trigger"]["interrupt_policy"], "resume")
        self.assertFalse(command["trigger"].get("abort_on_match", False))

    def test_arena_clear_error_command_clicks_clear_then_aborts_workflow(self):
        command = self.commands["cmd_19f7ae6f"]
        trigger = command["trigger"]

        self.assertTrue(command["enabled"])
        self.assertEqual(trigger["value"], "Clear")
        self.assertEqual(trigger["scope"], "domain")
        self.assertEqual(trigger["domain"], "arena.ai")
        self.assertTrue(trigger["check_while_busy_workflow"])
        self.assertTrue(trigger["allow_during_workflow"])
        self.assertEqual(trigger["interrupt_policy"], "abort")
        self.assertIn("text === 'clear'", trigger["probe_js"])
        self.assertEqual(
            [action["type"] for action in command["actions"]],
            ["run_js", "abort_task"],
        )
        self.assertIn("target.click()", command["actions"][0]["code"])
        self.assertEqual(
            command["actions"][1]["reason"],
            "arena_clear_error_detected",
        )

    def test_arena_clear_error_command_is_enabled_in_local_overrides(self):
        payload = json.loads(
            (PROJECT_ROOT / "config" / "commands.local.json").read_text(encoding="utf-8")
        )
        local_commands = {item.get("id"): item for item in payload.get("commands", [])}

        self.assertTrue(local_commands["cmd_19f7ae6f"]["enabled"])

    def test_arena_prompt_rejection_command_aborts_workflow_with_http_422_message(self):
        command = self.commands["cmd_arena_prompt_rejected_422"]
        trigger = command["trigger"]

        self.assertTrue(command["enabled"])
        self.assertEqual(trigger["type"], "page_check")
        self.assertEqual(trigger["scope"], "domain")
        self.assertEqual(trigger["domain"], "arena.ai")
        self.assertTrue(trigger["check_while_busy_workflow"])
        self.assertTrue(trigger["allow_during_workflow"])
        self.assertEqual(trigger["interrupt_policy"], "abort")
        self.assertIn("this content violates our terms of use", trigger["probe_js"].lower())
        self.assertIn("HTTP 422", trigger["interrupt_message"])
        self.assertEqual(
            [action["type"] for action in command["actions"]],
            ["abort_task"],
        )
        self.assertEqual(
            command["actions"][0]["reason"],
            "arena_prompt_rejected",
        )

    def test_arena_response_error_command_is_enabled_in_local_overrides(self):
        payload = json.loads(
            (PROJECT_ROOT / "config" / "commands.local.json").read_text(encoding="utf-8")
        )
        local_commands = {item.get("id"): item for item in payload.get("commands", [])}

        self.assertTrue(local_commands["cmd_arena_response_error_500"]["enabled"])
        self.assertTrue(local_commands["cmd_arena_prompt_rejected_422"]["enabled"])

    def test_standalone_arena_ip_rotation_command_is_enabled_in_local_overrides(self):
        command = self.commands["cmd_arena_rotate_ip_standalone"]
        self.assertEqual(command["name"], "ARENA手动切换IP（复用自动盲测IP池）")
        self.assertIn(
            "app.services.arena_proxy_rotation import rotate_arena_proxy",
            command["script"],
        )

        payload = json.loads(
            (PROJECT_ROOT / "config" / "commands.local.json").read_text(encoding="utf-8")
        )
        local_commands = {item.get("id"): item for item in payload.get("commands", [])}

        self.assertTrue(local_commands["cmd_arena_rotate_ip_standalone"]["enabled"])


if __name__ == "__main__":
    unittest.main()
