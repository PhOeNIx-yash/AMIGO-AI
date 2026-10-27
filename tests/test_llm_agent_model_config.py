import os
import unittest

from amigo.core import llm_agent


class TestLlmAgentModelConfig(unittest.TestCase):
    def test_default_model_matches_qwen_voice_weights(self):
        model = llm_agent.AVAILABLE_MODELS["qwen3.5-4b"]

        self.assertEqual(llm_agent._active_model_key, "qwen3.5-4b")
        self.assertEqual(llm_agent.MODEL_FILENAME, "Qwen3.5-4B-Q4_K_M.gguf")
        self.assertEqual(os.path.basename(model["path"]), llm_agent.MODEL_FILENAME)
        self.assertEqual(
            model["targets"],
            [("unsloth/Qwen3.5-4B-GGUF", llm_agent.MODEL_FILENAME)],
        )


if __name__ == "__main__":
    unittest.main()
