import unittest
from unittest.mock import patch

import llm_agent
import tool_registry


# Test queries that should be handled correctly by the new LLM agent
TEST_QUERIES = [
    # Basic tool calls
    ("what time is it", "get_time"),
    ("what's the date today", "get_date"),
    ("open notepad", "open_app"),
    ("close chrome", "close_app"),
    
    # Media control
    ("play believer by imagine dragons on youtube", "play_youtube"),
    ("pause the music", "pause_media"),
    ("resume playback", "play_media"),
    ("next song", "next_track"),
    ("previous track", "prev_track"),
    ("what song is playing", "current_media"),
    
    # Volume control
    ("set volume to 50", "set_volume"),
    ("mute the volume", "set_volume"),
    ("turn up the volume", "set_volume"),
    ("turn down the volume", "set_volume"),
    
    # System actions
    ("take a screenshot", "take_screenshot"),
    ("lock my pc", "lock_pc"),
    ("put pc to sleep", "sleep_pc"),
    ("restart computer", "restart_pc"),
    ("check system status", "system_status"),
    ("empty recycle bin", "empty_recycle_bin"),
    
    # Web & search
    ("search for python tutorials", "web_search"),
    ("what's the weather in london", "get_weather"),
    
    # Timers & reminders
    ("set a timer for 5 minutes", "set_timer"),
    ("remind me to call mom in 30 minutes", "set_reminder"),
    
    # Window management
    ("minimize all windows", "window_mgmt"),
    ("maximize this window", "window_mgmt"),
    
    # File & document
    ("find file report.pdf", "find_file"),
    ("what does this document say", "document_qa"),
    
    # Calendar & email
    ("check my calendar", "get_calendar"),
    ("check unread emails", "unread_emails"),
    
    # Chat (should use chat tool)
    ("how are you today", "chat"),
    ("tell me a joke", "chat"),
    ("explain quantum computing", "chat"),
]


class TestLLMAgent(unittest.TestCase):
    """Tests for the LLM-based agent."""
    
    def test_basic_tool_calls(self):
        """Test that basic queries route to correct tools."""
        for query, expected_tool in TEST_QUERIES:
            with self.subTest(query=query):
                actions = llm_agent.get_agent_action(query)
                self.assertTrue(len(actions) > 0, f"No actions returned for: {query}")
                self.assertEqual(actions[0]["tool"], expected_tool, 
                    f"Query '{query}' expected tool '{expected_tool}', got '{actions[0]['tool']}'")
    
    def test_open_app_extracts_name(self):
        """Test that open_app extracts the app name correctly."""
        actions = llm_agent.get_agent_action("open notepad")
        self.assertEqual(actions[0]["tool"], "open_app")
        self.assertEqual(actions[0]["params"].get("name"), "notepad")
    
    def test_play_youtube_extracts_query(self):
        """Test that play_youtube extracts the song query correctly."""
        actions = llm_agent.get_agent_action("play believer by imagine dragons on youtube")
        self.assertEqual(actions[0]["tool"], "play_youtube")
        self.assertIn("believer", actions[0]["params"].get("query", "").lower())
        self.assertIn("imagine dragons", actions[0]["params"].get("query", "").lower())
    
    def test_get_weather_extracts_city(self):
        """Test that get_weather extracts the city correctly."""
        actions = llm_agent.get_agent_action("what's the weather in london")
        self.assertEqual(actions[0]["tool"], "get_weather")
        self.assertEqual(actions[0]["params"].get("city"), "London")
    
    def test_set_volume_uses_correct_action(self):
        """Test that set_volume uses correct action enum."""
        actions = llm_agent.get_agent_action("set volume to 50")
        self.assertEqual(actions[0]["tool"], "set_volume")
        self.assertEqual(actions[0]["params"].get("action"), "set_volume")
        self.assertEqual(actions[0]["params"].get("level"), 50)
    
    def test_mute_volume_uses_mute_action(self):
        """Test that mute uses mute action."""
        actions = llm_agent.get_agent_action("mute the volume")
        self.assertEqual(actions[0]["tool"], "set_volume")
        self.assertEqual(actions[0]["params"].get("action"), "mute")
    
    def test_take_screenshot_no_params(self):
        """Test that take_screenshot has no extra parameters."""
        actions = llm_agent.get_agent_action("take a screenshot")
        self.assertEqual(actions[0]["tool"], "take_screenshot")
        self.assertEqual(actions[0]["params"], {})
    
    def test_chat_for_general_questions(self):
        """Test that general questions use chat tool."""
        actions = llm_agent.get_agent_action("how are you today")
        self.assertEqual(actions[0]["tool"], "chat")
        self.assertIn("response", actions[0]["params"])
    
    def test_conversation_context(self):
        """Test that conversation history is used for context."""
        history = [
            {"user": "play believer by imagine dragons", "assistant": "Playing Believer", "tool": "play_youtube"}
        ]
        actions = llm_agent.get_agent_action("play it again", conversation_history=history)
        self.assertEqual(actions[0]["tool"], "play_youtube")
        # Should reference the previous song
        self.assertIn("believer", actions[0]["params"].get("query", "").lower())
    
    def test_utility_functions(self):
        """Test utility functions work."""
        # get_last_played_song should return None with no history
        self.assertIsNone(llm_agent.get_last_played_song())


class TestToolRegistry(unittest.TestCase):
    """Tests for tool registry (unchanged)."""
    
    def test_tool_registry_clarification_metadata_is_returned(self):
        spoken, url, metadata = tool_registry.execute_tool(
            "clarification",
            {"candidate_tools": ["open_app", "web_search"], "query": "open the weather app"},
            query="open the weather app",
            spoken="I'm not sure which action you mean.",
        )

        self.assertEqual(metadata["status"], "requires_clarification")
        self.assertTrue(metadata["requires_clarification"])
        self.assertIn("open_app", metadata["candidates"])

    def test_format_clarification_prompt_lists_two_actions(self):
        prompt = llm_agent.format_clarification_prompt("open_app", "web_search", "open the weather app")
        lower = prompt.lower()

        self.assertIn("open an application", lower)
        self.assertIn("search google", lower)


class TestTaskAgent(unittest.TestCase):
    """Tests for task agent (updated to use new agent)."""
    
    def test_task_agent_decompose_preserves_natural_phrases(self):
        import task_agent
        single = task_agent.decompose_task("search for bed and breakfast")
        self.assertEqual(single, ["search for bed and breakfast"])

        compound = task_agent.decompose_task("close Chrome and search the web")
        self.assertEqual(compound, ["close Chrome", "search the web"])


class TestReminderTimer(unittest.TestCase):
    """Tests for reminder timer (unchanged)."""
    
    def test_reminder_dedup_does_not_swallow_distinct_reminders(self):
        import reminder_timer
        with reminder_timer._reminders_lock:
            reminder_timer._scheduled_reminders.clear()

        r1 = reminder_timer.handle_set_reminder({"message": "call mom", "time": "in 10 minutes"})
        r2 = reminder_timer.handle_set_reminder({"message": "call mom's doctor", "time": "in 10 minutes"})
        self.assertIn("remind you", r1.lower())
        self.assertIn("remind you", r2.lower())
        self.assertNotEqual(r2, "That reminder is already scheduled.")


class TestCalculatenumbers(unittest.TestCase):
    """Tests for calculator (unchanged)."""
    
    def test_spoken_math_multiplication_x(self):
        import Calculatenumbers
        res = Calculatenumbers.Calc("what is 4 x 5")
        self.assertEqual(res, "20")


class TestSettingsResolver(unittest.TestCase):
    """Tests for settings resolver (unchanged)."""
    
    def test_settings_resolver_word_boundary(self):
        import settings_resolver
        label, uri = settings_resolver.resolve_setting("opened files in background")
        self.assertNotIn("Pen", label)


if __name__ == "__main__":
    unittest.main(verbosity=2)

