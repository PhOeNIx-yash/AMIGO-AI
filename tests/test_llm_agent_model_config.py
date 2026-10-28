import os
import unittest

from amigo.core import llm_agent


class TestLlmAgentModelConfig(unittest.TestCase):
    def test_default_model_matches_nemotron_weights(self):
        model = llm_agent.AVAILABLE_MODELS["nemotron-3.5-bolt"]

        self.assertEqual(llm_agent._active_model_key, "nemotron-3.5-bolt")
        self.assertEqual(llm_agent.MODEL_FILENAME, "NVIDIA-Nemotron-3.5-Bolt-E4B-UD-IQ2_M.gguf")
        self.assertEqual(os.path.basename(model["path"]), llm_agent.MODEL_FILENAME)
        self.assertEqual(
            model["targets"],
            [("bartowski/NVIDIA-Nemotron-3.5-Bolt-E4B-UD-GGUF", llm_agent.MODEL_FILENAME)],
        )


if __name__ == "__main__":
    unittest.main()
