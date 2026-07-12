import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.utils import browser_profile_identity
from app.utils.browser_profile_identity import _CACHE, _profile_display_name, _resolve_via_profile_page


class BrowserProfileIdentityTests(unittest.TestCase):
    def setUp(self):
        _CACHE.clear()

    def test_local_state_maps_profile_directory_to_custom_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_path = root / "Profile 9"
            profile_path.mkdir()
            (root / "Local State").write_text(
                json.dumps({"profile": {"info_cache": {"Profile 9": {"name": "han"}}}}),
                encoding="utf-8",
            )

            identity = _profile_display_name(profile_path)

            self.assertEqual(identity["name"], "han")
            self.assertEqual(identity["profile_directory"], "Profile 9")

    def test_generic_default_name_prefers_shortcut_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_path = root / "Default"
            profile_path.mkdir()
            (root / "Local State").write_text(
                json.dumps({
                    "profile": {
                        "info_cache": {
                            "Default": {
                                "name": "您的 Chrome",
                                "shortcut_name": "Nhat Dung",
                                "gaia_given_name": "Nhat Dung",
                            }
                        }
                    }
                }, ensure_ascii=False),
                encoding="utf-8",
            )

            identity = _profile_display_name(profile_path)

            self.assertEqual(identity["name"], "Nhat Dung")

    def test_each_browser_context_resolves_its_own_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = {"ctx-han": "han", "ctx-nh": "NHxxx"}
            paths = {}
            for context_id, name in profiles.items():
                profile_path = root / name / "Profile 1"
                profile_path.mkdir(parents=True)
                (profile_path.parent / "Local State").write_text(
                    json.dumps({"profile": {"info_cache": {"Profile 1": {"name": name}}}}),
                    encoding="utf-8",
                )
                paths[context_id] = profile_path

            browser = _FakeBrowser(paths)
            han = _resolve_via_profile_page(_FakeSourceTab(browser, "source-han", "ctx-han"))
            nh = _resolve_via_profile_page(_FakeSourceTab(browser, "source-nh", "ctx-nh"))

            self.assertEqual(han["name"], "han")
            self.assertEqual(nh["name"], "NHxxx")
            self.assertNotEqual(han["browser_context_id"], nh["browser_context_id"])

    def test_public_resolver_retries_without_environment_fallback(self):
        with mock.patch.dict(
            "os.environ",
            {"BROWSER_PROFILE_NAME": "han", "BROWSER_PROFILE_DIR": "C:/wrong"},
        ):
            with mock.patch.object(
                browser_profile_identity,
                "_resolve_via_profile_page",
                side_effect=[{}, {"name": "NHxxx", "browser_context_id": "ctx-nh"}],
            ) as resolver:
                identity = browser_profile_identity.resolve_tab_browser_profile(object())

        self.assertEqual(identity["name"], "NHxxx")
        self.assertEqual(resolver.call_count, 2)


class _FakeBrowser:
    def __init__(self, profile_paths):
        self.profile_paths = profile_paths
        self.targets = {}
        self.temp_tabs = {}

    def open_popup(self, opener_id, context_id):
        target_id = f"popup-{opener_id}"
        self.targets[target_id] = {
            "targetId": target_id,
            "type": "page",
            "openerId": opener_id,
            "browserContextId": context_id,
        }
        self.temp_tabs[target_id] = _FakeTempTab(self.profile_paths[context_id])

    def _run_cdp(self, method, **kwargs):
        if method == "Target.getTargets":
            return {"targetInfos": list(self.targets.values())}
        if method == "Target.closeTarget":
            self.targets.pop(kwargs["targetId"], None)
            return {"success": True}
        raise AssertionError(method)

    def get_tab(self, target_id):
        return self.temp_tabs[target_id]


class _FakeSourceTab:
    def __init__(self, browser, tab_id, context_id):
        self.browser = browser
        self.tab_id = tab_id
        self.context_id = context_id

    def run_cdp(self, method, **kwargs):
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"targetId": self.tab_id, "browserContextId": self.context_id}}
        raise AssertionError(method)

    def run_js(self, script):
        self.browser.open_popup(self.tab_id, self.context_id)
        return True


class _FakeTempTab:
    def __init__(self, profile_path):
        self.profile_path = profile_path

    def run_cdp(self, method, **kwargs):
        if method != "Page.navigate":
            raise AssertionError(method)
        return {"frameId": "frame"}

    def run_js(self, script):
        return str(self.profile_path)


if __name__ == "__main__":
    unittest.main()
