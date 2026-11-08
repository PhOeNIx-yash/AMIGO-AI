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

if __name__ == "__main__":
    unittest.main()

