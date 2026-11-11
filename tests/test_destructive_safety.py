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

    def test_system_control_destructive_safety(self):
        for action in ["shutdown", "restart", "sleep", "empty_bin"]:
            msg, url, meta = execute_tool("system_control", {"action": action})
            self.assertTrue(
                meta.get("requires_confirmation"),
                f"system_control with action={action} must require confirmation!",
            )
            self.assertEqual(meta.get("status"), "requires_confirmation")

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

    def test_cannot_confirm_mismatched_action(self):
        """Confirming an action different from the pending one must be rejected and not execute."""
        from amigo.utils import tool_registry

        # Request confirmation for restart
        execute_tool("restart_pc", {})
        pending = get_pending_confirmation()
        self.assertIsNotNone(pending)

        # Attempt to confirm with a mismatched action (e.g. empty_recycle_bin)
        msg, url, meta = execute_tool("confirm_action", {"action": "empty_recycle_bin"})
        self.assertIn("does not match", msg.lower())
        self.assertEqual(meta.get("error"), "confirmation_action_mismatch")

        # The pending confirmation is still protected
        self.assertIsNotNone(get_pending_confirmation())

        # Correct matching confirmation succeeds
        msg_ok, _, _ = execute_tool("confirm_action", {"action": "restart"})
        self.assertNotIn("does not match", msg_ok.lower())
        self.assertIsNone(get_pending_confirmation())

    def test_confirmation_timer_starts_after_spoken(self):
        """Confirmation 30s timer is anchored/reset once speech playback completes."""
        import time
        from amigo.utils import tool_registry

        # Step 1: Tool requests confirmation. timer_started is initially None.
        execute_tool("sleep_pc", {})
        pending = get_pending_confirmation()
        self.assertIsNotNone(pending)
        self.assertIsNone(pending.get("timer_started"))

        # Step 2: Confirmation question finishes being spoken -> start_pending_confirmation_timer()
        tool_registry.start_pending_confirmation_timer()
        pending = get_pending_confirmation()
        self.assertIsNotNone(pending.get("timer_started"))
        start_t = pending["timer_started"]

        # Step 3: Fast forward 35s after speech finished
        tool_registry._PENDING_CONFIRMATION["timer_started"] = time.time() - 35.0
        self.assertIsNone(get_pending_confirmation())

    def test_confirmation_timer_starts_immediately_when_unspoken(self):
        """If speech is never played (voice off, typed chat, TTS error), confirmation timer starts immediately from creation."""
        import time
        from amigo.utils import tool_registry

        # Tool requests confirmation; speech is never played so timer_started remains None
        execute_tool("restart_pc", {})
        pending = get_pending_confirmation()
        self.assertIsNotNone(pending)
        self.assertIsNone(pending.get("timer_started"))
        self.assertIsNotNone(pending.get("created"))

        # Fast forward created time by 35 seconds (unspoken turn times out after 30s)
        tool_registry._PENDING_CONFIRMATION["created"] = time.time() - 35.0
        self.assertIsNone(get_pending_confirmation())

    def test_power_control_cannot_skip_confirmation(self):
        """Merged power_control must omit confirmed from schema and require confirmation for restart/sleep/shutdown."""
        from amigo.utils.tool_registry import get_tool_schema

        schema = get_tool_schema("power_control")
        self.assertIsNotNone(schema)
        props = (schema.get("parameters") or {}).get("properties") or {}
        self.assertNotIn("confirmed", props)

        for action in ("restart", "sleep", "shutdown"):
            results = execute_actions([{"tool": "power_control", "params": {"action": action, "confirmed": True}}])
            msg, url, meta = results[0]
            self.assertTrue(
                meta.get("requires_confirmation"),
                f"power_control(action='{action}', confirmed=True) must still require confirmation!",
            )
            self.assertEqual(meta.get("status"), "requires_confirmation")



if __name__ == "__main__":
    unittest.main()

