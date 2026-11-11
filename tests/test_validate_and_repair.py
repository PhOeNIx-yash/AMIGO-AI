import sys
import os
import unittest

sys.path.insert(0, ".")

from amigo.utils.tool_registry import (
    TOOL_DEFINITIONS,
    VALID_TOOL_NAMES,
    _validate_action_plan,
    get_tool_schema,
)

class TestValidateAndRepair(unittest.TestCase):
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

    def test_network_tools_fail_clearly_when_offline(self):
        """Internet-dependent tools fail with a short clear offline message when disconnected, and succeed online."""
        from unittest.mock import patch
        from amigo.utils.tool_registry import _tool_open_website, _tool_web_search, _tool_get_weather

        with patch("amigo.utils.tool_registry.is_internet_connected", return_value=False):
            msg_web, url_web, meta_web = _tool_open_website({"url": "python.org"}, "go to python.org", "")
            self.assertIn("offline", msg_web.lower())
            self.assertIsNone(url_web)
            self.assertEqual(meta_web.get("status"), "offline")

            msg_search, url_search, meta_search = _tool_web_search({"query": "python docs"}, "search python docs", "")
            self.assertIn("offline", msg_search.lower())
            self.assertIsNone(url_search)
            self.assertEqual(meta_search.get("status"), "offline")

            msg_wx, url_wx, meta_wx = _tool_get_weather({"location": "Tokyo"}, "weather in Tokyo", "")
            self.assertIn("offline", msg_wx.lower())
            self.assertIsNone(url_wx)
            self.assertEqual(meta_wx.get("status"), "offline")

        with patch("amigo.utils.tool_registry.is_internet_connected", return_value=True), \
             patch("amigo.utils.tool_registry.webbrowser.open") as mock_open:
            msg, url, meta = _tool_open_website({"url": "python.org"}, "go to python.org", "")
            mock_open.assert_called_once_with("https://python.org")
            self.assertEqual(url, "https://python.org")
            self.assertTrue(msg.startswith("Opening"))

    def test_merged_tool_enum_validation(self):
        """Merged tools validate enum choices generically from TOOL_DEFINITIONS."""
        self.assertEqual(_validate_action_plan([{"tool": "time_date", "params": {"action": "time"}}]), [])
        errs = _validate_action_plan([{"tool": "time_date", "params": {"action": "year"}}])
        self.assertEqual(len(errs), 1)
        self.assertIn("must be one of", errs[0].lower())
        self.assertEqual(_validate_action_plan([{"tool": "media_control", "params": {"action": "pause"}}]), [])
        self.assertEqual(len(_validate_action_plan([{"tool": "power_control", "params": {"action": "format"}}])), 1)


if __name__ == "__main__":
    unittest.main()

