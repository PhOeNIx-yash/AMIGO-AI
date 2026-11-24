import os
import unittest

from amigo.core import llm_agent


class TestLlmAgentModelConfig(unittest.TestCase):
    def test_default_model_matches_qwen_weights(self):
        model = llm_agent.AVAILABLE_MODELS["qwen3.5-4b-instruct"]

        self.assertEqual(llm_agent._active_model_key, "qwen3.5-4b-instruct")
        self.assertEqual(llm_agent.MODEL_FILENAME, "Qwen3.5-4B-Q4_K_M.gguf")
        self.assertEqual(os.path.basename(model["path"]), llm_agent.MODEL_FILENAME)
        self.assertEqual(
            model["targets"],
            llm_agent.MODEL_TARGETS,
        )

    def test_available_models_contains_qwen(self):
        self.assertIn("qwen3.5-4b-instruct", llm_agent.AVAILABLE_MODELS)
        self.assertNotIn("lfm-3-omni", llm_agent.AVAILABLE_MODELS)
        self.assertNotIn("nemotron-3.5-bolt", llm_agent.AVAILABLE_MODELS)
        self.assertNotIn("nemotron-dory-1.0", llm_agent.AVAILABLE_MODELS)


if __name__ == "__main__":
    unittest.main()
