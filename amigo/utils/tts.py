"""
Unified Speech Engine Module for Amigo Voice Assistant.
Handles high-fidelity Neural Text-to-Speech (TTS via Pocket TTS / Kyutai 24kHz Offline Mimi neural engine)
and Offline Speech-to-Text (STT via Vosk Neural Recognizer).
"""

import base64
from functools import lru_cache
import io
import json
import logging
from math import gcd
import os
import queue
import re
import threading
import time
from typing import Any
import numpy as np

try:
    import av
except ImportError:
    av = None

try:
    import num2words
except ImportError:
    num2words = None

try:
    import scipy.signal as _signal
except ImportError:
    _signal = None

logger = logging.getLogger("amigo.speech")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_MODELS_ROOT = os.path.join(_PROJECT_ROOT, "models")

try:
    import sounddevice as _sd
except ImportError:
    _sd = None

# ── Pocket TTS (Kyutai Labs 24kHz Offline Mimi Neural Engine) ───────────────
try:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import pocket_tts
    _USE_POCKET = True
    logger.info("[TTS] Pocket TTS (Kyutai Labs 24kHz Offline) engine available.")
except ImportError:
    pocket_tts = None
    _USE_POCKET = False

_USE_TTS = _USE_POCKET

# ── Curated Voice Catalog ───────────────────────────────────────────────────
CURATED_VOICES = {
    "alba":        {"engine": "pocket_tts", "id": "alba",        "name": "Alba",     "gender": "Female", "accent": "US", "desc": "Flagship conversational female — natural cadence & warm expression (Recommended / Default)"},
    "marius":      {"engine": "pocket_tts", "id": "marius",      "name": "Marius",   "gender": "Male",   "accent": "US", "desc": "Warm, crisp & relatable conversational male companion (Amigo Default)"},
    "javert":      {"engine": "pocket_tts", "id": "javert",      "name": "Javert",   "gender": "Male",   "accent": "US", "desc": "Confident, authoritative & deep narrative male"},
    "cosette":     {"engine": "pocket_tts", "id": "cosette",     "name": "Cosette",  "gender": "Female", "accent": "GB", "desc": "Refined, articulate & melodic British studio female"},
    "george":      {"engine": "pocket_tts", "id": "george",      "name": "George",   "gender": "Male",   "accent": "GB", "desc": "Polished & distinguished British gentleman"},
    "anna":        {"engine": "pocket_tts", "id": "anna",        "name": "Anna",     "gender": "Female", "accent": "US", "desc": "Bright, articulate & clear conversational female"},
    "michael":     {"engine": "pocket_tts", "id": "michael",     "name": "Michael",  "gender": "Male",   "accent": "US", "desc": "Relaxed & natural conversational male"},
    "eve":         {"engine": "pocket_tts", "id": "eve",         "name": "Eve",      "gender": "Female", "accent": "US", "desc": "Soft, calm & gentle conversational female"},

    # Direct aliases for Amigo
    "default":     {"engine": "pocket_tts", "id": "alba",        "name": "Alba",     "gender": "Female", "accent": "US", "desc": "Default Assistant Voice"},
    "amigo":       {"engine": "pocket_tts", "id": "marius",      "name": "Marius",   "gender": "Male",   "accent": "US", "desc": "Warm companion male"},
}

_VALID_POCKET_VOICES = frozenset({"alba", "marius", "javert", "cosette", "george", "anna", "michael", "eve"})

_PROFILE_PATH = os.path.join(_PROJECT_ROOT, "amigo_profile.json")
if not os.path.exists(_PROFILE_PATH):
    _PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amigo_profile.json")

_tts_lock = threading.Lock()
_audio_device_lock = threading.RLock()
_active_stream = None
_active_stream_lock = threading.Lock()
_profile_mtime_tts = 0.0

_pocket_model = None
_pocket_lock = threading.Lock()
_voice_states: dict[str, Any] = {}


def _get_pocket_model():
    """Loads and caches Pocket TTS (Kyutai) 24kHz offline model."""
    global _pocket_model
    if _pocket_model is not None:
        return _pocket_model
    if not _USE_POCKET:
        return None

    with _pocket_lock:
        if _pocket_model is not None:
            return _pocket_model
        try:
            os.environ["HF_HUB_OFFLINE"] = "1"
            from pocket_tts import TTSModel
            _pocket_model = TTSModel.load_model()
            logger.info("[PocketTTS] Kyutai 24kHz offline engine loaded successfully.")
            return _pocket_model
        except Exception as e:
            logger.error("[PocketTTS] Failed to initialize engine: %s", e)
            return None


def _resolve_pocket_voice_id(voice_name: str | None) -> str:
    v = (voice_name or "alba").lower().strip()
    clean_v = CURATED_VOICES.get(v, {}).get("id", v)
    return clean_v if clean_v in _VALID_POCKET_VOICES else "alba"


def _get_pocket_voice_state(voice_name: str):
    """Retrieves or computes cached voice conditioning state."""
    clean_v = _resolve_pocket_voice_id(voice_name)
    cached = _voice_states.get(clean_v)
    if cached is not None:
        return cached

    model = _get_pocket_model()
    if model is None:
        return None

    with _pocket_lock:
        if clean_v in _voice_states:
            return _voice_states[clean_v]
        try:
            state = model.get_state_for_audio_prompt(clean_v)
            _voice_states[clean_v] = state
            return state
        except Exception as e:
            logger.error("[PocketTTS] Failed to get voice state for %s: %s", clean_v, e)
            return None


def get_voice_catalog() -> dict:
    return dict(CURATED_VOICES)


def get_available_voices() -> list[str]:
    return list(CURATED_VOICES.keys())


def _get_default_voice() -> str:
    return "alba" if _USE_POCKET else "none"


ACTIVE_VOICE = _get_default_voice()


def sync_voice_from_profile() -> str:
    global ACTIVE_VOICE, _profile_mtime_tts
    default_voice = _get_default_voice()
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
                    if default_voice != "none":
                        ACTIVE_VOICE = default_voice
                        return default_voice
    except Exception as e:
        logger.debug("[TTS] Voice profile sync note: %s", e)
    if (ACTIVE_VOICE == "none" or not ACTIVE_VOICE) and default_voice != "none":
        ACTIVE_VOICE = default_voice
    return ACTIVE_VOICE


sync_voice_from_profile()


def set_voice(voice_name: str) -> str:
    global ACTIVE_VOICE, _profile_mtime_tts
    key = voice_name.lower().strip()
    default_voice = _get_default_voice()
    if key in CURATED_VOICES:
        ACTIVE_VOICE = key
        display_name = CURATED_VOICES[key].get("name", voice_name)
    else:
        ACTIVE_VOICE = default_voice
        display_name = CURATED_VOICES.get(default_voice, {}).get("name", default_voice)

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
            cache = getattr(rag_engine, "_profile_cache", None)
            if isinstance(cache, dict):
                cache.setdefault("preferences", {})["voice"] = ACTIVE_VOICE
                cache.setdefault("ui_settings", {})["voice"] = ACTIVE_VOICE
                cache.setdefault("user_profile", {}).setdefault("preferences", {})["voice"] = ACTIVE_VOICE
        except Exception:
            pass
    except Exception as e:
        logger.debug("[TTS] Voice persistence note: %s", e)

    return display_name


def get_active_voice() -> str:
    return ACTIVE_VOICE


_on_state_change = None
_on_broadcast = None


def set_tts_callbacks(state_cb=None, broadcast_cb=None):
    global _on_state_change, _on_broadcast
    if state_cb:
        _on_state_change = state_cb
    if broadcast_cb:
        _on_broadcast = broadcast_cb


def get_tts_engine_name() -> str:
    if _USE_POCKET:
        return "Pocket TTS (Kyutai 24kHz)"
    return "TTS Disabled"


def _trim_silence(samples: np.ndarray, threshold: float = 0.008, pad_samples: int = 480) -> np.ndarray:
    """Trims excessive leading/trailing silence padding so consecutive chunks flow seamlessly."""
    if samples is None or len(samples) == 0:
        return samples
    above = np.flatnonzero(np.abs(samples) > threshold)
    if len(above) == 0:
        return samples
    start = max(0, int(above[0]) - pad_samples)
    end = min(len(samples), int(above[-1]) + pad_samples)
    return samples[start:end]


def _fetch_pocket_audio(
    text: str,
    voice_id: str = "alba",
    stop_evt: threading.Event | None = None,
    cached_state: Any = None,
) -> tuple[np.ndarray | None, int]:
    """Generates ultra-expressive 24kHz studio audio via Pocket TTS (Kyutai Labs) with zero cloud latency."""
    if stop_evt and stop_evt.is_set():
        return None, 24000
    model = _get_pocket_model()
    if model is None:
        return None, 24000
    state = cached_state if cached_state is not None else _get_pocket_voice_state(voice_id)
    if state is None:
        state = _get_pocket_voice_state("alba")
    if state is None:
        return None, 24000

    try:
        with _tts_lock:
            if stop_evt and stop_evt.is_set():
                return None, 24000
            audio_tensor = model.generate_audio(state, text)
            if audio_tensor is None or audio_tensor.numel() == 0:
                return None, 24000
            audio = audio_tensor.detach().cpu().numpy().astype(np.float32)
            audio = _trim_silence(audio, pad_samples=int(0.02 * 24000))
            return audio, 24000
    except Exception as e:
        logger.warning("[PocketTTS] Synthesis error: %s", e)
        return None, 24000


def _warmup_tts():
    def _warm():
        try:
            if _USE_POCKET:
                m = _get_pocket_model()
                if m:
                    _get_pocket_voice_state("alba")
                    _get_pocket_voice_state("marius")
                    _get_mastering_sos(24000)
                    logger.info("[TTS] Pocket TTS (Kyutai 24kHz) primed and ready.")
        except Exception as e:
            logger.debug("[TTS Warmup] %s", e)

    threading.Thread(target=_warm, daemon=True, name="TTS-Warmup").start()


_warmup_tts()


# ── Asynchronous speech pipeline queues & generation ──────────────────────
_speech_queue = queue.Queue()
_playback_queue = queue.Queue(maxsize=32)
_speech_generation = 0
_speech_generation_lock = threading.Lock()
_generation_stop_events: dict[int, threading.Event] = {}
_active_synthesizers = 0
_state_lock = threading.Lock()
_is_speaking_state = False


def _speech_is_current(generation: int) -> bool:
    with _speech_generation_lock:
        return generation == _speech_generation


def _get_or_create_stop_event(generation: int) -> threading.Event:
    with _speech_generation_lock:
        if generation not in _generation_stop_events:
            _generation_stop_events[generation] = threading.Event()
        return _generation_stop_events[generation]


def _cleanup_stop_event(generation: int) -> None:
    with _speech_generation_lock:
        _generation_stop_events.pop(generation, None)


def _update_state(new_state: str):
    global _is_speaking_state
    cb = None
    with _state_lock:
        if new_state == "speaking":
            if not _is_speaking_state:
                _is_speaking_state = True
                cb = _on_state_change
        elif new_state == "idle":
            if _is_speaking_state:
                _is_speaking_state = False
                cb = _on_state_change
    if cb:
        try:
            cb(new_state)
        except Exception as e:
            logger.debug("[TTS] State callback error: %s", e)


_RE_URL = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
_RE_CONSECUTIVE_DOTS = re.compile(r'\.{2,}')
_RE_WHITESPACE = re.compile(r'\s+')
_RE_SPECIAL_CHARS = re.compile(r'[*_~`#<>{}[\]\\]')
_RE_SOURCES_BLOCK = re.compile(r'(?:\n+|\s+)(?:\[?(?:Sources?|References?|Citations?)\]?\s*:)\s*[\s\S]*$', re.IGNORECASE)
_RE_INLINE_CITATION = re.compile(r'\[(?:\d+|Source\s*\d+[^\]]*)\]', re.IGNORECASE)


def _extract_text_from_json(text: str) -> str:
    if not text or not isinstance(text, str):
        return ""
    s = text.strip()
    if (s.startswith("{") or s.startswith("[")) and any(k in s for k in ('"tool"', '"actions"', '"params"', '"response"', '"reply"', '"speak"')):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                for k in ("response", "reply", "speak", "text", "message", "answer"):
                    val = parsed.get(k)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                if isinstance(parsed.get("actions"), list) and parsed["actions"]:
                    first = parsed["actions"][0]
                    if isinstance(first, dict):
                        for k in ("speak", "response", "reply"):
                            val = first.get(k)
                            if isinstance(val, str) and val.strip():
                                return val.strip()
                        params = first.get("params", {})
                        if isinstance(params, dict):
                            for k in ("response", "reply", "speak", "text", "message"):
                                val = params.get(k)
                                if isinstance(val, str) and val.strip():
                                    return val.strip()
                return ""
            if isinstance(parsed, list) and parsed:
                first = parsed[0]
                if isinstance(first, dict):
                    for k in ("response", "reply", "speak", "text"):
                        val = first.get(k)
                        if isinstance(val, str) and val.strip():
                            return val.strip()
                return ""
        except Exception:
            pass
    return text


def _to_ordinal_word(num: int) -> str:
    if num2words is not None:
        try:
            return num2words.num2words(num, to="ordinal")
        except Exception:
            pass
    fallback = {1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth"}
    return fallback.get(num, f"Number {num}")


def normalize_text_for_tts(text: str) -> str:
    """Normalizes raw text into natural speech without inserting artificial pauses."""
    if not text:
        return ""
    try:
        from amigo.core.llm_agent import _strip_reasoning
        text, _ = _strip_reasoning(text)
    except Exception:
        pass
    t = _extract_text_from_json(text)

    t = _RE_SOURCES_BLOCK.sub('', t).strip()
    t = _RE_INLINE_CITATION.sub('', t)

    # 1. Markdown tables to natural speech
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
                        details.append(f"{col_name} {cell}")
                    else:
                        details.append(cell)
                row_str = f"{subject}, {', '.join(details)}." if details else f"{subject}."
                parsed_lines.append(row_str)
        else:
            parsed_lines.append(line)
    t = '\n'.join(parsed_lines)

    # 2. Remove URLs
    t = _RE_URL.sub('', t)

    # 3. Markdown headers
    t = re.sub(r'^(?:#{1,6}\s+)(.+)$', r'\1.', t, flags=re.MULTILINE)
    t = re.sub(r'#{1,6}\s*', '', t)

    # 4. Markdown bullet points -> clean space join
    t = re.sub(r'(?:^|\n)\s*[-*•]\s+', r' ', t)

    # 5. Numbered lists
    def _list_repl(m):
        num = int(m.group(1))
        return f"{_to_ordinal_word(num).capitalize()}, "
    t = re.sub(r'(?:^|\n)\s*(\d+)\.\s+', _list_repl, t)

    # 6. Number ranges
    t = re.sub(r'(\d+)\s*[-–—]\s*(\d+)', r'\1 to \2', t)

    # 7. Currency normalization
    if num2words is not None:
        def _curr_cents_repl(m):
            try:
                dollars = int(m.group(1))
                cents = int(m.group(2))
                d_str = num2words.num2words(dollars)
                d_unit = "dollar" if dollars == 1 else "dollars"
                if cents > 0:
                    c_str = num2words.num2words(cents)
                    c_unit = "cent" if cents == 1 else "cents"
                    return f"{d_str} {d_unit} and {c_str} {c_unit}"
                return f"{d_str} {d_unit}"
            except Exception:
                return m.group(0)
        t = re.sub(r'\$(\d+)\.(\d{1,2})\b', _curr_cents_repl, t)

        def _curr_whole_repl(m):
            try:
                val = int(m.group(1))
                unit = "dollar" if val == 1 else "dollars"
                return f"{num2words.num2words(val)} {unit}"
            except Exception:
                return m.group(0)
        t = re.sub(r'\$(\d+)\b', _curr_whole_repl, t)

    # 8. Phone numbers -> digit by digit
    def _phone_repl(m):
        cleaned = re.sub(r'\D', '', m.group(0))
        return " ".join(num2words.num2words(int(d)) if num2words else d for d in cleaned)
    t = re.sub(r'\b(?:\+?\d{1,3}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b', _phone_repl, t)

    # 9. OTP / PIN / Code patterns
    def _otp_repl(m):
        prefix = m.group(1)
        digits = m.group(2)
        dec = " ".join(num2words.num2words(int(d)) if num2words else d for d in digits)
        return f"{prefix}{dec}"
    t = re.sub(r'\b((?:otp|code|pin|verification|pnr|id|password|flight)\s*(?:is|:)?\s*)(\d{4,8})\b', _otp_repl, t, flags=re.IGNORECASE)

    # 10. Formatted large numbers
    if num2words is not None:
        def _comma_num_repl(m):
            raw = m.group(0).replace(",", "")
            try:
                return num2words.num2words(int(raw))
            except Exception:
                return raw
        t = re.sub(r'\b\d{1,3}(?:,\d{3})+\b', _comma_num_repl, t)

    # 11. Decimals
    def _dec_repl(m):
        integer_part = m.group(1)
        decimal_digits = m.group(2)
        int_str = num2words.num2words(int(integer_part)) if num2words else integer_part
        dec_str = " ".join(num2words.num2words(int(d)) if num2words else d for d in decimal_digits)
        return f"{int_str} point {dec_str}"
    t = re.sub(r'\b(\d+)\.(\d+)\b', _dec_repl, t)

    # 12. Standalone 7+ digit strings
    def _long_digits_repl(m):
        digits = m.group(0)
        return " ".join(num2words.num2words(int(d)) if num2words else d for d in digits)
    t = re.sub(r'\b\d{7,}\b', _long_digits_repl, t)

    # 13. Clean dashes, colons, parentheses, and semicolons without stacking commas
    t = re.sub(r'(?<=\s)[-–—]+(?=\s)', ' ', t)
    t = re.sub(r'[–—]', ' ', t)
    t = re.sub(r':(?=\s|$)', ' ', t)
    t = re.sub(r'\(([^)]+)\)', r', \1 ', t)
    t = re.sub(r';\s*', ', ', t)

    # 14. Strip remaining markdown formatting characters
    t = _RE_SPECIAL_CHARS.sub(' ', t)
    t = re.sub(r'[/|]', ' ', t)

    # 15. Percentages, dates, and numbers
    if num2words is not None:
        def _percent_repl(m):
            try:
                val = m.group(1)
                if "." in val:
                    parts = val.split(".")
                    w = num2words.num2words(int(parts[0]))
                    d = " ".join(num2words.num2words(int(x)) for x in parts[1])
                    return f"{w} point {d} percent"
                return f"{num2words.num2words(int(val))} percent"
            except Exception:
                return m.group(0)
        t = re.sub(r'(\d+(?:\.\d+)?)\s*%', _percent_repl, t)

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

        def _ord_repl(m):
            try:
                return num2words.num2words(int(m.group(1)), to='ordinal')
            except Exception:
                return m.group(0)
        t = re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', _ord_repl, t, flags=re.I)

        def _year_repl(m):
            prefix = m.group(1)
            year = int(m.group(2))
            try:
                return f"{prefix}{num2words.num2words(year, to='year')}"
            except Exception:
                return m.group(0)
        t = re.sub(r'\b(in |since |year |dated |from |circa )(18\d\d|19\d\d|20\d\d)\b', _year_repl, t, flags=re.I)

        def _standalone_year_repl(m):
            val = int(m.group(0))
            if 1900 <= val <= 2040:
                try:
                    return num2words.num2words(val, to='year')
                except Exception:
                    pass
            return m.group(0)
        t = re.sub(r'\b(19\d\d|20[0-3]\d)\b', _standalone_year_repl, t)

        def _num_repl(m):
            try:
                return num2words.num2words(int(m.group(1)))
            except Exception:
                return m.group(0)
        t = re.sub(r'\b(\d+)\b', _num_repl, t)
    else:
        t = re.sub(r'\$(\d+(?:\.\d{2})?)', r'\1 dollars', t)
        t = re.sub(r'(\d+)%', r'\1 percent', t)

    # 16. Acronyms
    PRONOUNCEABLE = {'NASA', 'CHAT', 'OPEN', 'PLAY', 'HELP', 'STOP', 'DONE', 'ASAP', 'PIN', 'STEM', 'NATO', 'AIDS', 'RAM', 'ROM', 'LASER', 'MUTE', 'NEXT', 'QUIT', 'EXIT'}
    ACRONYMS = {'CPU', 'GPU', 'API', 'USB', 'URL', 'TTS', 'STT', 'HTML', 'JSON', 'SDK', 'LLM', 'AI', 'UI', 'OS', 'IP', 'ID', 'PC', 'OCR', 'VAD', 'RMS', 'SNR', 'DAC'}

    def _acronym_repl(m):
        w = m.group(0)
        if w in ACRONYMS:
            return ' '.join(list(w))
        if w in PRONOUNCEABLE:
            return w.lower()
        if len(w) <= 3:
            return ' '.join(list(w))
        return w.capitalize()

    t = re.sub(r'\b[A-Z]{2,5}\b', _acronym_repl, t)

    # 17. Clean consecutive punctuation and whitespace
    t = _RE_CONSECUTIVE_DOTS.sub(' ', t)
    t = re.sub(r'!{2,}', '!', t)
    t = re.sub(r'\?{2,}', '?', t)
    t = re.sub(r'[,]{2,}', ',', t)
    t = re.sub(r'\s+,', ',', t)
    t = re.sub(r'\s+!', '!', t)
    t = re.sub(r'\s+\?', '?', t)
    return _RE_WHITESPACE.sub(' ', t).strip()


def _compact_tts_text(text: str) -> str:
    return normalize_text_for_tts(text)


def _peaking_sos(fc: float, gain_db: float, q: float, fs: float) -> np.ndarray:
    a_amp = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * fc / fs
    alpha = np.sin(w0) / (2.0 * q)
    cos_w0 = np.cos(w0)
    b0 = 1.0 + alpha * a_amp
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * a_amp
    a0 = 1.0 + alpha / a_amp
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / a_amp
    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]], dtype=np.float32)


@lru_cache(maxsize=8)
def _get_mastering_sos(sr: int) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]:
    if _signal is None or sr <= 0:
        return None, None, None
    hp_sos = _signal.butter(2, 45.0, btype="high", fs=sr, output="sos").astype(np.float32)
    warm_sos = _peaking_sos(260.0, 0.8, 0.85, float(sr))
    pres_sos = _peaking_sos(4800.0, 1.2, 0.9, float(sr))
    return hp_sos, warm_sos, pres_sos


def _process_audio_clarity(samples, sr=24000, is_final_chunk: bool = False) -> tuple[np.ndarray, int]:
    """Audio mastering & smooth boundary transitions without inter-chunk gaps."""
    if samples is None or len(samples) == 0:
        return samples, int(sr)
    samples = np.asarray(samples, dtype=np.float32)
    sr = int(sr)

    if sr not in (44100, 24000, 22050, 48000, 16000) and len(samples) > 0 and _signal is not None:
        target_sr = 44100
        try:
            g = gcd(sr, target_sr)
            samples = _signal.resample_poly(samples, target_sr // g, sr // g).astype(np.float32)
            sr = target_sr
        except Exception:
            pass

    samples = samples - np.mean(samples)

    if _signal is not None:
        try:
            hp_sos, warm_sos, pres_sos = _get_mastering_sos(sr)
            if hp_sos is not None:
                samples = _signal.sosfilt(hp_sos, samples).astype(np.float32)
            if warm_sos is not None:
                samples = _signal.sosfilt(warm_sos, samples).astype(np.float32)
            if pres_sos is not None:
                samples = _signal.sosfilt(pres_sos, samples).astype(np.float32)
        except Exception:
            pass

    fade_in_len = min(int(0.002 * sr), len(samples))
    if fade_in_len > 0:
        ramp_in = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, fade_in_len, dtype=np.float32)))
        samples[:fade_in_len] *= ramp_in

    if is_final_chunk:
        fade_out_len = min(int(0.015 * sr), len(samples))
        if fade_out_len > 0:
            ramp_out = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, fade_out_len, dtype=np.float32)))
            samples[-fade_out_len:] *= ramp_out
        cushion = np.zeros(int(0.08 * sr), dtype=np.float32)
        samples = np.concatenate([samples, cushion])
    else:
        fade_out_len = min(int(0.002 * sr), len(samples))
        if fade_out_len > 0:
            ramp_out = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, fade_out_len, dtype=np.float32)))
            samples[-fade_out_len:] *= ramp_out

    peak = float(np.max(np.abs(samples)))
    if peak > 0.95:
        samples = samples * (0.92 / peak)

    return samples, sr


def _split_into_safe_tts_chunks(text: str, max_chunk_len: int = 300) -> list[str]:
    """
    Splits text strictly along natural sentence boundaries (. ! ? \n) without chopping
    mid-sentence at commas, balancing low time-to-first-audio with smooth prosody.
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

    return chunks or [text.strip()]


# ── Audio Playback Worker ───────────────────────────────────────────────────
def _close_stream_locked(stream) -> None:
    if stream is None:
        return
    with _active_stream_lock:
        global _active_stream
        if _active_stream is stream:
            _active_stream = None
    try:
        stream.stop()
    except Exception:
        pass
    try:
        stream.close()
    except Exception:
        pass


def play_sound_file(sound_path: str, wait: bool = False) -> None:
    """Safely plays an audio file on the shared audio lock without colliding with active streams."""
    if not sound_path or not os.path.exists(sound_path) or _sd is None:
        return

    def _play():
        with _audio_device_lock:
            try:
                import soundfile as sf
                data, sr = sf.read(sound_path, dtype="float32")
                if data.ndim > 1:
                    data = np.mean(data, axis=1).astype(np.float32)
                data = np.ascontiguousarray(data, dtype=np.float32)
                with _sd.OutputStream(samplerate=int(sr), channels=1, dtype="float32", blocksize=2048) as out_s:
                    for offset in range(0, len(data), 2048):
                        out_s.write(data[offset:offset + 2048])
            except Exception as e:
                logger.debug("[Sound] Failed to play %s: %s", sound_path, e)

    if wait:
        _play()
    else:
        threading.Thread(target=_play, daemon=True, name="SafeSoundPlayback").start()


def _playback_worker():
    """
    Keeps the sounddevice OutputStream open across consecutive sentences (1.5s idle grace)
    with crash-proof lock protection and safe buffer handling.
    """
    stream = None
    stream_sr = None

    while True:
        try:
            item = _playback_queue.get(timeout=1.5)
        except queue.Empty:
            with _audio_device_lock:
                if stream is not None and _speech_queue.empty() and _playback_queue.empty():
                    _close_stream_locked(stream)
                    stream = None
                    stream_sr = None
            with _state_lock:
                synthesizing = _active_synthesizers > 0
            if not synthesizing and _speech_queue.empty() and _playback_queue.empty():
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

        with _audio_device_lock:
            try:
                if _sd is not None and _speech_is_current(gen):
                    if stream is None or stream_sr != sr:
                        _close_stream_locked(stream)
                        stream = _sd.OutputStream(samplerate=sr, channels=1, dtype="float32", blocksize=2048)
                        stream.start()
                        stream_sr = sr
                        with _active_stream_lock:
                            global _active_stream
                            _active_stream = stream

                    if len(samples) > 0 and stream is not None:
                        chunk_frames = 2048
                        for offset in range(0, len(samples), chunk_frames):
                            if not _speech_is_current(gen):
                                break
                            chunk = samples[offset:offset + chunk_frames]
                            if not chunk.flags["C_CONTIGUOUS"]:
                                chunk = np.ascontiguousarray(chunk, dtype=np.float32)
                            stream.write(chunk)

            except Exception as e:
                logger.debug("[Audio Playback Worker Note] %s", e)
                _close_stream_locked(stream)
                stream = None
                stream_sr = None

        with _state_lock:
            synthesizing = _active_synthesizers > 0

        if is_last_in_utterance and not synthesizing and _speech_queue.empty() and _playback_queue.empty():
            _update_state("idle")
            try:
                from amigo.utils.tool_registry import start_pending_confirmation_timer
                start_pending_confirmation_timer()
            except Exception:
                pass


threading.Thread(target=_playback_worker, daemon=True, name="TTS-Playback").start()


# ── Speech Synthesis Worker ─────────────────────────────────────────────────
def _speech_worker():
    global _active_synthesizers
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

        stop_evt = _get_or_create_stop_event(generation)
        with _state_lock:
            _active_synthesizers += 1

        try:
            tts_started = time.perf_counter()
            if broadcast_chat and _on_broadcast:
                _on_broadcast("chat_message", {"sender": "assistant", "text": text})

            if not _USE_TTS:
                continue

            target_voice = voice_override.lower().strip() if voice_override else sync_voice_from_profile()
            default_voice = _get_default_voice()
            v_info = CURATED_VOICES.get(target_voice) or CURATED_VOICES.get(default_voice) or {}
            voice_id = _resolve_pocket_voice_id(v_info.get("id", "alba"))
            voice_state = _get_pocket_voice_state(voice_id) or _get_pocket_voice_state("alba")
            fallback_state = _get_pocket_voice_state("alba") if voice_id != "alba" else voice_state

            clean_text = _compact_tts_text(text)
            if not clean_text:
                continue

            chunks = _split_into_safe_tts_chunks(clean_text)
            if not chunks:
                chunks = [clean_text]

            total_chunks = len(chunks)

            for idx, chunk in enumerate(chunks):
                if not chunk or not _speech_is_current(generation) or stop_evt.is_set():
                    break

                try:
                    samples = None
                    sr = 24000
                    if _USE_POCKET:
                        samples, sr = _fetch_pocket_audio(chunk, voice_id, stop_evt, cached_state=voice_state)
                        if (samples is None or len(samples) == 0) and not stop_evt.is_set() and voice_id != "alba":
                            samples, sr = _fetch_pocket_audio(chunk, "alba", stop_evt, cached_state=fallback_state)

                    if samples is not None and len(samples) > 0 and _speech_is_current(generation) and not stop_evt.is_set():
                        is_last = (idx == total_chunks - 1)
                        samples, sr = _process_audio_clarity(samples, sr, is_final_chunk=is_last)
                        _update_state("speaking")
                        _playback_queue.put((samples, sr, generation, is_last))

                except Exception as chunk_err:
                    logger.error("[TTS Chunk Error] %s on chunk: %s", chunk_err, chunk[:60])
                    continue

            logger.debug(
                "[Timing] request_id=%s stage=tts duration_ms=%.1f",
                request_id or "unknown",
                (time.perf_counter() - tts_started) * 1000,
            )

        except Exception as e:
            logger.error("[TTS] Speech synthesis error: %s", e)
        finally:
            with _state_lock:
                _active_synthesizers = max(0, _active_synthesizers - 1)
                synthesizing_left = _active_synthesizers > 0
            if not synthesizing_left and _speech_queue.empty() and _playback_queue.empty():
                _update_state("idle")
            _cleanup_stop_event(generation)
            _speech_queue.task_done()


threading.Thread(target=_speech_worker, daemon=True, name="TTS-Synthesizer").start()


_last_spoken_text = ""
_last_spoken_time = 0.0
_speech_dedup_lock = threading.Lock()


def speak(text: str, block: bool = False, request_id: str | None = None, broadcast_chat: bool = False, voice: str | None = None) -> None:
    if not text:
        return
    global _last_spoken_text, _last_spoken_time
    clean = text.strip()
    now = time.monotonic()
    with _speech_dedup_lock:
        if clean == _last_spoken_text and (now - _last_spoken_time) < 1.0 and len(clean.split()) > 3:
            return
        _last_spoken_text = clean
        _last_spoken_time = now

    with _speech_generation_lock:
        generation = _speech_generation
        if generation not in _generation_stop_events:
            _generation_stop_events[generation] = threading.Event()

    item = (text, generation, request_id, broadcast_chat, voice)
    if block:
        _speech_queue.put(item)
        _speech_queue.join()
        _playback_queue.join()
    else:
        _speech_queue.put(item)


def stop_speaking() -> None:
    global _speech_generation
    with _speech_generation_lock:
        for evt in list(_generation_stop_events.values()):
            evt.set()
        _speech_generation += 1
        _generation_stop_events[_speech_generation] = threading.Event()

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
    existing = _find_vosk_model_path()
    if existing:
        return existing

    os.makedirs(_VOSK_DIR, exist_ok=True)
    zip_path = os.path.join(_VOSK_DIR, "model.zip")
    url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
    try:
        logger.info("[STT] Downloading lightweight Vosk offline model (~40MB)...")
        import requests
        req_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        with requests.get(url, headers=req_headers, stream=True, timeout=30.0) as resp:
            resp.raise_for_status()
            with open(zip_path, "wb") as out_f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
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
            t0 = time.perf_counter()
            logger.info("[STT] Loading Vosk offline engine from %s...", model_path)
            _vosk_model = Model(model_path)
            _VOSK_IS_AVAILABLE = True
            logger.info("[STT] Vosk offline STT ready in %.1fms.", (time.perf_counter() - t0) * 1000)
            return _vosk_model
        except Exception as e:
            logger.error("[STT] Failed to initialize Vosk model: %s", e)
            _VOSK_IS_AVAILABLE = False
            return None


def is_stt_available() -> bool:
    return get_stt_recognizer() is not None


def get_stt_engine_name() -> str:
    if is_stt_available():
        return "Vosk (Offline Neural STT)"
    return "Unavailable"


def transcribe_samples(samples: np.ndarray, sample_rate: int = 16000) -> str:
    model = get_stt_recognizer()
    if model is None or samples is None or len(samples) == 0:
        return ""

    try:
        samples = np.asarray(samples, dtype=np.float32)
        sample_rate = int(sample_rate)
        if sample_rate != 16000 and len(samples) > 0 and _signal is not None:
            try:
                g = gcd(sample_rate, 16000)
                samples = _signal.resample_poly(samples, 16000 // g, sample_rate // g).astype(np.float32)
            except Exception:
                pass

        if len(samples) < 1600:
            return ""

        peak = float(np.max(np.abs(samples)))
        if peak < 0.01:
            return ""

        samples = samples / max(peak, 0.2)
        pcm_bytes = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()

        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(model, 16000)
        rec.AcceptWaveform(pcm_bytes)
        res = json.loads(rec.FinalResult())
        return res.get("text", "").strip()
    except Exception as e:
        logger.error("[STT] Vosk transcription error: %s", e)
        return ""


def transcribe_audio_data(audio_data) -> str:
    try:
        raw_pcm = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
        samples = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return transcribe_samples(samples, 16000)
    except Exception as e:
        logger.error("[STT] AudioData conversion error: %s", e)
        return ""


def transcribe_audio_bytes(audio_bytes: bytes) -> str:
    if not audio_bytes:
        return ""

    if av is not None:
        try:
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
            logger.debug("[STT] PyAV decode note: %s", e_av)

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
        logger.error("[STT] Raw audio byte transcription failed: %s", e2)
        return ""


def transcribe_b64(audio_b64: str) -> str:
    if not audio_b64:
        return ""
    try:
        if "," in audio_b64:
            audio_b64 = audio_b64.split(",", 1)[1]
        raw_bytes = base64.b64decode(audio_b64)
        return transcribe_audio_bytes(raw_bytes)
    except Exception as e:
        logger.error("[STT] Base64 decode error: %s", e)
        return ""


def _warmup_stt():
    def _run():
        try:
            model = get_stt_recognizer()
            if model:
                t = np.linspace(0.0, 0.2, 3200, dtype=np.float32)
                warmup_signal = (0.02 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)
                transcribe_samples(warmup_signal, 16000)
                logger.debug("[STT] Vosk STT primed.")
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="VoskSTT-Warmup").start()


_warmup_stt()