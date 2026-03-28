import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.config.engine import ConfigConstants, ConfigEngine


DEFAULT_PRESET = "\u4e3b\u9884\u8bbe-sidebyside\u6a21\u5f0f"
SWITCH_PRESET = "\u9884\u8bbe_\u5207\u6362SideBySide"
VERIFY_PRESET = "\u9884\u8bbe_\u8fc7cf\u76fe"
SWITCH_ALIAS = "\u5207\u6362SideBySide"
VERIFY_ALIAS = "\u8fc7cf\u76fe"
RENAMED_PRESET = "\u9884\u8bbe_\u65b0\u7684\u5207\u6362"
RENAMED_ALIAS = "\u65b0\u7684\u5207\u6362"


class ConfigEnginePresetAliasTests(unittest.TestCase):
    def setUp(self):
        self.engine = ConfigEngine.__new__(ConfigEngine)
        self.engine.sites = {
            "arena.ai": {
                "default_preset": DEFAULT_PRESET,
                "presets": {
                    DEFAULT_PRESET: {"name": "default"},
                    SWITCH_PRESET: {"name": "switch"},
                    VERIFY_PRESET: {"name": "verify"},
                },
            }
        }

    def test_resolves_alias_without_prefix(self):
        data = self.engine._get_site_data("arena.ai", SWITCH_ALIAS)
        self.assertEqual(data["name"], "switch")

    def test_resolves_another_alias_without_prefix(self):
        data = self.engine._get_site_data("arena.ai", VERIFY_ALIAS)
        self.assertEqual(data["name"], "verify")

    def test_keeps_exact_match_when_present(self):
        data = self.engine._get_site_data("arena.ai", DEFAULT_PRESET)
        self.assertEqual(data["name"], "default")


class ConfigEngineRenamePresetTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        self.commands_file = Path(self.tempdir.name) / "commands.json"
        self.local_sites_file = Path(self.tempdir.name) / "sites.local.json"
        self.config_file = Path(self.tempdir.name) / "sites.json"

        self.original_commands_file = ConfigConstants.COMMANDS_FILE
        ConfigConstants.COMMANDS_FILE = str(self.commands_file)
        self.addCleanup(self._restore_commands_file)

        commands_payload = {
            "commands": [
                {
                    "id": "cmd_arena",
                    "name": "Arena Command",
                    "trigger": {"scope": "domain", "domain": "arena.ai"},
                    "actions": [{"type": "execute_workflow", "preset_name": SWITCH_ALIAS}],
                },
                {
                    "id": "cmd_other",
                    "name": "Other Command",
                    "trigger": {"scope": "domain", "domain": "gemini.google.com"},
                    "actions": [{"type": "execute_workflow", "preset_name": SWITCH_ALIAS}],
                },
            ]
        }
        self.commands_file.write_text(
            json.dumps(commands_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.engine = ConfigEngine.__new__(ConfigEngine)
        self.engine.config_file = str(self.config_file)
        self.engine.local_sites_file = str(self.local_sites_file)
        self.engine.last_mtime = 0.0
        self.engine.last_local_mtime = 0.0
        self.engine.sites = {
            "arena.ai": {
                "default_preset": SWITCH_PRESET,
                "presets": {
                    DEFAULT_PRESET: {"name": "default"},
                    SWITCH_PRESET: {"name": "switch"},
                },
            }
        }
        self.engine._global_default_presets = {"arena.ai": SWITCH_PRESET}
        self.engine._local_default_presets = {"arena.ai": SWITCH_PRESET}
        self.engine.global_config = type("FakeGlobalConfig", (), {"to_dict": lambda self: {}})()
        self.engine.refresh_if_changed = lambda: None

    def _restore_commands_file(self):
        ConfigConstants.COMMANDS_FILE = self.original_commands_file

    def test_rename_preset_updates_domain_command_references(self):
        ok = self.engine.rename_preset("arena.ai", SWITCH_PRESET, RENAMED_PRESET)
        self.assertTrue(ok)

        commands = json.loads(self.commands_file.read_text(encoding="utf-8"))["commands"]
        arena_action = commands[0]["actions"][0]
        other_action = commands[1]["actions"][0]

        self.assertEqual(arena_action["preset_name"], RENAMED_ALIAS)
        self.assertEqual(other_action["preset_name"], SWITCH_ALIAS)
        self.assertNotIn("arena.ai", self.engine._local_default_presets)
        self.assertEqual(self.engine.sites["arena.ai"]["default_preset"], RENAMED_PRESET)
        self.assertIn(RENAMED_PRESET, self.engine.sites["arena.ai"]["presets"])

    def test_rename_preset_updates_active_tab_reference(self):
        from app.core import browser as browser_module

        original_instance = getattr(browser_module, "_browser_instance", None)
        self.addCleanup(setattr, browser_module, "_browser_instance", original_instance)

        session = types.SimpleNamespace(current_domain="arena.ai", preset_name=SWITCH_ALIAS)
        other_session = types.SimpleNamespace(
            current_domain="gemini.google.com",
            preset_name=SWITCH_ALIAS,
        )
        fake_pool = types.SimpleNamespace(
            get_sessions_snapshot=lambda: [session, other_session],
        )
        browser_module._browser_instance = types.SimpleNamespace(_tab_pool=fake_pool)

        ok = self.engine.rename_preset("arena.ai", SWITCH_PRESET, RENAMED_PRESET)
        self.assertTrue(ok)

        self.assertEqual(session.preset_name, RENAMED_ALIAS)
        self.assertEqual(other_session.preset_name, SWITCH_ALIAS)


if __name__ == "__main__":
    unittest.main()
