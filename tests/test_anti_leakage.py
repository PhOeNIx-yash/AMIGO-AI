import unittest
import re
import os
import sys

sys.path.insert(0, os.path.abspath("."))

from tests.held_out_eval_set import HELD_OUT_EVAL_DATA
from amigo.utils.tool_registry import get_static_master_prompt, TOOL_DEFINITIONS


def _extract_4grams(text: str) -> set[tuple[str, ...]]:
    tokens = re.findall(r"\b[a-z0-9]+\b", text.lower())
    if len(tokens) < 4:
        return set()
    return {tuple(tokens[i : i + 4]) for i in range(len(tokens) - 3)}


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"\b[a-z0-9]+\b", text.lower()))


class TestAntiLeakage(unittest.TestCase):
    """
    Guarantees no eval string or distinctive 4-gram phrase from the held-out eval set
    leaks into the master system prompt, few-shot examples, or tool descriptions.
    """

    def setUp(self):
        self.master_prompt = get_static_master_prompt()
        self.tool_descriptions = " ".join(
            f"{t['name']} {t['description']} {str(t.get('parameters', {}))}"
            for t in TOOL_DEFINITIONS
        )
        self.full_prompt_corpus = (self.master_prompt + " " + self.tool_descriptions).lower()
        self.normalized_corpus = _normalize(self.full_prompt_corpus)
        self.corpus_4grams = _extract_4grams(self.full_prompt_corpus)

    def test_no_exact_eval_string_leakage(self):
        """No normalized query from the held-out eval set may appear verbatim in the prompt corpus."""
        for item in HELD_OUT_EVAL_DATA:
            q = item["query"]
            norm_q = _normalize(q)
            # Ignore ultra-short 1-2 word queries like "time now", "screenshot" which are unavoidable atomic tokens
            if len(norm_q.split()) >= 3:
                self.assertNotIn(
                    norm_q,
                    self.normalized_corpus,
                    f"LEAKAGE DETECTED: Eval query '{q}' appears verbatim in system prompt/tool schemas!",
                )

    def test_no_4gram_eval_overlap(self):
        """No distinctive 4-gram phrase from eval queries may overlap with the system prompt or few-shot examples."""
        # Generic grammatical glue 4-grams that could legitimately appear in instructions
        allowed_stopwords = {
            ("what", "is", "the", "weather"),
            ("what", "time", "is", "it"),
            ("could", "you", "please", "tell"),
            ("i", "am", "thinking", "about"),
            ("if", "you", "have", "a"),
            ("do", "you", "know", "if"),
            ("can", "you", "search", "online"),
        }

        leaked_4grams = []
        for item in HELD_OUT_EVAL_DATA:
            q = item["query"]
            q_4grams = _extract_4grams(q)
            for gram in q_4grams:
                if gram not in allowed_stopwords and gram in self.corpus_4grams:
                    leaked_4grams.append((q, " ".join(gram)))

        self.assertEqual(
            leaked_4grams,
            [],
            f"LEAKAGE DETECTED: 4-grams from eval set found in system prompt/schemas: {leaked_4grams}",
        )


if __name__ == "__main__":
    unittest.main()

