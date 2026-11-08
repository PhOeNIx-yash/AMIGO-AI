"""
Windows Settings URI Resolver for Amigo.
Loads settings pages and aliases dynamically from amigo/services/settings_catalog.json.
Zero hardcoded tables in Python code.
"""

import json
import os
import random
import re

_CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "services",
    "settings_catalog.json",
)

_CATALOG_CACHE: dict | None = None
_SETTINGS_TABLE: list[tuple[str, str]] = []
_ALIASES: dict[str, tuple[str, str]] = {}
_KEYWORD_MAP: dict[str, tuple[str, str]] = {}


def _ensure_catalog_loaded():
    global _CATALOG_CACHE, _SETTINGS_TABLE, _ALIASES, _KEYWORD_MAP
    if _CATALOG_CACHE is not None:
        return

    try:
        with open(_CATALOG_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        data = {}

    _CATALOG_CACHE = data
    pages_data = data.get("settings_pages", {})
    raw_table = pages_data.get("table", [])
    raw_aliases = pages_data.get("aliases", {})

    _SETTINGS_TABLE = [(item[0], item[1]) for item in raw_table]
    _ALIASES = {k.lower(): (v[0], v[1]) for k, v in raw_aliases.items()}

    _KEYWORD_MAP = dict(_ALIASES)
    for label, uri in _SETTINGS_TABLE:
        for word in re.split(r"[\s]+", label.lower()):
            word = word.strip("&-/")
            if word and word not in _KEYWORD_MAP:
                _KEYWORD_MAP[word] = (label, uri)


_ensure_catalog_loaded()


def resolve_setting(setting: str) -> tuple[str, str]:
    """
    Resolve a natural-language settings string to (display_name, ms-settings URI).
    Dynamic lookup against settings_catalog.json. Zero hardcoded if/elif chains.
    """
    _ensure_catalog_loaded()
    s = (setting or "").lower().strip()

    # Empty or generic inquiry -> fallback to default Windows Settings landing page
    if not s or s in ("settings", "setting", "windows settings", "windows setting", "any setting"):
        return "Windows", "ms-settings:"

    if s.startswith("random"):
        if _SETTINGS_TABLE:
            label, uri = random.choice(_SETTINGS_TABLE[:15])
            return label, uri
        return "Windows", "ms-settings:"

    # 1. Full-phrase alias check first (multi-word e.g. "dark mode", "windows update")
    if s in _KEYWORD_MAP:
        return _KEYWORD_MAP[s]

    # 2. Word-by-word longest-score match
    words = re.split(r"[\s\-/&]+", s)
    best_label, best_uri, best_score = "Windows", "ms-settings:", 0
    for word in words:
        word = word.strip()
        if not word:
            continue
        if word in _KEYWORD_MAP:
            label, uri = _KEYWORD_MAP[word]
            score = len(word)
            if score > best_score:
                best_label, best_uri, best_score = label, uri, score

    if best_score > 0:
        return best_label, best_uri

    # 3. Token-bounded fallback on full phrase
    query_tokens = set(re.findall(r"\b[a-z0-9]+\b", s))
    for label, uri in _SETTINGS_TABLE:
        lbl = label.lower()
        lbl_tokens = re.findall(r"\b[a-z0-9]+\b", lbl)
        if not lbl_tokens:
            continue
        if len(lbl_tokens) > 1 and re.search(r"\b" + re.escape(lbl) + r"\b", s):
            return label, uri
        if any(w in query_tokens for w in lbl_tokens if len(w) >= 3):
            return label, uri

    # 4. Alias word-bounded fallback
    for alias, (label, uri) in _ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", s):
            return label, uri

    return "Windows", "ms-settings:"


def open_setting(setting: str) -> str:
    """Resolve, launch, and return a spoken confirmation string."""
    label, uri = resolve_setting(setting)
    os.system(f"start {uri}")
    from amigo.core.llm_agent import get_quick_feedback
    return get_quick_feedback(f"opened {label} settings")
