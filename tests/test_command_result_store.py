import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services import command_result_store


class CommandResultStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.results_file = Path(self.temp_dir.name) / "command_results.local.json"
        self.drawer_file = Path(self.temp_dir.name) / "drawer_data.json"
        self.results_patch = mock.patch.object(command_result_store, "RESULTS_FILE", self.results_file)
        self.results_patch.start()

    def tearDown(self):
        self.results_patch.stop()
        self.temp_dir.cleanup()

    def test_independent_rules_share_candidate_and_write_drawer_group(self):
        values = {
            "link_drawer_path": str(self.drawer_file),
            "rules": [
                {
                    "id": "gpt-54",
                    "name": "GPT 5.4",
                    "model_name": "gpt-5.4",
                    "required_all": "GPT, 5.4",
                    "excluded": "mini",
                    "drawer_group": "GPT 5.4",
                },
                {
                    "id": "claude",
                    "name": "Claude",
                    "model_name": "claude",
                    "required_all": "Claude",
                    "drawer_group": "Claude",
                },
            ],
        }
        info = {
            "url": "https://arena.ai/c/example",
            "response_sides": ["I am GPT 5.4", "I am Claude"],
        }

        outcome = command_result_store.record_arena_rule_candidates(
            "cmd",
            values,
            info,
            profile_resolver=lambda: {"name": "han", "profile_directory": "Profile 9"},
        )

        self.assertEqual(outcome["matched"], 2)
        records = command_result_store.list_command_results("cmd")
        self.assertEqual(
            {item["title"] for item in records},
            {"《han》-gpt-5.4-001", "《han》-claude-001"},
        )
        self.assertEqual({item["browser_profile"]["profile_directory"] for item in records}, {"Profile 9"})
        drawer = json.loads(self.drawer_file.read_text(encoding="utf-8"))
        # Link Drawer globally deduplicates URLs, so the first matching rule owns the shared URL.
        self.assertEqual(len(drawer["links"]), 1)
        self.assertEqual(drawer["links"][0]["category"], "GPT 5.4")
        self.assertEqual(
            drawer["links"][0]["controlledBrowser"]["profile"]["profile_directory"],
            "Profile 9",
        )

    def test_custom_title_template_does_not_carry_profile_routing(self):
        values = {
            "link_drawer_path": str(self.drawer_file),
            "controlled_browser_api_url": "http://127.0.0.1:8199/api/browser/open-profile-url",
            "rules": [{
                "id": "rule",
                "model_name": "gemini3.1",
                "required_all": "match",
                "title_template": "任意名字 {index}",
            }],
        }
        command_result_store.record_arena_rule_candidates(
            "cmd",
            values,
            {"url": "https://arena.ai/c/custom", "response_sides": ["match"]},
            profile_resolver=lambda: {
                "name": "Nhat Dung",
                "profile_directory": "Profile 12",
                "browser_context_id": "ctx-12",
            },
        )

        link = json.loads(self.drawer_file.read_text(encoding="utf-8"))["links"][0]
        self.assertEqual(link["title"], "任意名字 1")
        self.assertEqual(link["controlledBrowser"]["profile"]["browser_context_id"], "ctx-12")

    def test_same_rule_and_url_are_persisted_once(self):
        values = {
            "rules": [{"id": "rule", "model_name": "gpt", "required_all": "match"}],
        }
        info = {"url": "https://arena.ai/c/one", "response_sides": ["match"]}

        resolver = lambda: {"name": "han"}
        first = command_result_store.record_arena_rule_candidates("cmd", values, info, profile_resolver=resolver)
        second = command_result_store.record_arena_rule_candidates("cmd", values, info, profile_resolver=resolver)

        self.assertEqual(first["matched"], 1)
        self.assertEqual(second["matched"], 0)
        self.assertEqual(len(command_result_store.list_command_results("cmd")), 1)

    def test_clear_results_can_target_one_rule(self):
        values = {
            "rules": [
                {"id": "one", "model_name": "one", "required_all": "match"},
                {"id": "two", "model_name": "two", "required_all": "match"},
            ],
        }
        info = {"url": "https://arena.ai/c/one", "response_sides": ["match"]}
        command_result_store.record_arena_rule_candidates(
            "cmd", values, info, profile_resolver=lambda: {"name": "han"}
        )

        removed = command_result_store.clear_command_results("cmd", rule_id="one")

        self.assertEqual(removed, 1)
        self.assertEqual([item["rule_id"] for item in command_result_store.list_command_results("cmd")], ["two"])

    def test_detector_rejection_does_not_persist(self):
        values = {
            "detector_url": "http://detector.test/api/judge",
            "rules": [{
                "id": "rule",
                "model_name": "gpt",
                "required_all": "match",
                "detector_keyword": "gpt-5.4",
            }],
        }
        info = {"url": "https://arena.ai/c/one", "response_sides": ["match"]}
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"success": True, "predictions": [{"model": "gpt-4o"}]}

        with mock.patch.object(command_result_store.requests, "post", return_value=response):
            outcome = command_result_store.record_arena_rule_candidates(
                "cmd",
                values,
                info,
                prompt="hello",
                profile_resolver=lambda: {"name": "han"},
            )

        self.assertEqual(outcome["matched"], 0)
        self.assertEqual(command_result_store.list_command_results("cmd"), [])

    def test_unresolved_profile_does_not_use_manual_fallback(self):
        values = {
            "browser_profile_name": "han",
            "rules": [{
                "id": "rule",
                "model_name": "gpt-5.4",
                "required_all": "match",
                "browser_profile_name": "han",
            }],
        }
        info = {"url": "https://arena.ai/c/other-profile", "response_sides": ["match"]}

        outcome = command_result_store.record_arena_rule_candidates(
            "cmd", values, info, profile_resolver=lambda: {}
        )

        self.assertEqual(outcome["matched"], 0)
        self.assertTrue(outcome["identity_unresolved"])
        self.assertEqual(command_result_store.list_command_results("cmd"), [])


if __name__ == "__main__":
    unittest.main()
