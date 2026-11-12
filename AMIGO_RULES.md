# Amigo Engineering Rules

## Core Principle
The LLM routes intents and extracts parameters from tool descriptions, JSON schemas, and conversation context—never via query regexes, keyword lists, or silent guesses.

## Hard Rules
- **No query matching or word lists:** Never add regexes, substring checks, or natural-language word-list literals in `amigo/`, `scripts/`, or `ui_app/src/` to guess intent or params.
- **Ask instead of guessing:** Missing or ambiguous targets (apps, files, settings, power actions) must trigger a short clarifying question via `chat`, never a silent default.
- **No fabricated actions:** Never claim an action executed in `chat` unless a tool actually ran.
- **UI relies on backend intent:** Frontend components (`ui_app/src/`) must use `intent` and structured metadata, never `prompt` text matching.
- **Safe tests:** Always mock OS side effects (power, lock, volume, brightness, recycle bin) in tests.

## Guard & Allowlist Process
- `tests/test_anti_leakage.py` guards against hardcoded query rules, word lists, and unapproved defaults.
- `tests/guard_allowlist.json` records only explicitly justified, behavior-neutral or documented defaults; never add entries to bypass a failure.

## File Rules
- Keep `tests/` limited to existing unit-test files, `held_out_eval_set.py`, and `guard_allowlist.json`.
- Never commit `scratch/`, `*.log`, or `*_log.txt`; write temporary outputs to system `$TEMP` and delete them afterwards.
- Use `tools/eval.py` (`gate`, `stress`, `latency`) for all evaluations.

## 5-Step Add-Feature Recipe
1. **Schema & description:** Add or update the tool schema and concise description in `amigo/utils/tool_registry.py`.
2. **Handler & validation:** Implement execution and conditional parameter validation; ask via `chat` when required info is missing.
3. **Structured metadata & UI:** Return structured `result_metadata` and handle `intent` cleanly in `ui_app/src/` without prompt regexes.
4. **Unit tests:** Add mocked tests to existing `tests/` files covering both valid execution and missing-param clarification.
5. **Verify:** Run `pytest -q` (including `tests/test_anti_leakage.py`) and `python tools/eval.py gate` before committing.
