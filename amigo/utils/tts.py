"""
Unified Speech Engine Module for Amigo Voice Assistant.
Handles both Offline Neural Text-to-Speech (TTS via Supertonic 3)
and Offline Neural Speech-to-Text (STT via Vosk Neural Recognizer).
"""

import io
import json
import logging
import os
import queue
import re
import tarfile
import threading
import time
import urllib.request
import numpy as np

# Limit ONNX Runtime / OpenMP threads to 2 so TTS does not saturate all CPU cores
# and compete for CPU with llama.cpp LLM generation.
if "OMP_NUM_THREADS" not in os.environ:
    os.environ["OMP_NUM_THREADS"] = "2"
if "ORT_INTRA_OP_NUM_THREADS" not in os.environ:
    os.environ["ORT_INTRA_OP_NUM_THREADS"] = "2"

try:
    import num2words
except ImportError:
    num2words = None

logger = logging.getLogger("amigo.speech")

_supertonic_instance = None
_USE_SUPERTONIC = False

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_MODELS_ROOT = os.path.join(_PROJECT_ROOT, "models")

try:
    import sounddevice as _sd
    from supertonic import TTS as _SupertonicTTS

    _USE_SUPERTONIC = True
    logger.info("[TTS] Supertonic-3 Neural Engine available (44.1kHz).")
except ImportError:
    _USE_SUPERTONIC = False
    logger.warning("[TTS] supertonic package not available.")

# ── 10 Curated Best Offline Studio Neural Voices (Supertonic 3 - 44.1kHz Studio) ────────
CURATED_VOICES = {
    # 5 Best Female Neural Voices (Supertonic 3)
    "nova":    {"engine": "supertonic", "id": "F1", "name": "Nova",    "gender": "Female", "accent": "US", "desc": "Smooth, articulate & studio-clean neural female (Recommended)"},
    "aria":    {"engine": "supertonic", "id": "F2", "name": "Aria",    "gender": "Female", "accent": "US", "desc": "Soft, natural & warm conversational female"},
    "serena":  {"engine": "supertonic", "id": "F3", "name": "Serena",  "gender": "Female", "accent": "US", "desc": "Warm, expressive and friendly conversational tone"},
    "chloe":   {"engine": "supertonic", "id": "F4", "name": "Chloe",   "gender": "Female", "accent": "US", "desc": "Bright, friendly & clear female delivery"},
    "luna":    {"engine": "supertonic", "id": "F5", "name": "Luna",    "gender": "Female", "accent": "US", "desc": "Energetic, crisp and articulate female delivery"},

    # 5 Best Male Neural Voices (Supertonic 3)
    "orion":   {"engine": "supertonic", "id": "M1", "name": "Orion",   "gender": "Male",   "accent": "US", "desc": "Deep, calm and grounded baritone resonance"},
    "atlas":   {"engine": "supertonic", "id": "M2", "name": "Atlas",   "gender": "Male",   "accent": "US", "desc": "Professional, crisp and articulate executive tone"},
    "leo":     {"engine": "supertonic", "id": "M3", "name": "Leo",     "gender": "Male",   "accent": "US", "desc": "Warm, relatable and conversational companion"},
    "felix":   {"engine": "supertonic", "id": "M4", "name": "Felix",   "gender": "Male",   "accent": "US", "desc": "Young, natural, and modern male delivery"},
    "ethan":   {"engine": "supertonic", "id": "M5", "name": "Ethan",   "gender": "Male",   "accent": "US", "desc": "Distinguished, articulate gentleman delivery"},

    # Direct F1-F5 & M1-M5 IDs
    "f1":      {"engine": "supertonic", "id": "F1", "name": "Nova",    "gender": "Female", "accent": "US", "desc": "Smooth, articulate & studio-clean female"},
    "f2":      {"engine": "supertonic", "id": "F2", "name": "Aria",    "gender": "Female", "accent": "US", "desc": "Soft, natural & warm female"},
    "f3":      {"engine": "supertonic", "id": "F3", "name": "Serena",  "gender": "Female", "accent": "US", "desc": "Warm, expressive and natural female"},
    "f4":      {"engine": "supertonic", "id": "F4", "name": "Chloe",   "gender": "Female", "accent": "US", "desc": "Bright, friendly & clear female"},
    "f5":      {"engine": "supertonic", "id": "F5", "name": "Luna",    "gender": "Female", "accent": "US", "desc": "Energetic and crisp female"},
    "m1":      {"engine": "supertonic", "id": "M1", "name": "Orion",   "gender": "Male",   "accent": "US", "desc": "Deep calm baritone male"},
    "m2":      {"engine": "supertonic", "id": "M2", "name": "Atlas",   "gender": "Male",   "accent": "US", "desc": "Professional executive male"},
    "m3":      {"engine": "supertonic", "id": "M3", "name": "Leo",     "gender": "Male",   "accent": "US", "desc": "Warm & conversational male"},
    "m4":      {"engine": "supertonic", "id": "M4", "name": "Felix",   "gender": "Male",   "accent": "US", "desc": "Young & natural modern male"},
    "m5":      {"engine": "supertonic", "id": "M5", "name": "Ethan",   "gender": "Male",   "accent": "US", "desc": "Distinguished gentleman male"},

    # Seamless backward compatibility with legacy voice names
    "nicole":  {"engine": "supertonic", "id": "F1", "name": "Nova",    "gender": "Female", "accent": "US", "desc": "Studio-clean neural female"},
    "sarah":   {"engine": "supertonic", "id": "F2", "name": "Aria",    "gender": "Female", "accent": "US", "desc": "Warm conversational female"},
    "heart":   {"engine": "supertonic", "id": "F3", "name": "Serena",  "gender": "Female", "accent": "US", "desc": "Warm & expressive female"},
    "sky":     {"engine": "supertonic", "id": "F4", "name": "Chloe",   "gender": "Female", "accent": "US", "desc": "Bright & clear female"},
    "bella":   {"engine": "supertonic", "id": "F5", "name": "Luna",    "gender": "Female", "accent": "US", "desc": "Energetic & crisp female"},
    "adam":    {"engine": "supertonic", "id": "M1", "name": "Orion",   "gender": "Male",   "accent": "US", "desc": "Deep calm baritone male"},
    "michael": {"engine": "supertonic", "id": "M2", "name": "Atlas",   "gender": "Male",   "accent": "US", "desc": "Professional executive male"},
    "echo":    {"engine": "supertonic", "id": "M3", "name": "Leo",     "gender": "Male",   "accent": "US", "desc": "Warm & conversational male"},
    "liam":    {"engine": "supertonic", "id": "M4", "name": "Felix",   "gender": "Male",   "accent": "US", "desc": "Young & natural male"},
    "george":  {"engine": "supertonic", "id": "M5", "name": "Ethan",   "gender": "Male",   "accent": "US", "desc": "Distinguished gentleman"},
}

_PROFILE_PATH = os.path.join(_PROJECT_ROOT, "amigo_profile.json")
if not os.path.exists(_PROFILE_PATH):
    _PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amigo_profile.json")

ACTIVE_VOICE = "chloe"
SUPERTONIC_VOICE = "F4"
SUPERTONIC_SPEED = 1.05
SUPERTONIC_STEPS = 5  # 5 steps gives 35-40% faster latency with identical neural fidelity
SUPERTONIC_SILENCE_DURATION = 0.04
SUPERTONIC_LANG = "na"  # "na" enables universal multilingual support (English, Spanish, French, German, Hindi, etc.)

_tts_lock = threading.Lock()
_supertonic_init_lock = threading.Lock()
_active_stream = None
_active_stream_lock = threading.Lock()
_style_cache = {}
_profile_mtime_tts = 0.0


def sync_voice_from_profile() -> str:
    """Synchronizes active voice selection from amigo_profile.json, caching disk reads by mtime."""
    global ACTIVE_VOICE, SUPERTONIC_VOICE, SUPERTONIC_LANG, _profile_mtime_tts
    try:
        if os.path.exists(_PROFILE_PATH):
            mtime = os.path.getmtime(_PROFILE_PATH)
            if mtime > _profile_mtime_tts:
                _profile_mtime_tts = mtime
                with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                v = (
                    data.get("preferences", {}).get("voice")
                    or data.get("ui_settings", {}).get("voice")
                    or data.get("user_profile", {}).get("preferences", {}).get("voice")
                )
                if v and isinstance(v, str):
                    v_key = v.strip().lower()
                    if v_key in CURATED_VOICES:
                        ACTIVE_VOICE = v_key
                        info = CURATED_VOICES[v_key]
                        SUPERTONIC_VOICE = info["id"]
                        SUPERTONIC_LANG = "na"
                        return v_key
    except Exception as e:
        logger.debug(f"[TTS] Voice profile sync note: {e}")
    return ACTIVE_VOICE


# Initialize active voice from user preference immediately on startup
sync_voice_from_profile()


def set_voice(voice_name: str) -> str:
    """Sets the active voice, updates engine settings, and immediately persists to user profile atomically."""
    global ACTIVE_VOICE, SUPERTONIC_VOICE, SUPERTONIC_LANG, _profile_mtime_tts
    key = voice_name.lower().strip()
    display_name = voice_name
    if key in CURATED_VOICES:
        ACTIVE_VOICE = key
        info = CURATED_VOICES[key]
        display_name = info["name"]
        SUPERTONIC_VOICE = info["id"]
        SUPERTONIC_LANG = "na"
        logger.info(f"[TTS] Active voice switched to: {info['name']} (Supertonic-3 {info['id']})")
    elif voice_name.upper() in ("F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"):
        SUPERTONIC_VOICE = voice_name.upper()
        ACTIVE_VOICE = voice_name.lower()
        display_name = voice_name.upper()
    else:
        SUPERTONIC_VOICE = "F1"
        ACTIVE_VOICE = voice_name
        display_name = voice_name

    # Persist to profile atomically so voice selection is remembered across restarts
    try:
        pdata = {}
        if os.path.exists(_PROFILE_PATH):
            with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
                pdata = json.load(f)
        pdata.setdefault("preferences", {})["voice"] = key
        pdata.setdefault("ui_settings", {})["voice"] = key
        pdata.setdefault("user_profile", {}).setdefault("preferences", {})["voice"] = key

        tmp_file = _PROFILE_PATH + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(pdata, f, indent=2)
        os.replace(tmp_file, _PROFILE_PATH)
        _profile_mtime_tts = os.path.getmtime(_PROFILE_PATH)

        # Notify and update rag_engine's in-memory profile cache directly if loaded
        try:
            from amigo.core import rag_engine
            if hasattr(rag_engine, "_profile_cache") and rag_engine._profile_cache is not None:
                rag_engine._profile_cache.setdefault("preferences", {})["voice"] = key
                rag_engine._profile_cache.setdefault("ui_settings", {})["voice"] = key
                rag_engine._profile_cache.setdefault("user_profile", {}).setdefault("preferences", {})["voice"] = key
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"[TTS] Voice persistence note: {e}")

    return display_name


def get_voice_catalog() -> dict:
    """Returns dictionary of all curated voices with metadata."""
    return CURATED_VOICES


def get_active_voice() -> str:
    """Returns the current active voice identifier."""
    return ACTIVE_VOICE


# Callbacks for UI updates
_on_state_change = None
_on_broadcast = None


def set_tts_callbacks(state_cb=None, broadcast_cb=None):
    """Register state change and broadcast event callbacks."""
    global _on_state_change, _on_broadcast
    if state_cb:
        _on_state_change = state_cb
    if broadcast_cb:
        _on_broadcast = broadcast_cb


def get_tts_engine_name() -> str:
    voice_info = CURATED_VOICES.get(ACTIVE_VOICE)
    vname = voice_info["name"] if voice_info else SUPERTONIC_VOICE
    if _USE_SUPERTONIC:
        return f"Supertonic-3 44.1kHz ({vname})"
    return f"Supertonic-3 ({vname})"


def _get_supertonic():
    """Lazy-load Supertonic-3 TTS model - thread-safe."""
    global _supertonic_instance
    if _supertonic_instance is None:
        with _supertonic_init_lock:
            if _supertonic_instance is None:
                logger.info("[TTS] Initializing Supertonic-3 Neural Engine...")
                _supertonic_instance = _SupertonicTTS(model="supertonic-3", auto_download=True)
                logger.info("[TTS] Supertonic-3 ready (44.1kHz).")
    return _supertonic_instance


def _get_expressive_voice_style(engine, voice_id: str):
    """
    Retrieves and conditions an expressively contoured voice style vector:
    - Injects subtle prosodic warmth and dynamic pitch inflection into each voice profile.
    - Caches the resulting Style vectors in memory for instant 0ms reuse.
    """
    global _style_cache
    vid = voice_id.upper().strip()
    if vid in _style_cache:
        return _style_cache[vid]

    try:
        from supertonic import Style
        base_style = engine.get_voice_style(vid)
        # Prosodic dynamic enhancement:
        # Female voices (F1, F2, F4, F5) blend with F3 (Serena's rich conversational prosody)
        # Male voices (M1, M2, M4, M5) blend with M3 (Leo's warm expressive companion tone)
        if vid in ("F1", "F2", "F4", "F5"):
            if vid == "F3":
                styled = base_style
            else:
                f3_style = engine.get_voice_style("F3")
                ratio = 0.28 if vid == "F1" else 0.24
                blend_ttl = (1.0 - ratio) * base_style.ttl + ratio * f3_style.ttl
                blend_dp = (1.0 - ratio) * base_style.dp + ratio * f3_style.dp
                styled = Style(blend_ttl, blend_dp)
        elif vid in ("M1", "M2", "M4", "M5"):
            if vid == "M3":
                styled = base_style
            else:
                m3_style = engine.get_voice_style("M3")
                ratio = 0.28 if vid == "M1" else 0.24
                blend_ttl = (1.0 - ratio) * base_style.ttl + ratio * m3_style.ttl
                blend_dp = (1.0 - ratio) * base_style.dp + ratio * m3_style.dp
                styled = Style(blend_ttl, blend_dp)
        else:
            styled = base_style

        _style_cache[vid] = styled
        return styled
    except Exception as e:
        logger.debug(f"[TTS Style note] {e}")
        return engine.get_voice_style(vid)


# Pre-compiled regex patterns for zero overhead text sanitization & splitting
_RE_URL = re.compile(r'https?://\S+|www\.\S+')
_RE_SPECIAL_CHARS = re.compile(r'[*#_`~>\[\]()]')
_RE_CONSECUTIVE_DOTS = re.compile(r'\.{2,}')
_RE_WHITESPACE = re.compile(r'\s+')
_RE_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')


def _warmup_tts():
    """Warms up Supertonic-3 ONNX in a background thread so initial speech is instant."""
    def _warm():
        try:
            if _USE_SUPERTONIC:
                engine = _get_supertonic()
                style = _get_expressive_voice_style(engine, "F1")
                with _tts_lock:
                    engine.synthesize(
                        "Amigo ready.",
                        voice_style=style,
                        total_steps=SUPERTONIC_STEPS,
                        speed=SUPERTONIC_SPEED,
                        silence_duration=SUPERTONIC_SILENCE_DURATION,
                        lang="na",
                    )
                logger.info("[TTS] Supertonic-3 inference warmed up.")
        except Exception as e:
            logger.debug(f"[TTS Warmup] {e}")

    threading.Thread(target=_warm, daemon=True, name="TTS-Warmup").start()


_warmup_tts()


# ── Asynchronous speech pipeline queues & generation ──────────────────────
_speech_queue = queue.Queue()
_playback_queue = queue.Queue(maxsize=32)
_speech_generation = 0
_speech_generation_lock = threading.Lock()
_state_lock = threading.Lock()
_is_speaking_state = False


def _speech_is_current(generation: int) -> bool:
    with _speech_generation_lock:
        return generation == _speech_generation


def _update_state(new_state: str):
    """Updates assistant state and triggers UI callbacks only on genuine state changes."""
    global _is_speaking_state
    with _state_lock:
        if new_state == "speaking":
            if not _is_speaking_state:
                _is_speaking_state = True
                if _on_state_change:
                    _on_state_change("speaking")
        elif new_state == "idle":
            if _is_speaking_state:
                _is_speaking_state = False
                if _on_state_change:
                    _on_state_change("idle")


def normalize_text_for_tts(text: str) -> str:
    """
    Normalizes raw text into clean, phonetically expressive speech:
    - Preserves hyphenated words (well-known, state-of-the-art) while converting ranges (3-1 -> 3 to 1)
    - Decimals read accurately (23.5 -> twenty-three point five)
    - Long digit sequences (phone numbers, OTPs, codes) read digit-by-digit
    - Large formatted numbers (1,234,567) converted cleanly to cardinal words
    - Affirmation openers only accented when followed by punctuation, preserving phrases (Done correctly)
    - 4-digit years spoken as years only when preceded by year indicators (in, since, year, dated)
    - Acronyms spelled out without mangling common words (NASA, CHAT, OPEN, PLAY, STOP)
    - Zero content truncation
    """
    if not text:
        return ""
    t = text

    # 1. Markdown tables to natural conversational speech
    t = re.sub(r'\|\s*\|+', '|\n|', t)
    t = re.sub(r'(?:^|\n)\s*\|?[\s\-:|]{3,}\|[\s\-:|]*(?=\n|$)', '\n', t)
    lines = t.split('\n')
    parsed_lines = []
    headers = []
    for line in lines:
        if '|' in line:
            pipe_pos = line.find('|')
            prefix = line[:pipe_pos].strip()
            table_part = line[pipe_pos:]
            if prefix:
                for p in re.split(r'#{1,6}\s*', prefix):
                    if p.strip():
                        parsed_lines.append(p.strip() + '.')
            cells = [c.strip() for c in table_part.split('|') if c.strip()]
            if not cells:
                continue
            if not headers:
                headers = cells
            else:
                subject = cells[0]
                details = []
                for idx, cell in enumerate(cells[1:], 1):
                    col_name = headers[idx] if idx < len(headers) else ''
                    if col_name and not col_name.lower().startswith('col'):
                        details.append(f"{col_name}: {cell}")
                    else:
                        details.append(cell)
                row_str = f"{subject}: {', '.join(details)}." if details else f"{subject}."
                parsed_lines.append(row_str)
        else:
            parsed_lines.append(line)
    t = '\n'.join(parsed_lines)

    # 2. Remove URLs
    t = _RE_URL.sub('', t)

    # 3. Markdown headers (# Header -> Header.)
    t = re.sub(r'^(?:#{1,6}\s+)(.+)$', r'\1.', t, flags=re.MULTILINE)
    t = re.sub(r'#{1,6}\s*', '', t)

    # 4. Markdown bullet points (* item / - item / • item -> , item)
    t = re.sub(r'(?:^|\n)\s*[-*•]\s+', r', ', t)

    # 5. Numbered lists (1. item \n 2. item -> First, item. Second, item.)
    ord_map = {1: 'First', 2: 'Second', 3: 'Third', 4: 'Fourth', 5: 'Fifth'}
    def _list_repl(m):
        num = int(m.group(1))
        return f"{ord_map.get(num, f'Number {num}')}, "
    t = re.sub(r'(?:^|\n)\s*(\d+)\.\s+', _list_repl, t)

    # 6. Affirmation openers: ONLY when followed by punctuation (.,) so phrases like "Done correctly" or "Great news" are not mangled
    t = re.sub(r'^(Sure|Certainly|Great|Awesome|Done|Got it|Alright|Of course|Perfect|Understood|Absolutely)[,.](?:\s+|$)', r'\1! ', t, flags=re.IGNORECASE)
    t = re.sub(r'(?<=[.!?]\s)(Sure|Certainly|Great|Awesome|Done|Got it|Alright|Of course|Perfect|Understood|Absolutely)[,.](?:\s+|$)', r'\1! ', t, flags=re.IGNORECASE)

    # 7. Number ranges & scores BEFORE hyphens are touched (3-1 -> 3 to 1, 2024-2025 -> 2024 to 2025)
    t = re.sub(r'(\d+)\s*[-–—]\s*(\d+)', r'\1 to \2', t)

    # 8. Phone numbers (e.g. +1-800-555-0199 or 98765 43210 or 123-456-7890) -> digit by digit
    def _phone_repl(m):
        cleaned = re.sub(r'\D', '', m.group(0))
        return " ".join(num2words.num2words(int(d)) if num2words else d for d in cleaned)
    t = re.sub(r'\b(?:\+?\d{1,3}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b', _phone_repl, t)

    # 9. Explicit OTP / PIN / Code / PNR patterns (e.g. OTP: 583921 or code is 123456)
    def _otp_repl(m):
        prefix = m.group(1)
        digits = m.group(2)
        dec = " ".join(num2words.num2words(int(d)) if num2words else d for d in digits)
        return f"{prefix}{dec}"
    t = re.sub(r'\b((?:otp|code|pin|verification|pnr|id|password|flight)\s*(?:is|:)?\s*)(\d{4,8})\b', _otp_repl, t, flags=re.IGNORECASE)

    # 10. Formatted large numbers with commas: e.g. 1,234,567 -> convert to cardinal words directly
    if num2words is not None:
        def _comma_num_repl(m):
            raw = m.group(0).replace(",", "")
            try:
                return num2words.num2words(int(raw))
            except Exception:
                return raw
        t = re.sub(r'\b\d{1,3}(?:,\d{3})+\b', _comma_num_repl, t)

    # 11. Decimals: 23.5 -> twenty-three point five
    def _dec_repl(m):
        integer_part = m.group(1)
        decimal_digits = m.group(2)
        int_str = num2words.num2words(int(integer_part)) if num2words else integer_part
        dec_str = " ".join(num2words.num2words(int(d)) if num2words else d for d in decimal_digits)
        return f"{int_str} point {dec_str}"
    t = re.sub(r'\b(\d+)\.(\d+)\b', _dec_repl, t)

    # 12. Standalone 7+ unformatted digit strings -> digit by digit
    def _long_digits_repl(m):
        digits = m.group(0)
        return " ".join(num2words.num2words(int(d)) if num2words else d for d in digits)
    t = re.sub(r'\b\d{7,}\b', _long_digits_repl, t)

    # 13. Standalone em-dashes and spaced hyphens -> comma pause, while PRESERVING intra-word hyphens (well-known)
    t = re.sub(r'(?<=\s)[-–—]+(?=\s)', ', ', t)
    t = re.sub(r'[–—]', ', ', t)

    # 14. Colons at the end of clauses -> natural comma pause
    t = re.sub(r':(?=\s|$)', r', ', t)

    # 15. Parentheses: (extra info) -> , extra info,
    t = re.sub(r'\(([^)]+)\)', r', \1, ', t)

    # 16. Semicolons -> comma pause
    t = re.sub(r';\s*', r', ', t)

    # 17. Strip remaining markdown formatting characters
    t = _RE_SPECIAL_CHARS.sub(' ', t)
    t = re.sub(r'[/|]', ' ', t)

    # 18. Currency and percentages
    t = re.sub(r'\$(\d+(?:\.\d{2})?)', r'\1 dollars', t)
    t = re.sub(r'(\d+)%', r'\1 percent', t)

    if num2words is not None:
        # Dates: e.g. September 6, 2026
        def _date_repl(m):
            month = m.group(1)
            day = int(m.group(2))
            year = int(m.group(3))
            try:
                day_str = num2words.num2words(day, to='ordinal')
                year_str = num2words.num2words(year, to='year')
                return f"{month} {day_str}, {year_str}"
            except Exception:
                return m.group(0)

        t = re.sub(
            r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?,\s+(\d{4})\b',
            _date_repl,
            t,
            flags=re.I,
        )

        # Ordinals (1st, 2nd, 3rd, 4th, 21st)
        def _ord_repl(m):
            try:
                return num2words.num2words(int(m.group(1)), to='ordinal')
            except Exception:
                return m.group(0)

        t = re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', _ord_repl, t, flags=re.I)

        # Four-digit years: only convert to "year" when preceded by year indicators
        def _year_repl(m):
            prefix = m.group(1)
            year = int(m.group(2))
            try:
                return f"{prefix}{num2words.num2words(year, to='year')}"
            except Exception:
                return m.group(0)

        t = re.sub(r'\b(in |since |year |dated |from |circa )(19\d\d|20\d\d)\b', _year_repl, t, flags=re.I)

        # Other standalone numbers (cardinals)
        def _num_repl(m):
            try:
                return num2words.num2words(int(m.group(1)))
            except Exception:
                return m.group(0)

        t = re.sub(r'\b(\d+)\b', _num_repl, t)

    # 19. Acronyms vs common pronounceable capitalized words
    PRONOUNCEABLE = {'NASA', 'CHAT', 'OPEN', 'PLAY', 'HELP', 'STOP', 'DONE', 'ASAP', 'PIN', 'STEM', 'NATO', 'AIDS', 'RAM', 'ROM', 'LASER', 'MUTE', 'NEXT', 'QUIT', 'EXIT'}
    ACRONYMS = {'CPU', 'GPU', 'API', 'USB', 'URL', 'TTS', 'STT', 'HTML', 'JSON', 'SDK', 'LLM', 'AI', 'UI', 'OS', 'IP', 'ID', 'PC', 'OCR', 'VAD', 'RMS', 'SNR', 'DAC'}

    def _acronym_repl(m):
        w = m.group(0)
        if w in ACRONYMS:
            return '. '.join(list(w)) + '.'
        if w in PRONOUNCEABLE:
            return w.lower()
        if len(w) <= 3:
            return '. '.join(list(w)) + '.'
        return w.capitalize()

    t = re.sub(r'\b[A-Z]{2,5}\b', _acronym_repl, t)

    # 20. Clean consecutive punctuation and whitespace
    t = _RE_CONSECUTIVE_DOTS.sub(', ', t)
    t = re.sub(r'!{2,}', '!', t)
    t = re.sub(r'\?{2,}', '?', t)
    t = re.sub(r'[,]{2,}', ',', t)
    t = re.sub(r'\s+,', ',', t)
    t = re.sub(r'\s+!', '!', t)
    t = re.sub(r'\s+\?', '?', t)
    return _RE_WHITESPACE.sub(' ', t).strip()


def _clean_tts_text(text: str) -> str:
    """Cleans and normalizes text for natural neural TTS prosody without redundant per-char loops."""
    norm = normalize_text_for_tts(text)
    if _USE_SUPERTONIC:
        try:
            engine = _get_supertonic()
            valid_set = getattr(engine.model.text_processor, "supported_character_set", None)
            if valid_set:
                # Fast single-pass filter only if text contains characters outside valid_set
                text_chars = set(norm)
                unsupported = text_chars - valid_set
                if unsupported:
                    trans_table = {ord(c): " " for c in unsupported}
                    norm = norm.translate(trans_table)
                    norm = _RE_WHITESPACE.sub(" ", norm).strip()
        except Exception:
            pass
    return norm


def _compact_tts_text(text: str) -> str:
    """Cleans text for neural synthesis without truncating content."""
    return _clean_tts_text(text)


def _process_audio_clarity(samples, sr=44100, is_final_chunk: bool = False) -> tuple[np.ndarray, int]:
    """
    Studio audio mastering & anti-artifact pipeline:
    - DC offset removal & 45Hz subsonic filtering to eliminate low-end thump and drift.
    - Broadcast studio presence EQ (+0.8 dB @ 260Hz warmth, +1.2 dB @ 4.8kHz clarity & air).
    - Micro-fade in/out (2ms) for continuous intra-sentence chunks to avoid clicks without pauses.
    - Tail trimming, 20ms fade-out, and 25ms cushion applied only to the final chunk.
    - Soft peak limiter: only downscales if peak > 0.95, preventing inter-chunk loudness jumps.
    """
    if samples is None or len(samples) == 0:
        return samples, int(sr)
    samples = np.asarray(samples, dtype=np.float32)

    # 1. Resample only if non-standard
    target_sr = int(sr)
    if sr not in (44100, 24000, 48000, 16000) and len(samples) > 0:
        target_sr = 44100
        try:
            import scipy.signal
            from math import gcd
            g = gcd(int(sr), target_sr)
            samples = scipy.signal.resample_poly(samples, target_sr // g, int(sr) // g).astype(np.float32)
            sr = target_sr
        except Exception:
            pass

    # 2. DC-bias removal & 45Hz subsonic high-pass cleanup
    samples = samples - np.mean(samples)
    try:
        import scipy.signal
        sos = scipy.signal.butter(2, 45, btype="high", fs=sr, output="sos")
        samples = scipy.signal.sosfilt(sos, samples).astype(np.float32)
    except Exception:
        pass

    # 3. Broadcast studio presence EQ (+0.8 dB @ 260Hz warmth, +1.2 dB @ 4.8kHz clarity & air)
    try:
        import scipy.signal
        def _peaking_sos(fc, gain_db, q, fs):
            A = 10.0 ** (gain_db / 40.0)
            w0 = 2.0 * np.pi * fc / fs
            alpha = np.sin(w0) / (2.0 * q)
            b0 = 1.0 + alpha * A
            b1 = -2.0 * np.cos(w0)
            b2 = 1.0 - alpha * A
            a0 = 1.0 + alpha / A
            a1 = -2.0 * np.cos(w0)
            a2 = 1.0 - alpha / A
            return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]], dtype=np.float32)

        sos_warm = _peaking_sos(260.0, 0.8, 0.85, target_sr)
        sos_pres = _peaking_sos(4800.0, 1.2, 0.9, target_sr)
        samples = scipy.signal.sosfilt(sos_warm, samples).astype(np.float32)
        samples = scipy.signal.sosfilt(sos_pres, samples).astype(np.float32)
    except Exception:
        pass

    # 4. Micro-fade in (2ms) to guarantee clean click-free onset
    fade_in_len = min(int(0.002 * sr), len(samples))
    if fade_in_len > 0:
        ramp_in = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, fade_in_len, dtype=np.float32)))
        samples[:fade_in_len] *= ramp_in

    # 5. Intra-chunk vs Final-chunk boundary treatment
    if is_final_chunk:
        # Smooth fade-out and trailing silence cushion so the DAC buffer plays out every syllable
        fade_out_len = min(int(0.015 * sr), len(samples))
        if fade_out_len > 0:
            ramp_out = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, fade_out_len, dtype=np.float32)))
            samples[-fade_out_len:] *= ramp_out

        # Generous 120ms cushion at the end to prevent hardware buffer cut-off
        cushion = np.zeros(int(0.12 * sr), dtype=np.float32)
        samples = np.concatenate([samples, cushion])
    else:
        # Intermediate chunk within sentence: 2ms micro-fade out, NO silence cushion to prevent pause gaps
        fade_out_len = min(int(0.002 * sr), len(samples))
        if fade_out_len > 0:
            ramp_out = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, fade_out_len, dtype=np.float32)))
            samples[-fade_out_len:] *= ramp_out

    # 6. Soft ceiling peak limiter: only downscales if clipping, preserving natural dynamics
    peak = float(np.max(np.abs(samples)))
    if peak > 0.95:
        samples = samples * (0.92 / peak)

    return samples, int(sr)


def _split_into_safe_tts_chunks(text: str, max_chunk_len: int = 250) -> list[str]:
    """
    Splits text along natural sentence boundaries (. ! ? \n) to maintain complete
    prosodic flow, avoiding choppy clause-level cutting mid-sentence.
    """
    if not text or not text.strip():
        return []

    raw_sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', text) if s.strip()]
    if not raw_sentences:
        return [text.strip()]

    chunks = []
    for s in raw_sentences:
        if len(s) <= max_chunk_len:
            chunks.append(s)
            continue
        # Only split excessively long sentences along commas/semicolons
        subparts = [c.strip() for c in re.split(r'(?<=[,;:—])\s+', s) if c.strip()]
        cur = []
        cur_len = 0
        for p in subparts:
            if cur_len + len(p) + 1 > max_chunk_len and cur:
                chunks.append(" ".join(cur))
                cur = [p]
                cur_len = len(p)
            else:
                cur.append(p)
                cur_len += len(p) + 1
        if cur:
            chunks.append(" ".join(cur))

    return chunks or [text.strip()]


# ── Audio Playback Worker (Continuous Stream across Sentences) ──────────────
def _playback_worker():
    """
    Dedicated audio output worker:
    - Maintains an active sounddevice OutputStream across consecutive chunks and sentences.
    - Eliminates per-sentence driver opening/closing latency and 120ms dead-air sleep.
    - Transitions assistant state to 'idle' only when all queues are genuinely empty and playback finishes.
    """
    global _active_stream
    stream = None
    stream_sr = None

    while True:
        try:
            item = _playback_queue.get(timeout=0.6)
        except queue.Empty:
            # Idle timeout: no audio was queued for 600ms
            if stream is not None and _speech_queue.empty() and _playback_queue.empty():
                with _active_stream_lock:
                    if _active_stream is stream:
                        _active_stream = None
                try:
                    time.sleep(0.15)  # Allow hardware DAC buffer to drain before stopping stream
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                stream = None
                stream_sr = None
                _update_state("idle")
            continue

        if item is None:
            _playback_queue.task_done()
            break

        samples, sr, gen, is_last_in_utterance = item
        _playback_queue.task_done()

        if not _speech_is_current(gen):
            continue

        _update_state("speaking")

        try:
            if stream is None or stream_sr != sr:
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                stream = _sd.OutputStream(samplerate=sr, channels=1, dtype="float32", blocksize=1024)
                stream.start()
                stream_sr = sr
                with _active_stream_lock:
                    _active_stream = stream

            if _speech_is_current(gen) and len(samples) > 0:
                stream.write(samples)

        except Exception as e:
            logger.debug(f"[Audio Playback Worker Note] {e}")

        # Check if playback is finished and all queues are empty
        if is_last_in_utterance and _speech_queue.empty() and _playback_queue.empty():
            time.sleep(0.15)
            if _speech_queue.empty() and _playback_queue.empty():
                _update_state("idle")


threading.Thread(target=_playback_worker, daemon=True, name="TTS-Playback").start()


# ── Speech Synthesis Worker (Pipelines ahead of playback) ─────────────────
def _speech_worker():
    """
    Dedicated synthesis worker:
    - Synthesizes queued sentences ahead of playback so audio streams seamlessly.
    - Feeds chunks directly to _playback_queue without blocking on playback completion.
    """
    while True:
        item = _speech_queue.get()
        voice_override = None
        if isinstance(item, tuple):
            text = item[0] if len(item) > 0 else ""
            generation = item[1] if len(item) > 1 else 0
            request_id = item[2] if len(item) > 2 else None
            broadcast_chat = item[3] if len(item) > 3 else False
            voice_override = item[4] if len(item) > 4 else None
        else:
            text, generation, request_id, broadcast_chat = item, 0, None, False

        if not text or not _speech_is_current(generation):
            _speech_queue.task_done()
            continue

        try:
            tts_started = time.perf_counter()
            _update_state("speaking")
            if broadcast_chat and _on_broadcast:
                _on_broadcast("chat_message", {"sender": "assistant", "text": text})

            if not _USE_SUPERTONIC:
                logger.warning("[TTS] Supertonic-3 neural TTS engine not available.")
                continue

            engine = _get_supertonic()
            target_voice = voice_override.lower().strip() if voice_override else sync_voice_from_profile()
            voice_info = CURATED_VOICES.get(target_voice)
            voice_id = voice_info["id"] if voice_info else SUPERTONIC_VOICE
            if voice_id not in ("F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"):
                voice_id = "F1"
            style = _get_expressive_voice_style(engine, voice_id)

            clean_text = _compact_tts_text(text)
            if not clean_text:
                continue

            chunks = _split_into_safe_tts_chunks(clean_text)
            if not chunks:
                chunks = [clean_text]

            total_chunks = len(chunks)
            for idx, chunk in enumerate(chunks):
                if not chunk or not _speech_is_current(generation):
                    break

                is_last_chunk = (idx == total_chunks - 1)
                spoken_chunk = chunk if chunk.endswith((".", "!", "?", ",", ";", ":")) else chunk + "."
                silence_dur = 0.05 if is_last_chunk else (0.02 if chunk.endswith((",", ";", ":")) else 0.03)

                try:
                    with _tts_lock:
                        if not _speech_is_current(generation):
                            break
                        wav, dur = engine.synthesize(
                            spoken_chunk,
                            voice_style=style,
                            total_steps=SUPERTONIC_STEPS,
                            speed=SUPERTONIC_SPEED,
                            silence_duration=silence_dur,
                            lang="na",
                        )

                    if wav is not None and wav.size > 0:
                        samples = wav.squeeze(0) if wav.ndim > 1 else wav
                        samples, sr = _process_audio_clarity(
                            samples, sr=engine.sample_rate, is_final_chunk=is_last_chunk
                        )
                        # Put in playback queue without deadlock risk on interruption
                        while _speech_is_current(generation):
                            try:
                                _playback_queue.put((samples, sr, generation, is_last_chunk), timeout=0.1)
                                break
                            except queue.Full:
                                continue
                except Exception as chunk_err:
                    logger.error(f"[Supertonic TTS Chunk Error] {chunk_err} on chunk: {chunk[:60]}")
                    continue

            logger.info(
                "[Timing] request_id=%s stage=tts duration_ms=%.1f",
                request_id or "unknown",
                (time.perf_counter() - tts_started) * 1000,
            )

        except Exception as e:
            logger.error(f"[TTS] Speech synthesis error: {e}")
        finally:
            _speech_queue.task_done()


threading.Thread(target=_speech_worker, daemon=True, name="TTS-Synthesizer").start()


_last_spoken_text = ""
_last_spoken_time = 0.0
_speech_dedup_lock = threading.Lock()


def speak(text: str, block: bool = False, request_id: str | None = None, broadcast_chat: bool = False, voice: str | None = None) -> None:
    """Speak text asynchronously, update assistant state, and stream audio with zero dead air."""
    if not text:
        return
    global _last_spoken_text, _last_spoken_time
    clean = text.strip()
    now = time.time()
    # Deduplicate only longer phrases (> 3 words) repeated within 1.0 second.
    # Never suppress short confirmations like "Done.", "Sure.", "Cancelled."
    with _speech_dedup_lock:
        if clean == _last_spoken_text and (now - _last_spoken_time) < 1.0 and len(clean.split()) > 3:
            return
        _last_spoken_text = clean
        _last_spoken_time = now

    with _speech_generation_lock:
        generation = _speech_generation
    item = (text, generation, request_id, broadcast_chat, voice)
    if block:
        _speech_queue.put(item)
        _speech_queue.join()
        _playback_queue.join()
    else:
        _speech_queue.put(item)


def stop_speaking() -> None:
    """Stop current audio immediately, flush queued items, and abort output stream."""
    global _speech_generation
    with _speech_generation_lock:
        _speech_generation += 1

    # Flush speech queue
    while True:
        try:
            _speech_queue.get_nowait()
            _speech_queue.task_done()
        except queue.Empty:
            break

    # Flush playback queue
    while True:
        try:
            _playback_queue.get_nowait()
            _playback_queue.task_done()
        except queue.Empty:
            break

    # Abort active sounddevice output stream immediately
    with _active_stream_lock:
        if _active_stream is not None:
            try:
                _active_stream.abort()
                _active_stream.close()
            except Exception:
                pass
            _active_stream = None

    try:
        _sd.stop()
    except Exception:
        pass

    _update_state("idle")


# ────────────────────────────────────────────────────────────────
# ── VOSK HIGH-ACCURACY OFFLINE SPEECH-TO-TEXT (STT) ENGINE ──────
# ────────────────────────────────────────────────────────────────

_vosk_model = None
_vosk_model_lock = threading.Lock()
_VOSK_INIT_ATTEMPTED = False
_VOSK_IS_AVAILABLE = False
_VOSK_DIR = os.path.join(_MODELS_ROOT, "vosk")


def _find_vosk_model_path() -> str:
    """Finds best available Vosk model directory (prefers Indian English, then US English)."""
    candidates = [
        os.path.join(_VOSK_DIR, "vosk-model-small-en-in-0.4"),
        os.path.join(_VOSK_DIR, "vosk-model-small-en-us-0.15"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "vosk", "vosk-model-small-en-in-0.4"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "vosk", "vosk-model-small-en-us-0.15"),
    ]
    for path in candidates:
        if os.path.isdir(path) and (os.path.exists(os.path.join(path, "am")) or os.path.exists(os.path.join(path, "ivector"))):
            return path

    # Check any subfolder in _VOSK_DIR
    if os.path.isdir(_VOSK_DIR):
        for sub in os.listdir(_VOSK_DIR):
            sub_path = os.path.join(_VOSK_DIR, sub)
            if os.path.isdir(sub_path) and (os.path.exists(os.path.join(sub_path, "am")) or os.path.exists(os.path.join(sub_path, "ivector"))):
                return sub_path
    return ""


def _ensure_vosk_model_downloaded() -> str:
    """Ensures a clean Vosk offline model is present on disk."""
    existing = _find_vosk_model_path()
    if existing:
        return existing

    os.makedirs(_VOSK_DIR, exist_ok=True)
    zip_path = os.path.join(_VOSK_DIR, "model.zip")
    url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
    try:
        logger.info("[STT] Downloading lightweight Vosk offline model (~40MB)...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=30.0) as resp, open(zip_path, "wb") as out_f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out_f.write(chunk)
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(_VOSK_DIR)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        logger.info("[STT] Vosk offline STT model downloaded and unpacked successfully.")
        return _find_vosk_model_path()
    except Exception as e:
        logger.error("[STT] Failed to download Vosk model: %s", e)
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        return ""


def get_stt_recognizer():
    """Lazy-loads the Vosk offline speech recognizer."""
    global _vosk_model, _VOSK_INIT_ATTEMPTED, _VOSK_IS_AVAILABLE
    if _vosk_model is not None:
        return _vosk_model

    with _vosk_model_lock:
        if _vosk_model is not None:
            return _vosk_model
        if _VOSK_INIT_ATTEMPTED and not _VOSK_IS_AVAILABLE:
            return None

        _VOSK_INIT_ATTEMPTED = True
        try:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
        except ImportError:
            logger.warning("[STT] vosk not installed.")
            _VOSK_IS_AVAILABLE = False
            return None

        model_path = _ensure_vosk_model_downloaded()
        if not model_path:
            _VOSK_IS_AVAILABLE = False
            return None

        try:
            t0 = time.time()
            logger.info("[STT] Loading Vosk offline engine from %s...", model_path)
            _vosk_model = Model(model_path)
            _VOSK_IS_AVAILABLE = True
            logger.info("[STT] Vosk offline STT ready in %.1fms.", (time.time() - t0) * 1000)
            return _vosk_model
        except Exception as e:
            logger.error("[STT] Failed to initialize Vosk model: %s", e)
            _VOSK_IS_AVAILABLE = False
            return None


def is_stt_available() -> bool:
    """Returns True if offline STT is initialized and available."""
    return get_stt_recognizer() is not None


def get_stt_engine_name() -> str:
    """Returns descriptive name of active STT engine."""
    if is_stt_available():
        return "Vosk (Offline Neural STT)"
    return "Unavailable"


def transcribe_samples(samples: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribes raw float32 audio samples [-1.0, 1.0] completely offline with Vosk."""
    model = get_stt_recognizer()
    if model is None or samples is None or len(samples) == 0:
        return ""

    try:
        samples = np.asarray(samples, dtype=np.float32)
        # Resample to 16000 Hz if necessary
        if sample_rate != 16000 and len(samples) > 0:
            try:
                import scipy.signal
                from math import gcd
                g = gcd(int(sample_rate), 16000)
                samples = scipy.signal.resample_poly(samples, 16000 // g, int(sample_rate) // g).astype(np.float32)
            except Exception:
                pass

        if len(samples) < 1600:  # Less than 100ms
            return ""

        peak = float(np.max(np.abs(samples)))
        if peak < 0.01:
            return ""

        # Normalize gain if too low or high
        if peak > 0:
            samples = samples / max(peak, 0.2)

        pcm_bytes = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()

        from vosk import KaldiRecognizer
        with _vosk_model_lock:
            rec = KaldiRecognizer(model, 16000)
            rec.AcceptWaveform(pcm_bytes)
            res = json.loads(rec.FinalResult())
            raw_text = res.get("text", "")
            return raw_text.strip()
    except Exception as e:
        logger.error("[STT] Vosk transcription error: %s", e)
        return ""


def transcribe_audio_data(audio_data) -> str:
    """
    Transcribes speech_recognition.AudioData offline.
    Converts 16-bit PCM buffer to normalized float32 waveform.
    """
    try:
        raw_pcm = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
        samples = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return transcribe_samples(samples, 16000)
    except Exception as e:
        logger.error(f"[STT] AudioData conversion error: {e}")
        return ""


def transcribe_audio_bytes(audio_bytes: bytes) -> str:
    """Transcribes audio bytes from uploaded WebM, Opus, Ogg, WAV, or PCM buffer completely offline."""
    if not audio_bytes:
        return ""

    # 1. Try PyAV (handles WebM/Opus from browser MediaRecorder, OGG, WAV, MP3, AAC, FLAC)
    try:
        import av
        with io.BytesIO(audio_bytes) as in_bio:
            container = av.open(in_bio)
            resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
            chunks = []
            for frame in container.decode(audio=0):
                for rf in resampler.resample(frame):
                    chunks.append(rf.to_ndarray()[0])
            # Flush resampler buffer so the tail is never dropped
            for rf in resampler.resample(None):
                chunks.append(rf.to_ndarray()[0])
            if chunks:
                audio_samples = np.concatenate(chunks)
                return transcribe_samples(audio_samples, 16000)
    except Exception as e_av:
        logger.debug(f"[STT] PyAV decode note: {e_av}")

    # 2. Try soundfile (WAV, FLAC, OGG)
    try:
        import soundfile as sf
        with io.BytesIO(audio_bytes) as bio:
            audio, sr = sf.read(bio, dtype="float32")
            if audio.ndim > 1:
                audio = np.mean(audio, axis=1)
            return transcribe_samples(audio, sr)
    except Exception:
        pass

    # 3. Fallback: Raw 16-bit PCM
    try:
        samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return transcribe_samples(samples, 16000)
    except Exception as e2:
        logger.error(f"[STT] Raw audio byte transcription failed: {e2}")
        return ""


def transcribe_b64(audio_b64: str) -> str:
    """Decodes base64-encoded audio data and transcribes it offline with Vosk."""
    if not audio_b64:
        return ""
    try:
        import base64
        if "," in audio_b64:
            audio_b64 = audio_b64.split(",", 1)[1]
        raw_bytes = base64.b64decode(audio_b64)
        return transcribe_audio_bytes(raw_bytes)
    except Exception as e:
        logger.error(f"[STT] Base64 decode error: {e}")
        return ""


# Warm up STT in background thread
def _warmup_stt():
    def _run():
        try:
            model = get_stt_recognizer()
            if model:
                dummy = np.zeros(16000, dtype=np.float32)
                transcribe_samples(dummy, 16000)
                logger.debug("[STT] Vosk STT primed.")
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="VoskSTT-Warmup").start()


_warmup_stt()
