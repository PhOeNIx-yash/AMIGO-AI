import unittest
import os
import sys

sys.path.insert(0, os.path.abspath("."))

from amigo.utils.tool_registry import execute_tool
from amigo.utils.tts import get_active_voice, get_voice_catalog


class TestManageVoices(unittest.TestCase):
    def test_list_voices_summary_and_cards(self):
        spoken, direct, meta = execute_tool("manage_voices", {"action": "list"})
        self.assertIn("voices available", spoken.lower())
        self.assertIn("currently using", spoken.lower())
        self.assertIsInstance(meta, dict)
        self.assertIn("cards", meta)
        self.assertIn("voices", meta)
        self.assertGreater(len(meta["voices"]), 10)

    def test_switch_voice_dynamic_gender(self):
        catalog = get_voice_catalog()
        active_before = get_active_voice()

        # Switch to male voice
        spoken_m, _, meta_m = execute_tool("manage_voices", {"action": "set", "voice": "male"})
        self.assertEqual(meta_m.get("action"), "set")
        male_key = meta_m.get("voice")
        self.assertIn(male_key, catalog)
        self.assertEqual(catalog[male_key].get("gender", "").lower(), "male")

        # Switch to female voice
        spoken_f, _, meta_f = execute_tool("manage_voices", {"action": "set", "voice": "female"})
        self.assertEqual(meta_f.get("action"), "set")
        female_key = meta_f.get("voice")
        self.assertIn(female_key, catalog)
        self.assertEqual(catalog[female_key].get("gender", "").lower(), "female")

        # Switch by name
        spoken_s, _, meta_s = execute_tool("manage_voices", {"action": "set", "voice": "Sarah"})
        self.assertIn("Sarah", spoken_s)


if __name__ == "__main__":
    unittest.main()

