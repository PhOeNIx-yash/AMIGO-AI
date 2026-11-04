"""
Tests for os_settings.py and settings_catalog.json integration in Amigo.
"""
import unittest
from amigo.services import os_settings
from amigo.services import os_automation
from amigo.utils import tool_registry


class TestOSSettings(unittest.TestCase):
    def test_catalog_is_valid(self):
        """Verify settings_catalog.json syntax, schema, and references."""
        errors = os_settings.validate_catalog()
        self.assertEqual(errors, [], f"Catalog validation errors: {errors}")

    def test_list_settings(self):
        """Verify list_settings returns catalog entries and supports queries."""
        all_settings = os_settings.list_settings()
        self.assertGreater(len(all_settings), 20)
        
        # Query specific setting
        theme_matches = os_settings.list_settings("theme")
        self.assertTrue(any("theme" in s["name"].lower() or "color" in s["name"].lower() for s in theme_matches))

    def test_get_setting(self):
        """Verify get_setting returns a tuple (ok, message) for known settings."""
        ok, msg = os_settings.get_setting("color mode")
        self.assertTrue(ok)
        self.assertTrue("color mode" in msg.lower() or "theme" in msg.lower())

    def test_multilingual_vocabulary_loaded(self):
        """Verify language dictionary is loaded dynamically from settings_catalog.json."""
        lang = os_settings._lang()
        self.assertIn("on", lang.on)
        self.assertIn("enable", lang.on)
        self.assertIn("off", lang.off)
        self.assertIn("disable", lang.off)
        self.assertIn("toggle", lang.toggle)

    def test_os_automation_settings_exports(self):
        """Verify os_automation exports settings functions without raising."""
        self.assertTrue(callable(os_automation.change_setting))
        self.assertTrue(callable(os_automation.get_setting))
        self.assertTrue(callable(os_automation.toggle_setting))
        self.assertTrue(callable(os_automation.list_settings))

    def test_tool_registry_settings_handlers(self):
        """Verify tool registry executes change_setting and get_setting."""
        spoken, url, meta = tool_registry.execute_tool(
            "get_setting",
            {"setting": "color mode"},
            query="what is my color mode",
            spoken="",
        )
        self.assertTrue("color mode" in spoken.lower() or "theme" in spoken.lower())

        # Test tool registry recognizes change_setting tool name
        self.assertIn("change_setting", tool_registry.VALID_TOOL_NAMES)
        self.assertIn("get_setting", tool_registry.VALID_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()

