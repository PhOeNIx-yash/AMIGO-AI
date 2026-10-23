import os
import unittest

from amigo.core import llm_agent


class TestLlmAgentModelConfig(unittest.TestCase):
    def test_default_model_matches_spark_x2_6_weights(self):
        model = llm_agent.AVAILABLE_MODELS["spark-x2.6-4b"]

        self.assertEqual(llm_agent._active_model_key, "spark-x2.6-4b")
        self.assertEqual(llm_agent.MODEL_FILENAME, "Spark-X2.6-4B-Q4_K_M.gguf")
        self.assertEqual(os.path.basename(model["path"]), llm_agent.MODEL_FILENAME)
        self.assertEqual(
            model["targets"],
            [("abenzerps/Spark-X2.6-4B-Quantized", llm_agent.MODEL_FILENAME)],
        )


if __name__ == "__main__":
    unittest.main()
