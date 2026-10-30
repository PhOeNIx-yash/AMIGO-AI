"""
Unified Speech Engine Module for Amigo Voice Assistant.
Handles both Neural Text-to-Speech (TTS via Microsoft Edge Neural Voices - Azure Studio 24kHz)
and Offline Neural Speech-to-Text (STT via Vosk Neural Recognizer).
"""

import asyncio
import io
import json
import logging
import os
import queue
import re
import socket
import threading
import time
import urllib.request
import numpy as np

# Force IPv4 in socket.getaddrinfo to prevent Windows 11 IPv6 connection reset error [WinError 10054]
try:
    _orig_getaddrinfo = socket.getaddrinfo
    def _ipv4_getaddrinfo(*args, **kwargs):
        res = _orig_getaddrinfo(*args, **kwargs)
        ipv4 = [r for r in res if r[0] == socket.AF_INET]
        return ipv4 if ipv4 else res
    socket.getaddrinfo = _ipv4_getaddrinfo
except Exception:
    pass

try:
    import aiohttp
    _orig_connector_init = aiohttp.TCPConnector.__init__
    def _ipv4_connector_init(self, *args, **kwargs):
        kwargs['family'] = socket.AF_INET
        _orig_connector_init(self, *args, **kwargs)
    aiohttp.TCPConnector.__init__ = _ipv4_connector_init
except ImportError:
    aiohttp = None

try:
    import av
except ImportError:
    av = None

try:
    import num2words
except ImportError:
    num2words = None

logger = logging.getLogger("amigo.speech")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_MODELS_ROOT = os.path.join(_PROJECT_ROOT, "models")

try:
    import sounddevice as _sd
except ImportError:
    _sd = None

# ── Kokoro v1.0 ONNX Offline Neural Engine ─────────────────────────────────
_KOKORO_MODEL_PATH = None
for _cand in (
    os.path.join(_MODELS_ROOT, "kokoro", "kokoro-v1.0.onnx"),
    os.path.join(_MODELS_ROOT, "kokoro", "kokoro-v1.0.int8.onnx"),
    os.path.join(_MODELS_ROOT, "kokoro-v1.0.onnx"),
):
    if os.path.exists(_cand) and os.path.getsize(_cand) > 10_000_000:
        _KOKORO_MODEL_PATH = _cand
        break

_KOKORO_VOICES_PATH = None
for _cand in (
    os.path.join(_MODELS_ROOT, "kokoro", "voices-v1.0.bin"),
    os.path.join(_MODELS_ROOT, "voices-v1.0.bin"),
):
    if os.path.exists(_cand) and os.path.getsize(_cand) > 1_000_000:
        _KOKORO_VOICES_PATH = _cand
        break

try:
    import kokoro_onnx
    import onnxruntime as _ort
    _USE_KOKORO_ONNX = bool(_KOKORO_MODEL_PATH and _KOKORO_VOICES_PATH)
    if _USE_KOKORO_ONNX:
        logger.info("[TTS] Kokoro v1.0 ONNX Offline Neural engine available (24kHz Studio, 54 voices).")
except ImportError:
    kokoro_onnx = None
    _ort = None
    _USE_KOKORO_ONNX = False

# ── Microsoft Edge Neural Fallback Engine ──────────────────────────────────
try:
    import edge_tts
    _USE_EDGE_TTS = True
except ImportError:
    edge_tts = None
    _USE_EDGE_TTS = False

_USE_TTS = _USE_KOKORO_ONNX or _USE_EDGE_TTS

# ── Curated Voice Catalog ───────────────────────────────────────────────────
CURATED_VOICES = {
    # Kokoro v1.0 Flagship Studio Voices (Primary / Offline)
    "af_heart":    {"engine": "kokoro", "id": "af_heart",    "name": "Heart",    "gender": "Female", "accent": "US", "desc": "Flagship studio female — #1 natural human cadence (Default)"},
    "af_bella":    {"engine": "kokoro", "id": "af_bella",    "name": "Bella",    "gender": "Female", "accent": "US", "desc": "Bright, crisp & articulate assistant female"},
    "af_sarah":    {"engine": "kokoro", "id": "af_sarah",    "name": "Sarah",    "gender": "Female", "accent": "US", "desc": "Warm, confident & clear conversational female"},
    "af_nicole":   {"engine": "kokoro", "id": "af_nicole",   "name": "Nicole",   "gender": "Female", "accent": "US", "desc": "Soft, pleasant & relatable conversational female"},
    "af_sky":      {"engine": "kokoro", "id": "af_sky",      "name": "Sky",      "gender": "Female", "accent": "US", "desc": "Expressive, youthful & upbeat female"},
    "am_adam":     {"engine": "kokoro", "id": "am_adam",     "name": "Adam",     "gender": "Male",   "accent": "US", "desc": "Warm, relatable conversational male"},
    "am_michael":  {"engine": "kokoro", "id": "am_michael",  "name": "Michael",  "gender": "Male",   "accent": "US", "desc": "Deep, confident & crisp male"},
    "bm_george":   {"engine": "kokoro", "id": "bm_george",   "name": "George",   "gender": "Male",   "accent": "GB", "desc": "Polished & distinguished British gentleman"},
    "bf_emma":     {"engine": "kokoro", "id": "bf_emma",     "name": "Emma",     "gender": "Female", "accent": "GB", "desc": "Crisp, elegant British female"},

    # Edge TTS Studio Voices (Cloud / Fallback)
    "aria":        {"engine": "edge_tts", "id": "en-US-AriaNeural",        "name": "Aria",        "gender": "Female", "accent": "US", "desc": "Clear, natural studio female (Edge TTS)"},
    "jenny":       {"engine": "edge_tts", "id": "en-US-JennyNeural",       "name": "Jenny",       "gender": "Female", "accent": "US", "desc": "Conversational studio female (Edge TTS)"},
    "guy":         {"engine": "edge_tts", "id": "en-US-GuyNeural",         "name": "Guy",         "gender": "Male",   "accent": "US", "desc": "Conversational studio male (Edge TTS)"},
    "christopher": {"engine": "edge_tts", "id": "en-US-ChristopherNeural", "name": "Christopher", "gender": "Male",   "accent": "US", "desc": "Narrative studio male (Edge TTS)"},
    "sonia":       {"engine": "edge_tts", "id": "en-GB-SoniaNeural",       "name": "Sonia",       "gender": "Female", "accent": "GB", "desc": "Crisp British female (Edge TTS)"},
    "ryan":        {"engine": "edge_tts", "id": "en-GB-RyanNeural",        "name": "Ryan",        "gender": "Male",   "accent": "GB", "desc": "Crisp British male (Edge TTS)"},
    "neerja":      {"engine": "edge_tts", "id": "en-IN-NeerjaNeural",      "name": "Neerja",      "gender": "Female", "accent": "IN", "desc": "Natural Indian English female (Edge TTS)"},

    # Compatibility Aliases (Route to best available engine/voice with zero hardcoding)
    "default":     {"engine": "kokoro" if _USE_KOKORO_ONNX else "edge_tts", "id": "af_heart" if _USE_KOKORO_ONNX else "en-US-AriaNeural", "name": "Heart",   "gender": "Female", "accent": "US", "desc": "Default Assistant Voice"},
    "amigo":       {"engine": "kokoro" if _USE_KOKORO_ONNX else "edge_tts", "id": "am_adam" if _USE_KOKORO_ONNX else "en-US-GuyNeural",   "name": "Amigo",   "gender": "Male",   "accent": "US", "desc": "Warm companion male"},
    "clara":       {"engine": "kokoro" if _USE_KOKORO_ONNX else "edge_tts", "id": "af_bella" if _USE_KOKORO_ONNX else "en-US-JennyNeural", "name": "Clara",   "gender": "Female", "accent": "US", "desc": "Crisp conversational female"},
    "sophia":      {"engine": "kokoro" if _USE_KOKORO_ONNX else "edge_tts", "id": "af_sarah" if _USE_KOKORO_ONNX else "en-US-AriaNeural",  "name": "Sophia",  "gender": "Female", "accent": "US", "desc": "Studio female"},
    "leo":         {"engine": "kokoro" if _USE_KOKORO_ONNX else "edge_tts", "id": "am_michael" if _USE_KOKORO_ONNX else "en-US-ChristopherNeural", "name": "Leo", "gender": "Male", "accent": "US", "desc": "Narrative male"},
    "alba":        {"engine": "kokoro" if _USE_KOKORO_ONNX else "edge_tts", "id": "af_heart" if _USE_KOKORO_ONNX else "en-US-AriaNeural", "name": "Alba",    "gender": "Female", "accent": "US", "desc": "Studio female"},
    "cosette":     {"engine": "kokoro" if _USE_KOKORO_ONNX else "edge_tts", "id": "af_bella" if _USE_KOKORO_ONNX else "en-US-JennyNeural", "name": "Cosette", "gender": "Female", "accent": "US", "desc": "Conversational female"},
    "marius":      {"engine": "kokoro" if _USE_KOKORO_ONNX else "edge_tts", "id": "am_adam" if _USE_KOKORO_ONNX else "en-US-GuyNeural",   "name": "Marius",  "gender": "Male",   "accent": "US", "desc": "Conversational male"},
    "en-us":       {"engine": "kokoro" if _USE_KOKORO_ONNX else "edge_tts", "id": "af_heart" if _USE_KOKORO_ONNX else "en-US-AriaNeural", "name": "Default", "gender": "Female", "accent": "US", "desc": "Default Assistant Voice"},
    "en-default":  {"engine": "kokoro" if _USE_KOKORO_ONNX else "edge_tts", "id": "af_heart" if _USE_KOKORO_ONNX else "en-US-AriaNeural", "name": "Default", "gender": "Female", "accent": "US", "desc": "Default Assistant Voice"},
}

_PROFILE_PATH = os.path.join(_PROJECT_ROOT, "amigo_profile.json")
if not os.path.exists(_PROFILE_PATH):
    _PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amigo_profile.json")

ACTIVE_VOICE = "none"

_tts_lock = threading.Lock()
_active_stream = None
_active_stream_lock = threading.Lock()
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
    if key in CURATED_VOICES:
        ACTIVE_VOICE = key
        display_name = CURATED_VOICES[key]["name"]
    else:
        ACTIVE_VOICE = key
        display_name = voice_name

    # Persist to profile atomically so voice selection is remembered across restarts
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

        # Notify and update rag_engine's in-memory profile cache directly if loaded
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
    """Returns dictionary of all curated voices with metadata, augmented dynamically from Kokoro voices."""
    catalog = dict(CURATED_VOICES)
    if _USE_KOKORO_ONNX:
        try:
            k = _get_kokoro()
            if k:
                for v in k.get_voices():
                    if v not in catalog:
                        gender = "Female" if v.startswith(("af_", "bf_", "ef_", "ff_", "if_", "jf_", "zf_")) else "Male"
                        accent = "GB" if v.startswith("b") else "US"
                        catalog[v] = {
                            "engine": "kokoro",
                            "id": v,
                            "name": v.replace("_", " ").title(),
                            "gender": gender,
                            "accent": accent,
                            "desc": f"Kokoro v1.0 Studio voice ({v})",
                        }
        except Exception:
            pass
    return catalog


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
    if _USE_KOKORO_ONNX:
        return "Kokoro v1.0 ONNX Offline Neural TTS (24kHz Studio)"
    elif _USE_EDGE_TTS:
        return "Microsoft Edge Neural TTS (24kHz)"
    return "TTS Disabled"


_kokoro_instance = None
_kokoro_init_lock = threading.Lock()


def _get_kokoro():
    """Lazy-loads and warms up Kokoro v1.0 ONNX inference engine with optimized multi-threading."""
    global _kokoro_instance
    if _kokoro_instance is None and _USE_KOKORO_ONNX:
        with _kokoro_init_lock:
            if _kokoro_instance is None and _KOKORO_MODEL_PATH and _KOKORO_VOICES_PATH:
                try:
                    so = _ort.SessionOptions()
                    so.intra_op_num_threads = min(6, os.cpu_count() or 4)
                    so.inter_op_num_threads = 2
                    so.graph_optimization_level = _ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                    session = _ort.InferenceSession(_KOKORO_MODEL_PATH, sess_options=so, providers=["CPUExecutionProvider"])
                    _kokoro_instance = kokoro_onnx.Kokoro.from_session(session, _KOKORO_VOICES_PATH)
                    logger.info("[Kokoro] Kokoro v1.0 ONNX Engine loaded and ready.")
                except Exception as e:
                    logger.warning(f"[Kokoro] Init note: {e}")
    return _kokoro_instance


def _fetch_kokoro_audio(text: str, voice_id: str = "af_heart", stop_evt: threading.Event | None = None, speed: float = 1.05) -> tuple[np.ndarray | None, int]:
    """Generates ultra-fast 24kHz studio audio via Kokoro v1.0 ONNX with zero cloud latency."""
    kokoro = _get_kokoro()
    if kokoro is None:
        return None, 24000
    try:
        if stop_evt and stop_evt.is_set():
            return None, 24000
        # Determine language prefix from voice (e.g. bf_emma -> en-gb, af_heart -> en-us)
        lang = "en-gb" if voice_id.startswith("b") else "en-us"
        samples, sr = kokoro.create(text, voice=voice_id, speed=speed, lang=lang)
        if samples is None or len(samples) == 0:
            return None, 24000
        audio = np.ascontiguousarray(samples, dtype=np.float32)
        return audio, sr
    except Exception as e:
        logger.warning(f"[Kokoro] Synthesis error: {e}")
        return None, 24000


def _fetch_edge_tts_audio(text: str, voice_id: str, stop_evt: threading.Event | None = None, rate: str = "+8%", pitch: str = "+0Hz") -> tuple[np.ndarray | None, int]:
    """Fetches and decodes MP3 audio bytes from Edge TTS into float32 samples with assistant pacing tuning."""
    if not edge_tts or not av:
        return None, 24000

    async def _async_fetch():
        comm = edge_tts.Communicate(text, voice_id, rate=rate, pitch=pitch)
        buf = io.BytesIO()
        async for chunk in comm.stream():
            if stop_evt and stop_evt.is_set():
                return None
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        buf.seek(0)
        return buf

    loop = asyncio.new_event_loop()
    try:
        mp3_buf = loop.run_until_complete(_async_fetch())
    except Exception as e:
        logger.warning(f"[Edge TTS] Fetch error: {e}")
        return None, 24000
    finally:
        loop.close()

    if mp3_buf is None or mp3_buf.getbuffer().nbytes == 0:
        return None, 24000

    try:
        container = av.open(mp3_buf)
        frames = []
        sr = 24000
        for frame in container.decode(audio=0):
            sr = frame.sample_rate
            frames.append(frame.to_ndarray())
        container.close()

        if not frames:
            return None, sr

        audio = np.concatenate(frames, axis=1)
        if audio.ndim > 1:
            audio = audio.mean(axis=0)
        audio = np.ascontiguousarray(audio.astype(np.float32) / 32768.0, dtype=np.float32)
        return audio, sr
    except Exception as e:
        logger.warning(f"[Edge TTS] Decode error: {e}")
        return None, 24000


def _warmup_tts():
    """Warms up TTS engine in a background thread so first generation has zero delay."""
    def _warm():
        try:
            if _USE_KOKORO_ONNX:
                kokoro = _get_kokoro()
                if kokoro:
                    kokoro.create("Ready.", voice="af_heart")
                    logger.info("[TTS] Kokoro v1.0 ONNX Engine primed and ready.")
            elif _USE_EDGE_TTS:
                _fetch_edge_tts_audio("Ready.", "en-US-AriaNeural")
                logger.info("[TTS] Microsoft Edge Neural TTS initialized and connected.")
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


_RE_URL = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
_RE_CONSECUTIVE_DOTS = re.compile(r'\.{2,}')
_RE_WHITESPACE = re.compile(r'\s+')
_RE_SPECIAL_CHARS = re.compile(r'[*_~`#<>{}[\]\\]')


def _extract_text_from_json(text: str) -> str:
    """Safely extract human-readable text from a JSON payload if raw router/tool JSON is passed to TTS."""
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
            elif isinstance(parsed, list) and parsed:
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
    t = _extract_text_from_json(text)

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

    # 7.5 Currency normalization BEFORE decimal substitution ($50.25 -> fifty dollars and twenty-five cents)
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

    # 18. Percentages, dates, and comprehensive number-to-words normalization
    if num2words is not None:
        # Percentages: 50% -> fifty percent, 3.5% -> three point five percent
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

        # Four-digit years: convert to "year" when preceded by year indicators
        def _year_repl(m):
            prefix = m.group(1)
            year = int(m.group(2))
            try:
                return f"{prefix}{num2words.num2words(year, to='year')}"
            except Exception:
                return m.group(0)

        t = re.sub(r'\b(in |since |year |dated |from |circa )(18\d\d|19\d\d|20\d\d)\b', _year_repl, t, flags=re.I)

        # Standalone years between 1900 and 2040
        def _standalone_year_repl(m):
            val = int(m.group(0))
            if 1900 <= val <= 2040:
                try:
                    return num2words.num2words(val, to='year')
                except Exception:
                    pass
            return m.group(0)
        t = re.sub(r'\b(19\d\d|20[0-3]\d)\b', _standalone_year_repl, t)

        # Other standalone numbers (cardinals)
        def _num_repl(m):
            try:
                return num2words.num2words(int(m.group(1)))
            except Exception:
                return m.group(0)

        t = re.sub(r'\b(\d+)\b', _num_repl, t)
    else:
        t = re.sub(r'\$(\d+(?:\.\d{2})?)', r'\1 dollars', t)
        t = re.sub(r'(\d+)%', r'\1 percent', t)

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
    """Cleans and normalizes text for natural neural Pocket TTS prosody."""
    return normalize_text_for_tts(text)


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
    - Eliminates per-sentence driver opening/closing latency and dead-air sleep.
    - Transitions assistant state to 'idle' only when all queues are genuinely empty and playback finishes.
    """
    global _active_stream
    stream = None
    stream_sr = None

    while True:
        try:
            item = _playback_queue.get(timeout=0.4)
        except queue.Empty:
            # Idle timeout: no audio was queued for 400ms
            if stream is not None and _speech_queue.empty() and _playback_queue.empty():
                with _active_stream_lock:
                    if _active_stream is stream:
                        _active_stream = None
                try:
                    time.sleep(0.08)  # Allow hardware DAC buffer to drain before stopping stream
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
            time.sleep(0.08)
            if _speech_queue.empty() and _playback_queue.empty():
                _update_state("idle")


threading.Thread(target=_playback_worker, daemon=True, name="TTS-Playback").start()


# ── Speech Synthesis Worker (Pipelines ahead of playback) ─────────────────
def _speech_worker():
    """
    Dedicated synthesis worker:
    - Streams Pocket TTS audio in real-time (24kHz chunks) directly to playback worker.
    - Yields chunks as they are generated by the Kyutai neural engine (~150ms time-to-first-sound).
    - Halts immediately without CPU waste on user interruption.
    """
    global _current_stop_event
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

            if not _USE_TTS:
                _update_state("idle")
                continue

            target_voice = voice_override.lower().strip() if voice_override else sync_voice_from_profile()
            v_info = CURATED_VOICES.get(target_voice, CURATED_VOICES.get("af_heart" if _USE_KOKORO_ONNX else "aria"))
            engine_type = v_info.get("engine", "kokoro" if _USE_KOKORO_ONNX else "edge_tts") if v_info else "edge_tts"
            voice_id = v_info.get("id", "af_heart" if engine_type == "kokoro" else "en-US-AriaNeural") if v_info else "af_heart"

            clean_text = _compact_tts_text(text)
            if not clean_text:
                continue

            chunks = _split_into_safe_tts_chunks(clean_text)
            if not chunks:
                chunks = [clean_text]

            total_chunks = len(chunks)
            stop_evt = _current_stop_event
            first_chunk_emitted = False

            for idx, chunk in enumerate(chunks):
                if not chunk or not _speech_is_current(generation) or stop_evt.is_set():
                    break

                spoken_chunk = chunk if chunk.endswith((".", "!", "?", ",", ";", ":")) else chunk + "."

                try:
                    with _tts_lock:
                        if not _speech_is_current(generation) or stop_evt.is_set():
                            break
                        if engine_type == "kokoro" and _USE_KOKORO_ONNX:
                            samples, sr = _fetch_kokoro_audio(spoken_chunk, voice_id, stop_evt)
                        else:
                            samples, sr = _fetch_edge_tts_audio(spoken_chunk, voice_id, stop_evt)

                    if samples is not None and len(samples) > 0 and _speech_is_current(generation) and not stop_evt.is_set():
                        is_last = (idx == total_chunks - 1)
                        _playback_queue.put((samples, sr, generation, is_last))
                        first_chunk_emitted = True

                except Exception as chunk_err:
                    logger.error(f"[TTS Chunk Error] {chunk_err} on chunk: {chunk[:60]}")
                    continue

            # Signal utterance completion if still current
            if _speech_is_current(generation) and not stop_evt.is_set() and first_chunk_emitted:
                try:
                    tail_silence = np.zeros(int(0.06 * 24000), dtype=np.float32)
                    _playback_queue.put((tail_silence, 24000, generation, True), timeout=0.2)
                except Exception:
                    pass

            logger.debug(
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
    """Stop current audio immediately, signal model to halt generation, flush queues, and abort stream."""
    global _speech_generation, _current_stop_event, _active_stream
    _current_stop_event.set()
    with _speech_generation_lock:
        _speech_generation += 1

    # Reset stop event for next utterance
    _current_stop_event = threading.Event()

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
