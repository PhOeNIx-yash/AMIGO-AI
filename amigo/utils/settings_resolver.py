"""
Windows Settings URI Resolver for Amigo.
Loads settings pages and aliases dynamically from amigo/services/settings_catalog.json.
Zero hardcoded phrase tables or shell-injection calls in Python code.
"""

import difflib
import json
import os
import re
import subprocess

_CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "services",
    "settings_catalog.json",
)

_CATALOG_CACHE: dict | None = None
_SETTINGS_TABLE: list[tuple[str, str]] = []
_ALIASES: dict[str, tuple[str, str]] = {}
_KEYWORD_MAP: dict[str, tuple[str, str]] = {}


def _ensure_catalog_loaded() -> None:
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

    _SETTINGS_TABLE = [(str(item[0]), str(item[1])) for item in raw_table if len(item) >= 2]
    _ALIASES = {str(k).lower().strip(): (str(v[0]), str(v[1])) for k, v in raw_aliases.items() if len(v) >= 2}

    _KEYWORD_MAP = dict(_ALIASES)
    for label, uri in _SETTINGS_TABLE:
        _KEYWORD_MAP.setdefault(label.lower(), (label, uri))
        for word in re.findall(r"[^\W_]+", label.lower(), flags=re.UNICODE):
            if word and word not in _KEYWORD_MAP:
                _KEYWORD_MAP[word] = (label, uri)


_ensure_catalog_loaded()


def resolve_setting(setting: str) -> tuple[str, str]:
    """
    Resolve a settings target string to (display_name, ms-settings URI)
    using purely data-driven lookup + fuzzy matching against settings_catalog.json.
    """
    _ensure_catalog_loaded()
    s = (setting or "").lower().strip()
    if not s:
        return "Windows", "ms-settings:"

    if s.startswith("ms-settings:"):
        return s, s

    # 1. Exact phrase / alias match from catalog
    if s in _KEYWORD_MAP:
        return _KEYWORD_MAP[s]

    # 2. Longest token match across catalog aliases & labels
    tokens = [t for t in re.findall(r"[^\W_]+", s, flags=re.UNICODE) if t]
    best_label, best_uri, best_score = "Windows", "ms-settings:", 0
    for tok in tokens:
        if tok in _KEYWORD_MAP:
            label, uri = _KEYWORD_MAP[tok]
            if len(tok) > best_score:
                best_label, best_uri, best_score = label, uri, len(tok)

    if best_score > 0:
        return best_label, best_uri

    # 3. Fuzzy match against catalog keys
    candidates = list(_KEYWORD_MAP.keys())
    close = difflib.get_close_matches(s, candidates, n=1, cutoff=0.78)
    if close:
        return _KEYWORD_MAP[close[0]]

    for tok in tokens:
        if len(tok) >= 4:
            close_tok = difflib.get_close_matches(tok, candidates, n=1, cutoff=0.82)
            if close_tok:
                return _KEYWORD_MAP[close_tok[0]]

    return "Windows", "ms-settings:"


def open_setting(setting: str) -> str:
    """Resolve, safely launch via Windows shell URI handler, and return confirmation."""
    label, uri = resolve_setting(setting)
    try:
        os.startfile(uri)
    except AttributeError:
        subprocess.Popen(["explorer.exe", uri])
    from amigo.core.llm_agent import get_quick_feedback
    return get_quick_feedback(f"opened {label} settings")