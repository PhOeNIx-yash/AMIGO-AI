import os
import unittest

from amigo.core import llm_agent


class TestLlmAgentModelConfig(unittest.TestCase):
    def test_default_model_matches_lfm3_omni_weights(self):
        model = llm_agent.AVAILABLE_MODELS["lfm-3-omni"]

        self.assertEqual(llm_agent._active_model_key, "lfm-3-omni")
        self.assertEqual(llm_agent.MODEL_FILENAME, "LFM3-Omni-2B-Q4_K_M.gguf")
        self.assertEqual(os.path.basename(model["path"]), llm_agent.MODEL_FILENAME)
        self.assertEqual(
            model["targets"],
            llm_agent.MODEL_TARGETS,
        )

    def test_available_models_contains_lfm3_omni(self):
        self.assertIn("lfm-3-omni", llm_agent.AVAILABLE_MODELS)
        self.assertNotIn("lfm-3-1.7b", llm_agent.AVAILABLE_MODELS)


if __name__ == "__main__":
    unittest.main()
