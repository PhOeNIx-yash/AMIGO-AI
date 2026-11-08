import unittest
import os
import sys

sys.path.insert(0, os.path.abspath("."))

from amigo.utils.tool_registry import execute_tool, execute_actions, get_pending_confirmation


class TestDestructiveSafety(unittest.TestCase):
    """
    Guarantees that destructive tools (shutdown, restart, sleep, empty_recycle_bin)
    require user confirmation when confirmed is False/omitted, and that model-generated
    actions CANNOT bypass confirmation by outputting confirmed=True.
    """

    def test_model_cannot_skip_confirmation(self):
        """Model cannot bypass confirmation by injecting confirmed=True in action params."""
        for tool_name in ["restart_pc", "sleep_pc", "empty_recycle_bin"]:
            results = execute_actions([{"tool": tool_name, "params": {"confirmed": True}}])
            msg, url, meta = results[0]
            self.assertTrue(
                meta.get("requires_confirmation"),
                f"Model must not be able to skip confirmation on '{tool_name}' via params.confirmed=True!",
            )
            self.assertEqual(meta.get("status"), "requires_confirmation")

        # Also test system_control with shutdown
        results = execute_actions([{"tool": "system_control", "params": {"action": "shutdown", "confirmed": True}}])
        msg, url, meta = results[0]
        self.assertTrue(
            meta.get("requires_confirmation"),
            "Model must not be able to skip confirmation on system_control shutdown via params.confirmed=True!",
        )

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

    def test_confirmation_expires_after_30_seconds(self):
        """A pending confirmation must expire and be rejected after 30 seconds."""
        import time
        from amigo.utils import tool_registry

        # Request confirmation
        execute_tool("restart_pc", {})
        pending = get_pending_confirmation()
        self.assertIsNotNone(pending)

        # Fast-forward created timestamp beyond 30 seconds
        tool_registry._PENDING_CONFIRMATION["created"] = time.time() - 35.0

        # Attempt confirmation after expiration
        msg, url, meta = execute_tool("confirm_action", {})
        self.assertIn("nothing waiting", msg.lower())
        self.assertIsNone(get_pending_confirmation())

    def test_confirm_action_with_nothing_pending(self):
        """A 'yes' or confirm_action with nothing pending must do nothing."""
        from amigo.utils import tool_registry
        tool_registry._PENDING_CONFIRMATION = None

        msg, url, meta = execute_tool("confirm_action", {})
        self.assertIn("nothing waiting", msg.lower())
        self.assertIsNone(get_pending_confirmation())


if __name__ == "__main__":
    unittest.main()

