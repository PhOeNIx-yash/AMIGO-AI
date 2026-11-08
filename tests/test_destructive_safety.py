import unittest
import os
import sys

sys.path.insert(0, os.path.abspath("."))

from amigo.utils.tool_registry import execute_tool, get_pending_confirmation


class TestDestructiveSafety(unittest.TestCase):
    """
    Guarantees that destructive tools (shutdown, restart, sleep, empty_recycle_bin)
    require user confirmation when confirmed is False/omitted.
    """

    def test_empty_recycle_bin_safety(self):
        msg, url, meta = execute_tool("empty_recycle_bin", {})
        self.assertTrue(meta.get("requires_confirmation"))
        self.assertEqual(meta.get("status"), "requires_confirmation")
        pending = get_pending_confirmation()
        self.assertIsNotNone(pending)
        self.assertIn("recycle bin", pending.get("action", "").lower())

    def test_sleep_pc_safety(self):
        msg, url, meta = execute_tool("sleep_pc", {})
        self.assertTrue(meta.get("requires_confirmation"))
        self.assertEqual(meta.get("status"), "requires_confirmation")
        pending = get_pending_confirmation()
        self.assertIsNotNone(pending)
        self.assertIn("sleep", pending.get("action", "").lower())

    def test_restart_pc_safety(self):
        msg, url, meta = execute_tool("restart_pc", {})
        self.assertTrue(meta.get("requires_confirmation"))
        self.assertEqual(meta.get("status"), "requires_confirmation")
        pending = get_pending_confirmation()
        self.assertIsNotNone(pending)
        self.assertIn("restart", pending.get("action", "").lower())

    def test_system_control_destructive_safety(self):
        for action in ["shutdown", "restart", "sleep", "empty_bin"]:
            msg, url, meta = execute_tool("system_control", {"action": action})
            self.assertTrue(
                meta.get("requires_confirmation"),
                f"system_control with action={action} must require confirmation!",
            )
            self.assertEqual(meta.get("status"), "requires_confirmation")


if __name__ == "__main__":
    unittest.main()

