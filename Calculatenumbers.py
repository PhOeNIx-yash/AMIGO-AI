"""
Local Math Calculation Module for Amigo Voice Assistant.
Uses safe AST arithmetic evaluation for instant numbers, and the offline Local AI model for natural language & advanced math. Zero API keys, zero hardcoding.
"""

import ast
import operator
import re
from local_llm import query_local_llm

_RE_ALPHA = re.compile(r"[a-zA-Z_]")

# Safe arithmetic operators supported for instant calculation
_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_safe_arithmetic(node):
    """Safely evaluates an AST arithmetic expression without using eval()."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    elif isinstance(node, ast.BinOp):
        left = _eval_safe_arithmetic(node.left)
        right = _eval_safe_arithmetic(node.right)
        op_type = type(node.op)
        if op_type in _OPERATORS:
            return _OPERATORS[op_type](left, right)
    elif isinstance(node, ast.UnaryOp):
        operand = _eval_safe_arithmetic(node.operand)
        op_type = type(node.op)
        if op_type in _OPERATORS:
            return _OPERATORS[op_type](operand)
    raise ValueError("Not a simple arithmetic expression")


# Normalization mapping for common spoken math terms to standard operators
_SPOKEN_MATH_MAP = [
    (re.compile(r"\b(?:what is|calculate|solve|evaluate|how much is|tell me)\b", re.I), ""),
    (re.compile(r"\bmultiplied\s+by\b", re.I), "*"),
    (re.compile(r"\bdivided\s+by\b", re.I), "/"),
    (re.compile(r"\bto\s+the\s+power\s+of\b", re.I), "**"),
    (re.compile(r"\bpower\s+of\b", re.I), "**"),
    (re.compile(r"\btimes\b", re.I), "*"),
    (re.compile(r"\binto\b", re.I), "*"),
    (re.compile(r"\bplus\b", re.I), "+"),
    (re.compile(r"\bminus\b", re.I), "-"),
    (re.compile(r"\bover\b", re.I), "/"),
    (re.compile(r"\bmod\b", re.I), "%"),
    (re.compile(r"\bx\b", re.I), "*"),
    (re.compile(r"\^"), "**"),
]


def Calc(query, speak=None):
    """
    Calculates math queries using safe AST arithmetic evaluation for instant expressions,
    and the local AI model only for complex word-based / advanced reasoning problems.
    Zero API keys, zero hardcoding.
    """
    term = str(query).strip().replace("Amigo", "").replace("amigo", "").strip()

    # Step 1 (Ponytail: Stdlib first): Normalize spoken operators to arithmetic symbols
    clean_expr = term
    for pattern, repl in _SPOKEN_MATH_MAP:
        clean_expr = pattern.sub(repl, clean_expr)
    clean_expr = clean_expr.strip()

    # Step 2: Try fast-path AST evaluation if no alphabetic characters remain
    try:
        if clean_expr and not _RE_ALPHA.search(clean_expr):
            parsed = ast.parse(clean_expr, mode="eval")
            res = _eval_safe_arithmetic(parsed.body)
            if isinstance(res, (int, float)):
                if isinstance(res, float) and res.is_integer():
                    res_str = str(int(res))
                elif isinstance(res, float):
                    res_str = f"{res:.4f}".rstrip("0").rstrip(".")
                else:
                    res_str = str(res)

                result_str = f"The result is {res_str}"
                print(result_str)
                if speak:
                    speak(result_str)
                return res_str
    except Exception:
        pass  # Not a simple arithmetic expression — fall through to LLM reasoning

    # Step 3: LLM Fallback (Only reached for true natural-language reasoning / word problems)
    prompt = f"Calculate the exact numerical result for the mathematical problem: {term}. Provide a direct, concise spoken answer with the final result."
    ai_answer = query_local_llm(prompt)

    if ai_answer:
        print(f"Calculation Result: {ai_answer}")
        if speak:
            speak(ai_answer)
        return ai_answer

    err = "The value is not answerable."
    if speak:
        speak(err)
    else:
        print(err)
    return err

