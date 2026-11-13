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

        allowed_keys = set()
        for e in allowlist:
            self.assertNotIn("line", e, f"Allowlist entry must not contain line numbers: {e}")
            self.assertTrue(e.get("file") and e.get("function") and e.get("pattern") and e.get("reason"),
                            f"Allowlist entry must define file, function, pattern, and reason: {e}")
            allowed_keys.add((e["file"].replace("\\", "/"), e["function"].strip(), e["pattern"].strip()))

        files = [
            "amigo/utils/tool_registry.py",
            "amigo/core/llm_agent.py",
            "amigo/services/os_automation.py",
            "amigo/services/os_settings.py",
        ]

        guard_patterns = [
            (r're\.(?:search|match|findall)\([^)\n]*(?:query|clean|raw)', 'regex_on_query_or_text'),
            (r'(?<!for\s)\bin\s+(?:query|raw|clean)(?:\b|\.lower\(\))(?!\s*\.\s*(?:items|keys|values|get))', 'keyword_in_query'),
            (r'params\.get\(\s*["\'][^"\']+["\']\s*,(?!\s*(?:None\b|""|\'\'|\[\]|\{\}|0\b|False\b|True\b))\s*[^)]+\)', 'params_get_default_val'),
        ]

        violations = []
        for fp in files:
            if not os.path.exists(fp):
                continue
            norm_fp = fp.replace("\\", "/")
            with open(fp, encoding="utf-8") as f:
                src = f.read()
            lines = src.splitlines()
            tree = ast.parse(src, filename=fp)
            fn_map = {}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    end_ln = getattr(node, "end_lineno", node.lineno) or node.lineno
                    for ln in range(node.lineno, end_ln + 1):
                        fn_map[ln] = node.name

            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                fn_name = fn_map.get(line_no, "<module>")
                for p, kind in guard_patterns:
                    if re.search(p, stripped):
                        if (norm_fp, fn_name, stripped) not in allowed_keys:
                            violations.append(f"{norm_fp}:{line_no} ({fn_name}) [{kind}] -> {stripped}")

            # AST scan for set/tuple/list literals of >=2 natural-language words
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
                            if line_txt.startswith('_tool(') or '["message", "time"]' in line_txt or '["type", "topic"]' in line_txt:
                                continue
                            pat = f"AST:{type(node).__name__}:{sorted(word_elts)}"
                            fn_name = fn_map.get(ln, "<module>")
                            if (norm_fp, fn_name, pat) not in allowed_keys:
                                violations.append(f"{norm_fp}:{ln} ({fn_name}) [word_container_literal] -> {pat}")

        self.assertEqual(
            violations,
            [],
            f"NO-HARDCODING GUARD VIOLATION: Found unapproved hardcoded intent, word-list, or default patterns:\n"
            + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()

