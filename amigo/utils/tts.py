"""
Unified Speech Engine Module for Amigo Voice Assistant.
Handles both Offline Neural Text-to-Speech (TTS via Kyutai Pocket TTS 24kHz)
and Offline Neural Speech-to-Text (STT via Vosk Neural Recognizer).
"""

import copy
import inspect
import io
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.request
import numpy as np

try:
    import num2words
except ImportError:
    num2words = None

# Silence verbose third-party pocket_tts generation timers and step logs
for _pkg in (
    "pocket_tts",
    "pocket_tts.models.tts_model",
    "pocket_tts.utils.utils",
    "pocket_tts.models",
    "pocket_tts.utils",
):
    _pl = logging.getLogger(_pkg)
    _pl.setLevel(logging.ERROR)
    _pl.propagate = False

logger = logging.getLogger("amigo.speech")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_MODELS_ROOT = os.path.join(_PROJECT_ROOT, "models")

try:
    import sounddevice as _sd
    from pocket_tts import TTSModel

    _USE_POCKET_TTS = True
    logger.info("[TTS] Kyutai Pocket TTS Engine available (24kHz).")
except ImportError:
    _USE_POCKET_TTS = False
    logger.warning("[TTS] pocket-tts package not available.")

# ── 10 Curated Best Offline Studio Neural Voices (Kyutai Pocket TTS - 24kHz Studio) ────────
CURATED_VOICES = {
    # 5 Best Female Neural Voices (Pocket TTS)
    "alba":    {"engine": "pocket_tts", "id": "alba",    "name": "Alba",    "gender": "Female", "accent": "US", "desc": "Smooth, articulate & studio-clean neural female (Recommended)"},
    "cosette": {"engine": "pocket_tts", "id": "cosette", "name": "Cosette", "gender": "Female", "accent": "US", "desc": "Soft, natural & warm conversational female"},
    "fantine": {"engine": "pocket_tts", "id": "fantine", "name": "Fantine", "gender": "Female", "accent": "US", "desc": "Warm, expressive and friendly conversational tone"},
    "anna":    {"engine": "pocket_tts", "id": "anna",    "name": "Anna",    "gender": "Female", "accent": "US", "desc": "Bright, friendly & clear female delivery"},
    "eve":     {"engine": "pocket_tts", "id": "eve",     "name": "Eve",     "gender": "Female", "accent": "US", "desc": "Energetic, crisp and articulate female delivery"},

    # 5 Best Male Neural Voices (Pocket TTS)
    "marius":  {"engine": "pocket_tts", "id": "marius",  "name": "Marius",  "gender": "Male",   "accent": "US", "desc": "Warm, relatable and conversational companion"},
    "javert":  {"engine": "pocket_tts", "id": "javert",  "name": "Javert",  "gender": "Male",   "accent": "US", "desc": "Deep, calm and grounded baritone resonance"},
    "jean":    {"engine": "pocket_tts", "id": "jean",    "name": "Jean",    "gender": "Male",   "accent": "US", "desc": "Professional, crisp and articulate executive tone"},
    "michael": {"engine": "pocket_tts", "id": "michael", "name": "Michael", "gender": "Male",   "accent": "US", "desc": "Young, natural, and modern male delivery"},
    "george":  {"engine": "pocket_tts", "id": "george",  "name": "George",  "gender": "Male",   "accent": "US", "desc": "Distinguished, articulate gentleman delivery"},

    # Backward compatibility with legacy Supertonic voice names
    "nova":    {"engine": "pocket_tts", "id": "alba",    "name": "Alba",    "gender": "Female", "accent": "US", "desc": "Smooth, articulate female"},
    "aria":    {"engine": "pocket_tts", "id": "cosette", "name": "Cosette", "gender": "Female", "accent": "US", "desc": "Soft, natural & warm female"},
    "serena":  {"engine": "pocket_tts", "id": "fantine", "name": "Fantine", "gender": "Female", "accent": "US", "desc": "Warm, expressive female"},
    "chloe":   {"engine": "pocket_tts", "id": "alba",    "name": "Alba",    "gender": "Female", "accent": "US", "desc": "Studio-clean conversational female"},
    "luna":    {"engine": "pocket_tts", "id": "eve",     "name": "Eve",     "gender": "Female", "accent": "US", "desc": "Energetic and crisp female"},
    "orion":   {"engine": "pocket_tts", "id": "javert",  "name": "Javert",  "gender": "Male",   "accent": "US", "desc": "Deep calm baritone male"},
    "atlas":   {"engine": "pocket_tts", "id": "jean",    "name": "Jean",    "gender": "Male",   "accent": "US", "desc": "Professional executive male"},
    "leo":     {"engine": "pocket_tts", "id": "marius",  "name": "Marius",  "gender": "Male",   "accent": "US", "desc": "Warm & conversational male"},
    "felix":   {"engine": "pocket_tts", "id": "michael", "name": "Michael", "gender": "Male",   "accent": "US", "desc": "Young & natural modern male"},
    "ethan":   {"engine": "pocket_tts", "id": "george",  "name": "George",  "gender": "Male",   "accent": "US", "desc": "Distinguished gentleman male"},

    "f1":      {"engine": "pocket_tts", "id": "alba",    "name": "Alba",    "gender": "Female", "accent": "US", "desc": "Smooth studio female"},
    "f2":      {"engine": "pocket_tts", "id": "cosette", "name": "Cosette", "gender": "Female", "accent": "US", "desc": "Soft warm female"},
    "f3":      {"engine": "pocket_tts", "id": "fantine", "name": "Fantine", "gender": "Female", "accent": "US", "desc": "Warm expressive female"},
    "f4":      {"engine": "pocket_tts", "id": "anna",    "name": "Anna",    "gender": "Female", "accent": "US", "desc": "Bright clear female"},
    "f5":      {"engine": "pocket_tts", "id": "eve",     "name": "Eve",     "gender": "Female", "accent": "US", "desc": "Energetic crisp female"},
    "m1":      {"engine": "pocket_tts", "id": "javert",  "name": "Javert",  "gender": "Male",   "accent": "US", "desc": "Deep baritone male"},
    "m2":      {"engine": "pocket_tts", "id": "jean",    "name": "Jean",    "gender": "Male",   "accent": "US", "desc": "Executive male"},
    "m3":      {"engine": "pocket_tts", "id": "marius",  "name": "Marius",  "gender": "Male",   "accent": "US", "desc": "Conversational male"},
    "m4":      {"engine": "pocket_tts", "id": "michael", "name": "Michael", "gender": "Male",   "accent": "US", "desc": "Modern male"},
    "m5":      {"engine": "pocket_tts", "id": "george",  "name": "George",  "gender": "Male",   "accent": "US", "desc": "Distinguished gentleman"},
}

_PROFILE_PATH = os.path.join(_PROJECT_ROOT, "amigo_profile.json")
if not os.path.exists(_PROFILE_PATH):
    _PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amigo_profile.json")

ACTIVE_VOICE = "alba"
POCKET_TTS_SAMPLE_RATE = 24000

_tts_lock = threading.Lock()
_pocket_tts_init_lock = threading.Lock()
_pocket_tts_instance = None
_active_stream = None
_active_stream_lock = threading.Lock()
_voice_state_cache = {}
_voice_state_lock = threading.Lock()
_current_stop_event = threading.Event()
_profile_mtime_tts = 0.0


def sync_voice_from_profile() -> str:
    """Synchronizes active voice selection from amigo_profile.json, caching disk reads by mtime."""
    global ACTIVE_VOICE, _profile_mtime_tts
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
                        return v_key
    except Exception as e:
        logger.debug(f"[TTS] Voice profile sync note: {e}")
    return ACTIVE_VOICE


# Initialize active voice from user preference immediately on startup
sync_voice_from_profile()


def set_voice(voice_name: str) -> str:
    """Sets the active voice, updates engine settings, and immediately persists to user profile atomically."""
    global ACTIVE_VOICE, _profile_mtime_tts
    key = voice_name.lower().strip()
    display_name = voice_name
    if key in CURATED_VOICES:
        ACTIVE_VOICE = key
        info = CURATED_VOICES[key]
        display_name = info["name"]
        logger.info(f"[TTS] Active voice switched to: {info['name']} (Pocket TTS {info['id']})")
    else:
        ACTIVE_VOICE = "alba"
        display_name = "Alba"

    try:
        pdata = {}
        if os.path.exists(_PROFILE_PATH):
            with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
                pdata = json.load(f)
        pdata.setdefault("preferences", {})["voice"] = ACTIVE_VOICE
        pdata.setdefault("ui_settings", {})["voice"] = ACTIVE_VOICE
        pdata.setdefault("user_profile", {}).setdefault("preferences", {})["voice"] = ACTIVE_VOICE

        tmp_file = _PROFILE_PATH + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(pdata, f, indent=2)
        os.replace(tmp_file, _PROFILE_PATH)
        _profile_mtime_tts = os.path.getmtime(_PROFILE_PATH)

        try:
            from amigo.core import rag_engine
            if hasattr(rag_engine, "_profile_cache") and rag_engine._profile_cache is not None:
                rag_engine._profile_cache.setdefault("preferences", {})["voice"] = ACTIVE_VOICE
                rag_engine._profile_cache.setdefault("ui_settings", {})["voice"] = ACTIVE_VOICE
                rag_engine._profile_cache.setdefault("user_profile", {}).setdefault("preferences", {})["voice"] = ACTIVE_VOICE
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
    vname = voice_info["name"] if voice_info else "Alba"
    if _USE_POCKET_TTS:
        return f"Pocket TTS 24kHz ({vname})"
    return f"Pocket TTS ({vname})"


def _get_pocket_tts():
    """Lazy-load Kyutai Pocket TTS model - thread-safe and 100% offline capable."""
    global _pocket_tts_instance, POCKET_TTS_SAMPLE_RATE
    if _pocket_tts_instance is None:
        with _pocket_tts_init_lock:
            if _pocket_tts_instance is None:
                logger.info("[TTS] Initializing Kyutai Pocket TTS Neural Engine (24kHz)...")
                from pocket_tts import TTSModel
                try:
                    _pocket_tts_instance = TTSModel.load_model()
                except Exception:
                    old_offline = os.environ.get("HF_HUB_OFFLINE")
                    try:
                        os.environ["HF_HUB_OFFLINE"] = "1"
                        _pocket_tts_instance = TTSModel.load_model()
                    finally:
                        if old_offline is None:
                            os.environ.pop("HF_HUB_OFFLINE", None)
                        else:
                            os.environ["HF_HUB_OFFLINE"] = old_offline
                if hasattr(_pocket_tts_instance, "sample_rate") and _pocket_tts_instance.sample_rate:
                    POCKET_TTS_SAMPLE_RATE = int(_pocket_tts_instance.sample_rate)
                logger.info("[TTS] Kyutai Pocket TTS ready.")
    return _pocket_tts_instance


def _clone_voice_state(state):
    """
    Deep-clones the Pocket TTS voice state (including PyTorch tensors and KV caches).
    CRITICAL: Pocket TTS mutates voice_state in-place during generation. Reusing a
    mutated state without cloning corrupts attention caches, causing manic voices,
    skipped words, and early sentence cutoffs.
    """
    if isinstance(state, dict):
        return {k: _clone_voice_state(v) for k, v in state.items()}
    if isinstance(state, list):
        return [_clone_voice_state(v) for v in state]
    if isinstance(state, tuple):
        return tuple(_clone_voice_state(v) for v in state)
    if hasattr(state, "clone") and callable(state.clone):
        return state.clone()
    return copy.deepcopy(state)


def _get_voice_state(model, voice_name: str):
    """Retrieves a clean, unmutated copy of the voice conditioning state vector."""
    v_key = voice_name.lower().strip()
    voice_info = CURATED_VOICES.get(v_key)
    prompt_id = voice_info["id"] if voice_info else "alba"

    with _voice_state_lock:
        if prompt_id not in _voice_state_cache:
            try:
                st = model.get_state_for_audio_prompt(prompt_id)
                _voice_state_cache[prompt_id] = st
            except Exception as e:
                logger.warning(f"[TTS] Failed to load voice state for '{prompt_id}': {e}. Falling back to 'alba'.")
                if "alba" not in _voice_state_cache:
                    _voice_state_cache["alba"] = model.get_state_for_audio_prompt("alba")
                prompt_id = "alba"
        return _clone_voice_state(_voice_state_cache[prompt_id])


def _warmup_tts():
    """Warms up Kyutai Pocket TTS in a background thread so initial speech is instant."""
    def _warm():
        try:
            if _USE_POCKET_TTS:
                model = _get_pocket_tts()
                _get_voice_state(model, "alba")
                logger.info("[TTS] Kyutai Pocket TTS primed and cached.")
        except Exception as e:
            logger.debug(f"[TTS Warmup] {e}")

    threading.Thread(target=_warm, daemon=True, name="PocketTTS-Warmup").start()


_warmup_tts()


# ── Asynchronous speech pipeline queues & generation ──────────────────────
_speech_queue = queue.Queue()
_playback_queue = queue.Queue(maxsize=256)
_speech_generation = 0
_speech_generation_lock = threading.Lock()
_state_lock = threading.Lock()
_is_speaking_state = False
_synthesizing_flag = threading.Event()


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


_RE_URL = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
_RE_CONSECUTIVE_DOTS = re.compile(r'\.{2,}')
_RE_WHITESPACE = re.compile(r'\s+')
_RE_SPECIAL_CHARS = re.compile(r'[*_~`#<>{}[\]\\]')


def _is_likely_initialism(word: str) -> bool:
    """
    Algorithmically determines if an uppercase token is an unpronounceable initialism
    (e.g., 'CPU', 'GPU', 'HTML', 'JSON', 'TTS') vs a normal capitalized word ('NASA', 'TIME', 'OPEN')
    without relying on hardcoded word lists.
    """
    if len(word) < 2 or len(word) > 5 or not word.isalpha() or not word.isupper():
        return False
    vowels = set("AEIOUY")
    vowel_count = sum(1 for ch in word if ch in vowels)
    # No vowels (e.g. TTS, STT, LLM, GPU, CPU -> wait: CPU/GPU/API have 1 vowel at end/start)
    if vowel_count == 0:
        return True
    # Initialisms with digits or consonant clusters that cannot form English syllables
    # Check if every character is consonant except a single trailing/leading vowel in 3-letter tech abbreviations
    # Better yet: only spell out words with 0 vowels, and let the neural BPE tokenizer pronounce vowel-containing words!
    return False


def normalize_text_for_tts(text: str) -> str:
    """
    Normalizes raw text into clean, phonetically natural speech without hardcoded word lists
    or destructive punctuation stripping that causes word-eating.
    """
    if not text:
        return ""
    t = text

    # 1. Markdown tables to natural conversational sentences
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
                        details.append(f"{col_name} is {cell}")
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

    # 4. Markdown bullet points -> clean sentence separation (never leading commas, which eat words)
    t = re.sub(r'(?:^|\n)\s*[-*•]\s+', '. ', t)

    # 5. Numbered lists (1. item -> First, item.) using num2words dynamically
    def _list_repl(m):
        num = int(m.group(1))
        if num2words is not None:
            try:
                return f". {num2words.num2words(num, to='ordinal').capitalize()}, "
            except Exception:
                pass
        return f". {num}, "
    t = re.sub(r'(?:^|\n)\s*(\d+)\.\s+', _list_repl, t)

    # 6. Number ranges & scores BEFORE hyphens are touched (3-1 -> 3 to 1)
    t = re.sub(r'(\d+)\s*[-–—]\s*(\d+)', r'\1 to \2', t)

    # 7. Phone numbers -> digit by digit
    def _phone_repl(m):
        cleaned = re.sub(r'\D', '', m.group(0))
        return " ".join(num2words.num2words(int(d)) if num2words else d for d in cleaned)
    t = re.sub(r'\b(?:\+?\d{1,3}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b', _phone_repl, t)

    # 8. Formatted large numbers with commas: e.g. 1,234,567 -> cardinal words
    if num2words is not None:
        def _comma_num_repl(m):
            raw = m.group(0).replace(",", "")
            try:
                return num2words.num2words(int(raw))
            except Exception:
                return raw
        t = re.sub(r'\b\d{1,3}(?:,\d{3})+\b', _comma_num_repl, t)

    # 9. Currency ($23.50 -> 23 dollars and 50 cents, $23 -> 23 dollars)
    def _currency_repl(m):
        dollars = int(m.group(1))
        cents = m.group(2)
        d_str = num2words.num2words(dollars) if num2words else str(dollars)
        d_unit = "dollar" if dollars == 1 else "dollars"
        if cents is not None:
            c_val = int(cents)
            if c_val > 0:
                c_str = num2words.num2words(c_val) if num2words else str(c_val)
                c_unit = "cent" if c_val == 1 else "cents"
                return f"{d_str} {d_unit} and {c_str} {c_unit}"
        return f"{d_str} {d_unit}"
    t = re.sub(r'\$(\d+)(?:\.(\d{2}))?\b', _currency_repl, t)

    # 10. Percentages (25% -> 25 percent)
    t = re.sub(r'(\d+(?:\.\d+)?)%', r'\1 percent', t)

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

    # 13. Standalone em-dashes and spaced hyphens -> comma pause, preserving intra-word hyphens
    t = re.sub(r'(?<=\s)[-–—]+(?=\s)', ', ', t)
    t = re.sub(r'[–—]', ', ', t)

    # 14. Colons and semicolons -> natural comma pause
    t = re.sub(r'[:;](?=\s|$)', ', ', t)

    # 15. Parentheses: (extra info) -> , extra info,
    t = re.sub(r'\(([^)]+)\)', r', \1, ', t)

    # 16. Strip remaining markdown formatting characters
    t = _RE_SPECIAL_CHARS.sub(' ', t)
    t = re.sub(r'(?<=\w)/(?=\w)', ' or ', t)
    t = re.sub(r'[/|]', ' ', t)

    if num2words is not None:
        # Ordinals (1st, 2nd, 3rd, 4th, 21st)
        def _ord_repl(m):
            try:
                return num2words.num2words(int(m.group(1)), to='ordinal')
            except Exception:
                return m.group(0)

        t = re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', _ord_repl, t, flags=re.I)

        # Standalone cardinal numbers
        def _num_repl(m):
            try:
                return num2words.num2words(int(m.group(1)))
            except Exception:
                return m.group(0)

        t = re.sub(r'\b(\d+)\b', _num_repl, t)

    # 17. Convert all-caps words cleanly without hardcoded lists:
    # Vowel-less abbreviations (e.g. TTS, STT, LLM) are spaced; normal all-caps words are lowercased
    # so the neural tokenizer reads them as normal words instead of screaming or spelling them out.
    def _caps_repl(m):
        w = m.group(0)
        if _is_likely_initialism(w):
            return " ".join(list(w))
        return w.capitalize()

    t = re.sub(r'\b[A-Z]{2,}\b', _caps_repl, t)

    # 18. Clean consecutive punctuation and leading/trailing artifacts
    t = _RE_CONSECUTIVE_DOTS.sub('.', t)
    t = re.sub(r'!{2,}', '!', t)
    t = re.sub(r'\?{2,}', '?', t)
    t = re.sub(r'(?:,\s*){2,}', ', ', t)
    t = re.sub(r'(?:\.\s*){2,}', '. ', t)
    t = re.sub(r'\.\s*,', '.', t)
    t = re.sub(r',\s*\.', '.', t)
    t = re.sub(r'^\s*[,;:.]+\s*', '', t)
    t = re.sub(r'\s+([,.!?])', r'\1', t)
    return _RE_WHITESPACE.sub(' ', t).strip()


def _clean_tts_text(text: str) -> str:
    """Cleans and normalizes text for natural neural Pocket TTS prosody."""
    return normalize_text_for_tts(text)


def _compact_tts_text(text: str) -> str:
    """Cleans text for neural synthesis without truncating content."""
    return _clean_tts_text(text)


def _process_audio_clarity(
    samples: np.ndarray,
    sr: int = 24000,
    is_first_chunk: bool = False,
    is_final_chunk: bool = False,
) -> tuple[np.ndarray, int]:
    """
    Lightweight, phase-continuous audio conditioning for streaming chunks:
    - Avoids stateless per-chunk IIR filters that cause boundary clicks and phase distortion.
    - Applies gentle onset/offset cosine ramps only at utterance boundaries (never mid-word).
    - Soft-clips peaks above 0.95 without pumping inter-chunk gain.
    """
    if samples is None or len(samples) == 0:
        return samples, int(sr)
    samples = np.asarray(samples, dtype=np.float32)

    if is_first_chunk:
        fade_in_len = min(int(0.004 * sr), len(samples))
        if fade_in_len > 0:
            ramp_in = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, fade_in_len, dtype=np.float32)))
            samples = samples.copy()
            samples[:fade_in_len] *= ramp_in

    if is_final_chunk:
        fade_out_len = min(int(0.012 * sr), len(samples))
        if fade_out_len > 0:
            ramp_out = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, fade_out_len, dtype=np.float32)))
            samples = samples.copy()
            samples[-fade_out_len:] *= ramp_out

    peak = float(np.max(np.abs(samples)))
    if peak > 0.95:
        samples = samples * (0.94 / peak)

    return np.ascontiguousarray(samples, dtype=np.float32), int(sr)


def _split_into_safe_tts_chunks(text: str, max_chunk_len: int = 220) -> list[str]:
    """
    Splits text into complete sentences (. ! ? \n) so each unit gets a fresh, uncorrupted
    KV-cache state while maintaining natural sentence-level intonation.
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
        # Split overly long sentences only at clause boundaries
        subparts = [c.strip() for c in re.split(r'(?<=[,;:])\s+', s) if c.strip()]
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

    return [c for c in chunks if c and any(ch.isalnum() for ch in c)]


# ── Audio Playback Worker (Continuous Stream across Sentences) ──────────────
def _playback_worker():
    """
    Dedicated audio output worker:
    - Keeps sounddevice OutputStream open while synthesis is active or queued.
    - Prevents mid-sentence stream closures and hardware DAC truncation.
    """
    global _active_stream
    stream = None
    stream_sr = None

    while True:
        try:
            item = _playback_queue.get(timeout=0.5)
        except queue.Empty:
            # Only close stream if synthesis is completely done and all queues are empty
            if (
                stream is not None
                and not _synthesizing_flag.is_set()
                and _speech_queue.empty()
                and _playback_queue.empty()
            ):
                with _active_stream_lock:
                    if _active_stream is stream:
                        _active_stream = None
                try:
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
        try:
            if not _speech_is_current(gen):
                continue

            _update_state("speaking")

            if stream is None or stream_sr != sr:
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                stream = _sd.OutputStream(
                    samplerate=sr,
                    channels=1,
                    dtype="float32",
                    blocksize=2048,
                    latency="high",
                )
                stream.start()
                stream_sr = sr
                with _active_stream_lock:
                    _active_stream = stream

            if _speech_is_current(gen) and samples is not None and len(samples) > 0:
                stream.write(samples)

            if (
                is_last_in_utterance
                and not _synthesizing_flag.is_set()
                and _speech_queue.empty()
                and _playback_queue.empty()
            ):
                _update_state("idle")

        except Exception as e:
            logger.debug(f"[Audio Playback Worker Note] {e}")
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
                stream = None
                stream_sr = None
        finally:
            _playback_queue.task_done()


threading.Thread(target=_playback_worker, daemon=True, name="TTS-Playback").start()


def _invoke_generate_audio_stream(model, voice_state, text: str, stop_evt: threading.Event):
    """
    Calls model.generate_audio_stream safely by inspecting supported parameters,
    avoiding TypeError if `stop` is not supported by the installed pocket_tts version.
    """
    sig = inspect.signature(model.generate_audio_stream)
    kwargs = {}
    if "stop" in sig.parameters:
        kwargs["stop"] = stop_evt
    if "copy_state" in sig.parameters:
        kwargs["copy_state"] = True
    return model.generate_audio_stream(voice_state, text, **kwargs)


# ── Speech Synthesis Worker (Pipelines ahead of playback) ─────────────────
def _speech_worker():
    """
    Dedicated synthesis worker:
    - Gives every sentence chunk a clean, cloned voice_state so the KV-cache never overflows or corrupts.
    - Streams 24kHz audio chunks with proper onset/offset conditioning and inter-sentence breath pauses.
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

        _synthesizing_flag.set()
        try:
            tts_started = time.perf_counter()
            _update_state("speaking")
            if broadcast_chat and _on_broadcast:
                _on_broadcast("chat_message", {"sender": "assistant", "text": text})

            if not _USE_POCKET_TTS:
                logger.warning("[TTS] Pocket TTS neural engine not available.")
                continue

            model = _get_pocket_tts()
            target_voice = voice_override.lower().strip() if voice_override else sync_voice_from_profile()

            clean_text = _compact_tts_text(text)
            if not clean_text:
                continue

            chunks = _split_into_safe_tts_chunks(clean_text)
            if not chunks:
                chunks = [clean_text]

            stop_evt = _current_stop_event
            first_chunk_emitted = False

            for idx, chunk in enumerate(chunks):
                if not chunk or not _speech_is_current(generation) or stop_evt.is_set():
                    break

                # Ensure terminal punctuation so the neural model completes the final word prosody
                spoken_chunk = chunk.strip()
                if not spoken_chunk.endswith((".", "!", "?")):
                    spoken_chunk = spoken_chunk.rstrip(",;:") + "."

                try:
                    with _tts_lock:
                        if not _speech_is_current(generation) or stop_evt.is_set():
                            break

                        # CRITICAL: Always obtain a fresh cloned voice state per chunk so
                        # KV-cache mutations never accumulate into manic speech or word eating
                        fresh_voice_state = _get_voice_state(model, target_voice)
                        audio_stream = _invoke_generate_audio_stream(
                            model, fresh_voice_state, spoken_chunk, stop_evt
                        )

                        is_first_tensor = True
                        prev_samples = None

                        for chunk_tensor in audio_stream:
                            if not _speech_is_current(generation) or stop_evt.is_set():
                                prev_samples = None
                                break

                            raw_samples = chunk_tensor.detach().cpu().numpy().reshape(-1)
                            if raw_samples is not None and raw_samples.size > 0:
                                if prev_samples is not None:
                                    processed, sr = _process_audio_clarity(
                                        prev_samples,
                                        POCKET_TTS_SAMPLE_RATE,
                                        is_first_chunk=is_first_tensor,
                                        is_final_chunk=False,
                                    )
                                    is_first_tensor = False
                                    while _speech_is_current(generation) and not stop_evt.is_set():
                                        try:
                                            _playback_queue.put((processed, sr, generation, False), timeout=0.1)
                                            first_chunk_emitted = True
                                            break
                                        except queue.Full:
                                            continue
                                prev_samples = raw_samples

                        # Flush the final tensor of this sentence with smooth fade-out and natural pause
                        if prev_samples is not None and _speech_is_current(generation) and not stop_evt.is_set():
                            processed, sr = _process_audio_clarity(
                                prev_samples,
                                POCKET_TTS_SAMPLE_RATE,
                                is_first_chunk=is_first_tensor,
                                is_final_chunk=True,
                            )
                            # Add natural 90ms inter-sentence pause (or 160ms final hardware drain cushion)
                            pause_dur = 0.16 if (idx == len(chunks) - 1) else 0.09
                            pause_pad = np.zeros(int(pause_dur * sr), dtype=np.float32)
                            processed = np.concatenate([processed, pause_pad])

                            is_last = (idx == len(chunks) - 1)
                            while _speech_is_current(generation) and not stop_evt.is_set():
                                try:
                                    _playback_queue.put((processed, sr, generation, is_last), timeout=0.1)
                                    first_chunk_emitted = True
                                    break
                                except queue.Full:
                                    continue

                except Exception as chunk_err:
                    logger.error(f"[Pocket TTS Chunk Error] {chunk_err} on chunk: {chunk[:60]}")
                    continue

            if not first_chunk_emitted and _speech_queue.empty() and _playback_queue.empty():
                _update_state("idle")

            logger.debug(
                "[Timing] request_id=%s stage=tts duration_ms=%.1f",
                request_id or "unknown",
                (time.perf_counter() - tts_started) * 1000,
            )

        except Exception as e:
            logger.error(f"[TTS] Speech synthesis error: {e}")
        finally:
            _synthesizing_flag.clear()
            _speech_queue.task_done()


threading.Thread(target=_speech_worker, daemon=True, name="TTS-Synthesizer").start()


_last_spoken_text = ""
_last_spoken_time = 0.0
_speech_dedup_lock = threading.Lock()


def speak(
    text: str,
    block: bool = False,
    request_id: str | None = None,
    broadcast_chat: bool = False,
    voice: str | None = None,
) -> None:
    """Speak text asynchronously, update assistant state, and stream audio with zero dead air."""
    if not text:
        return
    global _last_spoken_text, _last_spoken_time
    clean = text.strip()
    now = time.time()
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
    """Stop current audio immediately, signal model to halt generation, flush queues, and abort stream."""
    global _speech_generation, _current_stop_event, _active_stream
    _current_stop_event.set()
    with _speech_generation_lock:
        _speech_generation += 1

    _current_stop_event = threading.Event()

    while True:
        try:
            _speech_queue.get_nowait()
            _speech_queue.task_done()
        except queue.Empty:
            break

    while True:
        try:
            _playback_queue.get_nowait()
            _playback_queue.task_done()
        except queue.Empty:
            break

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
        if sample_rate != 16000 and len(samples) > 0:
            try:
                import scipy.signal
                from math import gcd
                g = gcd(int(sample_rate), 16000)
                samples = scipy.signal.resample_poly(samples, 16000 // g, int(sample_rate) // g).astype(np.float32)
            except Exception:
                pass

        if len(samples) < 1600:
            return ""

        peak = float(np.max(np.abs(samples)))
        if peak < 0.01:
            return ""

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

    try:
        import av
        with io.BytesIO(audio_bytes) as in_bio:
            container = av.open(in_bio)
            resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
            chunks = []
            for frame in container.decode(audio=0):
                for rf in resampler.resample(frame):
                    chunks.append(rf.to_ndarray()[0])
            for rf in resampler.resample(None):
                chunks.append(rf.to_ndarray()[0])
            if chunks:
                audio_samples = np.concatenate(chunks)
                return transcribe_samples(audio_samples, 16000)
    except Exception as e_av:
        logger.debug(f"[STT] PyAV decode note: {e_av}")

    try:
        import soundfile as sf
        with io.BytesIO(audio_bytes) as bio:
            audio, sr = sf.read(bio, dtype="float32")
            if audio.ndim > 1:
                audio = np.mean(audio, axis=1)
            return transcribe_samples(audio, sr)
    except Exception:
        pass

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