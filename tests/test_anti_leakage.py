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

    def test_no_hardcoding_guard(self):
        """
        Scans routing and execution files to detect any new intent keyword matches,
        phrase lists, set/tuple/list literals of natural-language words, regex on user query,
        or fixed default values not in guard_allowlist.json.
        """
        import ast
        import json
        allowlist_path = os.path.join(os.path.dirname(__file__), "guard_allowlist.json")
        self.assertTrue(os.path.exists(allowlist_path), "guard_allowlist.json must exist")
        with open(allowlist_path, encoding="utf-8") as f:
            allowlist = json.load(f)

        allowed_patterns = {e["pattern"].strip() for e in allowlist}

        files = [
            "amigo/utils/tool_registry.py",
            "amigo/core/llm_agent.py",
            "amigo/services/os_automation.py",
            "amigo/services/os_settings.py",
        ]

        guard_patterns = [
            (r're\.(?:search|match|findall)\([^)\n]*(?:query|clean|raw)', 'regex_on_query_or_text'),
            (r'\bin\s+(?:query|raw|clean)(?:\.lower\(\))?', 'keyword_in_query'),
            (r'params\.get\(\s*["\'][^"\']+["\']\s*,\s*(?!None\b|""|\'\'|\[\]|\{\}|0\b|False\b|True\b)[^)]+\)', 'params_get_default_val'),
        ]

        violations = []
        for fp in files:
            if not os.path.exists(fp):
                continue
            norm_fp = fp.replace("\\", "/")
            with open(fp, encoding="utf-8") as f:
                src = f.read()
            lines = src.splitlines()
            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for p, kind in guard_patterns:
                    if re.search(p, stripped):
                        if stripped not in allowed_patterns:
                            violations.append(f"{norm_fp}:{line_no} [{kind}] -> {stripped}")

            # AST scan for set/tuple/list literals of >=2 natural-language words
            tree = ast.parse(src, filename=fp)
            seen_lines = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
                    str_elts = [
                        elt.value for elt in node.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
                    word_elts = [
                        s for s in str_elts
                        if re.fullmatch(r'[a-z][a-z\s_\-]{0,30}', s) and len(s.strip()) >= 1
                    ]
                    if len(word_elts) >= 2 and len(word_elts) == len(node.elts):
                        ln = node.lineno
                        if ln not in seen_lines:
                            seen_lines.add(ln)
                            line_txt = lines[ln - 1].strip()
                            if line_txt.startswith('_tool(') or ('_p(' in line_txt and 'enum=' in line_txt) or '["message", "time"]' in line_txt or '["type", "topic"]' in line_txt:
                                continue
                            pat = f"AST:{type(node).__name__}:{sorted(word_elts)}"
                            if pat not in allowed_patterns:
                                violations.append(f"{norm_fp}:{ln} [word_container_literal] -> {pat}")

        self.assertEqual(
            violations,
            [],
            f"NO-HARDCODING GUARD VIOLATION: Found unapproved hardcoded intent, word-list, or default patterns:\n"
            + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()

