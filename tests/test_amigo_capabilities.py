import unittest
from unittest.mock import patch

import laya_router
import tool_registry


class FakeLayaAgent:
    def __init__(self, choice, confidence=0.8, probabilities=None):
        self.choice = choice
        self.confidence = confidence
        self.probabilities = probabilities or {
            choice: confidence,
            "chat": max(0.05, (1.0 - confidence) / 2),
            "web_search": max(0.05, (1.0 - confidence) / 2),
        }

    def predict(self, state, questions):
        return {
            "answers": {
                "tool": {
                    "choice": self.choice,
                    "confidence": self.confidence,
                    "probabilities": self.probabilities,
                }
            }
        }


CONFUSING_PROMPTS = [
    (
        "what's the weather and also look up my last search",
        {"weather": 0.51, "web_search": 0.49, "chat": 0.00},
    ),
    (
        "open the weather app and search for it too",
        {"open_app": 0.44, "web_search": 0.43, "chat": 0.13},
    ),
    (
        "close Chrome and search the web for the best browser to use",
        {"close_app": 0.47, "web_search": 0.46, "chat": 0.07},
    ),
    (
        "play the song from that article and also open Spotify",
        {"play_youtube": 0.48, "open_app": 0.47, "chat": 0.05},
    ),
    (
        "open the weather app for me please, but also google the weather",
        {"open_app": 0.45, "web_search": 0.44, "chat": 0.11},
    ),
]

CLEAR_PROMPTS = [
    ("what is the weather in Tokyo tomorrow?", {"weather": 0.82, "chat": 0.08, "web_search": 0.10}, "get_weather"),
    ("what time is it right now?", {"time_date": 0.88, "chat": 0.07, "web_search": 0.05}, "get_time"),
    ("open Notepad for me", {"open_app": 0.90, "chat": 0.05, "web_search": 0.05}, "open_app"),
]


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

