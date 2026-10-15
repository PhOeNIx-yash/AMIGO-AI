"""
Unit tests for the dynamic content generation, revision tracking, and routing system.
"""

import unittest
from unittest.mock import MagicMock, patch

from amigo.services.content_generator import (
    build_temporal_and_user_context,
    route_content_to_destination,
    generate_content,
    regenerate_content,
    insert_content,
    type_text,
)
from amigo.utils.tool_registry import (
    UI_TOOL_HANDLERS,
    TOOL_DEFINITIONS,
    build_agent_system_prompt,
)
import amigo.core.rag_engine as rag_engine


class TestContentGenerator(unittest.TestCase):
    """Test dynamic context building, multi-destination routing, state tracking, and tool registry integration."""

    def setUp(self):
        # Reset active state before each test
        try:
            rag_engine.update_active_state("generated_content", None)
        except Exception:
            pass

    @patch("amigo.core.rag_engine.load_profile")
    def test_build_temporal_and_user_context(self, mock_load_profile):
        mock_load_profile.return_value = {
            "name": "Alex",
            "role": "Engineer",
            "preferences": {"style": "concise"},
        }
        ctx = build_temporal_and_user_context()
        self.assertIn("Current Date & Time:", ctx)
        self.assertIn("Tomorrow:", ctx)
        self.assertIn("Upcoming 7-Day Reference:", ctx)
        self.assertIn("User Name: Alex", ctx)
        self.assertIn("User Role: Engineer", ctx)

    @patch("pyperclip.copy")
    def test_route_to_clipboard(self, mock_copy):
        routed, msg = route_content_to_destination("Hello World", "clipboard")
        self.assertTrue(routed)
        self.assertIn("clipboard", msg.lower())
        mock_copy.assert_called_with("Hello World")

    @patch("webbrowser.open")
    @patch("pyperclip.copy")
    def test_route_to_gmail(self, mock_copy, mock_web_open):
        sample_email = "Subject: Leave Request\n\nPlease approve my leave tomorrow."
        routed, msg = route_content_to_destination(
            sample_email,
            "gmail",
            content_type="email",
            subject="Leave Request",
            recipient="manager@example.com",
        )
        self.assertTrue(routed)
        self.assertIn("Gmail", msg)
        mock_web_open.assert_called_once()
        opened_url = mock_web_open.call_args[0][0]
        self.assertIn("mail.google.com", opened_url)
        self.assertIn("Leave%20Request", opened_url)
        self.assertIn("manager%40example.com", opened_url)

    def test_route_to_empty_or_panel(self):
        routed, msg = route_content_to_destination("Draft text", "")
        self.assertFalse(routed)
        self.assertEqual(msg, "")

        routed2, msg2 = route_content_to_destination("Draft text", "panel")
        self.assertFalse(routed2)
        self.assertEqual(msg2, "")

    @patch("amigo.services.content_generator._get_ai_response")
    def test_generate_content(self, mock_ai):
        mock_ai.return_value = "Subject: Vacation\n\nI will be on vacation tomorrow."
        params = {"type": "email", "topic": "vacation tomorrow", "tone": "formal"}
        spoken, link, meta = generate_content(params, "draft an email for vacation", "Drafting email")

        self.assertIn("email", spoken.lower())
        self.assertIsNotNone(meta)
        self.assertIn("Vacation", meta["generated_content"])
        self.assertEqual(meta["content_type"], "email")

        # Verify saved in active state
        state = rag_engine.get_active_state(clean_expired=False) or {}
        gen_state = state.get("generated_content", {})
        self.assertEqual(gen_state.get("content"), "Subject: Vacation\n\nI will be on vacation tomorrow.")
        self.assertEqual(gen_state.get("type"), "email")

    @patch("amigo.services.content_generator._get_ai_response")
    def test_regenerate_content(self, mock_ai):
        # 1. Seed active state with initial draft
        rag_engine.update_active_state("generated_content", {
            "content": "Initial Draft",
            "type": "email",
            "topic": "project status",
            "tone": "formal",
            "history": ["Initial Draft"],
            "timestamp": 1234567.0,
        })

        mock_ai.return_value = "Regenerated Friendly Draft"
        params = {"feedback": "make it more friendly"}
        spoken, link, meta = regenerate_content(params, "make it friendly", "")

        self.assertTrue(any(word in spoken.lower() for word in ("updated", "regenerated")))
        self.assertTrue(meta.get("is_regenerated"))
        self.assertEqual(meta["generated_content"], "Regenerated Friendly Draft")

        # Verify active state history was updated
        state = rag_engine.get_active_state(clean_expired=False) or {}
        gen_state = state.get("generated_content", {})
        self.assertEqual(gen_state.get("content"), "Regenerated Friendly Draft")
        self.assertIn("Initial Draft", gen_state.get("history", []))
        self.assertIn("Regenerated Friendly Draft", gen_state.get("history", []))

    @patch("amigo.services.content_generator.route_content_to_destination")
    def test_insert_content_from_state(self, mock_route):
        mock_route.return_value = (True, "Inserted into active window.")
        rag_engine.update_active_state("generated_content", {
            "content": "Draft from state to insert",
            "type": "note",
            "topic": "quick note",
        })

        reply, link, meta = insert_content({"destination": "active window"}, "insert it", "")
        self.assertEqual(reply, "Inserted into active window.")
        mock_route.assert_called_once()
        self.assertEqual(mock_route.call_args[0][0], "Draft from state to insert")

    @patch("amigo.services.content_generator.generate_content")
    def test_type_text_delegates_draft_request(self, mock_gen):
        mock_gen.return_value = ("Drafted email", None, {})
        spoken, link = type_text({"text": "an email to my manager about sick leave"}, "write an email", "")
        mock_gen.assert_called_once()
        self.assertEqual(spoken, "Drafted email")

    @patch("amigo.services.os_automation.type_text")
    def test_type_text_literal(self, mock_type):
        spoken, link = type_text({"text": "Hello 123"}, "type hello 123", "")
        mock_type.assert_called_with("Hello 123")
        self.assertIn("Typed", spoken)

    def test_tool_registry_registration(self):
        self.assertIn("generate_content", UI_TOOL_HANDLERS)
        self.assertIn("regenerate_content", UI_TOOL_HANDLERS)
        self.assertIn("insert_content", UI_TOOL_HANDLERS)
        self.assertIn("type_text", UI_TOOL_HANDLERS)

        tool_names = [t["name"] for t in TOOL_DEFINITIONS]
        self.assertIn("generate_content", tool_names)
        self.assertIn("regenerate_content", tool_names)
        self.assertIn("insert_content", tool_names)

    def test_router_prompt_active_draft_state(self):
        rag_engine.update_active_state("generated_content", {
            "content": "Active Draft Text",
            "type": "leave email",
            "topic": "sick leave",
            "destination": "awaiting user destination choice",
        })
        prompt = build_agent_system_prompt("In Gmail")
        self.assertIn("Active Generated Draft: leave email about 'sick leave'", prompt)

    def test_valid_tool_names_includes_all_handlers(self):
        from amigo.utils.tool_registry import VALID_TOOL_NAMES
        for handler_key in UI_TOOL_HANDLERS.keys():
            self.assertIn(handler_key, VALID_TOOL_NAMES, f"Handler {handler_key} missing from VALID_TOOL_NAMES")

    @patch("amigo.utils.tool_registry.get_pending_confirmation")
    def test_pending_confirmation_line_uses_action(self, mock_pending):
        from amigo.utils.tool_registry import _pending_confirmation_line
        mock_pending.return_value = {
            "tool": "empty_recycle_bin",
            "action": "empty recycle bin",
            "params": {},
        }
        line = _pending_confirmation_line()
        self.assertIn("empty recycle bin", line)

    @patch("pyautogui.hotkey")
    def test_window_action_maximize_and_restore(self, mock_hotkey):
        from amigo.services.os_automation import window_action
        self.assertTrue(window_action("maximize"))
        mock_hotkey.assert_called_with("win", "up")

        self.assertTrue(window_action("restore"))
        mock_hotkey.assert_called_with("win", "down")

    @patch("subprocess.run")
    def test_safe_restart_pc(self, mock_run):
        from amigo.services.os_automation import restart_pc
        mock_run.return_value.returncode = 0
        self.assertTrue(restart_pc("45"))
        mock_run.assert_called_with(["shutdown", "/r", "/t", "45"], check=True)


if __name__ == "__main__":
    unittest.main()

