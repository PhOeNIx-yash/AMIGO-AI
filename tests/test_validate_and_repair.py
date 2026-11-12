import sys
import os
import unittest

sys.path.insert(0, ".")

from tests import SafeOSTestCase
from amigo.utils.tool_registry import (
    TOOL_DEFINITIONS,
    VALID_TOOL_NAMES,
    _validate_action_plan,
    get_tool_schema,
)

class TestValidateAndRepair(SafeOSTestCase):
    def test_valid_call_passes_validation(self):
        calls = [{"tool": "open_app", "params": {"name": "notepad"}}]
        errors = _validate_action_plan(calls)
        self.assertEqual(errors, [])

    def test_missing_required_param_fails_validation(self):
        calls = [{"tool": "open_app", "params": {}}]
        errors = _validate_action_plan(calls)
        self.assertEqual(len(errors), 1)
        self.assertIn("missing required parameter(s): name", errors[0])

    def test_reminder_missing_time_fails_validation(self):
        calls = [{"tool": "set_reminder", "params": {"message": "buy groceries"}}]
        errors = _validate_action_plan(calls)
        self.assertEqual(len(errors), 1)
        self.assertIn("missing required parameter(s): time", errors[0])

    def test_chat_without_required_params_passes(self):
        calls = [{"tool": "chat", "params": {}}]
        errors = _validate_action_plan(calls)
        self.assertEqual(errors, [])

    def test_conditional_volume_level_required_when_action_is_set_volume(self):
        """set_volume with action='set_volume' conditionally requires numeric level."""
        # Action set_volume without level must fail
        calls_missing = [{"tool": "set_volume", "params": {"action": "set_volume"}}]
        errors = _validate_action_plan(calls_missing)
        self.assertEqual(len(errors), 1)
        self.assertIn("requires a numeric 'level'", errors[0])

        # Action set_volume with level passes
        calls_valid = [{"tool": "set_volume", "params": {"action": "set_volume", "level": 50}}]
        self.assertEqual(_validate_action_plan(calls_valid), [])

        # Non-set actions (e.g. mute, volume_up) do not require level
        calls_up = [{"tool": "set_volume", "params": {"action": "volume_up"}}]
        self.assertEqual(_validate_action_plan(calls_up), [])

    def test_conditional_open_file_requires_path_or_target_index(self):
        """open_file conditionally requires either path or target_index."""
        # Both missing fails
        calls_none = [{"tool": "open_file", "params": {}}]
        errors = _validate_action_plan(calls_none)
        self.assertEqual(len(errors), 1)
        self.assertIn("either 'path' or 'target_index'", errors[0])

        # path present passes
        calls_path = [{"tool": "open_file", "params": {"path": "report.pdf"}}]
        self.assertEqual(_validate_action_plan(calls_path), [])

        # target_index present passes
        calls_idx = [{"tool": "open_file", "params": {"target_index": 1}}]
        self.assertEqual(_validate_action_plan(calls_idx), [])

    def test_conditional_memory_recall_store_and_recall(self):
        """memory_recall conditionally requires fact for store and query for recall."""
        # store missing fact fails
        calls_store_bad = [{"tool": "memory_recall", "params": {"action": "store"}}]
        errors = _validate_action_plan(calls_store_bad)
        self.assertEqual(len(errors), 1)
        self.assertIn("requires 'fact'", errors[0])

        # store with fact passes
        calls_store_ok = [{"tool": "memory_recall", "params": {"action": "store", "fact": "dog is Milo"}}]
        self.assertEqual(_validate_action_plan(calls_store_ok), [])

        # recall missing query fails
        calls_recall_bad = [{"tool": "memory_recall", "params": {"action": "recall"}}]
        errors = _validate_action_plan(calls_recall_bad)
        self.assertEqual(len(errors), 1)
        self.assertIn("requires 'query'", errors[0])

        # recall with query passes
        calls_recall_ok = [{"tool": "memory_recall", "params": {"action": "recall", "query": "coffee preference"}}]
        self.assertEqual(_validate_action_plan(calls_recall_ok), [])

    def test_conditional_read_emails_search_requires_query(self):
        """read_emails with action='search' conditionally requires query."""
        calls_search_bad = [{"tool": "read_emails", "params": {"action": "search"}}]
        errors = _validate_action_plan(calls_search_bad)
        self.assertEqual(len(errors), 1)
        self.assertIn("requires 'query'", errors[0])

        calls_search_ok = [{"tool": "read_emails", "params": {"action": "search", "query": "invoice"}}]
        self.assertEqual(_validate_action_plan(calls_search_ok), [])

        calls_unread = [{"tool": "read_emails", "params": {"action": "unread"}}]
        self.assertEqual(_validate_action_plan(calls_unread), [])

    def test_unrepairable_plan_asks_clarification(self):
        """When repair fails to satisfy required parameters, Amigo asks a spoken clarifying question."""
        from unittest.mock import patch
        from amigo.utils.tool_registry import _route_with_llm

        # Mock LLM to return invalid action plans on both initial turn and repair turn
        bad_json = '{"actions": [{"tool": "open_app", "params": {}}]}'
        with patch("amigo.utils.tool_registry.query_local_llm", return_value=bad_json):
            calls, response = _route_with_llm("open something")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["tool"], "clarification")
            self.assertTrue(calls[0]["params"]["message"].endswith("?"))
            self.assertIn("application", calls[0]["params"]["message"].lower())
            self.assertEqual(calls[0]["params"]["candidate_tools"], ["open_app"])

    def test_app_and_folder_validation_no_guess(self):
        """Non-matching app or folder names ask for clarification without word blacklists."""
        from amigo.services.app_opener import is_known_app, is_known_folder
        from amigo.utils.tool_registry import _tool_open_app, _tool_open_folder

        self.assertTrue(is_known_app("notepad"))
        self.assertFalse(is_known_app("it"))
        self.assertFalse(is_known_app("the app"))
        msg_app, _ = _tool_open_app({"name": "it"}, "open it", "")
        self.assertIn("which application", msg_app.lower())

        self.assertTrue(is_known_folder("downloads"))
        self.assertFalse(is_known_folder("it"))
        self.assertFalse(is_known_folder("the folder"))
        msg_fld, _ = _tool_open_folder({}, "open folder", "")
        self.assertIn("which folder", msg_fld.lower())

    def test_brightness_and_volume_numeric_range(self):
        """Volume and brightness reject non-numeric values or out-of-range numbers."""
        for tool in ("set_volume", "set_brightness"):
            for bad_lvl in ("default", -5, 150):
                p = {"action": "set_volume", "level": bad_lvl} if tool == "set_volume" else {"level": bad_lvl}
                errs = _validate_action_plan([{"tool": tool, "params": p}])
                self.assertEqual(len(errs), 1, f"{tool} with level={bad_lvl} should fail validation")

        self.assertEqual(_validate_action_plan([{"tool": "set_brightness", "params": {"level": 80}}]), [])

    def test_network_tools_try_first_and_fail_clearly_when_offline(self):
        """Network tools never pre-block on raw socket probes and return offline status only when execution fails offline."""
        from types import SimpleNamespace
        from unittest.mock import patch
        from amigo.utils.tool_registry import _tool_get_weather, _tool_open_website, _tool_web_search

        # Even if raw 1.1.1.1 probe returns False, tools execute directly instead of pre-blocking.
        with patch("amigo.utils.tool_registry.is_internet_connected", return_value=False), \
             patch("amigo.utils.tool_registry.webbrowser.open", return_value=True) as mock_open, \
             patch("amigo.utils.tool_registry.search_web", return_value=SimpleNamespace(results=[], engines_used=[])) as mock_search, \
             patch("amigo.utils.tool_registry._cached_weather_data", return_value={}), \
             patch("amigo.utils.tool_registry.weather_command", return_value=""):
            msg, url, _ = _tool_open_website({"url": "python.org"}, "go to python.org", "")
            mock_open.assert_called_once_with("https://python.org")
            self.assertEqual(url, "https://python.org")
            self.assertTrue(msg.startswith("Opening"))

            msg_srch, _, meta_srch = _tool_web_search({"query": "news"}, "latest news", "")
            mock_search.assert_called_once()
            self.assertEqual(meta_srch.get("status"), "offline")
            self.assertIn("offline", msg_srch.lower())

            msg_wx, _, meta_wx = _tool_get_weather({"city": "London"}, "weather in London", "")
            self.assertEqual(meta_wx.get("status"), "offline")
            self.assertIn("offline", msg_wx.lower())

        # When webbrowser.open fails while offline, return offline error.
        with patch("amigo.utils.tool_registry.is_internet_connected", return_value=False), \
             patch("amigo.utils.tool_registry.webbrowser.open", return_value=False):
            msg_web, url_web, meta_web = _tool_open_website({"url": "python.org"}, "go to python.org", "")
            self.assertEqual(meta_web.get("status"), "offline")
            self.assertIn("offline", msg_web.lower())
            self.assertIsNone(url_web)

    def test_step_23b_merged_tool_validation(self):
        """Merged media_control, time_date, and power_control validate required enums."""
        self.assertEqual(_validate_action_plan([{"tool": "media_control", "params": {"action": "pause"}}]), [])
        self.assertEqual(_validate_action_plan([{"tool": "media_control", "params": {}}]), ["Tool 'media_control' is missing required parameter(s): action.", "Tool 'media_control' requires 'action' in: resume, pause, next, prev, status."])
        self.assertEqual(_validate_action_plan([{"tool": "time_date", "params": {"action": "time"}}]), [])
        self.assertEqual(len(_validate_action_plan([{"tool": "time_date", "params": {"action": "invalid"}}])), 1)
        self.assertEqual(_validate_action_plan([{"tool": "power_control", "params": {"action": "restart"}}]), [])
        self.assertEqual(len(_validate_action_plan([{"tool": "power_control", "params": {"action": "restart my pc"}}])), 1)

    def test_grounding_validation_blocks_guessed_params_without_word_lists(self):
        """State-changing tools reject ungrounded hallucinated values and accept values present in user words or context."""
        # Ungrounded guesses must fail validation
        self.assertEqual(len(_validate_action_plan([{"tool": "open_app", "params": {"name": "Chrome"}}], grounding_text="open it")), 1)
        self.assertEqual(len(_validate_action_plan([{"tool": "open_app", "params": {"name": "Chrome"}}], grounding_text="launch the app")), 1)
        self.assertEqual(len(_validate_action_plan([{"tool": "change_setting", "params": {"setting": "dark mode", "value": "toggle"}}], grounding_text="toggle setting")), 1)
        self.assertEqual(len(_validate_action_plan([{"tool": "set_volume", "params": {"action": "set_volume", "level": 100}}], grounding_text="set volume level")), 1)
        self.assertGreaterEqual(len(_validate_action_plan([{"tool": "open_website", "params": {"url": "chrome"}}], grounding_text="launch site")), 1)
        self.assertEqual(len(_validate_action_plan([{"tool": "open_website", "params": {"url": "https://www.example.com"}}], grounding_text="open website")), 1)
        self.assertEqual(len(_validate_action_plan([{"tool": "open_website", "params": {"url": "https://www.google.com"}}], grounding_text="open web page")), 1)

        # Grounded values in query or recent context must pass
        self.assertEqual(_validate_action_plan([{"tool": "open_app", "params": {"name": "Visual Studio Code"}}], grounding_text="open vs code"), [])
        self.assertEqual(_validate_action_plan([{"tool": "change_setting", "params": {"setting": "wifi", "value": "off"}}], grounding_text="turn wifi off"), [])
        self.assertEqual(_validate_action_plan([{"tool": "change_setting", "params": {"setting": "wifi", "value": "toggle"}}], grounding_text="toggle wi-fi"), [])
        self.assertEqual(_validate_action_plan([{"tool": "change_setting", "params": {"setting": "wifi", "value": "on"}}], grounding_text="turn that back on turn off wifi Wi-Fi turned off."), [])
        self.assertEqual(_validate_action_plan([{"tool": "set_volume", "params": {"action": "set_volume", "level": 50}}], grounding_text="set volume to 50"), [])
        self.assertEqual(_validate_action_plan([{"tool": "open_website", "params": {"url": "python.org"}}], grounding_text="go to python.org"), [])


if __name__ == "__main__":
    unittest.main()

