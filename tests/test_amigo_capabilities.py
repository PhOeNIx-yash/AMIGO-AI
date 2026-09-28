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
    """Tests for the new LLM-based agent (replaces Laya Router)."""
    
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
        # is_laya_ready should return False now (no Laya model)
        self.assertFalse(llm_agent.is_laya_ready())
        
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


class TestAmigoCapabilities(unittest.TestCase):
    def test_confusing_weather_vs_search_is_flagged_for_clarification(self):
        probs = {
            "weather": 0.51,
            "web_search": 0.49,
            "chat": 0.0,
        }
        with patch.object(laya_router, "get_laya_agent", return_value=FakeLayaAgent("weather", 0.51, probs)):
            result = laya_router.route_intent_via_laya("what's the weather and also look up my last search", conversation_history=[])

        self.assertEqual(result["tool"], "clarification")
        self.assertIn("weather", result["speak"].lower())
        self.assertIn("search", result["speak"].lower())

    def test_confusing_prompt_set_is_flagged_for_clarification(self):
        for prompt, probs in CONFUSING_PROMPTS:
            with self.subTest(prompt=prompt):
                top_tool = max(probs, key=probs.get)
                with patch.object(laya_router, "get_laya_agent", return_value=FakeLayaAgent(top_tool, probs[top_tool], probs)):
                    result = laya_router.route_intent_via_laya(prompt, conversation_history=[])

                self.assertEqual(result["tool"], "clarification")
                self.assertIn("candidate_tools", result["params"])
                self.assertEqual(len(result["params"]["candidate_tools"]), 2)

    def test_clear_single_intent_requests_do_not_trigger_clarification(self):
        for prompt, probs, expected_tool in CLEAR_PROMPTS:
            with self.subTest(prompt=prompt):
                top_tool = max(probs, key=probs.get)
                with patch.object(laya_router, "get_laya_agent", return_value=FakeLayaAgent(top_tool, probs[top_tool], probs)):
                    result = laya_router.route_intent_via_laya(prompt, conversation_history=[])

                self.assertEqual(result["tool"], expected_tool)
                self.assertNotEqual(result["tool"], "clarification")

    def test_clear_weather_request_does_not_trigger_clarification(self):
        with patch.object(laya_router, "get_laya_agent", return_value=FakeLayaAgent("weather", 0.82, {"weather": 0.82, "chat": 0.08, "web_search": 0.10})):
            result = laya_router.route_intent_via_laya("what is the weather in Tokyo tomorrow?", conversation_history=[])

        self.assertEqual(result["tool"], "get_weather")
        self.assertNotEqual(result["tool"], "clarification")

    def test_confusing_open_app_vs_search_is_flagged_for_clarification(self):
        probs = {
            "open_app": 0.44,
            "web_search": 0.43,
            "chat": 0.13,
        }
        with patch.object(laya_router, "get_laya_agent", return_value=FakeLayaAgent("open_app", 0.44, probs)):
            result = laya_router.route_intent_via_laya("open the weather app and search for it too", conversation_history=[])

        self.assertEqual(result["tool"], "clarification")
        self.assertEqual(result["params"]["candidate_tools"][0], "open_app")
        self.assertEqual(result["params"]["candidate_tools"][1], "web_search")

    def test_low_confidence_router_still_asks_for_clarification(self):
        probs = {
            "weather": 0.38,
            "web_search": 0.36,
            "chat": 0.26,
        }
        with patch.object(laya_router, "get_laya_agent", return_value=FakeLayaAgent("weather", 0.38, probs)):
            result = laya_router.route_intent_via_laya("can you check the weather and also search the internet for what it was like yesterday?", conversation_history=[])

        self.assertEqual(result["tool"], "clarification")
        self.assertEqual(result["params"]["candidate_tools"], ["weather", "web_search"])

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
        prompt = laya_router.format_clarification_prompt("open_app", "web_search", "open the weather app")
        lower = prompt.lower()

        self.assertIn("open an application", lower)
        self.assertIn("search google", lower)

    def test_process_query_preserves_params(self):
        import ui_server
        with patch.object(ui_server, "get_agent_action", return_value=[{"tool": "open_app", "params": {"app_name": "notepad"}, "speak": "Opening Notepad."}]):
            res = ui_server.process_query("open notepad", is_voice=False)
            self.assertEqual(res["tool"], "open_app")
            self.assertEqual(res["params"], {"app_name": "notepad"})

    def test_song_opinion_resolves_to_chat(self):
        tool, params = laya_router.extract_parameters_and_tool("play_youtube", "this song is very good")
        self.assertEqual(tool, "chat")

    def test_play_song_with_sun_extracts_title(self):
        tool, params = laya_router.extract_parameters_and_tool("play_youtube", "Play song if the sun burns out tonight.")
        self.assertEqual(tool, "play_youtube")
        self.assertEqual(params.get("query"), "if the sun burns out tonight")

    def test_current_media_query_detected(self):
        tool, params = laya_router.extract_parameters_and_tool("media_control", "what song is currently playing")
        self.assertEqual(tool, "current_media")

    def test_laya_router_includes_history_in_state(self):
        fake_agent = FakeLayaAgent("close_app", 0.95)
        history = [
            {"user": "open Chrome", "assistant": "Opening Google Chrome", "tool": "open_app"},
        ]
        with patch.object(laya_router, "get_laya_agent", return_value=fake_agent):
            result = laya_router.route_intent_via_laya("close it", conversation_history=history)

        self.assertIsNotNone(fake_agent.last_state)
        self.assertEqual(fake_agent.last_state["request"], "close it")
        self.assertIn("history", fake_agent.last_state)
        self.assertEqual(len(fake_agent.last_state["history"]), 1)
        self.assertEqual(fake_agent.last_state["history"][0]["user"], "open Chrome")
        self.assertEqual(fake_agent.last_state["history"][0]["tool"], "open_app")
        self.assertEqual(result["tool"], "close_app")
        self.assertEqual(result["params"].get("app_name"), "chrome")

    def test_weather_follow_up_inherits_city_from_history(self):
        history = [
            {"user": "what is the weather in Mumbai?", "assistant": "In Mumbai it is 30°C", "tool": "weather"}
        ]
        tool, params = laya_router.extract_parameters_and_tool("weather", "what about tomorrow?", conversation_history=history)
        self.assertEqual(tool, "get_weather")
        self.assertEqual(params.get("city"), "Mumbai")

    def test_play_it_again_resolves_previous_song(self):
        history = [
            {"user": "play Believer by Imagine Dragons", "assistant": "Playing Believer", "tool": "play_youtube"}
        ]
        tool, params = laya_router.extract_parameters_and_tool("play_youtube", "play it again", conversation_history=history)
        self.assertEqual(tool, "play_youtube")
        self.assertEqual(params.get("query"), "Believer by Imagine Dragons")

    def test_spoken_math_multiplication_x(self):
        import Calculatenumbers
        res = Calculatenumbers.Calc("what is 4 x 5")
        self.assertEqual(res, "20")

    def test_settings_resolver_word_boundary(self):
        import settings_resolver
        label, uri = settings_resolver.resolve_setting("opened files in background")
        self.assertNotIn("Pen", label)

    def test_task_agent_decompose_preserves_natural_phrases(self):
        import task_agent
        single = task_agent.decompose_task("search for bed and breakfast")
        self.assertEqual(single, ["search for bed and breakfast"])

        compound = task_agent.decompose_task("close Chrome and search the web")
        self.assertEqual(compound, ["close Chrome", "search the web"])

    def test_reminder_dedup_does_not_swallow_distinct_reminders(self):
        import reminder_timer
        with reminder_timer._reminders_lock:
            reminder_timer._scheduled_reminders.clear()

        r1 = reminder_timer.handle_set_reminder({"message": "call mom", "time": "in 10 minutes"})
        r2 = reminder_timer.handle_set_reminder({"message": "call mom's doctor", "time": "in 10 minutes"})
        self.assertIn("remind you", r1.lower())
        self.assertIn("remind you", r2.lower())
        self.assertNotEqual(r2, "That reminder is already scheduled.")


if __name__ == "__main__":
    unittest.main(verbosity=2)


